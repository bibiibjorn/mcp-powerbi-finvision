"""Dictionary read robustness (owner decision 2026-10-12), so a machine whose Power BI Desktop or model
cannot be read the usual way still ends up with a ready dictionary:

* an engine without ``INFO.VIEW.*`` (older Desktop builds) reads the columns through
  ``INFO.COLUMNS()`` joined to ``INFO.TABLES()``; only when BOTH fail is the detection not ready;
* a column whose read FAILS (an error, not a truncation) in two detections in a row of the same
  model version is given up (``skipped_error``) instead of keeping the instance not ready forever.

Synthetic data only, fake ``run_dax``, no live seam.
"""

from __future__ import annotations

from typing import Any

import pytest

from client_gateway.provider import Provider
from client_gateway.state import GatewayState
from core.client_guard.detector import detect
from tests.test_client_guard_detector import FakeModel, decisions, masked_values

NAMES = ["Zorvex Holding", "Quillan Trust", "Brindle Capital"]
_CODES = {"Text": 2, "Int64": 6, "Double": 8, "DateTime": 9}


class DmvModel(FakeModel):
    """An engine that has no ``INFO.VIEW.*``: it answers ``INFO.TABLES`` / ``INFO.COLUMNS`` /
    ``INFO.MEASURES`` / ``INFO.RELATIONSHIPS`` (numeric ids and data type codes, like TMSCHEMA)."""

    def __init__(self) -> None:
        super().__init__()
        self.inferred_only: set[tuple[str, str]] = set()  # no explicit name / type: inferred ones
        self.no_description_columns = False
        self.queries_seen: list[str] = []

    def _tables(self) -> dict[str, int]:
        names = sorted({c[0] for c in self.columns} | set(self.measure_tables.values()))
        return {name: i + 1 for i, name in enumerate(names)}

    def __call__(self, query: str, max_rows: int) -> list[dict[str, Any]]:
        self.queries_seen.append(query)
        if "INFO.VIEW." in query:
            raise RuntimeError("function INFO.VIEW.* is not supported")
        ids = self._tables()
        if "INFO.TABLES()" in query and "INFO.COLUMNS" not in query:
            return [{"I": i, "N": n} for n, i in ids.items()]
        if "INFO.COLUMNS()" in query:
            if '"DC"' in query and self.no_description_columns:
                raise RuntimeError("no DataCategory")
            rows = []
            for t, c, dt, dc, d in self.columns:
                inferred = (t, c) in self.inferred_only
                row = {
                    "TID": float(ids[t]),  # ids may come as numbers of any kind
                    "EN": None if inferred else c,
                    "IN": c,
                    "ED": 0 if inferred else _CODES.get(dt, 1),
                    "ID": _CODES.get(dt, 1),
                }
                if '"DC"' in query:
                    row.update({"DC": dc, "D": d})
                rows.append(row)
            return rows
        if "INFO.MEASURES()" in query:
            return [
                {"N": m, "T": ids.get(self.measure_tables.get(m, ""), 0)} for m in self.measures
            ]
        if "INFO.RELATIONSHIPS()" in query:
            return [
                {"F": ids.get(f, 0), "FC": 1, "T": ids.get(t, 0), "TC": 2}
                for f, _fc, t, _tc in self.rels
            ]
        return super().__call__(query, max_rows)


def _customers(model: FakeModel) -> None:
    model.col("d_Customer", "Customer Name", NAMES)
    model.col("d_Customer", "Remark", ["note one here", "note two here"])
    model.col("f_Sales", "Amount", [1.0, 2.0], dtype="Double")


def test_a_desktop_without_info_view_reads_the_model_through_the_dmv_functions():
    model = DmvModel()
    _customers(model)
    result = detect(model, policy="identity")
    assert result.ready is True and result.columns_seen == 3
    assert decisions(result)[("d_Customer", "Customer Name")].mask is True
    assert masked_values(result) == set(NAMES)


