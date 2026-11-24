"""Job-board (ATS) fetchers, the companies source, and board discovery."""

import json
from datetime import date
from pathlib import Path

import httpx
from conftest import make_http

from radar.ats_discovery import discover, slug_candidates
from radar.config import Config
from radar.sources import ats_ashby, ats_greenhouse, ats_lever, ats_workday
from radar.sources.ats_common import is_student_title, relative_posted, terms_from_title
from radar.sources.companies import AtsCompaniesSource, covered_by

FIXTURES = Path(__file__).parent / "fixtures"


def test_student_titles():
    assert is_student_title("Research Analyst Intern - Summer 2027")
    assert is_student_title("Co-op Mechanical Engineering R&D")
    assert is_student_title("Undergraduate Research Fellow")
    assert not is_student_title("Internal Audit Manager")
    assert not is_student_title("International Sales Director")
    assert not is_student_title("Postdoctoral Fellow, Immunology")


def test_terms_and_dates():
    assert terms_from_title("2027 Summer Internship Program: AI/ML") == ["Summer 2027"]
    assert terms_from_title("Intern (Fall 2026) / Spring 2027") == ["Fall 2026", "Spring 2027"]
    today = date(2026, 11, 24)
    assert relative_posted("Posted Today", today) == "2026-11-24"
    assert relative_posted("Posted 3 Days Ago", today) == "2026-11-21"
    assert relative_posted("Posted 30+ Days Ago", today) is None


GH = "https://boards-api.greenhouse.io/v1/boards/biohub/jobs?content=true"
GH_BODY = {"jobs": [
    {"id": 1, "title": "Summer 2027 Computational Biology Intern", "absolute_url": "https://job-boards.greenhouse.io/biohub/jobs/1",
     "location": {"name": "San Francisco, CA; Stanford, CA"}, "first_published": "2026-11-01T09:00:00-08:00",
     "content": "&lt;p&gt;Open to &lt;b&gt;rising sophomores&lt;/b&gt; and juniors.&lt;/p&gt;"},
    {"id": 2, "title": "Senior Scientist", "absolute_url": "https://x/2", "location": {"name": "Chicago, IL"}},
], "meta": {"total": 2}}
BIOHUB = {"name": "CZ Biohub", "ats": "greenhouse", "slug": "biohub", "group": "biotech", "fields": ["biotech"]}


def test_greenhouse():
    items = ats_greenhouse.fetch_company(BIOHUB, make_http({GH: (200, GH_BODY)}))
    assert len(items) == 1                                   # the senior role is filtered out
    item = items[0]
    assert item.locations == ["San Francisco, CA", "Stanford, CA"]
    assert item.terms == ["Summer 2027"] and item.posted_at == "2026-11-01"
    assert "rising sophomores" in item.text and "<b>" not in item.text
    assert item.scope == "CZ Biohub" and item.fields == ["biotech"]


def test_lever_uses_commitment():
    url = "https://api.lever.co/v0/postings/grailbio?mode=json"
    body = [
        {"id": "a", "text": "Bioinformatics", "hostedUrl": "https://jobs.lever.co/grailbio/a",
         "categories": {"location": "Menlo Park, CA", "commitment": "Intern"}, "createdAt": 1795478400000,
         "descriptionPlain": "Summer role", "lists": [{"text": "You have", "content": "<li>Python</li>"}]},
        {"id": "b", "text": "Director, Finance", "hostedUrl": "https://x/b", "categories": {"commitment": "Full-time"}},
    ]
    items = ats_lever.fetch_company({"name": "GRAIL", "ats": "lever", "slug": "grailbio", "fields": ["biotech"]}, make_http({url: (200, body)}))
    assert [i.title for i in items] == ["Bioinformatics"]
    assert items[0].posted_at == "2026-11-24" and "Python" in items[0].text


def test_ashby():
    url = "https://api.ashbyhq.com/posting-api/job-board/genesis-molecular-ai?includeCompensation=true"
    body = {"jobs": [
        {"id": "j1", "title": "Machine Learning Intern", "location": "South San Francisco, CA",
         "secondaryLocations": [{"location": "Remote"}], "employmentType": "Intern", "isListed": True,
         "publishedAt": "2026-10-02T17:00:00.000+00:00", "jobUrl": "https://jobs.ashbyhq.com/g/j1", "descriptionPlain": "ML"},
        {"id": "j2", "title": "Hidden Intern", "isListed": False, "jobUrl": "https://x/j2"},
    ]}
    company = {"name": "Genesis Molecular AI", "ats": "ashby", "slug": "genesis-molecular-ai", "fields": ["biotech", "cs"]}
    items = ats_ashby.fetch_company(company, make_http({url: (200, body)}))
    assert len(items) == 1 and items[0].remote and items[0].locations == ["South San Francisco, CA", "Remote"]


