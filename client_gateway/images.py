"""Local screenshot redaction for Anthropic image content blocks.

The gateway masks every client value in the text of a request. A screenshot carries the same values
as PIXELS, so before an ``image`` block leaves the machine this module reads the text in it
(OCR, local only), looks for every value of the session dictionary and paints an opaque box over
each hit, with the pseudonym written on the box. The result is a PNG. Nothing here talks to a
network, writes to disk or logs a recognised text.

    redact_image_block(block, smap, policy="redact") -> dict

* returns the block unchanged when nothing was found (and ``policy="pass"``),
* returns the block with redacted PNG data when values were found,
* returns ``{"type": "text", "text": "[Client masking: image withheld - <reason>]"}`` whenever the
  image cannot be made safe: ``policy="withhold"``, an OCR failure or timeout, no OCR engine,
  an undecodable / oversized / animated image, a ``url`` or ``file`` source, a ``document`` block.

The reasons are fixed strings; they never carry text that was read from the image.

OCR engines
-----------
* ``winrt``: Windows.Media.Ocr through the ``winrt-*`` projections. Built into Windows, no model
  download, ~0.15 s for a 1920x1080 UI screenshot. Language: user-profile languages, falling back
  to en-US / nl-NL. Always the primary engine.
* ``rapidocr``: RapidOCR (onnxruntime), a second, independent engine, ON by default
  (``DEFAULT_ENGINES``). Slow on CPU (several seconds for a dense 1080p screenshot, once: results
  are cached); choose engines with ``FINVISION_GATEWAY_OCR_ENGINES`` or ``engines=``. It is
  optional: when the package is missing it is simply skipped. When ``winrt`` is not
  available either, the image is withheld.

Matching
--------
Words are grouped into lines and segments (a gap wider than the line is a column break). A line
is folded (casefold, diacritics and every non-alphanumeric character dropped, words concatenated)
and searched with the same boundary tiers as the text engine: a 6+ character value matches
anywhere, a 4-5 character value only on whole words, a shorter or common-word value only when it
is a whole segment. Values of 6+ characters also match with ONE OCR confusion (O/0, l/1/I, S/5).
Shape detectors (e-mail, IBAN, national numbers, phone) run on every line.

Rotated text
------------
Windows OCR reads upright text (and deskews a few degrees). Category axes and vertical labels of a
Power BI chart are rotated 45 / 90 degrees, so the primary engine also reads the image rotated by
``ROTATED_DEGREES`` (clockwise 45 and 90, counter-clockwise 45 and 90); the words are matched in the
rotated frame and their boxes mapped back to the image. Text at an angle in between is within the
engine's tolerance of one of those passes.

Text that could not be read
---------------------------
A value can only be matched when an engine READ it. RapidOCR reports per text line how sure it
is of its reading; a line found as text but read with a confidence below ``LOW_CONFIDENCE``
(small, blurred, stylised or cut-off text) cannot be checked, so it is painted too, with the
neutral label ``unreadable``, when it could hold a name (4+ letters, or nothing read in a box wide
enough for a word). Numbers, percentages and short labels stay readable: they are no client
value, and painting them would cost the analysis.
"""

from __future__ import annotations

import atexit
import base64
import binascii
import functools
import io
import json
import math
import os
import statistics
import threading
import time
import unicodedata
import weakref
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Optional

import ahocorasick  # type: ignore[import-not-found]
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat

from core.client_guard.engine import normalise
from core.client_guard.shapes import find_shapes

POLICIES = ("redact", "withhold", "pass")
ENGINES_ENV = "FINVISION_GATEWAY_OCR_ENGINES"
# Both engines, their readings unioned: Windows OCR (original, inverted / upscaled copies and
# 2x tiles, about 0.5 s) misses some short bold labels at every scale (2026-10-02: two
# profit-center codes in a matrix row header); RapidOCR reads them (about 6 s per screenshot,
# once: results are cached). Override with FINVISION_GATEWAY_OCR_ENGINES.
DEFAULT_ENGINES = ("winrt", "rapidocr")
# Tiles: strips of TILE_BAND px x TILE_COLS columns (overlapping), read at 2x. Small crops are read
# far more reliably than one dense full page.
TILE_BAND = 260
TILE_COLS = 3
TILE_OVERLAP = 60

# Extra reads of the primary engine on the image rotated by these angles (PIL: counter-clockwise).
# Rotated chart labels (45 degrees, vertical axis titles at 90 / 270) are not read upright.
ROTATED_DEGREES = (45, -45, 90, 270)
ROTATE_MAX_SIDE = 6000  # the rotated copy is up to 1.42x larger: skip for bigger images

OCR_TIMEOUT_S = 25.0
MAX_PIXELS = 40_000_000
MAX_UPSCALED = 9000  # largest upscaled side sent to the OCR engine (engine limit is 10000)
MIN_SIDE = 12  # below this an image cannot hold readable text
PAD = 2  # px around a painted box
DARK_MEAN = 110  # mean luminance below this = dark theme
SMALL_TEXT_PX = 20  # median word box height below this = small text (13px UI fonts: 10-18)
FUZZY_MIN = 6  # folded values at least this long also match with one OCR confusion
APPROX_MIN = 8  # folded values at least this long also match with 1 edit (2 from APPROX_MIN2)
APPROX_MIN2 = 14

