"""zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships.

That bot polls ~4,000 company job boards and publishes a JSON API on GitHub
Pages (the same file is also in the repo, used as a fallback):

  {"generated_at": "2026-11-24T00:21:45Z", "count": 1222, "jobs": [
     {"id": "ashby:illumio:78a9...", "company": "Illumio", "title": "...",
      "season": "Summer 2027", "category": "Data & ML/AI",
      "location": "Sunnyvale, California - HQ", "url": "https://...",
      "posted_at": "2026-11-24T00:00:26Z", "remote": false, "salary": "$37 per hour",
      "program": "Internship", ...}]}

Note: its feed is Atom (docs/feed.xml), not RSS; the JSON API has more fields,
so that's what we read.
"""

from __future__ import annotations

from radar.models import FetchResult, Item
from radar.sources.base import Source, register
from radar.timeutil import to_date_str


@register("zshah_jobs")
class ZshahJobsSource(Source):
    def fetch(self) -> FetchResult:
        data, _ = self.get_json_with_fallback()
        return parse_zshah(data, self.options)


def parse_zshah(data: dict, options: dict) -> FetchResult:
    by_category = options.get("fields_by_category", {}) or {}
    default_fields = options.get("default_fields", ["cs"])
    items = []
    for job in data.get("jobs", []) or []:
        if not job.get("url") or not job.get("title"):
            continue
        locations = [part.strip() for part in (job.get("location") or "").split(";") if part.strip()]
        season = job.get("season") or ""
        terms = [season] if season and season.lower() != "not stated" else []
        category = job.get("category") or ""
        items.append(
            Item(
                source_item_id=str(job.get("id") or job["url"]),
                title=job["title"].strip(),
                organization=(job.get("company") or "").strip(),
                url=job["url"],
                type="industry internship",
                fields=list(by_category.get(category, default_fields)),
                subcategory=category,
                locations=locations,
                remote=bool(job.get("remote")),
                posted_at=to_date_str(job.get("posted_at")),
                terms=terms,
                extra={
                    "salary": job.get("salary") or "",
                    "program": job.get("program") or "",
                    "off_season": bool(terms) and not any("summer" in t.lower() for t in terms),
                },
            )
        )
    return FetchResult(items=items, upstream_updated_at=data.get("generated_at"))
