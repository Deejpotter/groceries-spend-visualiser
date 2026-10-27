"""Optional AI pass over purchased products the matching rules couldn't place.

The rules in ``product_matching`` link most products. This asks a model, in
small batches, which existing ingredient (if any) a leftover product is and
what role it has (food, household, ...). It never creates ingredients or
changes links itself: verdicts are cached and shown as suggestions the user
saves. Nothing runs without an OpenRouter key.
"""

import hashlib
import json
import time
from typing import Dict, List

from services.ai_review import BATCH_SIZE, REVIEW_BUDGET_SECONDS, request_chat_json
from services.product_matching import (
    MEDIUM_CONFIDENCE, NON_FOOD_ROLES, ROLE_LOOKUP, classify_product, suggest_ingredient,
)

MAX_PRODUCTS = 40
MAX_INGREDIENTS = 300

_SYSTEM_PROMPT = """You sort Australian supermarket products for a household meal-planning app.

For each product decide:
- "ingredient_id": the id of the ONE listed ingredient that this product is, or null
  when none of the listed ingredients is a good fit. A brand, size or variety
  difference is fine ("Woolworths Full Cream Milk 2L" is ingredient "Milk").
  Never pick an ingredient that is only related ("Cream" is not "Milk").
- "role": one of __ROLES__.
- "reason": a few words.

Answer with JSON only: {"products": [{"product_name": ..., "ingredient_id": ..., "role": ..., "reason": ...}]}
"""
_SYSTEM_PROMPT = _SYSTEM_PROMPT.replace("__ROLES__", ", ".join(ROLE_LOOKUP))


def _hash(name: str, ingredients: List[Dict]) -> str:
    ids = ",".join(str(i["id"]) for i in sorted(ingredients, key=lambda i: i["id"]))
    return hashlib.sha1(f"1|{name}|{ids}".encode()).hexdigest()


def product_candidates(db, rows: List[Dict], ingredients: List[Dict], decisions: Dict[str, Dict]) -> List[Dict]:
    """Unlinked, non-ignored products with no confident rule match and no current review."""
    cached = {r["product_name"]: r["evidence_hash"] for r in db.execute(
        "SELECT product_name, evidence_hash FROM product_ai_reviews")}
    found = []
    for row in rows:
        name = row["product_name"]
        decision = decisions.get(name) or {}
        if row.get("ingredient_id") or decision.get("ignored"):
            continue
        if (decision.get("role") or classify_product(name)) in NON_FOOD_ROLES:
            continue
        match = suggest_ingredient(name, ingredients)
        if match and match["score"] >= MEDIUM_CONFIDENCE:
            continue
        evidence = _hash(name, ingredients)
        if cached.get(name) == evidence:
            continue
        found.append({"product_name": name, "evidence_hash": evidence})
        if len(found) >= MAX_PRODUCTS:
            break
    return found


def _validate(payload: Dict, batch: List[Dict], ingredient_ids: set) -> List[Dict]:
    names = {c["product_name"] for c in batch}
    reviews = []
    for item in payload.get("products") or []:
        if not isinstance(item, dict) or item.get("product_name") not in names:
            continue
        ingredient_id = item.get("ingredient_id")
        try:
            ingredient_id = int(ingredient_id) if ingredient_id is not None else None
        except (TypeError, ValueError):
            ingredient_id = None
        if ingredient_id not in ingredient_ids:
            ingredient_id = None
        role = item.get("role") if item.get("role") in ROLE_LOOKUP else None
        reviews.append({"product_name": item["product_name"], "ingredient_id": ingredient_id,
                        "role": role, "reason": str(item.get("reason") or "")[:200]})
    return reviews


def review_products(candidates: List[Dict], ingredients: List[Dict], api_key: str, model: str) -> List[Dict]:
    """Ask the model about candidates in batches; batches that fail are skipped once one succeeds."""
    menu = [{"id": i["id"], "name": i.get("display_name") or i["name"]} for i in ingredients][:MAX_INGREDIENTS]
    ids = {i["id"] for i in menu}
    deadline = time.monotonic() + REVIEW_BUDGET_SECONDS
    reviews, errors = [], []
    for start in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[start:start + BATCH_SIZE]
        prompt = ("Ingredients:\n" + json.dumps(menu, ensure_ascii=False)
                  + "\nProducts:\n" + json.dumps([c["product_name"] for c in batch], ensure_ascii=False))
        try:
            reviews += _validate(request_chat_json(_SYSTEM_PROMPT, prompt, api_key, model, deadline=deadline),
                                 batch, ids)
        except Exception as exc:  # one bad batch shouldn't lose the others
            errors.append(exc)
    if not reviews and errors:
        raise errors[0]
    return reviews


def save_product_reviews(db, candidates: List[Dict], reviews: List[Dict]) -> int:
    hashes = {c["product_name"]: c["evidence_hash"] for c in candidates}
    for review in reviews:
        db.execute(
            "INSERT OR REPLACE INTO product_ai_reviews (product_name, evidence_hash, ingredient_id, role, reason)"
            " VALUES (?, ?, ?, ?, ?)",
            (review["product_name"], hashes[review["product_name"]], review["ingredient_id"],
             review["role"], review["reason"]),
        )
    db.commit()
    return len(reviews)


def cached_product_reviews(db, rows: List[Dict]) -> Dict[str, Dict]:
    names = [r["product_name"] for r in rows]
    if not names:
        return {}
    marks = ",".join("?" * len(names))
    return {r["product_name"]: dict(r) for r in db.execute(
        f"SELECT * FROM product_ai_reviews WHERE product_name IN ({marks})", names)}
