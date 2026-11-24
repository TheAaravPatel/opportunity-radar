from conftest import make_http
from test_pipeline import A, listing_source, make_cfg, row

from radar.config import Config
from radar.digest import build_digest, mark_sent, render
from radar.pipeline import run_group


def registry_cfg(cfg, tmp_path, programs_yaml):
    (tmp_path / "early_programs.yaml").write_text(programs_yaml)
    return Config(settings=cfg.settings, keywords=cfg.keywords, config_dir=tmp_path,
                  sources={"reg": {"type": "early_programs_registry", "group": "daily"}, "a": listing_source(A)})


PROGRAMS = """programs:
  - id: fresh-prog
    firm: Bank
    program: Freshman Program
    url: https://p.test/1
    class_years: [freshman]
    deadlines:
      - {kind: application, date: 2026-12-01, label: Due}
  - id: soph-prog
    firm: Bank
    program: Sophomore Program
    url: https://p.test/2
    class_years: [sophomore]
    deadlines:
      - {kind: application, date: 2026-12-01, label: Due}
  - id: dec-prog
    firm: Firm
    program: Usually December
    url: https://p.test/3
    typical_open_month: 12
"""


def test_reminders_only_for_eligible_and_only_once(conn, cfg, tmp_path):
    # RADAR_NOW is Nov 24 2026 -> Dec 1 is exactly 7 days away.
    test_cfg = registry_cfg(cfg, tmp_path, PROGRAMS)
    run_group(conn, test_cfg, make_http({A: (200, [row(1)])}))
    digest = build_digest(conn, test_cfg)
    assert [d["title"] for d in digest.deadlines] == ["Freshman Program", "Sophomore Program"]
    assert [r["title"] for r in digest.reminders] == ["Freshman Program"]     # sophomore one: not eligible
    assert [w["title"] for w in digest.early_warnings] == ["Usually December"]
    assert digest.should_send

    mark_sent(conn, digest)
    again = build_digest(conn, test_cfg)
    assert not again.reminders and not again.early_warnings
    assert not again.should_send                 # nothing new, nothing firing


def test_new_items_after_first_import(conn, cfg, tmp_path):
    test_cfg = registry_cfg(cfg, tmp_path, "programs: []\n")
    run_group(conn, test_cfg, make_http({A: (200, [row(1)])}))
    mark_sent(conn, build_digest(conn, test_cfg))

    run_group(conn, test_cfg, make_http({A: (200, [row(1), row(2, title="Economics Research Intern")])}))
    digest = build_digest(conn, test_cfg)
    assert digest.new_counts == {"cs": 1, "econ": 1}
    html, text = render(digest, test_cfg)
    assert "Economics Research Intern" in html and "Economics Research Intern" in text
    assert "1 new" in digest.subject()


def test_failing_source_reported_once(conn, cfg, tmp_path):
    test_cfg = registry_cfg(cfg, tmp_path, "programs: []\n")
    run_group(conn, test_cfg, make_http({A: (500, "boom")}))
    digest = build_digest(conn, test_cfg)
    assert digest.newly_failed == ["a"]
    mark_sent(conn, digest)
    run_group(conn, test_cfg, make_http({A: (500, "boom")}))
    later = build_digest(conn, test_cfg)
    assert later.failed_sources and not later.newly_failed
