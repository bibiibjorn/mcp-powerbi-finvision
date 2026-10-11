"""Client gateway screenshot redaction (client_gateway/images.py).

Synthetic data only: Pillow-rendered screenshots with invented names. Tests that need an OCR engine
skip cleanly when it is not available; the matcher and the block/failure policy are tested without
one.
"""

from __future__ import annotations

import asyncio
import base64
import io
import threading
import time

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont

from client_gateway import images as G
from core.client_guard.engine import SessionMap

NAMES = ["Bertrand Lemoyne", "Quillfeather Capital", "Orsolya Kentwell"]
OTHER = ["Jan Smith", "Acme Holding NV"]
ALL_ROWS = NAMES + OTHER

needs_winrt = pytest.mark.skipif(not G._winrt_available(), reason="Windows.Media.Ocr unavailable")
needs_rapid = pytest.mark.skipif(not G._rapidocr_available(), reason="rapidocr not installed")


@pytest.fixture
def smap() -> SessionMap:
    m = SessionMap()
    m.add_values("CUS", NAMES + ["Zylstra Beheer", "Hollandia Partners"], "test")
    m.add_values("SHT", ["Zyl", "Mark"], "test")  # short values: whole-segment / whole-word only
    return m


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        pytest.skip("arial.ttf not available")


def _png_block(img: Image.Image, **extra) -> dict:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(buf.getvalue()).decode("ascii"),
        },
    }
    block.update(extra)
    return block


def _decode_block(block: dict) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(block["source"]["data"]))).convert("RGB")


def render_table(dark: bool, size: int, rows: int = 38, w: int = 1920, h: int = 1080):
    """Table-like screenshot. Returns (image, {row: (bbox of the name cell, text)})."""
    bg, fg = ((30, 30, 34), (225, 225, 225)) if dark else ((255, 255, 255), (20, 20, 20))
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    font = _font(size)
    cells: dict[int, tuple[tuple[int, int, int, int], str]] = {}
    for r in range(rows):
        text = ALL_ROWS[r % len(ALL_ROWS)]
        y = 20 + r * 26
        d.text((20, y), text, font=font, fill=fg)
        cells[r] = (d.textbbox((20, y), text, font=font), text)
        d.text((300, y), f"{r * 1234.5:,.2f}", font=font, fill=fg)
        d.text((500, y), "Active status", font=font, fill=fg)
    return img, cells


def _black_fraction(img: Image.Image, box) -> float:
    crop = img.crop(box).convert("L")
    px = list(crop.tobytes())
    return sum(1 for p in px if p < 10) / max(len(px), 1)


def _unchanged(a: Image.Image, b: Image.Image, box) -> bool:
    return ImageChops.difference(a.crop(box), b.crop(box)).getbbox() is None


# --------------------------------------------------------------------------------------------
# Matcher (no OCR engine needed)
# --------------------------------------------------------------------------------------------


def _line(*parts, h: float = 12.0, gap: float = 4.0, start: float = 10.0):
    """Words laid out left to right; a ('x', gap) tuple sets an explicit gap before the word."""
    words = []
    x = start
    for part in parts:
        text, g = (part, gap) if isinstance(part, str) else part
        x += g
        width = 7.0 * len(text)
        words.append(G.Word(text, x, 0.0, x + width, h))
        x += width
    return words


def _match(smap, *parts, **kw):
    return G._match_line(_line(*parts, **kw), G._index_for(smap), smap)


def test_match_multiword_value_over_word_windows(smap):
    hits = _match(smap, "Name:", "Bertrand", "Lemoyne", "Active")
    assert [(a, b) for a, b, _ in hits] == [(1, 3)]
    assert hits[0][2].startswith("CUS_")


def test_match_is_case_punctuation_and_split_insensitive(smap):
    assert len(_match(smap, "BERTRAND,", "LEMOYNE.")) == 1
    assert len(_match(smap, "Bertrand-Lemoyne")) == 1
    assert len(_match(smap, "Bertr", "and", "Lemoyne")) == 1  # OCR split a word


