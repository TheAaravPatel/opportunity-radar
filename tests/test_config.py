"""Sanity checks on the YAML config, so a typo fails CI instead of a nightly run."""

import re

from radar.models import CLASS_YEARS, DEADLINE_KINDS, FIELDS, TYPES
from radar.sources import SOURCE_TYPES

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def test_every_source_type_exists(cfg):
    for source_id, options in cfg.sources.items():
        assert options.get("type") in SOURCE_TYPES, source_id
        groups = options.get("group")
        groups = [groups] if isinstance(groups, str) else groups
        assert groups and set(groups) <= {"jobs", "daily", "weekly"}, source_id


def check_entries(entries, name_key):
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "duplicate ids"
    for e in entries:
        assert SLUG.match(e["id"]), e["id"]
        assert e.get(name_key), e["id"]
        assert e["url"].startswith("https://"), e["id"]
        assert set(e.get("fields") or []) <= set(FIELDS), e["id"]
        assert set(e.get("class_years") or []) <= set(CLASS_YEARS), e["id"]
        if e.get("type"):
            assert e["type"] in TYPES, e["id"]
        for d in e.get("deadlines") or []:
            assert d["kind"] in DEADLINE_KINDS, e["id"]
            assert re.match(r"^\d{4}-\d{2}-\d{2}$", str(d["date"])), e["id"]
        if e.get("typical_open_month") is not None:
            assert 1 <= int(e["typical_open_month"]) <= 12, e["id"]
        if e.get("travel_covered") is not None:
            assert e["travel_covered"] in ("full", "partial", "none"), e["id"]
        if e.get("watch") is not None:
            assert isinstance(e["watch"], bool), e["id"]
            assert e["watch"] or e.get("watch_note"), f"{e['id']}: say why it isn't watched"


def test_early_programs_registry(cfg):
    check_entries(cfg.load_file("early_programs.yaml")["programs"], "program")


def test_watched_pages(cfg):
    registry = cfg.load_file("watched_pages.yaml")
    pages, job_pages = registry["pages"], registry.get("job_pages") or []
    check_entries(pages, "name")
    check_entries(job_pages, "name")
    ids = [p["id"] for p in pages + job_pages]
    assert len(ids) == len(set(ids)), "page ids must be unique across pages and job_pages"
    for p in pages + job_pages:
        assert p.get("render", "auto") in ("auto", "http", "playwright"), p["id"]
        for key in ("only_lines_matching", "ignore_lines"):
            if p.get(key):
                re.compile(p[key])   # a broken regex would make the watcher fail every day


def test_page_watcher_runs_after_the_registries(cfg):
    # The registries must create each card first, so they own its details.
    order = list(cfg.sources)
    assert order.index("page_watcher") > max(order.index("stanford_pages"), order.index("early_programs"))


def test_page_ids_unique_across_registries(cfg):
    pages = cfg.load_file("watched_pages.yaml")
    ids = [e["id"] for e in (pages["pages"] + (pages.get("job_pages") or [])
                             + cfg.load_file("early_programs.yaml")["programs"])]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicates, f"the page-watcher keys pages by id: {duplicates}"


def test_companies(cfg):
    valid_ats = {"greenhouse", "lever", "ashby", "workday", "oracle", "unknown"}
    for c in cfg.companies:
        assert c["ats"] in valid_ats, c["name"]
        assert set(c.get("fields") or []) <= set(FIELDS), c["name"]
        if c["ats"] in ("greenhouse", "lever", "ashby"):
            assert c.get("slug"), c["name"]
        if c["ats"] == "workday":
            assert c.get("tenant") and c.get("host") and c.get("site"), c["name"]
        if c["ats"] == "oracle":
            assert c.get("host") and c.get("site"), c["name"]
    workday = [c for c in cfg.companies if c["ats"] == "workday" and c.get("enabled", True)]
    assert len(workday) <= 20, "keep the Workday list short - heavy pollers get bot-blocked"


def test_conferences_registry(cfg):
    entries = cfg.load_file("conferences.yaml")["conferences"]
    check_entries(entries, "name")
    for e in entries:
        assert e.get("type", "conference") in ("conference", "hackathon", "travel scholarship"), e["id"]
        assert e.get("cost", "unknown") in ("free", "scholarship", "student", "full", "unknown"), e["id"]
        if e.get("start") and e.get("end"):
            assert str(e["start"]) <= str(e["end"]), e["id"]
