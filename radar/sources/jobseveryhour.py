"""Jobs Every Hour - free JSON list of tech internships (no key needed).

  https://jobseveryhour.com/api/v1/lists/tech-internships.json
  {"total": 5493, "jobs": [{"company": "...", "title": "...", "url": "...",
                            "location": "...", "remote": true, "salary": "...",
                            "posted": "..."}]}

Only the ~300 newest jobs are in the file, so it mostly helps catch brand-new
postings quickly; older ones come from Simplify / zshah101.

Terms of use: they ask for a link back (shown in the dashboard footer and the
Sources panel) and for fetching each feed at most once an hour (we fetch every
6 hours). The response format above comes from their own GitHub mirror's
script; the verify workflow confirms it against the live API.
"""

from __future__ import annotations

from radar.models import FetchResult, Item
from radar.sources.base import Source, register
from radar.timeutil import to_date_str


@register("jobseveryhour")
class JobsEveryHourSource(Source):
    def fetch(self) -> FetchResult:
        data, _ = self.get_json_with_fallback()
        return parse_jobseveryhour(data, self.options)


def parse_jobseveryhour(data: dict, options: dict) -> FetchResult:
    items = []
    for job in data.get("jobs", []) or []:
        url, title = job.get("url"), (job.get("title") or "").strip()
        if not url or not title:
            continue
        location = (job.get("location") or "").strip()
        items.append(
            Item(
                source_item_id=url,                      # the feed has no id; the URL is stable
                title=title,
                organization=(job.get("company") or "").strip(),
                url=url,
                type="industry internship",
                fields=list(options.get("default_fields", ["cs"])),
                locations=[part.strip() for part in location.split(";") if part.strip()],
                remote=bool(job.get("remote")) or "remote" in location.lower(),
                posted_at=to_date_str(job.get("posted")),   # None if it's relative text
                extra={"salary": job.get("salary") or ""},
            )
        )
    warnings = []
    total = data.get("total")
    if isinstance(total, int) and total > len(items):
        warnings.append(f"feed lists the {len(items)} newest of {total} open roles")
    return FetchResult(items=items, warnings=warnings)
