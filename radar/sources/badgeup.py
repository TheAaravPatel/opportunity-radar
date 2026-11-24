"""Jose-Gael-Cruz-Lopez/BadgeUp - curated undergrad conferences (README tables).

STALE: no commits since Aug 8, 2026, and its statuses are out of date (it still
lists Grace Hopper 2026 as open). We still read it because it's a good curated
list across majors, but:
  * only its name / link / dates / location / cost columns are used (never its
    Status column),
  * past events come out as "closed",
  * source health shows it as stale (we ask GitHub for its last commit date).

Each section is a markdown table:
| Status | Conference | Focus | Dates & Location | Access | Audience | Register / Apply | Deadline |
"""

from __future__ import annotations

import re

from radar.models import FetchResult
from radar.sources.base import Source, register
from radar.sources.conferences_common import conference_item, cost_tier, parse_date_range
from radar.timeutil import today_local

# Section heading -> our fields. Sections not listed (Law, Arts, Education) are skipped.
SECTION_FIELDS = {
    "tech & computing": ["cs"],
    "engineering": ["cs"],
    "business & finance": ["econ"],
    "social sciences & policy": ["econ"],
    "health & medicine": ["biotech"],
    "sciences & research": ["biotech"],
    "multidisciplinary / general": [],
    "women in their field": [],
    "black professionals & students": [],
    "latinx professionals & students": [],
    "lgbtq+ in their field": [],
}
_HREF = re.compile(r'href="([^"]+)"')
_TAGS = re.compile(r"<[^>]+>")


@register("badgeup_readme")
class BadgeUpSource(Source):
    def fetch(self) -> FetchResult:
        text = self.http.get_text(self.options["url"])
        result = parse_badgeup(text, today_local())
        result.upstream_updated_at = self.github_last_commit(self.options.get("repo", ""))
        return result


def parse_badgeup(markdown: str, today) -> FetchResult:
    items, section = [], None
    for line in markdown.splitlines():
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        if section not in SECTION_FIELDS or not line.startswith("|") or line.startswith("| ---") or line.startswith("| Status"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) < 8:
            continue
        _status, name, focus, when_where, access, audience, register_cell, deadline_text = cells[:8]
        href = _HREF.search(register_cell)
        if not name or not href:
            continue
        name = _TAGS.sub("", name).replace("**", "").strip()
        when, _, where = when_where.partition("·")
        start, end = parse_date_range(when, today)
        where = where.strip()
        items.append(
            conference_item(
                source_item_id=re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
                name=name,
                url=href.group(1),
                type_="conference",
                fields=list(SECTION_FIELDS[section]),
                start=start,
                end=end,
                location=where,
                virtual="virtual" in where.lower() or "online" in where.lower(),
                cost=cost_tier(access),
                cost_note=access,
                scholarship=True if "scholarship" in access.lower() else None,
                notes=f"{focus}. Audience: {audience}. BadgeUp deadline note (may be outdated): {deadline_text}",
                text=f"{focus} {audience}",
                today=today,
            )
        )
    return FetchResult(items=items, warnings=["BadgeUp is hand-edited and stale (no updates since Aug 2026); deadlines come from notes only"])
