"""Sources that publish a SimplifyJobs-style `listings.json` file.

Used for:
  * SimplifyJobs/Summer2027-Internships (dev branch) - Summer AND Off-Season
    listings live in the same file; off-season rows just have other `terms`.
  * vinaynarahari/Summer2027-Biotech-Internships - same schema (Phase 2).

One row looks like:
  {"id": "5bd0...", "company_name": "Illumio", "title": "Engineering Intern",
   "category": "Software", "active": true, "is_visible": true,
   "terms": ["Summer 2027"], "locations": ["Sunnyvale, CA"],
   "date_posted": 1795478400, "date_updated": 1795488129,
   "url": "https://...", "degrees": ["Bachelor's", "Master's"], ...}

We keep only active + visible rows, and skip rows whose every term has already
ended (e.g. only "Summer 2026"), because the bot sometimes leaves those active.
"""

from __future__ import annotations

import re
from datetime import date

from radar.models import FetchResult, Item
from radar.normalize import is_remote
from radar.sources.base import Source, register
from radar.timeutil import today_local, unix_to_date, unix_to_iso

# Last day of each season, used to decide if a term like "Summer 2026" is over.
_SEASON_END = {"winter": (3, 31), "spring": (6, 30), "summer": (8, 31), "fall": (12, 31), "autumn": (12, 31)}
_TERM = re.compile(r"^\s*(winter|spring|summer|fall|autumn)\s+(20\d\d)\s*$", re.I)


def term_has_ended(term: str, today: date) -> bool | None:
    """True/False for terms like 'Summer 2026'; None if we can't tell ('N/A', 'Co-op')."""
    match = _TERM.match(term or "")
    if not match:
        return None
    month, day = _SEASON_END[match.group(1).lower()]
    return date(int(match.group(2)), month, day) < today


@register("listings_json")
class ListingsJsonSource(Source):
    def fetch(self) -> FetchResult:
        rows = self.http.get_json(self.options["url"])
        return parse_listings(rows, self.options, today_local())


def parse_listings(rows: list[dict], options: dict, today: date) -> FetchResult:
    by_category: dict = options.get("fields_by_category", {}) or {}
    default_fields: list = options.get("default_fields", []) or []
    items: list[Item] = []
    newest_update = 0
    skipped_ended = 0

    for row in rows:
        newest_update = max(newest_update, row.get("date_updated") or 0)
        if not row.get("active") or not row.get("is_visible", True):
            continue
        terms = [t for t in row.get("terms") or [] if t]
        ended = [term_has_ended(t, today) for t in terms]
        if terms and all(e is True for e in ended):
            skipped_ended += 1
            continue

        category = row.get("category") or ""
        locations = [loc for loc in row.get("locations") or [] if loc]
        items.append(
            Item(
                source_item_id=str(row.get("id")),
                title=(row.get("title") or "").strip(),
                organization=(row.get("company_name") or "").strip(),
                url=row.get("url") or "",
                type="industry internship",
                fields=list(by_category.get(category, default_fields)),
                subcategory=category,
                locations=locations,
                remote=is_remote(locations),
                posted_at=unix_to_date(row.get("date_posted")),
                terms=terms,
                # "Incomplete" means Simplify doesn't know - not a requirement.
                degrees=[d for d in row.get("degrees") or [] if d and d != "Incomplete"],
                extra={
                    "off_season": bool(terms) and not any("summer" in t.lower() for t in terms),
                    "sponsorship": row.get("sponsorship") or "",
                },
            )
        )

    warnings = [f"skipped {skipped_ended} active rows whose terms have all ended"] if skipped_ended else []
    return FetchResult(items=items, upstream_updated_at=unix_to_iso(newest_update), warnings=warnings)