def test_match_ignores_diacritics(smap):
    smap.add_values("CUS", ["Éloïse Dupré"], "test")
    assert len(_match(smap, "Eloise", "Dupre")) == 1


def test_match_non_names_stay(smap):
    assert _match(smap, "Jan", "Smith", "Acme", "Holding", "NV", "12,345.00") == []


def test_match_one_ocr_confusion_for_long_values(smap):
    assert len(_match(smap, "H0llandia", "Partners")) == 1  # O -> 0
    assert len(_match(smap, "Hollandia", "Partners")) == 1
    assert len(_match(smap, "Hol1andia", "Partners")) == 1  # l -> 1


def test_match_edit_distance_for_long_values(smap):
    assert len(_match(smap, "Quillteather", "Capital")) == 1  # f -> t
    assert len(_match(smap, "Quilfeather", "Capital")) == 1  # dropped letter
    assert _match(smap, "Quxxfxxther", "Capital") == []


def test_match_short_values_need_whole_segment(smap):
    # "Zyl" is 3 chars: matches only as a whole cell, never inside text or a longer word
    assert len(_match(smap, "Zyl")) == 1
    assert _match(smap, "Zyl", ("12,000", 40)) != []  # separate cells: the first is a whole segment
    assert _match(smap, "Report", "for", "Zyl", "today") == []
    assert _match(smap, "Zylon") == []


def test_match_four_to_five_chars_are_whole_words(smap):
    assert len(_match(smap, "Mark")) == 1
    assert len(_match(smap, "Name", "Mark", "Active")) == 1
    assert _match(smap, "Markets") == []
    assert _match(smap, "Trademark") == []


def test_match_long_value_matches_inside_a_word(smap):
    smap.add_values("CUS", ["Vanderwalle"], "test")
    assert len(_match(smap, "Vanderwallestraat")) == 1


def test_match_columns_split_segments(smap):
    # two cells far apart: a tier-0 value in the second cell is its own segment
    hits = _match(smap, "Active", ("Zyl", 60))
    assert [(a, b) for a, b, _ in hits] == [(1, 2)]


def test_match_shapes_email(smap):
    hits = _match(smap, "Contact:", "jan.x@example.org", "today")
    assert [(a, b) for a, b, _ in hits] == [(1, 2)]
    assert hits[0][2].startswith("USR_")


def test_inexact_hit_does_not_swallow_the_next_cell(smap):
    # "Kentwel" + "1..." reads as the key with one confusion (l -> 1) but must not cover the number
    hits = _match(smap, "Orsolya", "Kentwel", "14,814.00")
    assert [(a, b) for a, b, _ in hits] == [(0, 2)]


def test_match_longest_wins_no_double_boxes(smap):
    smap.add_values("CUS", ["Bertrand"], "test")
    hits = _match(smap, "Bertrand", "Lemoyne")
    assert [(a, b) for a, b, _ in hits] == [(0, 2)]


def test_index_follows_dictionary_growth(smap):
    assert _match(smap, "Wolfsgracht", "Groep") == []
    smap.add_values("CUS", ["Wolfsgracht Groep"], "test")
    assert len(_match(smap, "Wolfsgracht", "Groep")) == 1


def test_unrotate_inverts_deskew():
    # a point rotated by +3.1 degrees about the centre comes back within the 3 px inflation
    import math

    cx, cy, ang = 1920.0, 1080.0, 3.1
    x, y = 42.0, 44.0
    a = math.radians(-ang)
    rx = cx + (x - cx) * math.cos(a) - (y - cy) * math.sin(a)
    ry = cy + (x - cx) * math.sin(a) + (y - cy) * math.cos(a)
    x0, y0, x1, y1 = G._unrotate((rx, ry, rx + 1, ry + 1), ang, cx, cy)
    assert x0 <= x <= x1 + 1 and y0 <= y <= y1 + 1


