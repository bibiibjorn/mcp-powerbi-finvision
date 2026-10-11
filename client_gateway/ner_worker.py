"""NER worker process: the GLiNER model runs HERE, never inside the gateway.

Started by the gateway (``WorkerModel``) as ``python -m client_gateway.ner_worker`` at below-normal
priority. Protocol on stdin / stdout, one JSON object per line:

  worker -> gateway   {"ready": true, "load_s": 31.2}   or   {"ready": false, "error": "OSError"}
  gateway -> worker   {"id": 7, "texts": ["...", "..."], "labels": [...], "threshold": 0.5}
  worker -> gateway   {"id": 7, "results": [[{"text": "...", "label": "person", "score": 0.97}, ...]]}

Why a separate process: loading PyTorch and running the model inside the gateway held the
interpreter for seconds to minutes (2026-10-02: every chat stalled). Here a slow model only makes
the gateway's time budget run out; health checks and masking never wait for it.

Speed: batched inference (``batch_predict_entities``), optional int8 dynamic quantisation of the
Linear layers (``FINVISION_NER_INT8=1``, measured before it is the default), a fixed thread count.
The worker never logs or prints text, only the protocol lines.
"""

from __future__ import annotations

import json
import os
import sys
import time

from core.config.ner_defaults import DEFAULT_MODEL


def _lower_priority() -> None:
    try:
        import psutil  # noqa: PLC0415

        proc = psutil.Process()
        if os.name == "nt":
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            proc.nice(10)
    except Exception:  # noqa: BLE001 - best effort
        pass


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    _lower_priority()
    model_name = os.environ.get("FINVISION_NER_MODEL", DEFAULT_MODEL)
    threads = int(os.environ.get("FINVISION_NER_THREADS", "4") or 4)
    t0 = time.perf_counter()
    try:
        import torch  # noqa: PLC0415
        from gliner import GLiNER  # noqa: PLC0415

        torch.set_num_threads(max(1, threads))
        onnx_dir = os.environ.get("FINVISION_NER_ONNX", "")
        if onnx_dir and not os.path.isfile(os.path.join(onnx_dir, "model.onnx")):
            # first use: export the model once (about 20 s, 1.1 GB); ONNX Runtime is 2-3x faster on
            # CPU than PyTorch with the same results (tools/ner_bench.py)
            try:
                GLiNER.from_pretrained(model_name).export_to_onnx(onnx_dir)
            except Exception:  # noqa: BLE001 - fall back to PyTorch below
                pass
        if onnx_dir and os.path.isfile(os.path.join(onnx_dir, "model.onnx")):
            # the same model exported to ONNX (tools/ner_bench.py measures both)
            import onnxruntime as ort  # noqa: PLC0415

            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            opts.intra_op_num_threads = max(1, threads)
            model = GLiNER.from_pretrained(
                onnx_dir,
                load_onnx_model=True,
                onnx_model_file="model.onnx",
                runtime_options={"session_options": opts},
            )
        else:
            model = GLiNER.from_pretrained(model_name)
        model.eval()
        if os.environ.get("FINVISION_NER_INT8", "0") == "1":
            model = torch.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
        model.batch_predict_entities(
            ["Warm up: Jan Peeters works at Peeters NV."], ["person", "organization"], threshold=0.5
        )
    except Exception as exc:  # noqa: BLE001 - the class name only, never a message
        _emit({"ready": False, "error": type(exc).__name__})
        return 1
    _emit({"ready": True, "load_s": round(time.perf_counter() - t0, 1)})
    for line in sys.stdin:
        try:
            req = json.loads(line)
            texts = [str(t) for t in req.get("texts") or []]
            labels = [str(x) for x in req.get("labels") or ["person", "organization"]]
            threshold = float(req.get("threshold", 0.5))
            raw = model.batch_predict_entities(texts, labels, threshold=threshold) if texts else []
            results = [
                [
                    {
                        "text": str(e.get("text", "")),
                        "label": str(e.get("label", "")),
                        # the confidence: a span of ordinary words is kept only from
                        # client_guard.ner.confident on (core.client_guard.ner.keep)
                        "score": round(float(e.get("score") or 0.0), 4),
                    }
                    for e in ents
                ]
                for ents in raw
            ]
            _emit({"id": req.get("id"), "results": results})
        except Exception as exc:  # noqa: BLE001
            _emit(
                {
                    "id": (req.get("id") if isinstance(req, dict) else None),
                    "error": type(exc).__name__,
                }
            )
    return 0


