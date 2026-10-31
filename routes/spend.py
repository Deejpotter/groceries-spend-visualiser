"""Routes for grocery spend tracking: purchase import, spend dashboard, product↔ingredient links."""

import threading
from datetime import date, datetime, timedelta, timezone

import click
from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash

from auth import login_required
from database import get_db, get_setting
from models import label_of
from services.budget import budget_status
from services.spend_analysis import analyze_purchases, load_purchases, load_spend
from services.spend_import import (
    commit_import_batch, create_import_batch, discard_import_batch, get_import_batch,
    import_csv_file, import_purchases, parse_purchase_csv,
    parse_woolworths_invoice_pdf, save_staged_edits, staged_read_total,
)

spend_bp = Blueprint("spend", __name__)

RANGES = {"3m": 91, "6m": 182, "12m": 365, "all": None}


def _range_start(db, key):
    """Start date for a range key, measured back from the most recent purchase."""
    days = RANGES.get(key)
    latest = db.execute("SELECT MAX(order_date) FROM purchases").fetchone()[0]
    if not days or not latest:
        return None
    return (date.fromisoformat(latest) - timedelta(days=days)).isoformat()


_AUTO_SYNC_HOURS = 24
_banksync_lock = threading.Lock()


def _auto_sync_due(last_sync, now):
    """Whether a background sync should run (API key checked by caller)."""
    if not last_sync:
        return True
    try:
        last = datetime.fromisoformat(last_sync)
    except (TypeError, ValueError):
        return True
    return now - last >= timedelta(hours=_AUTO_SYNC_HOURS)


def _maybe_auto_sync():
    """Sync BankSync in the background when the bookmark is stale.

    Opening the spend dashboard is the natural trigger: whenever
    you look at your money, it is at most a day old. A lock keeps
    concurrent syncs (several tabs, or several gunicorn workers)
    from piling up — the import itself is idempotent anyway.
    """
    if not get_setting("banksync_api_key"):
        return
    if not _auto_sync_due(get_setting("banksync_last_sync"),
                            datetime.now(timezone.utc).replace(tzinfo=None)):
        return
    if not _banksync_lock.acquire(blocking=False):
        return
    app = current_app._get_current_object()

    def _run():
        try:
            with app.app_context():
                from services.banksync_client import (
                    BankSyncError, run_sync,
                )
                try:
                    run_sync(get_db())
                except BankSyncError as exc:
                    app.logger.error(f"BankSync auto-sync failed: {exc}")
        except Exception as exc:  # a thread must never break a request
            app.logger.error(f"BankSync auto-sync error: {exc}")
        finally:
            _banksync_lock.release()

    threading.Thread(target=_run, daemon=True,
                     name="banksync-auto-sync").start()


@spend_bp.route("/spend")
@login_required
def spend_dashboard():
    _maybe_auto_sync()
    db = get_db()
    range_key = request.args.get("range", "12m")
    if range_key not in RANGES:
        range_key = "12m"
    stats = analyze_purchases(load_purchases(db, start=_range_start(db, range_key)))
    raw_target = (get_setting("monthly_budget") or "").strip()
    try:
        target = float(raw_target) if raw_target else None
    except ValueError:
        target = None
    budget = budget_status(load_spend(db), target)
    unlinked = db.execute(
        "SELECT COUNT(DISTINCT product_name) FROM purchases WHERE ingredient_id IS NULL"
    ).fetchone()[0]
    return render_template(
        "spend/dashboard.html",
        stats=stats,
        range_key=range_key,
        ranges=[("3m", "3 months"), ("6m", "6 months"), ("12m", "12 months"), ("all", "All time")],
        unlinked_count=unlinked,
        budget=budget,
    )


