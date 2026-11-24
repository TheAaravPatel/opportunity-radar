"""USAJobs (federal government) - student / intern economics roles.

Official API, free key: https://developer.usajobs.gov/apirequest/
Needs two GitHub Secrets: USAJOBS_API_KEY and USAJOBS_EMAIL (USAJobs requires
the email you registered with as the User-Agent). Without them the source just
waits (see `requires_env` in sources.yaml) - nothing breaks.

GET https://data.usajobs.gov/api/search?JobCategoryCode=0110&HiringPath=STUDENT&ResultsPerPage=250
  {"SearchResult": {"SearchResultCount": 3, "SearchResultItems": [
     {"MatchedObjectId": "8123", "MatchedObjectDescriptor": {
        "PositionTitle": "Economist (Student Trainee)", "PositionURI": "https://www.usajobs.gov/job/8123",
        "OrganizationName": "Bureau of Economic Analysis", "DepartmentName": "Department of Commerce",
        "PositionLocation": [{"CityName": "Suitland, Maryland"}], "PositionLocationDisplay": "Suitland, Maryland",
        "PublicationStartDate": "2026-11-20T00:00:00.0000", "ApplicationCloseDate": "2026-12-10T23:59:59.9970",
        "UserArea": {"Details": {"JobSummary": "...", "HiringPath": ["STUDENT"]}}}}]}}

The student hiring-path code is looked up from USAJobs' own code list each run
(it's "STUDENT" today) instead of being hard-coded.
"""

from __future__ import annotations

import os

from radar.models import Deadline, FetchResult, Item
from radar.sources.base import Source, register
from radar.timeutil import to_date_str

API = "https://data.usajobs.gov/api"
# Occupational series (job families) worth searching, and the fields they map to.
DEFAULT_SERIES = {
    "0110": ["econ"],          # Economist
    "0119": ["econ"],          # Economics Assistant
    "0101": ["econ"],          # Social Science
    "0199": ["econ"],          # Social Science Student Trainee
    "1530": ["econ", "cs"],    # Statistics
    "1515": ["econ", "cs"],    # Operations Research
    "1599": ["econ", "cs"],    # Mathematics and Statistics Student Trainee
}


@register("usajobs")
class UsaJobsSource(Source):
    def fetch(self) -> FetchResult:
        headers = {
            "Host": "data.usajobs.gov",
            "User-Agent": os.environ["USAJOBS_EMAIL"],
            "Authorization-Key": os.environ["USAJOBS_API_KEY"],
        }
        codes = self._student_codes()
        series = self.options.get("series") or DEFAULT_SERIES
        items: dict[str, Item] = {}
        for code, fields in series.items():
            for path in codes:
                data = self.http.get_json(
                    f"{API}/search?JobCategoryCode={code}&HiringPath={path}&ResultsPerPage=250", headers=headers
                )
                for item in parse_usajobs(data, list(fields)):
                    items.setdefault(item.source_item_id, item)
        return FetchResult(items=list(items.values()))

    def _student_codes(self) -> list[str]:
        try:
            data = self.http.get_json(f"{API}/codelist/hiringpaths")
            values = data["CodeList"][0]["ValidValue"]
            codes = [v["Code"] for v in values if "student" in v.get("Value", "").lower() and v.get("IsDisabled") != "Yes"]
            return codes or ["STUDENT"]
        except Exception:  # noqa: BLE001 - fall back to the known code
            return ["STUDENT"]


def parse_usajobs(data: dict, fields: list[str]) -> list[Item]:
    items = []
    for result in (data.get("SearchResult") or {}).get("SearchResultItems") or []:
        job = result.get("MatchedObjectDescriptor") or {}
        title = job.get("PositionTitle") or ""
        if not title:
            continue
        display = job.get("PositionLocationDisplay") or ""
        locations = [loc.get("CityName") or loc.get("LocationName") for loc in job.get("PositionLocation") or []]
        locations = [loc for loc in locations if loc]
        details = (job.get("UserArea") or {}).get("Details") or {}
        close = to_date_str(job.get("ApplicationCloseDate"))
        org = job.get("OrganizationName") or job.get("DepartmentName") or "U.S. federal government"
        items.append(
            Item(
                source_item_id=str(result.get("MatchedObjectId") or job.get("PositionID") or job.get("PositionURI")),
                title=title,
                organization=org,
                url=job.get("PositionURI") or "",
                type="industry internship",
                fields=fields,
                subcategory=job.get("DepartmentName") or "",
                locations=locations,
                remote="remote" in display.lower() or "anywhere in the u.s." in display.lower(),
                posted_at=to_date_str(job.get("PublicationStartDate")),
                text=" ".join(filter(None, [details.get("JobSummary", ""), details.get("WhoMayApply", {}).get("Name", "")
                                            if isinstance(details.get("WhoMayApply"), dict) else ""]))[:3000],
                deadlines=[Deadline(kind="application", date=close, label="USAJobs closing date")] if close else [],
                extra={"notes": f"Federal job ({job.get('DepartmentName', '')}). Hiring path: {', '.join(details.get('HiringPath') or [])}"},
            )
        )
    return items
