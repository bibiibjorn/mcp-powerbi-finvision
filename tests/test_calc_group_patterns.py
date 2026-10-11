from core.calc_group_migration.patterns import match_suffix


def test_time_intel_matches_ytd_suffix():
    res = match_suffix("Sales YTD", "time_intel")
    assert res is not None
    assert res["base"] == "Sales"
    assert res["variant"] == "YTD"


def test_time_intel_does_not_match_unrelated_suffix():
    assert match_suffix("Random Stuff", "time_intel") is None


def test_scenario_matches_actual_suffix():
    res = match_suffix("Margin Actual", "scenario")
    assert res["variant"] == "Actual"


def test_currency_prefix_fx_matches():
    res = match_suffix("FX Sales", "currency")
    assert res["variant"] == "FX"
    assert res["base"] == "Sales"
