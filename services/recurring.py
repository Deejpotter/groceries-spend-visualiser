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
from difflib import SequenceMatcher
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

# Payment processors that front the real merchant, with or without a "*".
_PROCESSOR_PREFIX = re.compile(
    r"^(?:paypal|sq|square|zip|afterpay|google|apple\.com/bill)\s*\*\s*"
    r"|^(?:paypal|sq)\s+", re.IGNORECASE)

# Bank boilerplate appended to card descriptions, e.g.
# "VULTR NJ USA Card xx2287 USD 5.00 Value Date: 08/06/2026".
_BOILERPLATE = [
    re.compile(r"\bvalue\s+date\b.*$", re.IGNORECASE),
    re.compile(r"\bcard\s+(?:xx|x+)?\d+\b", re.IGNORECASE),
    re.compile(r"\b(?:usd|aud|eur|gbp|nzd|sgd)\s*\d+(?:[.,]\d{2})?\b",
               re.IGNORECASE),
    re.compile(r"\b(?:au|aus|usa|us|nz|gb|gbr|sg|ie|nld|ca|nj|nsw|vic|qld)\b\s*$",
               re.IGNORECASE),
]

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


def strip_boilerplate(description: str) -> str:
    """Remove processor prefixes and bank card boilerplate from a description."""
    text = (description or "").strip()
    text = _PROCESSOR_PREFIX.sub("", text)
    for pattern in _BOILERPLATE:
        text = pattern.sub(" ", text).strip()
    # Country codes can stack ("AU AUS"), so strip trailing ones repeatedly.
    previous = None
    while previous != text:
        previous = text
        text = _BOILERPLATE[-1].sub(" ", text).strip()
    return re.sub(r"\s+", " ", text)


def _similar_keys(a: str, b: str) -> bool:
    """True when two merchant keys are spelling variants of one merchant."""
    shorter, longer = sorted((a, b), key=len)
    if len(shorter) < 4:
        return False
    if longer.startswith(shorter):
        return True
    return len(shorter) >= 5 and SequenceMatcher(None, a, b).ratio() >= 0.85


