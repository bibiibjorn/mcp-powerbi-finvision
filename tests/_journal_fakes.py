"""Fakes for the write-journal tests (JR-01). Not a test module.

``LiveModel`` is the "server": one committed TMSL dict. ``FakeTom`` is one client connection to it -
a pending (mutated) TMSL dict that ``SaveChanges`` commits back. ``install`` patches the journal's
CLR seams (serialise / read-committed / connect / apply) so the whole record -> undo -> replay ->
checkpoint flow runs in pure Python. The real TOM applier is covered by test_write_journal_tom.py.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any, Dict, List

from core.operations import journal_images as images

PROJECT = "C:/Repo/Proj/Proj.pbip"
#: the artifact folders a verified save of PROJECT reports (mark_flushed matches strictly, so an
#: empty set no longer flushes everything)
REPORTS = {"c:/repo/proj/proj.report"}
MODELS = {"c:/repo/proj/proj.semanticmodel"}

BASE_TMSL: Dict[str, Any] = {
    "culture": "en-US",
    "tables": [
        {
            "name": "Sales",
            "lineageTag": "tag-sales",
            "columns": [
                {
                    "name": "Amount",
                    "dataType": "double",
                    "sourceColumn": "Amount",
                    "lineageTag": "tag-amount",
                },
                {
                    "name": "Qty",
                    "dataType": "int64",
                    "sourceColumn": "Qty",
                    "lineageTag": "tag-qty",
                },
            ],
            "measures": [
                {
                    "name": "Total",
                    "expression": "SUM(Sales[Amount])",
                    "formatString": "0.00",
                    "displayFolder": "Base",
                    "lineageTag": "tag-total",
                },
                {"name": "Count", "expression": "COUNTROWS(Sales)", "lineageTag": "tag-count"},
            ],
            "partitions": [
                {"name": "Sales", "source": {"type": "m", "expression": "let x = 1 in x"}}
            ],
        }
    ],
    "roles": [{"name": "Reader", "modelPermission": "read"}],
}


class LiveModel:
    """The committed model shared by every connection."""

    def __init__(self, tmsl: Dict[str, Any] = None):
        self.tmsl = copy.deepcopy(tmsl if tmsl is not None else BASE_TMSL)
        self.saves = 0
        self.stamp = 0
        self.reads = 0  # committed-model reads by the journal (fetch_committed)

    def connect(self) -> "FakeTom":
        return FakeTom(self)


class FakeTom:
    def __init__(self, live: LiveModel):
        self.live = live
        self.tmsl = copy.deepcopy(live.tmsl)
        self.HasLocalChanges = True
        self.Database = SimpleNamespace(Name="db1", ID="id-db1")
        self.ModifiedTime = f"T{live.stamp}"
        self.StructureModifiedTime = f"S{live.stamp}"
        self.fail: Exception = None
        self.undone = 0

    # ---- TOM surface used by write_lane.commit ---------------------------------
    def SaveChanges(self) -> None:
        if self.fail is not None:
            raise self.fail
        self.live.tmsl = copy.deepcopy(self.tmsl)
        self.live.saves += 1
        self.live.stamp += 1
        self.ModifiedTime = f"T{self.live.stamp}"
        self.StructureModifiedTime = f"S{self.live.stamp}"

    def UndoLocalChanges(self) -> None:
        self.tmsl = copy.deepcopy(self.live.tmsl)
        self.undone += 1

    # ---- mutation helpers -----------------------------------------------------
    def table(self, name: str) -> Dict[str, Any]:
        return next(t for t in self.tmsl["tables"] if t["name"] == name)

    def measure(self, table: str, name: str) -> Dict[str, Any]:
        return next(m for m in self.table(table)["measures"] if m["name"] == name)


def canon(tmsl: Dict[str, Any]) -> Dict[str, Any]:
    """Order-insensitive form of a TMSL dict (lists of named objects sorted by name)."""

    def walk(v):
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            items = [walk(x) for x in v]
            if items and all(isinstance(x, dict) and "name" in x for x in items):
                items.sort(key=lambda x: str(x["name"]))
            return items
        return v

    return walk(tmsl)


def measure_expr(live: LiveModel, table: str, name: str) -> str:
    t = next(t for t in live.tmsl["tables"] if t["name"] == table)
    return next(m for m in t["measures"] if m["name"] == name)["expression"]


def measure_names(live: LiveModel, table: str = "Sales") -> List[str]:
    t = next(t for t in live.tmsl["tables"] if t["name"] == table)
    return sorted(m["name"] for m in t["measures"])


# ---------------------------------------------------------------------------
# image map <-> TMSL (the inverse of journal_images.split_model), for the fake applier
# ---------------------------------------------------------------------------
_CHILD_KEY = {
    "column": "columns",
    "measure": "measures",
    "partition": "partitions",
    "hierarchy": "hierarchies",
}


def join_images(mp: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    model = copy.deepcopy(mp["model"]["json"]) if "model" in mp else {}
    tables: Dict[str, Dict[str, Any]] = {}
    for img in mp.values():
        if img["kind"] == "table":
            tables[img["name"]] = copy.deepcopy(img["json"])
    for img in mp.values():
        kind = img["kind"]
        if kind in _CHILD_KEY:
            tables[img["table"]].setdefault(_CHILD_KEY[kind], []).append(copy.deepcopy(img["json"]))
        elif kind == "calcitem":
            cg = tables[img["table"]].setdefault("calculationGroup", {})
            cg.setdefault("calculationItems", []).append(copy.deepcopy(img["json"]))
    model["tables"] = list(tables.values())
    for key, kind in images.TOP_LEVEL_KINDS.items():
        objs = [copy.deepcopy(i["json"]) for i in mp.values() if i["kind"] == kind]
        if objs:
            model[key] = objs
    return model


def fake_apply_ops(model: FakeTom, ops: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure-Python stand-in for journal_apply.apply_ops (same op semantics)."""
    mp = images.split_model(model.tmsl)
    for op in ops:
        kind, table = op["kind"], op.get("table")
        loc = images.make_ref(kind, table, op.get("locate_name") or op["name"])
        if op["action"] == "remove":
            mp.pop(loc, None)
            if kind == "table":  # the real TOM drops a table's children with it
                for ref in [r for r, i in mp.items() if i.get("table") == op["name"]]:
                    mp.pop(ref)
        elif op["action"] == "add" and kind == "table":
            extra = images.split_model({"tables": [copy.deepcopy(op["json"])]})
            extra.pop("model")
            mp.update(extra)
        else:  # add / update (in place, replace or rename)
            if op["action"] == "update":
                mp.pop(loc, None)
            name = "model" if kind == "model" else (op["json"].get("name") or op["name"])
            mp[images.make_ref(kind, table, name)] = {
                "kind": kind,
                "table": table,
                "name": name,
                "json": copy.deepcopy(op["json"]),
            }
    model.tmsl = join_images(mp)
    return {"applied": len(ops), "warnings": []}