@spend_bp.route("/spend/import", methods=["GET", "POST"])
@login_required
def spend_import():
    if request.method == "POST":
        upload = request.files.get("file")
        store = request.form.get("store", "").strip() or "Woolworths"
        if not upload or not upload.filename:
            flash("Choose a file to import (a Woolworths PDF invoice or a CSV).", "error")
            return redirect(url_for("spend.spend_import"))
        raw = upload.read()
        name = (upload.filename or "").lower()
        if name.endswith(".pdf") or raw[:5] == b"%PDF-":
            if store.lower() != "woolworths":
                flash("PDF invoices always import as Woolworths — the Store field applies to CSV files.", "info")
            return _import_invoice(raw)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            flash("That file isn't a UTF-8 CSV.", "error")
            return redirect(url_for("spend.spend_import"))

        rows, errors = parse_purchase_csv(text, store)
        if errors and not rows:
            flash(errors[0], "error")
            return redirect(url_for("spend.spend_import"))
        added, skipped = import_purchases(get_db(), rows)
        flash(f"Imported {added} purchase lines ({skipped} already imported).", "success")
        for err in errors[:3]:
            flash(err, "warning")
        return redirect(url_for("spend.spend_dashboard", range="all"))
    return render_template(
        "spend/import.html",
        banksync_configured=bool(get_setting("banksync_api_key")),
    )


@spend_bp.route("/spend/import-bank", methods=["POST"])
@login_required
def import_bank_csv():
    """Import a transaction CSV exported from NetBank."""
    from services.bank_import import (
        import_bank_transactions, load_merchant_patterns, parse_netbank_csv,
        store_bank_transactions, to_purchases,
    )
    upload = request.files.get("bank_file")
    if not upload or not upload.filename:
        flash("Choose a CSV file exported from NetBank.", "error")
        return redirect(url_for("spend.spend_import"))
    try:
        text = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("That file isn't a UTF-8 CSV.", "error")
        return redirect(url_for("spend.spend_import"))

    rows, errors = parse_netbank_csv(text)
    if errors and not rows:
        flash(errors[0], "error")
        return redirect(url_for("spend.spend_import"))
    db = get_db()
    patterns = load_merchant_patterns(db)
    store_bank_transactions(db, rows, "csv", patterns)
    purchases, unclassified = to_purchases(rows, patterns)
    added, duplicates = import_bank_transactions(db, purchases)
    from services.bank_import import remember_unclassified
    remember_unclassified(db, unclassified)
    return render_template(
        "spend/bank_result.html", added=added, duplicates=duplicates,
        unclassified=unclassified, errors=errors,
    )


@spend_bp.route("/spend/banksync/sync", methods=["POST"])
@login_required
def banksync_sync():
    """Pull new BankSync transactions for every connected account."""
    from services.banksync_client import BankSyncError, run_sync
    if not get_setting("banksync_api_key"):
        flash("Add your BankSync API key on the settings page first.", "error")
        return redirect(url_for("spend.spend_import"))
    try:
        result = run_sync(get_db())
    except BankSyncError as exc:
        flash(f"BankSync sync failed: {exc}", "error")
        return redirect(url_for("spend.spend_import"))
        return redirect(url_for("spend.spend_import"))
    return render_template(
        "spend/bank_result.html", added=result["added"],
        duplicates=result["duplicates"],
        unclassified=result["unclassified"],
        errors=result["errors"],
    )


@spend_bp.route("/spend/banksync/backfill", methods=["POST"])
@login_required
def banksync_backfill():
    """Pull up to 12 months of bank history for recurring-payment detection."""
    from services.banksync_client import BankSyncError, run_backfill
    if not get_setting("banksync_api_key"):
        flash("Add your BankSync API key on the settings page first.", "error")
        return redirect(url_for("spend.spend_import"))
    try:
        result = run_backfill(get_db(), months=12)
    except BankSyncError as exc:
        flash(f"Backfill failed: {exc}", "error")
        return redirect(url_for("spend.spend_import"))
    flash(
        f"Backfilled {result['transactions_added']} transactions "
        f"({result['added']} new grocery purchases). "
        "Open Suggestions to review the regular payments.",
        "success",
    )
    if request.form.get("next") == "suggest":
        return redirect(url_for("subscriptions.subscription_suggest"))
    return render_template(
        "spend/bank_result.html", added=result["added"],
        duplicates=result["duplicates"],
        unclassified=result["unclassified"],
        errors=result["errors"],
    )


