from core.codemods import transaction as tx
from core.codemods.transforms.move_measure_batch import MoveMeasureBatch


class _M:
    def __init__(self, name, expr, table):
        self.name = name
        self.expression = expr
        self.table_name = table


class _T:
    def __init__(self, name, ms):
        self.name = name
        self.measures = ms
        self.columns = []


class _Mod:
    def __init__(self):
        self.tables = [
            _T(
                "Sales",
                [
                    _M("Total Sales", "SUM(Sales[Amt])", "Sales"),
                    _M("Avg Sales", "DIVIDE([Total Sales], COUNTROWS(Sales))", "Sales"),
                ],
            ),
            _T("Reports", []),
        ]
        self.relationships = []


def test_move_batch_orders_independent_first():
    res = MoveMeasureBatch().preview(
        _Mod(),
        {
            "moves": [
                {"measure_ref": "Sales[Avg Sales]", "target_table": "Reports"},
                {"measure_ref": "Sales[Total Sales]", "target_table": "Reports"},
            ],
        },
    )
    targets = [e.target for e in res.edits if e.note == "move"]
    assert targets.index("measure:Sales[Total Sales]") < targets.index("measure:Sales[Avg Sales]")


def test_move_batch_warns_on_unknown_measure():
    res = MoveMeasureBatch().preview(
        _Mod(),
        {"moves": [{"measure_ref": "Sales[Nope]", "target_table": "Reports"}]},
    )
    assert any("not found" in w.lower() for w in res.warnings)


class _RecordingPersister:
    """Records upsert/delete calls so we can prove markers are never written."""

    def __init__(self):
        self.upserts = []
        self.deletes = []

    def upsert_measure(self, *, table_name, measure_name, dax_expression, **kw):
        self.upserts.append((table_name, measure_name, dax_expression))
        return {"success": True}

    def delete_measure(self, *, table_name, measure_name, **kw):
        self.deletes.append((table_name, measure_name))
        return {"success": True}


def test_apply_never_persists_move_markers():
    """Regression: move_measure_batch emits marker edits (after='table=Dst',
    note='move'). apply must NOT upsert them as measure DAX (that overwrote the
    measure body with the literal 'table=Dst' string) and must fail structured.
    """
    model = _Mod()
    persister = _RecordingPersister()
    plan_hash = tx.compute_plan_hash(model)

    out = tx.apply(
        model,
        "move_measure_batch",
        {"moves": [{"measure_ref": "Sales[Total Sales]", "target_table": "Reports"}]},
        plan_hash,
        persister=persister,
    )

    assert out["success"] is False
    assert out["error_type"] == "transform_not_persistable"
    assert out["applied_count"] == 0
    # NOTHING was written — no measure body clobbered with 'table=...'.
    assert persister.upserts == []
    assert persister.deletes == []
    # The message points at the real move ops.
    assert "measure/move" in out["error"] or "measures/move" in out["error"]


def test_apply_marker_guard_leaves_no_corrupt_expression():
    """Even defensively, no persisted expression may be a 'table=' marker."""
    model = _Mod()
    persister = _RecordingPersister()
    tx.apply(
        model,
        "move_measure_batch",
        {
            "moves": [
                {"measure_ref": "Sales[Total Sales]", "target_table": "Reports"},
                {"measure_ref": "Sales[Avg Sales]", "target_table": "Reports"},
            ]
        },
        tx.compute_plan_hash(model),
        persister=persister,
    )
    assert not any(str(expr).startswith("table=") for _, _, expr in persister.upserts)


def test_rollback_skips_move_markers(tmp_path, monkeypatch):
    """A journal that somehow carried move markers must never replay them as
    measure expressions on rollback."""
    import json

    journal = {
        "ts": 0,
        "transform": "move_measure_batch",
        "args": {},
        "plan_hash": "x",
        "edits": [
            {
                "target": "measure:Sales[Total Sales]",
                "before": "table=Sales",
                "after": "table=Reports",
                "note": "move",
            }
        ],
        "deleted": [],
    }
    # E-13: journals are only accepted from the journal directory, and a
    # journal without database identity needs force=true.
    monkeypatch.setenv("MCP_CODEMOD_JOURNAL_DIR", str(tmp_path))
    jpath = tmp_path / "codemod-move.json"
    jpath.write_text(json.dumps(journal), encoding="utf-8")

    persister = _RecordingPersister()
    out = tx.rollback(str(jpath), persister=persister, force=True)
    assert out["success"] is True
    # No 'table=...' string was written back as an expression.
    assert persister.upserts == []
