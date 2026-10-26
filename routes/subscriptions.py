"""Routes for subscription and bill tracking CRUD."""

from datetime import date, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash

from auth import login_required
from database import get_db, get_setting, set_setting
from services.subscriptions import (
    fortnight_for_date,
    format_frequency,
    FREQUENCY_LABELS,
    monthly_cost,
    monthly_cost_by_category,
    monthly_cost_total,
    subscription_category_label,
    SUBSCRIPTION_CATEGORY_LOOKUP,
    SUBSCRIPTION_CATEGORIES,
    suggest_from_purchases,
    upcoming_weeks,
)
from services.spend_analysis import load_purchases
from services.recurring import build_streams
from services.price_history import group_price_history, payment_rows, summarise
from services.subscription_health import ended_subscriptions
from services.dashboard import sparkline
from services.ai_review import (
    AIReviewError,
    build_candidates,
    cached_reviews,
    merge_suggestions,
    resolve_config,
    review_candidates,
    save_reviews,
)

subscriptions_bp = Blueprint("subscriptions", __name__)


def _all_histories(db):
    """Price history for every merchant, keyed by merchant key."""
    rows = [dict(row) for row in db.execute(
        """SELECT txn_date, amount, merchant_key, direction
           FROM bank_transactions"""
    ).fetchall()]
    return group_price_history(rows)


def _decorate_subscription(sub, histories):
    """Attach price history and a sparkline to a subscription row."""
    sub = dict(sub)
    points = histories.get(sub.get("merchant_key") or "", [])
    stream_amount = sub.get("stream_amount")
    if stream_amount:
        tolerance = max(5.0, float(stream_amount) * 0.35)
        points = [point for point in points
                  if abs(point["amount"] - float(stream_amount)) <= tolerance]
    sub["price_points"] = points
    sub["price_summary"] = summarise(points)
    sub["price_spark"] = (sparkline([p["amount"] for p in points], 64, 18)
                          if len(points) >= 2 else "")
    return sub


@subscriptions_bp.route("/subscriptions")
@login_required
def subscription_list():
    """List all active subscriptions, grouped by category."""
    db = get_db()
    histories = _all_histories(db)
    rows = db.execute(
        "SELECT * FROM subscriptions WHERE active = 1 ORDER BY category, name"
    ).fetchall()
    subscriptions = [_decorate_subscription(r, histories) for r in rows]

    # Group by category.
    by_category = {}
    for s in subscriptions:
        cat = s.get("category") or "other"
        by_category.setdefault(cat, []).append(s)

    # Inactive subscriptions (paused or soft-deleted) get their own section.
    inactive = [_decorate_subscription(r, histories) for r in db.execute(
        "SELECT * FROM subscriptions WHERE active = 0 ORDER BY category, name"
    ).fetchall()]

    ended = ended_subscriptions(subscriptions)
    ended_ids = {row["id"] for row in ended}
    counted = [s for s in subscriptions if s["id"] not in ended_ids]

    monthly_total = monthly_cost_total(counted)
    cost_by_category = [
        {**row, "label": subscription_category_label(row["category"])}
        for row in monthly_cost_by_category(counted)
    ]
    weeks = upcoming_weeks(counted, weeks=8)
    biggest = max(counted, key=monthly_cost) if counted else None
    biggest_monthly = monthly_cost(biggest) if biggest else 0

    return render_template(
        "subscriptions/list.html",
        by_category=by_category,
        categories=SUBSCRIPTION_CATEGORIES,
        category_label=subscription_category_label,
        format_frequency=format_frequency,
        inactive=inactive,
        inactive_count=len(inactive),
        monthly_total=monthly_total,
        cost_by_category=cost_by_category,
        weeks=weeks,
        biggest=biggest,
        biggest_monthly=biggest_monthly,
        ended=ended,
    )