@spend_bp.route("/spend/import-bank/duplicates", methods=["POST"])
@login_required
def import_bank_duplicates():
    """Force-import duplicate rows confirmed as separate shops."""
    from services.bank_import import force_import_duplicate
    keep = {int(i) for i in request.form.getlist("keep") if i.isdigit()}
    dates = request.form.getlist("d_date")
    descriptions = request.form.getlist("d_description")
    amounts = request.form.getlist("d_amount")
    sources = request.form.getlist("d_source")
    added = 0
    for i in sorted(keep):
        if i >= len(dates):
            continue
        if force_import_duplicate(
            get_db(), dates[i], descriptions[i], amounts[i],
            sources[i] if i < len(sources) else "csv",
        ):
            added += 1
    flash(f"Imported {added} previously-skipped duplicate(s).", "success")
    return redirect(url_for("spend.spend_dashboard", range="all"))


@spend_bp.route("/spend/products")
@login_required
def spend_products():
    from services.product_matching import (
        NON_FOOD_ROLES, ROLES, classify_product, suggest_links,
    )
    from services.repository import load_product_decisions
    db = get_db()
    search = request.args.get("q", "").strip()
    only_unlinked = request.args.get("unlinked") == "1"
    show = request.args.get("show", "food")
    if show not in ("food", "nonfood", "ignored", "all"):
        show = "food"
    per_page = 100
    page = max(1, request.args.get("page", type=int) or 1)
    where, having, params = "WHERE 1=1", "", []
    if search:
        where += " AND p.product_name LIKE ?"
        params.append(f"%{search}%")
    if only_unlinked:
        having = " HAVING MAX(p.ingredient_id) IS NULL"
    products = db.execute(
        """SELECT p.product_name,
                COUNT(DISTINCT p.store || '|' || p.basket_id) AS orders,
                ROUND(SUM(p.line_total), 2) AS total_spend,
                MAX(p.order_date) AS last_bought,
                MAX(p.ingredient_id) AS ingredient_id,
                (SELECT unit_price FROM purchases x WHERE x.product_name = p.product_name
                 ORDER BY x.order_date DESC LIMIT 1) AS last_price,
                (SELECT cup_price FROM purchases x WHERE x.product_name = p.product_name
                 ORDER BY x.order_date DESC LIMIT 1) AS last_cup_price
         FROM purchases p """ + where + " GROUP BY p.product_name" + having +
        " ORDER BY total_spend DESC",
        params,
    ).fetchall()
    decisions = load_product_decisions()
    all_rows = []
    hidden = {"nonfood": 0, "ignored": 0}
    for p in products:
        row = dict(p)
        decision = decisions.get(row["product_name"]) or {}
        row["role"] = decision.get("role") or classify_product(row["product_name"])
        row["role_chosen"] = bool(decision.get("role"))
        row["ignored"] = bool(decision.get("ignored"))
        if row["ignored"]:
            hidden["ignored"] += 1
        elif row["role"] in NON_FOOD_ROLES:
            hidden["nonfood"] += 1
        if show == "food" and (row["ignored"] or row["role"] in NON_FOOD_ROLES):
            continue
        if show == "nonfood" and (row["ignored"] or row["role"] not in NON_FOOD_ROLES):
            continue
        if show == "ignored" and not row["ignored"]:
            continue
        all_rows.append(row)
    total_count = len(all_rows)
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)
    product_rows = all_rows[(page - 1) * per_page:page * per_page]
    ingredients = db.execute("SELECT id, name, display_name, unit, price FROM ingredients ORDER BY name").fetchall()
    suggestions = {}
    if request.args.get("suggest") == "1":
        suggestions = suggest_links(product_rows, [dict(i) for i in ingredients])
        for name, review in _ai_product_suggestions(db, [r for r in product_rows if r["product_name"] not in suggestions]).items():
            suggestions[name] = review
    return render_template(
        "spend/products.html",
        products=product_rows,
        ingredients=ingredients,
        ingredient_names={i["id"]: label_of(i) for i in ingredients},
        suggestions=suggestions,
        search=search,
        only_unlinked=only_unlinked,
        show=show,
        hidden=hidden,
        roles=ROLES,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
        ai_configured=_ai_config()["configured"],
    )


def _ai_config():
    from services.ai_review import resolve_config
    return resolve_config(get_setting)


