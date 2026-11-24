"""Travel rule: is this opportunity somewhere you can get to?

Your rule (settings.yaml -> travel):
  * in the Bay Area or Chicagoland  -> fine
  * remote / virtual                -> fine (no travel needed; can be turned off)
  * anywhere else                   -> only if it pays for BOTH housing and flights

Every item gets one of these "travel fit" values, plus a reason the dashboard shows:
  bay_area, chicagoland, remote   near you, or no travel needed
  covered                         far away, but housing + travel are paid for
  partial                         far away; only part is paid for (e.g. "up to $600 travel")
  outside                         far away, and nothing says housing + travel are covered
  no_location                     no location listed (memberships, TBA events) - not hidden

Evidence for "covered", strongest first:
  1. a registry entry's `travel_covered: full | partial | none` (hand-checked)
  2. phrases in the description / notes / scholarship text (config/keywords.yaml -> travel)
Job feeds like Simplify don't include descriptions, so their far-away jobs are
usually "outside" - not because housing isn't offered, but because we can't tell.
"""

from __future__ import annotations

import re

from radar.classify import compile_phrases
from radar.models import Item

FITS = {"bay_area", "chicagoland", "remote", "covered", "no_location"}
_NEGATION = re.compile(r"\b(?:no|not|n't|without|unable to|cannot|isn't|doesn't|does not|won't|will not)\b[^.;]{0,40}$", re.I)


def _found(pattern: re.Pattern | None, text: str) -> str | None:
    """First match of `pattern` in `text` that isn't negated ("housing is NOT provided")."""
    if not pattern:
        return None
    for match in pattern.finditer(text):
        before = text[max(0, match.start() - 45): match.start()]
        after = text[match.end(): match.end() + 25].lower()
        if _NEGATION.search(before) or re.match(r"\s*(?:is |are )?(?:not|n't)\b", after):
            continue
        return match.group(0)
    return None


class TravelClassifier:
    def __init__(self, keywords_cfg: dict, allow_remote: bool = True):
        cfg = (keywords_cfg or {}).get("travel", {}) or {}
        self.both = compile_phrases(cfg.get("both", []))
        self.housing = compile_phrases(cfg.get("housing", []))
        self.travel = compile_phrases(cfg.get("travel", []))
        self.allow_remote = allow_remote

    def classify(self, item: Item, region: str | None, remote: bool) -> tuple[str, str]:
        if region == "bay_area":
            return "bay_area", "In the Bay Area"
        if region == "chicagoland":
            return "chicagoland", "In Chicagoland"
        if remote and self.allow_remote:
            return "remote", "Remote / virtual - no travel needed"

        explicit = (item.extra.get("travel_covered") or "").lower()
        note = item.extra.get("travel_note") or ""
        if explicit == "full":
            return "covered", note or "Housing and travel are covered"
        if explicit == "partial":
            return "partial", note or "Only part of travel/housing is covered"
        if explicit == "none":
            return "outside", note or "Housing and travel are not covered"

        conference = item.extra.get("conference") or {}
        # Not the title: "Flight Software Intern" or "Hotel Operations Intern"
        # say nothing about who pays for travel.
        text = " ".join(filter(None, [item.text, item.extra.get("notes", ""), conference.get("scholarship_covers", "")]))
        both = _found(self.both, text)
        if both:
            return "covered", f'Says "{both}"'
        housing, travel = _found(self.housing, text), _found(self.travel, text)
        if housing and travel:
            return "covered", f'Mentions "{travel}" and "{housing}"'
        if housing or travel:
            return "partial", f'Only mentions "{housing or travel}"'
        if not item.locations:
            return "no_location", "No location listed"
        return "outside", "Not in the Bay Area or Chicagoland, and no sign it pays for housing + flights"