# --------------------------------------------------------------------------------------------
# Block handling and failure policy (no OCR engine needed)
# --------------------------------------------------------------------------------------------


def _is_withheld(out: dict, reason: str | None = None) -> bool:
    ok = out["type"] == "text" and out["text"].startswith("[Client masking: image withheld - ")
    if reason is not None:
        ok = ok and out["text"] == f"[Client masking: image withheld - {reason}]"
    return ok


def test_non_image_block_unchanged(smap):
    block = {"type": "text", "text": "hello"}
    assert G.redact_image_block(block, smap) is block


def test_policy_withhold_always(smap):
    block = _png_block(Image.new("RGB", (100, 100), "white"))
    assert _is_withheld(G.redact_image_block(block, smap, policy="withhold"), G.W_POLICY)


def test_policy_pass_unchanged(smap):
    block = _png_block(Image.new("RGB", (100, 100), "white"))
    assert G.redact_image_block(block, smap, policy="pass") is block
    doc = {"type": "document", "source": {"type": "base64", "data": "AAAA"}}
    assert G.redact_image_block(doc, smap, policy="pass") is doc


def test_unknown_policy_fails_closed(smap):
    block = _png_block(Image.new("RGB", (100, 100), "white"))
    assert _is_withheld(G.redact_image_block(block, smap, policy="bogus"), G.W_BAD_POLICY)


def test_document_block_withheld(smap):
    doc = {"type": "document", "source": {"type": "base64", "data": "AAAA"}}
    assert _is_withheld(G.redact_image_block(doc, smap), G.W_DOCUMENT)


@pytest.mark.parametrize("src", [{"type": "url", "url": "https://x/y.png"}, {"type": "file"}])
def test_url_and_file_sources_withheld(smap, src):
    block = {"type": "image", "source": src}
    assert _is_withheld(G.redact_image_block(block, smap), G.W_URL)


def test_undecodable_data_withheld(smap):
    bad = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "!!"}}
    assert _is_withheld(G.redact_image_block(bad, smap))
    junk = base64.b64encode(b"not an image at all" * 5).decode()
    bad["source"]["data"] = junk
    assert _is_withheld(G.redact_image_block(bad, smap), G.W_DECODE)
    bad["source"]["data"] = ""
    assert _is_withheld(G.redact_image_block(bad, smap), G.W_DECODE)


def test_animated_image_withheld(smap):
    frames = [Image.new("RGB", (40, 40), c) for c in ("red", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
    block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/gif",
            "data": base64.b64encode(buf.getvalue()).decode(),
        },
    }
    assert _is_withheld(G.redact_image_block(block, smap), G.W_ANIMATED)


def test_oversized_image_withheld(smap, monkeypatch):
    monkeypatch.setattr(G, "MAX_PIXELS", 1000)
    block = _png_block(Image.new("RGB", (200, 200), "white"))
    assert _is_withheld(G.redact_image_block(block, smap), G.W_TOO_LARGE)


def test_tiny_image_passes_without_ocr(smap):
    block = _png_block(Image.new("RGB", (8, 8), "white"))
    assert G.redact_image_block(block, smap) is block


def test_no_engine_withholds(smap):
    block = _png_block(Image.new("RGB", (200, 100), "white"))
    out = G.redact_image_block(block, smap, engines=("nonexistent",))
    assert _is_withheld(out, G.W_NO_ENGINE)


def test_engine_exception_withholds(smap, monkeypatch):
    def boom(name, img):
        raise RuntimeError("secret text " + NAMES[0])

    monkeypatch.setattr(G, "_resolve_engines", lambda e: ["winrt"])
    monkeypatch.setattr(G, "_run_ocr", boom)
    block = _png_block(Image.new("RGB", (200, 100), "white"))
    out = G.redact_image_block(block, smap)
    assert _is_withheld(out, G.W_OCR_FAILED)
    assert NAMES[0] not in out["text"]
    assert G.last_stats()["withheld"] == G.W_OCR_FAILED


