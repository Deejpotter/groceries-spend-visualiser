"""Experimental stock count from photos of the cupboard, fridge or freezer.

A vision model (through the same OpenRouter key as the other AI reviews) is
shown the photos and the list of tracked items, and guesses how many packs of
each it can see. Guesses are only ever shown beside the count boxes, never
saved as counts, so the user's own count stays independent and the two can be
compared to see whether photo counts are reliable enough to trust.
"""

import base64
import os
from typing import Dict, Iterable, List, Optional

from services.ai_review import AIReviewError, request_chat_json

DEFAULT_MODEL = "anthropic/claude-opus-5.5"
MAX_PHOTOS = 4
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_ITEMS = 200
CONFIDENCES = ["high", "medium", "low"]

_SYSTEM_PROMPT = """You count groceries in photos of an Australian household's pantry, fridge and freezer.

You get a numbered list of items the household tracks and one or more photos.
For each listed item you can actually see, count the packs: one bottle, carton,
tin, bag, box or loaf is 1, whatever its size. A multipack still in its outer
wrap counts as the number of packs inside when you can tell, otherwise 1.
An opened pack counts as 1.

Only include items you can see. Leave out anything not visible or that you
can't tell apart from a similar item. Never guess an item into a photo.
Photos may overlap; don't count the same pack twice.

Answer with JSON only:
{"counts": [{"item": 3, "count": 2, "confidence": "high", "seen_as": "2 x Devondale long life milk 1L"}]}

- item is the number from the list.
- confidence is high (clearly visible and countable), medium (partly hidden or
  some packs may be behind others) or low (unsure it's the item).
- seen_as is a few words on what you saw.
"""


def resolve_model(get_setting) -> str:
    return ((get_setting("photo_count_model") or "").strip()
            or (os.getenv("PHOTO_COUNT_MODEL") or "").strip() or DEFAULT_MODEL)


def image_type(data: bytes) -> Optional[str]:
    """Media type from the file signature, or None when it isn't a supported image."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def item_list(rows: Iterable[Dict]) -> List[Dict]:
    """The tracked items, numbered for the prompt."""
    items = []
    for number, row in enumerate(list(rows)[:MAX_ITEMS], start=1):
        product = (row.get("products") or [None])[0]
        items.append({"number": number, "key": row["key"], "label": row["label"],
                      "product": product if product and product != row["label"] else None})
    return items


def build_prompt(items: List[Dict]) -> str:
    lines = [f"{i['number']}. {i['label']}" + (f" (usually bought as: {i['product']})" if i["product"] else "")
             for i in items]
    return "Items:\n" + "\n".join(lines) + "\n\nCount the listed items you can see in these photos."


def build_content(items: List[Dict], photos: List[tuple]) -> List[Dict]:
    """The user message: the item list then each photo as a data URL. ``photos`` is [(media_type, bytes)]."""
    content = [{"type": "text", "text": build_prompt(items)}]
    for media_type, data in photos:
        encoded = base64.standard_b64encode(data).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}})
    return content


def parse_counts(payload: Dict, items: List[Dict]) -> Dict[str, Dict]:
    """Valid guesses keyed by item key. Unknown items, negative or non-numeric counts are dropped."""
    by_number = {i["number"]: i for i in items}
    found: Dict[str, Dict] = {}
    for entry in payload.get("counts") or []:
        if not isinstance(entry, dict):
            continue
        try:
            item = by_number.get(int(entry.get("item")))
            count = float(entry.get("count"))
        except (TypeError, ValueError):
            continue
        if not item or count < 0:
            continue
        confidence = entry.get("confidence") if entry.get("confidence") in CONFIDENCES else "low"
        previous = found.get(item["key"])
        if previous:  # listed twice: the photos overlap or the model split it, so add them
            count += previous["count"]
        found[item["key"]] = {"count": count, "confidence": confidence,
                              "seen_as": str(entry.get("seen_as") or "")[:120]}
    return found


def count_from_photos(rows: Iterable[Dict], photos: List[tuple], api_key: str, model: str) -> Dict[str, Dict]:
    """Ask the model for counts. Raises AIReviewError when the request fails."""
    if not photos:
        raise AIReviewError("Add at least one photo.")
    items = item_list(rows)
    if not items:
        raise AIReviewError("There are no tracked items to count yet.")
    payload = request_chat_json(_SYSTEM_PROMPT, build_content(items, photos), api_key, model,
                                temperature=None)
    return parse_counts(payload, items)


def accuracy(pairs: Iterable[Dict]) -> Optional[Dict]:
    """How photo guesses compare with the user's own counts on the same day.

    Each pair has ``photo`` and ``counted`` (plus ``label`` and ``counted_on``).
    Returns None when nothing has been compared yet.
    """
    pairs = [p for p in pairs if p.get("photo") is not None and p.get("counted") is not None]
    if not pairs:
        return None
    exact = sum(1 for p in pairs if abs(p["photo"] - p["counted"]) < 0.5)
    close = sum(1 for p in pairs if abs(p["photo"] - p["counted"]) <= 1)
    misses = sorted((p for p in pairs if abs(p["photo"] - p["counted"]) >= 0.5),
                    key=lambda p: -abs(p["photo"] - p["counted"]))
    return {
        "compared": len(pairs),
        "exact": exact,
        "within_one": close,
        "exact_pct": round(100 * exact / len(pairs)),
        "mean_error": round(sum(abs(p["photo"] - p["counted"]) for p in pairs) / len(pairs), 2),
        "misses": misses[:10],
    }


def make_photo_pair(label: str = "Milk", photo: float = 2, counted: float = 2,
                    counted_on: str = "2026-11-08") -> Dict:
    """A photo-versus-count pair for tests."""
    return {"label": label, "photo": photo, "counted": counted, "counted_on": counted_on}