@subscriptions_bp.route("/subscriptions/<int:sub_id>")
@login_required
def subscription_detail(sub_id):
    """One bill, with the price it has been charged at over time."""
    db = get_db()
    row = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not row:
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))
    sub = _decorate_subscription(row, _all_histories(db))
    return render_template(
        "subscriptions/detail.html",
        s=sub,
        summary=sub["price_summary"],
        points=sub["price_points"],
        payments=payment_rows(sub["price_points"]),
        price_differs=bool(sub["price_summary"]
                           and abs(sub["price_summary"]["latest"] - sub["amount"]) >= 0.01),
        format_frequency=format_frequency,
        category_label=subscription_category_label,
    )


@subscriptions_bp.route("/subscriptions/<int:sub_id>/keep-tracking", methods=["POST"])
@login_required
def subscription_keep_tracking(sub_id):
    """Dismiss the 'possibly ended' prompt until a new pattern is missed."""
    db = get_db()
    sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))
    db.execute(
        "UPDATE subscriptions SET ended_dismissed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (sub_id,),
    )
    db.commit()
    flash(f"Keeping '{sub['name']}' tracked.", "success")
    return redirect(url_for("subscriptions.subscription_list"))


@subscriptions_bp.route("/subscriptions/<int:sub_id>/use-latest", methods=["POST"])
@login_required
def subscription_use_latest(sub_id):
    """Set the subscription amount to the latest observed payment."""
    db = get_db()
    row = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not row:
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))
    points = _decorate_subscription(row, _all_histories(db))["price_points"]
    if not points:
        flash("No linked payments to take a price from yet.", "error")
        return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))
    latest = points[-1]["amount"]
    db.execute("UPDATE subscriptions SET amount = ? WHERE id = ?", (latest, sub_id))
    db.commit()
    flash(f"Updated '{row['name']}' to ${latest:.2f} from the latest payment.", "success")
    return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))


@subscriptions_bp.route("/subscriptions/add", methods=["GET", "POST"])
@login_required
def subscription_add():
    """Add a new subscription."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        frequency = request.form.get("frequency", "monthly").strip().lower()
        category = request.form.get("category", "").strip()
        next_due = request.form.get("next_due", "").strip()
        notes = request.form.get("notes", "").strip()

        errors = []
        if not name:
            errors.append("Name is required.")
        try:
            amount = float(amount_raw.replace("$", "").replace(",", ""))
            if amount <= 0:
                errors.append("Amount must be greater than zero.")
        except (ValueError, AttributeError):
            errors.append("Amount must be a valid number.")
            amount = None
        if frequency not in FREQUENCY_LABELS:
            errors.append("Invalid frequency.")
        if not next_due:
            errors.append("Next due date is required.")
        else:
            try:
                date.fromisoformat(next_due)
            except ValueError:
                errors.append("Next due date must be a valid date.")

        if errors:
            return render_template(
                "subscriptions/form.html",
                errors=errors,
                categories=SUBSCRIPTION_CATEGORIES,
                form={
                    "name": name,
                    "amount": amount_raw,
                    "frequency": frequency,
                    "category": category,
                    "next_due": next_due,
                    "notes": notes,
                },
            )

        db = get_db()
        db.execute(
            """INSERT INTO subscriptions (name, amount, frequency, category, next_due, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, amount, frequency, category or "other", next_due, notes or None),
        )
        db.commit()
        flash(f"Subscription '{name}' added.", "success")
        return redirect(url_for("subscriptions.subscription_list"))

    return render_template(
        "subscriptions/form.html",
        errors=None,
        categories=SUBSCRIPTION_CATEGORIES,
        form=None,
    )


