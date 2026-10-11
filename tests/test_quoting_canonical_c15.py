"""C-15: the DAX / TMDL identifier-quoting copies agree with the canonical helpers.

``core/dax/dax_names`` (DAX) and ``core/tmdl/tmdl_tree.tmdl_name`` (TMDL) are the
implementations; ``vpa_extractor``, ``DaxValidator.escape_identifier`` and the
calc-group scaffold now delegate to them, and the stdlib-only ``client_guard``
detector keeps a local copy that must produce identical text.
"""

from __future__ import annotations

import pytest

from core.dax import dax_names
from core.tmdl import tmdl_tree

TRICKY = [
    "Sales",
    "Net Sales",
    "O'Brien",
    "''",
    "a]b",
    "]]",
    "[x]",
    'He said "hi"',
    "Straße €",
    "日本語",
    "Zoë's Table",
    "1st",
    "a.b",
    "x=y",
    "k:v",
    " lead",
    "trail ",
    "",
]


@pytest.mark.parametrize("name", TRICKY)
def test_client_guard_copies_match_canonical(name):
    from core.client_guard import detector

    assert detector._q_table(name) == dax_names.quote_table(name)
    assert detector._q_column(name) == dax_names.bracket(name)


@pytest.mark.parametrize("name", TRICKY)
def test_vpa_extractor_quoting_matches_canonical(name):
    from core.dax import vpa_extractor as v

    assert v._quote_table(name) == dax_names.quote_table(name)
    assert v._quote_column(name, "c") == dax_names.column_ref(name, "c")
    assert v._quote_column("T", name) == dax_names.column_ref("T", name)
    # the pre-C-15 inline implementations, byte for byte
    assert v._quote_table(name) == "'" + str(name).replace("'", "''") + "'"
    assert v._quote_column("T", name) == f"'T'[{str(name).replace(']', ']]')}]"


@pytest.mark.parametrize("name", [n for n in TRICKY if n.strip()])
def test_dax_validator_escape_identifier_matches_canonical(name):
    from core.dax.dax_validator import DaxValidator

    assert DaxValidator.escape_identifier(name) == dax_names.quote_table(name)
    assert DaxValidator.escape_identifier(name) == f"'{name.replace(chr(39), chr(39) * 2)}'"


def test_dax_validator_escape_identifier_still_rejects_invalid():
    from core.dax.dax_validator import DaxValidator

    for bad in ("", "   ", "a\0b", "x" * 129):
        with pytest.raises(ValueError):
            DaxValidator.escape_identifier(bad)


@pytest.mark.parametrize("name", TRICKY)
def test_scaffold_tmdl_name_is_the_canonical_one(name):
    from core.calc_group_migration import tmdl_scaffold

    assert tmdl_scaffold.tmdl_name is tmdl_tree.tmdl_name
    # the pre-C-15 scaffold implementation
    import re

    expected = (
        name if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name) else "'" + name.replace("'", "''") + "'"
    )
    assert tmdl_tree.tmdl_name(name) == expected


def test_visual_query_builder_unbracket_delegates():
    from core.debug.visual_query_builder import VisualQueryBuilder

    for name in TRICKY:
        assert VisualQueryBuilder._unbracket(dax_names.bracket(name)) == dax_names.unbracket(
            dax_names.bracket(name)
        )
