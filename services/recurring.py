"""Recurring payment detection — pure functions over bank transactions.

Groups debit transactions by a normalised merchant key and looks for a stable
cadence plus a stable amount. The output is suggestion dicts shaped for the
subscriptions inbox; nothing here touches Flask or SQL, so detection can run
after every import without a scheduler.

A payment counts as recurring when the merchant is seen at least three times
and the median interval between sightings lands on a known cadence. Fixed
amounts are subscriptions; variable amounts (utilities) are bills.
"""

import re
from collections import Counter, defaultdict
from datetime import date, datetime
from statistics import median
from typing import Dict, List, Optional

from services.subscriptions import KNOWN_MERCHANTS, suggested_next_due

MIN_OCCURRENCES = 3

# (frequency, ideal interval in days, allowed distance from it).
CADENCES = [
    ("weekly", 7, 2),
    ("fortnightly", 14, 3),
    ("monthly", 30, 7),
    ("quarterly", 91, 14),
    ("yearly", 365, 21),
]

# A fixed amount is a subscription; anything looser is a bill. Beyond the
# bill limit the spend is too erratic to call recurring at all.
_SUBSCRIPTION_VARIANCE = 0.15
_BILL_VARIANCE = 0.60
_UTILITY_VARIANCE = 1.50

_MONTHLY_MULTIPLIER = {
    "weekly": 52 / 12,
    "fortnightly": 26 / 12,
    "monthly": 1,
    "quarterly": 1 / 3,
    "yearly": 1 / 12,
}

# Payment-processor prefixes that carry no merchant information.
_NOISE_PREFIXES = (
    "paypal *", "paypal*", "sq *", "square *", "google *", "eftpos ",
    "visa purchase ", "visa debit ", "card purchase ", "direct debit ",
    "bpay ", "pending ", "pos ",
)

_NOISE_TOKENS = {
    "pty", "ltd", "limited", "aus", "au", "com", "www", "online", "payment",
    "purchase", "debit", "card", "eftpos", "bpay", "ref", "txn",
}

_INCOME_KEYWORDS = (
    "payroll", "salary", "wages", "interest paid", "interest credit",
    "dividend", "centrelink", "refund", "reversal", "cashback",
    "transfer from", "deposit",
)

# Moving money between your own accounts is not a bill. A generic "transfer"
# is left alone because it can be a rent or school payment.
_INTERNAL_TRANSFER_PATTERNS = (
    "transfer to savings", "transfer to offset", "transfer to credit",
    "transfer to visa", "transfer from savings", "transfer from offset",
    "internal transfer", "transfer between", "own account", "savings",
    "offset account", "credit card payment", "payment to card",
    "card payment received",
)

# Bank charges are not household bills, even when they repeat.
_FEE_KEYWORDS = (
    "transaction fee", "account fee", "monthly fee", "service fee",
    "international fee", "foreign transaction", "interest charge",
    "overdraft fee", "late fee", "card fee", "atm fee", "annual fee",
)

_CATEGORY_KEYWORDS = [
    ("utilities", ("energy", "water", "gas", "electric", "ausnet", "jemena",
                   "agl", "origin", "alinta", "red energy", "simply energy")),
    ("insurance", ("insurance", "aami", "racv", "nrma", "allianz", "gio",
                   "suncorp", "youi", "budget direct", "bingle")),
    ("health", ("health", "bupa", "medibank", "hcf", "gym", "fitness",
                "jetts", "anytime", "snap fitness")),
    ("entertainment", ("netflix", "spotify", "stan", "disney", "binge", "kayo",
                       "foxtel", "youtube", "audible", "paramount", "nrl",
                       "adobe", "microsoft", "apple")),
    ("transport", ("rego", "linkt", "eastlink", "transurban", "opal", "transport")),
    ("education", ("school", "tuition", "education", "university")),
    ("bills", ("telstra", "optus", "vodafone", "internet", "mobile", "phone",
               "council", "rates", "membership")),
]


