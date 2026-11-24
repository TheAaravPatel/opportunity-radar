"""Conference sources and the conference rules (priority, hiding, dedupe)."""

import json
from datetime import date
from pathlib import Path

from radar.sources.badgeup import parse_badgeup
from radar.sources.conferences_common import cost_tier, deadline_kind, key_name, parse_date_range
from radar.sources.registries import conference_registry_items
from radar.sources.student_opportunities import parse_student_opportunities

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 24)


def test_date_ranges():
    assert parse_date_range("Oct 27-30, 2026 · Anaheim, CA", TODAY) == ("2026-10-27", "2026-10-30")
    assert parse_date_range("Nov 30-Dec 4, 2026", TODAY) == ("2026-11-30", "2026-12-04")
    assert parse_date_range("Dec 28-Jan 2", TODAY) == ("2026-12-28", "2027-01-02")   # range crosses New Year
    assert parse_date_range("Mar 24-28", TODAY) == ("2027-03-24", "2027-03-28")     # no year -> next one
    assert parse_date_range("Dates TBA", TODAY) == (None, None)


def test_labels_and_costs():
    assert deadline_kind("Travel Scholarship") == "scholarship"
    assert deadline_kind("Paper") == "abstract"
    assert deadline_kind("Early-bird registration") == "registration"
    assert cost_tier("Free (virtual pass)") == "free"
    assert cost_tier("Scholarship · Paid") == "scholarship"
    assert cost_tier("Paid (student)") == "student"
    assert cost_tier("Paid") == "full"


def test_dedupe_names():
    assert key_name("ABRCMS (Annual Biomedical Research Conference for Minoritized Scientists)") == "abrcms"
    assert key_name("LA Hacks 27") == key_name("LA Hacks") == "la hacks"
    assert key_name("SHPE National Convention 2027") == "shpe national convention"


def test_student_opportunities_rules():
    data = json.loads((FIXTURES / "student_opportunities.json").read_text())
    result = parse_student_opportunities(data, TODAY)
    assert result.upstream_updated_at == f"{data['updated']}T00:00:00Z"
    by_cat = {c["id"]: c["category"] for c in data["conferences"]}
    for item in result.items:
        category = by_cat[item.source_item_id]
        assert item.type == ("hackathon" if category == "hackathon" else "conference")
        if category in ("academic", "industry") and not item.extra["conference"]["scholarship"]:
            assert item.extra["hidden_reason"], item.title           # PhD venues / trade shows hidden
        if category in ("student", "premed", "business", "tech"):
            assert not item.extra["hidden_reason"]
        assert item.dedupe_key.startswith("conf|")
        start = item.extra["conference"]["start_date"]
        if start:
            assert any(d.kind == "event" and d.date == start for d in item.deadlines)
    funded = [i for i in result.items if i.extra["conference"]["scholarship"]]
    assert funded and all(i.extra["conference"]["cost_tier"] in ("scholarship", "free") for i in funded)


def test_badgeup_parse_and_stale_dates():
    text = (FIXTURES / "badgeup_readme.md").read_text()
    result = parse_badgeup(text, TODAY)
    names = {i.title for i in result.items}
    assert "GitHub Universe" in names
    # Sections outside your fields (e.g. Law & Government) are skipped.
    assert names.isdisjoint(['NBLSA National Convention', 'HNBA Annual Convention', 'Lavender Law (National LGBTQ+ Bar)'])
    ghc = next(i for i in result.items if i.title.startswith("Grace Hopper"))
    assert ghc.status == "closed"                    # Oct 2026 has passed - BadgeUp's "OPEN" is ignored
    dev_week = next(i for i in result.items if i.title == "DeveloperWeek")
    assert dev_week.extra["conference"]["start_date"] == "2027-02-09"
    assert dev_week.extra["conference"]["cost_tier"] == "student"
    assert result.warnings and "stale" in result.warnings[0]


def test_registry_priority_and_statuses(cfg):
    entries = cfg.load_file("conferences.yaml")["conferences"]
    items = {i.registry_id: i for i in conference_registry_items(entries, TODAY)}
    assert items["grace-hopper"].status == "possibly discontinued"
    assert items["treehacks"].type == "hackathon"
    assert items["treehacks"].extra["conference"]["bay_area"]
    assert items["treehacks"].extra["priority"] == 0                # free + Bay Area
    assert items["afrotech"].extra["priority"] == 2.5               # full price + far away
    assert items["acm-w-conference-scholarships"].type == "travel scholarship"
    assert any(d.kind == "scholarship" and d.date == "2026-12-15" for d in items["sigcse-ts"].deadlines)
