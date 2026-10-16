"""Subscription and bill tracking — pure functions over plain dicts.

Each subscription is a dict with: id, name, amount, frequency, category,
next_due, active, notes. Frequencies: weekly, fortnightly, monthly, quarterly,
yearly. These helpers answer "what's due in this fortnight?" without touching
Flask or SQL so they are easy to unit-test.
"""

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

FREQUENCIES = ["weekly", "fortnightly", "monthly", "quarterly", "yearly"]

FREQUENCY_LABELS = {
    "weekly": "Weekly",
    "fortnightly": "Fortnightly",
    "monthly": "Monthly",
    "quarterly": "Quarterly",
    "yearly": "Yearly",
}

SUBSCRIPTION_CATEGORIES = [
    ("bills", "Bills"),
    ("entertainment", "Entertainment"),
    ("insurance", "Insurance"),
    ("utilities", "Utilities"),
    ("rent", "Rent & Housing"),
    ("health", "Health"),
    ("transport", "Transport"),
    ("education", "Education"),
    ("other", "Other"),
]

SUBSCRIPTION_CATEGORY_LOOKUP = {code: label for code, label in SUBSCRIPTION_CATEGORIES}

# Fixed-day intervals for weekly/fortnightly. Monthly/quarterly/yearly use
# calendar arithmetic (add_months) so a bill on the 14th stays on the 14th.
_FREQUENCY_DAYS = {
    "weekly": 7,
    "fortnightly": 14,
}