def test_engine_timeout_withholds(smap, monkeypatch):
    release = threading.Event()

    def slow(name, img):
        release.wait(5)
        return []

    monkeypatch.setattr(G, "_resolve_engines", lambda e: ["winrt"])
    monkeypatch.setattr(G, "_run_ocr", slow)
    monkeypatch.setattr(G, "OCR_TIMEOUT_S", 0.2)
    block = _png_block(Image.new("RGB", (200, 100), "white"))
    try:
        out = G.redact_image_block(block, smap)
    finally:
        release.set()
    assert _is_withheld(out, G.W_OCR_TIMEOUT)


def test_matching_failure_withholds(smap, monkeypatch):
    monkeypatch.setattr(G, "_resolve_engines", lambda e: ["winrt"])
    monkeypatch.setattr(G, "_run_ocr", lambda n, i: [G.Word("Bertrand", 5, 5, 60, 17)])

    def boom(*a, **k):
        raise ValueError("x")

    monkeypatch.setattr(G, "_match_line", boom)
    block = _png_block(Image.new("RGB", (200, 100), "white"))
    assert _is_withheld(G.redact_image_block(block, smap), G.W_MATCH_FAILED)


class _PaintAwareOCR:
    """Stub OCR that, like a real engine, cannot read a word under a box that was painted."""

    def __init__(self, monkeypatch, words, base_width):
        self.words, self.base_width, self.boxes, self.labels = words, base_width, [], []
        real = G._paint_image

        def spy(img, hits):
            self.boxes.extend(h.box for h in hits)
            self.labels.extend(h.label for h in hits)
            return real(img, hits)

        monkeypatch.setattr(G, "_paint_image", spy)
        monkeypatch.setattr(G, "_resolve_engines", lambda e: ["winrt"])
        monkeypatch.setattr(G, "_run_ocr", self)

    def _covered(self, w):
        cx, cy = (w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2
        return any(b[0] - 3 <= cx <= b[2] + 3 and b[1] - 3 <= cy <= b[3] + 3 for b in self.boxes)

    def __call__(self, name, im):
        k = im.width / self.base_width
        return [
            G.Word(w.text, w.x0 * k, w.y0 * k, w.x1 * k, w.y1 * k)
            for w in self.words
            if not self._covered(w)
        ]


def test_fake_engine_paints_and_labels(smap, monkeypatch):
    """Full path with a stub engine: hit box painted black, rest untouched, PNG out."""
    img = Image.new("RGB", (400, 120), "white")
    ImageDraw.Draw(img).rectangle((10, 10, 300, 22), fill=(200, 200, 255))
    words = [
        G.Word("Bertrand", 20, 30, 80, 44),
        G.Word("Lemoyne", 86, 30, 140, 44),
        G.Word("Active", 20, 60, 60, 72),
    ]
    _PaintAwareOCR(monkeypatch, words, img.width)
    block = _png_block(img, cache_control={"type": "ephemeral"})
    stats: dict = {}
    out = G.redact_image_block(block, smap, stats=stats)
    assert out["type"] == "image"
    assert out["cache_control"] == {"type": "ephemeral"}
    assert out["source"]["media_type"] == "image/png"
    res = _decode_block(out)
    assert _black_fraction(res, (24, 32, 136, 42)) > 0.5
    assert _unchanged(img, res, (0, 56, 400, 120))
    assert _unchanged(img, res, (0, 0, 400, 24))
    assert stats["hits"] == 1 and stats["words"] == 3 and stats["engines"] == ["winrt"]
    assert stats["ms"] >= 0 and G.last_stats()["hits"] == 1


def test_no_hits_returns_original_block(smap, monkeypatch):
    monkeypatch.setattr(G, "_resolve_engines", lambda e: ["winrt"])
    monkeypatch.setattr(G, "_run_ocr", lambda n, i: [G.Word("Nothing", 5, 5, 60, 17)])
    block = _png_block(Image.new("RGB", (200, 100), "white"))
    assert G.redact_image_block(block, smap) is block


def test_label_never_leaks_value(smap, monkeypatch):
    """The pseudonym is what gets drawn: the painter is given the token, not the OCR text."""
    ocr = _PaintAwareOCR(
        monkeypatch, [G.Word("Bertrand", 20, 30, 80, 44), G.Word("Lemoyne", 86, 30, 140, 44)], 300
    )
    G.redact_image_block(_png_block(Image.new("RGB", (300, 100), "white")), smap)
    assert ocr.labels and all(s.startswith("CUS_") for s in ocr.labels)


def test_runs_inside_a_running_event_loop(smap, monkeypatch):
    """The gateway calls this from async code: OCR must run in a worker thread."""
    monkeypatch.setattr(G, "_resolve_engines", lambda e: ["winrt"])
    monkeypatch.setattr(G, "_run_ocr", lambda n, i: [G.Word("Unrelated", 20, 30, 80, 44)])

    async def main():
        return G.redact_image_block(_png_block(Image.new("RGB", (300, 100), "white")), smap)

    assert asyncio.run(main())["type"] == "image"


def test_alpha_and_palette_images_decode(smap, monkeypatch):
    monkeypatch.setattr(G, "_resolve_engines", lambda e: ["winrt"])
    monkeypatch.setattr(G, "_run_ocr", lambda n, i: [])
    rgba = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    assert G.redact_image_block(_png_block(rgba), smap)["type"] == "image"
    pal = Image.new("P", (100, 100))
    assert G.redact_image_block(_png_block(pal), smap)["type"] == "image"


def test_tiling_spans():
    assert G._tile_spans(500, 1000) == [(0, 500)]
    spans = G._tile_spans(2500, 1000, overlap=100)
    assert spans[0] == (0, 1000) and spans[-1][1] == 2500
    assert all(b - a <= 1000 for a, b in spans)
    assert all(spans[i + 1][0] < spans[i][1] for i in range(len(spans) - 1))  # overlap


# --------------------------------------------------------------------------------------------
# Real OCR engines
# --------------------------------------------------------------------------------------------


@needs_winrt
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
@pytest.mark.parametrize("size", [11, 13, 14])
def test_winrt_covers_names_and_leaves_the_rest(smap, dark, size):
    img, cells = render_table(dark, size)
    out = G.redact_image_block(_png_block(img), smap, engines=("winrt",))
    assert out["type"] == "image", G.last_stats()
    res = _decode_block(out)
    planted = [r for r, (_b, t) in cells.items() if t in NAMES]
    assert G.last_stats()["hits"] >= len(planted)
    for r, (box, text) in cells.items():
        if text in NAMES:
            assert _black_fraction(res, box) > 0.45, (r, text)
        else:
            assert _unchanged(img, res, box), (r, text)
    # the numbers and the status columns are never touched
    assert _unchanged(img, res, (290, 0, 1920, 1080))


@needs_winrt
def test_winrt_case_and_spacing_variants(smap):
    img = Image.new("RGB", (900, 200), "white")
    d = ImageDraw.Draw(img)
    f = _font(16)
    d.text((20, 20), "BERTRAND   LEMOYNE", font=f, fill="black")
    d.text((20, 70), "orsolya kentwell", font=f, fill="black")
    d.text((20, 120), "Hollandia Partners 2024", font=f, fill="black")
    out = G.redact_image_block(_png_block(img), smap, engines=("winrt",))
    assert out["type"] == "image" and G.last_stats()["hits"] == 3


@needs_winrt
def test_winrt_ocr_confusion_in_rendered_text(smap):
    img = Image.new("RGB", (900, 120), "white")
    ImageDraw.Draw(img).text((20, 20), "H0llandia Partners", font=_font(18), fill="black")
    out = G.redact_image_block(_png_block(img), smap, engines=("winrt",))
    assert out["type"] == "image" and G.last_stats()["hits"] == 1


@needs_winrt
def test_winrt_email_shape_redacted_without_dictionary(smap):
    img = Image.new("RGB", (900, 120), "white")
    text = "Contact jan.test@example.org now"
    ImageDraw.Draw(img).text((20, 20), text, font=_font(18), fill="black")
    out = G.redact_image_block(_png_block(img), smap, engines=("winrt",))
    assert out["type"] == "image" and G.last_stats()["hits"] == 1


@needs_winrt
def test_winrt_image_without_client_values_is_unchanged(smap):
    img = Image.new("RGB", (900, 200), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 20), "Jan Smith and Acme Holding NV", font=_font(16), fill="black")
    d.text((20, 70), "Revenue 12,345.00 EUR", font=_font(16), fill="black")
    block = _png_block(img)
    assert G.redact_image_block(block, smap, engines=("winrt",)) is block
    assert G.last_stats()["hits"] == 0 and G.last_stats()["words"] > 0


