"""How fast steady items get used, from purchases and stock counts.

Pure functions over plain dicts, no Flask, no SQL. Counts and limits are in
packs, the same unit purchases are recorded in ("8 long-life milks"), so no
unit conversion is needed. Purchase gaps say when something was bought; usage
says how much is needed: a steady item like milk is predicted from its rate
even when the days it gets bought vary.
"""

import math
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional

from models import parse_ymd
from services.purchase_patterns import group_key, normalise_key
from services.spend_analysis import BANK_ORIGINS

STEADY_MIN_BUYS = 6
MIN_SPAN_DAYS = 28


def _as_date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return parse_ymd(str(value)[:10])
    except (TypeError, ValueError):
        return None


def daily_packs(purchases: Iterable[Dict]) -> Dict[str, Dict[date, float]]:
    """Packs bought per item key per day.

    One shop can be imported twice (an order-history CSV and its invoice), so
    each day keeps the largest basket rather than the sum of baskets. Bank
    payments are whole shops, not products, so they are left out.
    """
    baskets: Dict[tuple, float] = {}
    for row in purchases:
        if row.get("origin") in BANK_ORIGINS:
            continue
        day = _as_date(row.get("order_date"))
        if not day or not row.get("product_name"):
            continue
        k = (group_key(row), day, row.get("store"), row.get("basket_id"))
        baskets[k] = baskets.get(k, 0.0) + float(row.get("quantity") or 1)
    days: Dict[str, Dict[date, float]] = {}
    for (key, day, _, _), qty in baskets.items():
        per_day = days.setdefault(key, {})
        per_day[day] = max(per_day.get(day, 0.0), qty)
    return days


def bought_between(bought: Dict[date, float], after: date, until: date) -> float:
    """Packs bought after ``after`` up to and including ``until``."""
    return sum(q for d, q in bought.items() if after < d <= until)


def usage_rate(bought: Dict[date, float], counts: List[Dict], today: date) -> Optional[Dict]:
    """Packs used per day, or None when there isn't enough to go on.

    Between two stock counts, used = earlier count + bought since - later count;
    those spans win when any exist. Otherwise the rate is everything bought
    since the first buy (at least four weeks back) spread over that time.
    Returns {"per_day", "source": "counts" | "purchases"}.
    """
    ordered = sorted((c for c in counts if _as_date(c.get("counted_on"))),
                     key=lambda c: c["counted_on"])
    used = span = 0.0
    for a, b in zip(ordered, ordered[1:]):
        start, end = _as_date(a["counted_on"]), _as_date(b["counted_on"])
        days = (end - start).days
        used_here = float(a["quantity"]) + bought_between(bought, start, end) - float(b["quantity"])
        if days > 0 and used_here >= 0:
            used += used_here
            span += days
    if span:
        return {"per_day": used / span, "source": "counts"}
    if not bought:
        return None
    first = min(bought)
    days = max((today - first).days, MIN_SPAN_DAYS)
    return {"per_day": sum(bought.values()) / days, "source": "purchases"}


def on_hand(bought: Dict[date, float], counts: List[Dict], per_day: float, today: date) -> Optional[float]:
    """Packs probably left today: the last count plus buys since, minus usage since. None if never counted."""
    dated = [c for c in counts if _as_date(c.get("counted_on"))]
    if not dated:
        return None
    last = max(dated, key=lambda c: c["counted_on"])
    day = _as_date(last["counted_on"])
    left = float(last["quantity"]) + bought_between(bought, day, today) - per_day * (today - day).days
    return max(0.0, round(left, 2))


def packs_needed(per_day: float, days: int, have: Optional[float],
                 max_on_hand: Optional[float] = None, keep_days: Optional[int] = None) -> int:
    """Whole packs to buy now so the item lasts ``days``.

    Perishables (``keep_days``) are only bought for as long as they keep; a top-up
    shop covers the rest. ``max_on_hand`` caps what fits in the cupboard.
    """
    cover = per_day * days
    if keep_days:
        cover = min(cover, per_day * keep_days)
    packs = math.ceil(round(cover - (have or 0), 2))
    if max_on_hand:
        packs = min(packs, math.floor(round(max_on_hand - (have or 0), 2)))  # never over the cap
    return max(0, packs)


def is_tracked(pattern: Dict, settings: Optional[Dict], counts: List[Dict]) -> bool:
    """Steady buys, and anything the user has counted or given limits, use the usage model."""
    if settings and settings.get("track") == 0:
        return False
    return bool(counts) or bool(settings) or pattern.get("buys", 0) >= STEADY_MIN_BUYS


