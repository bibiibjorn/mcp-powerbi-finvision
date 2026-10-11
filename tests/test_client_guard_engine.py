"""Client Guard engine: tiers, normalisation, variants, escaping, shapes, tree rules, failure path.

Synthetic values only. No network, no CLR.
"""

from __future__ import annotations

import json
import random
import string
import time

import pytest

from core.client_guard.common_words import COMMON_WORDS
from core.client_guard.engine import (
    MASK_FAILED,
    UNMASK_FAILED,
    MaskingError,
    SessionMap,
    normalise,
)
from core.client_guard.shapes import (
    be_kbo_is_valid,
    be_national_number_is_valid,
    iban_is_valid,
)

IBAN_BE = "BE68539007547034"
IBAN_BE_SPACED = "BE68 5390 0754 7034"
IBAN_NL = "NL91ABNA0417164300"
IBAN_DE = "DE89370400440532013000"


@pytest.fixture()
def smap() -> SessionMap:
    m = SessionMap()
    m.add_values(
        "FAM",
        [
            "Janssens Holding",
            "Janssens",
            "Smith",
            "Test",
            "Jan",
            "Holding",
            "Smith & Sons",
            "Café Müller",
            "İstanbul Capital",
            "Straße Invest",
            'O"Brien Trust',
            "D'Hondt NV",
            "Fussball Club",
        ],
        "test",
    )
    m.add_values("PER", ["Marie Janssens"], "test")
    m.add_values("USR", ["marie.janssens@example.be"], "test")
    m.add_values("ACC", [IBAN_BE], "test")
    return m


def tok(m: SessionMap, real: str) -> str:
    out, n = m.mask_text(real)
    assert n == 1
    return out


def roundtrip(m: SessionMap, text: str) -> str:
    masked, _ = m.mask_text(text)
    unmasked, _ = m.unmask_text(masked)
    return unmasked


# ---------------------------------------------------------------- tiers


class TestBoundaryTiers:
    def test_four_char_common_word_only_matches_whole_string(self, smap: SessionMap) -> None:
        assert smap.mask_text("Latest news")[1] == 0
        assert smap.mask_text("Test results")[1] == 0
        assert smap.mask_text("a Test.")[1] == 0
        assert smap.mask_text("Test")[1] == 1

    def test_whole_string_hit_keeps_surrounding_whitespace(self, smap: SessionMap) -> None:
        out, n = smap.mask_text("  Test  ")
        assert n == 1
        assert out == "  " + tok(smap, "Test") + "  "

    def test_five_char_value_is_whole_word_only(self, smap: SessionMap) -> None:
        assert smap.mask_text("Smiths")[1] == 0
        assert smap.mask_text("blacksmith")[1] == 0
        out, n = smap.mask_text("see Smith, then")
        assert n == 1 and "Smith" not in out
        assert smap.mask_text("Smith's")[1] == 1  # an apostrophe is not a word character

    def test_eight_char_value_matches_inside_words(self, smap: SessionMap) -> None:
        # an identity value glued into a file name or identifier is still a leak
        out, n = smap.mask_text("xJanssens2024")
        assert n == 1 and "Janssens" not in out and out.endswith("2024")
        out, n = smap.mask_text("JanssensHolding")  # the no-separator form is a variant of its own
        assert n == 1 and "Janssens" not in out and "Holding" not in out
        assert roundtrip(smap, "JanssensHolding") == "JanssensHolding"
        assert roundtrip(smap, "xJanssens2024") == "xJanssens2024"

    def test_short_value_matches_whole_string_only(self, smap: SessionMap) -> None:
        assert smap.mask_text("January")[1] == 0
        assert smap.mask_text("Jan Peeters")[1] == 0
        assert smap.mask_text("Jan")[1] == 1

    def test_long_common_word_is_whole_string_only(self, smap: SessionMap) -> None:
        assert smap.mask_text("Holding company")[1] == 0
        assert smap.mask_text("Holding")[1] == 1

    def test_custom_common_words(self) -> None:
        m = SessionMap(common_words=["Acmecorp"])
        m.add_values("ORG", ["Acmecorp"], "t")
        assert m.mask_text("the Acmecorp deal")[1] == 0
        assert m.mask_text("Acmecorp")[1] == 1

    def test_builtin_common_words_cover_the_basics(self) -> None:
        assert len(COMMON_WORDS) >= 280
        for word in ("test", "data", "total", "group", "holding", "family", "services", "analysis"):
            assert word in COMMON_WORDS
        for word in ("value", "name", "client", "cash", "equity", "bank", "fund"):
            assert word in COMMON_WORDS


