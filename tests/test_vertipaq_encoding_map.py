"""VertiPaq COLUMN_ENCODING code mapping.

History: two modules once decoded the SAME code differently; the 2026-05 audit
"fixed" it to 1 = VALUE / 2 = HASH, which contradicted ``pbi_perf_core.vpa``
(the 04 vertipaq_analyzer op). The 2026-09-22 live check (LIVE-CHECKS.md, review
E-17) settled it on a real model: every DBTYPE_WSTR BASIC_DATA column (text is
always HASH encoded) reports COLUMN_ENCODING = 1. So 1 = HASH, 2 = VALUE, and
every module derives its map from ``pbi_perf_core.vpa.ENCODING_NAMES``.
"""

from __future__ import annotations

from core.dax.vertipaq_analyzer import ENCODING_CODE_MAP, encoding_label


def test_code_1_is_hash():
    assert encoding_label("1").upper() == "HASH"


def test_code_2_is_value():
    assert encoding_label("2").upper() == "VALUE"


def test_shared_constant_matches_live_check_and_pbi_perf_core():
    from pbi_perf_core.vpa import ENCODING_NAMES

    assert ENCODING_CODE_MAP["1"].upper() == "HASH" == ENCODING_NAMES[1]
    assert ENCODING_CODE_MAP["2"].upper() == "VALUE" == ENCODING_NAMES[2]


def test_reporter_uses_the_shared_map():
    from core.dax import vertipaq_reporter

    assert vertipaq_reporter.encoding_label("1").upper() == "HASH"
    assert vertipaq_reporter.encoding_label("2").upper() == "VALUE"


def test_unknown_code_is_passthrough_not_crash():
    label = encoding_label("7")
    assert label  # non-empty, does not raise
