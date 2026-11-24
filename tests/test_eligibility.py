from datetime import date

from radar.eligibility import EligibilityScorer, class_for_grad_year, detect_class_years, detect_grad_years
from radar.models import Item

FALL_2026 = date(2026, 11, 24)


def scorer(cfg):
    return EligibilityScorer(cfg.keywords, grad_year=2030)


def reasons(result):
    return [r["reason"] for r in result.reasons]


def test_class_year_advances_each_school_year():
    assert class_for_grad_year(2030, date(2026, 11, 24)) == "freshman"
    assert class_for_grad_year(2030, date(2027, 5, 1)) == "freshman"
    assert class_for_grad_year(2030, date(2027, 9, 15)) == "sophomore"
    assert class_for_grad_year(2029, FALL_2026) == "sophomore"


def test_rising_sophomores_means_current_freshmen():
    assert detect_class_years("Open to rising sophomores") == {"freshman"}
    assert detect_class_years("rising juniors and seniors") == {"sophomore", "junior"}


def test_job_levels_are_not_class_years():
    assert detect_class_years("Senior Software Engineer") == set()
    assert detect_class_years("Junior Analyst") == set()
    assert detect_class_years("2027 First Year Analyst Program") == set()   # bank new-grad level
    assert detect_class_years("juniors and seniors only") == {"junior", "senior"}


def test_grad_years():
    assert detect_grad_years("for students graduating between December 2028 and June 2030") == [2028, 2030]
    assert detect_grad_years("Class of 2030") == [2030]
    assert detect_grad_years("2029 graduates") == [2029]


def test_freshman_program_scores_high(cfg):
    result = scorer(cfg).score(Item(source_item_id="1", title="Freshman Discovery Program", organization="Citi"), FALL_2026)
    assert "Open to freshmen" in reasons(result)
    assert result.score >= 80
    assert result.class_years == ["freshman"]


def test_phd_role_scores_low(cfg):
    result = scorer(cfg).score(Item(source_item_id="1", title="PhD Research Intern", organization="X"), FALL_2026)
    assert "PhD-level role" in reasons(result)
    assert result.score < 40


def test_degree_list_without_bachelors(cfg):
    item = Item(source_item_id="1", title="Research Intern", organization="X", degrees=["Master's", "PhD"])
    result = scorer(cfg).score(item, FALL_2026)
    assert any("candidates only" in r for r in reasons(result))


def test_graduation_window(cfg):
    ok = scorer(cfg).score(Item(source_item_id="1", title="Discover Citadel", organization="Citadel",
                                text="graduating between December 2028 and June 2030"), FALL_2026)
    assert any("includes your 2030" in r for r in reasons(ok))
    late = scorer(cfg).score(Item(source_item_id="2", title="Summer Analyst", organization="Bank",
                                  text="expected graduation date between December 2027 and June 2028"), FALL_2026)
    assert any("Requires graduating by 2028" in r for r in reasons(late))


def test_registry_class_years_are_final(cfg):
    # The notes mention graduation years, but the registry's list wins.
    item = Item(source_item_id="1", title="Ascend", organization="MLT", class_years=["freshman"],
                text="For freshmen graduating Fall 2029-Summer 2030")
    result = scorer(cfg).score(item, FALL_2026)
    assert result.class_years == ["freshman"]
    assert not any("Graduation window" in r for r in reasons(result))


def test_sophomore_only_program_is_plan_ahead(cfg):
    item = Item(source_item_id="1", title="Early Insights", organization="MS", class_years=["sophomore"])
    result = scorer(cfg).score(item, FALL_2026)
    assert any("apply next year" in r for r in reasons(result))


def test_no_class_info_gets_small_bonus(cfg):
    result = scorer(cfg).score(Item(source_item_id="1", title="Software Engineer Intern", organization="X"), FALL_2026)
    assert reasons(result) == ["No class-year requirement found"]
    assert result.score == 60


def test_non_stanford_only(cfg):
    item = Item(source_item_id="1", title="Summer Research", organization="X", text="Open to students from other universities")
    assert "For non-Stanford students only" in reasons(scorer(cfg).score(item, FALL_2026))