@subscriptions_bp.route("/subscriptions/<int:sub_id>/edit", methods=["GET", "POST"])
@login_required
def subscription_edit(sub_id):
    """Edit an existing subscription."""
    db = get_db()
    sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        frequency = request.form.get("frequency", "monthly").strip().lower()
        category = request.form.get("category", "").strip()
        next_due = request.form.get("next_due", "").strip()
        notes = request.form.get("notes", "").strip()

        errors = []
        if not name:
            errors.append("Name is required.")
        try:
            amount = float(amount_raw.replace("$", "").replace(",", ""))
            if amount <= 0:
                errors.append("Amount must be greater than zero.")
        except (ValueError, AttributeError):
            errors.append("Amount must be a valid number.")
            amount = None
        if frequency not in FREQUENCY_LABELS:
            errors.append("Invalid frequency.")
        if not next_due:
            errors.append("Next due date is required.")
        else:
            try:
                date.fromisoformat(next_due)
            except ValueError:
                errors.append("Next due date must be a valid date.")

        if errors:
            return render_template(
                "subscriptions/form.html",
                errors=errors,
                categories=SUBSCRIPTION_CATEGORIES,
                form={
                    "id": sub_id,
                    "name": name,
                    "amount": amount_raw,
                    "frequency": frequency,
                    "category": category,
                    "next_due": next_due,
                    "notes": notes,
                },
            )

        db.execute(
            """UPDATE subscriptions SET name = ?, amount = ?, frequency = ?,
               category = ?, next_due = ?, notes = ? WHERE id = ?""",
            (name, amount, frequency, category or "other", next_due, notes or None, sub_id),
        )
        db.commit()
        flash(f"Subscription '{name}' updated.", "success")
        return redirect(url_for("subscriptions.subscription_list"))

    return render_template(
        "subscriptions/form.html",
        errors=None,
        categories=SUBSCRIPTION_CATEGORIES,
        form=dict(sub),
    )


@subscriptions_bp.route("/subscriptions/<int:sub_id>/toggle", methods=["POST"])
@login_required
def subscription_toggle(sub_id):
    """Activate or deactivate a subscription."""
    db = get_db()
    sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        if request.headers.get("HX-Request"):
            return "", 404
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))

    new_active = 0 if sub["active"] else 1
    db.execute("UPDATE subscriptions SET active = ? WHERE id = ?", (new_active, sub_id))
    db.commit()
    status = "activated" if new_active else "deactivated"
    if request.headers.get("HX-Request"):
        # Paused: remove row from active list. Resumed: render updated row.
        if new_active:
            sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
            if sub:
                return render_template("subscriptions/_sub_row.html",
                                       s=_decorate_subscription(sub, _all_histories(db)),
                                       format_frequency=format_frequency)
        return "", 204
    flash(f"Subscription '{sub['name']}' {status}.", "success")
    return redirect(url_for("subscriptions.subscription_list"))


@subscriptions_bp.route("/subscriptions/<int:sub_id>/resume", methods=["POST"])
@login_required
def subscription_resume(sub_id):
    """Reactivate a paused subscription."""
    db = get_db()
    sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        if request.headers.get("HX-Request"):
            return "", 404
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))

    db.execute("UPDATE subscriptions SET active = 1 WHERE id = ?", (sub_id,))
    db.commit()
    if request.headers.get("HX-Request"):
        sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
        if sub:
            return render_template("subscriptions/_sub_row.html",
                                   s=_decorate_subscription(sub, _all_histories(db)),
                                   format_frequency=format_frequency)
        return "", 204
    flash(f"Subscription '{sub['name']}' resumed.", "success")
    return redirect(url_for("subscriptions.subscription_list"))


@subscriptions_bp.route("/subscriptions/<int:sub_id>/purge", methods=["POST"])
@login_required
def subscription_purge(sub_id):
    """Permanently delete a paused subscription (it is already hidden)."""
    db = get_db()
    sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        if request.headers.get("HX-Request"):
            return "", 404
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))

    db.execute("DELETE FROM merchant_decisions WHERE subscription_id = ?", (sub_id,))
    db.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
    db.commit()
    if request.headers.get("HX-Request"):
        return "", 204
    flash(f"Subscription '{sub['name']}' permanently deleted.", "success")
    return redirect(url_for("subscriptions.subscription_list"))


@subscriptions_bp.route("/subscriptions/<int:sub_id>/delete", methods=["POST"])
@login_required
def subscription_delete(sub_id):
    """Soft-delete a subscription (set active = 0)."""
    db = get_db()
    sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        if request.headers.get("HX-Request"):
            return "", 404
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))

    db.execute("UPDATE subscriptions SET active = 0 WHERE id = ?", (sub_id,))
    db.commit()
    if request.headers.get("HX-Request"):
        return "", 204
    flash(f"Subscription '{sub['name']}' deleted.", "success")
    return redirect(url_for("subscriptions.subscription_list"))


