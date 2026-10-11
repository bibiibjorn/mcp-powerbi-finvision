"""Matching gaps found by the 2026-10-03 deep review (G-04, 07, 08, 09, 14, 17, 21, 02a, 06).

Synthetic names only."""

from __future__ import annotations

import threading
import time

import pytest

from core.client_guard.engine import SessionMap, _identifier_context, normalise


def _round_trip(m: SessionMap, text: str) -> str:
    out, n = m.mask_text(text)
    assert n >= 1, f"not masked: {text!r}"
    assert m.unmask_text(out)[0] == text
    return out


class TestPersonForms:  # G-04
    @pytest.fixture()
    def m(self) -> SessionMap:
        m = SessionMap()
        m.add_values("PER", ["Jan Peeters", "Lotte Vanhoutte"], "t")
        return m

    @pytest.mark.parametrize(
        "text",
        [
            "Peeters said hello",
            "Mr Peeters",
            "Peeters, Jan",
            "J.Peeters",
            "J. Peeters",
            "PEETERS",
            "Peeters Jan",
            "Jan P. called",
            "Vanhoutte replied",
        ],
    )
    def test_a_short_first_name_does_not_hide_the_surname(self, m: SessionMap, text: str) -> None:
        out = _round_trip(m, text)
        assert "Peeters" not in out and "PEETERS" not in out and "Vanhoutte" not in out

    def test_a_value_stored_last_name_first_masks_the_surname_too(self) -> None:
        m = SessionMap()
        m.add_values("PER", ["Peeters Jan"], "t")
        out = _round_trip(m, "Peeters in the report")
        assert "Peeters" not in out
        _round_trip(m, "Jan Peeters")

    def test_first_name_alone_stays_readable(self, m: SessionMap) -> None:
        assert m.mask_text("Jan opened the file")[1] == 0
        assert m.mask_text("Lotte opened the file")[1] == 0

    def test_person_forms_do_not_count_as_values(self, m: SessionMap) -> None:
        m.mask_text("Peeters, Jan and J.Peeters")
        assert m.stats["values"] == 2


class TestInvisibleCharacters:  # G-07
    @pytest.mark.parametrize(
        "ch", ["\u00ad", "\u200c", "\u200d", "\u2060", "\ufeff", "\u200e", "\u202a"]
    )
    def test_format_characters_inside_a_value_do_not_hide_it(self, ch: str) -> None:
        m = SessionMap()
        m.add_values("ORG", ["Acme Holding"], "t")
        text = f"see Acme{ch}Holding now"
        out = _round_trip(m, text)
        assert "Acme" not in out and m.count_leaks(out) == 0

    def test_normalise_drops_them_and_the_offset_map_still_lines_up(self) -> None:
        assert normalise("Ac\u00adme\ufeff") == "acme"
        m = SessionMap()
        m.add_values("ORG", ["Acme Holding"], "t")
        text = "x\u00adAcme\u00ad Holding\u2060y é"
        out, n = m.mask_text(text)
        assert n == 1 and out.endswith(" é") and out.startswith("x")
        assert m.unmask_text(out)[0] == text


class TestDiacritics:  # G-08
    @pytest.fixture()
    def m(self) -> SessionMap:
        m = SessionMap()
        m.add_values(
            "CUS", ["Zorvex Industries NV", "Muller Bouwwerken", "Jose Vandenbroucke"], "t"
        )
        return m

    @pytest.mark.parametrize(
        "text",
        [
            "Zörvex Industries NV",
            "Zörvex",
            "Müller Bouwwerken",
            "José Vandenbroucke",
            "JOSÉ VANDENBROUCKE",
        ],
    )
    def test_an_accented_spelling_of_an_ascii_value_is_masked(
        self, m: SessionMap, text: str
    ) -> None:
        out = _round_trip(m, text)
        assert not any(w in out for w in ("rvex", "ller", "Jos", "JOS"))

    def test_a_typo_in_the_distinctive_word_next_to_a_part_is_masked(self, m: SessionMap) -> None:
        out = _round_trip(m, "Zorvx Industries NV")
        assert "Zorvx" not in out and "Industries" not in out

    def test_an_accented_value_matches_the_plain_spelling(self) -> None:
        m = SessionMap()
        m.add_values("PER", ["Éloïse Dupré"], "t")
        _round_trip(m, "Eloise Dupre wrote")


