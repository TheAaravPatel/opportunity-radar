"""Shared logic for conference, hackathon and travel-scholarship sources.

Rules from the spec, implemented here so every conference source follows them:
  * Track the event dates AND each deadline separately. For freshmen the travel
    scholarship deadline matters most, and it's often months before the event.
  * Priority: free or scholarship-funded > student-priced > full price; Bay Area
    or virtual ranks above far away when the cost is the same.
  * Flag undergraduate poster / presentation tracks.
  * Hide pure industry trade shows and PhD-only academic conferences unless they
    offer an undergrad scholarship or volunteer program (hidden = not shown by
    default; a dashboard toggle reveals them).
  * Hackathons are their own type; Bay Area and online ones show by default.
  * Deduplicate by conference name + year.
"""

from __future__ import annotations

import re
from datetime import date

from radar.models import Deadline, Item
from radar.normalize import is_bay_area, is_chicagoland, simplify_text

# Lower = better. "unknown" sits between student-priced and full price.
COST_RANK = {"free": 0, "scholarship": 0, "student": 1, "unknown": 1.5, "full": 2}

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
# "Oct 28-29, 2026", "Nov 30-Dec 4, 2026", "Feb 9-11, 2027", "Mar 21, 2027", "Mar 24-28"
_DATE_RANGE = re.compile(
    r"(?P<m1>[A-Za-z]{3,9})\.?\s+(?P<d1>\d{1,2})"
    r"(?:\s*[-–]\s*(?:(?P<m2>[A-Za-z]{3,9})\.?\s+)?(?P<d2>\d{1,2}))?"
    r"(?:,?\s*(?P<y>20\d\d))?"
)


def parse_date_range(text: str, today: date) -> tuple[str | None, str | None]:
    """Pull a start/end date out of text like 'Oct 27-30, 2026 · Anaheim, CA'."""
    for match in _DATE_RANGE.finditer(text or ""):
        m1 = _MONTHS.get(match.group("m1")[:3].lower())
        if not m1:
            continue
        m2 = _MONTHS.get((match.group("m2") or match.group("m1"))[:3].lower()) or m1
        d1 = int(match.group("d1"))
        d2 = int(match.group("d2") or d1)
        year = int(match.group("y")) if match.group("y") else None
        if year is None:   # no year: the next time that date comes around
            year = today.year if (m1, d1) >= (today.month, today.day) else today.year + 1
        end_year = year + 1 if m2 < m1 else year
        try:
            return date(year, m1, d1).isoformat(), date(end_year, m2, d2).isoformat()
        except ValueError:
            continue
    return None, None


def deadline_kind(label: str) -> str:
    """Map a free-text deadline label to one of our deadline kinds."""
    low = (label or "").lower()
    if any(w in low for w in ("scholarship", "travel", "grant", "award", "funding", "fellowship")):
        return "scholarship"
    if any(w in low for w in ("abstract", "paper", "cfp", "submission", "poster", "proposal", "case plan")):
        return "abstract"
    if "application" in low or "apply" in low:
        return "application"
    return "registration"


def cost_tier(text: str) -> str:
    """'Free (virtual pass)' -> free, 'Scholarship · Paid' -> scholarship, 'Paid (student)' -> student."""
    low = (text or "").lower()
    if "free" in low:
        return "free"
    if "scholarship" in low or "travel grant" in low or "funded" in low:
        return "scholarship"
    if "student" in low:
        return "student"
    if "paid" in low or "$" in low:
        return "full"
    return "unknown"


def key_name(name: str) -> str:
    """Name used for dedupe: drop years and anything in parentheses.

    'ABRCMS (Annual Biomedical Research Conference ...)' -> 'abrcms'
    'SHPE National Convention 2027' -> 'shpe national convention'
    """
    base = re.sub(r"\(.*?\)", " ", name or "")
    base = re.sub(r"\b20\d\d\b|['’]\d\d\b", " ", base)
    # Trailing edition numbers: "LA Hacks 27", "Cal Hacks 13.0" -> "la hacks", "cal hacks"
    base = re.sub(r"\s\d{1,2}(?:\.0)?\s*$", "", base.strip())
    return simplify_text(base)


def conference_item(
    *,
    source_item_id: str,
    name: str,
    url: str,
    type_: str,
    fields: list[str],
    start: str | None,
    end: str | None,
    location: str,
    virtual: bool,
    cost: str,
    cost_note: str = "",
    scholarship: bool | None = None,
    scholarship_covers: str = "",
    undergrad_eligible: bool | None = None,
    poster_track: bool | None = None,
    deadlines: list[Deadline] | None = None,
    notes: str = "",
    organizer: str = "",
    hidden_reason: str = "",
    status: str | None = None,
    status_reason: str = "",
    registry_id: str = "",
    class_years: list[str] | None = None,
    text: str = "",
    today: date,
    dedupe_name: str = "",
) -> Item:
    locations = [location] if location else []
    bay_area = is_bay_area(locations)
    chicagoland = is_chicagoland(locations)
    year = (start or "")[:4] or (re.search(r"20\d\d", name or "") or [None])[0] or "tba"
    all_deadlines = list(deadlines or [])
    if start:
        all_deadlines.append(Deadline(kind="event", date=start, label="Event starts"))

    # Past events are closed; everything else is open unless the source says otherwise.
    last_day = end or start
    if last_day and last_day < today.isoformat():
        status, status_reason = "closed", "Event is over"
    # Near you (Bay Area or Chicagoland) or virtual ranks above far away at the same cost.
    priority = COST_RANK.get(cost, 1.5) + (0 if (bay_area or chicagoland or virtual) else 0.5)

    return Item(
        source_item_id=source_item_id,
        title=name.strip(),
        organization=organizer or name.strip(),
        url=url,
        type=type_,
        fields=fields,
        subcategory=type_,
        locations=locations,
        remote=virtual,
        class_years=class_years,
        text=" ".join(filter(None, [notes, text, scholarship_covers])),
        deadlines=all_deadlines,
        status=status,
        status_reason=status_reason,
        registry_id=registry_id,
        dedupe_key=f"conf|{dedupe_name or key_name(name)}|{year}",
        extra={
            "notes": notes,
            "hidden_reason": hidden_reason,
            "priority": priority,
            "conference": {
                "year": int(year) if str(year).isdigit() else None,
                "start_date": start,
                "end_date": end,
                "location": location,
                "virtual": virtual,
                "bay_area": bay_area,
                "chicagoland": chicagoland,
                "cost_tier": cost,
                "cost_note": cost_note,
                "scholarship": scholarship,
                "scholarship_covers": scholarship_covers,
                "undergrad_eligible": undergrad_eligible,
                "poster_track": poster_track,
            },
        },
    )
