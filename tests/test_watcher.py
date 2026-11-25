"""The page-watcher: text extraction, diffs, deadline finding, and the full pipeline."""

from datetime import date

import yaml

from conftest import make_http

from radar.config import Config
from radar.dashboard.build import collect_data
from radar.pipeline import run_group
from radar.watcher.deadlines import find_deadlines, worth_asking_claude
from radar.watcher.fetch import PageFetcher, RobotsDisallowed
from radar.watcher.snapshot import line_diff
from radar.watcher.text import page_lines

TODAY = date(2026, 11, 24)


# ---------------------------------------------------------------- text + diff
def test_page_lines_keeps_content_drops_chrome():
    html = """<html><body><nav>Menu Home About</nav><div id="onetrust-banner">We use cookies</div>
      <main><h1>BSURP</h1><p>Applications are <strong>due</strong> February 1, 2027.</p>
      <ul><li>Stipend: $7,000</li><li>Housing provided</li></ul><script>var x = 1;</script></main>
      <footer>Stanford University</footer></body></html>"""
    assert page_lines(html) == ["BSURP", "Applications are due February 1, 2027.", "Stipend: $7,000", "Housing provided"]
    assert page_lines(html, "li") == ["Stipend: $7,000", "Housing provided"]


def test_line_diff_filters_and_ignores_moves():
    old = ["Analyst (2027)", "Summer Analyst Intern - Boston", "Manager"]
    new = ["Summer Analyst Intern - Boston", "Analyst (2027)", "Summer Analyst Intern - SF", "Director"]
    assert line_diff(old, new) == ["- Manager", "+ Summer Analyst Intern - SF", "+ Director"]
    assert line_diff(old, new, only="intern") == ["+ Summer Analyst Intern - SF"]
    assert line_diff(["Updated Nov 1"], ["Updated Nov 2"], ignore="^updated") == []


# ------------------------------------------------------------------ deadlines
def test_find_deadlines():
    lines = [
        "Applications are due February 1, 2027 at 11:59 pm.",
        "Priority deadline: Jan. 4th, 2027; final deadline March 1, 2027",
        "The portal opens December 14, 2026.",
        "Applications due January 27 for summer 2027",       # no year, but the page says 2027
        "Posted on November 3, 2026",                       # not a deadline
        "The program runs June 22 - August 28, 2027.",      # program dates, not deadlines
        "Last updated: Nov 1, 2026. Deadline Jan 15, 2027",
        "January 12, 2027 deadline for Bio-X",
        "Deadline: February 10, 2025",                      # an old cycle - ignored
    ]
    found = {(d.kind, d.date, d.approx) for d in find_deadlines(lines, TODAY)}
    assert found == {
        ("application", "2027-02-01", False), ("priority", "2027-01-04", False), ("application", "2027-03-01", False),
        ("opens", "2026-12-14", False), ("application", "2027-01-27", True), ("application", "2027-01-15", False),
        ("application", "2027-01-12", False),
    }
    # Without any 2027 on the page, a year-less date is too risky to guess.
    assert find_deadlines(["Applications due January 27"], TODAY) == []
    # A window: opens on the first date, due on the second (year from the end).
    window = find_deadlines(["Student applications: January 27 - February 10, 2027"], TODAY)
    assert [(d.kind, d.date, d.approx) for d in window] == [("opens", "2027-01-27", False), ("application", "2027-02-10", False)]
    # A keyword doesn't reach across a sentence: these are program dates.
    assert find_deadlines(["Deadline: rolling. The program runs June 22 - August 28, 2027."], TODAY) == []


def test_deadline_labels_and_non_application_dates():
    lines = [
        "Faculty Mentor Letter Deadline: March 8, 2027 (Mon) @ 11:59 PM (PST)",
        "Application will open : Wednesday, December 16, 2026, at 12 am PST",
        "Priority deadline: Jan. 4th, 2027; final deadline March 1, 2027",
        "Apply by February 2, 2027",
        # Real lines from Stanford pages that are NOT application dates:
        "Deadline for candidates to accept their offer : March 13- no extensions granted (2027 cycle)",
        "Submit a final report and donor thank-you letter: due September 20 (2027 recipients)",
    ]
    labels = {d.date: d.label for d in find_deadlines(lines, TODAY)}
    assert labels == {
        "2027-03-08": "Faculty Mentor Letter Deadline (from page)",
        "2026-12-16": "Application will open (from page)",
        "2027-01-04": "Priority deadline (from page)",
        "2027-03-01": "Final deadline (from page)",
        "2027-02-02": "Deadline (from page)",
    }


