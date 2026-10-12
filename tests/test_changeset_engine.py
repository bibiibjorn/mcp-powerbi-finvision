"""Changeset engine: validation + safe dependency ordering (Phase 5).

Pure logic — no AMO. The engine validates a grouped-by-object_type changeset
document and flattens it into a dependency-safe operation sequence:
creates (tables -> columns -> relationships), then updates, then deletes in
reverse-dependency order (relationships -> columns -> tables).
"""

from core.operations.changeset_engine import validate_changeset, order_operations

# --- validation --------------------------------------------------------------


def test_validate_accepts_well_formed_changeset():
    doc = {
        "columns": {"create": [{"table": "Sales", "name": "Margin", "expression": "[Rev]-[Cost]"}]},
        "tables": {"delete": [{"table": "Old"}]},
    }
    errors = validate_changeset(doc)
    assert errors == []


def test_validate_rejects_unknown_object_type():
    errors = validate_changeset({"widgets": {"create": [{}]}})
    assert any("unknown object_type" in e.lower() for e in errors)


def test_validate_rejects_measures_with_pointer():
    # measures are not supported in the atomic changeset (see engine docstring).
    errors = validate_changeset({"measures": {"create": [{}]}})
    assert any("unknown object_type" in e.lower() for e in errors)


def test_validate_rejects_unknown_op_kind():
    errors = validate_changeset({"columns": {"frobnicate": [{}]}})
    assert any("frobnicate" in e for e in errors)


def test_validate_rejects_non_list_op_value():
    errors = validate_changeset({"columns": {"create": {"not": "a list"}}})
    assert any("must be a list" in e.lower() for e in errors)


# --- ordering ----------------------------------------------------------------


def test_create_order_is_dependency_safe():
    doc = {
        "relationships": {"create": [{"name": "r1"}]},
        "columns": {"create": [{"table": "S", "name": "c1"}]},
        "tables": {"create": [{"table": "S"}]},
    }
    ops = order_operations(doc)
    kinds = [(ot, op) for ot, op, _item in ops]
    # tables -> columns -> relationships for creates.
    assert kinds == [
        ("tables", "create"),
        ("columns", "create"),
        ("relationships", "create"),
    ]


def test_deletes_run_after_creates_and_in_reverse_dependency_order():
    doc = {
        "tables": {"create": [{"table": "New"}], "delete": [{"table": "Old"}]},
        "relationships": {"delete": [{"name": "r"}]},
        "columns": {"delete": [{"table": "S", "name": "c"}]},
    }
    ops = order_operations(doc)
    kinds = [(ot, op) for ot, op, _item in ops]
    # create first
    assert kinds[0] == ("tables", "create")
    # deletes after, reverse-dependency: relationships -> columns -> tables
    delete_kinds = [k for k in kinds if k[1] == "delete"]
    assert delete_kinds == [
        ("relationships", "delete"),
        ("columns", "delete"),
        ("tables", "delete"),
    ]


def test_updates_run_between_creates_and_deletes():
    doc = {
        "columns": {
            "create": [{"table": "S", "name": "c"}],
            "update": [{"table": "S", "name": "u"}],
            "delete": [{"table": "S", "name": "d"}],
        }
    }
    ops = order_operations(doc)
    ops_seq = [op for _ot, op, _item in ops]
    assert ops_seq == ["create", "update", "delete"]
