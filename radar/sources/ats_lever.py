"""Lever public postings API (no key needed).

GET https://api.lever.co/v0/postings/<slug>?mode=json
  [{"id": "f1c5...", "text": "Data Science Intern", "hostedUrl": "https://jobs.lever.co/<slug>/f1c5...",
    "categories": {"location": "Menlo Park, CA", "allLocations": ["Menlo Park, CA"],
                   "commitment": "Intern", "team": "R&D"},
    "workplaceType": "onsite", "createdAt": 1795478400000,
    "descriptionPlain": "...", "lists": [{"text": "Requirements", "content": "<li>...</li>"}],
    "additionalPlain": "..."}]
A posting counts as student-level if its title says so OR Lever's
`commitment` field says Intern.
"""

from __future__ import annotations

from radar.http import Http
from radar.sources.ats_common import html_to_text, is_student_title, make_item, split_locations
from radar.timeutil import unix_to_date


def _description(posting: dict) -> str:
    parts = [posting.get("descriptionPlain") or "", posting.get("additionalPlain") or ""]
    for block in posting.get("lists") or []:
        if isinstance(block, dict):
            parts.append(f"{block.get('text') or ''} {html_to_text(block.get('content'), 1500)}")
    return " ".join(p for p in parts if p)[:4000]


def fetch_company(company: dict, http: Http) -> list:
    postings = http.get_json(f"https://api.lever.co/v0/postings/{company['slug']}?mode=json")
    items = []
    for posting in postings or []:
        title = posting.get("text") or ""
        categories = posting.get("categories") or {}
        intern_commitment = "intern" in (categories.get("commitment") or "").lower()
        if not (is_student_title(title, company) or intern_commitment):
            continue
        created = posting.get("createdAt")
        items.append(
            make_item(
                company, "lever", str(posting.get("id")), title, posting.get("hostedUrl") or posting.get("applyUrl") or "",
                split_locations(categories.get("allLocations") or categories.get("location")),
                posted_at=unix_to_date(created / 1000) if isinstance(created, (int, float)) else None,
                text=_description(posting),
                remote=(posting.get("workplaceType") or "").lower() == "remote",
            )
        )
    return items