@needs_winrt
def test_winrt_engines_default_from_env(smap, monkeypatch):
    monkeypatch.setenv(G.ENGINES_ENV, "winrt")
    img, _cells = render_table(False, 14, rows=5, w=700, h=200)
    G.redact_image_block(_png_block(img), smap)
    assert G.last_stats()["engines"] == ["winrt"]


@needs_winrt
def test_winrt_large_image_tiles(smap, monkeypatch):
    """Force tiling with a tiny engine limit: names are still found and boxes land correctly."""
    img, cells = render_table(False, 14, rows=10, w=900, h=300)
    real = G._tile_spans
    monkeypatch.setattr(G, "_tile_spans", lambda n, limit, overlap=128: real(n, 600, 100))
    out = G.redact_image_block(_png_block(img), smap, engines=("winrt",))
    assert out["type"] == "image"
    res = _decode_block(out)
    for r, (box, text) in cells.items():
        if text in NAMES:
            assert _black_fraction(res, box) > 0.45, (r, text)


@needs_winrt
def test_winrt_performance_1080p(smap):
    img, _cells = render_table(False, 13)
    block = _png_block(img)
    G.redact_image_block(block, smap, engines=("winrt",))  # warm up (engine creation)
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        G.redact_image_block(block, smap, engines=("winrt",))
        times.append(time.perf_counter() - t0)
    # target 1.5 s on a laptop; the bound leaves room for a busy CI machine
    assert min(times) < 2.5, times


