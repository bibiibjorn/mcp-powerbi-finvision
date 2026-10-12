"""NER through the real worker process: load time, per-request latency, recall, false positives.

``python tools/ner_bench.py [--int8] [--threads N]`` (synthetic corpus only, no live gateway).
Requests are shaped like real chat traffic: a short prompt, a pasted e-mail, a long tool result
with prose. Latency is measured end to end through NerRecognizer (segmenting, cache, worker IPC).
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client_gateway.ner_worker import WorkerModel  # noqa: E402
from core.client_guard.engine import ordinary_word  # noqa: E402
from core.config.ner_defaults import DEFAULT_MODEL  # noqa: E402
from core.client_guard.ner import NerRecognizer, keep  # noqa: E402,F401
from tools.masking_eval.corpus import build  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--int8", action="store_true")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--onnx", default="", help="directory with model.onnx (export_to_onnx)")
    args = ap.parse_args()
    t0 = time.perf_counter()
    ner = NerRecognizer(
        ordinary_word,
        model_name=args.model,
        budget_s=30,
        wait_s=600,
        loader=lambda n: WorkerModel(n, threads=args.threads, int8=args.int8, onnx_dir=args.onnx),
    )
    ner.start()
    ner._ready.wait(600)
    print(
        f"model {args.model} int8={args.int8} onnx={bool(args.onnx)} threads={args.threads}: ready in "
        f"{time.perf_counter() - t0:.1f} s ({ner.state})",
        flush=True,
    )
    corpus = build()
    unknown = [s for s in corpus.samples if s.category == "unknown_names"]
    clean = [
        s
        for s in corpus.samples
        if s.category in ("negative_dev", "generic_prose", "wordlike_ordinary")
    ]
    # recall / false positives (fresh cache each)
    hit = total = 0
    for s in unknown:
        found = [f for f, _ in ner.find(s.text)]
        for g in s.gold:
            total += 1
            hit += any(g == f or g in f for f in found)
    fps = [f for s in clean for f, _ in ner.find(s.text)]
    print(
        f"recall on unknown names {hit}/{total} = {hit / total:.3f}; false positives on clean "
        f"text {len(fps)}: {sorted(set(fps))[:8]}",
        flush=True,
    )
    # latency per request shape (new text each time: no cache)
    shapes = {
        "short prompt (1 line)": lambda i: f"Kun je het saldo van klant {i} nakijken voor oktober?",
        "pasted e-mail (~1 KB)": lambda i: (
            f"Van: Lotte Vanhoutte\nOnderwerp: aanmaning {i}\n"
            + "Beste, de factuur van Dewulf Metaal BVBA staat nog open sinds september. Kunnen jullie "
            "nagaan of de betaling van Wouter Thys goed is aangekomen? " * 6 + f"Groeten, team {i}"
        ),
        "tool result (~5 KB prose)": lambda i: (
            f"Report {i}. " + "The overdue balance of Verbeke Coating NV rose again this month, "
            "while Margot Lauwers confirmed the credit note for Stijn Moens. " * 40
        ),
    }
    for name, make in shapes.items():
        ms = []
        for i in range(8):
            t1 = time.perf_counter()
            ner.find(make(i * 7919))
            ms.append((time.perf_counter() - t1) * 1000)
        print(
            f"{name:28} median {statistics.median(ms):7.0f} ms   max {max(ms):7.0f} ms", flush=True
        )
    t1 = time.perf_counter()
    ner.find(shapes["pasted e-mail (~1 KB)"](0))
    print(f"{'same text again (cache)':28} {((time.perf_counter() - t1) * 1000):7.1f} ms")
    print("stats", ner.stats)
    ner._model.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
