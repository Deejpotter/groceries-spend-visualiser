"""Suggest links between purchased product names and ingredients, and sort products by role.

Pure string matching — no Flask, no SQL. Used by the products page to
pre-select likely ingredient matches so 100+ products don't need hand-linking,
and to keep non-food items (cleaning, pet, toiletries) out of meal and staple
suggestions.
"""

import re

# Words that carry no product identity.
STOPWORDS = {
    "a", "an", "and", "the", "of", "with", "per", "for", "each", "ea", "in",
    "woolworths", "coles", "aldi", "iga", "homebrand", "ww", "brand", "macro",
    "essentials", "select", "value", "gold", "own",
}

# Descriptive words that say how a product is sold, not what it is.
FILLER = {
    "fresh", "free", "range", "loose", "punnet", "bag", "bags", "prepacked", "pre", "packed",
    "australian", "aussie", "approx", "min", "pack", "pk", "tray", "bulk", "large", "small",
    "medium", "family", "fillet", "fillets", "whole", "premium", "classic", "original",
    "natural", "style", "multipack", "bunch", "bottle", "can", "jar", "tub", "box",
}

_BRACKETS = re.compile(r"[()\[\]]")
_SIZE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:g|kg|ml|l|pk|pack|ea|each|s|x)?\b", re.IGNORECASE)
_NON_WORD = re.compile(r"[^a-z0-9]+")

DEFAULT_THRESHOLD = 0.5
HIGH_CONFIDENCE = 0.85
MEDIUM_CONFIDENCE = 0.65


def singular(word):
    """Fold a simple English plural: onions -> onion, tomatoes -> tomato, berries -> berry."""
    if len(word) <= 3 or word.endswith("ss") or word.endswith("us"):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("oes") or re.search(r"(?:ch|sh|x)es$", word):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def _words(name):
    """Meaningful words in name order, sizes and brand noise removed, plurals folded."""
    text = (name or "").lower()
    text = _BRACKETS.sub(" ", text)
    text = text.replace("&", " and ")
    text = _SIZE.sub(" ", text)
    text = _NON_WORD.sub(" ", text)
    words = []
    for word in text.split():
        if len(word) <= 1 or word in STOPWORDS or word.isdigit():
            continue
        word = singular(word)
        if word not in words:
            words.append(word)
    return words


def tokens(name):
    """Lower-cased, size-free set of meaningful words in a product/ingredient name."""
    return set(_words(name))


def _core(name):
    """Words without the filler, falling back to all words when only filler is left."""
    words = _words(name)
    core = [w for w in words if w not in FILLER]
    return core or words


def similarity(first, second):
    """Jaccard similarity of the meaningful word sets, 0.0–1.0."""
    a, b = tokens(first), tokens(second)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_score(product_name, ingredient_name):
    """How well an ingredient describes a product, 0.0–1.0.

    Mostly containment (how much of the ingredient's name is in the product),
    so 'Milk' scores well against 'Full Cream Milk 2L'; some overlap so a more
    specific ingredient ('Chicken breast') beats a vaguer one ('Chicken'); and a
    small bonus when both names end on the same word, since product names put
    the noun last ('Full Cream Milk' is milk, not cream).
    """
    product, ingredient = _core(product_name), _core(ingredient_name)
    if not product or not ingredient:
        return 0.0
    p, i = set(product), set(ingredient)
    shared = p & i
    if not shared:
        return 0.0
    containment = len(shared) / len(i)
    overlap = len(shared) / len(p | i)
    score = 0.6 * containment + 0.3 * overlap
    if product[-1] == ingredient[-1]:
        score += 0.1
    return round(min(score, 1.0), 2)


def confidence(score):
    """'high', 'medium' or 'low' label for a match score."""
    if score >= HIGH_CONFIDENCE:
        return "high"
    if score >= MEDIUM_CONFIDENCE:
        return "medium"
    return "low"


def suggest_ingredient(product_name, ingredients, threshold=DEFAULT_THRESHOLD):
    """Best matching ingredient for a product name, or None below the threshold.

    Each ingredient is a dict with ``id`` and ``name``; ``display_name`` is
    also considered when present because it is what the user sees on lists.
    Returns {ingredient_id, score, confidence, matched_on}.
    """
    best = None
    best_score = 0.0
    for ingredient in ingredients or []:
        for name in (ingredient.get("name") or "", ingredient.get("display_name") or ""):
            score = match_score(product_name, name)
            # Ties go to the longer (more specific) ingredient name.
            if score > best_score or (best and score == best_score and score > 0
                                      and len(tokens(name)) > len(tokens(best[1]))):
                best, best_score = (ingredient, name), score
    if best is None or best_score < threshold:
        return None
    matched = [w for w in _core(best[1]) if w in tokens(product_name)]
    return {"ingredient_id": best[0].get("id"), "score": best_score,
            "confidence": confidence(best_score), "matched_on": matched}