# ---------------------------------------------------------------- normalisation


class TestNormalisation:
    def test_turkish_dotted_capital_i_does_not_crash_or_leak(self, smap: SessionMap) -> None:
        for text in ("İstanbul Capital", "İSTANBUL CAPITAL", "i̇stanbul capital"):
            out, n = smap.mask_text(f"see {text} now")
            assert n == 1 and "stanbul" not in out.lower()
            assert smap.count_leaks(out) == 0

    def test_sharp_s_and_long_s(self, smap: SessionMap) -> None:
        for text in ("Straße Invest", "STRASSE INVEST", "strasse invest"):
            out, n = smap.mask_text(text)
            assert n == 1 and "invest" not in out.lower()
        out, n = smap.mask_text("Fuſſball Club")  # long s
        assert n == 1 and "ball" not in out.lower()

    def test_nfd_accents_are_matched_and_fully_replaced(self, smap: SessionMap) -> None:
        nfd = "Café Müller"
        out, n = smap.mask_text(f"x {nfd} y")
        assert n == 1
        assert out == f"x {tok(smap, 'Caf' + chr(233) + ' M' + chr(252) + 'ller')} y"
        assert "́" not in out and "̈" not in out

    def test_nbsp_and_hangul_filler_and_whitespace_runs(self, smap: SessionMap) -> None:
        for sep in (" ", "ㅤ", "  ", " \n\t ", " "):
            out, n = smap.mask_text(f"a Janssens{sep}Holding b")
            assert n == 1, repr(sep)
            assert out == f"a {tok(smap, 'Janssens Holding')} b", repr(sep)

    def test_normalise_collapses_and_folds(self) -> None:
        assert normalise("A  BㅤC") == "a b c"
        assert normalise("STRASSE") == normalise("Straße")


# ---------------------------------------------------------------- case variants and variants


