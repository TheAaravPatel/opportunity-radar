"""Econ sources: PREDOC, RSS blogs, USAJobs, EconJobMarket, Fed boards, secrets handling."""

from datetime import date
from pathlib import Path

from conftest import make_http
from test_pipeline import make_cfg

from radar.pipeline import Preparer, run_group
from radar.sources.ats_common import is_student_title, make_item
from radar.sources.econjobmarket import parse_ejm
from radar.sources.predoc import parse_predoc
from radar.sources.rss_feed import parse_feed, parse_rss_items
from radar.sources.usajobs import parse_usajobs

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 24)


def test_predoc_listings(cfg):
    result = parse_predoc((FIXTURES / "predoc_ra.html").read_text(), {"url": "https://www.predoc.org/ra-opportunities"}, TODAY)
    by_title = {i.title: i for i in result.items}
    assert set(by_title) == {"Summer 2027 Economics Internship", "Part-Time Research Assistant", "Pre-doctoral Research Assistant"}

    fed = by_title["Summer 2027 Economics Internship"]
    assert fed.organization == "Federal Reserve Board" and fed.status == "closed"   # Oct 31 deadline passed
    assert fed.url == "https://bit.ly/4yWzLim"

    uchicago = by_title["Part-Time Research Assistant"]
    assert uchicago.type == "RA" and uchicago.deadlines[0].date == "2027-01-15"
    assert uchicago.organization.startswith("University of Chicago")

    bu = by_title["Pre-doctoral Research Assistant"]
    assert bu.deadlines[0].kind == "priority" and bu.deadlines[0].date == "2027-01-04"
    assert bu.status == "open"
    # Post-bacc only -> demoted for a freshman; undergrad-friendly one isn't.
    prep = Preparer(make_cfg(cfg, {}))
    assert prep.prepare(bu).eligibility.score < prep.prepare(uchicago).eligibility.score


def test_econ_blog_feed():
    posts = parse_feed((FIXTURES / "econugblog_feed.xml").read_text())
    options = {"name": "Econ UG Blog", "fields": ["econ"], "org_fallback": "Econ UG Blog post",
               "title_must_match": "intern|research assistant|fellow|program|challenge|competition",
               "title_must_not_match": "UC San Diego|UCSD"}
    result = parse_rss_items(posts, options, TODAY)
    titles = [i.title for i in result.items]
    assert not any("Spring into Recruiting" in t for t in titles)       # info session filtered out
    assert not any("Sales Challenge" in t for t in titles)              # UCSD campus-only event
    assert result.warnings == ["skipped 2 posts that don't look like opportunities"]
    nvidia = next(i for i in result.items if "NVIDIA" in i.title)
    assert nvidia.organization == "NVIDIA" and nvidia.locations == ["Santa Clara, CA"]
    assert nvidia.status == "closed"                                     # applied by Nov 23
    citadel = next(i for i in result.items if "Citadel" in i.title)
    assert citadel.type == "conference" and citadel.deadlines[0].date == "2027-01-14"
    assert citadel.organization == "Econ UG Blog post"                   # title names no employer
    crew = next(i for i in result.items if "CREW" in i.title)
    assert crew.deadlines[0].date == "2026-11-30" and crew.status is None
    assert crew.organization == "Bain & Company"


def test_blog_title_organizations():
    from radar.sources.rss_feed import _organization
    assert _organization("Postdoctoral Fellowship in Political Economy at Johns Hopkins University", "x") == "Johns Hopkins University"
    assert _organization("The University of Zurich Pre-Doctoral Program – Global Talent Common Application", "x") == "University of Zurich"
    assert _organization("Research Assistant – Summer 2027 – Federal Reserve Bank of Dallas", "x") == "Federal Reserve Bank of Dallas"
    assert _organization("Research Fellow in Climate Change and Public Health", "x") == "x"


def test_usajobs_parse():
    data = {"SearchResult": {"SearchResultItems": [{"MatchedObjectId": "8123", "MatchedObjectDescriptor": {
        "PositionTitle": "Economist (Student Trainee)", "PositionURI": "https://www.usajobs.gov/job/8123",
        "OrganizationName": "Bureau of Economic Analysis", "DepartmentName": "Department of Commerce",
        "PositionLocation": [{"CityName": "Suitland, Maryland"}], "PositionLocationDisplay": "Suitland, Maryland",
        "PublicationStartDate": "2026-11-20T00:00:00.0000", "ApplicationCloseDate": "2026-12-10T23:59:59.9970",
        "UserArea": {"Details": {"JobSummary": "Pathways internship for current students.", "HiringPath": ["STUDENT"]}}}}]}}
    [item] = parse_usajobs(data, ["econ"])
    assert item.organization == "Bureau of Economic Analysis" and item.deadlines[0].date == "2026-12-10"
    assert item.locations == ["Suitland, Maryland"] and not item.remote


