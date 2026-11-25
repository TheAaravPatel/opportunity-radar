"""Phase 7: weekly discovery (with a fake Claude) and the approval flow."""

import json
import shutil
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from radar.approve import DecisionError, decide, insert_entry, parse_title
from radar.config import ROOT, load_config
from radar.discovery import Discovery, payload_for, url_key
from radar.dashboard.build import collect_data

TODAY = date(2026, 11, 29)   # a Sunday


def block(kind, **kw):
    return SimpleNamespace(type=kind, **kw)


def message(stop_reason, *content):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content), stop_details=None)


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.msg


class FakeClaude:
    """Stands in for anthropic.Anthropic(): scripted research turns, then one extraction."""

    def __init__(self, research_turns, suggestions):
        self.research_turns = list(research_turns)
        self.suggestions = suggestions
        self.stream_calls, self.create_calls = [], []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream, create=self._create))

    def _stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        return FakeStream(self.research_turns.pop(0))

    def _create(self, **kwargs):
        self.create_calls.append(kwargs)
        return message("end_turn", block("text", text=json.dumps({"suggestions": self.suggestions})))


def finding(**overrides):
    base = {"kind": "early_program", "name": "Sophomore Discovery Day", "organization": "Acme Capital",
            "url": "https://acme.example.com/students/discovery", "why": "Open to sophomores; Chicago; travel paid.",
            "fields": ["econ"], "class_years": ["sophomore"], "type": "early-career program", "location": "Chicago, IL",
            "typical_open_month": 1, "deadlines": [{"kind": "application", "date": "2027-02-01", "label": "Apply by"}],
            "start_date": "", "end_date": "", "travel_covered": "full", "feed_url": "", "ats": "none", "ats_slug": ""}
    return {**base, **overrides}


def api_cfg():
    """The API version of discovery is off in settings.yaml (no key); switch it on for these tests."""
    cfg = load_config()
    cfg.settings["discovery"]["enabled"] = True
    return cfg


@pytest.fixture
def discovery(conn, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return Discovery(conn, api_cfg())


def test_research_resumes_a_paused_turn_within_the_search_budget(discovery):
    search = lambda: block("server_tool_use", name="web_search", input={})   # noqa: E731
    discovery._client = FakeClaude(
        [message("pause_turn", search(), search(), block("text", text="Found A.")),
         message("end_turn", search(), block("text", text="Found B."))], [])
    write_up, searches = discovery.research("early_program", "programs", ["Known: Program"], TODAY)
    assert (write_up, searches) == ("Found A.\nFound B.", 3)
    first, second = discovery._client.stream_calls
    assert first["tools"][0]["type"] == "web_search_20260209" and first["tools"][0]["max_uses"] == 15
    assert second["tools"] == first["tools"]                           # never edited between turns
    assert second["messages"][1]["role"] == "assistant"                # the paused turn is sent back
    assert "- Known: Program" in first["messages"][0]["content"]
    assert first["fallbacks"] == "default"


def test_no_resume_once_the_search_budget_is_used(discovery):
    many = [block("server_tool_use", name="web_search", input={}) for _ in range(15)]
    discovery._client = FakeClaude([message("pause_turn", *many, block("text", text="Partial."))], [])
    assert discovery.research("early_program", "programs", [], TODAY) == ("Partial.", 15)
    assert len(discovery._client.stream_calls) == 1


def test_discovery_adds_only_new_allowed_finds(discovery, conn):
    known_url = load_config().load_file("early_programs.yaml")["programs"][0]["url"]
    finds = [
        finding(),
        finding(name="Old one", url=known_url),                              # already in the registry
        finding(name="Some job", url="https://www.linkedin.com/jobs/view/1"),  # never LinkedIn
        finding(name="No link", url="http://insecure.example.com"),
        finding(kind="other", name="Interesting list", organization="", url="https://example.org/list"),
    ]
    discovery._client = FakeClaude([message("end_turn", block("text", text="write-up"))], finds)
    summary = discovery.run(today=TODAY)
    assert summary["added"] == ["Sophomore Discovery Day", "Interesting list"]
    assert len(summary["skipped"]) == 3
    rows = {r["name"]: r for r in conn.execute("SELECT * FROM suggestions")}
    acme = rows["Acme Capital: Sophomore Discovery Day"]
    payload = json.loads(acme["payload"])
    assert acme["source"] == "discovery" and acme["target_config"] == "config/early_programs.yaml"
    assert payload["id"] == "acme-capital-sophomore-discovery-day" and payload["travel_covered"] == "full"
    assert rows["Interesting list"]["payload"] is None                     # needs code, no draft entry

    # Next week the same find (and a rejected one) is not suggested again.
    conn.execute("UPDATE suggestions SET status = 'rejected' WHERE name = 'Interesting list'")
    discovery._client = FakeClaude([message("end_turn", block("text", text="again"))],
                                   [finding(), finding(kind="other", name="Interesting list", organization="", url="https://example.org/list/")])
    assert discovery.run(today=TODAY)["added"] == []


def test_discovery_is_off_without_a_key(conn, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert Discovery(conn, api_cfg()).run(today=TODAY) == {"not_run": "the ANTHROPIC_API_KEY secret is not set"}
    assert Discovery(conn, load_config()).run(today=TODAY) == {"not_run": "discovery.enabled is false in settings.yaml"}


def test_a_rejected_key_is_explained_not_crashed(conn, monkeypatch):
    import anthropic
    import httpx

    monkeypatch.setenv("ANTHROPIC_API_KEY", "  sk-ant-api03-abc\n")
    discovery = Discovery(conn, api_cfg())
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def fail(**kwargs):
        raise anthropic.AuthenticationError("Invalid bearer token", response=httpx.Response(401, request=request), body=None)

    discovery._client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=fail, create=fail)))
    summary = discovery.run(today=TODAY)
    assert "rejected the API key (401)" in summary["error"]
    assert "regular API key" in summary["error"] and "spaces/newlines" in summary["error"]
    assert "abc" not in summary["error"]                                    # never the key itself
    assert "error" in json.loads(conn.execute("SELECT value FROM meta WHERE key='last_discovery'").fetchone()[0])