@subscriptions_bp.route("/subscriptions/<int:sub_id>/mark-paid", methods=["POST"])
@login_required
def subscription_mark_paid(sub_id):
    """Mark a payment as paid: advance one cycle, catching up missed ones."""
    from services.subscriptions import mark_paid
    db = get_db()
    sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        if request.headers.get("HX-Request"):
            return "", 404
        flash("Subscription not found.", "error")
        return redirect(url_for("subscriptions.subscription_list"))

    next_due, skipped = mark_paid(dict(sub))
    if next_due:
        db.execute("UPDATE subscriptions SET next_due = ? WHERE id = ?", (next_due, sub_id))
        db.commit()
        if request.headers.get("HX-Request"):
            sub = db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
            if sub:
                return render_template("subscriptions/_sub_row.html",
                                       s=_decorate_subscription(sub, _all_histories(db)),
                                       format_frequency=format_frequency)
            return "", 204
        note = f"Marked '{sub['name']}' as paid. Next due: {next_due}."
        if skipped:
            note += f" Caught up {skipped} missed cycle{'s' if skipped != 1 else ''}."
        flash(note, "success")
    else:
        flash("Couldn't compute next due date.", "error")
    return redirect(url_for("subscriptions.subscription_list"))


@subscriptions_bp.route("/subscriptions/fortnight")
@login_required
def subscription_fortnight():
    """Fortnightly budget summary: income - groceries - subscriptions = spare."""
    from services.budget import fortnightly_summary
    from services.repository import load_shopping_inputs, shopping_preferences
    from services.shopping_list_generator import generate_shopping_list, total_cost

    db = get_db()
    today = date.today()
    start, end = fortnight_for_date(today)

    # Subscriptions due this fortnight.
    subs = [dict(r) for r in db.execute(
        "SELECT * FROM subscriptions WHERE active = 1 ORDER BY category, name"
    ).fetchall()]

    # Grocery estimate for the fortnight (current plan, prorated to the overlap).
    from services.budget import prorate_estimate
    plan_start = get_setting("plan_start_date", "")
    plan_end = get_setting("plan_end_date", "")
    grocery_estimate = 0.0
    grocery_note = None
    if plan_start and plan_end:
        entries, recipes, recipe_ingredients, ingredients = load_shopping_inputs(plan_start, plan_end)
        unit_pref, subtract_pantry = shopping_preferences()
        grouped = generate_shopping_list(
            entries, recipes, recipe_ingredients, ingredients,
            unit_preference=unit_pref,
            subtract_pantry=subtract_pantry,
        )
        grocery_estimate, plan_days, overlap_days = prorate_estimate(
            total_cost(grouped), plan_start, plan_end, start, end)
        if plan_days and overlap_days != plan_days:
            grocery_note = f"{plan_days}-day plan, prorated to {overlap_days} days"

    # Fortnightly income from settings.
    raw_income = (get_setting("fortnightly_income") or "").strip()
    try:
        income = float(raw_income.replace("$", "").replace(",", "")) if raw_income else None
    except ValueError:
        income = None

    summary = fortnightly_summary(subs, grocery_estimate, income, start, end)
    return render_template(
        "subscriptions/fortnight.html",
        summary=summary,
        start=start,
        end=end,
        today=today,
        grocery_note=grocery_note,
    )


