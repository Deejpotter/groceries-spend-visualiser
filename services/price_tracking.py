"""Watched store products and saved shelf prices (the database side of store_prices).

An ingredient can watch one product per store. ``check_prices`` fetches each watched
product at most once a day and saves the price, building a shelf price history.
Woolworths products are watched automatically from the stockcode on the latest
linked invoice line; Coles and Aldi products are picked by hand from a search.
"""

import time
from datetime import date, timedelta
from typing import Dict, List, Optional

from services import store_prices as sp
from services.spend_import import product_url

REQUEST_GAP_SECONDS = 0.3


def load_watches(db, ingredient_id: Optional[int] = None) -> List[Dict]:
    sql = "SELECT * FROM price_watches"
    args = ()
    if ingredient_id is not None:
        sql += " WHERE ingredient_id = ?"
        args = (ingredient_id,)
    return [dict(r) for r in db.execute(sql + " ORDER BY ingredient_id, store", args)]


def save_shelf_price(db, product: Dict, day: str) -> None:
    db.execute(
        """INSERT INTO shelf_prices (store, store_code, checked_on, name, price, was_price, cup_price, available)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (store, store_code, checked_on) DO UPDATE SET name = excluded.name,
               price = excluded.price, was_price = excluded.was_price, cup_price = excluded.cup_price,
               available = excluded.available""",
        (product["store"], str(product["code"]), day, product.get("name"), product.get("price"),
         product.get("was_price"), product.get("cup_price"), 1 if product.get("available") else 0))


def watch_product(db, ingredient_id: int, product: Dict, day: str, origin: str = "manual") -> None:
    """Watch this store product for the ingredient (replacing that store's previous one)."""
    store = sp.store_key(product.get("store"))
    if not store or not product.get("code"):
        raise ValueError("A product needs a known store and code.")
    db.execute(
        """INSERT INTO price_watches (ingredient_id, store, store_code, name, url, origin)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (ingredient_id, store) DO UPDATE SET store_code = excluded.store_code,
               name = excluded.name, url = excluded.url, origin = excluded.origin""",
        (ingredient_id, store, str(product["code"]), product.get("name") or "", product.get("url"), origin))
    if product.get("price"):
        save_shelf_price(db, dict(product, store=store), day)
    db.commit()


def unwatch(db, ingredient_id: int, store: str) -> bool:
    cur = db.execute("DELETE FROM price_watches WHERE ingredient_id = ? AND store = ?",
                     (ingredient_id, sp.store_key(store) or store))
    db.commit()
    return cur.rowcount > 0


def auto_watch_woolworths(db) -> int:
    """Watch the Woolworths product behind each linked ingredient that has no Woolworths watch yet.

    Uses the stockcode on the ingredient's most recent linked purchase. Returns how many were added.
    """
    rows = db.execute(
        """SELECT p.ingredient_id, p.product_name, p.stockcode FROM purchases p
           WHERE p.ingredient_id IS NOT NULL AND p.stockcode IS NOT NULL AND p.stockcode != ''
             AND lower(p.store) = 'woolworths'
             AND NOT EXISTS (SELECT 1 FROM price_watches w
                             WHERE w.ingredient_id = p.ingredient_id AND w.store = 'woolworths')
           ORDER BY p.ingredient_id, p.order_date DESC, p.id DESC""").fetchall()
    added, seen = 0, set()
    for row in rows:
        code = str(row["stockcode"]).strip()
        if row["ingredient_id"] in seen or not code.isdigit():
            continue
        seen.add(row["ingredient_id"])
        db.execute(
            "INSERT OR IGNORE INTO price_watches (ingredient_id, store, store_code, name, url, origin)"
            " VALUES (?, 'woolworths', ?, ?, ?, 'auto')",
            (row["ingredient_id"], code, row["product_name"], product_url("woolworths", code)))
        added += 1
    db.commit()
    return added


