"""S-21: after a SUCCESSFUL measure save the stored expression is compared with the one sent.

``_measure_landed`` used to run only after SaveChanges raised, so a normalisation on store
(e.g. a non-ASCII literal) went unnoticed. A mismatch now surfaces as
``expression_changed_on_store`` (sha of both + the first differing code point).
"""

from __future__ import annotations

from types import SimpleNamespace

from core.operations import measure_crud_manager as mcm
from core.operations import model_readback


def _stored(monkeypatch, expression):
    monkeypatch.setattr(
        model_readback,
        "find_measure",
        lambda table, name: {"expression": expression, "table": table, "name": name},
    )


def test_matching_expression_returns_none(monkeypatch):
    _stored(monkeypatch, "SUM ( Sales[Amount] )")
    assert mcm._expression_round_trip("Sales", "M", "SUM ( Sales[Amount] )") is None


def test_line_endings_and_outer_whitespace_are_ignored(monkeypatch):
    _stored(monkeypatch, "VAR x = 1\nRETURN x")
    assert mcm._expression_round_trip("Sales", "M", "  VAR x = 1\r\nRETURN x \r\n") is None


def test_a_normalised_non_ascii_character_is_reported(monkeypatch):
    _stored(monkeypatch, 'SELECTEDVALUE ( T[c], "a b" )')  # U+2004 stored as a plain space
    out = mcm._expression_round_trip("Sales", "M", 'SELECTEDVALUE ( T[c], "a b" )')
    assert out["first_diff"] == {
        "index": 24,
        "sent_codepoint": "U+2004",
        "stored_codepoint": "U+0020",
    }
    assert out["sent_sha"] != out["stored_sha"] and len(out["sent_sha"]) == 12
    assert "UNICHAR" in out["message"]


def test_unreadable_or_missing_measure_is_silent(monkeypatch):
    monkeypatch.setattr(model_readback, "find_measure", lambda t, n: None)
    assert mcm._expression_round_trip("S", "M", "1") is None
    monkeypatch.setattr(model_readback, "find_measure", lambda t, n: {})
    assert mcm._expression_round_trip("S", "M", "1") is None
    assert mcm._expression_round_trip("S", "M", None) is None


def test_single_write_attaches_the_warning_after_a_clean_save(monkeypatch):
    mgr = object.__new__(mcm.MeasureCRUDManager)
    monkeypatch.setattr(mcm, "AMO_AVAILABLE", True)
    server = SimpleNamespace(Disconnect=lambda: None)
    monkeypatch.setattr(
        mgr, "_get_server_db_model", lambda: (server, None, object()), raising=False
    )
    monkeypatch.setattr(mcm, "commit", lambda model, label, verify=None: {})
    _stored(monkeypatch, "ROW(1)")
    out = mgr._single_write(
        "measure/update",
        lambda model: {"success": True},
        roundtrip=lambda: mcm._expression_round_trip("S", "M", "ROW(2)"),
    )
    assert out["success"] is True
    assert out["expression_changed_on_store"]["first_diff"]["index"] == 4


def test_single_write_without_a_mismatch_adds_nothing(monkeypatch):
    mgr = object.__new__(mcm.MeasureCRUDManager)
    monkeypatch.setattr(mcm, "AMO_AVAILABLE", True)
    server = SimpleNamespace(Disconnect=lambda: None)
    monkeypatch.setattr(
        mgr, "_get_server_db_model", lambda: (server, None, object()), raising=False
    )
    monkeypatch.setattr(mcm, "commit", lambda model, label, verify=None: {})
    _stored(monkeypatch, "ROW(1)")
    out = mgr._single_write(
        "measure/update",
        lambda model: {"success": True},
        roundtrip=lambda: mcm._expression_round_trip("S", "M", "ROW(1)"),
    )
    assert "expression_changed_on_store" not in out
