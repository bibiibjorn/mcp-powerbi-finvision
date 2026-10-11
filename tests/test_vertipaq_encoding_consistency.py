"""Regression: every VertiPaq encoding decoder agrees on numeric codes.

Single source of truth: ``pbi_perf_core.vpa.ENCODING_NAMES`` (1 = HASH, 2 =
VALUE, settled by LIVE-CHECKS.md / review E-17). vertipaq_storage_report once
mapped 2 to RUN_LENGTH and later followed the wrong 1 = VALUE map.
"""

from core.dax.vertipaq_analyzer import encoding_label
from core.dax.vertipaq_storage_report import _normalize_encoding


def test_code_1_is_hash_in_both():
    assert encoding_label("1") == "hash"
    assert _normalize_encoding("1").upper() == "HASH"


def test_code_2_is_value_not_run_length():
    assert encoding_label("2") == "value"
    assert _normalize_encoding("2").upper() == "VALUE"


def test_string_aliases_still_work():
    assert _normalize_encoding("HASH").upper() == "HASH"
    assert _normalize_encoding("VALUE").upper() == "VALUE"
    assert _normalize_encoding("RLE").upper() in ("RUN_LENGTH", "RLE")


def test_empty_is_unknown():
    assert _normalize_encoding("") == "unknown"
    assert _normalize_encoding(None) == "unknown"
