"""F-05: the taxonomy-only audit rules (``core/analysis/audit_rules.py``) - one true-positive and
one true-negative fixture each (over-firing is the failure mode of an advisor), over the
synthetic TMSL of ``tests/fixtures/bpa_synthetic.py``."""

from __future__ import annotations

import copy

import pytest

from core.analysis.audit_rules import AUDIT_CHECKS, AUDIT_RULES
from core.analysis.star_schema_validator import StarSchemaValidator, find_ambiguous_paths
from tests.fixtures.bpa_synthetic import build_clean_model, table, with_changes


@pytest.fixture(scope="module")
def clean():
    return build_clean_model(6)


def run(rule_id, model):
    return AUDIT_CHECKS[rule_id](model)


def names(hits):
    return sorted(h[1] for h in hits)


def role(name, table_name, expr="[X] = 1"):
    return {
        "name": name,
        "modelPermission": "read",
        "tablePermissions": [{"name": table_name, "filterExpression": expr}],
    }


def rel(model, name):
    return next(r for r in model["relationships"] if r["name"] == name)


def test_every_rule_is_registered_and_quiet_on_a_clean_star(clean):
    assert {r.id for r in AUDIT_RULES} == set(AUDIT_CHECKS)
    for rule_id in AUDIT_CHECKS:
        assert run(rule_id, clean) == [], rule_id


# ── CR1 ──────────────────────────────────────────────────────────────────────


def test_cr1_bidirectional_on_a_path_rls_reaches(clean):
    m = with_changes(
        clean,
        lambda m: (
            m.__setitem__("roles", [role("R", "Product")]),
            rel(m, "Sales_Product").__setitem__("crossFilteringBehavior", "bothDirections"),
        ),
    )
    hits = run("AUDIT_BIDI_ON_RLS_PATH", m)
    assert [h[1] for h in hits] == ["Sales[Product Key] -> Product[Product Key]"]
    assert "Product" in hits[0][3]


def test_cr1_no_hit_without_rls_or_off_the_rls_scope(clean):
    bidi = with_changes(
        clean,
        lambda m: rel(m, "Budget_Store").__setitem__("crossFilteringBehavior", "bothDirections"),
    )
    assert run("AUDIT_BIDI_ON_RLS_PATH", bidi) == []  # bidirectional but no RLS at all
    secured = with_changes(bidi, lambda m: m.__setitem__("roles", [role("R", "Customer")]))
    # RLS on Customer reaches Sales + Returns; Budget -> Store is outside that scope
    assert run("AUDIT_BIDI_ON_RLS_PATH", secured) == []


# ── CR3 ──────────────────────────────────────────────────────────────────────


def test_cr3_unknown_datatype(clean):
    m = with_changes(
        clean,
        lambda m: table(m, "Product")["columns"].append({"name": "Mystery", "dataType": "unknown"}),
    )
    assert names(run("AUDIT_COLUMN_DATATYPE_UNKNOWN", m)) == ["Mystery"]
    assert run("AUDIT_COLUMN_DATATYPE_UNKNOWN", clean) == []


# ── CR5 ──────────────────────────────────────────────────────────────────────


def test_cr5_rls_on_a_fact_not_on_a_dimension(clean):
    on_fact = with_changes(clean, lambda m: m.__setitem__("roles", [role("Reg", "Sales")]))
    hits = run("AUDIT_RLS_ON_FACT_TABLE", on_fact)
    assert names(hits) == ["Sales"] and "Reg" in hits[0][3]
    on_dim = with_changes(clean, lambda m: m.__setitem__("roles", [role("Reg", "Product")]))
    assert run("AUDIT_RLS_ON_FACT_TABLE", on_dim) == []
    empty = with_changes(clean, lambda m: m.__setitem__("roles", [role("Reg", "Sales", "  ")]))
    assert run("AUDIT_RLS_ON_FACT_TABLE", empty) == []  # no filter expression = no RLS


# ── CR6 ──────────────────────────────────────────────────────────────────────


def test_cr6_inactive_relationship_to_a_secured_table(clean):
    m = with_changes(
        clean,
        lambda m: (
            m.__setitem__("roles", [role("R", "Product")]),
            rel(m, "Sales_Product").__setitem__("isActive", False),
        ),
    )
    hits = run("AUDIT_RLS_INACTIVE_RELATIONSHIP", m)
    assert [h[1] for h in hits] == ["Sales[Product Key] -> Product[Product Key]"]


def test_cr6_no_hit_when_another_active_path_exists_or_table_not_secured(clean):
    alt = with_changes(
        clean,
        lambda m: (
            m.__setitem__("roles", [role("R", "Product")]),
            rel(m, "Sales_Product").__setitem__("isActive", False),
            m["relationships"].append(
                {
                    "name": "Sales_Product_2",
                    "fromTable": "Sales",
                    "fromColumn": "Product Key",
                    "toTable": "Product",
                    "toColumn": "Product Key",
                }
            ),
        ),
    )
    assert run("AUDIT_RLS_INACTIVE_RELATIONSHIP", alt) == []
    unsecured = with_changes(
        clean, lambda m: rel(m, "Sales_Product").__setitem__("isActive", False)
    )
    assert run("AUDIT_RLS_INACTIVE_RELATIONSHIP", unsecured) == []


