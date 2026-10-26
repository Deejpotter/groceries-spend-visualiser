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
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from statistics import median
from typing import Dict, List, Optional

from services.recurring import (
    build_streams,
    classify_frequency,
    has_ended,
    merge_similar_groups,
    spend_rows,
    split_amount_clusters,
)
from services.subscriptions import (
    FREQUENCIES,
    SUBSCRIPTION_CATEGORY_LOOKUP,
    suggested_next_due,
)
from models import parse_ymd

DEFAULT_MODEL = "openrouter/free"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_CANDIDATES = 40
MAX_RETRIES = 3
BATCH_SIZE = 10
# The whole review must finish inside Gunicorn's 90s worker timeout, or the
# worker is killed and the page's review panel never updates.
REVIEW_BUDGET_SECONDS = 70
PRIORITIES = ["high", "normal", "low"]

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

Each candidate comes with evidence from a rule-based detector: "signal" is
"merchant" (same merchant each time) or "amount" (the same exact amount on about
the same day each month under different descriptions, often a direct debit whose
reference changes), "score" is 0-1 pattern strength, and "rule_frequency" is the
cadence the rules found. A low score is not a reason to reject a genuine bill;
the household may not have noticed it. A high score is not a reason to keep noise.

Reply with strict JSON only, no markdown. Shape:
{"reviews":[{"merchant_key":"...","bill":true,"name":"Clean name","category":"bills","frequency":"monthly","priority":"high","reason":"one short sentence"}]}

Rules:
- Include every candidate exactly once, in the same order.
- category must be one of: bills, entertainment, insurance, utilities, rent, health, transport, education, other.
- frequency must be one of: weekly, fortnightly, monthly, quarterly, yearly, or null when unclear.
- name must be a human-readable merchant name, at most 40 characters.
- priority must be high (essential or costly: rent, utilities, insurance, loans),
  normal (ordinary subscriptions), or low (small or optional). Use low for noise.
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
        return parse_ymd(value)
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


def _candidate_from_stream(stream: Dict) -> Dict:
    """A detected stream, shaped for the model and the review cache."""
    amounts = stream["amounts"]
    mean = stream["amount"]
    return {
        **stream,
        "merchant_key": stream["stream_key"],
        "base_key": stream["merchant_key"],
        "descriptions": [redact(d) for d in stream["descriptions"]],
        "mean_amount": mean,
        "amount_variance": (round(max(abs(a - mean) for a in amounts) / mean, 3)
                            if mean else 0),
        "median_interval_days": _median_interval(stream["dates"]),
        "rule_frequency": stream["frequency"],
    }


def _median_interval(dates: List[str]) -> Optional[float]:
    parsed = [_as_date(d) for d in dates]
    gaps = [(parsed[i + 1] - parsed[i]).days for i in range(len(parsed) - 1)]
    return round(median(gaps), 1) if gaps else None


def _loose_candidates(rows: List[Dict], covered: set, excluded: set,
                      revive: Dict) -> List[Dict]:
    """Merchants seen twice or more that formed no stream.

    Two sightings are too few for the rules, but the model can still recognise
    a quarterly rates notice or a yearly insurance renewal by name.
    """
    groups: Dict[str, List[Dict]] = {}
    revived_keys = set()
    for row in rows:
        key = row["key"]
        if key in excluded:
            cutoff = revive.get(key)
            if cutoff is None or row["date"] <= cutoff:
                continue
            revived_keys.add(key)
        groups.setdefault(key, []).append(row)

    candidates = []
    for key, group in merge_similar_groups(groups).items():
        if key in covered or len(group) < 2:
            continue
        # Separate amount streams are the rules' job; averaging them here would
        # blend two bills into one.
        if len(split_amount_clusters(group)) > 1:
            continue
        group.sort(key=lambda r: r["date"])
        dates = [r["date"] for r in group]
        amounts = [r["amount"] for r in group]
        intervals = [(dates[i + 1] - dates[i]).days
                     for i in range(len(dates) - 1)]
        mean_amount = sum(amounts) / len(amounts)
        first_amount = round(amounts[0], 2)
        latest_amount = round(amounts[-1], 2)
        price_change = round(latest_amount - first_amount, 2)
        descriptions = []
        for row in group:
            cleaned = redact(row["description"])
            if cleaned and cleaned.lower() not in {d.lower() for d in descriptions}:
                descriptions.append(cleaned)
        candidates.append({
            "merchant_key": key,
            "base_key": key,
            "stream_key": key,
            "stream_amount": None,
            "name": group[-1]["description"],
            "descriptions": descriptions[:3],
            "amounts": [round(a, 2) for a in amounts[-8:]],
            "dates": [d.isoformat() for d in dates[-8:]],
            "occurrences": len(group),
            "mean_amount": round(mean_amount, 2),
            "amount_variance": round(
                max(abs(a - mean_amount) for a in amounts) / mean_amount, 3),
            "first_amount": first_amount,
            "latest_amount": latest_amount,
            "price_change": price_change,
            "price_pct": (round(abs(price_change) / first_amount * 100, 1)
                          if first_amount else 0),
            "price_direction": ("flat" if abs(price_change) < 0.005
                                else ("up" if price_change > 0 else "down")),
            "first_seen": dates[0].isoformat(),
            "median_interval_days": round(median(intervals), 1),
            "rule_frequency": classify_frequency(intervals),
            "confidence": None,
            "score": 0.0,
            "signal": "loose",
            "revived": key in revived_keys,
            "revived_since": (revive[key].isoformat()
                              if key in revived_keys else None),
        })
    candidates.sort(key=lambda c: (-c["occurrences"], -c["mean_amount"]))
    return candidates


