import json

from conftest import make_http
from test_pipeline import A, listing_source, make_cfg, row

from radar.dashboard.build import build_dashboard
from radar.pipeline import run_group


def test_dashboard_builds_and_escapes(conn, cfg, tmp_path):
    evil = row(1, title='Intern </script><script>alert(1)</script>')
    sources = {"a": listing_source(A), "reg": {"type": "early_programs_registry", "group": "daily"}}
    run_group(conn, make_cfg(cfg, sources), make_http({A: (200, [evil, row(2)])}))

    index = build_dashboard(conn, make_cfg(cfg, sources), tmp_path / "site")
    html = index.read_text()
    # The only </script> tags are the two real ones; the job title can't close the data block.
    assert html.count("</script>") == 2
    data = json.loads((tmp_path / "site" / "data.json").read_text())
    titles = {i["title"] for i in data["items"]}
    assert evil["title"] in titles
    assert data["profile"]["class_year"] == "freshman"
    # Items from a source's first import are the baseline, not "new".
    assert all(i["baseline"] for i in data["items"] if "a" in i["sources"])
    health = {s["id"]: s["state"] for s in data["sources"]}
    assert health["a"] == "ok"

    ics = (tmp_path / "site" / "deadlines.ics").read_text()
    assert ics.startswith("BEGIN:VCALENDAR") and "BEGIN:VEVENT" in ics and ics.rstrip().endswith("END:VCALENDAR")
