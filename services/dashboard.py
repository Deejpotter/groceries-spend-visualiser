"""Dashboard metrics - pure functions over plain rows.

The home dashboard loads raw rows and calls these to get at-a-glance numbers.
Keeping them free of Flask/SQL makes the metrics easy to unit-test. Each result
carries any presentational decision (tone, delta, percent) so templates only
render, and money is still formatted with the |money filter at the edge.
"""

from datetime import date, datetime
from typing import Dict, List, Optional

from models import today as local_today


def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def plan_coverage(entries, start, end) -> Dict:
    """Days in [start, end] that have at least one planned meal.

    entries: [{date}]. A two-night recipe's second day counts as covered because
    its continuation entry falls on that date. Returns days_total, days_planned
    and pct (0-100).
    """
    first, last = _as_date(start), _as_date(end)
    days_total = (last - first).days + 1 if last >= first else 0
    planned = {str(r["date"])[:10] for r in entries if first <= _as_date(r["date"]) <= last}
    days_planned = len(planned)
    return {
        "days_total": days_total,
        "days_planned": days_planned,
        "pct": round(100 * days_planned / days_total) if days_total else 0,
    }


def shopping_summary(rows) -> Dict:
    """Item count, estimated cost and ticked progress from shopping-list rows.

    rows: [{estimated_cost, checked, is_manual}]. Manual rows carry no cost.
    """
    items = len(rows)
    checked = sum(1 for r in rows if r.get("checked"))
    cost = round(sum(float(r.get("estimated_cost") or 0) for r in rows if not r.get("is_manual")), 2)
    return {
        "item_count": items,
        "checked": checked,
        "cost": cost,
        "pct": round(100 * checked / items) if items else 0,
    }


def pantry_alerts(items, today=None, soon_days: int = 3) -> Dict:
    """Split pantry rows into expired / expiring-soon / below-minimum-stock.

    items: [{quantity, expiry_date, minimum_stock, ...}]. Alert rows carry the
    original data plus 'days_left' and a 'status' of 'expired' or 'soon'. The
    overall 'tone' tells the dashboard which colour rail to use: 'danger' when
    anything has expired, 'warn' for expiry/low-stock pressure, else 'ok'.
    """
    today = today or local_today()
    expired: List[Dict] = []
    expiring: List[Dict] = []
    low: List[Dict] = []
    for it in items:
        row = dict(it)
        expiry = row.get("expiry_date")
        if expiry:
            days_left = (_as_date(expiry) - today).days
            row["days_left"] = days_left
            if days_left < 0:
                row["status"] = "expired"
                expired.append(row)
            elif days_left <= soon_days:
                row["status"] = "soon"
                expiring.append(row)
        minimum = row.get("minimum_stock") or 0
        if minimum and (row.get("quantity") or 0) < minimum:
            low.append(row)

    if expired:
        tone = "danger"
    elif expiring or low:
        tone = "warn"
    else:
        tone = "ok"
    return {
        "expired": expired,
        "expiring": expiring,
        "low": low,
        "expired_count": len(expired),
        "expiring_count": len(expiring),
        "low_count": len(low),
        "tone": tone,
    }


def spend_window(rows) -> Dict:
    """Total, shop count and average-per-shop from purchase rows.

    rows: [{store, basket_id, line_total}]. A shop is one (store, basket_id)
    pair, since basket ids are only unique per store.
    """
    baskets: Dict[tuple, float] = {}
    for r in rows:
        key = (r.get("store") or "", r.get("basket_id"))
        baskets[key] = baskets.get(key, 0.0) + float(r.get("line_total") or 0)
    total = round(sum(baskets.values()), 2)
    shops = len(baskets)
    return {
        "total": total,
        "shops": shops,
        "avg_per_shop": round(total / shops, 2) if shops else None,
    }


def trend(current: Optional[float], previous: Optional[float]) -> Optional[Dict]:
    """Percent change of `current` vs `previous`, for a KPI trend delta.

    Returns {'pct', 'direction'} with direction 'up' | 'down' | 'flat', or None
    when there is no meaningful previous value to compare against.
    """
    if current is None or previous in (None, 0):
        return None
    pct = round((current - previous) / abs(previous) * 100)
    if pct == 0:
        direction = "flat"
    elif pct > 0:
        direction = "up"
    else:
        direction = "down"
    return {"pct": abs(pct), "direction": direction}


def sparkline(values, width: int = 84, height: int = 28) -> str:
    """Polyline points for a tiny sparkline fitted to a width x height box.

    Returns 'x,y x,y ...' for an SVG <polyline points="...">, or '' when fewer
    than two values are present. The math lives here so templates only render.
    """
    vals = [float(v) for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    step = width / (len(vals) - 1)
    pad = 2.0
    inner = height - 2 * pad
    points = []
    for i, v in enumerate(vals):
        x = i * step
        y = pad + inner - (v - lo) / span * inner
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)