# Fixed withhold reasons (never carry image content).
W_POLICY = "policy"
W_NO_ENGINE = "no OCR engine available"
W_OCR_FAILED = "OCR failed"
W_OCR_TIMEOUT = "OCR timed out"
W_DECODE = "image could not be decoded"
W_TOO_LARGE = "image too large"
W_ANIMATED = "animated image"
W_URL = "image source is not inline data"
W_DOCUMENT = "document block"
W_MATCH_FAILED = "matching failed"
W_RESIDUAL = "client text still readable after redaction"
NEUTRAL_LABEL = "client"
UNREAD_LABEL = "unreadable"
LOW_CONFIDENCE = 0.7  # a RapidOCR line read with less confidence is painted, not trusted
VERIFY_ROUNDS = 2  # re-read the painted image; anything still matching is painted, then withheld
W_BAD_POLICY = "unknown policy"

_LANCZOS = Image.LANCZOS
_CONFUSABLE = str.maketrans({"o": "0", "i": "1", "l": "1", "s": "5"})
_FONT_PATHS = ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf")

_local = threading.local()
_pool_lock = threading.Lock()
_pool: Optional[ThreadPoolExecutor] = None


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float = 1.0  # recognition confidence of the line (RapidOCR); 1.0 when not reported


@dataclass
class Hit:
    box: tuple[float, float, float, float]
    label: str


@dataclass
class ImageResult:
    data: Optional[bytes]  # redacted PNG; None when unchanged or withheld
    withheld: Optional[str]  # reason, or None
    hits: int
    stats: dict[str, Any]


class _Withhold(Exception):
    """Internal: the image cannot be made safe. ``reason`` is one of the fixed W_* strings."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --------------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------------


def withheld_block(reason: str) -> dict[str, Any]:
    return {"type": "text", "text": f"[Client masking: image withheld - {reason}]"}


def last_stats() -> dict[str, Any]:
    """Stats of the last ``redact_image_block`` call on this thread (words, hits, engines, ms)."""
    return dict(getattr(_local, "stats", {}))


def redact_image_block(
    block: dict[str, Any],
    smap: Any,
    policy: str = "redact",
    engines: Optional[tuple[str, ...]] = None,
    stats: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Redact one Anthropic ``image`` (or ``document``) content block. See the module docstring."""
    st: dict[str, Any] = {"words": 0, "hits": 0, "engines": [], "ms": 0.0, "withheld": None}
    t0 = time.perf_counter()
    try:
        out = _redact_block(block, smap, policy, engines, st)
    except _Withhold as exc:
        st["withheld"] = exc.reason
        out = withheld_block(exc.reason)
    except Exception:  # noqa: BLE001 - fail closed; nothing from the failure is surfaced
        st["withheld"] = W_OCR_FAILED
        out = withheld_block(W_OCR_FAILED)
    st["ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    _local.stats = st
    if stats is not None:
        stats.update(st)
    return out


def redact_image_bytes(
    data: bytes,
    smap: Any,
    engines: Optional[tuple[str, ...]] = None,
) -> ImageResult:
    """Redact raw image bytes. Raises ``_Withhold`` (a fixed reason) when it cannot be made safe."""
    t0 = time.perf_counter()
    st: dict[str, Any] = {"words": 0, "hits": 0, "engines": [], "passes": 0, "ms": 0.0}
    img = _decode(data)
    st["size"] = img.size
    if min(img.size) < MIN_SIDE:
        st["ocr_norm"] = ""
        return ImageResult(None, None, 0, st)
    names = _resolve_engines(engines)
    hits = _find_hits(img, smap, names, st)
    png = None
    if hits:
        painted = _paint_image(img, hits)
        # Verify: read the painted image again with every pass. An OCR pass can read a value the
        # first time only partly (a box over half a name), or a variant pass can read text the
        # original pass missed; whatever still matches is painted too. Still readable after
        # VERIFY_ROUNDS: withhold the image rather than send a name.
        for _round in range(VERIFY_ROUNDS + 1):
            fast = [n for n in names if n == "winrt"] or names  # RapidOCR only on the first read
            extra = _find_hits(
                painted,
                smap,
                fast,
                {"engines": [], "passes": 0, "words_first_pass": 0},
                recognize_text=False,
            )  # what NER found is in the map already
            st["verify_rounds"] = _round + 1
            if not extra:
                break
            if _round == VERIFY_ROUNDS:
                raise _Withhold(W_RESIDUAL)
            hits.extend(extra)
            painted = _paint_image(painted, extra)
        png = _encode_png(painted)
    st["hits"] = len(hits)
    st["ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    return ImageResult(png, None, len(hits), st)


# --------------------------------------------------------------------------------------------
# Block handling
# --------------------------------------------------------------------------------------------


def _redact_block(
    block: dict[str, Any],
    smap: Any,
    policy: str,
    engines: Optional[tuple[str, ...]],
    st: dict[str, Any],
) -> dict[str, Any]:
    if policy not in POLICIES:
        raise _Withhold(W_BAD_POLICY)
    kind = block.get("type") if isinstance(block, dict) else None
    if kind not in ("image", "document"):
        return block
    if policy == "pass":
        return block
    if policy == "withhold":
        raise _Withhold(W_POLICY)
    if kind == "document":
        raise _Withhold(W_DOCUMENT)
    source = block.get("source")
    if not isinstance(source, dict) or source.get("type") != "base64":
        raise _Withhold(W_URL)
    raw = source.get("data")
    if not isinstance(raw, str) or not raw:
        raise _Withhold(W_DECODE)
    try:
        data = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError):
        raise _Withhold(W_DECODE) from None
    result = redact_image_bytes(data, smap, engines)
    st.update(result.stats)
    if result.data is None:
        return block
    out = dict(block)
    out["source"] = {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.b64encode(result.data).decode("ascii"),
    }
    return out


def _decode(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        if getattr(img, "n_frames", 1) > 1:
            raise _Withhold(W_ANIMATED)
        if img.width * img.height > MAX_PIXELS:
            raise _Withhold(W_TOO_LARGE)
        img.load()
        img = ImageOps.exif_transpose(img) or img
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            base = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            base.alpha_composite(rgba)
            return base.convert("RGB")
        return img.convert("RGB")
    except _Withhold:
        raise
    except Image.DecompressionBombError:
        raise _Withhold(W_TOO_LARGE) from None
    except Exception:  # noqa: BLE001 - any decode failure = cannot be made safe
        raise _Withhold(W_DECODE) from None


# --------------------------------------------------------------------------------------------
# OCR engines
# --------------------------------------------------------------------------------------------


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gw-ocr")
        return _pool


def _resolve_engines(engines: Optional[tuple[str, ...]]) -> list[str]:
    if engines is None:
        env = os.environ.get(ENGINES_ENV, "").strip()
        engines = tuple(e.strip().lower() for e in env.split(",") if e.strip()) or DEFAULT_ENGINES
    wanted = [e for e in engines if e in ("winrt", "rapidocr")]
    usable = [e for e in wanted if (_winrt_available() if e == "winrt" else _rapidocr_available())]
    if not usable:
        raise _Withhold(W_NO_ENGINE)
    return usable


_winrt_state: dict[str, Any] = {}


def _winrt_available() -> bool:
    if "ok" not in _winrt_state:
        try:
            if os.name != "nt":
                raise ImportError("not windows")
            from winrt.windows.media.ocr import OcrEngine

            _winrt_state["ok"] = _make_winrt_engine(OcrEngine) is not None
        except Exception:  # noqa: BLE001
            _winrt_state["ok"] = False
    return bool(_winrt_state["ok"])


def _make_winrt_engine(ocr_engine_cls: Any) -> Any:
    engine = ocr_engine_cls.try_create_from_user_profile_languages()
    if engine is not None:
        return engine
    from winrt.windows.globalization import Language

    for tag in ("en-US", "nl-NL"):
        lang = Language(tag)
        if ocr_engine_cls.is_language_supported(lang):
            engine = ocr_engine_cls.try_create_from_language(lang)
            if engine is not None:
                return engine
    return None


def _tile_spans(n: int, limit: int, overlap: int = 128) -> list[tuple[int, int]]:
    if n <= limit:
        return [(0, n)]
    out: list[tuple[int, int]] = []
    start = 0
    while True:
        end = min(start + limit, n)
        out.append((start, end))
        if end >= n:
            return out
        start += limit - overlap


def _unrotate(
    box: tuple[float, float, float, float], angle: float, cx: float, cy: float
) -> tuple[float, float, float, float]:
    """Map a word box from the engine's deskewed frame back to the image.

    Windows.Media.Ocr deskews the image by its estimated ``text_angle`` (a dense grid of text can
    get 3 degrees) and reports boxes in that rotated frame, about the image centre. Rotating the
    four corners back and taking their bounding box lands within ~3 px; grow it by that much.
    """
    a = math.radians(angle)
    ca, sa = math.cos(a), math.sin(a)
    xs: list[float] = []
    ys: list[float] = []
    for x, y in ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])):
        dx, dy = x - cx, y - cy
        xs.append(cx + dx * ca - dy * sa)
        ys.append(cy + dx * sa + dy * ca)
    return min(xs) - 3.0, min(ys) - 3.0, max(xs) + 3.0, max(ys) + 3.0