def suggest_links(products, ingredients, threshold=DEFAULT_THRESHOLD):
    """{product_name: {"ingredient_id", "score", ...}} for products with a plausible match.

    Products already linked, or whose role isn't food, are skipped.
    """
    suggestions = {}
    for product in products or []:
        if product.get("ingredient_id") or product.get("role") in NON_FOOD_ROLES:
            continue
        match = suggest_ingredient(product.get("product_name") or "", ingredients, threshold)
        if match:
            suggestions[product["product_name"]] = match
    return suggestions


# ---------------------------------------------------------------------------
# Product roles
# ---------------------------------------------------------------------------

ROLES = [
    ("food", "Food"),
    ("treat", "Snacks & treats"),
    ("household", "Household"),
    ("personal_care", "Personal care"),
    ("pet", "Pet"),
    ("baby", "Baby"),
    ("fee", "Fees & bags"),
]
ROLE_LOOKUP = dict(ROLES)
NON_FOOD_ROLES = {"household", "personal_care", "pet", "baby", "fee"}

# Checked in order: the first role with a matching keyword wins, so 'dog food'
# is pet before it is food. Keywords match whole (singular) words or phrases.
_ROLE_KEYWORDS = [
    ("fee", ["delivery fee", "service fee", "bag fee", "reusable bag", "gift card", "shopping bag",
             "delivery", "surcharge", "deposit"]),
    ("pet", ["dog", "cat", "puppy", "kitten", "pet", "kitty litter", "litter", "bird seed", "whiskas",
             "pedigree", "fancy feast", "schmackos", "purina", "dog food", "cat food"]),
    ("baby", ["nappy", "nappie", "baby wipe", "infant formula", "baby food", "huggies", "babylove",
              "rafferty", "wipe"]),
    ("personal_care", ["shampoo", "conditioner", "toothpaste", "toothbrush", "deodorant", "antiperspirant",
                       "body wash", "soap", "razor", "shaving", "tampon", "sanitary pad", "panty liner", "sunscreen",
                       "moisturiser", "lotion", "cotton bud", "floss", "mouthwash", "panadol", "nurofen",
                       "vitamin", "plaster", "band aid", "lip balm", "hair spray", "hair gel", "colgate", "rexona",
                       "libra", "u by kotex"]),
    ("household", ["dishwasher", "dishwashing", "detergent", "laundry", "bleach", "cleaner", "surface spray",
                   "toilet paper", "toilet tissue", "paper towel", "foil", "cling wrap", "baking paper",
                   "garbage", "bin liner", "bin bag", "sponge", "scourer", "glove", "light bulb", "battery",
                   "batterie", "fabric softener", "softener", "air freshener", "insect", "napisan", "dettol", "glen 20", "domestos", "harpic", "handee", "quilton",
                   "sorbent", "kleenex", "candle", "napkin", "zip lock", "sandwich bag",
                   "freezer bag", "storage bag", "pegs", "cloth", "wettex", "mop", "broom"]),
    ("treat", ["chocolate", "chip", "crisp", "lolly", "lollie", "candy", "ice cream", "soft drink",
               "cola", "coke", "pepsi", "lemonade", "energy drink", "red bull", "biscuit",
               "cookie", "cake", "doughnut", "donut", "muffin", "tim tam", "kitkat", "cadbury", "pringle",
               "doritos", "chewing gum", "popcorn", "dessert"]),
]


def _phrase_in(phrase, words, text):
    parts = [singular(p) for p in phrase.split()]
    if len(parts) == 1:
        return parts[0] in words
    return " ".join(parts) in text


def classify_product(product_name):
    """Best-guess role for a purchased product name (see ROLES); 'food' when nothing else fits."""
    raw = re.sub(r"[^a-z0-9]+", " ", (product_name or "").lower()).split()
    folded = [singular(w) for w in raw]
    words = set(folded)
    text = " ".join(folded)
    for role, keywords in _ROLE_KEYWORDS:
        if any(_phrase_in(k, words, text) for k in keywords):
            return role
    return "food"


def product_role(product_name, decisions=None):
    """The role the user chose for a product, else the keyword guess."""
    decision = (decisions or {}).get(product_name) or {}
    return decision.get("role") or classify_product(product_name)


def is_food(role):
    return role not in NON_FOOD_ROLES


# ---------------------------------------------------------------------------
# Helpers for building test data
# ---------------------------------------------------------------------------

def make_product(product_name="Full Cream Milk 2L", ingredient_id=None, role=None):
    """Create a product row dict for testing."""
    return {"product_name": product_name, "ingredient_id": ingredient_id, "role": role}