def test_the_dmv_path_gives_the_same_decisions_as_info_view():
    plain, dmv = FakeModel(), DmvModel()
    for model in (plain, dmv):
        _customers(model)
        model.col("d_Person", "E-mail", ["a.b@example.com", "c.d@example.org"])
    one, two = detect(plain, policy="dimensions"), detect(dmv, policy="dimensions")
    assert {(k, d.mask, d.prefix) for k, d in decisions(one).items()} == {
        (k, d.mask, d.prefix) for k, d in decisions(two).items()
    }
    assert masked_values(one) == masked_values(two)
    assert one.ready and two.ready


def test_the_dmv_functions_are_tried_only_after_info_view_failed():
    model = DmvModel()
    _customers(model)
    detect(model, policy="identity")
    first_dmv = next(i for i, q in enumerate(model.queries_seen) if "INFO.TABLES()" in q)
    assert all("INFO.VIEW.COLUMNS" in q for q in model.queries_seen[:first_dmv] if "INFO." in q)
    normal = FakeModel()
    _customers(normal)
    seen: list[str] = []

    def spy(query: str, max_rows: int) -> list[dict[str, Any]]:
        seen.append(query)
        return normal(query, max_rows)

    detect(spy, policy="identity")
    assert not any("INFO.COLUMNS()" in q or "INFO.MEASURES()" in q for q in seen)  # unchanged


def test_inferred_names_and_types_count_when_there_are_no_explicit_ones():
    model = DmvModel()
    model.col("d_Customer", "Customer Name", NAMES)
    model.inferred_only = {("d_Customer", "Customer Name")}
    assert masked_values(detect(model, policy="identity")) == set(NAMES)


def test_a_build_without_description_columns_still_reads_the_columns():
    model = DmvModel()
    _customers(model)
    model.no_description_columns = True
    result = detect(model, policy="identity")
    assert result.ready and masked_values(result) == set(NAMES)


def test_only_text_and_whole_number_codes_are_read():
    model = DmvModel()
    model.col("d_Customer", "Customer Name", NAMES)
    model.col("d_Customer", "Opened", ["2024-01-01"], dtype="DateTime")
    model.col("f_Sales", "Amount", [1.0], dtype="Double")
    result = detect(model, policy="dimensions")
    assert not any(k[1] in ("Opened", "Amount") for k in decisions(result))
    assert result.columns_seen == 3


def test_measure_tables_and_relationships_survive_the_dmv_path():
    model = DmvModel()
    model.col("Measures", "Dummy", ["x"])
    model.col("d_Customer", "Customer Name", NAMES)
    model.measures = ["Total Sales"]
    model.measure_tables = {"Total Sales": "Measures"}
    result = detect(model, policy="dimensions")
    assert result.ready and ("Measures", "Dummy") not in decisions(result)  # a measure table


def test_both_the_info_view_and_the_dmv_functions_failing_is_not_ready_and_does_not_raise():
    def nothing(query: str, max_rows: int) -> list[dict[str, Any]]:
        raise RuntimeError("no metadata")

    result = detect(nothing, policy="dimensions")
    assert result.ready is False and result.columns_seen == 0 and result.queries >= 3


def test_a_dmv_function_that_fails_halfway_is_a_detection_error_too():
    class HalfModel(DmvModel):
        def __call__(self, query: str, max_rows: int) -> list[dict[str, Any]]:
            if "INFO.COLUMNS()" in query:
                raise RuntimeError("columns failed")
            return super().__call__(query, max_rows)

    model = HalfModel()
    _customers(model)
    assert detect(model, policy="identity").ready is False


# ------------------------------------------------------------------ columns that keep failing
def _failing_model() -> FakeModel:
    model = FakeModel()
    model.col("d_Customer", "Customer Name", NAMES)
    model.col("d_Notes", "Remark", ["note one here", "note two here"])
    model.fail_on = {("d_Notes", "Remark", p) for p in range(1, 50)}
    return model


