"""Spend analysis over imported purchases (pure Python port of the old pandas report).

analyze_purchases() is a pure function over purchase dicts; the small helpers at
the bottom run SQL for dashboard widgets.

One shop can reach the purchases table more than once: an itemised invoice and
the bank payment for it, both invoices of an amended order, or the pending and
posted copies of a bank payment. Rows are kept (products need the invoices) and
mark_counted() decides which ones are money, so totals never add a shop twice.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional
from models import parse_ymd

DAYS_PER_MONTH = 30.44
BANK_ORIGINS = ("banksync", "csv")
BANK_SETTLE_DAYS = 3  # a bank payment posts within this many days of the shop
AMENDED_MIN_SHARED = 3  # two small same-day shops can easily share a line or two
AMENDED_OVERLAP = 0.8


def _as_date(value):
    """Parse YYYY-MM-DD to a date; None when missing or unparseable.

    One legacy purchase row must not crash the whole spend report.
    """
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return parse_ymd(value)
    except (ValueError, TypeError):
        return None


def _is_bank(row: Dict) -> bool:
    return row.get("origin") in BANK_ORIGINS


def _basket_key(row: Dict) -> tuple:
    return row.get("store") or "", row["basket_id"]  # basket ids are only unique per store


def _has_value_date(row: Dict) -> bool:
    return "value date" in (row.get("product_name") or "").lower()


def _replaced_baskets(rows: List[Dict]) -> set:
    """Itemised baskets replaced by an amended order.

    Amending a Woolworths order issues a new order number with a new invoice, so
    the same day has two near-identical baskets. The later order number is the
    one that was delivered.
    """
    baskets: Dict[tuple, Dict] = {}
    for r in rows:
        if not _is_bank(r):
            basket = baskets.setdefault(_basket_key(r), {"date": r["order_date"], "names": set()})
            basket["names"].add(r["product_name"])
    by_day: Dict[tuple, List[tuple]] = defaultdict(list)
    for key, basket in baskets.items():
        by_day[(key[0], basket["date"])].append(key)

    replaced = set()
    for keys in by_day.values():
        keys.sort(key=lambda k: (len(str(k[1])), str(k[1])))
        for i, earlier in enumerate(keys):
            names = baskets[earlier]["names"]
            for later in keys[i + 1:]:
                other = baskets[later]["names"]
                shared = len(names & other)
                if shared >= AMENDED_MIN_SHARED and shared >= AMENDED_OVERLAP * min(len(names), len(other)):
                    replaced.add(earlier)
    return replaced


def _pending_bank_rows(rows: List[Dict]) -> set:
    """Indexes of bank rows that are the pending copy of a posted payment.

    Pending card rows have no "Value date", and the posted row arrives under a
    new transaction id, so the two only match on store, amount and timing.
    """
    posted: Dict[tuple, List] = defaultdict(list)
    for r in rows:
        if _is_bank(r) and _has_value_date(r):
            posted[(r.get("store"), round(float(r["line_total"] or 0), 2))].append(_as_date(r["order_date"]))
    pending = set()
    for i, r in enumerate(rows):
        day = _as_date(r["order_date"])
        if not _is_bank(r) or _has_value_date(r) or day is None:
            continue
        dates = posted.get((r.get("store"), round(float(r["line_total"] or 0), 2)), [])
        if any(d and 0 <= (d - day).days <= BANK_SETTLE_DAYS for d in dates):
            pending.add(i)
    return pending


def _bank_coverage(rows: List[Dict]) -> Dict[str, tuple]:
    """Store -> (first, last) date with bank rows, widened for settlement lag."""
    days: Dict[str, List[date]] = defaultdict(list)
    for r in rows:
        day = _as_date(r["order_date"])
        if _is_bank(r) and day:
            days[r.get("store") or ""].append(day)
    return {store: (min(v) - timedelta(days=BANK_SETTLE_DAYS), max(v)) for store, v in days.items()}


def mark_counted(purchases: Iterable[Dict]) -> List[Dict]:
    """Copy rows, adding ``counted`` (money) and ``itemised`` (a real product line).

    The bank is the record of what was paid, so inside a store's bank coverage
    its payments are the money and invoices only supply product detail. Outside
    it (older invoices, or ones newer than the last sync) invoices are the money.
    """
    rows = list(purchases)
    replaced = _replaced_baskets(rows)
    pending = _pending_bank_rows(rows)
    coverage = _bank_coverage(rows)
    marked = []
    for i, r in enumerate(rows):
        bank = _is_bank(r)
        day = _as_date(r["order_date"])
        span = coverage.get(r.get("store") or "")
        covered = bool(span and day and span[0] <= day <= span[1])
        is_replaced = not bank and _basket_key(r) in replaced
        marked.append({
            **r,
            "counted": i not in pending if bank else not (is_replaced or covered),
            "itemised": not bank and not is_replaced,
        })
    return marked


def analyze_purchases(purchases: Iterable[Dict], top_n: int = 20) -> Optional[Dict]:
    """Compute spend and frequency stats.

    Each purchase needs: order_date, basket_id, product_name, quantity, line_total.
    Optional: category (from a linked ingredient). Returns None for no data.
    Money comes from counted rows, products and categories from itemised rows.
    """
    rows = list(purchases)
    if not rows:
        return None
    if "counted" not in rows[0]:
        rows = mark_counted(rows)

    baskets: Dict[tuple, Dict] = {}
    monthly: Dict[str, float] = defaultdict(float)
    by_category: Dict[str, float] = defaultdict(float)
    by_store: Dict[str, float] = defaultdict(float)
    products: Dict[str, Dict] = {}
    items = 0

    for r in rows:
        d = _as_date(r["order_date"])
        if d is None:
            continue
        total = float(r["line_total"] or 0)
        basket_key = _basket_key(r)

        if r["counted"]:
            basket = baskets.setdefault(basket_key, {"date": d, "total": 0.0})
            basket["total"] += total
            monthly[d.isoformat()[:7]] += total
            by_store[r.get("store") or "Unknown"] += total
        if not r["itemised"]:
            continue

        items += 1
        by_category[r.get("category") or "unlinked"] += total

        p = products.setdefault(r["product_name"], {
            "product_name": r["product_name"], "baskets": set(), "total_qty": 0.0,
            "total_spend": 0.0, "first_bought": d, "last_bought": d,
            "ingredient_id": r.get("ingredient_id"),
        })
        p["baskets"].add(basket_key)
        p["total_qty"] += float(r["quantity"] or 0)
        p["total_spend"] += total
        p["first_bought"] = min(p["first_bought"], d)
        p["last_bought"] = max(p["last_bought"], d)

    product_rows = []
    for p in products.values():
        p["orders"] = len(p.pop("baskets"))
        p["total_spend"] = round(p["total_spend"], 2)
        p["total_qty"] = round(p["total_qty"], 2)
        product_rows.append(p)

    if not baskets:
        return None
    first_date = min(b["date"] for b in baskets.values())
    last_date = max(b["date"] for b in baskets.values())
    span_days = (last_date - first_date).days or 1
    total_spend = sum(b["total"] for b in baskets.values())
    shops = len(baskets)

    return {
        "total_spend": round(total_spend, 2),
        "total_shops": shops,
        "total_items": items,
        "first_date": first_date,
        "last_date": last_date,
        "span_days": span_days,
        "avg_days_between_shops": round(span_days / max(shops - 1, 1), 1),
        "avg_spend_per_month": round(total_spend / (span_days / DAYS_PER_MONTH), 2) if span_days >= DAYS_PER_MONTH else round(total_spend, 2),
        "avg_spend_per_shop": round(total_spend / shops, 2),
        "monthly_spend": [(m, round(v, 2)) for m, v in sorted(monthly.items())],
        "category_spend": sorted(((c, round(v, 2)) for c, v in by_category.items()), key=lambda x: -x[1]),
        "store_spend": [
            {"store": store, "total": round(value, 2),
             "share": round(100 * value / total_spend) if total_spend else 0}
            for store, value in sorted(by_store.items(), key=lambda x: -x[1])
        ],
        "top_by_frequency": sorted(product_rows, key=lambda p: (-p["orders"], -p["total_spend"]))[:top_n],
        "top_by_spend": sorted(product_rows, key=lambda p: -p["total_spend"])[:top_n],
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

PURCHASE_QUERY = """
    SELECT p.*, i.category AS category, i.name AS ingredient_name
    FROM purchases p LEFT JOIN ingredients i ON p.ingredient_id = i.id