def _suggest_context(db, auto_review=False):
    """Everything the suggestions page and its HTMX panel need.

    The AI review of new candidates only runs when ``auto_review`` is set. The
    page itself renders from cached verdicts and asks for the review in a
    follow-up HTMX request, so a slow model never blocks the page.
    """

    # Merchants already accepted or dismissed are excluded so they don't nag.
    decisions = [dict(r) for r in db.execute(
        "SELECT * FROM merchant_decisions ORDER BY display_name"
    ).fetchall()]
    excluded_keys = [d["merchant_key"] for d in decisions]
    ignored_after = {
        d["merchant_key"]: d["created_at"]
        for d in decisions if d["decision"] == "ignored"
    }
    transactions = [dict(r) for r in db.execute(
        """SELECT txn_date, description, amount, merchant_key, direction, status
           FROM bank_transactions"""
    ).fetchall()]
    existing = {
        (row["name"] or "").strip().lower()
        for row in db.execute("SELECT name FROM subscriptions").fetchall()
    }
    linked_keys = {
        (row["stream_key"] or row["merchant_key"])
        for row in db.execute(
            "SELECT stream_key, merchant_key FROM subscriptions"
        ).fetchall()
        if row["stream_key"] or row["merchant_key"]
    }
    streams = build_streams(
        transactions, excluded_keys=set(excluded_keys) | linked_keys,
        revive_after=ignored_after)
    detected = [s for s in streams if s["confidence"] != "possible"]
    candidates = build_candidates(
        transactions, excluded_keys=set(excluded_keys) | linked_keys,
        revive_after=ignored_after, streams=streams)
    reviews = cached_reviews(db, candidates)
    ai = resolve_config(get_setting)
    unreviewed = [c for c in candidates
                  if c["merchant_key"] not in reviews]
    ai_error = None
    if auto_review and ai["configured"] and unreviewed:
        try:
            new_reviews = review_candidates(unreviewed, ai["key"], ai["model"])
            save_reviews(db, new_reviews)
            reviews = cached_reviews(db, candidates)
            unreviewed = [c for c in candidates
                          if c["merchant_key"] not in reviews]
        except AIReviewError as exc:
            ai_error = f"AI review failed: {exc}"
        except Exception:
            ai_error = "AI review failed."
    suggestions = merge_suggestions(detected, candidates, reviews)

    # Filter names already tracked, then deduplicate.
    seen = set()
    filtered = []
    for suggestion in suggestions:
        key = suggestion["name"].lower()
        if key in seen or key in existing:
            continue
        seen.add(key)
        filtered.append(suggestion)
    suggestions = filtered

    # Repeat grocery purchases are useful context, but not bills.
    from datetime import timedelta
    latest = db.execute("SELECT MAX(order_date) FROM purchases").fetchone()[0]
    if latest:
        start = (date.fromisoformat(latest[:10]) - timedelta(days=395)).isoformat()
    else:
        start = None
    purchases = load_purchases(db, start=start)
    repeat_purchases = []
    for suggestion in suggest_from_purchases(purchases):
        key = suggestion["name"].lower()
        if key in seen or key in existing:
            continue
        seen.add(key)
        repeat_purchases.append(suggestion)

    reviewed_at = max((r.get("reviewed_at") for r in reviews.values()
                       if r.get("reviewed_at")), default=None)
    ai_dismissed = [
        {"name": review.get("display_name") or key,
         "reason": review.get("reason") or ""}
        for key, review in reviews.items() if review["verdict"] == "noise"
    ]
    bank_count = db.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0]
    history = db.execute(
        "SELECT MIN(txn_date), MAX(txn_date) FROM bank_transactions"
    ).fetchone()
    dismissed = [d for d in decisions if d["decision"] == "ignored"]
    suggestion_total = round(sum(monthly_cost(s) for s in suggestions), 2)

    return {
        "suggestions": suggestions,
        "suggestion_total": suggestion_total,
        "repeat_purchases": repeat_purchases,
        "dismissed": dismissed,
        "format_frequency": format_frequency,
        "ai_configured": ai["configured"],
        "ai_model": ai["model"],
        "ai_from_env": ai["from_env"],
        "ai_candidate_count": len(candidates),
        "ai_unreviewed": len(unreviewed),
        "ai_review_count": len(reviews),
        "ai_reviewed_at": reviewed_at,
        "ai_dismissed": ai_dismissed,
        "bank_transaction_count": bank_count,
        "bank_history_first": history[0] if history else None,
        "bank_history_last": history[1] if history else None,
        "banksync_configured": bool(get_setting("banksync_api_key")),
        "suggest_error": ai_error,
        "suggest_notice": None,
        "ai_auto_attempted": auto_review,
    }


