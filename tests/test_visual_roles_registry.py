"""Registry-vs-catalog coverage + soft-unknown-type contract for VISUAL_ROLES.

Companion to `test_visual_roles.py` (which covers the base validate_role /
validate_bindings behaviour). This file locks down the 2026-08 refresh:

- every visual type with a `docs/visual-catalog/<type>.md` file has a roles
  entry (assertion driven off the directory listing, so it self-maintains as
  catalog docs are added);
- the types that previously raised `UnknownVisualType` despite being perfectly
  valid now validate their documented roles;
- an unknown visual type is a WARNING, not a hard failure;
- a genuinely invalid role on a KNOWN type still errors, and `validate_role`
  still raises `InvalidRole`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Set

import pytest

from core.pbip.authoring.visual_roles import (
    NO_DATA_ROLE_TYPES,
    VISUAL_ROLES,
    InvalidRole,
    UnknownVisualType,
    is_known_visual_type,
    validate_bindings,
    validate_bindings_ex,
    validate_role,
)

CATALOG_DIR = Path(__file__).resolve().parents[1] / "docs" / "visual-catalog"

#: Catalog files whose H1 is NOT a PBIR `visualType` string, and which are
#: therefore exempt from the "must be in VISUAL_ROLES" rule.
#:  - buttonSlicer      : product/UI name; author as `advancedSlicerVisual`
#:                        (MS report-authoring reference). Kept in the registry
#:                        as a tolerated alias, but exemption documented here.
#:  - datePickerSlicer  : placeholder filename; the real type key is unverified,
#:                        so deliberately absent from the registry.
CATALOG_TYPE_EXEMPTIONS: Set[str] = {"datePickerSlicer"}

#: Roles the catalog documents but which we assert are bindable without raising.
PREVIOUSLY_BROKEN_TYPES: Dict[str, List[str]] = {
    "waterfallChart": ["Category", "Breakdown", "Y"],
    "funnel": ["Category", "Values"],
    "listSlicer": ["Values", "Tooltips"],
    "textSlicer": ["Values"],
    "multiRowCard": ["Values"],
    "ribbonChart": ["Category", "Legend", "Y"],
}


def _catalog_types() -> List[str]:
    """Visual-type names implied by docs/visual-catalog/*.md filenames."""
    types = []
    for md in sorted(CATALOG_DIR.glob("*.md")):
        stem = md.stem
        if stem == "INDEX.md" or stem == "INDEX":
            continue
        if stem.startswith("_"):  # _shared / _extracts helpers
            continue
        types.append(stem)
    return types


def _catalog_roles(visual_type: str) -> List[str]:
    """Roles listed in the `## Roles` table of a catalog doc."""
    md = CATALOG_DIR / f"{visual_type}.md"
    text = md.read_text(encoding="utf-8")
    block = text.split("## Roles", 1)
    if len(block) < 2:
        return []
    body = re.split(r"^## ", block[1], maxsplit=1, flags=re.MULTILINE)[0]
    return re.findall(r"^\|\s*`([^`]+)`", body, flags=re.MULTILINE)


class TestCatalogCoverage:
    def test_catalog_dir_exists_and_is_populated(self) -> None:
        # Guards against the glob silently matching nothing (which would make
        # every coverage assertion below vacuously pass).
        assert CATALOG_DIR.is_dir()
        assert len(_catalog_types()) >= 40

    def test_every_catalogued_type_has_roles_entry(self) -> None:
        missing = [
            vt
            for vt in _catalog_types()
            if vt not in CATALOG_TYPE_EXEMPTIONS and vt not in VISUAL_ROLES
        ]
        assert missing == [], (
            "docs/visual-catalog documents these types but VISUAL_ROLES has no "
            f"entry (validate_role would raise UnknownVisualType): {missing}"
        )

    def test_shared_and_index_files_are_excluded(self) -> None:
        types = _catalog_types()
        assert "INDEX" not in types
        assert not any(t.startswith("_") for t in types)

    @pytest.mark.parametrize(
        "visual_type",
        [vt for vt in _catalog_types() if vt not in CATALOG_TYPE_EXEMPTIONS],
    )
    def test_catalog_documented_roles_are_accepted(self, visual_type: str) -> None:
        """Roles the catalog documents must validate against the registry."""
        documented = _catalog_roles(visual_type)
        if not documented:
            pytest.skip(f"{visual_type} documents no roles")
        ok, errors = validate_bindings(visual_type, documented)
        assert ok, f"{visual_type}: catalog roles rejected by registry: {errors}"


class TestPreviouslyBrokenTypes:
    """These types raised UnknownVisualType before the 2026-08 refresh."""

    @pytest.mark.parametrize("visual_type,roles", sorted(PREVIOUSLY_BROKEN_TYPES.items()))
    def test_validate_role_no_longer_raises(self, visual_type: str, roles: List[str]) -> None:
        for role in roles:
            validate_role(visual_type, role)  # must not raise

    @pytest.mark.parametrize("visual_type,roles", sorted(PREVIOUSLY_BROKEN_TYPES.items()))
    def test_validate_bindings_clean(self, visual_type: str, roles: List[str]) -> None:
        ok, errors, warnings = validate_bindings_ex(visual_type, roles)
        assert ok is True
        assert errors == []
        assert warnings == []

    def test_all_are_known(self) -> None:
        for vt in PREVIOUSLY_BROKEN_TYPES:
            assert is_known_visual_type(vt)


class TestUnknownTypeIsSoft:
    def test_unknown_type_does_not_fail_validate_bindings(self) -> None:
        ok, errors = validate_bindings("someBrandNew2027Visual", ["Values", "Whatever"])
        assert ok is True, "an uncatalogued type must not block authoring"
        assert errors == []

    def test_unknown_type_surfaces_a_warning(self) -> None:
        ok, errors, warnings = validate_bindings_ex("someBrandNew2027Visual", ["Values"])
        assert ok is True
        assert errors == []
        assert len(warnings) == 1
        # The warning must be actionable — name the type and where to look.
        assert "someBrandNew2027Visual" in warnings[0]
        assert "docs/visual-catalog/someBrandNew2027Visual.md" in warnings[0]

    def test_known_type_with_bad_role_still_errors(self) -> None:
        ok, errors, warnings = validate_bindings_ex("cardVisual", ["Data", "Legend"])
        assert ok is False
        assert len(errors) == 1
        assert "Legend" in errors[0]
        assert warnings == []

    def test_validate_role_still_raises_invalid_role_on_known_type(self) -> None:
        with pytest.raises(InvalidRole) as exc:
            validate_role("cardVisual", "Legend")
        assert exc.value.allowed == ["Data"]

    def test_validate_role_still_raises_unknown_for_unknown_type(self) -> None:
        # validate_role keeps its raising contract (callers depend on it);
        # the SOFT behaviour lives in validate_bindings / _ex.
        with pytest.raises(UnknownVisualType):
            validate_role("someBrandNew2027Visual", "Values")

    def test_exception_class_still_exported(self) -> None:
        assert issubclass(UnknownVisualType, ValueError)


class TestRegistryShape:
    def test_all_values_are_lists_of_str(self) -> None:
        for vt, roles in VISUAL_ROLES.items():
            assert isinstance(roles, list), f"{vt} roles must be a list"
            assert all(isinstance(r, str) for r in roles), f"{vt} roles must be strings"

    def test_no_duplicate_roles_within_a_type(self) -> None:
        for vt, roles in VISUAL_ROLES.items():
            assert len(roles) == len(set(roles)), f"{vt} has duplicate roles"

    def test_empty_role_lists_are_declared_no_data_types(self) -> None:
        """An empty list must be intentional, not a forgotten entry."""
        empty = {vt for vt, roles in VISUAL_ROLES.items() if not roles}
        undeclared = empty - set(NO_DATA_ROLE_TYPES)
        assert (
            undeclared == set()
        ), f"types with no roles but not in NO_DATA_ROLE_TYPES: {sorted(undeclared)}"

    def test_no_data_role_types_are_all_registered_and_empty(self) -> None:
        for vt in NO_DATA_ROLE_TYPES:
            assert vt in VISUAL_ROLES, f"{vt} declared no-data but missing from registry"
            assert VISUAL_ROLES[vt] == [], f"{vt} declared no-data but has roles"

    def test_2026_slicer_family_present(self) -> None:
        for vt in (
            "slicer",
            "advancedSlicerVisual",
            "listSlicer",
            "textSlicer",
            "filterSlicer",
            "buttonSlicer",
        ):
            assert vt in VISUAL_ROLES
            assert "Values" in VISUAL_ROLES[vt]

    def test_date_picker_deliberately_absent(self) -> None:
        """Its real visualType is unverified — a wrong key would be worse."""
        assert "datePickerSlicer" not in VISUAL_ROLES

    def test_registry_covers_more_than_the_pre_refresh_30(self) -> None:
        assert len(VISUAL_ROLES) > 30