def default_onnx_dir(model_name: str) -> str:
    """``%LOCALAPPDATA%/FinvisionNER/<model>-onnx`` (a model cache, never the masking home)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    slug = "".join(c if c.isalnum() else "-" for c in model_name).strip("-").lower()
    return os.path.join(base, "FinvisionNER", slug + "-onnx")


# ---------------------------------------------------------------------------------------------
# gateway side
# ---------------------------------------------------------------------------------------------
class WorkerModel:
    """Talks to the worker process; looks like a GLiNER model to ``NerRecognizer``.

    ``batch_predict_entities(texts, labels, threshold, timeout=...)`` raises ``TimeoutError``
    when the answer is not there in time (its late answer is discarded) and ``RuntimeError``
    when the worker is down (restarted after ``RESTART_BACKOFF_S``). Constructing it starts the
    worker and waits for its ready line (``load_timeout_s``), so call it from a background
    thread (``NerRecognizer.start`` does)."""

    RESTART_BACKOFF_S = 60.0

    def __init__(
        self,
        model_name: str,
        threads: int = 4,
        int8: bool = False,
        load_timeout_s: float = 300.0,
        argv: list[str] | None = None,
        onnx_dir: str | None = None,
    ) -> None:
        import threading  # noqa: PLC0415

        self.model_name = model_name
        self.threads = threads
        self.int8 = int8
        self.onnx_dir = default_onnx_dir(model_name) if onnx_dir is None else onnx_dir
        self.load_timeout_s = load_timeout_s
        self._argv = argv
        self._lock = threading.Lock()  # one request in flight
        self._cond = threading.Condition()
        self._replies: dict[int, dict] = {}
        self._next_id = 0
        self._pending: int | None = None  # the request the worker is still working on
        self._proc = None
        self._ready = threading.Event()
        self._failed_at = -1e9
        self.load_s: float | None = None
        self.error: str | None = None
        self.restarts = 0
        self._spawn()
        if not self._ready.wait(load_timeout_s):
            self.error = "load_timeout"
            raise TimeoutError("NER worker did not get ready")
        if self.error:
            raise RuntimeError("NER worker failed to load")

    # -------------------------------------------------------------- process
    def _spawn(self) -> None:
        import subprocess  # noqa: PLC0415
        import threading  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415

        repo = Path(__file__).resolve().parent.parent
        exe = repo / "venv" / "Scripts" / "python.exe"
        argv = self._argv or [
            str(exe if exe.is_file() else sys.executable),
            "-m",
            "client_gateway.ner_worker",
        ]
        env = dict(
            os.environ,
            FINVISION_NER_MODEL=self.model_name,
            FINVISION_NER_THREADS=str(self.threads),
            FINVISION_NER_INT8="1" if self.int8 else "0",
            FINVISION_NER_ONNX=self.onnx_dir,
            PYTHONIOENCODING="utf-8",
            HF_HUB_DISABLE_PROGRESS_BARS="1",
        )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self._ready.clear()
        self.error = None
        self._proc = subprocess.Popen(  # noqa: S603 - fixed argv
            argv,
            cwd=str(repo),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=flags,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        threading.Thread(
            target=self._read, args=(self._proc,), name="ner-worker-reader", daemon=True
        ).start()

    def _read(self, proc) -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "ready" in msg:
                if msg.get("ready"):
                    self.load_s = msg.get("load_s")
                else:
                    self.error = str(msg.get("error") or "load_failed")
                self._ready.set()
                continue
            with self._cond:
                if isinstance(msg.get("id"), int):
                    if msg["id"] == self._pending:
                        self._pending = None
                    self._replies = {msg["id"]: msg}  # only the newest answer is kept
                    self._cond.notify_all()
        # the worker exited
        if not self._ready.is_set():
            self.error = self.error or "exited"
            self._ready.set()
        with self._cond:
            self._pending = None
            self._cond.notify_all()

    def alive(self) -> bool:
        return (
            self._proc is not None
            and self._proc.poll() is None
            and self._ready.is_set()
            and not self.error
        )

    def close(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()

    def _ensure(self) -> None:
        if self.alive():
            return
        if time.monotonic() - self._failed_at < self.RESTART_BACKOFF_S:
            raise RuntimeError("NER worker down")
        self._failed_at = time.monotonic()
        self.close()
        self.restarts += 1
        self._spawn()  # ready later; this request is skipped
        raise RuntimeError("NER worker restarting")

    # -------------------------------------------------------------- inference
    def batch_predict_entities(
        self,
        texts: list[str],
        labels: list[str],
        threshold: float = 0.5,
        timeout: float | None = None,
    ) -> list[list[dict]]:
        deadline = time.monotonic() + (timeout if timeout is not None else 30.0)
        if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise TimeoutError("NER worker busy")
        try:
            self._ensure()
            with self._cond:
                # never queue behind an abandoned request: wait for it within this budget,
                # else skip (a queue would make every later request late too)
                while self._pending is not None:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise TimeoutError("NER worker still busy")
                    self._cond.wait(min(left, 0.5))
                self._next_id += 1
                rid = self._next_id
                self._pending = rid
            payload = {"id": rid, "texts": texts, "labels": labels, "threshold": threshold}
            try:
                self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
            except OSError:
                raise RuntimeError("NER worker pipe closed") from None
            with self._cond:
                while rid not in self._replies:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise TimeoutError("NER worker too slow")
                    if self._proc.poll() is not None:
                        raise RuntimeError("NER worker exited")
                    self._cond.wait(min(left, 0.5))
                reply = self._replies.pop(rid)
        finally:
            self._lock.release()
        if "error" in reply:
            raise RuntimeError("NER worker error")
        return reply.get("results") or []

    def predict_entities(
        self, text: str, labels: list[str], threshold: float = 0.5, timeout: float | None = None
    ) -> list[dict]:
        return self.batch_predict_entities([text], labels, threshold, timeout)[0]


if __name__ == "__main__":
    sys.exit(main())