def _ai_product_suggestions(db, rows):
    """Cached AI link suggestions for unlinked rows, shaped like suggest_links output."""
    from services.product_ai_review import cached_product_reviews
    unlinked = [r for r in rows if not r.get("ingredient_id")]
    found = {}
    for name, review in cached_product_reviews(db, unlinked).items():
        if review.get("ingredient_id"):
            found[name] = {"ingredient_id": review["ingredient_id"], "score": 0.7, "confidence": "ai",
                           "matched_on": [], "reason": review.get("reason")}
    return found


@spend_bp.route("/spend/products/role", methods=["POST"])
@login_required
def spend_product_role():
    """Set a product's role (food, household, ...) or ignore/restore it."""
    from services.product_matching import ROLE_LOOKUP
    db = get_db()
    name = request.form.get("product_name", "").strip()
    role = request.form.get("role", "").strip()
    ignored = request.form.get("ignored")
    back = redirect(url_for("spend.spend_products", q=request.form.get("q", ""),
                            show=request.form.get("show", "food"), page=request.form.get("page", "1")))
    if not name or (role and role not in ROLE_LOOKUP):
        flash("Choose a product and a valid role.", "error")
        return back
    existing = db.execute("SELECT role, ignored FROM product_decisions WHERE product_name = ?", (name,)).fetchone()
    new_role = role or (existing["role"] if existing else None)
    new_ignored = (1 if ignored == "1" else 0) if ignored in ("0", "1") else (existing["ignored"] if existing else 0)
    db.execute(
        "INSERT INTO product_decisions (product_name, role, ignored, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)"
        " ON CONFLICT(product_name) DO UPDATE SET role = excluded.role, ignored = excluded.ignored,"
        " updated_at = CURRENT_TIMESTAMP",
        (name, new_role, new_ignored),
    )
    db.commit()
    if request.headers.get("HX-Request"):
        return '<span class="text-success small">saved</span>'
    if ignored == "1":
        flash(f"{name} is hidden and won't be suggested.", "success")
    elif ignored == "0":
        flash(f"{name} is back.", "success")
    else:
        flash(f"{name} marked as {ROLE_LOOKUP[new_role].lower()}.", "success")
    return back


@spend_bp.route("/spend/products/accept-suggestions", methods=["POST"])
@login_required
def spend_accept_suggestions():
    """Link every unlinked food product whose best match is high confidence."""
    from services.mutations import link_product
    from services.product_matching import suggest_links
    from services.repository import load_product_decisions
    from services.product_matching import product_role
    db = get_db()
    decisions = load_product_decisions()
    rows = [{"product_name": r["product_name"], "ingredient_id": None,
             "role": product_role(r["product_name"], decisions)}
            for r in db.execute("SELECT product_name FROM purchases GROUP BY product_name"
                                " HAVING MAX(ingredient_id) IS NULL")
            if not (decisions.get(r["product_name"]) or {}).get("ignored")]
    ingredients = [dict(i) for i in db.execute("SELECT id, name, display_name FROM ingredients")]
    linked = 0
    for name, match in suggest_links(rows, ingredients).items():
        if match["confidence"] != "high":
            continue
        level, _, _ = link_product(db, name, str(match["ingredient_id"]), True, "other")
        linked += 1 if level != "error" else 0
    flash(f"Linked {linked} product{'s' if linked != 1 else ''} with a high-confidence match."
          if linked else "No high-confidence matches to link.", "success" if linked else "info")
    return redirect(url_for("spend.spend_products", unlinked="1"))


@spend_bp.route("/spend/products/ai-review", methods=["POST"])
@login_required
def spend_products_ai_review():
    """Ask the AI to suggest ingredients and roles for products the rules couldn't match."""
    from services.ai_review import AIReviewError
    from services.product_ai_review import product_candidates, review_products, save_product_reviews
    from services.repository import load_product_decisions
    config = _ai_config()
    if not config["configured"]:
        flash("Add an OpenRouter key in Settings → AI review first.", "error")
        return redirect(url_for("spend.spend_products"))
    db = get_db()
    ingredients = [dict(i) for i in db.execute("SELECT id, name, display_name, unit FROM ingredients")]
    rows = [dict(r) for r in db.execute(
        "SELECT product_name, MAX(ingredient_id) AS ingredient_id, COUNT(*) AS buys FROM purchases"
        " GROUP BY product_name ORDER BY SUM(line_total) DESC")]
    candidates = product_candidates(db, rows, ingredients, load_product_decisions())
    if not candidates:
        flash("Every product already has a link or a recent AI review.", "info")
        return redirect(url_for("spend.spend_products", suggest=1, unlinked=1))
    try:
        reviews = review_products(candidates, ingredients, config["key"], config["model"])
    except AIReviewError as exc:
        flash(f"AI review failed: {exc}", "error")
        return redirect(url_for("spend.spend_products"))
    saved = save_product_reviews(db, candidates, reviews)
    flash(f"AI reviewed {saved} product{'s' if saved != 1 else ''}. Suggestions are pre-selected below.", "success")
    return redirect(url_for("spend.spend_products", suggest=1, unlinked=1))


