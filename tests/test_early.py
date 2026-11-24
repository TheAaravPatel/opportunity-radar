"""Phase 5: early-career programs - detection, matching postings to the
registry, bank/trading boards (incl. Oracle), and program-page clues."""

import copy

import httpx
import yaml

from conftest import make_http
from test_pipeline import row

from radar.config import Config, load_config
from radar.dashboard.build import collect_data
from radar.digest import build_digest
from radar.early_detect import EarlyCareerDetector
from radar.early_match import ProgramMatcher
from radar.http import Http
from radar.pipeline import run_group
from radar.sources.ats_oracle import fetch_company as oracle_fetch
from radar.sources.companies import AtsCompaniesSource
from radar.watcher.signals import page_signal, years_seen
from datetime import date

TODAY = date(2026, 11, 24)


def test_weak_words_need_a_program_word_right_after():
    detector = EarlyCareerDetector(load_config().keywords)
    assert detector.is_early("Insight Day 2027") and detector.is_early("Discovery Summer Program")
    assert detector.is_early("Freshman Software Engineering Intern")
    # Biotech titles that fooled the old rule ("discovery" ... "program" anywhere):
    assert not detector.is_early("2027 Summer Internship Program: AI/ML for Drug Discovery Intern")
    assert not detector.is_early("2027 Future Talent Program - Discovery Neuroscience - Co-op")
    assert not detector.is_early("First Year Analyst - Investment Banking")     # a new-grad level


def test_program_matcher():
    matcher = ProgramMatcher(load_config().load_file("early_programs.yaml")["programs"])
    assert matcher.match("Morgan Stanley", "2027 Sophomore Summer Analyst Program") == "morgan-stanley-sophomore-analyst"
    assert matcher.match("Morgan Stanley", "Early Insights Program 2027") == "morgan-stanley-early-insights"
    assert matcher.match("Goldman Sachs", "2027 Possibilities Summit") == "goldman-possibilities-series"
    assert matcher.match("JPMorgan Chase & Co.", "JPMorganChase Fellowship - Sophomore") == "jpmorganchase-fellowship"
    assert matcher.match("Citigroup", "Citi Freshman Discovery Program") == "citi-freshman-discovery"
    assert matcher.match("Citadel Securities", "Discover Citadel Securities") == "discover-citadel-securities"
    assert matcher.match("Goldman Sachs", "Summer Analyst") is None          # which of its 3 programs? unknown
    assert matcher.match("Acme", "Insight Day") is None                      # firm not in the registry


# ------------------------------------------------------------------ job boards
ORACLE_API = "https://bank.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"


def test_oracle_fetcher_pages_and_filters():
    def requisition(i, title):
        return {"Id": str(i), "Title": title, "PostedDate": "2026-11-01", "PrimaryLocation": "New York, NY, United States",
                "secondaryLocations": [{"Name": "Chicago, IL, United States"}], "ShortDescriptionStr": "For sophomores."}

    pages = [
        {"items": [{"TotalJobsCount": 3, "Offset": 0, "Limit": 2,
                    "requisitionList": [requisition(1, "2027 Sophomore Summer Analyst"), requisition(2, "Origination Assistant")]}]},
        {"items": [{"TotalJobsCount": 3, "Offset": 2, "Limit": 2, "requisitionList": [requisition(3, "Winter Insight Day - Students")]}]},
    ]
    calls = []

    def handler(request):
        assert str(request.url).startswith(ORACLE_API)
        calls.append(request.url.params["finder"])
        offset = int(request.url.params["finder"].rsplit("offset=", 1)[1])
        return httpx.Response(200, json=pages[0] if offset == 0 else pages[1])

    company = {"name": "Big Bank", "ats": "oracle", "host": "bank.fa.oraclecloud.com", "site": "CX_1",
               "group": "early-career", "fields": ["econ"], "search": ["sophomore"], "extra_titles": ["insight"]}
    http = Http("test", transport=httpx.MockTransport(handler), sleep=lambda s: None, min_delay=0)
    items = oracle_fetch(company, http)
    assert [i.title for i in items] == ["2027 Sophomore Summer Analyst", "Winter Insight Day - Students"]
    assert items[0].url == "https://bank.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1"
    assert items[0].locations == ["New York, NY, United States", "Chicago, IL, United States"]
    assert calls == ["findReqs;siteNumber=CX_1,keyword=sophomore,sortBy=POSTING_DATES_DESC,offset=0",
                     "findReqs;siteNumber=CX_1,keyword=sophomore,sortBy=POSTING_DATES_DESC,offset=2"]


