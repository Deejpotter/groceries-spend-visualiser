"""Spend analysis over imported purchases (pure Python port of the old pandas report).

analyze_purchases() is a pure function over purchase dicts; the small helpers at
the bottom run SQL for dashboard widgets.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional

DAYS_PER_MONTH = 30.44


def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def analyze_purchases(purchases: Iterable[Dict], top_n: int = 20) -> Optional[Dict]:
    """Compute spend and frequency stats.

    Each purchase needs: order_date, basket_id, product_name, quantity, line_total.
    Optional: category (from a linked ingredient). Returns None for no data.
    """
    rows = list(purchases)
    if not rows:
        return None

    baskets: Dict[str, Dict] = {}
    monthly: Dict[str, float] = defaultdict(float)
    by_category: Dict[str, float] = defaultdict(float)
    products: Dict[str, Dict] = {}

    for r in rows:
        d = _as_date(r["order_date"])
        total = float(r["line_total"] or 0)

        basket = baskets.setdefault(r["basket_id"], {"date": d, "total": 0.0})
        basket["total"] += total
        monthly[d.strftime("%Y-%m")] += total
        by_category[r.get("category") or "unlinked"] += total

        p = products.setdefault(r["product_name"], {
            "product_name": r["product_name"], "baskets": set(), "total_qty": 0.0,
            "total_spend": 0.0, "first_bought": d, "last_bought": d,
            "ingredient_id": r.get("ingredient_id"),
        })
        p["baskets"].add(r["basket_id"])
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

    first_date = min(b["date"] for b in baskets.values())
    last_date = max(b["date"] for b in baskets.values())
    span_days = (last_date - first_date).days or 1
    total_spend = sum(b["total"] for b in baskets.values())
    shops = len(baskets)

    return {
        "total_spend": round(total_spend, 2),
        "total_shops": shops,
        "total_items": len(rows),
        "first_date": first_date,
        "last_date": last_date,
        "span_days": span_days,
        "avg_days_between_shops": round(span_days / max(shops - 1, 1), 1),
        "avg_spend_per_month": round(total_spend / (span_days / DAYS_PER_MONTH), 2) if span_days >= DAYS_PER_MONTH else round(total_spend, 2),
        "avg_spend_per_shop": round(total_spend / shops, 2),
        "monthly_spend": [(m, round(v, 2)) for m, v in sorted(monthly.items())],
        "category_spend": sorted(((c, round(v, 2)) for c, v in by_category.items()), key=lambda x: -x[1]),
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
    sql, params = PURCHASE_QUERY + " WHERE 1=1", []
    if start:
        sql += " AND p.order_date >= ?"
        params.append(start)
    if end:
        sql += " AND p.order_date <= ?"
        params.append(end)
    return [dict(r) for r in db.execute(sql + " ORDER BY p.order_date", params)]


def average_spend_per_shop(db) -> Optional[float]:
    row = db.execute(
        "SELECT AVG(total) FROM (SELECT SUM(line_total) AS total FROM purchases GROUP BY basket_id)"
    ).fetchone()
    return round(row[0], 2) if row and row[0] is not None else None


def summarise_recent_spend(db, days: int = 90) -> Optional[Dict]:
    """Small summary for the dashboard: last N days relative to the latest purchase."""
    latest = db.execute("SELECT MAX(order_date) FROM purchases").fetchone()[0]
    if not latest:
        return None
    start = (_as_date(latest) - timedelta(days=days)).isoformat()
    stats = analyze_purchases(load_purchases(db, start=start), top_n=5)
    if stats:
        stats["window_days"] = days
    return stats
