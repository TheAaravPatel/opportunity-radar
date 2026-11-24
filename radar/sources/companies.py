"""Source type `ats_companies`: poll the companies in config/companies.yaml.

sources.yaml picks which companies via `groups` (biotech / econ / early-career):

  biotech_companies:
    type: ats_companies
    groups: [biotech]
    # Skip companies another source already polls (checked every run):
    skip_if_covered_by: https://raw.githubusercontent.com/.../companies.json

Each company is its own "scope": if one company's board fails, only that
company is marked failed, and its existing jobs are NOT closed. The source as
a whole only fails if every company failed.

`early_career_only: true` (banks, trading firms) keeps just the postings for
first- and second-years: titles with early-career words ("Sophomore",
"Insight Day"...) or ones that match a program in early_programs.yaml.
The firms' regular internships already come from SimplifyJobs.
"""

from __future__ import annotations

import logging

from radar.early_detect import EarlyCareerDetector
from radar.early_match import ProgramMatcher
from radar.models import FetchResult
from radar.normalize import normalize_org
from radar.sources import ats_ashby, ats_greenhouse, ats_lever, ats_oracle, ats_workday
from radar.sources.base import Source, register

log = logging.getLogger(__name__)

FETCHERS = {
    "greenhouse": ats_greenhouse.fetch_company,
    "lever": ats_lever.fetch_company,
    "ashby": ats_ashby.fetch_company,
    "workday": ats_workday.fetch_company,
    "oracle": ats_oracle.fetch_company,
}


def board_key(company: dict) -> tuple:
    """(ats, slug-or-tenant) - two entries with the same key poll the same board."""
    return (company.get("ats"), (company.get("slug") or company.get("tenant") or "").lower())


def covered_by(company: dict, other_companies: list[dict]) -> bool:
    names = {normalize_org(c.get("name", "")) for c in other_companies}
    boards = {board_key(c) for c in other_companies}
    return normalize_org(company["name"]) in names or board_key(company) in boards


@register("ats_companies")
class AtsCompaniesSource(Source):
    def fetch(self) -> FetchResult:
        groups = set(self.options.get("groups") or [])
        others = self._covered_list()
        items, failed, warnings = [], set(), []
        attempted = 0

        for company in self.cfg.companies:
            if groups and company.get("group") not in groups:
                continue
            if not company.get("enabled", True) or company.get("ats") not in FETCHERS:
                continue
            if others and covered_by(company, others):
                warnings.append(f"skipped {company['name']}: already covered by {self.options['skip_if_covered_by']}")
                continue
            attempted += 1
            try:
                if company.get("early_career_only"):
                    found = self._early_only(company)
                else:
                    found = FETCHERS[company["ats"]](company, self.http)
                items.extend(found)
                log.info("%s (%s): %d student postings", company["name"], company["ats"], len(found))
            except Exception as exc:  # noqa: BLE001 - one board must not stop the rest
                failed.add(company["name"])
                warnings.append(f"{company['name']} ({company['ats']}) failed: {type(exc).__name__}: {exc}"[:240])

        if attempted and len(failed) == attempted:
            raise RuntimeError(f"all {attempted} company boards failed; first: {warnings[-1] if warnings else '?'}")
        return FetchResult(items=items, failed_scopes=failed, warnings=warnings)

    def _early_only(self, company: dict) -> list:
        """Fetch with a generous title filter, then keep only early-career postings."""
        detector = EarlyCareerDetector(self.cfg.keywords)
        matcher = ProgramMatcher((self.cfg.load_file("early_programs.yaml") or {}).get("programs") or [])
        early_words = self.cfg.keywords.get("early_career", {}) or {}
        # Let titles like "Freshman Discovery Program" (no "intern") through the
        # board's student-title filter; the exact check happens below.
        relaxed = {**company, "extra_titles": [*(company.get("extra_titles") or []),
                                               *(early_words.get("strong") or []), *(early_words.get("weak") or [])]}
        found = FETCHERS[company["ats"]](relaxed, self.http)
        return [i for i in found if detector.is_early(i.title) or matcher.match(i.organization, i.title, i.class_years)]

    def _covered_list(self) -> list[dict]:
        url = self.options.get("skip_if_covered_by")
        if not url:
            return []
        try:
            data = self.http.get_json(url)
        except Exception as exc:  # noqa: BLE001 - not fatal: just poll everything
            log.warning("could not load %s (%s); polling all companies", url, exc)
            return []
        return data.get("companies", []) if isinstance(data, dict) else data or []
