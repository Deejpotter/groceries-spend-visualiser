"""Optional AI review of candidate merchants via OpenRouter.

The rules in ``recurring.py`` decide cadence and amounts. This module only asks
a model to judge whether a merchant is a genuine household bill or noise, and
to give it a clean name and category. Amounts and dates always come from the
bank data, never from the model.

Nothing here runs unless the user has configured an OpenRouter key. The page
reads cached verdicts; a review is only requested when the user asks for it.
"""

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from statistics import median
from typing import Dict, List, Optional

from services.recurring import (
    classify_frequency,
    is_ignored_description,
    merge_similar_groups,
    normalise_merchant,
    split_amount_clusters,
)
from services.subscriptions import (
    FREQUENCIES,
    SUBSCRIPTION_CATEGORY_LOOKUP,
    suggested_next_due,
)

DEFAULT_MODEL = "openrouter/free"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_CANDIDATES = 30
MAX_RETRIES = 2

_SYSTEM_PROMPT = """You review bank merchants for an Australian household budgeting app.

Decide whether each merchant is a genuine recurring household bill or subscription,
or whether it is noise. Genuine examples: rent, mortgage, electricity, gas, water,
council rates, home or car insurance, health insurance, phone/internet, streaming,
gym, school fees, loan repayments, childcare.

Noise examples that must be rejected:
- transfers between the person's own accounts, including savings, offset and credit card payments
- bank fees, transaction fees, interest charges and card fees
- salary, wages, interest paid, dividends, refunds and cashback
- one-off purchases, supermarket or marketplace shopping, Amazon, Kmart, Bunnings
- API, cloud or software usage billed by consumption (for example DeepInfra, OpenAI)
- gambling, donations and government one-off payments

A merchant can still be a bill if its amount varies, such as electricity. Judge
the merchant, not the amount.

Reply with strict JSON only, no markdown. Shape:
{"reviews":[{"merchant_key":"...","bill":true,"name":"Clean name","category":"bills","frequency":"monthly","reason":"one short sentence"}]}

Rules:
- Include every candidate exactly once, in the same order.
- category must be one of: bills, entertainment, insurance, utilities, rent, health, transport, education, other.
- frequency must be one of: weekly, fortnightly, monthly, quarterly, yearly, or null when unclear.
- name must be a human-readable merchant name, at most 40 characters.
- reason must be one short sentence, at most 20 words.
"""


class AIReviewError(Exception):
    """The AI review could not be completed."""


def resolve_config(get_setting) -> Dict:
    """OpenRouter settings, preferring the settings page over the environment."""
    stored_key = (get_setting("openrouter_api_key") or "").strip()
    stored_model = (get_setting("openrouter_model") or "").strip()
    env_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    env_model = (os.getenv("OPENROUTER_MODEL") or "").strip()
    key = stored_key or env_key
    return {
        "key": key,
        "model": stored_model or env_model or DEFAULT_MODEL,
        "configured": bool(key),
        "from_env": bool(env_key) and not bool(stored_key),
    }


def redact(text: str) -> str:
    """Remove card and account numbers before anything leaves the machine."""
    value = str(text or "")
    value = re.sub(r"\bxx\d+\b", "[card]", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:\d[ -]?){4,}\b", "[number]", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:120]