class TestCaseAndVariants:
    def test_case_variant_round_trip(self, smap: SessionMap) -> None:
        canon, upper, lower = "Janssens Holding", "JANSSENS HOLDING", "janssens holding"
        t, tu, tl = (smap.mask_text(s)[0] for s in (canon, upper, lower))
        assert tu == t + "U" and tl == t + "L"
        for s in (canon, upper, lower):
            assert roundtrip(smap, s) == s

    def test_mixed_case_gets_its_own_alias_token_and_round_trips_exactly(
        self, smap: SessionMap
    ) -> None:
        canonical = tok(smap, "Janssens Holding")
        before = smap.stats["case_other"]
        out, n = smap.mask_text("jAnSSens holding")
        assert n == 1 and out != canonical and out.startswith("FAM_")
        assert smap.stats["case_other"] == before + 1
        assert smap.mask_text("jAnSSens holding")[0] == out  # stable: one alias per surface
        assert smap.stats["case_other"] == before + 1
        assert roundtrip(smap, "jAnSSens holding") == "jAnSSens holding"

    def test_generic_everyday_words_are_left_alone_in_code_and_prose(self) -> None:
        # Policy A puts generic words in the dictionary; they are report text, not client names:
        # only an exact cell holding just that word is masked
        m = SessionMap(token_fn=lambda prefix, key: 20_000)
        m.add_values("DIM", ["Insurance", "Operations", "Other income"], "t")
        for text in (
            "insuranceCost",
            '"visualType": "x"  INSURANCEcost',
            "src/trainingData/operationsLog.ts",
            "Insurance INSURANCE insurance",
            "Total other income for the year",
        ):
            masked, n = m.mask_text(text)
            assert n == 0 and masked == text, text
        assert m.mask_text("Operations")[1] == 1 and m.mask_text("Other income")[1] == 1
        m.add_values("DIM", ["Atelier Zuid"], "t")  # not everyday words: masked, on word boundaries
        assert m.mask_text("cost of Atelier Zuid")[1] == 1
        assert m.mask_text("xAtelier Zuidx")[1] == 0

    def test_identity_values_with_everyday_words_keep_matching_in_prose(self) -> None:
        m = SessionMap(token_fn=lambda prefix, key: 30_000)
        m.add_values("PER", ["Smith"], "t")
        m.add_values("CUS", ["Global Investment Partners"], "t")
        masked, n = m.mask_text("Smith asked Global Investment Partners for a call")
        assert n == 2 and "Smith" not in masked and "Global" not in masked

    @pytest.mark.parametrize("identity_first", [True, False])
    def test_a_value_that_is_both_a_name_and_a_dimension_value_keeps_name_matching(
        self, identity_first: bool
    ) -> None:
        # an enterprise name that is also a security-key dimension value, made of words a
        # frequency list knows: whatever order the batches come in, it is masked inside text
        m = SessionMap(token_fn=lambda prefix, key: 40_000)
        name = "Vincent Group Holding"
        batches = [("ORG", [name]), ("DIM", [name])]
        for prefix, values in batches if identity_first else batches[::-1]:
            m.add_values(prefix, values, "t")
        for text in ('IN {"' + name + '"}', "filter on " + name + " only", '\\"' + name + '\\"'):
            masked, n = m.mask_text(text)
            assert n == 1 and name not in masked, (identity_first, text)

    def test_strengthening_changes_the_revision_and_keeps_the_token(self) -> None:
        m = SessionMap(token_fn=lambda prefix, key: 41_000)
        m.add_values("DIM", ["Other Income Group"], "t")
        token, rev = m.mask_text("Other Income Group")[0], m.revision
        assert m.mask_text("the Other Income Group line")[1] == 0  # report text while generic
        m.add_values("CUS", ["Other Income Group"], "t")
        assert m.revision > rev and m.mask_text("Other Income Group")[0] == token
        assert m.mask_text("the Other Income Group line")[1] == 1

    def test_strong_generic_values_get_identity_matching(self) -> None:
        m = SessionMap(token_fn=lambda prefix, key: 42_000)
        m.add_values("DIM", ["Main Street West"], "t", strong=True)  # an address of a vendor
        m.add_values("DIM", ["Other income"], "t")
        masked, n = m.mask_text("ship to Main Street West; Other income")
        assert n == 1 and "Main Street West" not in masked and "Other income" in masked

    def test_whitespace_and_unicode_form_differences_keep_the_canonical_token(
        self, smap: SessionMap
    ) -> None:
        canonical = tok(smap, "Janssens Holding")
        assert smap.mask_text("Janssens Holding")[0] == canonical
        assert smap.mask_text("Janssens   Holding")[0] == canonical

    def test_suffix_token_is_not_used_before_a_letter(self, smap: SessionMap) -> None:
        # "...L" followed by "td" would read back as token FAM_xxxxL + "td".
        out, n = smap.mask_text("janssens holdingltd")
        assert n == 1
        assert not out.startswith(tok(smap, "Janssens Holding") + "L")
        assert out.endswith("ltd")

    def test_iban_with_and_without_spaces(self, smap: SessionMap) -> None:
        for text in (IBAN_BE, IBAN_BE_SPACED, IBAN_BE.lower(), IBAN_BE_SPACED.lower()):
            out, n = smap.mask_text(f"iban: {text}.")
            assert n == 1 and "68" not in out and "5390" not in out
            assert roundtrip(smap, f"iban: {text}.") == f"iban: {text}."

    def test_json_escaped_form_of_non_ascii_value(self, smap: SessionMap) -> None:
        escaped = json.dumps("Café Müller")[1:-1]  # Café Müller as text
        assert "\\u00e9" in escaped
        out, n = smap.mask_text(f'{{"name": "{escaped}"}}')
        assert n == 1 and "u00e9" not in out
        assert roundtrip(smap, f'{{"name": "{escaped}"}}') == f'{{"name": "{escaped}"}}'
        assert smap.mask_text(escaped.upper())[1] == 1  # \U00C9 style hex escapes fold too

    def test_ampersand_entity_form(self, smap: SessionMap) -> None:
        out, n = smap.mask_text("Smith &amp; Sons")
        assert n == 1 and "Sons" not in out
        assert roundtrip(smap, "<b>Smith &amp; Sons</b>") == "<b>Smith &amp; Sons</b>"
        assert roundtrip(smap, "Smith & Sons") == "Smith & Sons"

    def test_dax_doubled_quote_form_round_trips(self, smap: SessionMap) -> None:
        dax = 'T[Name] = "O""Brien Trust"'
        out, n = smap.mask_text(dax)
        assert n == 1 and "Brien" not in out
        assert out == f'T[Name] = "{tok(smap, chr(79) + chr(34) + "Brien Trust")}"'
        assert smap.unmask_text(out)[0] == dax
        # outside a literal the doubled form keeps its own token and also round-trips
        plain = 'raw O""Brien Trust here'
        assert roundtrip(smap, plain) == plain


