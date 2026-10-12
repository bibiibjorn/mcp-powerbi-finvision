"""Stable pseudonyms: keyed-hash token_fn, SessionMap(token_fn=...), five-digit token shapes.

Synthetic values only. Everything that touches the masking home uses a tmp dir.
"""

from __future__ import annotations

import re

import pytest

from core.client_guard.engine import SessionMap
from core.client_guard.tokens import (
    KEY_FILE,
    ephemeral_token_fn,
    load_or_create_key,
    stable_token_fn,
    token_fn_from_key,
)

NAMES = [
    "Alpha Trading NV",
    "Theta Imports NV",
    "Omicron Logistics BV",
    "Zeta Holdings NV",
    "Kappa Foods SA",
]
TOKEN5 = re.compile(r"^[A-Z]{3}_\d{5}$")


# ------------------------------------------------------------------------------------ tokens.py
def test_key_is_created_once_and_has_32_bytes(tmp_path) -> None:
    key = load_or_create_key(tmp_path / "home")
    assert len(key) == 32 and (tmp_path / "home" / KEY_FILE).read_bytes() == key
    assert load_or_create_key(tmp_path / "home") == key


def test_wrong_sized_key_file_is_an_error_not_a_silent_new_key(tmp_path) -> None:
    (tmp_path / KEY_FILE).write_bytes(b"short")
    with pytest.raises(ValueError):
        load_or_create_key(tmp_path)
    assert (tmp_path / KEY_FILE).read_bytes() == b"short"


def test_token_fn_is_stable_across_instances_and_in_range(tmp_path) -> None:
    a, b = stable_token_fn(tmp_path), stable_token_fn(tmp_path)
    for i in range(500):
        n = a("FAM", f"value {i}")
        assert 10_000 <= n <= 99_999 and n == b("FAM", f"value {i}")


def test_token_fn_depends_on_the_key(tmp_path) -> None:
    one = token_fn_from_key(b"\x01" * 32)
    two = token_fn_from_key(b"\x02" * 32)
    assert any(one("FAM", f"v{i}") != two("FAM", f"v{i}") for i in range(20))


def test_token_fn_spreads_values(tmp_path) -> None:
    fn = stable_token_fn(tmp_path)
    numbers = {fn("DIM", f"value {i}") for i in range(2_000)}
    assert len(numbers) > 1_900  # a birthday-collision level, not a sorted sequence


def test_ephemeral_token_fn_is_stable_within_the_process() -> None:
    fn = ephemeral_token_fn()
    assert fn("FAM", "x") == fn("FAM", "x")


# ------------------------------------------------------------------------------------ SessionMap
def _map(tmp_path, **kw) -> SessionMap:
    return SessionMap(token_fn=stable_token_fn(tmp_path), **kw)


def test_same_value_same_token_whatever_else_is_in_the_model(tmp_path) -> None:
    a, b = _map(tmp_path), _map(tmp_path)
    a.add_values("FAM", NAMES, "t")
    b.add_values("FAM", ["Brand New Client BV"], "t")
    b.add_values("FAM", reversed(NAMES), "t")
    for name in NAMES:
        ta, tb = a.mask_text(name)[0], b.mask_text(name)[0]
        assert ta == tb and TOKEN5.match(ta), (ta, tb)


def test_tokens_roundtrip_and_use_five_digits(tmp_path) -> None:
    m = _map(tmp_path)
    m.add_values("CUS", NAMES, "t")
    text = f"Invoice for {NAMES[0]} and {NAMES[1]}; ask {NAMES[0]} again."
    masked, hits = m.mask_text(text)
    assert hits == 3 and all(n not in masked for n in NAMES[:2])
    assert len(re.findall(r"CUS_\d{5}", masked)) == 3
    assert m.unmask_text(masked) == (text, 3)
    assert m.count_leaks(masked) == 0


def test_without_token_fn_numbering_is_unchanged() -> None:
    m = SessionMap()
    m.add_values("FAM", ["One Client", "Two Client"], "t")
    tokens = {m.mask_text("One Client")[0], m.mask_text("Two Client")[0]}
    assert tokens == {"FAM_0001", "FAM_0002"}


def test_collisions_take_the_next_free_number() -> None:
    m = SessionMap(token_fn=lambda prefix, key: 12_345)
    m.add_values("FAM", ["Client Aaaa", "Client Bbbb", "Client Cccc"], "t")
    tokens = sorted(m.mask_text(v)[0] for v in ("Client Aaaa", "Client Bbbb", "Client Cccc"))
    assert tokens == ["FAM_12345", "FAM_12346", "FAM_12347"]


