"""Spotting early-career (freshman/sophomore) programs inside ordinary job feeds.

Uses the `early_career` word lists in config/keywords.yaml:
  strong words count on their own:            "Sophomore Summer Analyst"
  weak words need a program word next to them: "Insight Day", "Discovery Program"
so "Market Research Insights Intern" is NOT flagged.

Phase 5 adds matching flagged postings to registry entries and suggesting new
registry entries for unmatched ones.
"""

from __future__ import annotations

import re

from radar.classify import compile_phrases

# "First Year Analyst" at a bank means a NEW GRAD, not a first-year student.
_NEW_GRAD_LEVEL = re.compile(r"\b(?:first|1st|second|2nd)[- ]year\s+(?:analyst|associate|consultant|ib)\b", re.I)


class EarlyCareerDetector:
    def __init__(self, keywords_cfg: dict):
        cfg = (keywords_cfg or {}).get("early_career", {}) or {}
        if isinstance(cfg, list):          # older flat format: treat all as strong
            cfg = {"strong": cfg}
        self.strong = compile_phrases(cfg.get("strong", []))
        self.weak = compile_phrases(cfg.get("weak", []))
        self.program_words = compile_phrases(cfg.get("program_words", []))

    def is_early(self, title: str) -> bool:
        text = _NEW_GRAD_LEVEL.sub(" ", title or "")
        if self.strong and self.strong.search(text):
            return True
        return bool(self.weak and self.weak.search(text) and self.program_words and self.program_words.search(text))
