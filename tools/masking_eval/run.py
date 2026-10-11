"""Score the masking engine on the synthetic corpus: ``python -m tools.masking_eval.run``.

Per category: gold values found / total (recall: a gold value counts as caught only when no
readable copy of it is left), keep-terms lost (over-masking), pseudonyms inserted into text that
should stay readable, and milliseconds. ``--json PATH`` writes the numbers for a report.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from typing import Any, Callable

from core.client_guard.engine import GENERIC_PREFIXES, SessionMap
from tools.masking_eval.corpus import Corpus, build

_TOKEN = re.compile(r"\b[A-Z]{2,4}_\d{4,}[UL]?\b")


def _present(text: str, value: str) -> bool:
    """A readable copy of ``value`` (case-insensitive, not as part of a pseudonym)."""
    return (
        re.search(
            r"(?<![A-Za-z0-9_])" + re.escape(value) + r"(?![A-Za-z0-9])",
            _TOKEN.sub(" ", text),
            re.IGNORECASE,
        )
        is not None
    )


def default_map(corpus: Corpus, factory: Callable[[], SessionMap] | None = None) -> SessionMap:
    smap = factory() if factory else SessionMap(token_fn=lambda prefix, key: 50_000)
    for prefix in sorted(corpus.dictionary, key=lambda p: (p in GENERIC_PREFIXES, p)):
        smap.add_values(prefix, corpus.dictionary[prefix], "eval")
    smap.add_values("DIM", corpus.strong_dim, "eval", strong=True)
    return smap


def evaluate(smap: SessionMap, corpus: Corpus) -> dict[str, Any]:
    cats: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    misses: dict[str, list[str]] = defaultdict(list)
    for sample in corpus.samples:
        c = cats[sample.category]
        t0 = time.perf_counter()
        masked, _ = smap.mask_text(sample.text)
        c["ms"] += (time.perf_counter() - t0) * 1000
        c["samples"] += 1
        for gold in sample.gold:
            c["gold"] += 1
            if _present(masked, gold):
                misses[sample.category].append(gold)
            else:
                c["caught"] += 1
        for keep in sample.keep:
            c["keep"] += 1
            if not _present(masked, keep):
                c["keep_lost"] += 1
        if not sample.gold:
            c["tokens_in_clean_text"] += len(_TOKEN.findall(masked)) - len(
                _TOKEN.findall(sample.text)
            )
    out: dict[str, Any] = {}
    for name, c in cats.items():
        row = {k: round(v, 1) for k, v in c.items()}
        if c["gold"]:
            row["recall"] = round(c["caught"] / c["gold"], 3)
        if c["keep"]:
            row["keep_rate"] = round(1 - c["keep_lost"] / c["keep"], 3)
        row["miss_examples"] = sorted(set(misses[name]))[:6]
        out[name] = row
    gold = sum(c["gold"] for c in cats.values())
    caught = sum(c["caught"] for c in cats.values())
    keep = sum(c["keep"] for c in cats.values())
    lost = sum(c["keep_lost"] for c in cats.values())
    out["_total"] = {
        "recall": round(caught / gold, 3) if gold else None,
        "keep_rate": round(1 - lost / keep, 3) if keep else None,
        "gold": gold,
        "keep": keep,
        "ms": round(sum(c["ms"] for c in cats.values()), 1),
    }
    return out


def _print(result: dict[str, Any]) -> None:
    head = f"{'category':22} {'recall':>7} {'keep':>6} {'tok+':>5} {'ms':>8}  misses"
    print(head)
    print("-" * len(head))
    for name in sorted(k for k in result if not k.startswith("_")):
        r = result[name]
        print(
            f"{name:22} {r.get('recall', ''):>7} {r.get('keep_rate', ''):>6} "
            f"{int(r.get('tokens_in_clean_text', 0)):>5} {r.get('ms', 0):>8}  "
            f"{', '.join(r['miss_examples'][:3])}"
        )
    t = result["_total"]
    print(f"{'TOTAL':22} {t['recall']:>7} {t['keep_rate']:>6} {'':>5} {t['ms']:>8}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default=None)
    parser.add_argument("--ner", default=None, help="GLiNER model name (enables the recognizer)")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--no-fuzzy", action="store_true")
    args = parser.parse_args(argv)
    corpus = build()
    recognizers = []
    if args.ner:
        from core.client_guard.engine import ordinary_word
        from core.client_guard.ner import NerRecognizer

        from client_gateway.ner_worker import WorkerModel

        ner = NerRecognizer(
            ordinary_word,
            model_name=args.ner,
            threshold=args.threshold,
            budget_s=30,
            wait_s=600,
            loader=lambda n: WorkerModel(n, threads=12),
        )  # as the gateway runs it
        ner.start()
        ner.find("Warm up: Jan Peeters works at Peeters Bouwwerken NV in Gent.")
        recognizers.append(ner)

    def factory() -> SessionMap:
        return SessionMap(
            token_fn=lambda prefix, key: 50_000, recognizers=recognizers, fuzzy=not args.no_fuzzy
        )

    result = evaluate(default_map(corpus, factory), corpus)
    _print(result)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