def _per_week(per_day: float) -> str:
    week = per_day * 7
    return f"{week:.1f}".rstrip("0").rstrip(".") if week < 10 else str(round(week))


def usage_needs(patterns: List[Dict], purchases: Iterable[Dict], settings: Dict[str, Dict],
                counts: Dict[str, List[Dict]], start: date, end: date, today: date) -> Dict[str, Optional[Dict]]:
    """Usage-based needs for tracked items, keyed by pattern key.

    A value of None means the item is tracked and nothing is needed this time,
    so the caller should drop any rhythm-based guess for it. Untracked items are
    left out, and keep their purchase-rhythm prediction.
    """
    bought = daily_packs(purchases)
    days = (end - start).days + 1
    result: Dict[str, Optional[Dict]] = {}
    for pattern in patterns:
        key = pattern["key"]
        item_settings = settings.get(key) or {}
        item_counts = counts.get(key, [])
        if pattern.get("rhythm") == "one_off" or not is_tracked(pattern, item_settings, item_counts):
            continue
        rate = usage_rate(bought.get(key, {}), item_counts, today)
        if not rate or rate["per_day"] <= 0:
            continue
        have = on_hand(bought.get(key, {}), item_counts, rate["per_day"], today)
        if have is not None:
            have = max(0.0, have - rate["per_day"] * max(0, (start - today).days))
        packs = packs_needed(rate["per_day"], days, have,
                             item_settings.get("max_on_hand"), item_settings.get("keep_days"))
        if not packs:
            result[key] = None
            continue
        reason = f"Uses about {_per_week(rate['per_day'])} a week"
        reason += " (from your counts)" if rate["source"] == "counts" else " (from purchases)"
        if have is not None:
            reason += f"; about {have:g} left"
        if item_settings.get("max_on_hand"):
            reason += f"; keep at most {item_settings['max_on_hand']:g}"
        if item_settings.get("keep_days"):
            reason += f"; keeps {item_settings['keep_days']} days, top up later"
        unit_cost = (pattern.get("typical_spend") or 0) / (pattern.get("typical_quantity") or 1)
        result[key] = {
            **pattern,
            "times": 1,
            "quantity": packs,
            "estimated_cost": round(packs * unit_cost, 2) if unit_cost else None,
            "first_due": start.isoformat(),
            "usage_reason": reason,
            "per_day": rate["per_day"],
            "on_hand": have,
        }
    return result


def merge_needs(rhythm_needs: List[Dict], usage: Dict[str, Optional[Dict]]) -> List[Dict]:
    """Rhythm needs with tracked items replaced (or removed) by their usage need."""
    merged = [n for n in rhythm_needs if n["key"] not in usage]
    merged += [n for n in usage.values() if n]
    return merged


def stock_rows(patterns: List[Dict], purchases: Iterable[Dict], settings: Dict[str, Dict],
               counts: Dict[str, List[Dict]], today: date) -> List[Dict]:
    """Items for the stock count page: tracked items first, each with its rate and estimate."""
    bought = daily_packs(purchases)
    rows = []
    for pattern in patterns:
        key = pattern["key"]
        item_settings = settings.get(key) or {}
        item_counts = counts.get(key, [])
        if pattern.get("rhythm") == "one_off" or not is_tracked(pattern, item_settings, item_counts):
            continue
        rate = usage_rate(bought.get(key, {}), item_counts, today)
        if not rate:
            continue  # nothing bought as products (e.g. only bank payments for a whole shop)
        per_day = rate["per_day"]
        last = max(item_counts, key=lambda c: c["counted_on"]) if item_counts else None
        rows.append({
            "key": key,
            "label": pattern["label"],
            "products": pattern.get("products") or [],
            "role_label": pattern.get("role_label"),
            "per_week": _per_week(per_day) if per_day else None,
            "rate_source": rate["source"] if rate else None,
            "on_hand": on_hand(bought.get(key, {}), item_counts, per_day, today) if per_day else None,
            "last_count": last,
            "max_on_hand": item_settings.get("max_on_hand"),
            "keep_days": item_settings.get("keep_days"),
        })
    return sorted(rows, key=lambda r: r["label"].lower())


def counts_by_key(rows: Iterable[Dict]) -> Dict[str, List[Dict]]:
    found: Dict[str, List[Dict]] = {}
    for row in rows:
        found.setdefault(normalise_key(row["item_key"]), []).append(dict(row))
    return found


def make_count(item_key: str = "ing:1", counted_on: str = "2026-11-01", quantity: float = 1) -> Dict:
    """A stock count row for tests."""
    return {"item_key": item_key, "counted_on": counted_on, "quantity": quantity}
