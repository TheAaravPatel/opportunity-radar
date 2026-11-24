"""Finding deadlines in a page's text.

A date counts only when a deadline-ish word is right before it ("Deadline:",
"due", "apply by", "applications open"...) or right after it ("Jan 12
deadline"). Program dates, event dates and "posted on" dates are ignored.

  "Applications are due February 1, 2027"     -> application  2027-02-01
  "Priority deadline: Jan. 4th, 2027"         -> priority     2027-01-04
  "The portal opens December 14, 2026"        -> opens        2026-12-14
  "Applications due January 27" (no year)     -> application  2027-01-27, approx,
                                                 only if the page mentions 2027

Dates are parsed with dateparser. Pages that clearly talk about deadlines
but defeat these rules can be sent to Claude (radar/llm.py) by the caller.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import dateparser

from radar.models import Deadline

MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
         r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
DATE = re.compile(
    rf"\b(?:{MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+(?P<y1>20\d\d))?"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH}\b\.?(?:,?\s+(?P<y2>20\d\d))?"
    rf"|\d{{1,2}}/\d{{1,2}}/(?P<y3>(?:20)?\d\d)\b"
    rf"|(?P<y4>20\d\d)-\d\d-\d\d\b)",
    re.I,
)
# The nearest of these before a date decides what kind of date it is.
KEYWORDS = [
    ("opens", re.compile(r"\b(?:open(?:s|ing)?|re-?opens?|go(?:es)? live|launch(?:es)?|available (?:on|starting|from)"
                         r"|accepting applications (?:from|starting|beginning)|begins? accepting|starts? accepting)\b", re.I)),
    ("application", re.compile(r"\b(?:deadlines?|due|apply by|submit(?:ted)? by|no later than|last day to apply"
                               r"|closes?|closing|accepted (?:until|through)|open until|apply until)\b"
                               r"|\bapplications?(?:\s+(?:period|window))?\s*:", re.I)),   # "Applications: Jan 27 - Feb 10"
]
AFTER_DEADLINE = re.compile(r"^\s*(?:[-–]\s*)?(?:deadline|due date|is the deadline)\b", re.I)
PRIORITY = re.compile(r"\bpriority\b", re.I)
NOT_A_DEADLINE = re.compile(r"\b(?:posted|updated|last modified|published|copyright)\b|©", re.I)
MENTIONS_DEADLINES = re.compile(r"\b(?:deadline|due|apply by|applications? (?:open|close|are due))\b", re.I)
SENTENCE_BREAK = re.compile(r";|\.\s+[A-Z]")      # "Deadline: rolling. Program June 22..." isn't one
RANGE_SEP = re.compile(r"^\s*(?:-|–|—|to|through|until)\s*$", re.I)
UNDECIDED = re.compile(r"\b(?:tba|tbd|to be (?:announced|determined)|rolling)\b", re.I)

MAX_GAP = 80            # characters allowed between the keyword and the date
PAST_DAYS = 30          # keep deadlines that passed up to this many days ago
FUTURE_DAYS = 450       # ...and ignore anything further ahead than this
LABELS = {"application": "Deadline (found on page)", "priority": "Priority deadline (found on page)",
          "opens": "Applications open (found on page)"}


def _parse(text: str, today: date) -> date | None:
    base = datetime.combine(today - timedelta(days=PAST_DAYS), datetime.min.time())
    parsed = dateparser.parse(
        text.replace(".", ""), languages=["en"],
        settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": base, "DATE_ORDER": "MDY",
                  "REQUIRE_PARTS": ["day", "month"]},
    )
    return parsed.date() if parsed else None


def _kind_for(line: str, start: int, end: int) -> str | None:
    before = line[max(0, start - 120):start]
    best, best_start, best_end = None, -1, -1
    for kind, pattern in KEYWORDS:
        for match in pattern.finditer(before):
            if match.end() > best_end:
                best, best_start, best_end = kind, match.start(), match.end()
    if best and len(before) - best_end <= MAX_GAP and not SENTENCE_BREAK.search(before[best_end:]):
        if NOT_A_DEADLINE.search(before[best_end:]):
            return None
        # "Priority deadline: ..." - the word sits right before the keyword.
        if best == "application" and PRIORITY.search(before[max(0, best_start - 20):best_end]):
            return "priority"
        return best
    if AFTER_DEADLINE.search(line[end:end + 30]) and not NOT_A_DEADLINE.search(before[-40:]):
        return "application"
    return None


def find_deadlines(lines: list[str], today: date) -> list[Deadline]:
    page_text = "\n".join(lines)
    found: dict[tuple[str, str], Deadline] = {}
    for line in lines:
        matches = list(DATE.finditer(line))
        range_kind: dict[int, str] = {}
        for i, match in enumerate(matches):
            kind = range_kind.get(i) or _kind_for(line, match.start(), match.end())
            if not kind:
                continue
            # "Applications: January 27 - February 10, 2027" is a window:
            # the first date is when they open, the second the deadline.
            if i + 1 < len(matches) and RANGE_SEP.match(line[match.end():matches[i + 1].start()]):
                kind, range_kind[i + 1] = "opens", "application"
            year = next((y for y in match.group("y1", "y2", "y3", "y4") if y), None)
            text, approx = match.group(0), False
            if not year and i + 1 < len(matches) and matches[i + 1].start() - match.end() <= 30:
                # "January 27 - February 10, 2027": the year comes after the range.
                later = matches[i + 1]
                later_year = next((y for y in later.group("y1", "y2", "y3", "y4") if y), None)
                if later_year:
                    year = later_year
                    text = f"{text} {later_year}"
            day = _parse(text, today)
            if not day:
                continue
            if not year:
                # No year on the page: assume the next such date, but only if
                # the page mentions that year somewhere (so it's this cycle).
                if str(day.year) not in page_text:
                    continue
                approx = True
            if not (today - timedelta(days=PAST_DAYS) <= day <= today + timedelta(days=FUTURE_DAYS)):
                continue
            label = LABELS[kind] if not approx else LABELS[kind].replace("(found", "(year assumed - found")
            found.setdefault((kind, day.isoformat()), Deadline(
                kind=kind, date=day.isoformat(), label=label, approx=approx, method="regex", evidence=line[:240]))
    return sorted(found.values(), key=lambda d: (d.date, d.kind))


def deadline_excerpt(lines: list[str], max_lines: int = 80) -> list[str]:
    """Lines that talk about deadlines, with one line of context each side."""
    keep: set[int] = set()
    for i, line in enumerate(lines):
        if MENTIONS_DEADLINES.search(line):
            keep.update({i - 1, i, i + 1})
    return [lines[i] for i in sorted(keep) if 0 <= i < len(lines)][:max_lines]


def worth_asking_claude(lines: list[str], found: list[Deadline]) -> bool:
    """The page talks about deadlines, yet the rules found no date for them."""
    if found:
        return False
    mentions = [line for line in lines if MENTIONS_DEADLINES.search(line)]
    return bool(mentions) and not all(UNDECIDED.search(line) for line in mentions)