def test_when_to_ask_claude():
    assert worth_asking_claude(["The deadline is the second Friday of winter quarter."], [])
    assert not worth_asking_claude(["Deadline: TBA"], [])
    assert not worth_asking_claude(["Nothing about dates here"], [])


# -------------------------------------------------------------------- fetching
def test_robots_txt_is_respected():
    http = make_http({"https://example.edu/robots.txt": (200, "User-agent: *\nDisallow: /private/\n"),
                      "https://example.edu/ok": (200, "<main>" + "x" * 400 + "</main>")})
    with PageFetcher(http) as fetcher:
        html, status, method = fetcher.fetch("https://example.edu/ok", "http")
        assert status == 200 and method == "http"
        try:
            fetcher.fetch("https://example.edu/private/page", "http")
            raise AssertionError("should have refused")
        except RobotsDisallowed:
            pass


def test_empty_page_falls_back_to_the_browser(monkeypatch):
    http = make_http({"https://app.example.edu/": (200, "<html><body><div id=root></div></body></html>")})
    calls = []

    def fake_browser(self, url):
        calls.append(url)
        return "<main>" + "Rendered by JavaScript. " * 20 + "</main>", 200, "browser"

    monkeypatch.setattr(PageFetcher, "_browser_fetch", fake_browser)
    with PageFetcher(http) as fetcher:
        html, _, method = fetcher.fetch("https://app.example.edu/", "auto")
    assert method == "browser" and "Rendered" in html and calls == ["https://app.example.edu/"]


# ------------------------------------------------------------- full pipeline
STANFORD_URL = "https://biology.example.edu/bsurp"
JOBS_URL = "https://jobs.example.com/search"


def page(body: str) -> str:
    return f"<html><body><nav>Home</nav><main><h1>Biology Summer Research</h1><p>{body}</p>" \
           f"<p>{'Ten weeks of paid lab research for Stanford undergraduates. ' * 3}</p></main></body></html>"


def jobs(*titles: str) -> str:
    return "<ul>" + "".join(f"<li class='job'><h3>{t}</h3></li>" for t in titles) + "</ul>"


def watcher_cfg(base_cfg, tmp_path):
    registry = {
        "pages": [{
            "id": "bsurp-test", "name": "Biology summer research", "org": "Stanford Biology", "url": STANFORD_URL,
            "type": "research program", "fields": ["biotech"], "typical_open_month": 1,
            "deadlines": [{"kind": "application", "date": "2026-02-10", "label": "2026 deadline"}],
        }],
        "job_pages": [{"id": "acme-jobs", "name": "Acme Economics jobs", "org": "Acme", "fields": ["econ"],
                       "url": JOBS_URL, "selector": "li.job h3", "only_lines_matching": "intern|summer"}],
    }
    (tmp_path / "watched.yaml").write_text(yaml.safe_dump(registry))
    sources = {
        "stanford_pages": {"type": "watched_pages_registry", "group": ["jobs", "daily"], "file": "watched.yaml"},
        "page_watcher": {"type": "page_watcher", "group": "daily", "file": "watched.yaml"},
    }
    return Config(settings=base_cfg.settings, sources=sources, keywords=base_cfg.keywords,
                  companies=[], config_dir=tmp_path)


def card(conn):
    return conn.execute("SELECT * FROM opportunities WHERE registry_id = 'bsurp-test'").fetchone()


def active_deadlines(conn):
    return {(d["kind"], d["date"], d["method"]) for d in conn.execute(
        "SELECT * FROM deadlines WHERE active = 1 AND opportunity_id = ?", (card(conn)["id"],))}


