from core.calc_group_migration.tmdl_scaffold import scaffold_calc_group


def test_scaffold_emits_calculation_group_header():
    candidate = {
        "pattern_name": "time_intel",
        "variant_suffix_set": ["YTD", "MTD", "LY"],
        "base_measures": ["Sales", "Margin"],
    }
    out = scaffold_calc_group(candidate, calc_group_table_name="TimeIntel", precedence=10)
    assert "table TimeIntel" in out["tmdl"]
    assert "calculationGroup" in out["tmdl"]
    assert "precedence: 10" in out["tmdl"]


def test_scaffold_includes_one_item_per_variant():
    candidate = {
        "pattern_name": "time_intel",
        "variant_suffix_set": ["YTD", "MTD"],
        "base_measures": ["Sales"],
    }
    out = scaffold_calc_group(candidate, calc_group_table_name="TI", precedence=5)
    assert "calculationItem YTD" in out["tmdl"]
    assert "calculationItem MTD" in out["tmdl"]


def test_scaffold_includes_next_steps():
    candidate = {
        "pattern_name": "scenario",
        "variant_suffix_set": ["Actual", "Budget"],
        "base_measures": ["Profit"],
    }
    out = scaffold_calc_group(candidate, calc_group_table_name="Scenario", precedence=20)
    assert any("02_Model_Operations" in s for s in out["next_steps"])