def check_prices(db, today: date, client, pause: float = REQUEST_GAP_SECONDS) -> Dict:
    """Fetch today's price for every watched product not yet checked today.

    A store that fails once is skipped for the rest of the run (it is likely blocking
    or down), and its error is reported. A product the store no longer lists is saved
    as unavailable so it isn't fetched again today.
    """
    from services import store_clients
    from services.store_clients import StoreError
    day = today.isoformat()
    saved_build = db.execute("SELECT value FROM settings WHERE key = 'coles_build_id'").fetchone()
    if saved_build and not store_clients._coles_build_cache["id"]:
        store_clients._coles_build_cache["id"] = saved_build[0]
    auto_added = auto_watch_woolworths(db)
    products = db.execute(
        """SELECT DISTINCT w.store, w.store_code, w.url FROM price_watches w
           WHERE NOT EXISTS (SELECT 1 FROM shelf_prices s WHERE s.store = w.store
                             AND s.store_code = w.store_code AND s.checked_on = ?)
           ORDER BY w.store, w.store_code""", (day,)).fetchall()
    result = {"checked": 0, "unavailable": 0, "failed": {}, "auto_watched": auto_added}
    for row in products:
        if row["store"] in result["failed"]:
            continue
        try:
            product = client.product(row["store"], row["store_code"], row["url"])
        except StoreError as exc:
            result["failed"][row["store"]] = str(exc)
            continue
        if product and product.get("price"):
            save_shelf_price(db, dict(product, store=row["store"], code=row["store_code"]), day)
            result["checked"] += 1
        else:
            save_shelf_price(db, {"store": row["store"], "code": row["store_code"], "price": None,
                                  "available": False}, day)
            result["unavailable"] += 1
        db.commit()
        if pause:
            time.sleep(pause)
    build = store_clients._coles_build_cache["id"]
    if build and (not saved_build or saved_build[0] != build):
        db.execute("INSERT INTO settings (key, value) VALUES ('coles_build_id', ?)"
                   " ON CONFLICT (key) DO UPDATE SET value = excluded.value", (build,))
        db.commit()
    return result


def check_messages(result: Dict) -> List[tuple]:
    """Flash messages for a check run."""
    messages = []
    if result["checked"] or result["unavailable"] or not result["failed"]:
        text = f"Checked {result['checked']} shelf price{'s' if result['checked'] != 1 else ''}."
        if result["unavailable"]:
            text += f" {result['unavailable']} no longer listed."
        messages.append(("success", text))
    for store, error in result["failed"].items():
        messages.append(("warning", f"{sp.store_label(store)} prices weren't checked: {error}"))
    return messages


def _paid_rows(db, ingredient_id: int, since: str) -> List[Dict]:
    return [dict(r) for r in db.execute(
        """SELECT order_date, store, product_name, unit_price, cup_price FROM purchases
           WHERE ingredient_id = ? AND order_date >= ? AND unit_price > 0 ORDER BY order_date""",
        (ingredient_id, since))]


def _shelf_rows(db, ingredient_id: int, since: str) -> List[Dict]:
    return [dict(r) for r in db.execute(
        """SELECT s.store, s.store_code, s.checked_on, s.name, s.price, s.was_price, s.cup_price, s.available
           FROM shelf_prices s JOIN price_watches w ON w.store = s.store AND w.store_code = s.store_code
           WHERE w.ingredient_id = ? AND s.checked_on >= ? ORDER BY s.checked_on""",
        (ingredient_id, since))]


def ingredient_prices(db, ingredient: Dict, today: date, days: int = 365) -> Dict:
    """History series and summary for one ingredient."""
    since = (today - timedelta(days=days)).isoformat()
    series = sp.price_history(ingredient, _paid_rows(db, ingredient["id"], since),
                              _shelf_rows(db, ingredient["id"], since))
    return {"series": series, "summary": sp.price_summary(series, today)}


def prices_overview(db, today: date) -> List[Dict]:
    """Every ingredient with a watched product, with its summary, biggest saving first."""
    ingredients = db.execute(
        """SELECT DISTINCT i.id, i.name, i.display_name, i.unit, i.pack_size, i.price
           FROM ingredients i JOIN price_watches w ON w.ingredient_id = i.id ORDER BY i.name""").fetchall()
    rows = []
    for ing in ingredients:
        ingredient = dict(ing)
        data = ingredient_prices(db, ingredient, today, days=200)
        rows.append({"ingredient": ingredient, "summary": data["summary"],
                     "stores": {s["store"]: s for s in data["summary"]["stores"]}})
    rows.sort(key=lambda r: (-(r["summary"]["saving_per_unit"] or 0), r["ingredient"]["name"].lower()))
    return rows


class FakeStoreClient:
    """A stand-in client for tests: ``products`` maps (store, code) -> product dict or an exception."""

    def __init__(self, products=None, results=None):
        self.products = products or {}
        self.results = results or {}
        self.calls = []

    def product(self, store, code, url=None):
        self.calls.append((store, str(code)))
        value = self.products.get((store, str(code)))
        if isinstance(value, Exception):
            raise value
        return value

    def search(self, store, query):
        self.calls.append((store, query))
        value = self.results.get(store, [])
        if isinstance(value, Exception):
            raise value
        return value