def test_workday_paginates_and_filters():
    api = "https://rb.wd5.myworkdayjobs.com/wday/cxs/rb/FRS/jobs"
    seen_bodies = []

    def handler(request):
        body = json.loads(request.content)
        seen_bodies.append(body)
        offset = body["offset"]
        postings = [{"title": f"2027 Summer Intern - Research {offset + i}", "externalPath": f"/job/SF/Intern_R-{offset + i}",
                     "locationsText": "San Francisco, CA", "postedOn": "Posted 2 Days Ago"} for i in range(20)]
        if offset == 20:
            postings = postings[:5] + [{"title": "Economist", "externalPath": "/job/x/Econ_R-999", "locationsText": "3 Locations"}]
        return httpx.Response(200, json={"total": 26 if offset == 0 else 0, "jobPostings": postings})

    company = {"name": "Federal Reserve Banks", "ats": "workday", "tenant": "rb", "host": "wd5", "site": "FRS",
               "fields": ["econ"], "search": ["intern"]}
    items = ats_workday.fetch_company(company, make_http({api: handler}))
    assert len(seen_bodies) == 2                     # stopped after the short second page
    assert len(items) == 25                          # "Economist" isn't a student role
    assert items[0].url == "https://rb.wd5.myworkdayjobs.com/FRS/job/SF/Intern_R-0"
    assert items[0].posted_at == "2026-11-22"


def test_workday_government_cloud_domain():
    api = "https://bd.wd104.myworkdayjobsgov.com/wday/cxs/bd/frb/jobs"
    body = {"total": 1, "jobPostings": [{"title": "Summer 2027 Intern - Research", "externalPath": "/job/DC/Intern_R1"}]}
    company = {"name": "Fed Board", "ats": "workday", "tenant": "bd", "host": "wd104", "site": "frb",
               "domain": "myworkdayjobsgov.com", "fields": ["econ"]}
    items = ats_workday.fetch_company(company, make_http({api: (200, body)}))
    assert items[0].url.startswith("https://bd.wd104.myworkdayjobsgov.com/frb/job/")


def companies_cfg(base, companies):
    return Config(settings=base.settings, sources={}, keywords=base.keywords, companies=companies, config_dir=base.config_dir)


def test_one_failing_company_does_not_stop_the_others(cfg):
    companies = [BIOHUB, {"name": "Broken Bio", "ats": "greenhouse", "slug": "broken", "group": "biotech", "fields": ["biotech"]}]
    http = make_http({GH: (200, GH_BODY)})          # "broken" returns 404
    result = AtsCompaniesSource("bc", {"groups": ["biotech"]}, companies_cfg(cfg, companies), http).fetch()
    assert len(result.items) == 1
    assert result.failed_scopes == {"Broken Bio"}
    assert any("Broken Bio" in w for w in result.warnings)


def test_all_companies_failing_fails_the_source(cfg):
    companies = [{"name": "Broken Bio", "ats": "greenhouse", "slug": "broken", "group": "biotech"}]
    source = AtsCompaniesSource("bc", {"groups": ["biotech"]}, companies_cfg(cfg, companies), make_http({}))
    try:
        source.fetch()
    except RuntimeError as exc:
        assert "all 1 company boards failed" in str(exc)
    else:
        raise AssertionError("expected the source to fail")


def test_skips_companies_the_biotech_repo_covers(cfg):
    covered_url = "https://raw.test/companies.json"
    theirs = {"companies": [{"name": "CZ Biohub", "ats": "greenhouse", "slug": "biohub"}]}
    source = AtsCompaniesSource("bc", {"groups": ["biotech"], "skip_if_covered_by": covered_url},
                                companies_cfg(cfg, [BIOHUB]), make_http({covered_url: (200, theirs), GH: (200, GH_BODY)}))
    result = source.fetch()
    assert result.items == [] and "already covered" in result.warnings[0]


def test_our_biotech_list_does_not_duplicate_the_biotech_repo(cfg):
    """companies.yaml must only hold biotech companies the GitHub repo doesn't poll."""
    theirs = json.loads((FIXTURES / "biotech_repo_companies.json").read_text())["companies"]
    ours = [c for c in cfg.companies if c.get("group") == "biotech"]
    duplicates = [c["name"] for c in ours if covered_by(c, theirs)]
    assert duplicates == [], f"already in the biotech repo: {duplicates}"


def test_discovery():
    assert "denali" in slug_candidates("Denali Therapeutics")
    assert "biorad" in slug_candidates("Bio-Rad Laboratories")
    jobs = {"jobs": [{"id": 1, "title": "Research Associate", "location": {"name": "South San Francisco, CA"}}, {"id": 2, "title": "Intern"}]}
    routes = {"https://boards-api.greenhouse.io/v1/boards/denali/jobs": (200, jobs)}
    assert discover("Denali Therapeutics", make_http(routes)) == [
        ("greenhouse", "denali", 2, ["Research Associate (South San Francisco, CA)", "Intern"])
    ]
