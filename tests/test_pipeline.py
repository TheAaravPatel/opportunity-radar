"""End-to-end pipeline behaviour with fake (in-memory) feeds."""

import copy

import pytest
from conftest import make_http

from radar.config import Config
from radar.pipeline import run_group

A = "https://feeds.test/a.json"
B = "https://feeds.test/b.json"


def row(i, title=None, company="Acme", location="Palo Alto, CA", term="Summer 2027"):
    return {
        "id": f"id-{i}", "company_name": company, "title": title or f"Software Intern {i}",
        "category": "Software", "active": True, "is_visible": True, "terms": [term],
        "locations": [location], "date_posted": 1795478400, "date_updated": 1795488129,
        "url": f"https://jobs.test/{i}", "degrees": ["Bachelor's"],
    }


def make_cfg(base_cfg, sources):
    return Config(settings=base_cfg.settings, sources=sources, keywords=base_cfg.keywords,
                  companies=[], config_dir=base_cfg.config_dir)


def listing_source(url):
    return {"type": "listings_json", "group": "jobs", "url": url, "default_fields": ["cs"]}


@pytest.fixture
def feeds():
    return {A: [row(i) for i in range(30)], B: []}


def run(conn, cfg, feeds, sources):
    routes = {url: (200, copy.deepcopy(body)) for url, body in feeds.items() if isinstance(body, list)}
    routes.update({url: body for url, body in feeds.items() if not isinstance(body, list)})
    return {r.source_id: r for r in run_group(conn, make_cfg(cfg, sources), make_http(routes))}


def status_of(conn, url):
    return conn.execute(
        "SELECT o.status, o.closed_at FROM opportunities o JOIN sightings s ON s.opportunity_id = o.id WHERE s.url = ?", (url,)
    ).fetchone()


def test_second_identical_run_changes_nothing(conn, cfg, feeds):
    sources = {"a": listing_source(A)}
    first = run(conn, cfg, feeds, sources)["a"]
    assert (first.seen, first.new, first.closed) == (30, 30, 0)
    second = run(conn, cfg, feeds, sources)["a"]
    assert (second.seen, second.new, second.closed) == (30, 0, 0)


def test_missing_item_is_closed_then_reopened(conn, cfg, feeds):
    sources = {"a": listing_source(A)}
    run(conn, cfg, feeds, sources)
    gone = feeds[A].pop(0)
    report = run(conn, cfg, feeds, sources)["a"]
    assert report.closed == 1
    assert status_of(conn, gone["url"])["status"] == "closed"

    feeds[A].append(gone)
    run(conn, cfg, feeds, sources)
    back = status_of(conn, gone["url"])
    assert back["status"] == "open" and back["closed_at"] is None


def test_suspicious_drop_closes_nothing(conn, cfg, feeds):
    sources = {"a": listing_source(A)}
    run(conn, cfg, feeds, sources)
    feeds[A] = feeds[A][:5]                     # 30 -> 5 items: upstream looks broken
    report = run(conn, cfg, feeds, sources)["a"]
    assert report.closed == 0
    assert any("looks broken" in w for w in report.warnings)


def test_failing_source_is_isolated(conn, cfg, feeds):
    sources = {"broken": listing_source(B), "a": listing_source(A)}
    feeds[B] = (500, "server error")
    reports = run(conn, cfg, feeds, sources)
    assert not reports["broken"].ok and reports["a"].ok
    health = conn.execute("SELECT consecutive_failures, last_error FROM sources WHERE id='broken'").fetchone()
    assert health["consecutive_failures"] == 1 and "500" in health["last_error"]


def test_failed_fetch_never_closes_items(conn, cfg, feeds):
    sources = {"a": listing_source(A)}
    run(conn, cfg, feeds, sources)
    feeds[A] = (503, "down")
    run(conn, cfg, feeds, sources)
    assert conn.execute("SELECT COUNT(*) FROM opportunities WHERE status='closed'").fetchone()[0] == 0


def test_same_job_from_two_sources_is_merged(conn, cfg, feeds):
    feeds[B] = [row("b1", title="Software Intern 3 (Summer 2027)", company="Acme, Inc.", location="Palo Alto, California")]
    sources = {"a": listing_source(A), "b": listing_source(B)}
    run(conn, cfg, feeds, sources)
    assert conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 30
    merged = conn.execute(
        "SELECT o.id, o.primary_source, COUNT(s.id) n FROM opportunities o JOIN sightings s ON s.opportunity_id=o.id GROUP BY o.id HAVING n > 1"
    ).fetchall()
    assert len(merged) == 1 and merged[0]["primary_source"] == "a"

    # Source A drops it, but B still lists it -> stays open.
    feeds[A] = [r for r in feeds[A] if r["id"] != "id-3"]
    run(conn, cfg, feeds, sources)
    status = conn.execute("SELECT status FROM opportunities WHERE id = ?", (merged[0]["id"],)).fetchone()["status"]
    assert status == "open"


