"""The travel rule: Bay Area / Chicagoland / remote, otherwise housing + flights must be covered."""

import sqlite3

from conftest import make_http
from test_pipeline import A, listing_source, make_cfg, row

from radar import db
from radar.config import Config
from radar.digest import build_digest
from radar.models import Item
from radar.pipeline import Preparer, run_group
from radar.travel import TravelClassifier


def fit(cfg, **kwargs):
    item = Item(source_item_id="1", title=kwargs.pop("title", "Intern"), organization="X", **kwargs)
    return Preparer(make_cfg(cfg, {})).prepare(item).travel


def test_near_you_or_remote(cfg):
    assert fit(cfg, locations=["Palo Alto, CA"]) == "bay_area"
    assert fit(cfg, locations=["Chicago, IL"]) == "chicagoland"
    assert fit(cfg, locations=["New York, NY", "Evanston, IL"]) == "chicagoland"   # any listed office near you
    assert fit(cfg, locations=["Remote in USA"], remote=True) == "remote"
    assert fit(cfg, locations=["Richmond, VA"]) == "outside"


def test_far_away_needs_housing_and_travel(cfg):
    assert fit(cfg, locations=["New York, NY"], text="Relocation assistance provided.") == "covered"
    assert fit(cfg, locations=["Atlanta, GA"], text="Scholarship covers airfare and hotel nights.") == "covered"
    assert fit(cfg, locations=["Atlanta, GA"], text="Up to $600 travel reimbursement.") == "partial"
    assert fit(cfg, locations=["New York, NY"], text="Housing is not provided; no relocation assistance.") == "outside"


def test_registry_value_wins(cfg):
    assert fit(cfg, locations=["New York, NY"], extra={"travel_covered": "full"}) == "covered"
    assert fit(cfg, locations=["Boston, MA"], text="fully funded", extra={"travel_covered": "none"}) == "outside"


def test_remote_can_be_switched_off(cfg):
    classifier = TravelClassifier(cfg.keywords, allow_remote=False)
    item = Item(source_item_id="1", title="Intern", organization="X", locations=["Remote"], remote=True)
    assert classifier.classify(item, None, True)[0] == "outside"


def test_digest_leaves_out_far_away_items(conn, cfg, tmp_path):
    (tmp_path / "early_programs.yaml").write_text("programs: []\n")
    test_cfg = Config(settings=cfg.settings, keywords=cfg.keywords, config_dir=tmp_path,
                      sources={"a": listing_source(A)})
    run_group(conn, test_cfg, make_http({A: (200, [row(1)])}))
    from radar.digest import mark_sent
    mark_sent(conn, build_digest(conn, test_cfg))
    feed = [row(1), row(2, title="Bay Intern"), row(3, title="Far Intern", location="Austin, TX")]
    run_group(conn, test_cfg, make_http({A: (200, feed)}))
    digest = build_digest(conn, test_cfg)
    titles = [i["title"] for items in digest.new_by_field.values() for i in items]
    assert "Bay Intern" in titles and "Far Intern" not in titles
    assert digest.hidden_far_away == 1


def test_old_database_gets_new_columns(tmp_path):
    """A version-1 database (like the one already on the data branch) is upgraded in place."""
    import re
    create = re.search(r"CREATE TABLE IF NOT EXISTS opportunities \(.*?\n\);", db.SCHEMA, re.S).group(0)
    v1 = "\n".join(line for line in create.splitlines() if not re.match(r"\s+(chicagoland|travel|travel_reason)\s", line))
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(v1)
    old.execute("INSERT INTO opportunities (dedupe_key, match_key, title, organization, type, first_seen_at, last_seen_at) "
                "VALUES ('k', 'm', 'T', 'O', 'industry internship', 'x', 'x')")
    old.commit()
    old.close()
    conn = db.connect(path)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(opportunities)")}
    assert {"chicagoland", "travel", "travel_reason"} <= columns
    assert conn.execute("SELECT title FROM opportunities").fetchone()["title"] == "T"   # data kept



def test_job_titles_dont_count_as_travel_coverage(cfg):
    assert fit(cfg, title="Flight Software Intern", locations=["Hawthorne, CA"]) == "outside"
    assert fit(cfg, title="Hotel Revenue Analyst Intern", locations=["Las Vegas, NV"]) == "outside"