class TestPseudonymCollisions:  # G-09
    def test_a_dictionary_code_shaped_like_a_pseudonym_is_still_masked(self) -> None:
        # entity B's pseudonym is CUS_10001; entity A's REAL customer code is also CUS_10001
        def token_fn(prefix: str, key: str) -> int:
            return 10_001 if key == "zorvex holding" else 55_555

        m = SessionMap(token_fn=token_fn)
        m.add_values("CUS", ["Zorvex Holding"], "t")  # token CUS_10001
        m.add_values("CUS", ["CUS_10001"], "t")  # a real code of the same shape
        out, n = m.mask_text("code CUS_10001 is open")
        assert n == 1 and out.startswith("code CUS_555")
        assert m.unmask_text(out)[0] == "code CUS_10001 is open"

    def test_a_real_code_that_is_not_in_the_dictionary_gets_its_own_pseudonym(self) -> None:
        m = SessionMap(token_fn=lambda prefix, key: 10_001)
        m.add_values("CUS", ["Zorvex Holding"], "t")  # its pseudonym is CUS_10001
        masked_name = m.mask_text("Zorvex Holding")[0]
        assert masked_name == "CUS_10001"
        out, n = m.mask_text("export CUS_10001 and Zorvex Holding")
        assert n == 2
        real_code_token, name_token = out.split(" ")[1], out.split(" ")[3]
        assert real_code_token != name_token == "CUS_10001"
        # the model echoes both: each comes back as what it was
        back = m.unmask_text(f"{real_code_token} / {name_token}")[0]
        assert back == "CUS_10001 / Zorvex Holding"

    def test_unrelated_text_is_not_touched(self) -> None:
        m = SessionMap(token_fn=lambda prefix, key: 10_001)
        m.add_values("CUS", ["Zorvex Holding"], "t")
        assert m.mask_text("nothing here, ORD_77777")[1] == 0


class TestAutomatonLocking:  # G-14
    def test_unmasking_does_not_wait_for_a_long_masking_call(self) -> None:
        m = SessionMap()
        m.add_values("CUS", ["Zorvex Holding"], "t")
        token = m.mask_text("Zorvex Holding")[0]
        done: list[float] = []

        def unmask() -> None:
            t0 = time.perf_counter()
            assert m.unmask_text(f"x {token} y")[0] == "x Zorvex Holding y"
            done.append(time.perf_counter() - t0)

        with m._lock:  # a masking call that takes a long time holds this
            t = threading.Thread(target=unmask)
            t.start()
            t.join(2.0)
            assert not t.is_alive(), "unmask waited for the masking lock"
        assert done and done[0] < 1.0

    def test_a_new_value_is_matched_at_once_without_rebuilding_the_big_automaton(self) -> None:
        m = SessionMap(fuzzy=False)
        m.add_values("CUS", [f"Customer Number{i:05d} Holding" for i in range(3000)], "t")
        m.mask_text("warm up Customer Number00001 Holding")  # builds the big automaton once
        main_before = m._key_auto._main
        m.add_values("PER", ["Lotte Vanhoutte"], "t")
        out, n = m.mask_text("Lotte Vanhoutte wrote")
        assert n == 1 and "Lotte" not in out
        assert m._key_auto._main is main_before  # not rebuilt: the new key sits in the pending set

    def test_the_big_automaton_is_replaced_in_the_background_when_pending_grows(self) -> None:
        m = SessionMap(fuzzy=False)
        m._key_auto._threshold = 5
        m._key_auto._background = False  # compact inline for the test
        m.add_values("CUS", ["Zorvex Holding"], "t")
        m.mask_text("Zorvex Holding")
        for i in range(10):
            m.add_values("PER", [f"Lotte Vanhoutte{chr(97 + i)}x"], "t")
        m.mask_text("go")  # pending > threshold: compacts
        assert len(m._key_auto._pending) <= 5
        assert m.mask_text("Zorvex Holding")[1] == 1
        assert m.mask_text("Lotte Vanhouttejx")[1] == 1