def _as_date(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _add_months(d: date, months: int) -> date:
    """Add calendar months to a date, clamping the day to the target month."""
    total = d.year * 12 + (d.month - 1) + months
    year, month_idx = divmod(total, 12)
    month = month_idx + 1
    max_day = monthrange(year, month)[1]
    return date(year, month, min(d.day, max_day))


def _add_years(d: date, years: int) -> date:
    """Add calendar years, clamping leap-day results to Feb 28."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return date(d.year + years, 2, 28)


def _advance_by_frequency(d: date, freq: str) -> date:
    """Advance a date by one frequency interval using calendar arithmetic."""
    if freq == "monthly":
        return _add_months(d, 1)
    if freq == "quarterly":
        return _add_months(d, 3)
    if freq == "yearly":
        return _add_years(d, 1)
    return d + timedelta(days=_FREQUENCY_DAYS[freq])


def fortnight_for_date(d: date) -> tuple:
    """Return (start, end) of the Monday-Sunday fortnight containing date d.

    Fortnights run Monday to Sunday (14 days). A Sunday is the last day of a
    fortnight, so it maps back to the Monday 13 days earlier — not the Monday
    of its own week.
    """
    monday = d - timedelta(days=d.weekday())
    if d.weekday() == 6:
        monday -= timedelta(days=7)
    end = monday + timedelta(days=13)
    return monday, end


def next_occurrence(subscription: Dict, from_date: Optional[date] = None) -> Optional[date]:
    """Next due date on or after from_date for a subscription."""
    freq = (subscription.get("frequency") or "").lower()
    if freq not in _FREQUENCY_DAYS and freq not in ("monthly", "quarterly", "yearly"):
        return None
    anchor = _as_date(subscription.get("next_due"))
    if anchor is None:
        return None
    from_date = from_date or date.today()
    current = anchor
    while current < from_date:
        current = _advance_by_frequency(current, freq)
    return current


def is_due_in_range(subscription: Dict, start: date, end: date) -> bool:
    """True if the subscription has a payment due within [start, end]."""
    if not subscription.get("active", 1):
        return False
    freq = (subscription.get("frequency") or "").lower()
    if freq not in _FREQUENCY_DAYS and freq not in ("monthly", "quarterly", "yearly"):
        return False
    anchor = _as_date(subscription.get("next_due"))
    if anchor is None:
        return False
    delta = _FREQUENCY_DAYS.get(freq)
    current = anchor
    if delta:
        while current < start - timedelta(days=delta):
            current += timedelta(days=delta)
    else:
        while current < start:
            current = _advance_by_frequency(current, freq)
    while current <= end:
        if start <= current <= end:
            return True
        if delta:
            current += timedelta(days=delta)
        else:
            current = _advance_by_frequency(current, freq)
    return False


def subscriptions_due_in_range(subscriptions: List[Dict], start: date, end: date) -> List[Dict]:
    """Filter subscriptions with a payment due in [start, end]."""
    return [s for s in subscriptions if is_due_in_range(s, start, end)]


def fortnightly_subscription_total(subscriptions: List[Dict], start: date, end: date) -> float:
    """Sum of subscription amounts due in [start, end]."""
    due = subscriptions_due_in_range(subscriptions, start, end)
    return round(sum(float(s.get("amount") or 0) for s in due), 2)


def format_frequency(freq: str) -> str:
    """Human-readable label for a frequency code."""
    return FREQUENCY_LABELS.get(freq, freq.title() if freq else "")


def subscription_category_label(code: str) -> str:
    """Human-readable label for a subscription category code."""
    return SUBSCRIPTION_CATEGORY_LOOKUP.get(code, code)


def mark_paid(subscription: Dict, today: Optional[date] = None) -> tuple:
    """Advance for the paid cycle, forgiving any earlier missed cycles.

    Returns (new_due_iso, skipped): the next due date and how many extra
    cycles were caught up beyond the one just paid. A bill three cycles
    overdue lands on the next upcoming date instead of staying overdue.
    """
    freq = (subscription.get("frequency") or "").lower()
    anchor = _as_date(subscription.get("next_due"))
    if freq not in _FREQUENCY_DAYS and freq not in ("monthly", "quarterly", "yearly"):
        return subscription.get("next_due") or "", 0
    if anchor is None:
        return subscription.get("next_due") or "", 0
    today = today or date.today()
    new_due = _advance_by_frequency(anchor, freq)  # the cycle just paid
    skipped = 0
    while new_due < today:
        new_due = _advance_by_frequency(new_due, freq)
        skipped += 1
    return new_due.isoformat(), skipped


def advance_next_due(subscription: Dict) -> str:
    """Advance next_due by one frequency, returning an ISO date string."""
    freq = (subscription.get("frequency") or "").lower()
    if freq not in _FREQUENCY_DAYS and freq not in ("monthly", "quarterly", "yearly"):
        return subscription.get("next_due") or ""
    current = _as_date(subscription.get("next_due"))
    if current is None:
        return subscription.get("next_due") or ""
    return _advance_by_frequency(current, freq).isoformat()


def suggested_next_due(freq: str, last_seen) -> Optional[str]:
    """Best-guess next due date after a sighting: last seen + one interval."""
    anchor = _as_date(last_seen)
    if anchor is None:
        return None
    freq = (freq or "").lower()
    if freq not in _FREQUENCY_DAYS and freq not in ("monthly", "quarterly", "yearly"):
        return None
    return _advance_by_frequency(anchor, freq).isoformat()


def suggest_from_unclassified(unclassified: List[Dict]) -> List[Dict]:
    """Suggest subscriptions from BankSync unclassified transactions.

    Matches known Australian merchant names and surfaces recurring descriptions.
    """
    KNOWN_MERCHANTS = [
        ("netflix", "Netflix", "monthly", "entertainment"),
        ("spotify", "Spotify", "monthly", "entertainment"),
        ("stan", "Stan", "monthly", "entertainment"),
        ("disney", "Disney+", "monthly", "entertainment"),
        ("amazon prime", "Amazon Prime", "monthly", "entertainment"),
        ("youtube", "YouTube Premium", "monthly", "entertainment"),
        ("apple", "Apple Services", "monthly", "entertainment"),
        ("microsoft", "Microsoft", "monthly", "entertainment"),
        ("agl", "AGL Energy", "quarterly", "utilities"),
        ("origin energy", "Origin Energy", "quarterly", "utilities"),
        ("energyaustralia", "EnergyAustralia", "monthly", "utilities"),
        ("aami", "AAMI Insurance", "monthly", "insurance"),
        ("racv", "RACV Insurance", "monthly", "insurance"),
        ("nrma", "NRMA Insurance", "monthly", "insurance"),
        ("bupa", "Bupa Health", "monthly", "health"),
        ("medibank", "Medibank Health", "monthly", "health"),
        ("telstra", "Telstra", "monthly", "bills"),
        ("optus", "Optus", "monthly", "bills"),
        ("voda", "Vodafone", "monthly", "bills"),
        ("foxtel", "Foxtel", "monthly", "entertainment"),
        ("nrl", "NRL/Telstra", "yearly", "entertainment"),
        ("nrl ticket", "NRL Tickets", "yearly", "entertainment"),
        ("iinet", "iiNet", "monthly", "bills"),
        ("tpg", "TPG Internet", "monthly", "bills"),
        ("austar", "Austar", "monthly", "entertainment"),
        ("drops", "Gym/Health", "monthly", "health"),
        ("fitness", "Gym/Fitness", "monthly", "health"),
        ("jetts", "Jetts Fitness", "monthly", "health"),
        ("anytime", "Anytime Fitness", "monthly", "health"),
        ("snap", "Snap Fitness", "monthly", "health"),
        ("jim", "Jim's Fitness", "monthly", "health"),
    ]

    suggestions = []
    seen = set()

    for row in unclassified:
        desc = (row.get("description") or row.get("name") or "").lower()
        amount = float(row.get("amount") or 0)
        if not desc or amount <= 0:
            continue

        for keyword, name, freq, cat in KNOWN_MERCHANTS:
            if keyword in desc:
                key = (name, round(amount, 2))
                if key not in seen:
                    seen.add(key)
                    suggestions.append({
                        "name": name,
                        "amount": round(amount, 2),
                        "frequency": freq,
                        "category": cat,
                        "confidence": "high",
                        "source": "banksync",
                        "reason": f"Matches known merchant: {name}",
                        "next_due": None,  # bank rows carry no usable date here
                    })
                break

    desc_groups: Dict[str, Dict] = {}
    for row in unclassified:
        desc = (row.get("description") or row.get("name") or "").strip()
        amount = float(row.get("amount") or 0)
        if not desc or amount <= 0:
            continue
        key = desc.lower()
        if key not in desc_groups:
            desc_groups[key] = {"count": 0, "amount": amount, "original": desc}
        group = desc_groups[key]
        group["count"] += 1

    for key, group in desc_groups.items():
        if group["count"] >= 2 and (group["original"].lower(), round(group["amount"], 2)) not in seen:
            desc_lower = group["original"].lower()
            already_matched = any(kw in desc_lower for kw, _, _, _ in KNOWN_MERCHANTS)
            if not already_matched:
                seen.add((group["original"].lower(), round(group["amount"], 2)))
                suggestions.append({
                    "name": group["original"],
                    "amount": round(group["amount"], 2),
                    "frequency": "monthly",
                    "category": "other",
                    "confidence": "medium",
                    "source": "banksync",
                    "reason": f"Appears {group['count']} times in bank feed",
                })

    return sorted(suggestions, key=lambda s: (s["confidence"] != "high", s["name"]))


def suggest_from_purchases(purchases: List[Dict]) -> List[Dict]:
    """Suggest subscriptions from purchase history (recurrence detection)."""
    from collections import defaultdict

    groups: Dict[tuple, Dict] = defaultdict(lambda: {"dates": [], "amounts": [], "store": ""})
    for row in purchases:
        store = (row.get("store") or "").lower()
        name = (row.get("product_name") or row.get("description") or "").lower()
        amount = float(row.get("line_total") or row.get("amount") or 0)
        order_date = _as_date(row.get("order_date"))
        if not store or not name or amount <= 0 or order_date is None:
            continue
        key = (store, name)
        group = groups[key]
        group["dates"].append(order_date)
        group["amounts"].append(amount)
        group["store"] = row.get("store") or ""

    suggestions = []
    seen = set()

    for (store, name), group in groups.items():
        if len(group["dates"]) < 3:
            continue

        dates = sorted(group["dates"])
        amounts = group["amounts"]
        avg_amount = sum(amounts) / len(amounts)

        amount_variance = max(abs(a - avg_amount) for a in amounts) / avg_amount if avg_amount else 0
        if amount_variance > 0.20:
            continue

        intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        if not intervals:
            continue
        avg_interval = sum(intervals) / len(intervals)

        if 5 <= avg_interval <= 9:
            freq = "weekly"
        elif 12 <= avg_interval <= 16:
            freq = "fortnightly"
        elif 27 <= avg_interval <= 33:
            freq = "monthly"
        elif 85 <= avg_interval <= 97:
            freq = "quarterly"
        elif 350 <= avg_interval <= 380:
            freq = "yearly"
        else:
            continue

        interval_variance = max(abs(i - avg_interval) for i in intervals)
        if interval_variance > max(3, avg_interval * 0.2):
            continue

        key = (name, round(avg_amount, 2))
        if key not in seen:
            seen.add(key)
            last_seen = max(dates).isoformat()
            suggestions.append({
                "name": name.title(),
                "amount": round(avg_amount, 2),
                "frequency": freq,
                "category": "other",
                "confidence": "medium",
                "source": "purchases",
                "reason": f"Bought {len(dates)} times, ~{avg_interval:.0f} days apart",
                "last_seen": last_seen,
                "next_due": suggested_next_due(freq, last_seen),
            })

    return sorted(suggestions, key=lambda s: (s["confidence"] != "high", s["name"]))


# ---------------------------------------------------------------------------
# Test data helpers (following the services/* make_* pattern)
# ---------------------------------------------------------------------------

def make_subscription(
    id: int = 1,
    name: str = "Test Subscription",
    amount: float = 10.0,
    frequency: str = "monthly",
    category: str = "bills",
    next_due: str = "2026-10-15",
    active: int = 1,
    notes: str = None,
) -> Dict:
    """Create a subscription dict for testing."""
    return {
        "id": id,
        "name": name,
        "amount": amount,
        "frequency": frequency,
        "category": category,
        "next_due": next_due,
        "active": active,
        "notes": notes,
    }