def _winrt_ocr(img: Image.Image) -> list[Word]:
    """Windows.Media.Ocr word boxes in ``img`` pixel coordinates (runs in a worker thread)."""
    import asyncio

    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    engine = _make_winrt_engine(OcrEngine)
    if engine is None:
        raise _Withhold(W_NO_ENGINE)
    limit = int(OcrEngine.max_image_dimension)

    async def recognise(tile: Image.Image) -> list[Word]:
        writer = DataWriter()
        writer.write_bytes(tile.convert("RGBA").tobytes("raw", "BGRA"))
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, tile.width, tile.height
        )
        result = await engine.recognize_async(bitmap)
        angle = float(result.text_angle or 0.0)
        words: list[Word] = []
        for line in result.lines:
            for w in line.words:
                r = w.bounding_rect
                box = (r.x, r.y, r.x + r.width, r.y + r.height)
                if abs(angle) > 0.01:
                    box = _unrotate(box, angle, tile.width / 2.0, tile.height / 2.0)
                words.append(Word(w.text, *box))
        return words

    async def run() -> list[Word]:
        out: list[Word] = []
        for y0, y1 in _tile_spans(img.height, limit):
            for x0, x1 in _tile_spans(img.width, limit):
                tile = img if (x1 - x0, y1 - y0) == img.size else img.crop((x0, y0, x1, y1))
                for w in await recognise(tile):
                    out.append(Word(w.text, w.x0 + x0, w.y0 + y0, w.x1 + x0, w.y1 + y0))
        return out

    return asyncio.run(run())


_rapid_state: dict[str, Any] = {}


def _rapidocr_available() -> bool:
    if "ok" not in _rapid_state:
        try:
            import importlib.util

            _rapid_state["ok"] = importlib.util.find_spec("rapidocr_onnxruntime") is not None
        except Exception:  # noqa: BLE001
            _rapid_state["ok"] = False
    return bool(_rapid_state["ok"])


# RapidOCR runs in its OWN process. Windows.Media.Ocr loads the system onnxruntime.dll into the
# gateway process; the pip onnxruntime that RapidOCR needs then crashes the interpreter with an
# access violation (verified: winrt first, onnxruntime second). A worker process also contains any
# native crash of the second engine: it can only cost the image (withheld), never the gateway.


