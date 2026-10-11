"""WP-P wave 2 -- batched integrity queries + deadline (P-04).

``06 full`` ran up to 4R sequential ``bypass_cache`` DAX queries (R relationships):
one orphan check per relationship, COUNTROWS + DISTINCTCOUNT per "one"-side key,
COUNTBLANK per key column. The checks now evaluate ``INTEGRITY_BATCH_SIZE``
scalar expressions per ``ROW(...)`` round trip, retry a rejected batch one
expression at a time, and stop once the orchestrator's deadline has passed.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List


from core.model import model_validator as mv
from core.model.model_validator import ModelValidator


def _rels(n: int, many_to_many_every: int = 0) -> List[Dict[str, Any]]:
    rels = []
    for i in range(n):
        rels.append(
            {
                "FromTable": f"Fact{i}",
                "FromColumn": f"Key{i}",
                "ToTable": f"Dim{i}",
                "ToColumn": f"Id{i}",
                "ToCardinality": (
                    "Many" if many_to_many_every and i % many_to_many_every == 0 else "One"
                ),
            }
        )
    return rels


class FakeExecutor:
    """Answers ROW("name", expr, ...) batches; counts round trips."""

    def __init__(self, rels, orphans=None, dups=None, nulls=None, reject=()):
        self.rels = rels
        self.orphans = orphans or {}  # relationship index -> orphan count
        self.dups = dups or {}  # Dim index -> (total, unique)
        self.nulls = nulls or {}  # table -> null count
        self.reject = set(reject)  # query fragments the engine "rejects"
        self.queries: List[str] = []

    def execute_info_query(self, what, top_n=0):
        assert what == "RELATIONSHIPS"
        return {"success": True, "rows": self.rels}

    def validate_and_execute_dax(self, query, top_n=0, bypass_cache=False, **kw):
        assert bypass_cache is True
        self.queries.append(query)
        if any(r in query for r in self.reject):
            return {"success": False, "error": "engine said no"}
        row = {}
        for m in re.finditer(r'"([ontu]\d+)", (.*?)(?=, "[ontu]\d+", |\)\s*$)', query, re.S):
            name, expr = m.group(1), m.group(2)
            row[f"[{name}]"] = self._value(name, expr)
        return {"success": True, "rows": [row]}

    def _value(self, name, expr):
        idx = int(name[1:])
        if name[0] == "o":
            return self.orphans.get(idx, 0)
        if name[0] == "t":
            return self.dups.get(idx, (10, 10))[0]
        if name[0] == "u":
            return self.dups.get(idx, (10, 10))[1]
        table = re.search(r"'?(\w+)'?\[", expr).group(1)
        return self.nulls.get(table, 0)


def test_round_trips_drop_from_4r_to_about_r_over_5():
    rels = _rels(100)
    ex = FakeExecutor(rels)
    ModelValidator(ex).validate_model_integrity()
    # old: 100 orphan + 100 dup + 200 null = 400 sequential queries
    assert len(ex.queries) == 5 + 10 + 10
    assert all(q.lstrip().startswith("EVALUATE") for q in ex.queries)


def test_issues_equal_the_per_item_results():
    rels = _rels(30)
    ex = FakeExecutor(
        rels,
        orphans={3: 5, 17: 1},
        dups={4: (10, 7)},
        nulls={"Fact9": 2, "Dim12": 4},
    )
    out = ModelValidator(ex).validate_model_integrity()
    by_type = {}
    for i in out["issues"]:
        by_type.setdefault(i["type"], []).append(i)
    assert [i["relationship"] for i in by_type["orphaned_records"]] == [
        "Fact3[Key3] -> Dim3[Id3]",
        "Fact17[Key17] -> Dim17[Id17]",
    ]
    assert by_type["orphaned_records"][0]["description"].startswith("5 key value(s)")
    assert [(i["table"], i["column"], i["duplicate_count"]) for i in by_type["duplicate_keys"]] == [
        ("Dim4", "Id4", 3)
    ]
    assert {(i["table"], i["column"]) for i in by_type["null_keys"]} == {
        ("Fact9", "Key9"),
        ("Dim12", "Id12"),
    }
    assert out["validation_scope"] == "full" and "budget_exhausted" not in out
    assert out["summary"]["critical_issues"] == 1


def test_many_sided_to_ends_are_not_duplicate_checked():
    ex = FakeExecutor(_rels(10, many_to_many_every=2))
    ModelValidator(ex).validate_model_integrity()
    t_names = [m for q in ex.queries for m in re.findall(r'"(t\d+)"', q)]
    assert len(t_names) == 5  # only the five "one"-sided Dim columns


def test_a_rejected_batch_is_retried_singly_and_only_the_bad_item_is_lost():
    rels = _rels(6)
    # Dim2/Id2 does not exist: every query mentioning it fails
    ex = FakeExecutor(rels, orphans={1: 9, 4: 2}, reject=("Dim2",))
    out = ModelValidator(ex).validate_model_integrity()
    orphans = [i["relationship"] for i in out["issues"] if i["type"] == "orphaned_records"]
    assert orphans == ["Fact1[Key1] -> Dim1[Id1]", "Fact4[Key4] -> Dim4[Id4]"]
    # 1 failed batch + 6 single retries for the orphan check (5 of them succeed)
    orphan_queries = [q for q in ex.queries if '"o' in q]
    assert len(orphan_queries) == 1 + 6


def test_single_expression_batches_are_not_retried_twice():
    ex = FakeExecutor(_rels(1), reject=("Dim0",))
    ModelValidator(ex).validate_model_integrity()
    # orphan (1 query, rejected), duplicates (1 batch of 2 -> rejected -> 2 singles),
    # nulls (1 batch of 2: Fact0 ok? mentions only Fact0 and Dim0 -> rejected -> 2 singles)
    assert len(ex.queries) == 1 + 1 + 2 + 1 + 2


def test_deadline_stops_the_data_checks_and_marks_the_result_partial(monkeypatch):
    rels = _rels(100)
    ex = FakeExecutor(rels, orphans={0: 1})
    clock = {"t": 1000.0}
    monkeypatch.setattr(mv.time, "time", lambda: clock["t"])
    real = ex.validate_and_execute_dax

    def slow(*a, **k):
        clock["t"] += 10  # every round trip "takes" 10 s
        return real(*a, **k)

    ex.validate_and_execute_dax = slow
    out = ModelValidator(ex).validate_model_integrity(deadline=1000.0 + 25)
    assert out["success"] is True
    assert out["budget_exhausted"] is True and out["validation_scope"] == "partial"
    assert any("budget exhausted" in w for w in out["warnings"])
    assert len(ex.queries) <= 4  # stopped between round trips, not after all 25
    # the cheap structural checks still ran
    assert "issues" in out


def test_no_deadline_means_no_budget_note():
    out = ModelValidator(FakeExecutor(_rels(3))).validate_model_integrity()
    assert out["validation_scope"] == "full" and "warnings" not in out


def test_orchestrator_passes_its_deadline():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from core.orchestration.analysis_orchestrator import AnalysisOrchestrator

    validator = MagicMock()
    validator.validate_model_integrity.return_value = {"success": True, "issues": []}
    cs = SimpleNamespace(
        is_connected=lambda: True,
        query_executor=None,
        model_validator=validator,
        bpa_analyzer=None,
        performance_optimizer=None,
    )
    t0 = time.time()
    AnalysisOrchestrator(config={}).comprehensive_analysis(
        cs, scope="integrity", include_bpa=False, max_seconds=50
    )
    deadline = validator.validate_model_integrity.call_args.kwargs["deadline"]
    assert t0 + 40 < deadline < t0 + 60

    validator.reset_mock()
    AnalysisOrchestrator(config={}).comprehensive_analysis(cs, scope="integrity", include_bpa=False)
    assert validator.validate_model_integrity.call_args.kwargs["deadline"] is None
