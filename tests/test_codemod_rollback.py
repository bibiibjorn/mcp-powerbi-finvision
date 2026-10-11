"""P3a: 15_DAX_Refactor apply must persist edits + support rollback.

Two defects: (1) transforms' apply() was a no-op — the preview computed edits
but nothing wrote `after` back or deleted the target, so apply never changed
anything; (2) the transaction wrote a full before/after journal but nothing
ever replayed it, so there was no undo.

The apply now persists edits through an injectable persister (DAXInjector in
production), and `rollback(journal_path, persister)` replays each edit's
`before` expression to restore the pre-apply state.

Updated for the 2026-09-22 review (E-06/E-12/E-13): the plan hash comes from
preview (it covers transform + args), a delete needs a report-usage probe, and
rollback only accepts journals from the journal directory.
"""

import json

import pytest

from core.codemods import transaction as tx


class _Measure:
    def __init__(self, name, expression):
        self.name = name
        self.expression = expression


class _Table:
    def __init__(self, name, measures):
        self.name = name
        self.measures = measures


class _Model:
    def __init__(self, tables):
        self.tables = tables


class _RecordingPersister:
    """Stand-in for a legacy persister: records upsert/delete calls."""

    def __init__(self):
        self.upserts = []
        self.deletes = []

    def upsert_measure(self, *, table_name, measure_name, dax_expression, **kw):
        self.upserts.append((table_name, measure_name, dax_expression))
        return {"success": True}

    def delete_measure(self, *, table_name, measure_name, **kw):
        self.deletes.append((table_name, measure_name))
        return {"success": True}


@pytest.fixture(autouse=True)
def _journal_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_CODEMOD_JOURNAL_DIR", str(tmp_path))
    return tmp_path


def _no_visuals(table, measure):
    return {"checked": True, "visuals": [], "files": []}


def _hash(model, transform, args):
    return tx.preview(model, transform, args)["plan_hash"]


def test_apply_persists_edits_through_persister():
    base = _Measure("Base", "SUM(Sales[Amt])")
    derived = _Measure("Derived", "[Base] * 2")
    model = _Model([_Table("Sales", [base, derived])])
    persister = _RecordingPersister()
    args = {"measure_ref": "Sales[Base]", "delete_after": False}

    out = tx.apply(
        model, "inline_measure", args, _hash(model, "inline_measure", args), persister=persister
    )
    assert out["success"] is True, out
    assert any(
        u[1] == "Derived" and "SUM(Sales[Amt])" in u[2] for u in persister.upserts
    ), f"apply did not persist the inlined expression: {persister.upserts}"


def test_apply_delete_after_deletes_target():
    base = _Measure("Base", "SUM(Sales[Amt])")
    derived = _Measure("Derived", "[Base] * 2")
    model = _Model([_Table("Sales", [base, derived])])
    persister = _RecordingPersister()
    args = {"measure_ref": "Sales[Base]", "delete_after": True}
    out = tx.apply(
        model,
        "inline_measure",
        args,
        _hash(model, "inline_measure", args),
        persister=persister,
        visual_checker=_no_visuals,
    )
    assert out["success"] is True, out
    assert ("Sales", "Base") in persister.deletes


def test_rollback_replays_before_from_journal(_journal_dir):
    journal = {
        "ts": 0,
        "status": "committed",
        "identity": {"database": "db-1"},
        "transform": "inline_measure",
        "args": {},
        "plan_hash": "x",
        "edits": [
            {
                "target": "measure:Sales[Derived]",
                "before": "[Base] * 2",
                "after": "(SUM(Sales[Amt])) * 2",
                "note": "",
            },
        ],
        "deleted": [{"table": "Sales", "measure": "Base", "expression": "SUM(Sales[Amt])"}],
    }
    jpath = _journal_dir / "codemod-1.json"
    jpath.write_text(json.dumps(journal), encoding="utf-8")

    persister = _RecordingPersister()
    out = tx.rollback(str(jpath), persister=persister, identity="db-1")
    assert out["success"] is True, out
    assert ("Sales", "Derived", "[Base] * 2") in persister.upserts
    assert ("Sales", "Base", "SUM(Sales[Amt])") in persister.upserts


def test_rollback_missing_journal_errors():
    out = tx.rollback("/does/not/exist.json", persister=_RecordingPersister())
    assert out["success"] is False
    assert out["error_type"]