def test_payload_shapes():
    feed = payload_for(finding(kind="rss_feed", name="Econ Jobs Blog", url="https://blog.example.org",
                               feed_url="https://blog.example.org/feed/"), TODAY)
    assert feed == {"econ_jobs_blog": {"type": "rss_feed", "group": "daily", "name": "Econ Jobs Blog",
                                       "homepage": "https://blog.example.org", "url": "https://blog.example.org/feed/",
                                       "fields": ["econ"], "max_age_days": 60, "keep_missing_days": 60}}
    company = payload_for(finding(kind="company", organization="Helix Bio", ats="greenhouse", ats_slug="helixbio",
                                  fields=["biotech"]), TODAY)
    assert company["ats"] == "greenhouse" and company["slug"] == "helixbio" and company["group"] == "biotech"
    assert payload_for(finding(kind="company", ats="none"), TODAY) is None
    assert url_key("https://www.Example.com/a/b/?x=1") == "example.com/a/b"


# ------------------------------------------------------------------ approving
@pytest.fixture
def repo(tmp_path):
    shutil.copytree(ROOT / "config", tmp_path / "config")
    return tmp_path


def add_suggestion(conn, kind, name, target, payload, url="https://x.example.com/p"):
    cur = conn.execute(
        """INSERT INTO suggestions (kind, name, url, why, target_config, payload, source, found_at, status)
           VALUES (?, ?, ?, 'why', ?, ?, 'discovery', '2026-11-29T17:30:00Z', 'pending')""",
        (kind, name, url, target, json.dumps(payload) if payload else None))
    return cur.lastrowid


def test_parse_title():
    assert parse_title("Approve suggestion #42") == ("approve", 42)
    assert parse_title("reject suggestion 7 please") == ("reject", 7)
    with pytest.raises(DecisionError):
        parse_title("Approve everything; rm -rf /")


def test_approve_adds_entry_to_the_right_list_and_keeps_comments(conn, repo):
    path = repo / "config/watched_pages.yaml"
    before = path.read_text()
    page = {"id": "new-research-program", "name": "New Research Program", "org": "Stanford Econ",
            "url": "https://econ.stanford.edu/new", "type": "research program", "fields": ["econ"], "render": "auto"}
    sid = add_suggestion(conn, "stanford_page", "New Research Program", "config/watched_pages.yaml", page)
    message = decide(conn, "approve", sid, root=repo)
    assert "Added `new-research-program`" in message
    data = yaml.safe_load(path.read_text())
    assert data["pages"][-1]["id"] == "new-research-program"                # in pages, NOT job_pages
    assert all(p["id"] != "new-research-program" for p in data["job_pages"])
    after = path.read_text()
    assert [l for l in before.splitlines() if l.lstrip().startswith("#")] == \
           [l for l in after.splitlines() if l.lstrip().startswith("#") and "from suggestion" not in l]
    assert conn.execute("SELECT status FROM suggestions WHERE id = ?", (sid,)).fetchone()[0] == "approved"
    # Deciding again changes nothing.
    assert "already approved" in decide(conn, "approve", sid, root=repo)

    # The edited config still passes the same checks CI runs.
    cfg = load_config(repo / "config")
    assert any(p["id"] == "new-research-program" for p in cfg.load_file("watched_pages.yaml")["pages"])


def test_approve_early_program_gets_a_unique_id_and_feed_goes_to_sources(conn, repo):
    taken = load_config().load_file("early_programs.yaml")["programs"][0]["id"]
    sid = add_suggestion(conn, "early_program", "Dup", "config/early_programs.yaml",
                         {"id": taken, "firm": "Someone", "program": "Thing", "url": "https://a.example.com", "fields": ["cs"]})
    decide(conn, "approve", sid, root=repo)
    programs = yaml.safe_load((repo / "config/early_programs.yaml").read_text())["programs"]
    assert programs[-1]["id"] == f"{taken}-2" and "approved from suggestion" in programs[-1]["last_verified"]

    sid = add_suggestion(conn, "rss_feed", "Blog", "config/sources.yaml",
                         {"econ_blog": {"type": "rss_feed", "group": "daily", "url": "https://b.example.org/feed/"}},
                         url="https://b.example.org")
    decide(conn, "approve", sid, root=repo)
    sources = yaml.safe_load((repo / "config/sources.yaml").read_text())["sources"]
    assert sources["econ_blog"]["type"] == "rss_feed"