# ---------------------------------------------------------------- unmask escaping


class TestUnmaskEscaping:
    def test_json_context_escapes_real_value(self, smap: SessionMap) -> None:
        token = tok(smap, 'O"Brien Trust')
        plain, _ = smap.unmask_text(f"x {token} y", "plain")
        assert plain == 'x O"Brien Trust y'
        escaped, _ = smap.unmask_text(f"x {token} y", "json")
        assert escaped == 'x O\\"Brien Trust y'

    def test_dax_double_quote_literal_doubles_quotes(self, smap: SessionMap) -> None:
        token = tok(smap, 'O"Brien Trust')
        out, _ = smap.unmask_text(f'FILTER(T, T[n] = "{token}")')
        assert out == 'FILTER(T, T[n] = "O""Brien Trust")'
        out, _ = smap.unmask_text(f"-- {token} outside")
        assert out == '-- O"Brien Trust outside'

    def test_dax_literal_inside_json_fragment(self, smap: SessionMap) -> None:
        token = tok(smap, 'O"Brien Trust')
        raw = '{"query":"EVALUATE FILTER(T, T[n]=\\"' + token + '\\")"}'
        out, _ = smap.unmask_text(raw, "json")
        assert json.loads(out)["query"] == 'EVALUATE FILTER(T, T[n]="O""Brien Trust")'

    def test_prefix_gives_context_for_a_split_fragment(self, smap: SessionMap) -> None:
        token = tok(smap, 'O"Brien Trust')
        prefix = '{"query":"EVALUATE FILTER(T, T[n]=\\"'
        out, _ = smap.unmask_text(token + '\\")"}', "json", prefix=prefix)
        assert json.loads(prefix + out)["query"].endswith('"O""Brien Trust")')

    def test_new_json_string_resets_literal_state(self, smap: SessionMap) -> None:
        token = tok(smap, 'O"Brien Trust')
        raw = '{"a":"x=\\"y","b":"' + token + '"}'
        out, _ = smap.unmask_text(raw, "json")
        assert json.loads(out)["b"] == 'O"Brien Trust'

    def test_single_quoted_name_doubles_apostrophes(self, smap: SessionMap) -> None:
        token = tok(smap, "D'Hondt NV")
        out, _ = smap.unmask_text(f"CALCULATE(SUM('{token}'[Amount]))")
        assert out == "CALCULATE(SUM('D''Hondt NV'[Amount]))"

    def test_apostrophe_inside_a_word_is_not_a_quote(self, smap: SessionMap) -> None:
        token = tok(smap, "D'Hondt NV")
        out, _ = smap.unmask_text(f"Don't ask {token}, it's fine")
        assert out == "Don't ask D'Hondt NV, it's fine"

    def test_mask_then_unmask_dax_with_apostrophe_name(self, smap: SessionMap) -> None:
        dax = "SUM('D''Hondt NV'[Amount])"
        masked, _ = smap.mask_text(dax)
        assert "Hondt" not in masked
        assert smap.unmask_text(masked)[0] == dax


# ---------------------------------------------------------------- tokens