def test_board_filter_needs_real_evidence(cfg):
    # A firm with ONE registry program: its ordinary internships must not pass
    # the early-career filter just because the firm is in the registry.
    matcher = ProgramMatcher([{"id": "bank-fellowship", "firm": "Big Bank", "program": "Big Bank Fellowship"}])
    title = "2027 Market Sales Analyst Internship - Summer Experience Program"
    assert matcher.match("Big Bank", title) == "bank-fellowship"            # fine for linking flagged postings
    assert matcher.match("Big Bank", title, strict=True) is None            # not for filtering a whole board
    assert matcher.match("Big Bank", "Big Bank Fellowship 2027", strict=True) == "bank-fellowship"


def test_early_career_only_boards(cfg):
    board = "https://boards-api.greenhouse.io/v1/boards/fastfirm/jobs?content=true"
    jobs = {"jobs": [
        {"id": 1, "title": "Software Engineer Intern", "absolute_url": "https://x/1", "location": {"name": "New York, NY"}},
        {"id": 2, "title": "First Year Trading and Technology Program", "absolute_url": "https://x/2", "location": {"name": "New York, NY"}},
        {"id": 3, "title": "Insight Day 2027", "absolute_url": "https://x/3", "location": {"name": "New York, NY"}},
    ]}
    cfg = Config(settings=cfg.settings, sources={}, keywords=cfg.keywords, config_dir=cfg.config_dir,
                 companies=[{"name": "Fast Firm", "ats": "greenhouse", "slug": "fastfirm", "group": "early-career",
                             "fields": ["cs"], "early_career_only": True}])
    source = AtsCompaniesSource("early", {"groups": ["early-career"]}, cfg, make_http({board: (200, jobs)}))
    titles = [i.title for i in source.fetch().items]
    assert titles == ["First Year Trading and Technology Program", "Insight Day 2027"]


# ----------------------------------------------------------------- page clues
def test_page_open_text_does_not_revive_discontinued_programs(conn, cfg, tmp_path, monkeypatch):
    cfg = early_cfg(cfg, tmp_path)
    programs = yaml.safe_load((tmp_path / "early_programs.yaml").read_text())
    programs["programs"][1]["status_hint"] = "possibly discontinued"        # acme-insight
    (tmp_path / "early_programs.yaml").write_text(yaml.safe_dump(programs))
    routes = {FEED: (200, []), PAGES["ms"]: (200, page("Info.")),
              PAGES["acme"]: (200, page("Applications are now open for Insight Day 2027.")), PAGES["old"]: (200, page("Info."))}
    run_group(conn, cfg, make_http(routes), group="daily")
    assert card(conn, "acme-insight")["status"] == "possibly discontinued"
    item = next(i for i in collect_data(conn, cfg)["items"] if i["registry_id"] == "acme-insight")
    assert item["page"]["signal"] == "open"            # still shown as a chip, to re-check by hand


def test_page_signals():
    assert page_signal(["Applications are now open for the 2027 program!"], TODAY)[0] == "open"
    assert page_signal(["Applications for 2025 are closed."], TODAY) == (None, "")     # an old cycle
    assert page_signal(["Applications have closed. Check back in the fall."], TODAY)[0] == "closed"
    assert years_seen(["Summer 2026 recap", "Apply for 2027"], TODAY) == [2026, 2027]


def test_empty_main_falls_back_to_the_page():
    from radar.watcher.text import page_lines
    html = "<body><main></main><div class='content'><p>Evercore Sophomore Program</p><p>Apply by January 5, 2027</p></div></body>"
    assert page_lines(html) == ["Evercore Sophomore Program", "Apply by January 5, 2027"]


# ------------------------------------------------------- the whole pipeline
FEED = "https://feeds.test/early.json"
PAGES = {"ms": "https://ms.example.com/sophomore", "acme": "https://acme.example.com/insight",
         "old": "https://old.example.com/explore", "beta": "https://beta.example.com/fellowship"}


def early_cfg(base_cfg, tmp_path):
    programs = {"programs": [
        {"id": "ms-sophomore", "firm": "Morgan Stanley", "program": "Sophomore Summer Analyst Program",
         "fields": ["econ"], "class_years": ["sophomore"], "url": PAGES["ms"], "typical_open_month": 9,
         "deadlines": [{"kind": "application", "date": "2026-10-01"}]},
        {"id": "acme-insight", "firm": "Acme Bank", "program": "Insight Day", "fields": ["econ"],
         "url": PAGES["acme"], "typical_open_month": 3},
        {"id": "old-explore", "firm": "Old Bank", "program": "Explore Series", "fields": ["econ"],
         "url": PAGES["old"], "typical_open_month": 6},
        {"id": "beta-fellowship", "firm": "Beta Capital", "program": "Beta Fellowship", "fields": ["econ"],
         "url": PAGES["beta"], "typical_open_month": 4, "watch": False},
    ]}
    (tmp_path / "early_programs.yaml").write_text(yaml.safe_dump(programs))
    (tmp_path / "watched_pages.yaml").write_text(yaml.safe_dump({"pages": [], "job_pages": []}))
    sources = {
        "early_programs": {"type": "early_programs_registry", "group": ["jobs", "daily"], "file": "early_programs.yaml"},
        "feed": {"type": "listings_json", "group": ["jobs", "daily"], "url": FEED, "default_fields": ["econ"]},
        "page_watcher": {"type": "page_watcher", "group": "daily", "file": "watched_pages.yaml",
                         "programs_file": "early_programs.yaml"},
    }
    return Config(settings=base_cfg.settings, sources=sources, keywords=base_cfg.keywords, companies=[], config_dir=tmp_path)