# ── CR8 ──────────────────────────────────────────────────────────────────────


def test_cr8_userelationship_inside_an_rls_filter(clean):
    bad = with_changes(
        clean,
        lambda m: m.__setitem__(
            "roles",
            [role("R", "Sales", "CALCULATE(TRUE(), USERELATIONSHIP(Sales[A], Date[Date]))")],
        ),
    )
    assert names(run("AUDIT_USERELATIONSHIP_IN_RLS_FILTER", bad)) == ["Sales"]
    commented = with_changes(
        clean,
        lambda m: m.__setitem__(
            "roles", [role("R", "Sales", "// USERELATIONSHIP(a, b) was removed\n[X] = 1")]
        ),
    )
    assert run("AUDIT_USERELATIONSHIP_IN_RLS_FILTER", commented) == []
    plain = with_changes(clean, lambda m: m.__setitem__("roles", [role("R", "Sales")]))
    assert run("AUDIT_USERELATIONSHIP_IN_RLS_FILTER", plain) == []


# ── CR9 ──────────────────────────────────────────────────────────────────────


def _extra_rel(model, name, fc, tc, to="Product", **kw):
    model["relationships"].append(
        {"name": name, "fromTable": "Sales", "fromColumn": fc, "toTable": to, "toColumn": tc, **kw}
    )


def test_cr9_parallel_relationships_on_different_key_pairs(clean):
    m = with_changes(
        clean,
        lambda m: _extra_rel(m, "Sales_Product_Variant", "Variant", "Variant", isActive=False),
    )
    hits = run("AUDIT_COMPOSITE_KEY_RELATIONSHIP", m)
    assert len(hits) == 1 and "composite key" in hits[0][3]
    multi = with_changes(clean, lambda m: rel(m, "Sales_Date").__setitem__("type", "multiColumn"))
    assert "multiColumn" in run("AUDIT_COMPOSITE_KEY_RELATIONSHIP", multi)[0][3]


def test_cr9_role_playing_dimension_is_not_a_composite_key(clean):
    m = with_changes(
        clean,
        lambda m: _extra_rel(m, "Sales_ShipDate", "Ship Date", "Date", to="Date", isActive=False),
    )
    assert run("AUDIT_COMPOSITE_KEY_RELATIONSHIP", m) == []
    assert run("AUDIT_COMPOSITE_KEY_RELATIONSHIP", clean) == []


# ── DES11 (graph check lives in the topology validator) ──────────────────────


def _r(a, b, **kw):
    return {"fromTable": a, "toTable": b, **kw}


def test_des11_classic_multi_fact_star_is_not_ambiguous():
    star = [
        _r("Sales", "Product"),
        _r("Sales", "Date"),
        _r("Returns", "Product"),
        _r("Returns", "Date"),
    ]
    assert find_ambiguous_paths(star) == []


def test_des11_bidirectional_fact_creates_two_paths():
    rels = [
        _r("Sales", "Product", crossFilteringBehavior="bothDirections"),
        _r("Sales", "Date"),
        _r("Returns", "Product"),
        _r("Returns", "Date"),
    ]
    found = find_ambiguous_paths(rels)
    assert found and found[0][0] == "Date" and found[0][1] == "Returns"
    findings = StarSchemaValidator().validate(
        {
            "tables": [{"name": n} for n in ("Sales", "Product", "Date", "Returns")],
            "relationships": rels,
        }
    )
    assert "AMBIGUOUS_RELATIONSHIP_PATH" in {f.finding_id for f in findings}


def test_des11_inactive_relationship_does_not_count_and_search_is_bounded():
    rels = [_r("Sales", "Product"), _r("Sales", "Product", isActive=False)]
    assert find_ambiguous_paths(rels) == []
    # a dense bidirectional mesh must terminate and cap its findings
    n = 9
    mesh = [
        _r(f"T{i}", f"T{j}", crossFilteringBehavior="bothDirections")
        for i in range(n)
        for j in range(i + 1, n)
    ]
    assert 0 < len(find_ambiguous_paths(mesh)) <= 10


# ── DES12 / DES13 ────────────────────────────────────────────────────────────


def _calc_group(items):
    return {
        "name": "Time Intelligence",
        "columns": [{"name": "Time", "dataType": "string"}],
        "calculationGroup": {
            "precedence": 1,
            "calculationItems": [{"name": n, "expression": e} for n, e in items],
        },
        "partitions": [],
    }


