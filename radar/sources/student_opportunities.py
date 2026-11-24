"""gkarpe2204/student-opportunities - conferences + hackathons, updated daily by a bot.

data/conferences.json:
  {"updated": "2026-11-23", "count": 531, "conferences": [
    {"id": "sacnas-2027", "name": "SACNAS National Diversity in STEM Conference",
     "url": "https://...", "category": "student", "source": "watchlist",
     "start": "2027-10-28", "end": "2027-10-31", "location": "Kansas City, MO",
     "online": false, "deadline": "2027-06-01", "deadline_label": "Scholarship",
     "topics": ["STEM"], "funding": "Travel scholarships cover airfare...", "notes": "..."}]}

Categories: academic (CS paper venues from ccfddl - mostly PhD-level), hackathon
(MLH + Devpost), industry (developer conferences from confs.tech), and hand-picked
student / tech / premed / business events.
"""

from __future__ import annotations

from radar.models import Deadline, FetchResult
from radar.sources.base import Source, register
from radar.sources.conferences_common import conference_item, cost_tier, deadline_kind
from radar.timeutil import to_date_str, today_local

FIELDS_BY_CATEGORY = {
    "academic": ["cs"],      # ccfddl lists computer-science venues
    "hackathon": ["cs"],
    "industry": ["cs"],
    "tech": ["cs"],
    "premed": ["biotech"],
    "business": ["econ"],
    "student": [],           # multi-field (NSBE, SACNAS, NCUR...): keyword rules decide
}
UNDERGRAD_WORDS = ("student", "undergrad", "volunteer", "scholarship", "travel grant", "free for students")


@register("student_opportunities")
class StudentOpportunitiesSource(Source):
    def fetch(self) -> FetchResult:
        data, _ = self.get_json_with_fallback()
        return parse_student_opportunities(data, today_local())


def parse_student_opportunities(data: dict, today) -> FetchResult:
    items = []
    for c in data.get("conferences", []) or []:
        if not c.get("name") or not c.get("url"):
            continue
        category = c.get("category") or ""
        funding = (c.get("funding") or "").strip()
        notes = (c.get("notes") or "").strip()
        about = f"{notes} {funding} {' '.join(c.get('topics') or [])}".lower()

        hidden = ""
        if category in ("academic", "industry") and not any(w in about for w in UNDERGRAD_WORDS):
            hidden = "PhD-level research venue" if category == "academic" else "Industry trade show"

        deadlines = []
        if to_date_str(c.get("deadline")):
            label = c.get("deadline_label") or "Deadline"
            deadlines.append(Deadline(kind=deadline_kind(label), date=to_date_str(c["deadline"]), label=label.replace("⚠️", "").strip()))

        location = (c.get("location") or "").strip()
        items.append(
            conference_item(
                source_item_id=c.get("id") or c["url"],
                name=c["name"],
                url=c["url"],
                type_="hackathon" if category == "hackathon" else "conference",
                fields=list(FIELDS_BY_CATEGORY.get(category, [])),
                start=to_date_str(c.get("start")),
                end=to_date_str(c.get("end")),
                location=location or ("Online" if c.get("online") else ""),
                virtual=bool(c.get("online")),
                cost=cost_tier(funding) if funding else ("free" if category == "hackathon" else "unknown"),
                scholarship=True if funding else None,
                scholarship_covers=funding,
                poster_track=True if "poster" in about else None,
                deadlines=deadlines,
                notes=notes,
                organizer={"mlh": "Major League Hacking", "devpost": "Devpost"}.get(c.get("source"), ""),
                hidden_reason=hidden,
                text=" ".join(c.get("topics") or []),
                today=today,
            )
        )
    updated = to_date_str(data.get("updated"))
    return FetchResult(items=items, upstream_updated_at=f"{updated}T00:00:00Z" if updated else None)
