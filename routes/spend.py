"""Routes for grocery spend tracking: purchase import, spend dashboard, product↔ingredient links."""

from datetime import date, timedelta

import click
from flask import Blueprint, render_template, request, redirect, url_for, flash

from auth import login_required
from database import get_db
from services.spend_analysis import analyze_purchases, load_purchases
from services.spend_import import import_csv_file, import_purchases, parse_cup_price, parse_purchase_csv, price_per_unit, product_url

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
            flash("Choose a CSV file to import.", "error")
            return redirect(url_for("spend.spend_import"))
        try:
            text = upload.read().decode("utf-8-sig")
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
    sql = """
        SELECT p.product_name,
               COUNT(DISTINCT p.store || '|' || p.basket_id) AS orders,
               ROUND(SUM(p.line_total), 2) AS total_spend,
               MAX(p.order_date) AS last_bought,
               MAX(p.ingredient_id) AS ingredient_id,
               (SELECT unit_price FROM purchases x WHERE x.product_name = p.product_name
                ORDER BY x.order_date DESC LIMIT 1) AS last_price,
               (SELECT cup_price FROM purchases x WHERE x.product_name = p.product_name
                ORDER BY x.order_date DESC LIMIT 1) AS last_cup_price
        FROM purchases p WHERE 1=1"""
    params = []
    if search:
        sql += " AND p.product_name LIKE ?"
        params.append(f"%{search}%")
    sql += " GROUP BY p.product_name"
    if only_unlinked:
        sql += " HAVING MAX(p.ingredient_id) IS NULL"
    sql += " ORDER BY total_spend DESC LIMIT 150"
    products = db.execute(sql, params).fetchall()
    ingredients = db.execute("SELECT id, name, unit, price FROM ingredients ORDER BY name").fetchall()
    return render_template(
        "spend/products.html",
        products=products,
        ingredients=ingredients,
        ingredient_names={i["id"]: i["name"] for i in ingredients},
        search=search,
        only_unlinked=only_unlinked,
    )


def _latest_purchase(db, product_name):
    return db.execute(
        "SELECT unit_price, cup_price, store, stockcode FROM purchases WHERE product_name = ? ORDER BY order_date DESC LIMIT 1",
        (product_name,),
    ).fetchone()


@spend_bp.route("/spend/products/link", methods=["POST"])
@login_required
def spend_link_product():
    db = get_db()
    product_name = request.form.get("product_name", "").strip()
    choice = request.form.get("ingredient_id", "").strip()
    update_price = bool(request.form.get("update_price"))
    back = redirect(url_for("spend.spend_products", q=request.form.get("q", ""),
                            unlinked=request.form.get("unlinked", "")))
    if not product_name:
        return back

    latest = _latest_purchase(db, product_name)
    if choice == "new":
        # Create an ingredient named after the product, priced from the latest purchase.
        cup = parse_cup_price(latest["cup_price"]) if latest else None
        unit = {"g": "kg", "mL": "L"}.get(cup[2], cup[2]) if cup else "each"
        price = price_per_unit(latest["cup_price"], latest["unit_price"], unit) if latest else None
        cur = db.execute(
            "INSERT INTO ingredients (name, category, unit, price, store, url) VALUES (?, 'other', ?, ?, ?, ?)",
            (product_name, unit, price, latest["store"] if latest else None,
             product_url(latest["store"], latest["stockcode"]) if latest else None),
        )
        ingredient_id = cur.lastrowid
        flash(f"Created ingredient “{product_name}”. Set its category on the Ingredients page.", "success")
    elif choice:
        ingredient = db.execute("SELECT id, unit FROM ingredients WHERE id = ?", (choice,)).fetchone()
        if not ingredient:
            flash("Ingredient not found.", "error")
            return back
        ingredient_id = ingredient["id"]
        if update_price and latest:
            price = price_per_unit(latest["cup_price"], latest["unit_price"], ingredient["unit"])
            if price is not None:
                db.execute("UPDATE ingredients SET price = ? WHERE id = ?", (price, ingredient_id))
                flash(f"Linked and updated price to ${price:.2f} per {ingredient['unit']}.", "success")
            else:
                flash("Linked. The purchase price couldn't be converted to the ingredient's unit, so the price wasn't changed.", "warning")
        else:
            flash("Product linked.", "success")
    else:
        ingredient_id = None
        flash("Product unlinked.", "info")

    db.execute("UPDATE purchases SET ingredient_id = ? WHERE product_name = ?", (ingredient_id, product_name))
    db.commit()
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
