from datetime import date

from radar.models import Deadline
from radar.sources.registries import early_program_items, expected_window, registry_status, watched_page_items

TODAY = date(2026, 11, 24)


def status(entry, deadlines=()):
    return registry_status(entry, list(deadlines), TODAY)[0]


def test_expected_window():
    assert expected_window(12, TODAY) == (date(2026, 11, 17), date(2026, 12, 31))
    assert expected_window(3, TODAY) == (date(2027, 2, 15), date(2027, 3, 31))
    assert expected_window(11, TODAY) == (date(2026, 10, 18), date(2026, 11, 30))   # still this year's


def test_statuses():
    assert status({"status_hint": "discontinued"}) == "possibly discontinued"
    assert status({}, [Deadline("opens", "2026-12-14"), Deadline("application", "2027-02-15")]) == "opening soon"
    assert status({}, [Deadline("opens", "2026-11-02"), Deadline("application", "2027-01-04")]) == "open"
    assert status({"typical_open_month": 12}) == "expected soon"
    assert status({"typical_open_month": 3}) == "closed"
    assert status({}, [Deadline("application", "2026-10-01")]) == "closed"
    assert status({}) == "open"          # rolling / ongoing


def test_closed_reason_mentions_month():
    _, reason = registry_status({"typical_open_month": 3}, [], TODAY)
    assert "March" in reason


def test_real_registries_load(cfg):
    programs = cfg.load_file("early_programs.yaml")["programs"]
    items = early_program_items(programs, TODAY)
    assert len(items) == len(programs) >= 40
    by_id = {i.source_item_id: i for i in items}
    assert by_id["meta-university"].status == "possibly discontinued"
    assert by_id["bofa-student-leaders"].status == "opening soon"
    assert by_id["jane-street-fttp"].status == "open"

    pages = watched_page_items(cfg.load_file("watched_pages.yaml")["pages"], TODAY)
    assert all(p.extra["stanford"] for p in pages)
    biox = next(p for p in pages if p.source_item_id == "biox-usrp")
    assert biox.deadlines[0].date == "2027-01-12"
