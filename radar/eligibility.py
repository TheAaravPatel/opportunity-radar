"""Eligibility scoring: how likely is it that YOU (a freshman right now) can apply?

Every item gets a 0-100 score plus the list of reasons that produced it, so the
dashboard can show *why* (e.g. "+30 Open to freshmen", "-30 PhD-level role").

Class years are always relative to your graduation year (settings.yaml), so the
same posting text scores differently next year when you're a sophomore.

Two tricky bits of English this handles:
  * "rising sophomores" means students who are freshmen NOW (they'll be
    sophomores by the summer), so it counts as open to freshmen.
  * "Junior Analyst" / "Senior Engineer" are job levels, not class years, so
    "junior"/"senior" only count when plural ("juniors and seniors") or followed
    by year/standing/students.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from radar.models import CLASS_YEARS, Item

PLURAL = {"freshman": "freshmen", "sophomore": "sophomores", "junior": "juniors", "senior": "seniors"}

# "rising juniors and seniors" -> "rising" applies to every class in the list.
_CLASS_WORD = r"(?:sophomore|junior|senior)s?"
_RISING = re.compile(rf"\brising\s+({_CLASS_WORD}(?:\s*(?:,|and|or|&|/)\s*{_CLASS_WORD})*)\b", re.I)
_CLASS_PATTERNS = {
    "freshman": re.compile(
        r"\bfreshm[ae]n\b|\b(?:first|1st)[- ]years?\b(?!\s+(?:analyst|associate|consultant|ib\b))"
        r"|\bunderclass(?:m[ae]n)?\b",
        re.I,
    ),
    "sophomore": re.compile(r"\bsophomores?\b|\b(?:second|2nd)[- ]years?\b(?!\s+(?:analyst|associate))|\bunderclass(?:m[ae]n)?\b", re.I),
    "junior": re.compile(
        r"\bjuniors\b|\bjunior\s+(?:year|standing|students?|undergrad\w*|class)\b|\b(?:third|3rd)[- ]years?\b(?!\s+(?:analyst|associate))",
        re.I,
    ),
    "senior": re.compile(
        r"\bseniors\b|\bsenior\s+(?:year|standing|students?|undergrad\w*|class)\b|\b(?:fourth|4th|final)[- ]years?\b(?!\s+(?:analyst|associate))",
        re.I,
    ),
}
# "graduating between December 2028 and June 2030", "Class of 2030", "2029 graduates"
_GRAD_CONTEXT = re.compile(r"graduat\w*|class of|grad(?:uation)?\s+(?:date|year)|expected to graduate", re.I)
_GRAD_SUFFIX = re.compile(r"\b(20\d\d)\s+(?:grads?|graduates)\b", re.I)
_YEAR = re.compile(r"\b(20\d\d)\b")
_BACHELOR = re.compile(r"bachelor|associate|undergrad", re.I)


def academic_year_end(today: date) -> int:
    """School year that ends next summer: Nov 2026 -> 2027 (the 2026-27 year)."""
    return today.year + (1 if today.month >= 7 else 0)


def class_for_grad_year(grad_year: int, today: date) -> str | None:
    """Which class someone graduating in `grad_year` is in today."""
    years_left = grad_year - academic_year_end(today)
    index = 3 - years_left          # 3 years left -> freshman (index 0)
    if 0 <= index < len(CLASS_YEARS):
        return CLASS_YEARS[index]
    return None


def detect_class_years(text: str) -> set[str]:
    found: set[str] = set()
    remaining = text or ""
    for match in _RISING.finditer(remaining):
        # rising sophomore -> freshman now, rising junior -> sophomore now, ...
        for word in re.findall(r"sophomore|junior|senior", match.group(1), re.I):
            found.add(CLASS_YEARS[CLASS_YEARS.index(word.lower()) - 1])
    remaining = _RISING.sub(" ", remaining)
    for year, pattern in _CLASS_PATTERNS.items():
        if pattern.search(remaining):
            found.add(year)
    return found


def detect_grad_years(text: str) -> list[int]:
    years: set[int] = set()
    for match in _GRAD_CONTEXT.finditer(text or ""):
        window = text[match.end(): match.end() + 70]
        years.update(int(y) for y in _YEAR.findall(window))
    years.update(int(y) for y in _GRAD_SUFFIX.findall(text or ""))
    return sorted(y for y in years if 2020 <= y <= 2040)


@dataclass
class Eligibility:
    score: int
    reasons: list[dict]          # [{"points": 30, "reason": "Open to freshmen"}, ...]
    class_years: list[str]       # detected/listed class years ([] = no restriction found)


class EligibilityScorer:
    def __init__(self, keywords_cfg: dict, grad_year: int):
        cfg = (keywords_cfg or {}).get("eligibility", {}) or {}
        self.grad_year = grad_year
        self.base = int(cfg.get("base", 50))
        self.points = {
            "none": int(cfg.get("no_class_year_bonus", 10)),
            "yours": int(cfg.get("your_year_bonus", 30)),
            "next": int(cfg.get("next_year_bonus", 5)),
            "other": int(cfg.get("other_years_penalty", -25)),
            "grad_ok": int(cfg.get("grad_window_bonus", 25)),
            "grad_early": int(cfg.get("grad_too_early_penalty", -30)),
        }
        self.rules = [
            (re.compile(rule["pattern"], re.I), int(rule["points"]), rule["reason"])
            for rule in cfg.get("rules", []) or []
        ]

    def score(self, item: Item, today: date) -> Eligibility:
        text = " ".join(
            part for part in [item.title, item.subcategory, item.text, " ".join(item.terms)] if part
        )
        reasons: list[dict] = []

        def add(points: int, reason: str) -> None:
            reasons.append({"points": points, "reason": reason})

        me = class_for_grad_year(self.grad_year, today)
        next_year = CLASS_YEARS[CLASS_YEARS.index(me) + 1] if me and me != "senior" else None

        # 1. Who can apply, by class year (listed by the registry, or found in the text).
        listed = set(item.class_years) if item.class_years is not None else detect_class_years(text)
        if listed and me:
            ordered = [y for y in CLASS_YEARS if y in listed]
            if me in listed:
                add(self.points["yours"], f"Open to {PLURAL[me]}")
            elif next_year in listed:
                add(self.points["next"], f"Open to {PLURAL[next_year]} - you can apply next year")
            else:
                add(self.points["other"], "For " + " / ".join(PLURAL[y] for y in ordered) + " only")

        # 2. Graduation-year windows ("graduating Dec 2028 - Jun 2030"). Skipped
        #    when a registry lists class years explicitly - those are final.
        grad_years = detect_grad_years(text) if item.class_years is None else []
        from_grad: set[str] = set()
        if grad_years:
            low, high = grad_years[0], grad_years[-1]
            from_grad = {c for y in range(low, high + 1) if (c := class_for_grad_year(y, today))}
            span = f"{low}" if low == high else f"{low}-{high}"
            if low <= self.grad_year <= high:
                add(self.points["grad_ok"], f"Graduation window {span} includes your {self.grad_year}")
            elif high < self.grad_year:
                add(self.points["grad_early"], f"Requires graduating by {high} (you graduate {self.grad_year})")
            else:
                add(-10, f"For later graduation years ({span})")

        if not listed and not grad_years:
            add(self.points["none"], "No class-year requirement found")

        # 3. Phrase rules from keywords.yaml (PhD-only, non-Stanford only, ...).
        fired_degree_rule = False
        for pattern, points, reason in self.rules:
            if pattern.search(text):
                add(points, reason)
                fired_degree_rule |= points < 0 and ("PhD" in reason or "graduate" in reason)

        # 4. The source's own degree list (Simplify: ["Master's", "PhD"]).
        if item.degrees and not fired_degree_rule and not any(_BACHELOR.search(d) for d in item.degrees):
            add(-25, "Listed for " + "/".join(item.degrees) + " candidates only")

        total = self.base + sum(r["points"] for r in reasons)
        class_years = [y for y in CLASS_YEARS if y in (listed | from_grad)]
        return Eligibility(score=max(0, min(100, total)), reasons=reasons, class_years=class_years)
