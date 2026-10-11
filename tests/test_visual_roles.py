"""Tests for core.pbip.authoring.visual_roles."""

from __future__ import annotations

import pytest

from core.pbip.authoring.visual_roles import (
    VISUAL_ROLES,
    InvalidRole,
    UnknownVisualType,
    validate_bindings,
    validate_role,
)


class TestValidateRole:
    def test_card_visual_data_role_passes(self):
        validate_role("cardVisual", "Data")

    def test_line_chart_y_role_passes(self):
        validate_role("lineChart", "Y")

    def test_pivot_table_rows_values_columns_pass(self):
        validate_role("pivotTable", "Rows")
        validate_role("pivotTable", "Columns")
        validate_role("pivotTable", "Values")

    def test_wrong_role_for_card_raises(self):
        with pytest.raises(InvalidRole) as exc_info:
            validate_role("cardVisual", "Values")
        assert exc_info.value.allowed == ["Data"]
        assert "cardVisual" in str(exc_info.value)

    def test_unknown_visual_type_raises(self):
        with pytest.raises(UnknownVisualType):
            validate_role("notARealVisual", "Data")

    def test_role_is_case_sensitive(self):
        with pytest.raises(InvalidRole):
            validate_role("cardVisual", "data")  # lowercase rejected


class TestValidateBindings:
    def test_all_valid_roles_passes(self):
        ok, errors = validate_bindings("lineChart", ["Category", "Y"])
        assert ok is True
        assert errors == []

    def test_one_invalid_role_fails(self):
        ok, errors = validate_bindings("cardVisual", ["Data", "Legend"])
        assert ok is False
        assert len(errors) == 1
        assert "Legend" in errors[0]

    def test_unknown_visual_type_passes_softly(self):
        """An uncatalogued type must NOT block authoring.

        This registry is a guard rail against binding to a non-existent role,
        not an allowlist of shippable visuals — Power BI adds visual types
        faster than we catalogue them, and hard-failing an unknown type blocked
        valid work (the pre-2026-08 behaviour). Genuinely invalid roles on a
        KNOWN type still fail; see test_one_invalid_role_fails.
        """
        ok, errors = validate_bindings("nope", ["Foo"])
        assert ok is True
        assert errors == []

    def test_unknown_visual_type_surfaces_a_warning(self):
        from core.pbip.authoring.visual_roles import validate_bindings_ex

        ok, errors, warnings = validate_bindings_ex("nope", ["Foo"])
        assert ok is True
        assert errors == []
        # The warning must be actionable: name the type and where to look it up.
        assert len(warnings) == 1
        assert "nope" in warnings[0]
        assert "not in the roles registry" in warnings[0]
        assert "get_roles_for_type" in warnings[0]

    def test_empty_roles_list_passes(self):
        ok, errors = validate_bindings("cardVisual", [])
        assert ok is True
        assert errors == []


class TestRegistryShape:
    def test_all_types_have_list_value(self):
        for vt, roles in VISUAL_ROLES.items():
            assert isinstance(roles, list), f"{vt} roles must be a list"

    def test_data_bound_types_have_at_least_one_role(self):
        """Every type that binds data declares a role; the roleless ones are
        declared as such in NO_DATA_ROLE_TYPES rather than being an empty
        entry nobody vetted (buttons, shapes, navigators, scorecard, Q&A)."""
        from core.pbip.authoring.visual_roles import NO_DATA_ROLE_TYPES

        for vt, roles in VISUAL_ROLES.items():
            if vt in NO_DATA_ROLE_TYPES:
                assert roles == [], f"{vt} is declared roleless but has roles"
            else:
                assert len(roles) >= 1, f"{vt} should have >=1 role"

    def test_roleless_set_matches_registry_reality(self):
        """Guard against an empty-role entry sneaking in undeclared."""
        from core.pbip.authoring.visual_roles import NO_DATA_ROLE_TYPES

        empty = {vt for vt, roles in VISUAL_ROLES.items() if not roles}
        assert empty == set(NO_DATA_ROLE_TYPES) & set(VISUAL_ROLES)

    def test_common_types_present(self):
        # Sanity check on the types most likely to be used autonomously.
        for required in (
            "cardVisual",
            "lineChart",
            "donutChart",
            "pivotTable",
            "tableEx",
            "slicer",
        ):
            assert required in VISUAL_ROLES
