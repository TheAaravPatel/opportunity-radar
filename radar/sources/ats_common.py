"""Helpers shared by the job-board (ATS) fetchers: Greenhouse, Lever, Ashby, Workday.

ATS = "applicant tracking system": the software companies use to post jobs.
Most have a free public JSON endpoint behind their career pages, which is far
more reliable (and polite) than scraping the pages themselves.

Each fetcher module exposes `fetch_company(company, http) -> list[Item]`, where
`company` is one entry from config/companies.yaml.
"""

from __future__ import annotations

import html
import re
from datetime import date, timedelta

from bs4 import BeautifulSoup

from radar.models import Item
from radar.normalize import is_remote

# Titles we keep: internships and other student roles. Whole words only, so
# "Internal Audit Manager" and "International Sales" are NOT matched.
STUDENT_TITLE = re.compile(
    r"\b(?:interns?|internships?|co-?ops?|summer\s+(?:analyst|associate|student|scholar|program|research)"
    r"|students?|undergrad(?:uate)?s?|fellows?|fellowships?|apprentices?|apprenticeships?|trainees?|scholars?)\b",
    re.I,
)
# ...but never these (they match "fellow"/"student" yet aren't for undergrads).
NOT_STUDENT_TITLE = re.compile(r"\b(?:post-?doc\w*|post-?doctoral|medical\s+student|graduate\s+fellow|phd\s+fellow)\b", re.I)

# "Summer 2027", "2027 Summer", "Fall 2026" -> terms
_SEASON_FIRST = re.compile(r"\b(summer|fall|autumn|winter|spring)\s*'?(20\d\d)\b", re.I)
_YEAR_FIRST = re.compile(r"\b(20\d\d)\s+(summer|fall|autumn|winter|spring)\b", re.I)
_DAYS_AGO = re.compile(r"(\d+)\+?\s*days?\s+ago", re.I)


def is_skipped_title(title: str, company: dict | None = None) -> bool:
    """`skip_titles` in companies.yaml drops roles outside your fields at
    employers that hire for everything (the Fed Board's photography intern)."""
    skip = (company or {}).get("skip_titles") or []
    return any(phrase.lower() in (title or "").lower() for phrase in skip)


def is_student_title(title: str, company: dict | None = None) -> bool:
    """Internship-type title? A company can also accept e.g. "research assistant"
    roles via `extra_titles` in companies.yaml (think tanks, the Fed)."""
    title = title or ""
    if NOT_STUDENT_TITLE.search(title) or is_skipped_title(title, company):
        return False
    if STUDENT_TITLE.search(title):
        return True
    extra = (company or {}).get("extra_titles") or []
    return any(phrase.lower() in title.lower() for phrase in extra)


_RA_TITLE = re.compile(r"research assistant|pre-?doc|research analyst|research associate", re.I)


def organization_for(company: dict, title: str) -> str:
    """Shared boards host many employers (all 12 Federal Reserve Banks post on
    one Workday board); `title_org_map` names the real employer from the title."""
    for needle, org in (company.get("title_org_map") or {}).items():
        if needle.lower() in (title or "").lower():
            return org
    return company["name"]


def terms_from_title(title: str) -> list[str]:
    found = []
    for season, year in _SEASON_FIRST.findall(title or ""):
        found.append(f"{season.capitalize().replace('Autumn', 'Fall')} {year}")
    for year, season in _YEAR_FIRST.findall(title or ""):
        found.append(f"{season.capitalize().replace('Autumn', 'Fall')} {year}")
    return list(dict.fromkeys(found))   # de-duplicate, keep order


def html_to_text(raw: str | None, limit: int = 4000) -> str:
    """Job descriptions arrive as HTML (Greenhouse even double-escapes it)."""
    if not raw:
        return ""
    text = BeautifulSoup(html.unescape(raw), "html.parser").get_text(" ", strip=True)
    return text[:limit]


def relative_posted(text: str | None, today: date) -> str | None:
    """Workday says 'Posted Today' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago'."""
    if not text:
        return None
    low = text.lower()
    if "today" in low:
        return today.isoformat()
    if "yesterday" in low:
        return (today - timedelta(days=1)).isoformat()
    if "30+" in low:
        return None   # too vague to be a date
    match = _DAYS_AGO.search(low)
    return (today - timedelta(days=int(match.group(1)))).isoformat() if match else None


def split_locations(*values) -> list[str]:
    """Flatten location strings/lists; "A; B" and "A | B" become ["A", "B"]."""
    out: list[str] = []
    for value in values:
        if not value:
            continue
        parts = value if isinstance(value, list) else re.split(r"\s*[;|]\s*", str(value))
        for part in parts:
            part = str(part).strip()
            if part and part not in out and part != "—":
                out.append(part)
    return out


def make_item(company: dict, ats: str, job_id: str, title: str, url: str, locations: list[str],
              posted_at: str | None = None, text: str = "", remote: bool = False) -> Item:
    """Build the Item every ATS fetcher returns, filled in from the company entry."""
    key = company.get("slug") or company.get("tenant") or company.get("host") or company["name"]
    is_ra = company.get("group") == "econ" and _RA_TITLE.search(title or "") and not STUDENT_TITLE.search(title or "")
    # Some boards use a building name as the location ("Federal Reserve Board");
    # `location_map` in companies.yaml turns it into a real city.
    location_map = company.get("location_map") or {}
    locations = [location_map.get(place, place) for place in locations]
    return Item(
        source_item_id=f"{ats}:{key}:{job_id}",
        title=title.strip(),
        organization=organization_for(company, title),
        url=url,
        type="RA" if is_ra else "industry internship",
        fields=list(company.get("fields") or []),
        locations=locations,
        remote=remote or is_remote(locations),
        posted_at=posted_at,
        terms=terms_from_title(title),
        text=text,
        scope=company["name"],          # lets one failing board not close another's jobs
        extra={"ats": ats, "company_group": company.get("group", "")},
    )