def test_des12_item_without_selectedmeasure(clean):
    m = with_changes(
        clean,
        lambda m: m["tables"].append(
            _calc_group(
                [
                    ("YTD", "CALCULATE(SELECTEDMEASURE(), DATESYTD('Date'[Date]))"),
                    ("Constant", "1"),
                    ("Commented", ["// SELECTEDMEASURE() would go here", "0"]),
                ]
            )
        ),
    )
    hits = run("AUDIT_CALCITEM_NO_SELECTEDMEASURE", m)
    assert names(hits) == ["Commented", "Constant"]
    ok = with_changes(
        clean,
        lambda m: m["tables"].append(_calc_group([("Current", "SELECTEDMEASURE()")])),
    )
    assert run("AUDIT_CALCITEM_NO_SELECTEDMEASURE", ok) == []


def _udf_model(clean, funcs, measure_expr="1"):
    def mut(m):
        m["functions"] = funcs
        table(m, "Measures")["measures"].append({"name": "Caller", "expression": measure_expr})

    return with_changes(clean, mut)


def test_des13_unused_udf(clean):
    funcs = [
        {"name": "Local.Used", "expression": "(x) => x + 1"},
        {"name": "Local.Dead", "expression": "(x) => x * 2"},
    ]
    m = _udf_model(clean, funcs, measure_expr="Local.Used(1)")
    assert names(run("AUDIT_UDF_UNUSED", m)) == ["Local.Dead"]


def test_des13_udf_called_from_another_udf_or_a_comment_only_mention(clean):
    chained = _udf_model(
        clean,
        [
            {"name": "Local.Inner", "expression": "(x) => x"},
            {"name": "Local.Outer", "expression": "(x) => Local.Inner(x)"},
        ],
        measure_expr="Local.Outer(1)",
    )
    assert run("AUDIT_UDF_UNUSED", chained) == []
    only_comment = _udf_model(
        clean,
        [{"name": "Local.Dead", "expression": "(x) => x"}],
        measure_expr=["// Local.Dead(1) was inlined", "1"],
    )
    assert names(run("AUDIT_UDF_UNUSED", only_comment)) == ["Local.Dead"]


def test_des13_duplicate_udf_body(clean):
    dup = _udf_model(
        clean,
        [
            {"name": "Local.A", "expression": "(x) =>   x + 1"},
            {"name": "Local.B", "expression": "(x) => x + 1"},
            {"name": "Local.C", "expression": "(x) => x + 2"},
        ],
    )
    assert names(run("AUDIT_UDF_DUPLICATE_BODY", dup)) == ["Local.A", "Local.B"]
    distinct = _udf_model(clean, [{"name": "Local.A", "expression": "(x) => x"}])
    assert run("AUDIT_UDF_DUPLICATE_BODY", distinct) == []


# ── AI readiness ─────────────────────────────────────────────────────────────


def _add_col(model, tname, col):
    table(model, tname)["columns"].append(col)


def test_ai1_implicit_measures(clean):
    base = with_changes(
        clean,
        lambda m: (
            m.__setitem__("discourageImplicitMeasures", False),
            _add_col(m, "Product", {"name": "List Price", "dataType": "decimal"}),
            _add_col(m, "Product", {"name": "Legacy Code", "dataType": "int64"}),
            _add_col(m, "Product", {"name": "Weight", "dataType": "double", "summarizeBy": "none"}),
        ),
    )
    assert names(run("AUDIT_AI_IMPLICIT_MEASURES", base)) == ["List Price"]  # *Code, none excluded
    guarded = with_changes(base, lambda m: m.__setitem__("discourageImplicitMeasures", True))
    assert run("AUDIT_AI_IMPLICIT_MEASURES", guarded) == []


def test_ai5_duplicate_field_names(clean):
    dup = with_changes(
        clean,
        lambda m: (
            _add_col(m, "Product", {"name": "Name", "dataType": "string"}),
            _add_col(m, "Customer", {"name": "Name", "dataType": "string"}),
        ),
    )
    hits = run("AUDIT_AI_DUPLICATE_FIELD_NAMES", dup)
    assert {h[2] for h in hits} == {"Product", "Customer"}
    hidden = with_changes(
        dup, lambda m: table(m, "Customer")["columns"][-1].__setitem__("isHidden", True)
    )
    assert run("AUDIT_AI_DUPLICATE_FIELD_NAMES", hidden) == []


def test_ai6_cryptic_names(clean):
    m = with_changes(
        clean,
        lambda m: (
            _add_col(m, "Product", {"name": "TR_AMT", "dataType": "decimal"}),
            _add_col(m, "Product", {"name": "CustName", "dataType": "string"}),
            _add_col(m, "Product", {"name": "unit_price", "dataType": "decimal"}),
            _add_col(m, "Product", {"name": "ID", "dataType": "string"}),
            _add_col(m, "Product", {"name": "Net Weight", "dataType": "decimal"}),
            _add_col(m, "Product", {"name": "TR_HIDDEN", "dataType": "string", "isHidden": True}),
        ),
    )
    assert names(run("AUDIT_AI_CRYPTIC_NAMES", m)) == ["CustName", "TR_AMT", "unit_price"]


def test_rule_dicts_are_pure_functions_of_the_model(clean):
    snapshot = copy.deepcopy(clean)
    for rule_id in AUDIT_CHECKS:
        run(rule_id, clean)
    assert clean == snapshot
