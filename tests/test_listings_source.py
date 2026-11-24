from datetime import date

from conftest import load_fixture

from radar.sources.listings_json import parse_listings, term_has_ended

OPTIONS = {"fields_by_category": {"Software": ["cs"], "Quant": ["cs", "econ"]}, "default_fields": ["cs"]}
TODAY = date(2026, 11, 24)


def test_term_has_ended():
    assert term_has_ended("Summer 2026", TODAY) is True
    assert term_has_ended("Summer 2027", TODAY) is False
    assert term_has_ended("Fall 2026", TODAY) is False      # runs to Dec 31
    assert term_has_ended("N/A", TODAY) is None


def test_parse_real_simplify_rows():
    rows = load_fixture("simplify_listings.json")
    result = parse_listings(rows, OPTIONS, TODAY)
    ids = {i.source_item_id for i in result.items}
    inactive = {r["id"] for r in rows if not r["active"]}
    ended = {r["id"] for r in rows if r["active"] and r["terms"] == ["Summer 2026"]}
    assert ids.isdisjoint(inactive)
    assert ids.isdisjoint(ended) and ended
    assert result.warnings and "skipped" in result.warnings[0]
    assert result.upstream_updated_at.endswith("Z")

    by_id = {i.source_item_id: i for i in result.items}
    quant = next(r for r in rows if r["category"] == "Quant" and r["id"] in by_id)
    assert by_id[quant["id"]].fields == ["cs", "econ"]

    off = [i for i in result.items if i.extra["off_season"]]
    assert off and all(not any("summer" in t.lower() for t in i.terms) for i in off)
    assert all(i.posted_at and len(i.posted_at) == 10 for i in result.items)
    assert all("Incomplete" not in i.degrees for i in result.items)


BIOTECH_OPTIONS = {
    "fields_by_category": {"Computational Biology, Data & AI": ["biotech", "cs"]},
    "default_fields": ["biotech"],
}


def test_parse_biotech_repo_rows():
    rows = load_fixture("biotech_listings.json")
    result = parse_listings(rows, BIOTECH_OPTIONS, TODAY)
    assert len(result.items) == sum(1 for r in rows if r["active"])
    comp_bio = [i for i in result.items if i.subcategory == "Computational Biology, Data & AI"]
    assert comp_bio and all(i.fields == ["biotech", "cs"] for i in comp_bio)
    coop = [i for i in result.items if "Co-op / Off-Cycle" in i.terms]
    assert coop and all(i.extra["off_season"] for i in coop)   # kept, and flagged off-season