def _as_date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def merge_similar_groups(groups: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    """Fold merchant keys that are variants of one another.

    Bank descriptions often append a suffix (``AMAZONAU`` next to ``AMAZON``),
    which would otherwise split a regular payment into two weak groups. Only
    keys of four or more characters are folded, so short codes stay distinct.
    """
    keys = sorted(groups, key=lambda key: (len(key), key))
    merged: Dict[str, List[Dict]] = {}
    used = set()
    for key in keys:
        if key in used:
            continue
        rows = list(groups[key])
        used.add(key)
        for other in keys:
            if other in used:
                continue
            shorter, longer = sorted((key, other), key=len)
            if len(shorter) >= 4 and longer.startswith(shorter):
                rows.extend(groups[other])
                used.add(other)
        merged[key] = rows
    return merged


def normalise_merchant(description: str) -> str:
    """A stable key for a bank description, so variants group together.

    Strips payment-processor prefixes, punctuation and reference numbers, then
    keeps the first two meaningful words. ``PAYPAL *SPOTIFY`` and
    ``SPOTIFY PTE LTD`` both become ``spotify``.
    """
    text = (description or "").lower().strip()
    for prefix in _NOISE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    text = re.sub(r"[^a-z0-9& ]+", " ", text)
    tokens = [
        token for token in text.split()
        if token and token not in _NOISE_TOKENS
        and not (token.isdigit() and len(token) >= 3)
    ]
    if not tokens:
        return ""
    return tokens[0]


def classify_frequency(intervals: List[float]) -> Optional[str]:
    """The cadence whose ideal interval is closest to the median interval."""
    if not intervals:
        return None
    middle = median(intervals)
    for frequency, target, tolerance in CADENCES:
        if abs(middle - target) <= tolerance:
            return frequency
    return None


def known_merchant(descriptions: List[str]) -> Optional[Dict]:
    """The first known merchant matching any description variant."""
    lowered = " ".join(descriptions).lower()
    for keyword, name, frequency, category in KNOWN_MERCHANTS:
        if re.search(rf"\b{re.escape(keyword)}\b", lowered):
            return {"name": name, "category": category,
                    "suggested_frequency": frequency}
    return None


def guess_category(description: str) -> str:
    """Best-effort category for an unrecognised merchant."""
    text = (description or "").lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return category
    return "other"


def is_ignored_description(description: str) -> bool:
    """True for income, bank fees and internal transfers, which are never bills."""
    lowered = (description or "").lower()
    if any(word in lowered for word in _INCOME_KEYWORDS):
        return True
    if any(pattern in lowered for pattern in _INTERNAL_TRANSFER_PATTERNS):
        return True
    if any(word in lowered for word in _FEE_KEYWORDS):
        return True
    return False


def _display_name(descriptions: List[str]) -> str:
    """The most common description, trimmed and title-cased for display."""
    counts = Counter(d.strip() for d in descriptions if d and d.strip())
    if not counts:
        return "Unknown merchant"
    raw = counts.most_common(1)[0][0]
    raw = re.sub(r"(?i)^(paypal\s*\*?|sq\s*\*?|square\s*\*?|google\s*\*?)\s*",
                 "", raw)
    raw = re.sub(r"(?i)\bcard\s+(?:xx)?\d+\b", "", raw)
    raw = re.sub(r"(?i)\bvalue\s+date\b.*$", "", raw)
    raw = re.sub(r"\.(com\.au|com|net|org)(\.au)?\b", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"[^a-zA-Z0-9&' ]+", " ", raw)
    noise = {"au", "aus", "aud", "usd", "card", "value", "date", "pos",
             "eftpos", "purchase", "payment", "debit", "credit"}
    tokens = [t for t in raw.split()
              if t.lower() not in noise and not (t.isdigit() and len(t) >= 3)]
    cleaned = re.sub(r"\s+", " ", " ".join(tokens)).strip()
    return (cleaned or raw.strip())[:60].title()


def split_amount_clusters(rows: List[Dict]) -> List[List[Dict]]:
    """Split one merchant's payments into distinct amount streams.

    A single merchant can charge two different bills (for example $90 for
    internet on the 1st and $25 for a phone mid-month). Amounts are sorted and
    a new stream starts when the jump is larger than 25% or $5, whichever is
    greater. A normal price rise stays in one stream.
    """
    if not rows:
        return []
    ordered = sorted(rows, key=lambda row: row["amount"])
    clusters = [[ordered[0]]]
    for row in ordered[1:]:
        previous = clusters[-1][-1]["amount"]
        gap = row["amount"] - previous
        if gap > max(5.0, previous * 0.25):
            clusters.append([row])
        else:
            clusters[-1].append(row)
    return clusters


def _suggestion_for_rows(base_key: str, stream_key: str, rows: List[Dict],
                         excluded, revive, today, multi: bool = False,
                         stream_count: int = 0) -> Optional[Dict]:
    """Build one suggestion from a merchant, or one amount stream of it."""
    excluded_key = stream_key if stream_key in excluded else (
        base_key if base_key in excluded else None)
    revived = False
    revived_since = None
    if excluded_key:
        cutoff = revive.get(stream_key) or revive.get(base_key)
        if cutoff is None:
            return None
        rows = [row for row in rows if row["date"] > cutoff]
        if len(rows) < 2:
            return None
        revived = True
        revived_since = cutoff.isoformat()

    if len(rows) < 2:
        return None
    rows.sort(key=lambda row: row["date"])
    dates = [row["date"] for row in rows]
    amounts = [row["amount"] for row in rows]

    intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    frequency = classify_frequency(intervals)
    if frequency is None:
        return None
    # A yearly bill only comes around once a year, so waiting for three
    # sightings would mean three years of history. Two is enough when the gap
    # lands cleanly on a year.
    if len(rows) < MIN_OCCURRENCES and frequency != "yearly":
        return None

    target = next(t for f, t, _ in CADENCES if f == frequency)
    tolerance = next(tol for f, _, tol in CADENCES if f == frequency)
    consistent = sum(1 for i in intervals if abs(i - target) <= tolerance)
    consistency = consistent / len(intervals)
    if consistency < 0.5:
        return None

    mean_amount = sum(amounts) / len(amounts)
    variance = max(abs(a - mean_amount) for a in amounts) / mean_amount

    known = known_merchant([row["description"] for row in rows])
    is_utility = bool(known and known["category"] == "utilities")
    limit = _UTILITY_VARIANCE if is_utility else _BILL_VARIANCE
    if variance > limit:
        return None

    last_seen = dates[-1]
    first_amount = round(amounts[0], 2)
    latest_amount = round(amounts[-1], 2)
    price_change = round(latest_amount - first_amount, 2)
    price_pct = round(abs(price_change) / first_amount * 100, 1) if first_amount else 0
    price_direction = ("flat" if abs(price_change) < 0.005
                       else ("up" if price_change > 0 else "down"))
    kind = "subscription" if variance <= _SUBSCRIPTION_VARIANCE else "bill"
    high = (len(rows) >= 4 and consistency >= 0.8
            and variance <= 0.25 and intervals[-1] <= target + tolerance)
    base_name = known["name"] if known else _display_name(
        [row["description"] for row in rows])
    category = known["category"] if known else guess_category(
        " ".join(row["description"] for row in rows))
    name = f"{base_name} ({mean_amount:.2f})" if multi else base_name

    reason = (f"{len(rows)} payments, about {int(median(intervals))} days apart, "
              f"last seen {last_seen.isoformat()}")
    if multi:
        reason += f" — one of {stream_count} amounts at this merchant"

    return {
        "merchant_key": base_key,
        "stream_key": stream_key,
        "stream_amount": round(mean_amount, 2) if multi else None,
        "name": name,
        "amount": round(mean_amount, 2),
        "frequency": frequency,
        "category": category,
        "kind": kind,
        "confidence": "high" if high else "medium",
        "source": "transactions",
        "revived": revived,
        "revived_since": revived_since,
        "occurrences": len(rows),
        "first_seen": dates[0].isoformat(),
        "last_seen": last_seen.isoformat(),
        "first_amount": first_amount,
        "latest_amount": latest_amount,
        "price_change": price_change,
        "price_pct": price_pct,
        "price_direction": price_direction,
        "next_due": suggested_next_due(frequency, last_seen.isoformat()),
        "monthly_cost": round(mean_amount * _MONTHLY_MULTIPLIER[frequency], 2),
        "reason": reason,
    }


def detect_recurring(transactions: List[Dict],
                     excluded_keys=None,
                     revive_after=None,
                     today: Optional[date] = None) -> List[Dict]:
    """Detect recurring bills and subscriptions from bank transactions.

    ``transactions`` are dicts with txn_date (or order_date), description,
    amount, optional merchant_key, direction and status. Only debits that were
    not already classified as groceries are considered. ``excluded_keys`` are
    stream keys the user has accepted or dismissed.

    ``revive_after`` maps a dismissed stream to the date it was dismissed. Only
    payments after that date are considered, so a new regular pattern can
    surface even though the old one was dismissed.

    A merchant with clearly separate amounts is split into separate streams, so
    two bills from one provider are not averaged into one fortnightly payment.
    """
    excluded = {str(k) for k in (excluded_keys or ()) if k}
    revive = {}
    for key, value in (revive_after or {}).items():
        cutoff = _as_date(value)
        if cutoff is not None:
            revive[str(key)] = cutoff
    groups: Dict[str, List[Dict]] = defaultdict(list)

    for row in transactions:
        # Credits are income; an unknown direction (some bank APIs omit the
        # sign) is still a candidate unless the description says it's income.
        if (row.get("direction") or "debit") == "credit":
            continue
        description = row.get("description") or ""
        if is_ignored_description(description):
            continue
        if (row.get("status") or "") == "grocery":
            continue
        amount = float(row.get("amount") or 0)
        seen = _as_date(row.get("txn_date") or row.get("order_date"))
        if amount <= 0 or seen is None:
            continue
        key = row.get("merchant_key") or normalise_merchant(description)
        if not key:
            continue
        groups[key].append({
            "date": seen,
            "amount": amount,
            "description": description,
        })

    groups = merge_similar_groups(groups)
    suggestions = []
    for base_key, rows in groups.items():
        clusters = split_amount_clusters(rows)
        if len(clusters) <= 1:
            suggestion = _suggestion_for_rows(
                base_key, base_key, rows, excluded, revive, today)
            if suggestion:
                suggestions.append(suggestion)
            continue

        found = []
        for index, cluster in enumerate(clusters, start=1):
            suggestion = _suggestion_for_rows(
                base_key, f"{base_key}#{index}", cluster, excluded, revive,
                today, multi=True, stream_count=len(clusters))
            if suggestion:
                found.append(suggestion)
        if found:
            suggestions.extend(found)
            continue

        # The split may have been too aggressive for a variable utility. Only
        # fall back when the amounts are close enough to be one variable bill;
        # 90/25 is clearly two bills and must not become one fortnightly one.
        means = [sum(r["amount"] for r in cluster) / len(cluster)
                 for cluster in clusters if cluster]
        if means and max(means) / min(means) < 1.6:
            suggestion = _suggestion_for_rows(
                base_key, base_key, rows, excluded, revive, today)
            if suggestion:
                suggestions.append(suggestion)

    return sorted(suggestions,
                  key=lambda s: (s["confidence"] != "high", -s["monthly_cost"]))