def build_candidates(transactions: List[Dict], excluded_keys=None,
                     revive_after=None, streams=None) -> List[Dict]:
    """Everything worth asking the model about, strongest evidence first.

    The detected streams from ``recurring.build_streams`` come first, including
    "possible" ones the rules alone would hide. Then come loose merchants seen
    twice or more that formed no stream. Each candidate's ``merchant_key`` is
    its stream key, which is what verdicts are cached and merged by.

    Pass ``streams`` when the caller already built them, to avoid doing the
    detection twice.
    """
    excluded = {str(key) for key in (excluded_keys or ()) if key}
    revive = {}
    for key, value in (revive_after or {}).items():
        cutoff = _as_date(value)
        if cutoff is not None:
            revive[str(key)] = cutoff

    if streams is None:
        streams = build_streams(transactions, excluded, revive_after)
    streams = [_candidate_from_stream(s) for s in streams]
    covered = {s["base_key"] for s in streams}
    loose = _loose_candidates(spend_rows(transactions), covered, excluded, revive)

    candidates = (streams + loose)[:MAX_CANDIDATES]
    for candidate in candidates:
        candidate["evidence_hash"] = _evidence_hash(candidate)
    return candidates


def _parse_json(content: str) -> Dict:
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start == -1:
        raise AIReviewError("The AI reply did not contain JSON.")
    # Try to find the correct end brace by working backward from the last }
    # and attempting json.loads at each step. This handles trailing explanation.
    for end in range(text.rfind("}"), start, -1):
        if text[end] != "}":
            continue
        try:
            result = json.loads(text[start:end + 1])
            if isinstance(result, dict):
                return result
        except ValueError:
            continue
    raise AIReviewError("The AI reply was not valid JSON.")


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
        priority = str(review.get("priority") or "normal").strip().lower()
        if priority not in PRIORITIES:
            priority = "normal"
        name = str(review.get("name") or "").strip()[:40]
        if not name:
            name = _clean_name(by_key[key])
        reason = re.sub(r"\s+", " ", str(review.get("reason") or "")).strip()[:160]
        cleaned.append({
            "merchant_key": key, "bill": bill, "name": name,
            "category": category, "frequency": frequency,
            "priority": priority, "reason": reason,
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
    """Ask OpenRouter to classify the candidates, in parallel batches.

    Free models truncate long replies mid-JSON, so candidates are sent
    BATCH_SIZE at a time. Verdicts from batches that succeed are kept even if
    another batch fails; AIReviewError is raised only when every batch fails.
    """
    if not candidates:
        return []
    if not api_key:
        raise AIReviewError("No OpenRouter API key is configured.")
    batches = [candidates[i:i + BATCH_SIZE]
               for i in range(0, len(candidates), BATCH_SIZE)]
    reviews, errors = [], []
    with ThreadPoolExecutor(max_workers=len(batches)) as pool:
        deadline = time.monotonic() + REVIEW_BUDGET_SECONDS
        futures = [pool.submit(_review_batch, batch, api_key, model, timeout,
                               deadline)
                   for batch in batches]
        for future in futures:
            try:
                reviews.extend(future.result())
            except AIReviewError as exc:
                errors.append(str(exc))
    if errors and not reviews:
        raise AIReviewError(errors[0])
    return reviews


def _review_batch(candidates: List[Dict], api_key: str, model: str,
                  timeout: int, deadline: Optional[float] = None) -> List[Dict]:
    """One OpenRouter request for a batch of candidates, retried until ``deadline``."""

    payload_candidates = [
        {
            "merchant_key": c["merchant_key"],
            "descriptions": c["descriptions"],
            "amounts": c["amounts"],
            "dates": c["dates"],
            "median_interval_days": c["median_interval_days"],
            "occurrences": c["occurrences"],
            "signal": c.get("signal"),
            "score": c.get("score"),
            "rule_frequency": c.get("rule_frequency"),
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
        remaining = (deadline - time.monotonic()) if deadline else timeout
        if remaining < 5:
            last_error = last_error or "The AI review ran out of time."
            break
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
            with urllib.request.urlopen(request,
                                        timeout=min(timeout, remaining)) as response:
                result = json.load(response)
            content = result["choices"][0]["message"]["content"]
            try:
                return _validate_reviews(_parse_json(content), candidates)
            except AIReviewError:
                # JSON parse failed – retry, but only if we still have attempts left
                last_error = "The AI reply did not contain valid JSON."
        except urllib.error.HTTPError as exc:
            last_error = f"OpenRouter returned HTTP {exc.code}."
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = f"Could not reach OpenRouter: {exc.reason}."
        except AIReviewError as exc:
            last_error = str(exc)
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
                frequency, priority, reason, reviewed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(merchant_key) DO UPDATE SET
                 evidence_hash = excluded.evidence_hash,
                 verdict = excluded.verdict,
                 display_name = excluded.display_name,
                 category = excluded.category,
                 frequency = excluded.frequency,
                 priority = excluded.priority,
                 reason = excluded.reason,
                 reviewed_at = CURRENT_TIMESTAMP""",
            (review["merchant_key"], review["evidence_hash"],
             "bill" if review["bill"] else "noise", review["name"],
             review["category"], review["frequency"],
             review.get("priority") or "normal", review["reason"]),
        )
        saved += 1
    db.commit()
    return saved


def _apply_review(suggestion: Dict, review: Optional[Dict]) -> Dict:
    suggestion = dict(suggestion)
    if review:
        suggestion["name"] = review.get("display_name") or suggestion.get("name")
        suggestion["category"] = (review.get("category")
                                  or suggestion.get("category") or "other")
        suggestion["ai_reason"] = review.get("reason") or ""
        suggestion["priority"] = review.get("priority") or "normal"
    return suggestion


def _rank(suggestion: Dict):
    """AI priority first, then rule confidence, then pattern strength and cost."""
    return (PRIORITIES.index(suggestion.get("priority") or "normal"),
            suggestion.get("confidence") != "high",
            -(suggestion.get("score") or 0),
            -suggestion.get("monthly_cost", 0))


def merge_suggestions(detected: List[Dict], candidates: List[Dict],
                      reviews: Dict[str, Dict]) -> List[Dict]:
    """Apply AI verdicts to the detected streams and promote what the rules missed.

    Rule amounts and schedules always win. The model decides whether to keep a
    stream, names and categorises it, and sets its priority. "Possible" streams
    and loose merchants appear only when the model calls them a bill.
    """
    by_key = {c["merchant_key"]: c for c in candidates}
    merged = []
    seen = set()
    newest = max((d for c in candidates for d in c.get("dates") or []),
                 default=None)

    for suggestion in detected:
        key = suggestion.get("stream_key") or suggestion["merchant_key"]
        review = reviews.get(key)
        seen.add(key)
        if review and review["verdict"] == "noise":
            continue
        merged.append(_apply_review(suggestion, review))

    for key, review in reviews.items():
        if key in seen or review["verdict"] != "bill" or key not in by_key:
            continue
        candidate = by_key[key]
        seen.add(key)
        if candidate.get("signal") in ("merchant", "amount"):
            # A weak stream the rules hid; its cadence and amounts are known.
            promoted = _apply_review(candidate, review)
            promoted["confidence"] = "medium"
            promoted["source"] = "ai"
            promoted["merchant_key"] = candidate["base_key"]
            promoted["reason"] = candidate["reason"]
            merged.append(promoted)
            continue

        frequency = review.get("frequency")
        if frequency not in FREQUENCIES:
            continue
        last_seen = (candidate.get("dates") or [None])[-1]
        if not last_seen:
            continue
        # The model judges the merchant, not whether it's still being paid.
        if newest and has_ended({"frequency": frequency, "last_seen": last_seen},
                                parse_ymd(newest)):
            continue
        amount = candidate["mean_amount"]
        variance = candidate.get("amount_variance") or 0
        monthly_factor = {"weekly": 52 / 12, "fortnightly": 26 / 12,
                          "monthly": 1, "quarterly": 1 / 3, "yearly": 1 / 12}[frequency]
        merged.append({
            "merchant_key": candidate.get("base_key") or key,
            "stream_key": key,
            "stream_amount": None,
            "name": review.get("display_name") or _clean_name(candidate),
            "amount": amount,
            "frequency": frequency,
            "category": review.get("category") or "other",
            "kind": "subscription" if variance <= 0.15 else "bill",
            "confidence": "medium",
            "priority": review.get("priority") or "normal",
            "score": 0.0,
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

    return sorted(merged, key=_rank)