def test_watcher_end_to_end(conn, cfg, tmp_path, monkeypatch):
    cfg = watcher_cfg(cfg, tmp_path)
    routes = {STANFORD_URL: (200, page("Applications are due February 1, 2027.")),
              JOBS_URL: (200, jobs("Analyst (2027 start)"))}

    # Day 1: first snapshot (no "changed" alert), deadline read off the page.
    run_group(conn, cfg, make_http(routes), group="daily")
    row = card(conn)
    assert conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1     # merged into one card
    assert ("application", "2027-02-01", "regex") in active_deadlines(conn)
    assert (row["status"], row["status_reason"]) == ("open", "Next deadline Feb 1, 2027")
    assert conn.execute("SELECT last_changed_at FROM pages WHERE id='bsurp-test'").fetchone()[0] is None

    # The 6-hourly run re-reads only the YAML (which has just last year's date);
    # the found deadline still decides the status.
    run_group(conn, cfg, make_http(routes), group="jobs")
    assert card(conn)["status"] == "open"

    # Day 2: the page moved the deadline, and the job page got an internship.
    routes[STANFORD_URL] = (200, page("Applications are due February 5, 2027."))
    routes[JOBS_URL] = (200, jobs("Analyst (2027 start)", "Summer Analyst Intern", "Senior Manager"))
    run_group(conn, cfg, make_http(routes), group="daily")
    deadlines = active_deadlines(conn)
    assert ("application", "2027-02-05", "regex") in deadlines and ("application", "2027-02-01", "regex") not in deadlines
    data = collect_data(conn, cfg)
    changes = {c["id"]: c for c in data["page_changes"]}
    assert set(changes) == {"bsurp-test", "acme-jobs"}
    assert changes["acme-jobs"]["diff"] == "+ Summer Analyst Intern"          # "Senior Manager" isn't a match
    assert changes["acme-jobs"]["kind"] == "job_page" and changes["acme-jobs"]["fields"] == ["econ"]
    assert {p["id"] for p in data["watched_pages"]} == {"bsurp-test", "acme-jobs"}

    # Day 3: the Stanford page is down. Its deadlines are kept; the failure is shown.
    routes[STANFORD_URL] = (500, "oops")
    run_group(conn, cfg, make_http(routes), group="daily")
    assert ("application", "2027-02-05", "regex") in active_deadlines(conn)
    page_row = conn.execute("SELECT * FROM pages WHERE id='bsurp-test'").fetchone()
    assert page_row["consecutive_failures"] == 1 and "500" in page_row["last_error"]


def test_claude_reads_deadlines_the_rules_cant(conn, cfg, tmp_path, monkeypatch):
    from radar.llm import Claude

    cfg = watcher_cfg(cfg, tmp_path)
    cfg.settings["llm"]["enabled"] = True          # off in settings.yaml (no key); on for this test
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    asked = []

    def fake_ask(self, system, prompt, schema):
        asked.append(prompt)
        return {"deadlines": [{"kind": "application", "date": "2027-01-15", "year_stated": True,
                               "evidence": "Applications are due the Friday of week 2 of winter quarter (Jan 15, 2027 this year)"}]}

    monkeypatch.setattr(Claude, "_ask_json", fake_ask)
    routes = {STANFORD_URL: (200, page("The deadline is the Friday of week 2 of winter quarter.")),
              JOBS_URL: (200, jobs())}
    run_group(conn, cfg, make_http(routes), group="daily")
    assert ("application", "2027-01-15", "llm") in active_deadlines(conn)
    assert "week 2 of winter quarter" in asked[0] and "<page_text>" in asked[0]

    # Same page text tomorrow: the cached answer is reused, no new request.
    run_group(conn, cfg, make_http(routes), group="daily")
    assert len(asked) == 1 and ("application", "2027-01-15", "llm") in active_deadlines(conn)


def test_claude_is_off_without_a_key(conn, cfg, tmp_path, monkeypatch):
    from radar.llm import Claude

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(Claude, "_ask_json", lambda *a: (_ for _ in ()).throw(AssertionError("must not be called")))
    cfg = watcher_cfg(cfg, tmp_path)
    routes = {STANFORD_URL: (200, page("The deadline is the Friday of week 2 of winter quarter.")), JOBS_URL: (200, jobs())}
    [_, report] = run_group(conn, cfg, make_http(routes), group="daily")
    assert report.ok