def test_ejm_keeps_only_student_level():
    ads = [
        {"posid": 1, "adtitle": "Assistant Professor of Economics", "name": "Plaksha University", "position_type": "Assistant Professor"},
        {"posid": 2, "adtitle": "Pre-doctoral Fellow", "name": "Harvard University", "department": "Economics",
         "position_type": "Pre-doc", "enddate": "2027-01-15 23:59:00", "adtext": "<p>Two-year predoc</p>"},
    ]
    result = parse_ejm(ads)
    assert [i.source_item_id for i in result.items] == ["2"]
    assert result.items[0].url == "https://econjobmarket.org/positions/2"


def test_fed_board_names_and_ra_titles():
    fed = {"name": "Federal Reserve Banks (incl. SF Fed)", "ats": "workday", "tenant": "rb", "group": "econ",
           "fields": ["econ"], "extra_titles": ["research assistant"],
           "title_org_map": {"SF Fed": "Federal Reserve Bank of San Francisco"}}
    assert is_student_title("Research Assistant - Macro", fed)
    assert not is_student_title("Research Assistant - Macro")            # only with extra_titles
    intern = make_item(fed, "workday", "R1", "2027 SF Fed Summer Internship - ECONOMIC RESEARCH Intern", "u", ["San Francisco, CA"])
    assert intern.organization == "Federal Reserve Bank of San Francisco" and intern.type == "industry internship"
    ra = make_item(fed, "workday", "R2", "Research Assistant - Macro", "u", ["Chicago, IL"])
    assert ra.type == "RA" and ra.organization == fed["name"]


def test_fed_board_skips_and_maps_location():
    from radar.sources.ats_workday import fetch_company
    board = {"name": "Federal Reserve Board of Governors", "ats": "workday", "tenant": "bd", "host": "wd104",
             "site": "frb", "domain": "myworkdayjobsgov.com", "group": "econ", "fields": ["econ"],
             "location_map": {"Federal Reserve Board": "Washington, DC"},
             "skip_titles": ["photography", "graphic design"]}
    postings = [
        {"title": "Summer 2027 Intern – Advanced Research Computing (ARC) – R&S", "externalPath": "/job/Federal-Reserve-Board/ARC_R026020",
         "locationsText": "Federal Reserve Board", "postedOn": "Posted 3 Days Ago"},
        {"title": "Year-Round Intern - Digital Media Services, Photography", "externalPath": "/job/Federal-Reserve-Board/Photo_R1",
         "locationsText": "Federal Reserve Board"},
    ]
    http = make_http({"https://bd.wd104.myworkdayjobsgov.com/wday/cxs/bd/frb/jobs": (200, {"total": 2, "jobPostings": postings})})
    [item] = fetch_company(board, http)
    assert "Research Computing" in item.title and item.locations == ["Washington, DC"]
    # A student-only board (empty search term) still honours skip_titles.
    http = make_http({"https://bd.wd104.myworkdayjobsgov.com/wday/cxs/bd/frb/jobs": (200, {"total": 2, "jobPostings": postings})})
    assert len(fetch_company({**board, "search": [""]}, http)) == 1


def test_graduate_intern_is_demoted(cfg):
    prep = Preparer(make_cfg(cfg, {}))
    from radar.models import Item
    grad = Item(source_item_id="g", title="Year-Round Graduate Intern, Public Affairs", organization="Fed", url="u", type="industry internship")
    plain = Item(source_item_id="p", title="Year-Round Intern, Public Affairs", organization="Fed", url="u", type="industry internship")
    assert prep.prepare(grad).eligibility.score < prep.prepare(plain).eligibility.score


def test_verify_reports_missing_secret(cfg, monkeypatch):
    from radar.verify import verify_sources
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    sources = {"usajobs": {"type": "usajobs", "requires_env": ["USAJOBS_API_KEY"]}}
    lines = verify_sources(make_cfg(cfg, sources), make_http({}))
    assert "needs secret: USAJOBS_API_KEY" in lines[-1]


def test_missing_secret_skips_without_failing(conn, cfg, monkeypatch):
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    sources = {"usajobs": {"type": "usajobs", "group": "daily", "requires_env": ["USAJOBS_API_KEY", "USAJOBS_EMAIL"]}}
    [report] = run_group(conn, make_cfg(cfg, sources), make_http({}))
    assert report.ok and "waiting for GitHub Secret" in report.warnings[0]
    row = conn.execute("SELECT consecutive_failures FROM sources WHERE id='usajobs'").fetchone()
    assert row["consecutive_failures"] == 0