def merge_similar_groups(groups: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    """Fold merchant keys that are variants of one another.

    Bank descriptions often append a suffix (``AMAZONAU`` next to ``AMAZON``),
    which would otherwise split a regular payment into two weak groups, and
    sometimes misspell a merchant (``TELSTRA`` next to ``TELSTR``). Only keys
    of four or more characters are folded, so short codes stay distinct.
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
            if _similar_keys(key, other):
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
    text = strip_boilerplate(description).lower()
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
        if any(re.search(rf"\b{re.escape(keyword)}\b", text)
               for keyword in keywords):
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
    raw = strip_boilerplate(counts.most_common(1)[0][0])
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


# Score bands. "possible" streams are only shown when the AI review keeps them.
HIGH_SCORE = 0.75
MEDIUM_SCORE = 0.5
POSSIBLE_SCORE = 0.35

# How much each piece of evidence contributes to the score.
_WEIGHTS = {"timing": 0.4, "stability": 0.25, "cycles": 0.2, "day": 0.1,
            "known": 0.05}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _day_spread(dates: List[date]) -> int:
    """Days between the earliest and latest day-of-month, wrapping month ends.

    The 30th and the 1st are two days apart, not 29, so the spread is the
    circle minus the widest gap between neighbouring days.
    """
    days = sorted(d.day for d in dates)
    if len(days) < 2:
        return 0
    gaps = [days[i + 1] - days[i] for i in range(len(days) - 1)]
    gaps.append(days[0] + 31 - days[-1])
    return 31 - max(gaps)


def score_stream(rows: List[Dict], utility: bool = False) -> Optional[Dict]:
    """Score how much a run of payments looks like one recurring bill.

    Returns the cadence, the 0-1 score and the evidence behind it, or None when
    there is no cadence at all. Each interval is compared with the nearest whole
    number of cycles, so a skipped month counts as on time with a small
    penalty, while a 46-day gap in a monthly pattern is simply late.
    """
    if len(rows) < 2:
        return None
    dates = [row["date"] for row in rows]
    amounts = [row["amount"] for row in rows]
    intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    frequency = classify_frequency(intervals)
    if frequency is None:
        return None
    target = next(t for f, t, _ in CADENCES if f == frequency)

    errors = []
    skipped = 0
    for interval in intervals:
        cycles = max(1, round(interval / target))
        skipped += cycles - 1
        errors.append(abs(interval - cycles * target) / target)
    timing_error = sum(errors) / len(errors) + 0.1 * skipped / len(intervals)

    mean_amount = sum(amounts) / len(amounts)
    spread = (sum((a - mean_amount) ** 2 for a in amounts) / len(amounts)) ** 0.5
    amount_cv = spread / mean_amount if mean_amount else 1.0

    day_spread = (_day_spread(dates)
                  if frequency in ("monthly", "quarterly", "yearly") else None)
    known = known_merchant([row["description"] for row in rows])

    timing = _clamp(1 - timing_error / 0.25)
    parts = {
        "timing": timing,
        "stability": _clamp(1 - amount_cv / (0.6 if utility else 0.3)),
        "cycles": _clamp((len(rows) - 1) / 5),
        "day": (_clamp(1 - (day_spread - 2) / 8) if day_spread is not None
                else timing),
        "known": 1.0 if known else 0.0,
    }
    score = round(sum(_WEIGHTS[name] * value for name, value in parts.items()), 3)
    return {
        "frequency": frequency,
        "score": score,
        "timing_error": round(timing_error, 3),
        "amount_cv": round(amount_cv, 3),
        "day_spread": day_spread,
        "skipped": skipped,
        "parts": {name: round(value, 2) for name, value in parts.items()},
    }


def confidence_for(score: float) -> Optional[str]:
    """The confidence band for a stream score, or None below "possible"."""
    if score >= HIGH_SCORE:
        return "high"
    if score >= MEDIUM_SCORE:
        return "medium"
    if score >= POSSIBLE_SCORE:
        return "possible"
    return None


def spend_rows(transactions: List[Dict]) -> List[Dict]:
    """Debits that could be bills, as ``{date, amount, description, key}``.

    Drops credits, income, fees, internal transfers and grocery rows. The key
    is recomputed from the description, because keys stored at import time may
    come from an older, coarser normaliser (every PayPal payment as "paypal").
    """
    rows = []
    for row in transactions:
        # Credits are income; an unknown direction (some bank APIs omit the
        # sign) is still a candidate unless the description says it's income.
        if (row.get("direction") or "debit") == "credit":
            continue
        if (row.get("status") or "") == "grocery":
            continue
        description = row.get("description") or ""
        if is_ignored_description(description):
            continue
        try:
            amount = float(row.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        seen = _as_date(row.get("txn_date") or row.get("order_date"))
        if amount <= 0 or seen is None:
            continue
        key = normalise_merchant(description) or row.get("merchant_key")
        if not key:
            continue
        rows.append({"date": seen, "amount": amount,
                     "description": description, "key": key})
    return rows


def _suggestion_for_rows(base_key: str, stream_key: str, rows: List[Dict],
                         excluded, revive, multi: bool = False,
                         stream_count: int = 0,
                         signal: str = "merchant") -> Optional[Dict]:
    """Score one stream and shape it as a suggestion, or None if it isn't one."""
    excluded_key = stream_key if stream_key in excluded else (
        base_key if base_key in excluded else None)
    revived = False
    revived_since = None
    if excluded_key:
        cutoff = revive.get(stream_key) or revive.get(base_key)
        if cutoff is None:
            return None
        rows = [row for row in rows if row["date"] > cutoff]
        revived = True
        revived_since = cutoff.isoformat()

    rows = sorted(rows, key=lambda row: row["date"])
    known = known_merchant([row["description"] for row in rows])
    is_utility = bool(known and known["category"] == "utilities")
    scored = score_stream(rows, utility=is_utility)
    if scored is None:
        return None
    frequency = scored["frequency"]
    # A yearly bill only comes around once a year, so waiting for three
    # sightings would mean three years of history. Two is enough when the gap
    # lands cleanly on a year.
    if len(rows) < MIN_OCCURRENCES and frequency != "yearly":
        return None

    dates = [row["date"] for row in rows]
    amounts = [row["amount"] for row in rows]
    intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    mean_amount = sum(amounts) / len(amounts)
    variance = max(abs(a - mean_amount) for a in amounts) / mean_amount
    if variance > (_UTILITY_VARIANCE if is_utility else _BILL_VARIANCE):
        return None
    confidence = confidence_for(scored["score"])
    if confidence is None:
        return None
    # Two or three clean sightings can be coincidence; high needs a track record.
    if confidence == "high" and len(rows) < 4:
        confidence = "medium"

    last_seen = dates[-1]
    first_amount = round(amounts[0], 2)
    latest_amount = round(amounts[-1], 2)
    price_change = round(latest_amount - first_amount, 2)
    price_pct = round(abs(price_change) / first_amount * 100, 1) if first_amount else 0
    price_direction = ("flat" if abs(price_change) < 0.005
                       else ("up" if price_change > 0 else "down"))
    descriptions = [row["description"] for row in rows]
    base_name = known["name"] if known else _display_name(descriptions)
    category = known["category"] if known else guess_category(" ".join(descriptions))
    name = f"{base_name} ({mean_amount:.2f})" if multi else base_name

    reason = (f"{len(rows)} payments, about {int(median(intervals))} days apart, "
              f"last seen {last_seen.isoformat()}")
    if scored["skipped"]:
        reason += f", {scored['skipped']} missed"
    if multi:
        reason += f" — one of {stream_count} amounts at this merchant"
    if signal == "amount":
        keys = len({row["key"] for row in rows})
        reason += (f" — same ${mean_amount:.2f} around day {last_seen.day} "
                   f"under {keys} different descriptions")

    return {
        "merchant_key": base_key,
        "stream_key": stream_key,
        "stream_amount": round(mean_amount, 2) if multi else None,
        "name": name,
        "amount": round(mean_amount, 2),
        "frequency": frequency,
        "category": category,
        "kind": "subscription" if variance <= _SUBSCRIPTION_VARIANCE else "bill",
        "confidence": confidence,
        "score": scored["score"],
        "evidence": scored,
        "signal": signal,
        "source": "transactions",
        "revived": revived,
        "revived_since": revived_since,
        "occurrences": len(rows),
        "descriptions": list(dict.fromkeys(descriptions))[-3:],
        "amounts": [round(a, 2) for a in amounts[-8:]],
        "dates": [d.isoformat() for d in dates[-8:]],
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


def _merchant_streams(groups, excluded, revive) -> List[Dict]:
    """Signal 1: payments to the same (fuzzy-matched) merchant."""
    found = []
    for base_key, rows in groups.items():
        clusters = split_amount_clusters(rows)
        if len(clusters) <= 1:
            suggestion = _suggestion_for_rows(base_key, base_key, rows,
                                              excluded, revive)
            if suggestion:
                found.append(suggestion)
            continue

        streams = []
        for index, cluster in enumerate(clusters, start=1):
            suggestion = _suggestion_for_rows(
                base_key, f"{base_key}#{index}", cluster, excluded, revive,
                multi=True, stream_count=len(clusters))
            if suggestion:
                streams.append(suggestion)
        if streams:
            found.extend(streams)
            continue

        # The split may have been too aggressive for a variable utility. Only
        # fall back when the amounts are close enough to be one variable bill;
        # 90/25 is clearly two bills and must not become one fortnightly one.
        means = [sum(r["amount"] for r in cluster) / len(cluster)
                 for cluster in clusters if cluster]
        if means and max(means) / min(means) < 1.6:
            suggestion = _suggestion_for_rows(base_key, base_key, rows,
                                              excluded, revive)
            if suggestion:
                found.append(suggestion)
    return found


def _amount_streams(rows: List[Dict], merchant_found: List[Dict],
                    excluded, revive) -> List[Dict]:
    """Signal 2: the same exact amount on about the same day each month.

    Banks often describe one direct debit differently from month to month
    (new reference numbers, a processor prefix that comes and goes), which
    splits it across merchant groups. The amount and the day still line up.
    Only amounts seen under two or more merchant keys count; a single merchant
    is already judged by signal 1.
    """
    strong = {s["merchant_key"] for s in merchant_found
              if s["confidence"] in ("high", "medium")}
    by_amount: Dict[float, List[Dict]] = defaultdict(list)
    for row in rows:
        by_amount[round(row["amount"], 2)].append(row)

    found = []
    for amount, group in by_amount.items():
        if amount < 5 or len(group) < MIN_OCCURRENCES:
            continue
        if len({row["key"] for row in group}) < 2:
            continue
        # Mostly an already-detected merchant: nothing new to report.
        if sum(1 for row in group if row["key"] in strong) * 2 > len(group):
            continue
        group = sorted(group, key=lambda row: row["date"])
        months = {(row["date"].year, row["date"].month) for row in group}
        if len(months) != len(group):
            continue
        if _day_spread([row["date"] for row in group]) > 3:
            continue
        key = f"amount:{amount:.2f}"
        suggestion = _suggestion_for_rows(key, key, group, excluded, revive,
                                          signal="amount")
        if suggestion and suggestion["frequency"] == "monthly":
            found.append(suggestion)
    return found


def build_streams(transactions: List[Dict], excluded_keys=None,
                  revive_after=None) -> List[Dict]:
    """Every recurring stream worth showing or reviewing, best first.

    Includes "possible" streams, which the page shows only when the AI review
    keeps them.
    """
    excluded = {str(k) for k in (excluded_keys or ()) if k}
    revive = {}
    for key, value in (revive_after or {}).items():
        cutoff = _as_date(value)
        if cutoff is not None:
            revive[str(key)] = cutoff

    rows = spend_rows(transactions)
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for row in rows:
        groups[row["key"]].append(row)
    groups = merge_similar_groups(groups)
    # Fuzzy merging folds spellings together; point rows at the merged key so
    # the amount signal counts merchants, not spellings.
    for base_key, group in groups.items():
        for row in group:
            row["key"] = base_key

    found = _merchant_streams(groups, excluded, revive)
    found.extend(_amount_streams(rows, found, excluded, revive))
    return sorted(found, key=lambda s: (-s["score"], -s["monthly_cost"]))


def detect_recurring(transactions: List[Dict],
                     excluded_keys=None,
                     revive_after=None,
                     today: Optional[date] = None,
                     include_possible: bool = False) -> List[Dict]:
    """Detect recurring bills and subscriptions from bank transactions.

    ``transactions`` are dicts with txn_date (or order_date), description,
    amount, optional merchant_key, direction and status. ``excluded_keys`` are
    stream keys the user has accepted or dismissed. ``revive_after`` maps a
    dismissed stream to the date it was dismissed; only later payments count,
    so a new regular pattern can surface again.

    Returns high and medium confidence streams, plus "possible" ones when
    ``include_possible`` is set.
    """
    streams = build_streams(transactions, excluded_keys, revive_after)
    if not include_possible:
        streams = [s for s in streams if s["confidence"] != "possible"]
    return sorted(streams,
                  key=lambda s: (s["confidence"] != "high", -s["monthly_cost"]))
