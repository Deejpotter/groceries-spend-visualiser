"""Suggest links between purchased product names and ingredients.

Pure string matching — no Flask, no SQL. Used by the products page to
pre-select likely ingredient matches so 100+ products don't need hand-linking.
"""

import re

# Words that carry no product identity.
STOPWORDS = {
    "a", "an", "and", "the", "of", "with", "per", "for", "each", "ea",
    "woolworths", "coles", "aldi", "iga", "homebrand", "ww", "brand",
}

_BRACKETS = re.compile(r"[()\[\]]")
_SIZE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:g|kg|ml|l|pk|pack|ea|each|s)\b", re.IGNORECASE)
_NON_WORD = re.compile(r"[^a-z0-9]+")

DEFAULT_THRESHOLD = 0.5


def tokens(name):
    """Lower-cased, size-free set of meaningful words in a product/ingredient name."""
    text = (name or "").lower()
    text = _BRACKETS.sub(" ", text)
    text = text.replace("&", " and ")
    text = _SIZE.sub(" ", text)
    text = _NON_WORD.sub(" ", text)
    return {word for word in text.split() if len(word) > 1 and word not in STOPWORDS}


def similarity(first, second):
    """Jaccard similarity of the meaningful word sets, 0.0–1.0."""
    a, b = tokens(first), tokens(second)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def suggest_ingredient(product_name, ingredients, threshold=DEFAULT_THRESHOLD):
    """Best matching ingredient for a product name, or None below the threshold.

    Each ingredient is a dict with ``id`` and ``name``; ``display_name`` is
    also considered when present because it is what the user sees on lists.
    """
    best = None
    best_score = 0.0
    for ingredient in ingredients or []:
        name = ingredient.get("name") or ""
        label = ingredient.get("display_name") or ""
        score = max(similarity(product_name, name), similarity(product_name, label))
        if score > best_score:
            best, best_score = ingredient, score
    if best is None or best_score < threshold:
        return None
    return {"ingredient_id": best.get("id"), "score": round(best_score, 2)}


def suggest_links(products, ingredients, threshold=DEFAULT_THRESHOLD):
    """{product_name: {"ingredient_id", "score"}} for products with a plausible match."""
    suggestions = {}
    for product in products or []:
        if product.get("ingredient_id"):
            continue
        match = suggest_ingredient(product.get("product_name") or "", ingredients, threshold)
        if match:
            suggestions[product["product_name"]] = match
    return suggestions