def _suggest_response(db, error=None, notice=None, auto_review=False):
    """Render the suggestions panel after an in-page action."""
    context = _suggest_context(db, auto_review=auto_review)
    context["suggest_error"] = error or context["suggest_error"]
    context["suggest_notice"] = notice
    # Only a full page load starts the background review. Re-triggering it on
    # every Add/Dismiss would start a new AI call per click and discard the
    # one already running.
    context["ai_auto_attempted"] = True
    return render_template("subscriptions/suggest.html", **context)


@subscriptions_bp.route("/subscriptions/suggest")
@login_required
def subscription_suggest():
    """Show bill suggestions from bank transactions, with optional AI review."""
    return render_template(
        "subscriptions/suggest.html", **_suggest_context(get_db()))


@subscriptions_bp.route("/subscriptions/suggest/review-new", methods=["POST"])
@login_required
def subscription_ai_review_new():
    """Review only the new candidates; requested by the page after it loads."""
    return _suggest_response(get_db(), auto_review=True)


@subscriptions_bp.route("/subscriptions/suggest/review", methods=["POST"])
@login_required
def subscription_ai_review():
    """Ask OpenRouter to keep or dismiss each candidate merchant."""
    db = get_db()
    ai = resolve_config(get_setting)
    if not ai["configured"]:
        flash("Add an OpenRouter API key in Settings before using AI review.", "error")
        return redirect(url_for("subscriptions.subscription_suggest"))

    decisions = [dict(r) for r in db.execute(
        "SELECT merchant_key, decision, created_at FROM merchant_decisions").fetchall()]
    ignored_after = {
        d["merchant_key"]: d["created_at"]
        for d in decisions if d["decision"] == "ignored"
    }
    linked_keys = {
        (row["stream_key"] or row["merchant_key"])
        for row in db.execute(
            "SELECT stream_key, merchant_key FROM subscriptions"
        ).fetchall()
        if row["stream_key"] or row["merchant_key"]
    }
    transactions = [dict(r) for r in db.execute(
        """SELECT txn_date, description, amount, merchant_key, direction, status
           FROM bank_transactions"""
    ).fetchall()]
    candidates = build_candidates(
        transactions,
        excluded_keys=set(d["merchant_key"] for d in decisions) | linked_keys,
        revive_after=ignored_after,
    )
    if not candidates:
        flash("No merchants with two or more transactions to review yet.", "info")
        return redirect(url_for("subscriptions.subscription_suggest"))

    try:
        reviews = review_candidates(candidates, ai["key"], ai["model"])
    except AIReviewError as exc:
        flash(f"AI review failed: {exc}", "error")
        return redirect(url_for("subscriptions.subscription_suggest"))

    saved = save_reviews(db, reviews)
    kept = sum(1 for review in reviews if review["bill"])
    flash(
        f"AI reviewed {saved} merchant{'s' if saved != 1 else ''}: "
        f"{kept} kept, {saved - kept} dismissed.",
        "success",
    )
    return redirect(url_for("subscriptions.subscription_suggest"))