def test_collision_outcome_does_not_depend_on_input_order() -> None:
    values = ["Client Aaaa", "Client Bbbb", "Client Cccc", "Client Dddd"]
    a = SessionMap(token_fn=lambda p, k: 20_000)
    b = SessionMap(token_fn=lambda p, k: 20_000)
    a.add_values("FAM", values, "t")
    b.add_values("FAM", list(reversed(values)), "t")
    assert [a.mask_text(v)[0] for v in values] == [b.mask_text(v)[0] for v in values]


def test_probe_wraps_at_the_top_of_the_range() -> None:
    m = SessionMap(token_fn=lambda prefix, key: 99_999)
    m.add_values("FAM", ["Client Aaaa", "Client Bbbb"], "t")
    tokens = sorted(m.mask_text(v)[0] for v in ("Client Aaaa", "Client Bbbb"))
    assert tokens == ["FAM_10000", "FAM_99999"]


def test_prefix_space_overflows_past_every_key_generation() -> None:
    # 100000.. belongs to key generation 1 (tokens.token_range): a full range continues at 1000000
    m = SessionMap(token_fn=lambda prefix, key: 10_000 + int(key[1:]) % 90_000)
    m.add_values("DIM", [f"x{i}" for i in range(90_000)], "t")
    m.add_values("DIM", ["Overflow Alpha", "Overflow Beta"], "t")
    tokens = sorted(m.mask_text(v)[0] for v in ("Overflow Alpha", "Overflow Beta"))
    assert tokens == ["DIM_1000000", "DIM_1000001"]
    assert m.unmask_text("see DIM_1000001")[0].endswith("Overflow Beta")


def test_out_of_range_token_fn_result_is_folded_into_range() -> None:
    m = SessionMap(token_fn=lambda prefix, key: 7)
    m.add_values("FAM", ["Client Aaaa"], "t")
    assert TOKEN5.match(m.mask_text("Client Aaaa")[0])


def test_case_variants_get_a_suffix_after_five_digits(tmp_path) -> None:
    m = _map(tmp_path)
    m.add_values("FAM", ["Janssens Holding"], "t")
    upper = m.mask_text("JANSSENS HOLDING")[0]
    lower = m.mask_text("janssens holding")[0]
    assert re.fullmatch(r"FAM_\d{5}U", upper) and re.fullmatch(r"FAM_\d{5}L", lower)
    assert m.unmask_text(upper)[0] == "JANSSENS HOLDING"
    assert m.unmask_text(lower)[0] == "janssens holding"


def test_quote_variants_keep_stable_five_digit_tokens(tmp_path) -> None:
    m = _map(tmp_path)
    m.add_values("FAM", ['O"Brien Trust'], "t")
    masked = m.mask_text('x = "O""Brien Trust"')[0]
    assert re.search(r"FAM_\d{5}", masked) and "Brien" not in masked
    assert m.unmask_text(masked)[0] == 'x = "O""Brien Trust"'


def test_unknown_five_digit_tokens_are_left_alone_and_counted(tmp_path) -> None:
    m = _map(tmp_path)
    m.add_values("FAM", ["Client Aaaa"], "t")
    known = m.mask_text("Client Aaaa")[0]
    unknown = "FAM_00001" if known != "FAM_00001" else "FAM_00002"
    out, n = m.unmask_text(f"{unknown} and {known}")
    assert out == f"{unknown} and Client Aaaa" and n == 1
    assert m.stats["unknown_tokens"] == 1


def test_digits_glued_to_a_five_digit_token_stay_a_hit(tmp_path) -> None:
    m = _map(tmp_path)
    m.add_values("FAM", ["Janssens"], "t")
    token = m.mask_text("Janssens")[0]
    assert m.unmask_text(f"{token}2024")[0] == "Janssens2024"


def test_stable_numbers_are_the_same_in_a_second_process_state(tmp_path) -> None:
    first = _map(tmp_path)
    first.add_values("GLA", ["Cash at bank", "Rent expense"], "t")
    tokens = [first.mask_text(v)[0] for v in ("Cash at bank", "Rent expense")]
    second = _map(tmp_path)  # a restart: new map, same key file
    second.add_values("GLA", ["Rent expense", "Cash at bank", "Other account"], "t")
    assert tokens == [second.mask_text(v)[0] for v in ("Cash at bank", "Rent expense")]