def _import_invoice(raw: bytes):
    """Stage one Woolworths PDF tax invoice for review before importing.

    Invoices are Woolworths-issued, so the store label is fixed rather than
    taken from the form. Nothing reaches purchases until the review is confirmed.
    """
    meta, rows, warnings = parse_woolworths_invoice_pdf(raw)
    if not meta:
        flash(warnings[0] if warnings else "That PDF couldn't be read as a Woolworths invoice.", "error")
        return redirect(url_for("spend.spend_import"))
    token = create_import_batch(get_db(), meta, rows)
    for warning in warnings[:3]:
        flash(warning, "warning")
    return redirect(url_for("spend.import_review", token=token))


def _read_review_form():
    """Parallel line_id/product_name/quantity/unit_price/line_total/excluded lists."""
    ids = request.form.getlist("line_id")
    names = request.form.getlist("product_name")
    quantities = request.form.getlist("quantity")
    prices = request.form.getlist("unit_price")
    totals = request.form.getlist("line_total")
    excluded = set(request.form.getlist("excluded"))
    edits = []
    for i, raw_id in enumerate(ids):
        if not raw_id.isdigit():
            continue
        edits.append({
            "id": int(raw_id),
            "product_name": names[i] if i < len(names) else "",
            "quantity": quantities[i] if i < len(quantities) else "",
            "unit_price": prices[i] if i < len(prices) else "",
            "line_total": totals[i] if i < len(totals) else "",
            "excluded": raw_id in excluded,
        })
    return edits


@spend_bp.route("/spend/import/review/<token>", methods=["GET", "POST"])
@login_required
def import_review(token):
    db = get_db()
    batch, lines = get_import_batch(db, token)
    if not batch:
        flash("That import review is no longer available — upload the invoice again.", "error")
        return redirect(url_for("spend.spend_import"))
    action = request.form.get("action", "").strip() if request.method == "POST" else ""
    if action == "discard":
        discard_import_batch(db, token)
        flash("Import discarded — nothing was added.", "info")
        return redirect(url_for("spend.spend_import"))
    if action in ("save", "confirm"):
        errors = save_staged_edits(db, token, _read_review_form())
        error_map: dict = {}
        for err in errors:
            if err["id"] is not None:
                error_map.setdefault(err["id"], []).append(err["field"])
        batch, lines = get_import_batch(db, token)
        if not batch:
            flash("That import review is no longer available — upload the invoice again.", "error")
            return redirect(url_for("spend.spend_import"))
        if not errors and action == "confirm":
            added, _ = commit_import_batch(db, token)
            label = f"Woolworths invoice #{batch['invoice_number']} ({batch['order_date']})"
            if added == 0:
                flash(f"{label} was already imported — no new lines.", "info")
            else:
                flash(f"Imported {label}: {added} items.", "success")
            return redirect(url_for("spend.spend_dashboard", range="all"))
        if not errors:
            flash("Review saved.", "success")
    else:
        error_map = {}
        errors = []
    return render_template(
        "spend/import_review.html",
        batch=batch,
        lines=lines,
        read_total=staged_read_total(lines),
        already_count=sum(1 for line in lines if line["already"] and not line["excluded"]),
        review_errors=errors,
        error_map=error_map,
    )