@subscriptions_bp.route("/subscriptions/suggest/accept", methods=["POST"])
@login_required
def subscription_accept_suggestion():
    """Accept a suggested subscription and create it."""
    name = request.form.get("name", "").strip()
    amount_raw = request.form.get("amount", "").strip()
    frequency = request.form.get("frequency", "monthly").strip().lower()
    category = request.form.get("category", "other").strip()
    merchant_key = request.form.get("merchant_key", "").strip()
    is_htmx = bool(request.headers.get("HX-Request"))

    if not name:
        if is_htmx:
            return _suggest_response(get_db(), error="Suggestion is missing a name.")
        flash("Suggestion is missing a name.", "error")
        return redirect(url_for("subscriptions.subscription_suggest"))

    try:
        amount = float(amount_raw)
    except (ValueError, TypeError):
        if is_htmx:
            return _suggest_response(get_db(), error="Suggestion has an invalid amount.")
        flash("Suggestion has an invalid amount.", "error")
        return redirect(url_for("subscriptions.subscription_suggest"))

    if frequency not in FREQUENCY_LABELS:
        frequency = "monthly"

    # Next due defaults to the detected cadence, else today.
    next_due_raw = (request.form.get("next_due") or "").strip()
    try:
        next_due = date.fromisoformat(next_due_raw).isoformat()
    except ValueError:
        next_due = date.today().isoformat()

    db = get_db()
    stream_key = (request.form.get("stream_key", "").strip()
                  or merchant_key or name.lower())
    stream_amount = _amount_or_none(request.form.get("stream_amount"))
    cursor = db.execute(
        """INSERT INTO subscriptions
           (name, amount, frequency, category, next_due, notes, merchant_key,
            stream_key, stream_amount)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, amount, frequency, category, next_due, "Added from suggestion",
         merchant_key or None, stream_key, stream_amount),
    )
    if merchant_key:
        db.execute(
            """INSERT INTO merchant_decisions
               (merchant_key, display_name, amount, frequency, decision, subscription_id)
               VALUES (?, ?, ?, ?, 'accepted', ?)
               ON CONFLICT(merchant_key) DO UPDATE SET
                 decision = 'accepted',
                 display_name = excluded.display_name,
                 amount = excluded.amount,
                 frequency = excluded.frequency,
                 subscription_id = excluded.subscription_id""",
            (stream_key, name, amount, frequency, cursor.lastrowid),
        )
    db.commit()
    if is_htmx:
        return _suggest_response(db, notice=f"Added '{name}' to Bills.")
    flash(f"Added subscription '{name}'.", "success")
    return redirect(url_for("subscriptions.subscription_list"))


@subscriptions_bp.route("/subscriptions/suggest/ignore", methods=["POST"])
@login_required
def subscription_ignore_suggestion():
    """Dismiss a detected merchant so it stops being suggested."""
    merchant_key = request.form.get("merchant_key", "").strip()
    stream_key = request.form.get("stream_key", "").strip() or merchant_key
    is_htmx = bool(request.headers.get("HX-Request"))
    if not stream_key:
        if is_htmx:
            return _suggest_response(get_db(),
                                     error="That suggestion can't be dismissed.")
        flash("That suggestion can't be dismissed.", "error")
        return redirect(url_for("subscriptions.subscription_suggest"))

    db = get_db()
    db.execute(
        """INSERT INTO merchant_decisions
           (merchant_key, display_name, amount, frequency, decision)
           VALUES (?, ?, ?, ?, 'ignored')
           ON CONFLICT(merchant_key) DO UPDATE SET
             decision = 'ignored',
             display_name = excluded.display_name,
             amount = excluded.amount,
             frequency = excluded.frequency,
             created_at = CURRENT_TIMESTAMP""",
        (stream_key,
         request.form.get("name", "").strip() or merchant_key,
         _amount_or_none(request.form.get("amount")),
         request.form.get("frequency", "").strip() or None),
    )
    db.commit()
    if is_htmx:
        dismissed_name = request.form.get("name", "").strip() or merchant_key
        return _suggest_response(db, notice=f"Dismissed '{dismissed_name}'.")
    flash("Suggestion dismissed. It won't be suggested again.", "success")
    return redirect(url_for("subscriptions.subscription_suggest"))


@subscriptions_bp.route("/subscriptions/suggest/restore/<int:decision_id>", methods=["POST"])
@login_required
def subscription_restore_suggestion(decision_id):
    """Undo a dismissal."""
    db = get_db()
    db.execute(
        "DELETE FROM merchant_decisions WHERE id = ? AND decision = 'ignored'",
        (decision_id,),
    )
    db.commit()
    if request.headers.get("HX-Request"):
        return _suggest_response(db, notice="Suggestion restored.")
    flash("Suggestion restored.", "success")
    return redirect(url_for("subscriptions.subscription_suggest"))


def _amount_or_none(raw):
    try:
        return float(str(raw).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def register_subscriptions_routes(app):
    app.register_blueprint(subscriptions_bp)