class TestTokens:
    def test_token_format_and_stability(self) -> None:
        m = SessionMap()
        assert m.add_values("FAM", ["Alpha Holding", "Beta Holding"], "s1") == 2
        t1 = m.mask_text("Alpha Holding")[0]
        assert t1.startswith("FAM_") and len(t1) == len("FAM_0000")
        assert m.add_values("FAM", ["alpha holding", "Gamma Holding"], "s2") == 1
        assert m.mask_text("Alpha Holding")[0] == t1  # same casefolded value, same token
        assert m.stats["values"] == 3

    def test_numbering_is_randomised_per_map(self) -> None:
        values = [f"Zeta Value {i:03d}" for i in range(60)]
        orders = []
        for _ in range(2):
            m = SessionMap()
            m.add_values("FAM", values, "t")
            orders.append([m.mask_text(v)[0] for v in values])
        assert orders[0] != orders[1]
        assert sorted(orders[0]) == sorted(orders[1])

    def test_new_batches_never_renumber(self) -> None:
        m = SessionMap()
        m.add_values("PER", [f"Person Number {i}" for i in range(20)], "a")
        before = {i: m.mask_text(f"Person Number {i}")[0] for i in range(20)}
        m.add_values("PER", [f"Other Person {i}" for i in range(20)], "b")
        assert before == {i: m.mask_text(f"Person Number {i}")[0] for i in range(20)}

    def test_five_digit_tokens_and_unknown_token_forms(self) -> None:
        m = SessionMap()
        m.add_values("FAM", [f"Zed{i:06d}Corp" for i in range(10001)], "bulk")
        assert m.unmask_text("FAM_10000")[0] != "FAM_10000"
        assert m.unmask_text("FAM_10002")[0] == "FAM_10002"  # one past the end
        assert m.stats["unknown_tokens"] == 1

    def test_unknown_token_shaped_strings_stay_and_are_counted(self, smap: SessionMap) -> None:
        out, n = smap.unmask_text("keep FAM_9999 and XYZ_0001 as is")
        assert out == "keep FAM_9999 and XYZ_0001 as is" and n == 0
        assert smap.stats["unknown_tokens"] == 2

    def test_token_adjacent_to_letters_and_digits_unmasks(self, smap: SessionMap) -> None:
        masked = smap.mask_text("xJanssens2024")[0]
        assert smap.unmask_text(masked)[0] == "xJanssens2024"

    def test_count_leaks_counts_without_returning_values(self, smap: SessionMap) -> None:
        text = "Janssens Holding, Janssens and Marie Janssens"
        assert smap.count_leaks(text) == 3
        masked, _ = smap.mask_text(text)
        assert smap.count_leaks(masked) == 0
        assert smap.count_leaks("") == 0


# ---------------------------------------------------------------- shape detectors


def _nn(base: int, post2000: bool = False, wrong: bool = False) -> str:
    n = (2_000_000_000 + base) if post2000 else base
    check = 97 - n % 97
    if wrong:
        check = (check % 97) + 1
    return f"{base:09d}{check:02d}"


