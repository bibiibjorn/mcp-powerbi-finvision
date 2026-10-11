"""NER worker process protocol (fake worker, no model): ready, answers, budget, no queueing,
dead worker, and the recognizer degrading instead of blocking a request."""

from __future__ import annotations

import sys
import textwrap
import time

import pytest

from client_gateway.ner_worker import WorkerModel
from core.client_guard.engine import ordinary_word
from core.client_guard.ner import NerRecognizer

FAKE = textwrap.dedent("""
    import json, sys, time
    mode = sys.argv[1]
    if mode == "fail":
        print(json.dumps({"ready": False, "error": "OSError"}), flush=True); sys.exit(1)
    print(json.dumps({"ready": True, "load_s": 0.1}), flush=True)
    for line in sys.stdin:
        req = json.loads(line)
        if mode == "slow":
            time.sleep(1.5)
        if mode == "die":
            sys.exit(3)
        res = [[{"text": "Lotte Vanhoutte", "label": "person"}] if "Lotte" in t else []
               for t in req["texts"]]
        print(json.dumps({"id": req["id"], "results": res}), flush=True)
""")


@pytest.fixture
def fake(tmp_path):
    path = tmp_path / "fake_worker.py"
    path.write_text(FAKE, encoding="utf-8")

    def make(mode: str) -> WorkerModel:
        return WorkerModel("fake", argv=[sys.executable, str(path), mode], load_timeout_s=20)

    return make


def test_answers_come_back_per_text(fake):
    w = fake("ok")
    try:
        out = w.batch_predict_entities(
            ["Mail van Lotte Vanhoutte", "niets"], ["person"], 0.5, timeout=5
        )
        assert out == [[{"text": "Lotte Vanhoutte", "label": "person"}], []]
    finally:
        w.close()


def test_a_slow_answer_times_out_and_the_next_request_does_not_queue(fake):
    w = fake("slow")
    try:
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            w.batch_predict_entities(["Lotte"], ["person"], timeout=0.3)
        assert time.monotonic() - t0 < 1.0  # the budget, not the worker, decides
        t1 = time.monotonic()
        with pytest.raises(TimeoutError):  # still busy with the abandoned one: skipped, no queue
            w.batch_predict_entities(["Lotte"], ["person"], timeout=0.3)
        assert time.monotonic() - t1 < 1.0
        time.sleep(1.6)
        assert w.batch_predict_entities(["Lotte"], ["person"], timeout=3)[0]  # free again
    finally:
        w.close()


def test_a_worker_that_fails_to_load_raises(fake):
    with pytest.raises(RuntimeError):
        fake("fail")


def test_a_dead_worker_is_reported_and_restarted_with_backoff(fake):
    w = fake("die")
    try:
        with pytest.raises(RuntimeError):
            w.batch_predict_entities(["Lotte"], ["person"], timeout=5)
        with pytest.raises(RuntimeError):  # restart spawned, this request is skipped
            w.batch_predict_entities(["Lotte"], ["person"], timeout=5)
        assert w.restarts == 1
        with pytest.raises(RuntimeError):  # within the backoff: no new spawn storm
            w.batch_predict_entities(["Lotte"], ["person"], timeout=5)
        assert w.restarts == 1
    finally:
        w.close()


def test_the_recognizer_degrades_on_a_slow_worker_and_never_blocks(fake):
    ner = NerRecognizer(ordinary_word, loader=lambda name: fake("slow"), budget_s=0.3, wait_s=10)
    ner.start()
    assert ner._ready.wait(20) and ner.ready()
    text = "Mail van Lotte Vanhoutte over de factuur van vorige maand."
    t0 = time.monotonic()
    assert ner.find(text) == []
    assert time.monotonic() - t0 < 1.0
    assert ner.stats["timeouts"] == 1
    ner._model.close()


def test_the_recognizer_finds_names_through_the_worker(fake):
    ner = NerRecognizer(ordinary_word, loader=lambda name: fake("ok"), budget_s=3, wait_s=10)
    ner.start()
    assert ner._ready.wait(20)
    assert ner.find("Mail van Lotte Vanhoutte over de factuur van vorige maand.") == [
        ("Lotte Vanhoutte", "PER")
    ]
    ner._model.close()


def test_the_worker_reports_the_model_score(monkeypatch, capsys):
    # the recognizer keeps a confident span of ordinary words ("De Wit" 0.96): the score must
    # come back from the worker process (evaluation 2026-10-08)
    import io
    import json
    import types

    from client_gateway import ner_worker

    class Model:
        def eval(self):
            return self

        def batch_predict_entities(self, texts, labels, threshold=0.5):
            return [[{"text": "De Wit", "label": "person", "score": 0.9612345}] for _ in texts]

    fake = types.ModuleType("gliner")
    fake.GLiNER = types.SimpleNamespace(from_pretrained=lambda *a, **k: Model())
    monkeypatch.setitem(sys.modules, "gliner", fake)
    monkeypatch.setattr(ner_worker, "_lower_priority", lambda: None)
    monkeypatch.delenv("FINVISION_NER_ONNX", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"id": 1, "texts": ["x"]}) + "\n"))
    assert ner_worker.main() == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert lines[-1]["results"] == [[{"text": "De Wit", "label": "person", "score": 0.9612}]]
