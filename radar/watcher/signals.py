"""Clues on a program page besides dates.

  page_signal  "Applications are now open" -> open,  "Applications have
               closed" -> closed. A line that names only past years
               ("Applications for 2025 are closed") is ignored.
  years_seen   which of last / this / next year the page mentions. A page
               that mentions none of this year or next is probably stale.
"""

from __future__ import annotations

import re
from datetime import date

OPEN = re.compile(
    r"\b(?:applications?\s+(?:is|are)\s+(?:now\s+)?open\b|now\s+accepting\s+applications|"
    r"application\s+(?:portal|period|window)\s+is\s+(?:now\s+)?open|applications?\s+(?:have|has)\s+opened)", re.I)
CLOSED = re.compile(
    r"\b(?:applications?\s+(?:is|are|have|has)\s+(?:now\s+)?closed|no\s+longer\s+accepting\s+applications|"
    r"application\s+(?:period|window|portal)\s+(?:has\s+|is\s+)?(?:now\s+)?(?:ended|closed))", re.I)
YEAR = re.compile(r"\b(20\d\d)\b")


def _current_line(line: str, today: date) -> bool:
    years = [int(y) for y in YEAR.findall(line)]
    return not years or max(years) >= today.year


def page_signal(lines: list[str], today: date) -> tuple[str | None, str]:
    """("open" | "closed" | None, the line that says so)."""
    found: dict[str, str] = {}
    for line in lines:
        for name, pattern in (("open", OPEN), ("closed", CLOSED)):
            if name not in found and pattern.search(line) and _current_line(line, today):
                found[name] = line[:200]
    if len(found) == 1:                       # both -> the page is ambiguous; say nothing
        name, line = next(iter(found.items()))
        return name, line
    return None, ""


def years_seen(lines: list[str], today: date) -> list[int]:
    text = " ".join(lines)
    return [y for y in (today.year - 1, today.year, today.year + 1) if str(y) in text]