def _as_date(value):
    try:
        from datetime import datetime
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _evidence_hash(candidate: Dict) -> str:
    parts = [candidate["merchant_key"], str(candidate["occurrences"]),
             str(candidate.get("mean_amount")), str(candidate.get("median_interval_days"))]
    for date, amount in zip(candidate.get("dates", []), candidate.get("amounts", [])):
        parts.append(f"{date}:{amount}")
    for text in candidate.get("descriptions", []):
        parts.append(text.lower())
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def build_candidates(transactions: List[Dict], excluded_keys=None,
                     revive_after=None) -> List[Dict]:
    """Group spend transactions into merchants worth asking the model about.

    Ignores income, internal transfers, fees and grocery rows up front. Any
    merchant seen twice or more is a candidate; the model decides whether it is
    a bill. Returns at most MAX_CANDIDATES, busiest first.

    Dismissed merchants are limited to payments after their dismissal date, so
    a genuinely new pattern can be reviewed again.
    """
    excluded = {str(key) for key in (excluded_keys or ()) if key}
    revive = {}
    for key, value in (revive_after or {}).items():
        cutoff = _as_date(value)
        if cutoff is not None:
            revive[str(key)] = cutoff
    revived_keys = set()
    groups: Dict[str, List[Dict]] = {}
    for row in transactions:
        if (row.get("direction") or "debit") == "credit":
            continue
        if (row.get("status") or "") == "grocery":
            continue
        description = row.get("description") or ""
        if is_ignored_description(description):
            continue
        seen = _as_date(row.get("txn_date") or row.get("order_date"))
        try:
            amount = float(row.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if seen is None or amount <= 0:
            continue
        key = row.get("merchant_key") or normalise_merchant(description)
        if not key:
            continue
        if key in excluded:
            cutoff = revive.get(key)
            if cutoff is None or seen <= cutoff:
                continue
            revived_keys.add(key)
        groups.setdefault(key, []).append({
            "date": seen, "amount": amount, "description": description,
        })

    candidates = []
    groups = merge_similar_groups(groups)
    for key, rows in groups.items():
        # A merchant with clearly separate amounts is handled by the rule
        # detector as multiple streams. Sending the combined merchant to the
        # model would average them back into one bill.
        if len(split_amount_clusters(rows)) > 1:
            continue
        if len(rows) < 2:
            continue
        rows.sort(key=lambda r: r["date"])
        dates = [r["date"] for r in rows]
        amounts = [r["amount"] for r in rows]
        intervals = [(dates[i + 1] - dates[i]).days
                     for i in range(len(dates) - 1)]
        mean_amount = sum(amounts) / len(amounts)
        variance = (max(abs(a - mean_amount) for a in amounts) / mean_amount
                    if mean_amount else 0)
        first_amount = round(amounts[0], 2)
        latest_amount = round(amounts[-1], 2)
        price_change = round(latest_amount - first_amount, 2)
        price_pct = (round(abs(price_change) / first_amount * 100, 1)
                     if first_amount else 0)
        price_direction = ("flat" if abs(price_change) < 0.005
                           else ("up" if price_change > 0 else "down"))
        unique_descriptions = []
        for row in rows:
            cleaned = redact(row["description"])
            if cleaned and cleaned.lower() not in {d.lower() for d in unique_descriptions}:
                unique_descriptions.append(cleaned)
        candidates.append({
            "merchant_key": key,
            "stream_key": key,
            "stream_amount": None,
            "name": rows[-1]["description"],
            "descriptions": unique_descriptions[:3],
            "amounts": [round(a, 2) for a in amounts[-8:]],
            "dates": [d.isoformat() for d in dates[-8:]],
            "occurrences": len(rows),
            "mean_amount": round(mean_amount, 2),
            "amount_variance": round(variance, 3),
            "first_amount": first_amount,
            "latest_amount": latest_amount,
            "price_change": price_change,
            "price_pct": price_pct,
            "price_direction": price_direction,
            "first_seen": dates[0].isoformat(),
            "median_interval_days": round(median(intervals), 1) if intervals else None,
            "rule_frequency": classify_frequency(intervals),
            "revived": key in revived_keys,
            "revived_since": (revive[key].isoformat()
                              if key in revived_keys else None),
        })

    candidates.sort(key=lambda c: (-c["occurrences"], -c["mean_amount"]))
    candidates = candidates[:MAX_CANDIDATES]
    for candidate in candidates:
        candidate["evidence_hash"] = _evidence_hash(candidate)
    return candidates


def _parse_json(content: str) -> Dict:
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise AIReviewError("The AI reply did not contain JSON.")
    try:
        return json.loads(text[start:end + 1])
    except ValueError as exc:
        raise AIReviewError("The AI reply was not valid JSON.") from exc


def _validate_reviews(payload: Dict, candidates: List[Dict]) -> List[Dict]:
    allowed_keys = {c["merchant_key"] for c in candidates}
    by_key = {c["merchant_key"]: c for c in candidates}
    reviews = payload.get("reviews")
    if not isinstance(reviews, list):
        raise AIReviewError("The AI reply had no reviews list.")

    cleaned = []
    seen = set()
    for review in reviews:
        if not isinstance(review, dict):
            continue
        key = str(review.get("merchant_key") or "").strip()
        if key not in allowed_keys or key in seen:
            continue
        bill = review.get("bill")
        if not isinstance(bill, bool):
            continue
        seen.add(key)
        category = str(review.get("category") or "other").strip().lower()
        if category not in SUBSCRIPTION_CATEGORY_LOOKUP:
            category = "other"
        frequency = review.get("frequency")
        if frequency not in FREQUENCIES:
            frequency = None
        name = str(review.get("name") or "").strip()[:40]
        if not name:
            name = _clean_name(by_key[key])
        reason = re.sub(r"\s+", " ", str(review.get("reason") or "")).strip()[:160]
        cleaned.append({
            "merchant_key": key, "bill": bill, "name": name,
            "category": category, "frequency": frequency, "reason": reason,
            "evidence_hash": by_key[key]["evidence_hash"],
        })
    return cleaned


def _clean_name(candidate: Dict) -> str:
    raw = candidate.get("descriptions", [candidate.get("name", "")])[0]
    raw = re.sub(r"\.(com\.au|com|net|org)(\.au)?\b", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"[^a-zA-Z0-9&' ]+", " ", raw)
    tokens = [t for t in raw.split() if not (t.isdigit() and len(t) >= 3)]
    return re.sub(r"\s+", " ", " ".join(tokens)).strip().title()[:40] or "Unknown merchant"


def review_candidates(candidates: List[Dict], api_key: str,
                      model: str = DEFAULT_MODEL,
                      timeout: int = 60) -> List[Dict]:
    """Ask OpenRouter to classify the candidates. Raises AIReviewError on failure."""
    if not candidates:
        return []
    if not api_key:
        raise AIReviewError("No OpenRouter API key is configured.")

    payload_candidates = [
        {
            "merchant_key": c["merchant_key"],
            "descriptions": c["descriptions"],
            "amounts": c["amounts"],
            "dates": c["dates"],
            "median_interval_days": c["median_interval_days"],
            "occurrences": c["occurrences"],
        }
        for c in candidates
    ]
    user_prompt = (
        "Review these merchants. Use only the supplied data.\n"
        "Candidates:\n" + json.dumps(payload_candidates, ensure_ascii=False)
    )
    payload = {
        "model": model,
        "temperature": 0.1,
        "max_tokens": 2500,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    last_error = None
    for attempt in range(MAX_RETRIES):
        request = urllib.request.Request(
            API_URL, data=body,
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "HTTP-Referer": "https://groceries.deejpotter.com",
                "X-Title": "Groceries Visualiser",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.load(response)
            content = result["choices"][0]["message"]["content"]
            return _validate_reviews(_parse_json(content), candidates)
        except urllib.error.HTTPError as exc:
            last_error = f"OpenRouter returned HTTP {exc.code}."
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = f"Could not reach OpenRouter: {exc.reason}."
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            last_error = "OpenRouter returned an unexpected response."
        if attempt + 1 < MAX_RETRIES:
            continue
    raise AIReviewError(last_error or "The AI review failed.")


def cached_reviews(db, candidates: List[Dict]) -> Dict[str, Dict]:
    """Verdicts that still match the current evidence, keyed by merchant."""
    if not candidates:
        return {}
    rows = db.execute("SELECT * FROM ai_reviews").fetchall()
    stored = {row["merchant_key"]: dict(row) for row in rows}
    valid = {}
    for candidate in candidates:
        row = stored.get(candidate["merchant_key"])
        if row and row["evidence_hash"] == candidate["evidence_hash"]:
            valid[candidate["merchant_key"]] = row
    return valid


def save_reviews(db, reviews: List[Dict]) -> int:
    """Store verdicts, replacing older evidence for the same merchant."""
    saved = 0
    for review in reviews:
        db.execute(
            """INSERT INTO ai_reviews
               (merchant_key, evidence_hash, verdict, display_name, category,
                frequency, reason, reviewed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(merchant_key) DO UPDATE SET
                 evidence_hash = excluded.evidence_hash,
                 verdict = excluded.verdict,
                 display_name = excluded.display_name,
                 category = excluded.category,
                 frequency = excluded.frequency,
                 reason = excluded.reason,
                 reviewed_at = CURRENT_TIMESTAMP""",
            (review["merchant_key"], review["evidence_hash"],
             "bill" if review["bill"] else "noise", review["name"],
             review["category"], review["frequency"], review["reason"]),
        )
        saved += 1
    db.commit()
    return saved


def merge_suggestions(detected: List[Dict], candidates: List[Dict],
                      reviews: Dict[str, Dict]) -> List[Dict]:
    """Apply AI verdicts to rule-detected suggestions and promote missed bills.

    Rule-detected amounts and schedules always win. The model only decides
    whether to keep a merchant, and supplies its name, category and reason.
    """
    by_key = {(c.get("stream_key") or c["merchant_key"]): c
              for c in candidates}
    merged = []
    seen = set()

    for suggestion in detected:
        key = suggestion.get("stream_key") or suggestion["merchant_key"]
        review = reviews.get(key)
        if review and review["verdict"] == "noise":
            continue
        if review:
            suggestion = dict(suggestion)
            suggestion["name"] = review.get("display_name") or suggestion["name"]
            suggestion["category"] = review.get("category") or suggestion["category"]
            suggestion["ai_reason"] = review.get("reason") or ""
        merged.append(suggestion)
        seen.add(key)

    for key, review in reviews.items():
        if key in seen or review["verdict"] != "bill" or key not in by_key:
            continue
        candidate = by_key[key]
        frequency = review.get("frequency")
        if frequency not in FREQUENCIES:
            continue
        last_seen = (candidate.get("dates") or [None])[-1]
        if not last_seen:
            continue
        amount = candidate["mean_amount"]
        variance = candidate.get("amount_variance") or 0
        monthly_factor = {"weekly": 52 / 12, "fortnightly": 26 / 12,
                          "monthly": 1, "quarterly": 1 / 3, "yearly": 1 / 12}[frequency]
        merged.append({
            "merchant_key": candidate.get("merchant_key") or key,
            "stream_key": key,
            "stream_amount": None,
            "name": review.get("display_name") or _clean_name(candidate),
            "amount": amount,
            "frequency": frequency,
            "category": review.get("category") or "other",
            "kind": "subscription" if variance <= 0.15 else "bill",
            "confidence": "medium",
            "source": "ai",
            "occurrences": candidate["occurrences"],
            "last_seen": last_seen,
            "first_seen": candidate.get("first_seen"),
            "first_amount": candidate.get("first_amount"),
            "latest_amount": candidate.get("latest_amount"),
            "price_change": candidate.get("price_change"),
            "price_pct": candidate.get("price_pct"),
            "price_direction": candidate.get("price_direction"),
            "revived": candidate.get("revived", False),
            "revived_since": candidate.get("revived_since"),
            "next_due": suggested_next_due(frequency, last_seen),
            "monthly_cost": round(amount * monthly_factor, 2),
            "reason": review.get("reason") or "AI review",
            "ai_reason": review.get("reason") or "",
        })
        seen.add(key)

    return sorted(merged,
                  key=lambda s: (s["confidence"] != "high", -s["monthly_cost"]))
