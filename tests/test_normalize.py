from radar.normalize import city_of, dedupe_key, is_bay_area, is_remote, normalize_org, normalize_title


def test_org_suffixes_are_ignored():
    assert normalize_org("Illumio, Inc.") == normalize_org("Illumio")
    assert normalize_org("The Brattle Group") == "brattle"
    assert normalize_org("Forté Foundation") == "forte foundation"


def test_titles_drop_season_and_year():
    assert normalize_title("Software Engineer Intern (Summer 2027)") == "software engineer intern"
    assert normalize_title("Software Engineer Intern - Summer '27") == "software engineer intern"
    # "Summer Analyst" alone is a job name, not a season tag - keep it
    assert normalize_title("Summer Analyst") == "summer analyst"


def test_cities():
    assert city_of("Sunnyvale, California - HQ") == "sunnyvale"
    assert city_of("Sunnyvale, CA") == "sunnyvale"
    assert city_of("NYC") == "new york"
    assert city_of("Remote in USA") == "remote"


def test_same_job_from_two_feeds_gets_same_key():
    simplify = dedupe_key("Illumio", "Engineering Intern - Architecture Platform", ["Sunnyvale, CA"])
    zshah = dedupe_key("Illumio", "Engineering Intern - Architecture Platform (Summer 2027)", ["Sunnyvale, California - HQ"])
    assert simplify == zshah


def test_bay_area_and_remote():
    assert is_bay_area(["South San Francisco, CA"])
    assert is_bay_area(["Palo Alto, CA", "New York, NY"])
    assert not is_bay_area(["Seattle, WA"])
    assert is_remote(["Remote in USA"])
    assert not is_remote(["Austin, TX"])



def test_regions_use_the_state():
    from radar.normalize import is_chicagoland, region_of
    assert region_of("Richmond, VA") is None              # Capital One HQ is not the Bay Area
    assert region_of("Richmond, CA") == "bay_area"
    assert region_of("Richmond") is None                    # ambiguous without a state
    assert region_of("Sunnyvale, California - HQ") == "bay_area"
    assert region_of("Chicago, IL") == "chicagoland"
    assert region_of("Greater Chicago Area") == "chicagoland"
    assert region_of("Aurora, CO") is None and region_of("Aurora, IL") == "chicagoland"
    assert region_of("Lake Forest, CA") is None             # Orange County, not Lake Forest IL
    assert is_chicagoland(["New York, NY", "Northbrook, IL"])
    assert region_of("Austin, TX, United States") is None
    assert region_of("Palo Alto, United States") == "bay_area"