@needs_rapid
def test_rapidocr_second_engine(smap):
    img, cells = render_table(False, 16, rows=5, w=700, h=200)
    out = G.redact_image_block(_png_block(img), smap, engines=("rapidocr",))
    assert out["type"] == "image", G.last_stats()
    assert G.last_stats()["engines"] == ["rapidocr"]
    res = _decode_block(out)
    for r, (box, text) in cells.items():
        if text in NAMES:
            assert _black_fraction(res, box) > 0.3, (r, text)
    assert "extra_passes" not in G.last_stats()  # the slow engine only reads the original


@needs_rapid
@needs_winrt
def test_both_engines_merge_without_duplicate_boxes(smap):
    img, cells = render_table(False, 16, rows=5, w=700, h=200)
    out = G.redact_image_block(_png_block(img), smap, engines=("winrt", "rapidocr"))
    assert out["type"] == "image"
    assert G.last_stats()["engines"] == ["winrt", "rapidocr"]
    planted = sum(1 for _b, t in cells.values() if t in NAMES)
    assert G.last_stats()["hits"] == planted  # same names found twice are merged to one box


def test_rapidocr_worker_crash_withholds_and_recovers(smap, monkeypatch):
    import subprocess
    import sys

    client = G._rapid_client
    client.close()  # drop a worker an earlier test left running
    monkeypatch.setattr(
        client,
        "_start",
        lambda: subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(3)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        ),
    )
    monkeypatch.setattr(G, "_resolve_engines", lambda e: ["rapidocr"])
    block = _png_block(Image.new("RGB", (200, 100), "white"))
    assert _is_withheld(G.redact_image_block(block, smap), G.W_OCR_FAILED)
    client.close()