class TestShapeDetectors:
    def test_email_is_masked_and_added_with_usr_prefix(self) -> None:
        m = SessionMap()
        out, n = m.mask_text("write to Lucas.Peeters+x@Example.BE, thanks")
        assert n == 1 and out.startswith("write to USR_") and "Peeters" not in out
        assert m.counts()["shape"]["USR"] == 1
        assert m.unmask_text(out)[0] == "write to Lucas.Peeters+x@Example.BE, thanks"
        assert m.mask_text("no mail here a@b")[1] == 0

    @pytest.mark.parametrize("iban", [IBAN_BE, IBAN_BE_SPACED, IBAN_NL, IBAN_DE])
    def test_valid_iban_not_in_dictionary_is_masked(self, iban: str) -> None:
        m = SessionMap()
        out, n = m.mask_text(f"pay {iban} today")
        assert n == 1 and out.startswith("pay ACC_") and out.endswith(" today")
        assert m.unmask_text(out)[0] == f"pay {iban} today"
        assert m.counts()["shape"]["ACC"] >= 1

    def test_invalid_iban_checksum_is_left_alone(self) -> None:
        m = SessionMap()
        bad = IBAN_BE[:-1] + "5"
        assert not iban_is_valid(bad) and iban_is_valid(IBAN_BE)
        assert m.mask_text(f"pay {bad} today")[1] == 0
        assert m.mask_text("pay BE99 5390 0754 7034 today")[1] == 0

    def test_iban_followed_by_a_number_group_is_cut_at_the_right_length(self) -> None:
        m = SessionMap()
        out, n = m.mask_text(f"{IBAN_BE_SPACED} 1234")
        assert n == 1 and out.endswith(" 1234")

    def test_belgian_national_number(self) -> None:
        m = SessionMap()
        good = _nn(850730033)
        assert be_national_number_is_valid(good)
        out, n = m.mask_text(f"rrn {good} end")
        assert n == 1 and good not in out
        formatted = f"{good[0:2]}.{good[2:4]}.{good[4:6]}-{good[6:9]}.{good[9:11]}"
        out, n = m.mask_text(f"rrn {formatted} end")
        assert n == 1 and "-" not in out
        assert m.unmask_text(out)[0] == f"rrn {formatted} end"
        post2000 = _nn(123456789, post2000=True)
        assert be_national_number_is_valid(post2000)
        assert m.mask_text(post2000)[1] == 1

    def test_belgian_national_number_wrong_checksum_or_decimal(self) -> None:
        m = SessionMap()
        bad = _nn(850730033, wrong=True)
        assert not be_national_number_is_valid(bad)
        assert m.mask_text(f"amount {bad}")[1] == 0
        good = _nn(850730033)
        assert m.mask_text(f"ratio 0.{good}")[1] == 0
        assert m.mask_text(f"ratio {good}.5")[1] == 0

    def test_belgian_enterprise_number(self) -> None:
        m = SessionMap()
        assert be_kbo_is_valid("0403019261") and not be_kbo_is_valid("0403019262")
        for form in ("BE0403019261", "BE 0403.019.261", "be0403.019.261"):
            out, n = m.mask_text(f"vat {form} ok")
            assert n == 1 and "019" not in out, form
        assert m.mask_text("vat BE0403019262 ok")[1] == 0

    def test_e164_phone(self) -> None:
        m = SessionMap()
        for phone in ("+32 475 12 34 56", "+32.475.12.34.56", "+32-475-123456", "+14155552671"):
            out, n = m.mask_text(f"call {phone}!")
            assert n == 1 and "47" not in out.replace("CLI_", ""), phone
            assert m.unmask_text(out)[0] == f"call {phone}!"
        assert m.mask_text("call +3247512")[1] == 0  # too short
        assert m.mask_text("sum [x]+12345678")[1] == 0  # arithmetic, not a phone

    def test_shape_hit_variants_and_detection_with_existing_dictionary(
        self, smap: SessionMap
    ) -> None:
        out, n = smap.mask_text(f"{IBAN_NL} and {IBAN_NL.lower()}")
        assert n == 2
        assert smap.count_leaks(out) == 0
        assert smap.count_leaks("mail x.y@z.be") == 1


# ---------------------------------------------------------------- tree walk


def mask_tree(smap: SessionMap, obj: object, pre=None) -> object:
    """Mask every string and key of a JSON-like tree through the engine's own walker (the one
    ``unmask_tree`` uses; the request masker has its own, see client_gateway.masking)."""

    def leaf(value: str) -> str:
        if pre is not None:
            hit = pre(value)
            if hit is not None:
                return hit
        return smap.mask_text(value)[0]

    return smap._walk(obj, leaf, lambda key: smap.mask_text(key)[0], MaskingError(MASK_FAILED))


