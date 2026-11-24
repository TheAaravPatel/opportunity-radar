"""Find which public job board (Greenhouse / Lever / Ashby) a company uses.

For every `ats: unknown` entry in config/companies.yaml, try a few likely board
names ("Denali Therapeutics" -> denalitherapeutics, denali-therapeutics,
denali, ...) on each ATS's public API and report the ones that exist. Runs as
part of `python -m radar verify`; you then copy a hit into companies.yaml.
(Workday boards can't be guessed - they need tenant + site from the careers page.)
"""

from __future__ import annotations

from radar.http import Http
from radar.normalize import simplify_text

# Words companies often leave out of their board names.
DROP_WORDS = {
    "inc", "the", "co", "company", "corporation", "therapeutics", "biosciences", "bio", "biotechnology",
    "biotech", "laboratories", "labs", "life", "sciences", "health", "pharmaceuticals", "pharma", "group",
}

PROBES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}


def slug_candidates(name: str) -> list[str]:
    words = simplify_text(name).split()
    core = [w for w in words if w not in DROP_WORDS] or words
    options = ["".join(words), "-".join(words), "".join(words[:2]), "".join(core), "-".join(core), "".join(core) + "bio"]
    # Very short guesses ("rad" from "Bio-Rad") match unrelated companies.
    return list(dict.fromkeys(o for o in options if len(o) >= 4))


def count_jobs(data) -> int:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        return len(data.get("jobs") or [])
    return 0


def discover(name: str, http: Http) -> list[tuple[str, str, int]]:
    """[(ats, slug, number of jobs)] for every board that exists."""
    hits = []
    for slug in slug_candidates(name):
        for ats, url in PROBES.items():
            try:
                data = http.get_json(url.format(slug=slug))
            except Exception:  # noqa: BLE001 - a 404 just means "not this one"
                continue
            hits.append((ats, slug, count_jobs(data)))
    return hits


def discovery_report(cfg, http: Http) -> list[str]:
    unknown = [c for c in cfg.companies if c.get("ats") == "unknown"]
    if not unknown:
        return []
    lines = ["", "## Job-board discovery for `ats: unknown` companies", "",
             "| Company | Boards found (ats:slug, jobs) |", "|---|---|"]
    for company in unknown:
        hits = discover(company["name"], http)
        found = ", ".join(f"{ats}:{slug} ({n} jobs)" for ats, slug, n in hits)
        lines.append(f"| {company['name']} | {found or 'none on Greenhouse/Lever/Ashby - check the careers page (Workday? iCIMS?)'} |")
    return lines