class _RapidClient:
    """Persistent RapidOCR worker process (length-prefixed PNG in, JSON boxes out)."""

    def __init__(self, idle_s: float = 600.0) -> None:
        self._lock = threading.Lock()
        self._proc: Any = None
        self.idle_s = idle_s  # the worker process (a model in memory) ends after this idle time
        self._timer: Optional[threading.Timer] = None

    def _arm_idle(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self.idle_s, self._idle_kill)
        self._timer.daemon = True
        self._timer.start()

    def _idle_kill(self) -> None:
        if self._lock.acquire(blocking=False):  # a read in progress is not idle
            try:
                self._kill()
            finally:
                self._lock.release()

    def _start(self) -> Any:
        import subprocess
        import sys
        from pathlib import Path

        root = str(Path(__file__).resolve().parent.parent)
        env = dict(os.environ)
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(
            [sys.executable, "-m", "client_gateway.images", "--rapid-worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=root,
            env=env,
            creationflags=flags,
        )

    def close(self) -> None:
        with self._lock:
            self._kill()

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass

    def recognise(self, png: bytes, timeout: float) -> list[list[Any]]:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._proc = self._start()
            proc = self._proc
            box: dict[str, Any] = {}

            def talk() -> None:
                try:
                    proc.stdin.write(len(png).to_bytes(8, "big") + png)
                    proc.stdin.flush()
                    head = proc.stdout.read(8)
                    if len(head) != 8:
                        raise OSError("worker closed")
                    n = int.from_bytes(head, "big")
                    body = proc.stdout.read(n)
                    if len(body) != n:
                        raise OSError("worker closed")
                    box["data"] = json.loads(body.decode("utf-8"))
                except Exception as exc:  # noqa: BLE001
                    box["error"] = exc

            t = threading.Thread(target=talk, daemon=True)
            t.start()
            t.join(timeout)
            if t.is_alive() or "data" not in box:
                self._kill()
                if t.is_alive():
                    raise FutureTimeout()
                raise RuntimeError("rapidocr worker failed")
            data = box["data"]
            if isinstance(data, dict):  # {"error": ...}
                self._kill()
                raise RuntimeError("rapidocr worker error")
            self._arm_idle()
            return data


_rapid_client = _RapidClient()
atexit.register(_rapid_client.close)


def _rapid_ocr(img: Image.Image) -> list[Word]:
    """RapidOCR line boxes split into pseudo-words (width shared by character count)."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", compress_level=1)
    lines = _rapid_client.recognise(buf.getvalue(), OCR_TIMEOUT_S)
    words: list[Word] = []
    for line in lines:
        x0, y0, x1, y1, text = line[:5]
        conf = float(line[5]) if len(line) > 5 else 1.0
        n = len(text)
        if x1 <= x0:
            continue
        if conf < LOW_CONFIDENCE and _could_be_a_name(text, x1 - x0, y1 - y0):
            words.append(Word("", x0, y0, x1, y1, conf))  # a marker: painted (_unread_hits)
        if n == 0:
            continue
        cw = (x1 - x0) / n
        pos = 0
        for part in text.split(" "):
            if part:
                a = x0 + pos * cw - cw * 0.5
                b = x0 + (pos + len(part)) * cw + cw * 0.5
                words.append(Word(part, max(a, x0 - cw), y0, min(b, x1 + cw), y1, conf))
            pos += len(part) + 1
    return words


def _rapid_worker_main() -> int:
    """Worker process: RapidOCR over stdin/stdout. Never imports the winrt side."""
    import sys

    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # stray prints from the libraries must not corrupt the protocol
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    inp = sys.stdin.buffer
    while True:
        head = inp.read(8)
        if len(head) != 8:
            return 0
        n = int.from_bytes(head, "big")
        data = inp.read(n)
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            result, _elapse = engine(np.asarray(img)[:, :, ::-1])
            lines = []
            for quad, text, score in result or []:
                xs = [pt[0] for pt in quad]
                ys = [pt[1] for pt in quad]
                lines.append([min(xs), min(ys), max(xs), max(ys), text, float(score)])
            payload = json.dumps(lines).encode("utf-8")
        except Exception:  # noqa: BLE001
            payload = json.dumps({"error": "ocr"}).encode("utf-8")
        out.write(len(payload).to_bytes(8, "big") + payload)
        out.flush()


def _run_ocr(name: str, img: Image.Image) -> list[Word]:
    return _winrt_ocr(img) if name == "winrt" else _rapid_ocr(img)


def _run_variant(name: str, img: Image.Image, invert: bool, k: int) -> list[Word]:
    """OCR of a prepared copy of ``img`` (optionally inverted, upscaled ``k`` times)."""
    copy = ImageOps.invert(img) if invert else img
    if k != 1:
        copy = copy.resize((img.width * k, img.height * k), _LANCZOS)
    return _run_ocr(name, copy)


def _run_tile(name: str, img: Image.Image, box: tuple[int, int, int, int], k: int) -> list[Word]:
    """OCR of one tile at ``k``x, in ``img`` coordinates."""
    x0, y0 = box[0], box[1]
    words = _run_variant(name, img.crop(box), False, k)
    return [Word(w.text, w.x0 / k + x0, w.y0 / k + y0, w.x1 / k + x0, w.y1 / k + y0) for w in words]


def _tiles(width: int, height: int) -> list[tuple[int, int, int, int]]:
    out: list[tuple[int, int, int, int]] = []
    cols = TILE_COLS if width >= 900 else 1
    cw = width // cols
    y = 0
    while y < height:
        for c in range(cols):
            x0 = max(0, c * cw - (2 * TILE_OVERLAP if c else 0))
            x1 = min(width, (c + 1) * cw + 2 * TILE_OVERLAP)
            out.append((x0, y, x1, min(height, y + TILE_BAND)))
        if y + TILE_BAND >= height:
            break
        y += TILE_BAND - TILE_OVERLAP
    return out


class _RotatedPass(list):
    """Words read from the image rotated by ``deg`` degrees, in the ROTATED frame (so lines group
    correctly there); ``back`` maps a box of that frame to a box of the image."""

    def __init__(self, items: Any, back: Any) -> None:
        super().__init__(items)
        self.back = back


def _rot_back(
    box: tuple[float, float, float, float],
    deg: float,
    rot_size: tuple[int, int],
    size: tuple[int, int],
) -> tuple[float, float, float, float]:
    """Box in the frame of ``img.rotate(deg, expand=True)`` (size ``rot_size``) -> bounding box in
    the image (size ``size``), grown by a few pixels."""
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    cx, cy = size[0] / 2.0, size[1] / 2.0
    rx, ry = rot_size[0] / 2.0, rot_size[1] / 2.0
    xs: list[float] = []
    ys: list[float] = []
    for x, y in ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])):
        dx, dy = x - rx, y - ry
        xs.append(cx + dx * ca - dy * sa)
        ys.append(cy + dx * sa + dy * ca)
    return min(xs) - 3.0, min(ys) - 3.0, max(xs) + 3.0, max(ys) + 3.0


def _run_rotated(name: str, img: Image.Image, deg: float) -> "_RotatedPass":
    rotated = img.rotate(deg, resample=Image.BICUBIC, expand=True, fillcolor=_edge_colour(img))
    words = _run_ocr(name, rotated)
    size, rot_size = img.size, rotated.size

    def back(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        return _rot_back(box, deg, rot_size, size)

    return _RotatedPass(words, back)


def _edge_colour(img: Image.Image) -> tuple[int, int, int]:
    """Background of the corners the rotation adds: the colour at the image's top-left pixel."""
    px = img.getpixel((0, 0))
    return px if isinstance(px, tuple) and len(px) == 3 else (255, 255, 255)


def _scale_words(words: list[Word], k: float) -> list[Word]:
    if k == 1:
        return words
    return [Word(w.text, w.x0 / k, w.y0 / k, w.x1 / k, w.y1 / k) for w in words]


def _is_dark(img: Image.Image) -> bool:
    small = img.convert("L").resize((96, 96), Image.BILINEAR)
    return ImageStat.Stat(small).mean[0] < DARK_MEAN


def _collect_passes(img: Image.Image, names: list[str], st: dict[str, Any]) -> list[list[Word]]:
    """One word list per OCR run (engine x image variant), all in ``img`` coordinates.

    The lists are kept apart on purpose: merging two readings of the same line would repeat its
    words and break every multi-word value.
    """
    pool = _get_pool()
    deadline = time.perf_counter() + OCR_TIMEOUT_S

    def wait(fut: Any) -> list[Word]:
        try:
            return fut.result(timeout=max(0.1, deadline - time.perf_counter()))
        except FutureTimeout:
            raise _Withhold(W_OCR_TIMEOUT) from None
        except _Withhold:
            raise
        except Exception:  # noqa: BLE001
            raise _Withhold(W_OCR_FAILED) from None

    # Extra passes (primary engine only) for dark or small-text images: an inverted 2x copy, and
    # for small text a 3x copy. Small UI text is where a single reading misses letters. The copies
    # are made inside the worker threads (PIL releases the GIL) and the passes run in parallel.
    primary = "winrt"  # the fast engine; RapidOCR (slow on CPU) only ever reads the original
    dark = _is_dark(img)
    k2 = 2 if max(img.size) * 2 <= MAX_UPSCALED else 1
    extra: list[tuple[str, int, Any]] = []

    def start_inv() -> None:
        extra.append(("inv2x", k2, pool.submit(_run_variant, primary, img, True, k2)))

    futures = {n: pool.submit(_run_ocr, n, img) for n in names}
    if dark and "winrt" in names:
        start_inv()
    tile_futs = []
    if "winrt" in names and min(img.size) >= 200 and max(img.size) * 2 <= MAX_UPSCALED * 2:
        tile_futs = [pool.submit(_run_tile, primary, img, box, 2) for box in _tiles(*img.size)]
    rotated_futs = []
    if (
        "winrt" in names
        and ROTATED_DEGREES
        and max(img.size) <= ROTATE_MAX_SIDE
        and min(img.size) >= 40
    ):
        rotated_futs = [pool.submit(_run_rotated, primary, img, deg) for deg in ROTATED_DEGREES]
    passes: list[list[Word]] = []
    for n, fut in futures.items():
        passes.append(wait(fut))
        st["engines"].append(n)
    st["words_first_pass"] = len(passes[0])

    base = passes[0] if "winrt" not in names else passes[names.index("winrt")]
    heights = [w.y1 - w.y0 for w in base if w.y1 > w.y0]
    small = bool(heights) and statistics.median(heights) < SMALL_TEXT_PX
    if "winrt" in names and (dark or small):
        if not extra:
            start_inv()
        if small and max(img.size) * 3 <= MAX_UPSCALED:
            extra.append(("3x", 3, pool.submit(_run_variant, primary, img, False, 3)))
        for n, k, fut in extra:
            passes.append(_scale_words(wait(fut), k))
            st.setdefault("extra_passes", []).append(n)
    for fut in tile_futs:  # one pass per tile: overlapping tiles repeat lines
        words = wait(fut)
        if words:
            passes.append(words)
    for fut in rotated_futs:  # rotated chart labels and vertical titles
        words = wait(fut)
        if words:
            passes.append(words)
    st["rotated"] = len(rotated_futs)
    st["tiles"] = len(tile_futs)
    st["passes"] = len(passes)
    return passes


# --------------------------------------------------------------------------------------------
# Folding and dictionary index
# --------------------------------------------------------------------------------------------


def _fold(text: str) -> str:
    """Casefold, strip diacritics, keep only letters and digits."""
    s = unicodedata.normalize("NFKD", text).casefold()
    return "".join(c for c in s if c.isalnum() and not unicodedata.combining(c))


class _Index:
    """Folded snapshot of the SessionMap dictionary (exact + one-confusion automatons)."""

    def __init__(self, smap: Any) -> None:
        best: dict[str, Any] = {}
        with smap._lock:
            items = list(smap._by_key.items())
        for key, entry in items:
            f = _fold(key)
            if len(f) < 2:
                continue
            cur = best.get(f)
            if cur is None or (cur.alias_of is not None and entry.alias_of is None):
                best[f] = entry
            elif entry.alias_of is None and cur.alias_of is None and entry.tier > cur.tier:
                best[f] = entry
        self.entries: dict[str, tuple[Any, int]] = {}
        for f, entry in best.items():
            if getattr(entry, "generic", False) and not any(c.isalpha() for c in f):
                continue  # an amount / band label: axis numbers must not be covered
            tier = int(entry.tier)
            if tier == 2 and len(f) < 6:
                tier = 1
            if tier >= 1 and len(f) < 3:
                tier = 0
            self.entries[f] = (entry, tier)
        self.exact = _automaton(self.entries)
        fuzzy: dict[str, list[str]] = {}
        for f in self.entries:
            if len(f) >= FUZZY_MIN:
                fuzzy.setdefault(f.translate(_CONFUSABLE), []).append(f)
        self.fuzzy_map = fuzzy
        self.fuzzy = _automaton(fuzzy)
        pieces: dict[str, list[tuple[str, int]]] = {}
        for f, (_entry, tier) in self.entries.items():
            if tier != 2 or len(f) < APPROX_MIN:
                continue
            k = _max_edits(len(f))
            for off, piece in _split_pieces(f, k + 1):
                pieces.setdefault(piece, []).append((f, off))
        self.piece_map = pieces
        self.pieces = _automaton(pieces)


def _max_edits(n: int) -> int:
    return 2 if n >= APPROX_MIN2 else 1


def _split_pieces(f: str, n: int) -> list[tuple[int, str]]:
    base, extra = divmod(len(f), n)
    out: list[tuple[int, str]] = []
    pos = 0
    for i in range(n):
        size = base + (1 if i < extra else 0)
        end = pos + size
        out.append((pos, f[pos:end]))
        pos += size
    return out


def _approx_at(text: str, start: int, key: str, k: int) -> Optional[tuple[int, int]]:
    """Best ``(edits, end)`` of ``key`` against ``text`` starting at ``start`` (<= ``k`` edits)."""
    m = min(len(text) - start, len(key) + k)
    if m <= 0:
        return None
    prev = list(range(m + 1))
    for i, kc in enumerate(key, 1):
        cur = [i] + [0] * m
        lo = i
        for j in range(1, m + 1):
            v = prev[j - 1] + (kc != text[start + j - 1])
            d = prev[j] + 1
            if d < v:
                v = d
            d = cur[j - 1] + 1
            if d < v:
                v = d
            cur[j] = v
            if v < lo:
                lo = v
        if lo > k:
            return None
        prev = cur
    best: Optional[tuple[int, int]] = None
    for j in range(max(len(key) - k, 1), m + 1):
        if prev[j] <= k and (best is None or prev[j] < best[0]):
            best = (prev[j], start + j)
    return best


def _automaton(keys: Any) -> Any:
    auto = ahocorasick.Automaton()
    n = 0
    for k in keys:
        auto.add_word(k, k)
        n += 1
    if n:
        auto.make_automaton()
        return auto
    return None


_index_cache: "weakref.WeakKeyDictionary[Any, tuple[int, _Index]]" = weakref.WeakKeyDictionary()
_index_lock = threading.Lock()


def _index_for(smap: Any) -> _Index:
    with _index_lock:
        version = getattr(smap, "revision", None) or len(smap._by_key)
        cached = _index_cache.get(smap)
        if cached is not None and cached[0] == version:
            return cached[1]
        index = _Index(smap)
        _index_cache[smap] = (version, index)
        return index


# --------------------------------------------------------------------------------------------
# Lines, segments, matching
# --------------------------------------------------------------------------------------------


def _group_lines(words: list[Word]) -> list[list[Word]]:
    ws = sorted((w for w in words if w.text.strip()), key=lambda w: (w.y0 + w.y1) / 2)
    lines: list[list[Word]] = []
    ranges: list[tuple[float, float]] = []
    for w in ws:
        h = max(w.y1 - w.y0, 1.0)
        if lines:
            ly0, ly1 = ranges[-1]
            overlap = min(w.y1, ly1) - max(w.y0, ly0)
            if overlap >= 0.5 * min(h, max(ly1 - ly0, 1.0)):
                lines[-1].append(w)
                ranges[-1] = (min(ly0, w.y0), max(ly1, w.y1))
                continue
        lines.append([w])
        ranges.append((w.y0, w.y1))
    for ln in lines:
        ln.sort(key=lambda w: w.x0)
    return lines


def _segments(line: list[Word]) -> list[tuple[int, int]]:
    """Word index ranges ``[a, b)`` separated by gaps wider than the text height."""
    height = statistics.median(max(w.y1 - w.y0, 1.0) for w in line)
    limit = max(height * 1.2, 8.0)
    out: list[tuple[int, int]] = []
    start = 0
    for i in range(1, len(line)):
        if line[i].x0 - line[i - 1].x1 > limit:
            out.append((start, i))
            start = i
    out.append((start, len(line)))
    return out


def _match_line(line: list[Word], index: _Index, smap: Any) -> list[tuple[int, int, str]]:
    """Hits in one line as ``(first word, last word + 1, pseudonym label)``."""
    segs = _segments(line)
    seg_set = set(segs)
    parts: list[str] = []
    wstart: list[int] = []
    wend: list[int] = []
    wid: list[int] = []
    pos = 0
    for i, w in enumerate(line):
        f = _fold(w.text)
        parts.append(f)
        wstart.append(pos)
        pos += len(f)
        wend.append(pos)
        wid.extend([i] * len(f))
    folded = "".join(parts)
    raw: list[tuple[int, int, int, str]] = []  # (c_start, c_end, edits, key)

    def consider(cs: int, ce: int, key: str) -> Optional[tuple[int, int]]:
        entry, tier = index.entries[key]
        w0, w1 = wid[cs], wid[ce - 1] + 1
        aligned = cs == wstart[w0] and ce == wend[w1 - 1]
        if not aligned and (tier == 1 or (tier == 2 and getattr(entry, "generic", False))):
            return None  # whole OCR words only: "include" inside "includes" is not the value
        if tier == 0 and ((w0, w1) not in seg_set or cs != wstart[w0] or ce != wend[w1 - 1]):
            return None
        return w0, w1

    if folded and index.exact is not None:
        for end, key in index.exact.iter(folded):
            cs, ce = end + 1 - len(key), end + 1
            span = consider(cs, ce, key)
            if span is not None:
                raw.append((cs, ce, 0, key))
    if folded and index.fuzzy is not None:
        conf = folded.translate(_CONFUSABLE)
        for end, ckey in index.fuzzy.iter(conf):
            cs, ce = end + 1 - len(ckey), end + 1
            seen = folded[cs:ce]
            for key in index.fuzzy_map[ckey]:
                edits = sum(a != b for a, b in zip(seen, key))
                if edits <= 1:
                    if consider(cs, ce, key) is not None:
                        raw.append((cs, ce, edits, key))
                    break

    if folded and index.pieces is not None:
        tried: set[tuple[str, int]] = set()
        for end, piece in index.pieces.iter(folded):
            pstart = end + 1 - len(piece)
            for key, off in index.piece_map[piece]:
                k = _max_edits(len(key))
                for s0 in range(pstart - off - k, pstart - off + k + 1):
                    if s0 < 0 or (key, s0) in tried:
                        continue
                    tried.add((key, s0))
                    found = _approx_at(folded, s0, key, k)
                    if found is not None and found[0] > 0 and found[1] > s0:
                        raw.append((s0, found[1], found[0], key))

    # One value read at several offsets (an approximate match slides): keep the closest reading.
    best: list[tuple[int, int, int, str]] = []
    for cs, ce, edits, key in sorted(raw, key=lambda t: (t[2], -(t[1] - t[0]), t[0])):
        if any(k == key and cs < b and a < ce for a, b, _e, k in best):
            continue
        best.append((cs, ce, edits, key))
    hits: list[tuple[int, int, str]] = []
    # then longest first (leftmost on ties); drop hits overlapping a chosen one
    taken: list[tuple[int, int]] = []
    for cs, ce, edits, key in sorted(best, key=lambda t: (-(t[1] - t[0]), t[0])):
        if any(cs < b and a < ce for a, b in taken):
            continue
        taken.append((cs, ce))
        entry, _tier = index.entries[key]
        # an exact reading carries the value's code; an inexact one (OCR confusion, edits) is
        # covered with a neutral label: its code could belong to another client ("Vendor 4l")
        label = smap.mask_text(entry.canon)[0] if edits == 0 else NEUTRAL_LABEL
        w0, w1 = wid[cs], wid[ce - 1] + 1
        if edits and w1 - w0 > 1:
            # an inexact reading can spill a character or two into the neighbouring word
            if (ce - wstart[w1 - 1]) * 2 < wend[w1 - 1] - wstart[w1 - 1]:
                w1 -= 1
            if (wend[w0] - cs) * 2 < wend[w0] - wstart[w0] and w1 - w0 > 1:
                w0 += 1
        hits.append((w0, w1, label))
    hits.extend(_shape_hits(line, smap))
    return hits


def _shape_hits(line: list[Word], smap: Any) -> list[tuple[int, int, str]]:
    norm_parts = [normalise(w.text) for w in line]
    spans: list[tuple[int, int]] = []
    pos = 0
    for p in norm_parts:
        spans.append((pos, pos + len(p)))
        pos += len(p) + 1
    norm = " ".join(norm_parts)
    out: list[tuple[int, int, str]] = []
    for s, e, _prefix in find_shapes(norm):
        idx = [i for i, (a, b) in enumerate(spans) if a < e and s < b]
        if not idx:
            continue
        w0, w1 = idx[0], idx[-1] + 1
        surface = " ".join(w.text for w in line[w0:w1])
        label, n = smap.mask_text(surface)
        out.append((w0, w1, label if n else "***"))
    return out


def _find_hits(
    img: Image.Image, smap: Any, names: list[str], st: dict[str, Any], recognize_text: bool = True
) -> list[Hit]:
    passes = _collect_passes(img, names, st)
    unread = _unread_hits(passes)
    st["unreadable"] = len(unread)
    passes = [_text_only(p) for p in passes]
    if "ocr_norm" not in st:
        # every word any pass read (normalised): lets a cached result be revalidated against
        # values added later without running OCR again
        st["ocr_norm"] = normalise(" ".join(w.text for p in passes for w in p))
    st["words"] = st["words_first_pass"]
    st["words_total"] = sum(len(p) for p in passes)
    if not st["words_total"]:
        return unread
    recognize = getattr(smap, "recognize", None) if recognize_text else None
    if recognize is not None and passes:
        # names in no model (a screenshot of an e-mail or a document): the recognizers (NER) read
        # the OCR text first, so what they find is matched and painted like a dictionary value
        try:
            lines = _group_lines(passes[0])
            text = "\n".join(" ".join(w.text for w in line) for line in lines)
            st["recognized"] = recognize(text)
        except Exception:  # noqa: BLE001 - fail closed
            raise _Withhold(W_MATCH_FAILED) from None
    try:
        index = _index_for(smap)
        hits: list[Hit] = []
        n_lines = 0
        for words in passes:
            lines = _group_lines(words)
            n_lines += len(lines)
            for line in lines:
                for w0, w1, label in _match_line(line, index, smap):
                    ws = line[w0:w1]
                    box = (
                        min(w.x0 for w in ws),
                        min(w.y0 for w in ws),
                        max(w.x1 for w in ws),
                        max(w.y1 for w in ws),
                    )
                    back = getattr(words, "back", None)
                    hits.append(Hit(back(box) if back is not None else box, label))
        st["lines"] = n_lines
    except _Withhold:
        raise
    except Exception:  # noqa: BLE001 - fail closed
        raise _Withhold(W_MATCH_FAILED) from None
    return _merge_hits(hits + unread)


def _text_only(words: list[Word]) -> list[Word]:
    """The words of a pass that hold text (a rotated pass stays a rotated pass)."""
    kept = [w for w in words if w.text]
    back = getattr(words, "back", None)
    return _RotatedPass(kept, back) if back is not None else kept


def _could_be_a_name(text: str, width: float, height: float) -> bool:
    """Can an unsure reading hide a client value? Numbers, percentages, short labels ("Jan",
    "+2.5 pp") cannot: they are left readable. A reading with 4+ letters can, and so can a box
    that was found as text but read as nothing, when it is wide enough for a word."""
    if not text.strip():
        return width >= 3 * max(height, 1.0)
    return sum(c.isalpha() for c in text) >= 4


def _unread_hits(passes: list[list[Word]]) -> list[Hit]:
    """Boxes of text an engine found but could not read reliably (see "Text that could not be
    read"): the line markers ``_rapid_ocr`` adds, painted with the neutral label."""
    return [
        Hit((w.x0, w.y0, w.x1, w.y1), UNREAD_LABEL)
        for words in passes
        for w in words
        if not w.text and w.conf < LOW_CONFIDENCE
    ]


def _merge_hits(hits: list[Hit]) -> list[Hit]:
    """Drop duplicate hits (the same region found by several passes / engines)."""
    out: list[Hit] = []
    for h in sorted(hits, key=lambda h: (h.box[1], h.box[0])):
        x0, y0, x1, y1 = h.box
        dup = False
        for o in out:
            ox0, oy0, ox1, oy1 = o.box
            ix = min(x1, ox1) - max(x0, ox0)
            iy = min(y1, oy1) - max(y0, oy0)
            if ix > 0 and iy > 0:
                small = min((x1 - x0) * (y1 - y0), (ox1 - ox0) * (oy1 - oy0))
                if ix * iy >= 0.6 * max(small, 1.0):
                    o.box = (min(x0, ox0), min(y0, oy0), max(x1, ox1), max(y1, oy1))
                    dup = True
                    break
        if not dup:
            out.append(Hit(h.box, h.label))
    return out


# --------------------------------------------------------------------------------------------
# Painting
# --------------------------------------------------------------------------------------------


@functools.lru_cache(maxsize=32)
def _font(size: int) -> Any:
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:  # old Pillow
        return ImageFont.load_default()


def _paint(img: Image.Image, hits: list[Hit]) -> bytes:
    return _encode_png(_paint_image(img, hits))


def _encode_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def _paint_image(img: Image.Image, hits: list[Hit]) -> Image.Image:
    out = img.copy()
    draw = ImageDraw.Draw(out)
    for hit in hits:
        x0 = max(0, int(hit.box[0]) - PAD)
        y0 = max(0, int(hit.box[1]) - PAD)
        x1 = min(out.width, int(hit.box[2] + 0.999) + PAD)
        y1 = min(out.height, int(hit.box[3] + 0.999) + PAD)
        if x1 <= x0 or y1 <= y0:
            continue
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=(0, 0, 0))
        _label(draw, (x0, y0, x1, y1), hit.label)
    return out


def _label(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str) -> None:
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0 - 2, y1 - y0 - 2
    size = min(14, bh)
    while size >= 7:
        font = _font(size)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        if right - left <= bw and bottom - top <= bh:
            tx = x0 + 1 + (bw - (right - left)) // 2 - left
            ty = y0 + 1 + (bh - (bottom - top)) // 2 - top
            draw.text((tx, ty), text, font=font, fill=(255, 255, 255))
            return
        size -= 1


if __name__ == "__main__":
    import sys

    if "--rapid-worker" in sys.argv:
        raise SystemExit(_rapid_worker_main())
