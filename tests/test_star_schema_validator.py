"""Tests for the star-schema / topology validator (Phase 3.1).

The validator takes a model dict ({tables, relationships}) and returns a list of
topology findings. It is pure-Python and model-shape-driven so it can be unit
tested without a live connection.

Relationship dict shape mirrors analysis_orchestrator.list_relationships_simple:
    fromTable, fromColumn, toTable, toColumn,
    fromCardinality ('One'|'Many'), toCardinality ('One'|'Many'),
    isActive (bool), crossFilteringBehavior, name

Convention used by the validator: a relationship points from the MANY side
(fact / child) to the ONE side (dimension / parent), i.e. fromCardinality=='Many'
and toCardinality=='One' is the canonical star edge fromTable->toTable.
"""

from core.analysis.star_schema_validator import (
    StarSchemaValidator,
    TopologyFinding,
)


def _rel(from_t, to_t, from_card="Many", to_card="One", active=True):
    return {
        "fromTable": from_t,
        "fromColumn": f"{to_t}Key",
        "toTable": to_t,
        "toColumn": f"{to_t}Key",
        "fromCardinality": from_card,
        "toCardinality": to_card,
        "isActive": active,
        "crossFilteringBehavior": "OneDirection",
        "name": f"{from_t}_{to_t}",
    }


def _table(name, **kw):
    t = {"name": name, "columns": kw.get("columns", [])}
    t.update({k: v for k, v in kw.items() if k != "columns"})
    return t


def _ids(findings):
    return {f.finding_id for f in findings}


# ── Clean star schema produces no topology findings ─────────────────────────


def test_clean_star_schema_is_clean():
    model = {
        "tables": [
            _table("Sales"),
            _table("Date"),
            _table("Product"),
            _table("Customer"),
        ],
        "relationships": [
            _rel("Sales", "Date"),
            _rel("Sales", "Product"),
            _rel("Sales", "Customer"),
        ],
    }
    findings = StarSchemaValidator().validate(model)
    assert _ids(findings) == set(), f"clean star should be clean, got {findings}"


# ── Fact-to-fact relationship ───────────────────────────────────────────────


def test_fact_to_fact_relationship_flagged():
    # Two fact tables (each is the Many-side of links to shared dims) joined
    # DIRECTLY to each other -> fact-to-fact anti-pattern.
    model = {
        "tables": [_table("Sales"), _table("Returns"), _table("Date"), _table("Product")],
        "relationships": [
            _rel("Sales", "Date"),
            _rel("Sales", "Product"),
            _rel("Returns", "Date"),
            _rel("Returns", "Product"),
            # the offending direct fact<->fact edge
            _rel("Returns", "Sales", from_card="Many", to_card="Many"),
        ],
    }
    findings = StarSchemaValidator().validate(model)
    assert "FACT_TO_FACT" in _ids(findings)
    f = next(f for f in findings if f.finding_id == "FACT_TO_FACT")
    assert f.severity in {"high", "critical"}
    assert "Sales" in f.object_name or "Returns" in f.object_name


# ── Snowflake dimension (dim -> dim -> fact chain) ──────────────────────────


def test_snowflake_dimension_flagged():
    # ProductCategory connects to fact only via Product (dim->dim->fact).
    model = {
        "tables": [_table("Sales"), _table("Product"), _table("ProductCategory"), _table("Date")],
        "relationships": [
            _rel("Sales", "Date"),
            _rel("Sales", "Product"),
            _rel("Product", "ProductCategory"),  # snowflake leg
        ],
    }
    findings = StarSchemaValidator().validate(model)
    assert "SNOWFLAKE_DIMENSION" in _ids(findings)
    f = next(f for f in findings if f.finding_id == "SNOWFLAKE_DIMENSION")
    assert "ProductCategory" in f.object_name


# ── Disconnected table ──────────────────────────────────────────────────────


def test_disconnected_table_flagged():
    model = {
        "tables": [_table("Sales"), _table("Date"), _table("Orphan")],
        "relationships": [_rel("Sales", "Date")],
    }
    findings = StarSchemaValidator().validate(model)
    assert "DISCONNECTED_TABLE" in _ids(findings)
    assert any(f.object_name == "Orphan" for f in findings if f.finding_id == "DISCONNECTED_TABLE")


def test_disconnected_does_not_flag_calc_group_or_field_param():
    # Calc groups and field-parameter tables are disconnected BY DESIGN.
    model = {
        "tables": [
            _table("Sales"),
            _table("Date"),
            _table("Time Intelligence", dataCategory=None, isCalculationGroup=True),
            _table("Metric", isFieldParameter=True),
        ],
        "relationships": [_rel("Sales", "Date")],
    }
    findings = StarSchemaValidator().validate(model)
    flagged = {f.object_name for f in findings if f.finding_id == "DISCONNECTED_TABLE"}
    assert "Time Intelligence" not in flagged
    assert "Metric" not in flagged


# ── Missing date table ──────────────────────────────────────────────────────


def test_missing_date_table_flagged():
    model = {
        "tables": [_table("Sales"), _table("Product")],
        "relationships": [_rel("Sales", "Product")],
    }
    findings = StarSchemaValidator().validate(model)
    assert "MISSING_DATE_TABLE" in _ids(findings)


def test_date_table_detected_by_name_is_clean():
    model = {
        "tables": [_table("Sales"), _table("Calendar")],
        "relationships": [_rel("Sales", "Calendar")],
    }
    findings = StarSchemaValidator().validate(model)
    assert "MISSING_DATE_TABLE" not in _ids(findings)


def test_date_table_detected_by_data_category_is_clean():
    model = {
        "tables": [_table("Sales"), _table("DimDate", dataCategory="Time")],
        "relationships": [_rel("Sales", "DimDate")],
    }
    findings = StarSchemaValidator().validate(model)
    assert "MISSING_DATE_TABLE" not in _ids(findings)


# ── Robustness ──────────────────────────────────────────────────────────────


def test_empty_model_does_not_crash():
    findings = StarSchemaValidator().validate({"tables": [], "relationships": []})
    # No tables at all -> nothing to validate; just must not raise.
    assert isinstance(findings, list)


def test_findings_are_topology_finding_instances():
    model = {
        "tables": [_table("Sales"), _table("Date"), _table("Orphan")],
        "relationships": [_rel("Sales", "Date")],
    }
    findings = StarSchemaValidator().validate(model)
    assert all(isinstance(f, TopologyFinding) for f in findings)
    for f in findings:
        assert f.recommendation, "every finding must carry an actionable recommendation"