class TestTrees:
    def test_dict_keys_sets_and_tuples(self, smap: SessionMap) -> None:
        tree = {
            "Janssens Holding": ["x", {"a": "Janssens Holding"}],
            "s": {"Janssens Holding"},
            "t": ("Janssens Holding", 3),
            "n": 5,
        }
        out = mask_tree(smap, tree)
        text = json.dumps(out)
        assert "Janssens" not in text
        assert out["s"] == [tok(smap, "Janssens Holding")]
        assert out["t"] == [tok(smap, "Janssens Holding"), 3]
        assert out["n"] == 5
        assert tok(smap, "Janssens Holding") in out
        assert smap.unmask_tree(out)["t"][0] == "Janssens Holding"

    def test_skipped_blocks_and_keys_are_untouched(self, smap: SessionMap) -> None:
        sig = "Eu8BCkYIBxgCKkA" + "A" * 80
        blob = "iVBORw0KGgo" + "B" * 80
        msg = {
            "content": [
                {"type": "thinking", "thinking": "about Janssens Holding", "signature": sig},
                {"type": "redacted_thinking", "data": blob + "Janssens"},
                {"type": "image", "source": {"type": "base64", "data": blob}},
                {"type": "document", "title": "Janssens Holding"},
                {"type": "text", "text": "Janssens Holding", "signature": sig},
            ]
        }
        out = mask_tree(smap, msg)
        c = out["content"]
        assert c[0] == msg["content"][0]
        assert c[1] == msg["content"][1]
        assert c[2] == msg["content"][2]
        assert c[3] == msg["content"][3]
        assert c[4]["text"] == tok(smap, "Janssens Holding") and c[4]["signature"] == sig
        assert smap.unmask_tree(out)["content"][0] == msg["content"][0]

    def test_data_key_with_real_text_is_masked_but_base64_is_not(self, smap: SessionMap) -> None:
        out = mask_tree(smap, {"data": "Janssens Holding", "signature": "C" * 64})
        assert out["data"] == tok(smap, "Janssens Holding")
        assert out["signature"] == "C" * 64

    def test_pre_hook_replaces_strings(self, smap: SessionMap) -> None:
        out = mask_tree(smap, ["a", "b"], pre=lambda s: "memo" if s == "a" else None)
        assert out == ["memo", "b"]

    def test_unmask_tree_reports_changed_strings(self, smap: SessionMap) -> None:
        seen: list[tuple[str, str]] = []
        masked = mask_tree(smap, {"k": ["Janssens Holding", "plain"]})
        smap.unmask_tree(masked, on_string=lambda m, u: seen.append((m, u)))
        assert seen == [(tok(smap, "Janssens Holding"), "Janssens Holding")]


# ---------------------------------------------------------------- failure path


