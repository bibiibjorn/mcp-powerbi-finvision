"""Query plan parsing, verified against a real 19,963-row DAX Studio export."""

from __future__ import annotations

import pytest

from pbi_perf_core.query_plan import (
    analyse_plan,
    compare_plans,
    parse_plan,
    rows_from_export,
)

EXPECTED_PHYSICAL_ROWS = 10044
EXPECTED_LOGICAL_ROWS = 9919
EXPECTED_MAX_RECORDS = 418
EXPECTED_HIGHLIGHTS = 470


@pytest.fixture(scope="module")
def physical(query_plan_export):
    return rows_from_export(query_plan_export["PhysicalQueryPlanRows"], "physical")


@pytest.fixture(scope="module")
def logical(query_plan_export):
    return rows_from_export(query_plan_export["LogicalQueryPlanRows"], "logical")


class TestFixtureShape:
    """The export we must be able to reproduce."""

    def test_physical_row_count(self, physical):
        assert len(physical) == EXPECTED_PHYSICAL_ROWS

    def test_logical_row_count(self, logical):
        assert len(logical) == EXPECTED_LOGICAL_ROWS

    def test_max_records(self, physical):
        assert physical.max_records == EXPECTED_MAX_RECORDS

    def test_highlight_count(self, physical):
        assert physical.highlight_count == EXPECTED_HIGHLIGHTS

    def test_logical_rows_have_no_records(self, query_plan_export):
        # DAX Studio omits the Records key entirely on logical plans.
        assert all("Records" not in row for row in query_plan_export["LogicalQueryPlanRows"])


class TestParsing:
    """Parsing raw plan text - the form the trace event actually delivers."""

    def test_level_is_tab_count(self):
        text = "Root: IterPhyOp\n\tChild: IterPhyOp\n\t\tGrandchild: LookupPhyOp"
        plan = parse_plan(text)
        assert [r.level for r in plan.rows] == [0, 1, 2]

    def test_indented_operation_uses_four_spaces(self):
        plan = parse_plan("Root: IterPhyOp\n\tChild: IterPhyOp")
        assert plan.rows[1].indented_operation == "    Child: IterPhyOp"

    def test_records_parsed(self):
        plan = parse_plan("Spool: IterPhyOp #Records=418")
        assert plan.rows[0].records == 418

    def test_recs_alternate_spelling(self):
        # HashLookup emits #Recs= rather than #Records=.
        plan = parse_plan("HashLookup: IterPhyOp #Recs=29")
        assert plan.rows[0].records == 29

    def test_blank_lines_skipped(self):
        plan = parse_plan("A: IterPhyOp\n\n\nB: IterPhyOp")
        assert len(plan) == 2
        assert [r.row_number for r in plan.rows] == [1, 2]

    def test_highlight_matches_cache_and_vertipaq(self):
        plan = parse_plan(
            "Cache: IterPhyOp\n"
            "VertipaqResult: IterPhyOp\n"
            "DataPostFilter: IterPhyOp\n"
            "Filter: IterPhyOp"
        )
        assert [r.highlight_row for r in plan.rows] == [True, True, True, False]

    def test_empty_text(self):
        assert len(parse_plan("")) == 0
        assert len(parse_plan(None)) == 0

    def test_crlf_and_lf_both_split(self):
        assert len(parse_plan("A: Op\r\nB: Op\nC: Op")) == 3


class TestRecordsInheritance:
    """A physical row without #Records shows its children's cardinality."""

    def test_parent_inherits_child_records(self):
        plan = parse_plan("Parent: IterPhyOp\n\tChild: IterPhyOp #Records=100")
        assert plan.rows[0].records == 100

    def test_parent_takes_max_of_children(self):
        plan = parse_plan(
            "Parent: IterPhyOp\n" "\tA: IterPhyOp #Records=10\n" "\tB: IterPhyOp #Records=250"
        )
        assert plan.rows[0].records == 250

    def test_own_records_win_over_children(self):
        plan = parse_plan("Parent: IterPhyOp #Records=5\n\tChild: IterPhyOp #Records=99")
        assert plan.rows[0].records == 5

    def test_logical_plans_do_not_inherit(self):
        plan = parse_plan("Parent: RelLogOp\n\tChild: RelLogOp #Records=100", "logical")
        assert plan.rows[0].records is None


class TestSiblings:
    def test_next_sibling_marks_subtree_end(self):
        plan = parse_plan("A: Op\n\tA1: Op\n\tA2: Op\nB: Op")
        # A's subtree ends at row 3 (A2); B is the next top-level row.
        assert plan.rows[0].next_sibling_row_number == 3
        assert plan.rows[3].next_sibling_row_number == 4


class TestOperatorExtraction:
    def test_operator_strips_properties(self):
        plan = parse_plan("GroupSemijoin: IterPhyOp LogOp=GroupSemiJoin IterCols(0)(''[X])")
        assert plan.rows[0].operator == "GroupSemijoin: IterPhyOp"

    def test_census_counts_real_operators(self, physical):
        census = dict(physical.operator_census())
        # These spellings were previously documented as non-existent; they are
        # present in a real production plan, in quantity.
        assert census.get("SpoolLookup: LookupPhyOp", 0) > 0
        assert census.get("GroupSemijoin: IterPhyOp", 0) > 0
        assert census.get("Cache: IterPhyOp", 0) > 0

    def test_top_records_sorted_descending(self, physical):
        top = physical.top_records(10)
        values = [r.records for r in top]
        assert values == sorted(values, reverse=True)
        assert values[0] == EXPECTED_MAX_RECORDS


class TestAnalysis:
    def test_analysis_reports_core_metrics(self, physical, logical):
        out = analyse_plan(physical, logical, result_rows=8, se_query_count=189)
        assert out["physical_rows"] == EXPECTED_PHYSICAL_ROWS
        assert out["logical_rows"] == EXPECTED_LOGICAL_ROWS
        assert out["physical_max_records"] == EXPECTED_MAX_RECORDS
        assert out["operator_census"]

    def test_over_materialisation_flagged(self, physical, logical):
        out = analyse_plan(physical, logical, result_rows=8, se_query_count=189)
        codes = {f["code"] for f in out["findings"]}
        # 418 records for 8 result rows is a 52x materialisation.
        assert "CROSSAPPLY" in codes or "FE_GROUPBY" in codes

    def test_no_findings_on_trivial_plan(self):
        plan = parse_plan("Constant: LookupPhyOp")
        out = analyse_plan(plan, result_rows=1, se_query_count=1)
        assert out["findings"] == []

    def test_handles_missing_logical(self, physical):
        out = analyse_plan(physical, None)
        assert "logical_rows" not in out


class TestCompare:
    def test_identical_plans_show_no_delta(self, physical):
        diff = compare_plans(physical, physical)
        assert diff["rows"]["delta"] == 0
        assert diff["max_records"]["delta"] == 0
        assert diff["operator_deltas"] == []

    def test_removed_operator_detected(self):
        before = parse_plan("A: Op\nCrossApply: IterPhyOp\nB: Op")
        after = parse_plan("A: Op\nB: Op")
        diff = compare_plans(before, after)
        assert "CrossApply: IterPhyOp" in diff["removed_operators"]
        assert diff["rows"]["delta"] == -1

    def test_added_operator_detected(self):
        before = parse_plan("A: Op")
        after = parse_plan("A: Op\nCache: IterPhyOp")
        diff = compare_plans(before, after)
        assert "Cache: IterPhyOp" in diff["added_operators"]
