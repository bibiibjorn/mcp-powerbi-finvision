"""Client Guard: cross-script look-alikes (Unicode TR39) are folded onto Latin for matching.

NFKC leaves Cyrillic a (U+0430) and Greek o (U+03BF) alone, so "Zorvex" with a Cyrillic o must be
folded by the engine's own table (``core/client_guard/data/confusables_latin.txt``, made by
``tools/masking_lab/gen_confusables.py``). Synthetic values only. No network.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from core.client_guard import engine
from core.client_guard.engine import SessionMap, normalise

DATA = Path(engine.__file__).with_name("data") / "confusables_latin.txt"
CYRILLIC_O = "о"
NAME = "Zorvex Holding"


def _rows() -> list[tuple[int, str]]:
    rows = []
    for line in DATA.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            code, target = line.split("\t")
            rows.append((int(code, 16), target))
    return rows


@pytest.fixture()
def smap() -> SessionMap:
    m = SessionMap()
    m.add_values("FAM", [NAME], "test")
    return m


def test_the_data_file_loads_with_provenance_and_many_entries() -> None:
    text = DATA.read_text(encoding="utf-8")
    header = [line for line in text.splitlines() if line.startswith("#")]
    assert any("unicode.org/Public/security/latest/confusables.txt" in h for h in header)
    assert any(h.startswith("# Version:") for h in header)
    assert any(h.startswith("# Date:") for h in header)
    assert any("Unicode License" in h for h in header)
    assert len(_rows()) > 400
    assert text.count("\r") == 0
    assert len(engine._CONFUSABLES) == len(_rows())


def test_the_table_never_maps_an_ascii_character_and_only_to_one_latin_letter_or_digit() -> None:
    rows = _rows()
    assert [p for p, _ in rows] == sorted(p for p, _ in rows)
    for point, target in rows:
        char = chr(point)
        assert not char.isascii()
        assert len(target) == 1 and target in "abcdefghijklmnopqrstuvwxyz0123456789"
        assert not unicodedata.combining(char)
        assert unicodedata.category(char) not in {"Cf", "Zs", "Cc"}
        # what the engine sees at translate time is stable
        assert unicodedata.normalize("NFKC", char).casefold() == char
    assert not any(engine._CONFUSABLES.get(c) for c in range(0x80))


def test_the_usual_look_alikes_are_in_the_table() -> None:
    table = {chr(p): t for p, t in _rows()}
    assert table["а"] == "a"
    assert table["е"] == "e"
    assert table[CYRILLIC_O] == "o"
    assert table["ο"] == "o"
    assert table["і"] == "i"  # lower-case Cyrillic i, not TR39's capital-I "l"


def test_a_cyrillic_o_normalises_like_the_latin_one() -> None:
    assert normalise("Zоrvex") == normalise("Zorvex") == "zorvex"
    assert normalise("Zοrvex") == "zorvex"
    assert normalise("Аео") == "aeo"  # capital Cyrillic A folds via casefold


def test_ascii_text_is_unchanged_by_the_fold() -> None:
    text = "Plain ASCII text 123, Zorvex Holding."
    assert normalise(text) == text.lower()


@pytest.mark.parametrize("o", [CYRILLIC_O, "ο", "օ"])
def test_masking_replaces_exactly_the_name_span(smap: SessionMap, o: str) -> None:
    text = f"Mail from Z{o}rvex Holding today"
    masked, hits = smap.mask_text(text)
    assert hits == 1
    assert masked.startswith("Mail from ") and masked.endswith(" today")
    middle = masked[len("Mail from ") : -len(" today")]
    assert middle.startswith("FAM_") and " " not in middle
    assert o not in masked
    assert smap.unmask_text(masked)[0] == text  # the surface the user typed comes back


def _cyr(text: str) -> str:
    """``text`` with every a / e / o / c written as its Cyrillic look-alike."""
    return text.translate(
        {ord("a"): 0x0430, ord("e"): 0x0435, ord("o"): 0x043E, ord("c"): 0x0441, ord("A"): 0x0410}
    )


@pytest.mark.parametrize(
    "value, spelled",
    [
        ("Acme", _cyr("Acme")),  # short value: whole-word tier, no typo tolerance to lean on
        ("Oroco BV", _cyr("oroco BV")),
        ("Peeters", _cyr("Peeters")),
        ("Zorvex Holding", _cyr("Zorvex Holding")),
    ],
)
def test_a_value_spelled_with_look_alikes_is_masked_as_exactly_that_span(
    value: str, spelled: str
) -> None:
    m = SessionMap()
    m.add_values("FAM", [value], "test")
    assert spelled != value
    masked, hits = m.mask_text(f"from {spelled} now")
    assert hits == 1
    assert masked.startswith("from FAM_") and masked.endswith(" now")
    assert " " not in masked[len("from ") : -len(" now")]
    assert not any(ord(ch) > 0x7F for ch in masked)


def test_the_offset_map_is_exact_and_does_not_fall_back_to_the_whole_string() -> None:
    text = f"Mail from Z{CYRILLIC_O}rvex Holding today"
    norm = normalise(text)
    om = engine._build_offset_map(text, norm)
    # the fall-back map is one stretch [0, len) over the whole string; the exact one has none
    assert not (om.n_starts == [0] and om.n_ends == [len(norm)])
    start = norm.index("zorvex holding")
    end = start + len("zorvex holding")
    assert text[om.start(start) : om.end(end)] == f"Z{CYRILLIC_O}rvex Holding"


def test_a_russian_sentence_without_a_dictionary_value_comes_back_unchanged(
    smap: SessionMap,
) -> None:
    text = "Привет, как дела?"
    assert smap.mask_text(text) == (text, 0)


def test_a_missing_data_file_gives_an_empty_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError()))
    assert engine._load_confusables() == {}
