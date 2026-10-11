"""Routes for grocery spend tracking: purchase import, spend dashboard, product↔ingredient links."""

from datetime import date, timedelta

import click
from flask import Blueprint, render_template, request, redirect, url_for, flash

from auth import login_required
from database import get_db
from models import label_of
from services.spend_analysis import analyze_purchases, load_purchases
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


@spend_bp.route("/spend")
@login_required
def spend_dashboard():
    db = get_db()
    range_key = request.args.get("range", "12m")
    if range_key not in RANGES:
        range_key = "12m"
    stats = analyze_purchases(load_purchases(db, start=_range_start(db, range_key)))
    unlinked = db.execute(
        "SELECT COUNT(DISTINCT product_name) FROM purchases WHERE ingredient_id IS NULL"
    ).fetchone()[0]
    return render_template(
        "spend/dashboard.html",
        stats=stats,
        range_key=range_key,
        ranges=[("3m", "3 months"), ("6m", "6 months"), ("12m", "12 months"), ("all", "All time")],
        unlinked_count=unlinked,
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
    return render_template("spend/import.html")


@spend_bp.route("/spend/products")
@login_required
def spend_products():
    db = get_db()
    search = request.args.get("q", "").strip()
    only_unlinked = request.args.get("unlinked") == "1"
    per_page = 100
    page = max(1, request.args.get("page", type=int) or 1)
    where, having, params = "WHERE 1=1", "", []
    if search:
        where += " AND p.product_name LIKE ?"
        params.append(f"%{search}%")
    if only_unlinked:
        having = " HAVING MAX(p.ingredient_id) IS NULL"
    total_count = db.execute(
        f"SELECT COUNT(*) FROM (SELECT p.product_name FROM purchases p {where}"
        f" GROUP BY p.product_name{having})",
        params,
    ).fetchone()[0]
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)
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
        " ORDER BY total_spend DESC LIMIT ? OFFSET ?",
        (*params, per_page, (page - 1) * per_page),
    ).fetchall()
    ingredients = db.execute("SELECT id, name, display_name, unit, price FROM ingredients ORDER BY name").fetchall()
    return render_template(
        "spend/products.html",
        products=products,
        ingredients=ingredients,
        ingredient_names={i["id"]: label_of(i) for i in ingredients},
        search=search,
        only_unlinked=only_unlinked,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
    )


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
    from services.mutations import link_product
    db = get_db()
    product_name = request.form.get("product_name", "").strip()
    back = redirect(url_for("spend.spend_products", q=request.form.get("q", ""),
                            unlinked=request.form.get("unlinked", ""),
                            page=request.form.get("page", "1")))
    if not product_name:
        return back
    level, message, _ = link_product(
        db, product_name, request.form.get("ingredient_id", "").strip(),
        bool(request.form.get("update_price")),
        request.form.get("new_category", "other").strip(),
    )
    flash(message, level)
    return back


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