class TestIdentifierContext:  # G-17
    def test_helper(self) -> None:
        assert _identifier_context("a/peeters/b", 2, 9)
        assert _identifier_context("peeters_q3", 0, 7)
        assert not _identifier_context("said peeters here", 5, 12)

    def test_lower_case_surname_and_acronym_in_file_names_and_paths(self) -> None:
        m = SessionMap()
        m.add_values("PER", ["Jan Peeters", "Lotte Vanhoutte"], "t")
        m.add_values("ORG", ["KBC"], "t")
        text = "see c:/clients/peeters/report.xlsx and vanhoutte_q3.xlsx and kbc_report.xlsx"
        out = _round_trip(m, text)
        assert "peeters" not in out and "vanhoutte" not in out and "kbc" not in out
        # in running text the lower-case words stay readable
        assert m.mask_text("the kbc said that peeters left")[1] == 0


class TestLooseTokens:  # G-21
    @pytest.fixture()
    def m(self) -> SessionMap:
        m = SessionMap(token_fn=lambda prefix, key: 60_818)
        m.add_values("CUS", ["Janssens Holding"], "t")
        return m

    def test_lower_case_token_in_a_file_name(self, m: SessionMap) -> None:
        assert m.unmask_text("cus_60818_q3.xlsx")[0] == "janssens holding_q3.xlsx"

    def test_markdown_escaped_token(self, m: SessionMap) -> None:
        assert m.unmask_text("CUS\\_60818 owes")[0] == "Janssens Holding owes"
        assert m.unmask_text("CUS\\\\_60818", "json")[0] == "Janssens Holding"

    def test_unknown_or_glued_forms_are_left_alone(self, m: SessionMap) -> None:
        assert m.unmask_text("xcus_60818 and cus_60999")[1] == 0


class TestRecognizedValues:  # G-02a, G-06
    class Rec:
        def __init__(self, found, boom=False):
            self.found, self.boom = found, boom

        def find_many(self, texts, complete=False):
            assert complete
            if self.boom:
                raise RuntimeError("model down")
            return list(self.found)

    def test_new_recognized_values_are_reported_once(self) -> None:
        seen: list[list[tuple[str, str]]] = []
        m = SessionMap(
            recognizers=[self.Rec([("Lotte Vanhoutte", "PER")])], on_recognized=seen.append
        )
        m.mask_text("Mail from Lotte Vanhoutte")
        m.mask_text("Mail from Lotte Vanhoutte again")
        assert seen == [[("Lotte Vanhoutte", "PER")]]

    def test_a_failing_callback_never_breaks_masking(self) -> None:
        def boom(_new):
            raise OSError("disk")

        m = SessionMap(recognizers=[self.Rec([("Lotte Vanhoutte", "PER")])], on_recognized=boom)
        out, n = m.mask_text("Mail from Lotte Vanhoutte")
        assert n == 1 and "Lotte" not in out

    def test_recognize_fails_closed_like_a_request_text(self) -> None:
        m = SessionMap(recognizers=[self.Rec([], boom=True)])
        with pytest.raises(RuntimeError):
            m.recognize("OCR text of a screenshot")


class TestNumericIdentities:  # G-16 (engine side)
    def test_a_numeric_customer_number_matches_as_a_whole_word_only(self) -> None:
        m = SessionMap()
        m.add_values("CUS", ["123456", "VDK"], "t")
        assert m.mask_text("order 123456 is open")[1] == 1
        assert m.mask_text("amount 1234567 and 9123456")[1] == 0  # inside another number
        assert m.mask_text("id123456x")[1] == 0
        assert m.mask_text("VDK")[1] == 1 and m.mask_text("the VDK file")[1] == 1
        assert m.mask_text("the vdk file")[1] == 0  # an acronym only in capitals in running text
