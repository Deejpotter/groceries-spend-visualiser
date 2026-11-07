"""Routes for shelf price tracking across Woolworths, Coles and Aldi."""

import json
import re
import threading
import urllib.parse

import click
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from auth import login_required
from database import get_db, get_setting, set_setting
from models import today as local_today
from services import store_prices as sp
from services.price_tracking import (
    capture_review, check_messages, check_prices, ingredient_prices, load_watches, prices_overview, recent_changes,
    save_capture,
    unwatch, watch_product,
)

prices_bp = Blueprint("prices", __name__)

_check_lock = threading.Lock()


def _client():
    """The store client; tests swap it via app.config['STORE_CLIENT_FACTORY']."""
    factory = current_app.config.get("STORE_CLIENT_FACTORY")
    if factory:
        return factory()
    from services.store_clients import StoreClient
    return StoreClient()


def _record_check(result):
    set_setting("price_check_last", local_today().isoformat())
    set_setting("price_check_result", json.dumps(result))
    get_db().commit()


def run_check(app, client_factory=None):
    """Check every watched product; one run at a time. Returns the result, or None when busy."""
    if not _check_lock.acquire(blocking=False):
        return None
    try:
        with app.app_context():
            client = (client_factory or _client)()
            result = check_prices(get_db(), local_today(), client)
            _record_check(result)
            return result
    finally:
        _check_lock.release()


def _maybe_check_prices():
    """Check prices in the background once a day, when the prices page is opened."""
    if get_setting("price_check_last") == local_today().isoformat():
        return
    if not load_watches(get_db()) and not get_db().execute(
            "SELECT 1 FROM purchases WHERE stockcode IS NOT NULL AND ingredient_id IS NOT NULL LIMIT 1").fetchone():
        return
    app = current_app._get_current_object()
    factory = app.config.get("STORE_CLIENT_FACTORY")

    def _run():
        try:
            run_check(app, factory)
        except Exception as exc:  # a thread must never break a request
            app.logger.error(f"Price check failed: {exc}")

    if app.config.get("TESTING"):
        return
    threading.Thread(target=_run, daemon=True, name="price-check").start()


def _last_check():
    try:
        result = json.loads(get_setting("price_check_result") or "null")
    except ValueError:
        result = None
    return {"date": get_setting("price_check_last"), "result": result, "running": _check_lock.locked()}


@prices_bp.route("/prices")
@login_required
def prices_list():
    _maybe_check_prices()
    db = get_db()
    rows = prices_overview(db, local_today())
    return render_template("prices/list.html", rows=rows, stores=sp.STORES, last_check=_last_check(),
                           changes=recent_changes(rows))


@prices_bp.route("/prices/check", methods=["POST"])
@login_required
def prices_check():
    app = current_app._get_current_object()
    if app.config.get("TESTING"):
        result = run_check(app)
        for level, message in check_messages(result or {"checked": 0, "unavailable": 0, "failed": {}}):
            flash(message, level)
        return redirect(url_for("prices.prices_list"))
    if _check_lock.locked():
        flash("A price check is already running.", "info")
    else:
        threading.Thread(target=lambda: run_check(app, app.config.get("STORE_CLIENT_FACTORY")),
                         daemon=True, name="price-check").start()
        flash("Checking shelf prices in the background. Refresh in a minute.", "info")
    return redirect(url_for("prices.prices_list"))


