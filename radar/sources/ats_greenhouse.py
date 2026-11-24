"""Greenhouse public job board API (no key needed).

GET https://boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true
  {"jobs": [{"id": 4735791005, "title": "Research Analyst Intern - Summer 2027",
             "absolute_url": "https://job-boards.greenhouse.io/<slug>/jobs/4735791005",
             "location": {"name": "Boston, MA; Washington, DC"},
             "first_published": "2026-10-01T12:00:00-04:00", "updated_at": "...",
             "content": "&lt;p&gt;HTML-escaped description&lt;/p&gt;"}], "meta": {"total": 12}}
`content=true` adds the description, which eligibility scoring reads
("open to rising sophomores", "PhD students only"...).
"""

from __future__ import annotations

from radar.http import Http
from radar.sources.ats_common import html_to_text, is_student_title, make_item, split_locations
from radar.timeutil import to_date_str

URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def fetch_company(company: dict, http: Http) -> list:
    data = http.get_json(URL.format(slug=company["slug"]))
    items = []
    for job in data.get("jobs", []) or []:
        title = job.get("title") or ""
        if not is_student_title(title):
            continue
        location = (job.get("location") or {}).get("name") if isinstance(job.get("location"), dict) else None
        items.append(
            make_item(
                company, "greenhouse", str(job.get("id")), title, job.get("absolute_url") or "",
                split_locations(location),
                posted_at=to_date_str(job.get("first_published") or job.get("updated_at")),
                text=html_to_text(job.get("content")),
            )
        )
    return items
