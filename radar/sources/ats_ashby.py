"""Ashby public job board API (no key needed).

GET https://api.ashbyhq.com/posting-api/job-board/<slug>?includeCompensation=true
  {"jobs": [{"id": "...", "title": "Machine Learning Intern", "location": "South San Francisco, CA",
             "secondaryLocations": [{"location": "Remote"}], "isRemote": false,
             "employmentType": "Intern", "isListed": true, "publishedAt": "2026-10-02T17:00:00.000+00:00",
             "jobUrl": "https://jobs.ashbyhq.com/<slug>/<id>", "descriptionPlain": "..."}]}
"""

from __future__ import annotations

from radar.http import Http
from radar.sources.ats_common import html_to_text, is_student_title, make_item, split_locations
from radar.timeutil import to_date_str

URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


def fetch_company(company: dict, http: Http) -> list:
    data = http.get_json(URL.format(slug=company["slug"]))
    items = []
    for job in data.get("jobs", []) or []:
        if job.get("isListed") is False:
            continue
        title = job.get("title") or ""
        if not (is_student_title(title) or (job.get("employmentType") or "").lower() == "intern"):
            continue
        secondary = [s.get("location") for s in job.get("secondaryLocations") or [] if isinstance(s, dict)]
        items.append(
            make_item(
                company, "ashby", str(job.get("id") or job.get("jobUrl")), title, job.get("jobUrl") or job.get("applyUrl") or "",
                split_locations(job.get("location"), secondary),
                posted_at=to_date_str(job.get("publishedAt")),
                text=(job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml")))[:4000],
                remote=bool(job.get("isRemote")) or (job.get("workplaceType") or "").lower() == "remote",
            )
        )
    return items