def page(text):
    return f"<main><h1>Program</h1><p>{text}</p><p>{'About the program for first- and second-year students. ' * 4}</p></main>"


def card(conn, registry_id):
    return conn.execute("SELECT * FROM opportunities WHERE registry_id = ?", (registry_id,)).fetchone()


def test_early_programs_end_to_end(conn, cfg, tmp_path, monkeypatch):
    cfg = early_cfg(cfg, tmp_path)
    listings = [row(1, "Software Intern", company="Zeta Corp"), row(2, "Zeta Insight Day 2027", company="Zeta Corp")]
    routes = {FEED: (200, listings), PAGES["ms"]: (200, page("Information about our programs.")),
              PAGES["acme"]: (200, page("Applications are now open for Insight Day 2027.")),
              PAGES["old"]: (404, "not found")}

    # Day 1: nothing for Morgan Stanley yet; Acme's page says it's open; Old Bank's page is gone.
    run_group(conn, cfg, make_http(copy.deepcopy(routes)), group="daily")
    assert card(conn, "ms-sophomore")["status"] == "closed"
    acme = card(conn, "acme-insight")
    assert acme["status"] == "open" and "says applications are open" in acme["status_reason"]
    assert conn.execute("SELECT gone_since FROM pages WHERE id='old-explore'").fetchone()[0]
    assert card(conn, "old-explore")["status"] != "possibly discontinued"       # only gone for a day
    [suggestion] = conn.execute("SELECT * FROM suggestions WHERE status='pending'").fetchall()
    assert suggestion["name"] == "Zeta Corp: Zeta Insight Day 2027" and "Zeta Insight Day" in suggestion["payload"]
    # First run = baseline: nothing counts as "now open" yet.
    assert build_digest(conn, cfg).early_now_open == []

    # Day 9: both programs are posted on the feed; Old Bank still 404s.
    #  * Morgan Stanley's title differs from the program name -> a separate card, matched to it
    #  * Beta's is the program name itself -> dedupe merges it into the registry card
    monkeypatch.setenv("RADAR_NOW", "2026-12-03T18:00:00Z")
    listings.append(row(3, "2027 Sophomore Summer Analyst Program - Wealth Management", company="Morgan Stanley",
                        location="New York, NY"))
    listings.append(row(4, "Beta Fellowship", company="Beta Capital", location="Chicago, IL"))
    routes[FEED] = (200, listings)
    run_group(conn, cfg, make_http(copy.deepcopy(routes)), group="daily")
    ms = card(conn, "ms-sophomore")
    assert (ms["status"], ms["status_reason"]) == ("open", "Posting is live: 2027 Sophomore Summer Analyst Program - Wealth Management")
    beta = card(conn, "beta-fellowship")
    assert (beta["status"], beta["status_reason"]) == ("open", "Posting is live (listed by feed)")
    old = card(conn, "old-explore")
    assert old["status"] == "possibly discontinued" and "gone (HTTP 404)" in old["status_reason"]

    data = collect_data(conn, cfg)
    by_id = {i["registry_id"] or i["title"]: i for i in data["items"]}
    posting = "2027 Sophomore Summer Analyst Program - Wealth Management"
    assert [p["title"] for p in by_id["ms-sophomore"]["postings"]] == [posting]
    assert by_id[posting]["program"] == "Morgan Stanley: Sophomore Summer Analyst Program"
    assert by_id["beta-fellowship"]["postings"] == [{"title": "Beta Fellowship", "url": "https://jobs.test/4", "source": "feed"}]
    assert by_id["acme-insight"]["page"]["signal"] == "open"
    assert [s["name"] for s in data["suggestions"]] == ["Zeta Corp: Zeta Insight Day 2027"]
    digest = build_digest(conn, cfg)
    assert sorted(x["registry_id"] for x in digest.early_now_open) == ["beta-fellowship", "ms-sophomore"]

    # The Zeta posting closes -> its suggestion expires.
    routes[FEED] = (200, [r for r in listings if "Zeta Insight" not in r["title"]])
    run_group(conn, cfg, make_http(copy.deepcopy(routes)), group="daily")
    assert conn.execute("SELECT status FROM suggestions").fetchone()[0] == "expired"