def _ingredient(ingredient_id):
    row = get_db().execute("SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)).fetchone()
    if not row:
        abort(404)
    return dict(row)


def _default_query(db, ingredient):
    """Search text for a store: the latest linked product name without the store's brand word."""
    row = db.execute("SELECT product_name FROM purchases WHERE ingredient_id = ? ORDER BY order_date DESC LIMIT 1",
                     (ingredient["id"],)).fetchone()
    text = row["product_name"] if row else (ingredient.get("display_name") or ingredient["name"])
    words = [w for w in text.split() if w.lower() not in ("woolworths", "coles", "aldi", "pdm")]
    return " ".join(words)


@prices_bp.route("/prices/<int:ingredient_id>")
@login_required
def prices_detail(ingredient_id):
    db = get_db()
    ingredient = _ingredient(ingredient_id)
    range_key, days = sp.history_days(request.args.get("range"))
    data = ingredient_prices(db, ingredient, local_today(), days=days)
    watches = {w["store"]: w for w in load_watches(db, ingredient_id)}
    store = sp.store_key(request.args.get("store"))
    query = request.args.get("q", "").strip()
    results, search_error = [], None
    if store and query:
        from services.store_clients import StoreError
        try:
            results = _client().search(store, query)
        except StoreError as exc:
            search_error = str(exc)
    from services.spend_import import ingredient_unit_price
    for product in results:
        product["unit_price"] = ingredient_unit_price(product["name"], product["cup_price"], product["price"],
                                                      ingredient["unit"], ingredient.get("pack_size"))
    return render_template(
        "prices/detail.html", ingredient=ingredient, series=data["series"], summary=data["summary"],
        stats=data["stats"], changes=data["changes"], range_key=range_key, ranges=sp.HISTORY_RANGES,
        watches=watches, stores=sp.STORES, store=store, query=query or _default_query(db, ingredient),
        searched=bool(store and query), results=results, search_error=search_error,
    )


@prices_bp.route("/prices/<int:ingredient_id>/watch", methods=["POST"])
@login_required
def prices_watch(ingredient_id):
    db = get_db()
    _ingredient(ingredient_id)
    link = request.form.get("link", "").strip()
    if link:
        parsed = sp.parse_product_link(link)
        if not parsed:
            flash("That isn't a Woolworths, Coles or Aldi product page address.", "warning")
            return redirect(url_for("prices.prices_detail", ingredient_id=ingredient_id))
        store, code, url = parsed["store"], parsed["code"], parsed["url"]
    else:
        store = sp.store_key(request.form.get("store"))
        code = request.form.get("code", "").strip()
        url = request.form.get("url", "").strip() or None
    if not store or not code:
        flash("Pick a product to track.", "warning")
        return redirect(url_for("prices.prices_detail", ingredient_id=ingredient_id))
    product = None
    from services.store_clients import StoreError
    try:
        product = _client().product(store, code, url)
    except StoreError as exc:
        flash(f"Tracking it, but today's price couldn't be read: {exc}", "warning")
    if not product:
        product = {"store": store, "code": code, "name": request.form.get("name", "").strip(), "url": url}
    watch_product(db, ingredient_id, dict(product, store=store, code=code), local_today().isoformat())
    flash(f"Tracking {product.get('name') or code} at {sp.store_label(store)}.", "success")
    return redirect(url_for("prices.prices_detail", ingredient_id=ingredient_id))


@prices_bp.route("/prices/<int:ingredient_id>/unwatch/<store>", methods=["POST"])
@login_required
def prices_unwatch(ingredient_id, store):
    if unwatch(get_db(), ingredient_id, store):
        flash(f"Stopped tracking {sp.store_label(store)} for this ingredient. Its saved prices stay.", "success")
    return redirect(url_for("prices.prices_detail", ingredient_id=ingredient_id))


@prices_bp.route("/prices/<int:ingredient_id>/check", methods=["POST"])
@login_required
def prices_check_one(ingredient_id):
    db = get_db()
    _ingredient(ingredient_id)
    from services.store_clients import StoreError
    client = _client()
    checked, day = 0, local_today().isoformat()
    from services.price_tracking import save_shelf_price
    for watch in load_watches(db, ingredient_id):
        try:
            product = client.product(watch["store"], watch["store_code"], watch["url"])
        except StoreError as exc:
            flash(f"{sp.store_label(watch['store'])}: {exc}", "warning")
            continue
        save_shelf_price(db, dict(product or {"price": None, "available": False},
                                  store=watch["store"], code=watch["store_code"]), day)
        checked += 1
    db.commit()
    if checked:
        flash(f"Checked {checked} store price{'s' if checked != 1 else ''}.", "success")
    return redirect(url_for("prices.prices_detail", ingredient_id=ingredient_id))


def _app_url():
    """This app's address for the bookmarklet. Behind the proxy the request says http, so force https."""
    host = request.host
    local = host.split(":")[0] in ("localhost", "127.0.0.1") or host.endswith((".localhost", ".test"))
    return f"{'http' if local else 'https'}://{host}"


def bookmarklet_href():
    """The capture bookmarklet as a javascript: link, pointing at this app."""
    code = render_template("prices/bookmarklet.js", app_url=_app_url())
    code = re.sub(r"\s*\n\s*", " ", code).strip()
    return "javascript:" + urllib.parse.quote(code, safe="(){};,=+'!*:.?/")


@prices_bp.route("/prices/capture")
@login_required
def prices_capture():
    return render_template("prices/capture.html", bookmarklet=bookmarklet_href())


@prices_bp.route("/prices/capture/review", methods=["POST"])
@login_required
def prices_capture_review():
    data = request.form.get("data", "")
    captured = sp.parse_capture(data)
    if not captured["store"]:
        flash("That didn't come from a Woolworths, Coles or Aldi page. Open one and click the bookmark again.",
              "warning")
        return redirect(url_for("prices.prices_capture"))
    review = capture_review(get_db(), captured)
    return render_template("prices/capture_review.html", captured=captured, rows=review["rows"],
                           ingredients=review["ingredients"], data=data, store=sp.STORES[captured["store"]])


@prices_bp.route("/prices/capture/save", methods=["POST"])
@login_required
def prices_capture_save():
    captured = sp.parse_capture(request.form.get("data", ""))
    if not captured["store"]:
        flash("Nothing to save.", "warning")
        return redirect(url_for("prices.prices_capture"))
    choices = {key[len("ingredient_"):]: int(value) for key, value in request.form.items()
               if key.startswith("ingredient_") and value.isdigit()}
    result = save_capture(get_db(), captured, choices, local_today().isoformat())
    label = sp.store_label(captured["store"])
    if result["saved"]:
        flash(f"Saved {result['saved']} {label} price{'s' if result['saved'] != 1 else ''}.", "success")
    elif not result["build"]:
        flash("No prices saved: none of these products are tracked, and none were picked.", "info")
    if result["build"]:
        flash("Coles is connected again: the daily check can read Coles prices.", "success")
    if len(set(result["watched"])) == 1:
        return redirect(url_for("prices.prices_detail", ingredient_id=result["watched"][0]))
    return redirect(url_for("prices.prices_list"))


def register_prices_routes(app):
    app.register_blueprint(prices_bp)

    @app.cli.command("check-prices")
    def check_prices_command():
        """Check today's shelf prices for every tracked product (for a daily scheduled task)."""
        result = run_check(app)
        if result is None:
            click.echo("A price check is already running.")
            return
        for _level, message in check_messages(result):
            click.echo(message)
