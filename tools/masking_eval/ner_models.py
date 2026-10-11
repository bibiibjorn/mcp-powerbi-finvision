"""Compare name-recognition models on the synthetic NL / FR / EN sets, raw and through the
gateway's own filter (``core.client_guard.ner.NerRecognizer``: prose segments, ``name_part``,
allow list, ``keep`` with the model score). Run it when a new model appears, before changing
``client_guard.ner.model``.

    python tools/masking_eval/ner_models.py MODEL [MODEL ...]
        [--set basic|hard|all] [--gliner2-site DIR] [--out DIR]

A model id starting with ``fastino/`` is loaded with the ``gliner2`` library (not a dependency of
this repo: install it into a separate folder, ``pip install --target DIR gliner2 peft accelerate
--no-deps``, and pass ``--gliner2-site DIR``); every other one with ``gliner``. In-process PyTorch,
no worker, no gateway, no masking home. Results go to ``output/ner_eval/`` (gitignored).

Result of 2026-10-08 (107 names, 0 false hits for every model below, CPU):

    model                                   basic 74   hard 33   ms per text
    fastino/gliner2-privacy-filter-PII-multi   73        29       120-470 (needs gliner2)
    urchade/gliner_multi_pii-v1                72        29       120-190  <- default since then
    E3-JSI/gliner-multi-pii-domains-v1         71        25       100-155  (the earlier default)
    knowledgator/gliner-pii-base-v1.0          68        25        80-100
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from tools.masking_eval import ner_set_basic, ner_set_hard  # noqa: E402

LABELS = ["person", "organization", "company"]
SETS = {"basic": ner_set_basic.ITEMS, "hard": ner_set_hard.ITEMS}


class Gliner2Adapter:
    """gliner2 behind the gliner call the gateway makes (``predict_entities`` / batch)."""

    def __init__(self, name: str) -> None:
        from gliner2 import GLiNER2  # type: ignore[import-not-found]  # noqa: PLC0415

        self.model = GLiNER2.from_pretrained(name)

    def predict_entities(
        self, text: str, labels: list[str], threshold: float = 0.5
    ) -> list[dict[str, Any]]:
        out = self.model.extract_entities(
            text, list(labels), threshold=threshold, include_confidence=True, include_spans=True
        )
        return [
            {"text": h["text"], "label": label, "score": h.get("confidence", 1.0)}
            for label, hits in (out.get("entities") or {}).items()
            for h in hits
            if isinstance(h, dict)
        ]

    def batch_predict_entities(
        self, texts: list[str], labels: list[str], threshold: float = 0.5
    ) -> list[list[dict[str, Any]]]:
        return [self.predict_entities(t, labels, threshold) for t in texts]


def load(name: str) -> Any:
    if name.startswith("fastino/"):
        return Gliner2Adapter(name)
    from gliner import GLiNER  # noqa: PLC0415

    return GLiNER.from_pretrained(name)


def _words(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in s if c.isalnum() or c == " ").split()


def covers(pred: str, gold: str) -> bool:
    """A hit masks the name when it holds at least half of the gold name's words, or it is a real
    part of it (a surname of four letters or more)."""
    p, g = set(_words(pred)), set(_words(gold))
    if not p or not g:
        return False
    shared = len(p & g)
    return shared / len(g) >= 0.5 or (p <= g and shared >= 1 and len("".join(p)) >= 4)


def score(items: list[tuple[str, str, list[str], list[str]]], found: list[list[str]]) -> dict:
    tp = fn = fp = 0
    missed: list[str] = []
    false: list[str] = []
    for (_lang, _text, gold, optional), hits in zip(items, found):
        for g in gold:
            if any(covers(f, g) for f in hits):
                tp += 1
            else:
                fn += 1
                missed.append(g)
        for f in hits:
            if not any(covers(f, g) or covers(g, f) for g in [*gold, *optional]):
                fp += 1
                false.append(f)
    return {"found": tp, "names": tp + fn, "false": fp, "missed": missed, "false_hits": false}


def evaluate(name: str, items: list[tuple[str, str, list[str], list[str]]]) -> dict:
    from core.client_guard.engine import ordinary_word  # noqa: PLC0415
    from core.client_guard.ner import NerRecognizer  # noqa: PLC0415
    from core.client_guard.profiles import FINVISION_ALLOW  # noqa: PLC0415

    model = load(name)
    texts = [t for _, t, _, _ in items]
    model.predict_entities(texts[0], LABELS, threshold=0.5)  # warm up
    raw = [[s["text"] for s in model.predict_entities(t, LABELS, threshold=0.5)] for t in texts]
    rec = NerRecognizer(
        ordinary_word,
        model_name=name,
        wait_s=5,
        max_s=3600,
        strict=True,
        loader=lambda _n: model,
        default_allow=FINVISION_ALLOW,
    )
    rec.start()
    t0 = time.perf_counter()
    kept = [[s for s, _prefix in rec.find_many([t], complete=True)] for t in texts]
    ms = (time.perf_counter() - t0) * 1000 / len(texts)
    return {"model": name, "raw": score(items, raw), "pipeline": score(items, kept), "ms": ms}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--set", choices=["basic", "hard", "all"], default="all")
    ap.add_argument("--gliner2-site", default="")
    ap.add_argument("--out", default=str(REPO / "output" / "ner_eval"))
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):  # gliner2 prints emoji: a cp1252 console would fail
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    if args.gliner2_site:
        sys.path.insert(0, args.gliner2_site)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sets = list(SETS) if args.set == "all" else [args.set]
    for name in args.models:
        for set_name in sets:
            result = evaluate(name, SETS[set_name])
            slug = "".join(c if c.isalnum() else "-" for c in name)
            path = out / f"{set_name}-{slug}.json"
            path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
            p = result["pipeline"]
            print(
                f"{set_name:5} {name}: {p['found']}/{p['names']} found, {p['false']} false, "
                f"{result['ms']:.0f} ms/text (raw {result['raw']['found']}/{result['raw']['names']},"
                f" {result['raw']['false']} false)",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
