"""Deep review 2026-10-03, slice N: N-30 - the model-reload helper timeout and TOCTOU.

A subprocess timeout of ``reload-model`` is NOT "nothing happened" (Desktop may already have
applied the definition), and the on-disk TMDL must be unchanged between the classifier verdict and
the reload. Never touches a Desktop; the node helper is faked like ``test_bridge_model_reload``.
"""

from __future__ import annotations

import subprocess

import pytest

from core.autonomous import desktop_bridge as db
from core.autonomous import edit_ledger
from core.autonomous import model_change_classifier as mcc
from tests.test_bridge_model_reload import Rig, _inst, _metadata_edit, _project


@pytest.fixture
def proj(tmp_path):
    return _project(tmp_path)


@pytest.fixture
def lib_target(monkeypatch):
    monkeypatch.setattr(
        db,
        "_bridge_lib_target",
        lambda: {"node": "node", "package_dir": "/pkg", "version": "1.0.0"},
    )


def _timeout(*a, **k):
    raise subprocess.TimeoutExpired(cmd="node", timeout=5)


def test_reload_model_helper_timeout_is_outcome_unknown(monkeypatch, lib_target):
    monkeypatch.setattr(subprocess, "run", _timeout)
    res = db._exec_bridge_lib("reload-model", ["--pid", "7"], timeout=5)
    assert res["ok"] is False and res["error_type"] == "timeout_outcome_unknown"
    assert "may already have" in res["error"]


def test_other_helper_commands_keep_the_plain_timeout(monkeypatch, lib_target):
    monkeypatch.setattr(subprocess, "run", _timeout)
    res = db._exec_bridge_lib("capture-regions", [], timeout=5)
    assert res["error_type"] == "timeout"


def test_op_reload_verifies_the_model_after_a_helper_timeout(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    _metadata_edit(rig)
    edit_ledger.record_disk_write(str(proj["tmdl"]))
    rig.lib_result = {
        "ok": False,
        "error_type": "timeout_outcome_unknown",
        "error": "bridge library helper `reload-model` exceeded 5s; Desktop may already have "
        "applied the model reload",
    }
    out = db.op_reload(pid=7, include_model=True)
    assert out["success"] is False and out["error_type"] == "timeout_outcome_unknown"
    assert out["model_reload"]["verification"] in ("verified", "mismatch", "unavailable")
    assert out["include_model"] is True
    # the pending TMDL edit is NOT cleared: nothing proved it was applied
    left = edit_ledger.pending_disk_edits([str(proj["report"])], [str(proj["model"])])
    assert [e["kind"] for e in left] == ["tmdl"]


def test_tmdl_changed_between_classification_and_reload_is_refused(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    _metadata_edit(rig)
    tmdl = proj["model"] / "definition" / "model.tmdl"
    real = mcc._read_disk_signature  # the Rig's fake

    def sneaky(ddir):
        out = real(ddir)
        tmdl.write_text("model Model\n\tannotation X = 1\n", encoding="utf-8")  # agent edit
        return out

    monkeypatch.setattr(mcc, "_read_disk_signature", sneaky)
    out = db.op_reload(pid=7, include_model=True)
    assert out["success"] is False and out["error_type"] == mcc.ERR_UNCLASSIFIED
    assert "changed" in out["error"] and rig.lib_calls == []


def test_unchanged_tmdl_still_reloads(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    _metadata_edit(rig)
    out = db.op_reload(pid=7, include_model=True)
    assert out["success"] is True and [c[0] for c in rig.lib_calls] == ["reload-model"]
