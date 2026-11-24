"""Workday public job search ("CXS") API (no key needed).

POST https://<tenant>.<host>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs
     {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "intern"}
  {"total": 57, "jobPostings": [{"title": "2027 Summer Intern - Research", "externalPath": "/job/San-Francisco/..._R-123",
                                 "locationsText": "San Francisco, CA", "postedOn": "Posted 3 Days Ago",
                                 "bulletFields": ["R-123"]}]}
Public job link: https://<tenant>.<host>.myworkdayjobs.com/<site><externalPath>

WARNING: Workday's bot protection blocks heavy pollers from GitHub Actions, so:
  * we send few requests (search text, max 5 pages per term, no per-job detail calls)
  * the shared HTTP client waits >= 3 s between Workday requests (settings.yaml)
  * a bot challenge raises BlockedError -> this company is marked failed and its
    existing jobs are NOT closed; SimplifyJobs covers many Workday employers anyway.
Workday only reports `total` on the first page of a search.
"""

from __future__ import annotations

from radar.http import Http
from radar.sources.ats_common import is_student_title, make_item, relative_posted, split_locations
from radar.timeutil import today_local

PAGE = 20
MAX_PAGES = 5


def fetch_company(company: dict, http: Http) -> list:
    domain = company.get("domain", "myworkdayjobs.com")
    base = f"https://{company['tenant']}.{company['host']}.{domain}"
    api = f"{base}/wday/cxs/{company['tenant']}/{company['site']}/jobs"
    today = today_local()
    found: dict[str, dict] = {}

    for term in company.get("search") or ["intern"]:
        total = None
        for page in range(MAX_PAGES):
            data = http.post_json(api, {"appliedFacets": {}, "limit": PAGE, "offset": page * PAGE, "searchText": term})
            if total is None:
                total = data.get("total") or 0
            postings = data.get("jobPostings") or []
            for posting in postings:
                path = posting.get("externalPath")
                # An empty search term means "this whole board is student-focused"
                # (e.g. Citi's early-careers board), so keep everything on it.
                if path and (term == "" or is_student_title(posting.get("title", ""), company)):
                    found.setdefault(path, posting)
            if len(postings) < PAGE or (page + 1) * PAGE >= total:
                break

    items = []
    for path, posting in found.items():
        locations_text = posting.get("locationsText") or ""
        # "3 Locations" isn't a place; leave the location blank rather than wrong.
        locations = [] if locations_text.endswith("Locations") else split_locations(locations_text)
        items.append(
            make_item(
                company, "workday", path.rstrip("/").rsplit("_", 1)[-1] or path, posting.get("title") or "",
                f"{base}/{company['site']}{path}", locations,
                posted_at=relative_posted(posting.get("postedOn"), today),
            )
        )
    return items