def _resolve_project(monkeypatch) -> None:
    """Let the fake PROJECT (not on disk) resolve to REPORTS / MODELS for strict matching."""
    from core.autonomous import edit_ledger

    real = edit_ledger.project_artifacts

    def fake(path):
        if path and edit_ledger.norm_path(path) == edit_ledger.norm_path(PROJECT):
            return {edit_ledger.norm_path(r) for r in REPORTS}, {
                edit_ledger.norm_path(m) for m in MODELS
            }
        return real(path)

    monkeypatch.setattr(edit_ledger, "project_artifacts", fake)


def install(monkeypatch, live: LiveModel) -> None:
    """Patch every CLR seam of the journal + its manager to talk to *live*."""
    from core.operations import journal_apply, journal_manager, write_journal

    monkeypatch.setattr(write_journal, "_connected_project", lambda: PROJECT)
    _resolve_project(monkeypatch)
    monkeypatch.setattr(write_journal, "serialize_model", lambda m: copy.deepcopy(m.tmsl))

    def fetch(_model):
        live.reads += 1
        return copy.deepcopy(live.tmsl)

    monkeypatch.setattr(write_journal, "fetch_committed", fetch)
    monkeypatch.setattr(journal_apply, "apply_ops", fake_apply_ops)
    monkeypatch.setattr(
        journal_manager.WriteJournalManager,
        "_connect",
        lambda self: (SimpleNamespace(Disconnect=lambda: None), live.connect(), None),
    )
    monkeypatch.setattr(
        journal_manager.WriteJournalManager, "_function_images", staticmethod(lambda: None)
    )