class TestFailClosed:
    def test_internal_error_raises_masking_error_without_input_text(
        self, smap: SessionMap, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "Janssens Holding secret text"

        def boom(self: SessionMap, norm: str) -> list[tuple[int, int, object]]:
            raise RuntimeError(norm)  # an exception that WOULD carry the text

        monkeypatch.setattr(SessionMap, "_dictionary_hits", boom)
        with pytest.raises(MaskingError) as info:
            smap.mask_text(secret)
        err = info.value
        assert str(err) == MASK_FAILED
        assert "Janssens" not in repr(err) and "secret" not in repr(err)
        assert err.__cause__ is None and err.__context__ is None

    def test_tree_walk_failure_is_fixed_text(
        self, smap: SessionMap, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(SessionMap, "_mask_text", lambda self, s: 1 / 0)
        with pytest.raises(MaskingError) as info:
            mask_tree(smap, {"a": ["Janssens Holding"]})
        assert str(info.value) == MASK_FAILED and info.value.__context__ is None

    def test_unmask_failure_is_fixed_text(
        self, smap: SessionMap, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(SessionMap, "_unmask_text", lambda self, s, c, p: 1 / 0)
        with pytest.raises(MaskingError) as info:
            smap.unmask_text("FAM_0001")
        assert str(info.value) == UNMASK_FAILED

    def test_too_deep_tree_is_a_masking_error(self, smap: SessionMap) -> None:
        deep: list[object] = []
        node = deep
        for _ in range(5000):
            nxt: list[object] = []
            node.append(nxt)
            node = nxt
        with pytest.raises(MaskingError):
            mask_tree(smap, deep)

    def test_empty_and_unknown_inputs(self, smap: SessionMap) -> None:
        assert smap.mask_text("") == ("", 0)
        assert smap.unmask_text("") == ("", 0)
        assert SessionMap().mask_text("nothing to see") == ("nothing to see", 0)
        assert SessionMap().unmask_text("FAM_0001") == ("FAM_0001", 0)


# ---------------------------------------------------------------- performance


def test_mask_one_megabyte_against_20k_values_is_fast() -> None:
    rnd = random.Random(7)

    def word(n: int) -> str:
        return "".join(rnd.choice(string.ascii_lowercase) for _ in range(n))

    names = sorted(
        {
            f"{word(rnd.randint(6, 10)).title()} {word(rnd.randint(5, 9)).title()}"
            for _ in range(20_000)
        }
    )
    m = SessionMap()
    m.add_values("FAM", names, "perf")
    assert m.stats["values"] >= 19_000

    parts = [word(rnd.randint(2, 9)) for _ in range(150_000)]
    for i in range(0, len(parts), 3_000):
        parts[i] = names[(i // 3_000) % 50]
    text = " ".join(parts)[:1_000_000]
    assert len(text) >= 900_000
    m.mask_text("warm up")  # builds the automaton

    best = min(_timed(m, text) for _ in range(3))
    # Target is 200 ms; the assertion keeps a 2x margin for slow CI machines.
    assert best < 0.4, f"mask_text took {best * 1000:.0f} ms"
    masked, hits = m.mask_text(text)
    assert hits >= 50
    assert m.count_leaks(masked) == 0
    assert m.unmask_text(masked)[0] == text


def _timed(m: SessionMap, text: str) -> float:
    start = time.perf_counter()
    m.mask_text(text)
    return time.perf_counter() - start


# ---------------------------------------------------------------- slug / URL forms, token spans


class TestSlugFormsAndTokenSpans:
    def test_slug_url_and_accent_stripped_forms_are_masked_and_restored_exactly(self) -> None:
        m = SessionMap()
        m.add_values("ORG", ["Janssens Holding", "Société Générale"], "t")
        texts = [
            "Janssens_Holding_2025.xlsx",
            "janssens-holding",
            "JanssensHolding.pbix",
            r"C:\Clients\Janssens_Holding\y.pbip",
            "https://x.sharepoint.example/Janssens%20Holding/Docs",
            "q=Janssens+Holding",
            "Societe Generale",
            "Societe_Generale.xlsx",
            "Soci%C3%A9t%C3%A9%20G%C3%A9n%C3%A9rale",
        ]
        for text in texts:
            masked, n = m.mask_text(text)
            assert n >= 1, text
            assert m.count_leaks(masked) == 0, text
            low = masked.lower()
            assert "janssens" not in low and "societe" not in low and "soci%c3" not in low, text
            assert m.unmask_text(masked)[0] == text, text

    def test_slug_variants_are_not_counted_as_values(self) -> None:
        m = SessionMap()
        assert m.add_values("ORG", ["Janssens Holding"], "t") == 1
        assert m.stats["values"] == 1
        assert m.counts() == {"t": {"ORG": 1}}

    def test_quote_and_backslash_json_escapes_inside_nested_json_text(self) -> None:
        m = SessionMap()
        value = 'Acme "Prime" Holding'
        m.add_values("ORG", [value], "t")
        inner = json.dumps({"name": value})  # a JSON document ...
        outer = json.dumps({"payload": inner})  # ... stored inside a JSON string
        for text in (inner, outer):
            masked, n = m.mask_text(text)
            assert n >= 1 and "Prime" not in masked, text
            assert m.unmask_text(masked)[0] == text

    def test_a_dictionary_value_equal_to_another_values_token_digits_is_no_leak(self) -> None:
        # a 5-digit account number is a whole word inside "CUS_48213"
        m = SessionMap(token_fn=lambda prefix, key: 48_213)
        m.add_values("CUS", ["Zorvex Holding"], "t")
        m.add_values("GLA", ["48213"], "t")
        masked, n = m.mask_text("see Zorvex Holding")
        assert n == 1 and masked == "see CUS_48213"
        assert m.count_leaks(masked) == 0
        # a letterless GENERIC value (an account number, an amount band) is a whole cell only
        assert m.count_leaks("account 48213 here") == 0 and m.count_leaks("48213") == 1
        # a pseudonym-shaped string in OUTGOING text is real data that looks like one (the model
        # holds no pseudonym of ours there): it gets a pseudonym of its own and comes back as typed
        out = m.mask_text("CUS_48213 pays 48213")[0]
        assert out.endswith(" pays 48213") and not out.startswith("CUS_48213 ")
        assert m.unmask_text(out)[0] == "CUS_48213 pays 48213"
        assert m.unmask_text(masked)[0] == "see Zorvex Holding"

    def test_leak_check_ignores_case_suffixed_tokens_too(self) -> None:
        m = SessionMap(token_fn=lambda prefix, key: 48_213)
        m.add_values("CUS", ["Zorvex Holding"], "t")
        m.add_values("GLA", ["48213"], "t")
        masked, _ = m.mask_text("ZORVEX HOLDING.")
        assert masked.startswith("CUS_48213U") and m.count_leaks(masked) == 0
