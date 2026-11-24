"""Field classification: is this item CS, econ, biotech (or several)?

Order of evidence, cheapest first:
  1. the source itself (everything in the biotech repo is biotech; Simplify's
     "Quant" category is cs + econ) -> method "source"
  2. keyword rules from config/keywords.yaml, which can ADD fields
     ("Bioinformatics Intern" on Simplify -> cs + biotech) -> method "keyword"
  3. Claude, only for items that matched nothing -> method "llm" (radar/llm.py)
Anything still unplaced is marked "unplaced" and shown under all fields.
"""

from __future__ import annotations

import re

from radar.models import FIELDS, Item


def compile_phrases(phrases: list[str]) -> re.Pattern | None:
    """Turn ['machine learning', 'ai'] into one whole-word, case-insensitive regex."""
    cleaned = [p.strip() for p in phrases or [] if p and p.strip()]
    if not cleaned:
        return None
    # Longest first so "machine learning" wins over "machine".
    alternatives = sorted({re.escape(p) for p in cleaned}, key=len, reverse=True)
    # \b only works next to word characters; phrases like "c++" still match
    # because we use lookarounds for "not a letter/digit" instead.
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(alternatives) + r")(?![a-z0-9])", re.I)


class FieldClassifier:
    def __init__(self, keywords_cfg: dict):
        field_words = (keywords_cfg or {}).get("fields", {}) or {}
        self.patterns = {f: compile_phrases(field_words.get(f, [])) for f in FIELDS}

    def keyword_fields(self, text: str) -> set[str]:
        return {f for f, pattern in self.patterns.items() if pattern and pattern.search(text or "")}

    def classify(self, item: Item) -> tuple[list[str], str]:
        """Return (sorted fields, method)."""
        fields = {f for f in item.fields if f in FIELDS}
        method = "source" if fields else ""
        text = f"{item.title} {item.subcategory}"
        found = self.keyword_fields(text)
        if found - fields:
            fields |= found
            method = method or "keyword"
        return sorted(fields), (method or "unplaced")