def test_a_value_still_readable_after_painting_is_painted_again_or_withheld(smap, monkeypatch):
    """The post-paint check: an engine that still reads the name (the box missed it) withholds."""
    monkeypatch.setattr(G, "_resolve_engines", lambda e: ["winrt"])
    monkeypatch.setattr(
        G,
        "_run_ocr",
        lambda n, i: [G.Word("Bertrand", 20, 30, 80, 44), G.Word("Lemoyne", 86, 30, 140, 44)],
    )
    out = G.redact_image_block(_png_block(Image.new("RGB", (300, 100), "white")), smap)
    assert out["type"] == "text" and G.W_RESIDUAL in out["text"]


def test_an_inexact_reading_is_covered_with_a_neutral_label_never_another_code(smap, monkeypatch):
    # "Bertrand Lem0yne": one OCR confusion (0 for o)
    ocr = _PaintAwareOCR(
        monkeypatch, [G.Word("Bertrand", 20, 30, 80, 44), G.Word("Lem0yne", 86, 30, 140, 44)], 300
    )
    out = G.redact_image_block(_png_block(Image.new("RGB", (300, 100), "white")), smap)
    assert out["type"] == "image" and ocr.labels and set(ocr.labels) == {G.NEUTRAL_LABEL}


def test_text_read_with_low_confidence_is_painted_as_unreadable(monkeypatch):
    lines = [
        [10, 10, 110, 30, "Revenue", 0.98],
        [10, 50, 210, 70, "Kxrtr1jk Zu1dp", 0.41],
        [10, 90, 110, 110, "", 0.2],
        [10, 130, 40, 150, "+2.5 pp", 0.3],
    ]
    monkeypatch.setattr(G._rapid_client, "recognise", lambda png, timeout: lines)
    words = G._rapid_ocr(Image.new("RGB", (300, 120), "white"))
    hits = G._unread_hits([words])
    assert [h.label for h in hits] == [G.UNREAD_LABEL, G.UNREAD_LABEL]
    assert hits[0].box == (10, 50, 210, 70) and hits[1].box == (10, 90, 110, 110)
    # the confident line is read as words and never painted for being unsure
    assert "Revenue" in [w.text for w in words]


def test_old_worker_lines_without_a_score_are_trusted(monkeypatch):
    monkeypatch.setattr(
        G._rapid_client, "recognise", lambda png, timeout: [[10, 10, 110, 30, "Revenue"]]
    )
    words = G._rapid_ocr(Image.new("RGB", (300, 120), "white"))
    assert G._unread_hits([words]) == [] and words[0].conf == 1.0


# --------------------------------------------------------------------------------------------
# Rotated text (G-03): category axes and vertical titles of a chart
# --------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_rotated_passes_unless_asked(monkeypatch, request):
    """The stub engines of this file ignore the image they are given, so the extra rotated reads
    would only add noise: they are switched off except in the tests that use ``rotated``."""
    if "rotated" not in request.fixturenames:
        monkeypatch.setattr(G, "ROTATED_DEGREES", ())


@pytest.fixture
def rotated():
    """Opt in to the rotated OCR passes."""