def test_registry_deadline_removed_from_yaml(conn, cfg, tmp_path):
    (tmp_path / "early_programs.yaml").write_text(
        "programs:\n  - id: p1\n    firm: F\n    program: P\n    url: https://p.test\n"
        "    deadlines:\n      - {kind: application, date: 2027-01-31, label: Due}\n"
    )
    test_cfg = Config(settings=cfg.settings, sources={"reg": {"type": "early_programs_registry", "group": "daily"}},
                      keywords=cfg.keywords, config_dir=tmp_path)
    run_group(conn, test_cfg, make_http({}))
    assert conn.execute("SELECT COUNT(*) FROM deadlines WHERE active=1").fetchone()[0] == 1

    (tmp_path / "early_programs.yaml").write_text("programs:\n  - id: p1\n    firm: F\n    program: P\n    url: https://p.test\n")
    run_group(conn, test_cfg, make_http({}))
    assert conn.execute("SELECT COUNT(*) FROM deadlines WHERE active=1").fetchone()[0] == 0


def test_duplicate_closed_item_stays_closed(conn, cfg, feeds):
    """A source listing the same past event twice must not re-open it."""
    past = row("p1", title="Old Event")
    feeds[A] = [past, {**past, "id": "id-p2"}] + feeds[A]
    sources = {"a": listing_source(A)}
    run(conn, cfg, feeds, sources)
    conn.execute("UPDATE opportunities SET status='closed' WHERE title='Old Event'")
    from radar import store
    from radar.models import Item
    from radar.pipeline import Preparer
    prep = Preparer(make_cfg(cfg, sources))
    closed_item = Item(source_item_id="id-p2", title="Old Event", organization="Acme",
                       locations=["Palo Alto, CA"], status="closed")
    store.save_items(conn, "a", [prep.prepare(closed_item)], run_id=999, now="2026-11-24T18:00:00Z", deadline_method="structured")
    assert conn.execute("SELECT status FROM opportunities WHERE title='Old Event'").fetchone()["status"] == "closed"


def test_conference_with_unknown_year_matches_dated_one(conn, cfg):
    from radar import store
    from radar.models import Item
    from radar.pipeline import Preparer
    prep = Preparer(make_cfg(cfg, {}))
    dated = Item(source_item_id="r1", title="TreeHacks", organization="TreeHacks", dedupe_key="conf|treehacks|2027")
    undated = Item(source_item_id="f1", title="TreeHacks", organization="TreeHacks", dedupe_key="conf|treehacks|tba")
    store.save_items(conn, "registry", [prep.prepare(dated)], run_id=1, now="2026-11-24T18:00:00Z", deadline_method="manual")
    store.save_items(conn, "feed", [prep.prepare(undated)], run_id=2, now="2026-11-24T18:00:00Z", deadline_method="structured")
    assert conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1


def test_registry_aka_names_merge_feed_items(conn, cfg):
    from radar import store
    from radar.models import Item
    from radar.pipeline import Preparer
    prep = Preparer(make_cfg(cfg, {}))
    registry = Item(source_item_id="wids", title="WiDS Worldwide Stanford Conference", organization="WiDS",
                    dedupe_key="conf|wids worldwide conference|tba",
                    extra={"alias_keys": ["conf|women in data science worldwide"]})
    feed = Item(source_item_id="x", title="Women in Data Science (WiDS) Worldwide", organization="WiDS",
                dedupe_key="conf|women in data science worldwide|2027")
    store.save_items(conn, "registry", [prep.prepare(registry)], run_id=1, now="2026-11-24T18:00:00Z", deadline_method="manual")
    store.save_items(conn, "badgeup", [prep.prepare(feed)], run_id=2, now="2026-11-24T18:00:00Z", deadline_method="structured")
    assert conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1


def test_window_feed_keeps_missing_items_open_then_heals(conn, cfg, feeds, monkeypatch):
    """Jobs Every Hour lists only its newest 300 jobs: dropping out != closed."""
    window = {"type": "listings_json", "group": "jobs", "url": A, "default_fields": ["cs"], "keep_missing_days": 21}
    sources = {"a": window}
    run(conn, cfg, feeds, sources)
    dropped = feeds[A].pop(0)
    report = run(conn, cfg, feeds, sources)["a"]
    assert report.closed == 0 and status_of(conn, dropped["url"])["status"] == "open"

    # 22 days later it's still missing -> now it closes.
    monkeypatch.setenv("RADAR_NOW", "2026-12-17T18:00:00Z")
    report = run(conn, cfg, feeds, sources)["a"]
    assert status_of(conn, dropped["url"])["status"] == "closed"


def test_window_feed_reopens_items_closed_by_the_old_rule(conn, cfg, feeds):
    sources = {"a": listing_source(A)}
    run(conn, cfg, feeds, sources)
    dropped = feeds[A].pop(0)
    run(conn, cfg, feeds, sources)                      # old rule: closed right away
    assert status_of(conn, dropped["url"])["status"] == "closed"
    sources["a"] = {**sources["a"], "keep_missing_days": 21}
    run(conn, cfg, feeds, sources)                      # new rule heals it
    assert status_of(conn, dropped["url"])["status"] == "open"