"""


def load_purchases(db, start: Optional[str] = None, end: Optional[str] = None) -> List[Dict]:
    """Purchases in the range, marked against the whole table.

    Marking first means a window edge can't split a shop from its bank payment.
    """
    rows = [dict(r) for r in db.execute(PURCHASE_QUERY + " ORDER BY p.order_date")]
    return [r for r in mark_counted(rows)
            if (not start or r["order_date"] >= start) and (not end or r["order_date"] <= end)]


def load_spend(db, start: Optional[str] = None, end: Optional[str] = None) -> List[Dict]:
    """Only the rows that are money, for totals that don't go through analyze_purchases."""
    return [r for r in load_purchases(db, start, end) if r["counted"]]


def average_spend_per_shop(db) -> Optional[float]:
    stats = analyze_purchases(load_purchases(db), top_n=1)
    return stats["avg_spend_per_shop"] if stats else None


def summarise_recent_spend(db, days: int = 90) -> Optional[Dict]:
    """Small summary for the dashboard: last N days relative to the latest purchase."""
    latest = db.execute("SELECT MAX(order_date) FROM purchases").fetchone()[0]
    latest_day = _as_date(latest) if latest else None
    if latest_day is None:
        return None
    start = (latest_day - timedelta(days=days)).isoformat()
    stats = analyze_purchases(load_purchases(db, start=start), top_n=5)
    if stats:
        stats["window_days"] = days
    return stats