def test_rot_back_maps_a_box_of_the_rotated_frame_to_the_image():
    img = Image.new("RGB", (700, 220), "white")
    ImageDraw.Draw(img).rectangle((100, 50, 300, 80), fill=(0, 0, 0))
    for deg in (45, -45, 90, 270):
        rot = img.rotate(deg, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))
        ink = rot.convert("L").point(lambda p: 255 if p < 128 else 0).getbbox()
        x0, y0, x1, y1 = G._rot_back(tuple(map(float, ink)), deg, rot.size, img.size)
        assert x0 <= 100 and y0 <= 50 and x1 >= 300 and y1 >= 80, (deg, (x0, y0, x1, y1))
        if abs(deg) == 90:  # an exact quarter turn comes back tight
            assert x0 >= 90 and x1 <= 310 and y0 >= 40 and y1 <= 90


@pytest.mark.usefixtures("rotated")
def test_a_name_read_only_in_a_rotated_pass_is_painted_where_it_sits(smap, monkeypatch):
    """Vertical text (rotated 90 degrees): the upright read finds nothing, the rotated pass reads
    the name; the box is mapped back to the vertical strip of the image."""
    img = Image.new("RGB", (400, 120), "white")
    ImageDraw.Draw(img).rectangle((40, 10, 58, 110), fill=(120, 120, 120))  # the vertical label
    monkeypatch.setattr(G, "ROTATED_DEGREES", (90,))
    monkeypatch.setattr(G, "_resolve_engines", lambda e: ["winrt"])

    def fake(name, im):
        if im.size == (120, 400) and im.getpixel((60, 351)) != (0, 0, 0):
            # the 90 degree pass: the label reads horizontally there (not once it is painted)
            return [G.Word("Bertrand", 10, 342, 60, 360), G.Word("Lemoyne", 64, 342, 110, 360)]
        return []

    monkeypatch.setattr(G, "_run_ocr", fake)
    out = G.redact_image_block(_png_block(img), smap)
    assert out["type"] == "image" and G.last_stats()["hits"] >= 1
    res = _decode_block(out)
    assert _black_fraction(res, (40, 14, 58, 106)) > 0.6
    assert _unchanged(img, res, (150, 0, 400, 120))  # nothing else touched


@pytest.mark.usefixtures("rotated")
def test_the_rotated_reads_are_counted_and_skipped_for_huge_images(smap, monkeypatch):
    monkeypatch.setattr(G, "_resolve_engines", lambda e: ["winrt"])
    monkeypatch.setattr(G, "_run_ocr", lambda n, i: [])
    G.redact_image_block(_png_block(Image.new("RGB", (300, 200), "white")), smap)
    assert G.last_stats().get("rotated") == len(G.ROTATED_DEGREES)
    monkeypatch.setattr(G, "ROTATE_MAX_SIDE", 100)
    G.redact_image_block(_png_block(Image.new("RGB", (300, 200), "white")), smap)
    assert G.last_stats().get("rotated") == 0


@needs_winrt
@pytest.mark.usefixtures("rotated")
@pytest.mark.parametrize("angle", [45, -45, 90, 270])
def test_winrt_reads_rotated_chart_labels(smap, angle):
    label = Image.new("RGB", (300, 40), "white")
    ImageDraw.Draw(label).text((6, 8), "Quillfeather Capital", font=_font(18), fill="black")
    rotated = label.rotate(angle, expand=True, fillcolor="white")
    img = Image.new("RGB", (700, 400), "white")
    img.paste(rotated, (200, 40))
    out = G.redact_image_block(_png_block(img), smap, engines=("winrt",))
    assert out["type"] == "image", G.last_stats()
    assert G.last_stats()["hits"] >= 1, (angle, G.last_stats())
    res = _decode_block(out)
    assert not _unchanged(img, res, (200, 40, 200 + rotated.width, 40 + rotated.height))


def test_the_rapidocr_worker_process_ends_after_an_idle_period():
    killed: list[int] = []

    class Proc:
        def kill(self):
            killed.append(1)

        def wait(self, timeout=None):
            return 0

    client = G._RapidClient(idle_s=0.1)
    client._proc = Proc()
    client._arm_idle()
    deadline = time.time() + 5
    while time.time() < deadline and not killed:
        time.sleep(0.05)
    assert killed == [1] and client._proc is None