def test_a_column_whose_read_fails_makes_the_detection_not_ready():
    result = detect(_failing_model(), policy="dimensions")
    assert result.ready is False and result.skipped_error == 0
    assert result.error_columns == [("d_Notes", "Remark")]


def test_a_column_given_up_after_repeated_errors_lets_the_detection_complete():
    result = detect(
        _failing_model(), policy="dimensions", skip_error_columns={("d_Notes", "Remark")}
    )
    assert result.ready is True and result.skipped_error == 1
    assert masked_values(result) == set(NAMES)  # everything else is still masked
    skipped = decisions(result)[("d_Notes", "Remark")]
    assert skipped.not_ready is False and "skipped-error" in skipped.reasons
    assert result.error_columns == [("d_Notes", "Remark")]  # still counted for the next run


def test_a_truncated_column_is_never_given_up():
    model = FakeModel()
    model.col("d_Customer", "Customer Name", [f"Name {i:04d} Holding" for i in range(30)])
    result = detect(
        model,
        policy="identity",
        page_size=10,
        max_pages=1,
        skip_error_columns={("d_Customer", "Customer Name")},
    )
    assert result.ready is False and result.skipped_error == 0 and result.error_columns == []


def test_counts_keep_the_recorded_keys():
    counts = detect(_failing_model(), policy="dimensions").counts()
    assert "skipped_error" not in counts  # the finvision golden records exactly these keys


# ------------------------------------------------------------------ the provider counts the repeats
class _Target:
    def __init__(self, model: FakeModel, fingerprints: dict[str, object]) -> None:
        self.model, self.fingerprints = model, fingerprints

    def run_dax(self, query: str, max_rows: int) -> list[dict[str, Any]]:
        return self.model(query, max_rows)

    def fingerprint(self) -> object | None:
        return self.fingerprints["fp"]

    def close(self) -> None:
        pass


class _Backend:
    def __init__(self, model: FakeModel) -> None:
        self.fingerprints: dict[str, object] = {"fp": 1}
        self.target = _Target(model, self.fingerprints)

    def instances(self) -> list[dict[str, Any]]:
        return [{"port": 51000, "msmdsrv_pid": 4000}]

    def open(self, instance: dict[str, Any]) -> _Target:
        return self.target


@pytest.fixture()
def provider(tmp_path):
    state = GatewayState(home=tmp_path, default_mode="on")
    backend = _Backend(_failing_model())
    prov = Provider(state, backend, policy_fn=lambda: ("dimensions", None))
    return state, backend, prov


def test_an_instance_with_a_column_that_cannot_be_read_becomes_ready_after_two_failures(provider):
    state, _backend, prov = provider
    prov.step()
    assert not state.ready and state.not_ready_info()["classes"] == ["column_unreadable"]
    prov.step()
    assert not state.ready  # the second failure in a row
    prov.step()
    assert state.ready and state.not_ready_info() == {}  # given up: skipped_error
    assert prov.status()["last_detection_counts"]["skipped_error"] == 1
    assert state.smap.mask_text("see Zorvex Holding")[0] != "see Zorvex Holding"


def test_new_data_gives_a_failing_column_its_chances_again(provider):
    state, backend, prov = provider
    prov.step()  # fingerprint 1: failure 1
    backend.fingerprints["fp"] = 2  # the model changed
    prov.step()  # failure 1 of the new version
    prov.step()  # failure 2
    assert not state.ready
    prov.step()  # given up
    assert state.ready


def test_a_column_that_recovers_is_not_skipped_later(provider):
    state, backend, prov = provider
    prov.step()
    backend.target.model.fail_on = set()  # the column can be read now
    prov.step()
    assert state.ready and "skipped_error" not in prov.status()["last_detection_counts"]
    assert prov._err_cols == {}