@spend_bp.route("/spend/products/link", methods=["POST"])
@login_required
def spend_link_product():
    """Link one product to an ingredient (HTMX-friendly single-link endpoint)."""
    from services.mutations import link_product
    db = get_db()
    product_name = request.form.get("product_name", "").strip()
    ingredient_id = request.form.get("ingredient_id", "").strip()
    update_price = bool(request.form.get("update_price"))
    new_category = request.form.get("new_category", "other").strip()

    if not product_name:
        if request.headers.get("HX-Request"):
            return '<div class="text-danger small">No product name.</div>', 400
        return redirect(url_for("spend.spend_products"))

    level, message, _ = link_product(
        db, product_name, ingredient_id,
        update_price, new_category,
    )
    if request.headers.get("HX-Request"):
        css = "text-success" if level == "success" else "text-danger"
        return f'<div class="{css} small">{message}</div>'
    flash(message, level)
    return redirect(url_for("spend.spend_products",
                            q=request.form.get("q", ""),
                            unlinked=request.form.get("unlinked", ""),
                            page=request.form.get("page", "1")))


@spend_bp.route("/spend/products/link-batch", methods=["POST"])
@login_required
def spend_link_batch():
    """Save every row on the products page in one request."""
    from services.mutations import link_product
    db = get_db()
    names = request.form.getlist("product_name")
    choices = request.form.getlist("ingredient_id")
    checked = {v for v in request.form.getlist("update_price") if v.isdigit()}
    new_category = request.form.get("new_category", "other").strip()
    back = redirect(url_for("spend.spend_products", q=request.form.get("q", ""),
                            unlinked=request.form.get("unlinked", ""),
                            page=request.form.get("page", "1")))
    if not names:
        return back
    saved = prices = 0
    problems = []
    for i, raw_name in enumerate(names):
        name = (raw_name or "").strip()
        if not name:
            continue
        choice = choices[i].strip() if i < len(choices) else ""
        level, message, price_updated = link_product(db, name, choice, str(i) in checked, new_category)
        if level == "error":
            problems.append(f"{name}: {message}")
        else:
            saved += 1
            prices += 1 if price_updated else 0
    summary = f"Saved links for {saved} product{'s' if saved != 1 else ''}."
    if prices:
        summary += f" {prices} price{'s' if prices != 1 else ''} updated."
    flash(summary, "success" if saved else "info")
    for problem in problems[:3]:
        flash(problem, "error")
    return back


@spend_bp.route("/spend/patterns")
@login_required
def spend_patterns():
    """What we buy regularly, how often, and how well that predicted the last cycle."""
    from models import today as local_today
    from services.purchase_patterns import feedback_suppressed
    from services.repository import load_feedback
    from services.suggestions import accuracy_last_cycle, current_pay_cycle, patterns_for
    today = local_today()
    patterns = patterns_for(today)
    feedback = load_feedback("staple")
    for p in patterns:
        p["suppressed"], p["forced"] = feedback_suppressed(feedback, p["key"])
    groups = {r: [p for p in patterns if p["rhythm"] == r] for r in ("regular", "occasional", "stopped", "one_off")}
    return render_template("spend/patterns.html", groups=groups, accuracy=accuracy_last_cycle(today),
                           pay_cycle=current_pay_cycle(today), today=today.isoformat())


@spend_bp.route("/spend/patterns/feedback", methods=["POST"])
@login_required
def spend_patterns_feedback():
    """Always / never / reset suggestions for one regular buy."""
    from services.repository import record_feedback
    key = request.form.get("key", "").strip()
    action = request.form.get("action", "")
    if not (key.startswith("ing:") or key.startswith("prod:")) or action not in ("always", "never", "kept"):
        flash("Unknown item.", "error")
        return redirect(url_for("spend.spend_patterns"))
    record_feedback("staple", key, action)
    get_db().commit()
    flash({"always": "It will be on every pay-cycle list.", "never": "It won't be suggested again.",
           "kept": "Back to automatic suggestions."}[action], "success")
    return redirect(url_for("spend.spend_patterns"))


def register_spend_routes(app):
    app.register_blueprint(spend_bp)

    @app.cli.command("import-purchases")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--store", default="Woolworths", help="Store name recorded on each purchase.")
    def import_purchases_command(path, store):
        """Import a purchase-history CSV (e.g. data/woolworths_order_history.csv)."""
        added, skipped, errors = import_csv_file(get_db(), path, store)
        click.echo(f"Imported {added} lines ({skipped} already present).")
        for err in errors[:10]:
            click.echo(f"  {err}")
