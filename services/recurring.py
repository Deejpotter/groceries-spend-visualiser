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
    raw = re.sub(r"\.(com\.au|com|net|org)(\.au)?\b", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"[^a-zA-Z0-9&' ]+", " ", raw)
    tokens = [t for t in raw.split()
              if not (t.isdigit() and len(t) >= 3)]
    cleaned = re.sub(r"\s+", " ", " ".join(tokens)).strip()
    return (cleaned or raw.strip())[:60].title()


def detect_recurring(transactions: List[Dict],
                     excluded_keys=None,
                     today: Optional[date] = None) -> List[Dict]:
    """Detect recurring bills and subscriptions from bank transactions.

    ``transactions`` are dicts with txn_date (or order_date), description,
    amount, optional merchant_key, direction and status. Only debits that were
    not already classified as groceries are considered. ``excluded_keys`` are
    merchants the user has accepted or dismissed.
    """
    excluded = {str(k) for k in (excluded_keys or ()) if k}
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
        if not key or key in excluded:
            continue
        groups[key].append({
            "date": seen,
            "amount": amount,
            "description": description,
        })

    suggestions = []
    for key, rows in groups.items():
        # A yearly bill only comes around once a year, so waiting for three
        # sightings would mean three years of history. Two is enough when the
        # gap lands cleanly on a year.
        if len(rows) < 2:
            continue
        rows.sort(key=lambda r: r["date"])
        dates = [r["date"] for r in rows]
        amounts = [r["amount"] for r in rows]

        intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        frequency = classify_frequency(intervals)
        if frequency is None:
            continue
        if len(rows) < MIN_OCCURRENCES and frequency != "yearly":
            continue

        target = next(t for f, t, _ in CADENCES if f == frequency)
        tolerance = next(tol for f, _, tol in CADENCES if f == frequency)
        consistent = sum(1 for i in intervals if abs(i - target) <= tolerance)
        consistency = consistent / len(intervals)
        if consistency < 0.5:
            continue

        mean_amount = sum(amounts) / len(amounts)
        variance = max(abs(a - mean_amount) for a in amounts) / mean_amount

        known = known_merchant([r["description"] for r in rows])
        is_utility = bool(known and known["category"] == "utilities")
        limit = _UTILITY_VARIANCE if is_utility else _BILL_VARIANCE
        if variance > limit:
            continue

        last_seen = dates[-1]
        kind = "subscription" if variance <= _SUBSCRIPTION_VARIANCE else "bill"
        high = (len(rows) >= 4 and consistency >= 0.8
                and variance <= 0.25 and intervals[-1] <= target + tolerance)
        name = known["name"] if known else _display_name(
            [r["description"] for r in rows])
        category = known["category"] if known else guess_category(
            " ".join(r["description"] for r in rows))

        suggestions.append({
            "merchant_key": key,
            "name": name,
            "amount": round(mean_amount, 2),
            "frequency": frequency,
            "category": category,
            "kind": kind,
            "confidence": "high" if high else "medium",
            "source": "transactions",
            "occurrences": len(rows),
            "first_seen": dates[0].isoformat(),
            "last_seen": last_seen.isoformat(),
            "next_due": suggested_next_due(frequency, last_seen.isoformat()),
            "monthly_cost": round(mean_amount * _MONTHLY_MULTIPLIER[frequency], 2),
            "reason": (f"{len(rows)} payments, about {int(median(intervals))} days "
                       f"apart, last seen {last_seen.isoformat()}"),
        })

    return sorted(suggestions,
                  key=lambda s: (s["confidence"] != "high", -s["monthly_cost"]))
