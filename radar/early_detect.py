"""Spotting early-career (freshman/sophomore) programs inside ordinary job feeds.

Uses the `early_career` word lists in config/keywords.yaml:
  strong words count on their own:          "Sophomore Summer Analyst"
  weak words need a program word right after them (at most 2 words between):
                                            "Insight Day", "Discovery Program"
so "Market Research Insights Intern" and "Internship Program: Drug Discovery
Intern" are NOT flagged.

radar/early_match.py then links flagged postings to registry programs.
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
        # "<weak word> (up to 2 other words) <program word>", e.g. "Insight Day",
        # "Discovery Summer Program", "Explore Series".
        self.weak_then_program = None
        if self.weak and self.program_words:
            self.weak_then_program = re.compile(
                self.weak.pattern + r"(?:[\s:/&-]+[\w'.]+){0,2}?[\s:/&-]+" + self.program_words.pattern, re.I)

    def is_early(self, title: str) -> bool:
        text = _NEW_GRAD_LEVEL.sub(" ", title or "")
        if self.strong and self.strong.search(text):
            return True
        return bool(self.weak_then_program and self.weak_then_program.search(text))
