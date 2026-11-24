"""zshah101 + Jobs Every Hour parsers."""

import json
from pathlib import Path

from radar.sources.jobseveryhour import parse_jobseveryhour
from radar.sources.zshah import parse_zshah

FIXTURES = Path(__file__).parent / "fixtures"
ZSHAH_OPTIONS = {"fields_by_category": {"Software": ["cs"], "Quant": ["cs", "econ"], "Other": []}, "default_fields": ["cs"]}


def test_zshah_real_sample():
    data = json.loads((FIXTURES / "zshah_jobs.json").read_text())
    result = parse_zshah(data, ZSHAH_OPTIONS)
    assert result.upstream_updated_at == data["generated_at"]
    assert len(result.items) == len(data["jobs"])
    multi = [i for i in result.items if len(i.locations) > 1]
    assert multi, "the sample includes a '; '-separated multi-location job"
    assert all(i.posted_at is None or len(i.posted_at) == 10 for i in result.items)
    assert not any(t.lower() == "not stated" for i in result.items for t in i.terms)


def test_jobseveryhour_shape():
    data = {"total": 5493, "jobs": [
        {"company": "Acme", "title": "Software Intern", "url": "https://acme.test/1",
         "location": "Remote", "remote": True, "salary": "$40/hr", "posted": "2026-11-23"},
        {"company": "Beta", "title": "Data Intern", "url": "https://beta.test/2", "posted": "3 hours ago"},
        {"company": "Gamma", "title": "", "url": "https://gamma.test/3"},
    ]}
    result = parse_jobseveryhour(data, {"default_fields": ["cs"]})
    assert [i.organization for i in result.items] == ["Acme", "Beta"]
    assert result.items[0].remote and result.items[0].posted_at == "2026-11-23"
    assert result.items[1].posted_at is None           # relative text isn't a date
    assert "newest" in result.warnings[0]
