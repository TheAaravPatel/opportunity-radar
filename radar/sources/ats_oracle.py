"""Oracle Recruiting Cloud ("Candidate Experience") public job search (no key needed).

Banks like JPMorganChase use it. Each employer has its own host and a site
number (CX_1, CX_1001...), both visible in its careers page links:
  https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/210800354

GET https://<host>/hcmRestApi/resources/latest/recruitingCEJobRequisitions
    ?onlyData=true&expand=requisitionList.secondaryLocations&limit=25
    &finder=findReqs;siteNumber=CX_1,keyword=sophomore,sortBy=POSTING_DATES_DESC,offset=0
  {"items": [{"TotalJobsCount": 4, "Offset": 0, "Limit": 25,
              "requisitionList": [{"Id": "210703254", "Title": "...", "PostedDate": "2026-10-23",
                                   "PrimaryLocation": "New York, NY, United States",
                                   "secondaryLocations": [{"Name": "Chicago, IL, United States"}],
                                   "ShortDescriptionStr": "..."}]}]}

The keyword search also matches descriptions, so titles are filtered here
like every other board. We send a few keywords (companies.yaml `search`),
at most MAX_PAGES pages each.
"""

from __future__ import annotations

from radar.http import Http
from radar.sources.ats_common import is_student_title, make_item, split_locations
from radar.timeutil import to_date_str

PAGE = 25
MAX_PAGES = 4


def _locations(req: dict) -> list[str]:
    names = [req.get("PrimaryLocation")]
    names += [(loc or {}).get("Name") for loc in req.get("secondaryLocations") or []]
    return split_locations([n for n in names if n])


def fetch_company(company: dict, http: Http) -> list:
    host, site = company["host"], company.get("site", "CX_1")
    api = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    found: dict[str, dict] = {}
    for keyword in company.get("search") or ["intern"]:
        offset = 0
        for _ in range(MAX_PAGES):
            params = {
                "onlyData": "true",
                "expand": "requisitionList.secondaryLocations",
                "finder": f"findReqs;siteNumber={site},keyword={keyword},sortBy=POSTING_DATES_DESC,offset={offset}",
                "limit": str(PAGE),
            }
            data = http.get(api, params=params, headers={"Accept": "application/json"}).json()
            page = (data.get("items") or [{}])[0]
            reqs = page.get("requisitionList") or []
            for req in reqs:
                if req.get("Id") and is_student_title(req.get("Title") or "", company):
                    found.setdefault(str(req["Id"]), req)
            total = page.get("TotalJobsCount") or 0
            # The API may return fewer per page than asked; trust its Limit.
            offset = (page.get("Offset") or offset) + (page.get("Limit") or PAGE)
            if not reqs or offset >= total:
                break

    return [
        make_item(company, "oracle", rid, req.get("Title") or "",
                  f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}",
                  _locations(req), posted_at=to_date_str(req.get("PostedDate")),
                  text=req.get("ShortDescriptionStr") or "")
        for rid, req in found.items()
    ]