def test_bad_entries_are_refused_and_files_untouched(conn, repo):
    path = repo / "config/conferences.yaml"
    before = path.read_text()
    sid = add_suggestion(conn, "conference", "Bad", "config/conferences.yaml",
                         {"id": "Bad Id!", "name": "Bad", "url": "https://c.example.com"})
    with pytest.raises(DecisionError):
        decide(conn, "approve", sid, root=repo)
    assert path.read_text() == before
    assert conn.execute("SELECT status FROM suggestions WHERE id = ?", (sid,)).fetchone()[0] == "pending"


def test_reject_and_code_only_suggestions(conn, repo):
    sid = add_suggestion(conn, "other", "A GitHub list", None, None)
    assert "needs code" in decide(conn, "approve", sid, root=repo)
    sid = add_suggestion(conn, "early_program", "Meh", "config/early_programs.yaml", {"id": "meh"}, url="https://m.example.com")
    assert "Rejected" in decide(conn, "reject", sid, root=repo)
    assert conn.execute("SELECT status FROM suggestions WHERE id = ?", (sid,)).fetchone()[0] == "rejected"


def test_insert_entry_at_end_of_file_block(tmp_path):
    path = tmp_path / "x.yaml"
    path.write_text("# header\nitems:\n  - id: a   # first\n")
    insert_entry(path, "items", {"id": "b", "fields": ["cs"]}, "added")
    assert path.read_text() == "# header\nitems:\n  - id: a   # first\n\n  # added\n  - id: b\n    fields: [cs]\n"


def test_dashboard_has_what_the_buttons_need(conn):
    add_suggestion(conn, "early_program", "Acme: Day", "config/early_programs.yaml", {"id": "acme-day"})
    data = collect_data(conn, load_config())
    assert data["repo"] == "TheAaravPatel/opportunity-radar"
    assert data["suggestions"][0]["payload"] == {"id": "acme-day"}


# ----------------------------------------------- Claude Code task (no API key)
def test_brief_has_what_the_task_needs():
    from radar.discovery import brief

    text = brief(load_config(), TODAY)
    assert "This week's focus" in text and "at most 15 web searches" in text
    assert "import-findings.yml" in text and '"travel_covered"' in text
    assert "Never suggest LinkedIn" in text


def test_imported_findings_are_cleaned_and_checked(conn, tmp_path):
    from radar.cli import main

    findings = {"suggestions": [
        finding(),                                                     # good
        {**finding(name="Weird", url="https://w.example.com"), "kind": "early_program", "fields": ["econ", "astrology"],
         "deadlines": [{"kind": "application", "date": "soon"}], "typical_open_month": 99, "extra": "ignored"},
        finding(name="Job ad", url="https://www.indeed.com/viewjob?jk=1"),   # never Indeed
        {"kind": "nonsense", "name": "x", "url": "https://x.example.com"},   # unknown kind
        "not even an object",
    ]}
    path = tmp_path / "findings.json"
    path.write_text(json.dumps(findings))
    db_path = tmp_path / "radar.db"
    assert main(["--db", str(db_path), "import-findings", str(path), "--focus", "early_program", "--searches", "11"]) == 0

    from radar import db
    conn2 = db.connect(db_path)
    rows = {r["name"]: r for r in conn2.execute("SELECT * FROM suggestions")}
    assert set(rows) == {"Acme Capital: Sophomore Discovery Day", "Acme Capital: Weird"}
    weird = json.loads(rows["Acme Capital: Weird"]["payload"])
    assert weird["fields"] == ["econ"] and "deadlines" not in weird and "typical_open_month" not in weird
    last = json.loads(conn2.execute("SELECT value FROM meta WHERE key='last_discovery'").fetchone()[0])
    assert last["via"] == "Claude Code task" and last["searches"] == 11 and len(last["skipped"]) == 1


def test_pushed_inbox_file_carries_focus_and_searches(tmp_path):
    from radar import db
    from radar.cli import main

    path = tmp_path / "2026-11-29.json"
    path.write_text(json.dumps({"focus": "conference", "searches": 9, "suggestions": [finding(kind="conference")]}))
    assert main(["--db", str(tmp_path / "radar.db"), "import-findings", str(path)]) == 0
    last = json.loads(db.connect(tmp_path / "radar.db").execute("SELECT value FROM meta WHERE key='last_discovery'").fetchone()[0])
    assert (last["focus"], last["searches"], len(last["added"])) == ("conference", 9, 1)


def test_bad_json_is_refused(tmp_path):
    from radar.cli import main

    path = tmp_path / "findings.json"
    path.write_text("{not json")
    assert main(["--db", str(tmp_path / "radar.db"), "import-findings", str(path)]) == 1
