"""Name recognition coverage found by the 2026-10-03 deep review (G-01, 02, 05, 06, 12, 13, 18).

Synthetic names only; fake recognizers, no model, no network."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from client_gateway import masking
from client_gateway.masking import MaskingFailed, mask_request
from client_gateway.memo import RemaskMemo
from client_gateway.reminders import is_static, recognition_texts
from client_gateway.state import GatewayState
from core.client_guard import ner as ner_mod
from core.client_guard.engine import SessionMap, ordinary_word
from core.client_guard.ner import NerIncomplete, NerRecognizer, keep, prose_segments

JSON = "application/json"
PERSON = "Lotte Vanhoutte"


class Seen:
    """A recognizer that knows one synthetic person and records every text it is shown."""

    def __init__(self, fail: Exception | None = None, fail_history: bool = False) -> None:
        self.texts: list[str] = []
        self.strict: list[str] = []  # what was checked strictly (complete=True: the new text)
        self.fail = fail
        self.fail_history = fail_history

    def find_many(
        self,
        texts: list[str],
        complete: bool = False,
        budget_s: float | None = None,
        wait: bool = True,
    ) -> list[tuple[str, str]]:
        if self.fail is not None and (complete or self.fail_history):
            raise self.fail
        self.texts.extend(texts)
        if complete:
            self.strict.extend(texts)
        return [(PERSON, "PER")] if any(PERSON in t for t in texts) else []


class _HistoryNeverFinishes:
    """Best-effort calls (the earlier conversation) never finish; strict calls (the new text) do."""

    def find_many(self, texts, complete=False, budget_s=None, wait=True):
        if not complete:
            raise NerIncomplete("timeout_or_worker")
        return []


@pytest.fixture(autouse=True)
def _history_inline(monkeypatch):
    """The background part of the history scan runs inline, so the tests are deterministic."""
    import client_gateway.masking as masking_mod

    monkeypatch.setattr(masking_mod, "_background", lambda fn: fn())


def make_state(tmp_path, recognizer: Any) -> GatewayState:
    smap = SessionMap(token_fn=lambda prefix, key: 60_000, recognizers=[recognizer])
    return GatewayState(home=tmp_path, default_mode="on", images_policy="withhold", smap=smap)


def run(state: GatewayState, memo: RemaskMemo, messages: list[dict], **kw: Any) -> dict:
    body = json.dumps({"model": "m", "messages": messages}).encode()
    return json.loads(mask_request(body, JSON, state, memo, **kw))


def user(*texts: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": t} for t in texts]}


# ------------------------------------------------------------------------------ G-01
class TestReminders:
    def test_static_instruction_is_recognised_and_attachments_are_not(self) -> None:
        assert is_static("As you answer the user's questions, you can use the following context:")
        assert is_static("Contents of C:\\x\\CLAUDE.md (user's private global instructions):")
        assert is_static("gitStatus: This is the git status at the start of the conversation.")
        assert not is_static("Called the Read tool with the following input: {...}")
        assert not is_static("Result of calling the Read tool:\n" + PERSON + " wrote")
        assert not is_static("The user selected the lines 3 to 5 from mail.txt:\n" + PERSON)

    def test_pieces(self) -> None:
        text = (
            "hello <system-reminder>Result of calling the Read tool:\nMail from "
            + PERSON
            + "</system-reminder> and <system-reminder>Codebase and user instructions are shown "
            "below</system-reminder>"
        )
        pieces = recognition_texts(text)
        assert len(pieces) == 2 and pieces[0].startswith("hello")
        assert PERSON in pieces[1] and "Codebase" not in " ".join(pieces)
        assert recognition_texts("   ") == []

    def test_an_attached_file_reaches_name_recognition_and_is_masked(self, tmp_path) -> None:
        rec = Seen()
        state = make_state(tmp_path, rec)
        attached = (
            '<system-reminder>Called the Read tool with the following input: {"file_path": '
            '"mail.txt"}</system-reminder><system-reminder>Result of calling the Read tool:\n'
            f"Hello, this is a mail from {PERSON} about an invoice.</system-reminder>"
        )
        out = run(state, RemaskMemo(), [user("please summarise", attached)])
        assert any(PERSON in t for t in rec.texts)
        assert PERSON not in json.dumps(out)

    def test_an_ide_selection_is_read_too(self, tmp_path) -> None:
        rec = Seen()
        state = make_state(tmp_path, rec)
        sel = f"<system-reminder>The user selected the lines 1 to 2 from n.md:\nNote for {PERSON}.</system-reminder>"
        out = run(state, RemaskMemo(), [user("what is this?", sel)])
        assert PERSON not in json.dumps(out)

    def test_static_claude_md_is_not_sent_to_the_model(self, tmp_path) -> None:
        rec = Seen()
        state = make_state(tmp_path, rec)
        static = (
            "<system-reminder>As you answer the user's questions, you can use the following "
            f"context: Contents of CLAUDE.md (user's private global instructions): {PERSON}"
            "</system-reminder>"
        )
        run(state, RemaskMemo(), [user("hi there friend", static)])
        assert not any(PERSON in t for t in rec.texts)


# ------------------------------------------------------------------------------ G-02
class TestHistoryScan:
    def test_first_masked_request_reads_the_whole_history_once(self, tmp_path) -> None:
        rec = Seen()
        state = make_state(tmp_path, rec)
        memo = RemaskMemo()
        history = [
            user(f"Mail from {PERSON} about the invoice."),
            {"role": "assistant", "content": [{"type": "text", "text": "Noted, thanks a lot."}]},
            user("and what about the second invoice?"),
        ]
        out = run(state, memo, history)  # a restart: the memo is empty, the name is old history
        assert PERSON not in json.dumps(out) and memo.history_scanned
        assert not any(PERSON in t for t in rec.strict)  # history: best effort, never strict
        rec.texts.clear()
        run(state, memo, history + [user("one more question please")])
        assert not any(PERSON in t for t in rec.texts)  # only the new message this time

    def test_switching_masking_on_again_reads_the_history_again(self, tmp_path) -> None:
        state = make_state(tmp_path, Seen())
        sess = state.session("s1")
        sess.memo.history_scanned = True
        state.set_mode("s1", "on")
        assert sess.memo.history_scanned is False

    def test_a_history_that_cannot_be_checked_never_blocks_the_new_message(self, tmp_path) -> None:
        # 2026-10-07 incident: a day-long chat switched to masking on; the strict whole-history
        # scan never finished and every message was refused with "send it again"
        state = make_state(tmp_path, _HistoryNeverFinishes())
        memo = RemaskMemo()
        history = [user(f"old message number {i} about the books") for i in range(200)]
        out = run(state, memo, history + [user("the new question")])  # not refused
        assert memo.history_scanned
        assert out["messages"][-1]["content"][0]["text"] == "the new question"

    def test_new_text_that_cannot_be_checked_in_time_goes_on(self, tmp_path) -> None:
        # owner rule 2026-10-07: never a held or refused message; counted instead
        state = make_state(tmp_path, Seen(fail=NerIncomplete("timeout_or_worker")))
        run(state, RemaskMemo(), [user(f"Mail from {PERSON} about the invoice.")])
        assert state.stats()["ner_partial"] == 1

    def test_recognized_names_are_remembered_across_a_restart(self, tmp_path) -> None:
        from client_gateway.detectors import Detectors

        first = Detectors(tmp_path)
        if first.store is None or not first.store.available:
            pytest.skip("DPAPI store not available")
        smap = first.session_map(lambda prefix, key: 60_000)
        smap._recognizers = [Seen()]
        smap.mask_text(f"Mail from {PERSON} about the invoice.")
        second = Detectors(tmp_path)  # a gateway restart: same home, empty memory
        fresh = second.session_map(lambda prefix, key: 60_000)
        second.load_static(fresh)
        out, n = fresh.mask_text(f"the earlier chat said {PERSON} twice")
        assert n >= 1 and PERSON not in out


# ------------------------------------------------------------------------------ G-05 / G-12
class TestRecognizerLifecycle:
    def test_a_failed_load_is_retried_after_the_backoff(self) -> None:
        calls: list[int] = []

        class Model:
            def batch_predict_entities(self, texts, labels, threshold=0.5):
                return [[{"text": PERSON, "label": "person"}] for _ in texts]

        def loader(name: str) -> Model:
            calls.append(1)
            if len(calls) == 1:
                raise OSError("hub unreachable")
            return Model()

        rec = NerRecognizer(ordinary_word, loader=loader, wait_s=5, retry_s=0.05)
        text = f"Mail from {PERSON} about the invoice and more words."
        with pytest.raises(NerIncomplete):
            rec.find_many([text], complete=True)
        assert rec.state.startswith("failed")
        time.sleep(0.1)
        assert rec.find_many([text], complete=True) == [(PERSON, "PER")]
        assert len(calls) == 2 and rec.state == "ready"

    def test_the_backoff_doubles_and_no_retry_storm(self) -> None:
        calls: list[int] = []

        def loader(name: str):
            calls.append(1)
            raise OSError("no")

        rec = NerRecognizer(ordinary_word, loader=loader, wait_s=5, retry_s=60)
        text = f"Mail from {PERSON} about the invoice and more words."
        for _ in range(3):
            with pytest.raises(NerIncomplete):
                rec.find_many([text], complete=True)
        assert len(calls) == 1  # inside the backoff: not tried again

    def test_an_idle_model_is_unloaded_unless_a_masked_chat_needs_it(self) -> None:
        closed: list[int] = []

        class Model:
            def batch_predict_entities(self, texts, labels, threshold=0.5):
                return [[] for _ in texts]

            def close(self) -> None:
                closed.append(1)

        rec = NerRecognizer(ordinary_word, loader=lambda n: Model(), wait_s=5, idle_s=0.2)
        rec.keep_alive = lambda: True
        text = "A plain sentence with enough words in it to count as prose."
        rec.find_many([text], complete=True)
        time.sleep(0.5)
        assert rec.ready() and not closed  # a masked chat exists: stays loaded
        rec.keep_alive = lambda: False
        deadline = time.time() + 5
        while time.time() < deadline and rec.ready():
            time.sleep(0.1)
        assert not rec.ready() and closed and rec.state == "not_started"

    def test_the_safety_net_starts_the_model_lazily_and_only_for_prose(self, tmp_path) -> None:
        started: list[int] = []

        class Ner:
            def ready(self) -> bool:
                return False

            def start(self) -> None:
                started.append(1)

        class Det:
            ner = Ner()

        state = GatewayState(home=tmp_path, default_mode="off", images_policy="withhold")
        state.detectors = Det()
        body = json.dumps({"messages": [user("Hello, this is a plain question about a report.")]})
        masking.client_values_in_last_message(body.encode(), JSON, state)
        assert started == [1]
        started.clear()
        masking.client_values_in_last_message(
            json.dumps({"messages": [user("")]}).encode(), JSON, state
        )
        assert started == []


# ------------------------------------------------------------------------------ G-06
def test_ocr_text_is_recognized_completely_or_the_image_is_withheld(monkeypatch) -> None:
    from client_gateway import images as G

    smap = SessionMap(recognizers=[Seen(fail=NerIncomplete("model_loading"))])
    monkeypatch.setattr(
        G, "_collect_passes", lambda img, names, st: [[G.Word("Hello", 1, 1, 30, 12)]]
    )
    st: dict[str, Any] = {"engines": [], "passes": 0, "words_first_pass": 1}
    with pytest.raises(G._Withhold) as exc:
        G._find_hits(G.Image.new("RGB", (40, 40), "white"), smap, ["winrt"], st)
    assert exc.value.reason == G.W_MATCH_FAILED
    assert G.W_MATCH_FAILED in masking.TRANSIENT_WITHHELD  # not cached: the next turn tries again


# ------------------------------------------------------------------------------ G-13
class TestRecognizerFilters:
    @pytest.mark.parametrize(
        "surface,label",
        [
            ("Isabel Peeters", "person"),
            ("Claude Janssens", "person"),
            ("Windows & Doors Verhaeghe BV", "organization"),
            ("Excel Logistics NV", "organization"),
        ],
    )
    def test_allow_listed_first_words_do_not_drop_clients(self, surface: str, label: str) -> None:
        assert keep(surface, label, ordinary_word, ner_mod.DEFAULT_ALLOW)

    @pytest.mark.parametrize(
        "surface", ["Isabel", "Azure Data Factory", "Microsoft Teams Rooms", "Power BI"]
    )
    def test_products_stay_dropped(self, surface: str) -> None:
        assert not keep(surface, "organization", ordinary_word, ner_mod.DEFAULT_ALLOW)

    def test_short_messages_and_table_cells_reach_the_model(self) -> None:
        assert prose_segments("Marnix Verbeke") == ["Marnix Verbeke"]
        assert prose_segments("| Marnix Verbeke | 1.200 |\n| Acme Holding NV | 3 |") == [
            "Marnix Verbeke",
            "Acme Holding NV",
        ]
        assert prose_segments("Customer=Lotte Vanhoutte; Amount=12") == ["Lotte Vanhoutte"]

    def test_code_is_still_skipped(self) -> None:
        assert prose_segments("x = foo bar baz;") == []
        assert prose_segments("EVALUATE SUMMARIZE(Sales, Customer[Name])") == []


# ------------------------------------------------------------------------------ G-18
class TestExemptSubtrees:
    @pytest.fixture()
    def state(self, tmp_path) -> GatewayState:
        st = GatewayState(home=tmp_path, default_mode="on", images_policy="withhold")
        st.smap.add_values("CUS", ["Zorvex Holding"], "t")
        return st

    @pytest.mark.parametrize(
        "extra",
        [
            {"metadata": {"note": "for Zorvex Holding"}},
            {"output_config": {"format": {"schema": {"enum": ["Zorvex Holding"]}}}},
            {"mcp_servers": [{"name": "Zorvex Holding"}]},
            {"tool_choice": {"type": "tool", "name": "Zorvex Holding"}},
        ],
    )
    def test_a_client_value_in_an_exempt_setting_fails_the_request_closed(
        self, state: GatewayState, extra: dict
    ) -> None:
        body = json.dumps({"model": "m", "messages": [user("hello there")], **extra}).encode()
        with pytest.raises(MaskingFailed) as exc:
            mask_request(body, JSON, state, RemaskMemo())
        assert exc.value.code == "residual_leak"

    def test_a_client_value_as_a_dict_key_fails_closed(self, state: GatewayState) -> None:
        block = {"type": "tool_result", "tool_use_id": "t", "content": {"Zorvex Holding": 5}}
        body = json.dumps({"messages": [{"role": "user", "content": [block]}]}).encode()
        with pytest.raises(MaskingFailed):
            mask_request(body, JSON, state, RemaskMemo())

    def test_clean_settings_and_schema_keywords_pass(self, state: GatewayState) -> None:
        state.smap.add_values("DIM", ["include", "string"], "t")  # vocabulary collision
        tools = [
            {
                "name": "t",
                "input_schema": {
                    "type": "object",
                    "properties": {"mode": {"type": "string", "enum": ["include", "exclude"]}},
                },
            }
        ]
        body = json.dumps(
            {
                "model": "m",
                "metadata": {"user_id": "abc123"},
                "tools": tools,
                "messages": [user("hello there")],
            }
        ).encode()
        out = json.loads(mask_request(body, JSON, state, RemaskMemo()))
        assert out["tools"][0]["input_schema"]["properties"]["mode"]["enum"] == [
            "include",
            "exclude",
        ]


# ------------------------------------------------------------------------------ degrade
class TestDictionaryOnly:
    def test_ner_that_cannot_finish_never_blocks_and_the_dictionary_still_masks(
        self, tmp_path
    ) -> None:
        state = make_state(tmp_path, Seen(fail=NerIncomplete("model_unavailable")))
        state.smap.add_values("CUS", ["Zorvex Holding"], "t")
        msg = [user("Zorvex Holding sent a mail, please read it.")]
        for degrade in (False, True):
            out = run(state, RemaskMemo(), msg, ner_degrade=degrade)
            assert "Zorvex" not in json.dumps(out)  # the dictionary still masks
        assert state.stats()["ner_partial"] == 2

    def test_dictionary_only_needs_the_token_and_resets_on_a_plain_on(self, tmp_path) -> None:
        from client_gateway.state import ModeDenied

        state = GatewayState(home=tmp_path, default_mode="off", images_policy="withhold")
        with pytest.raises(ModeDenied):
            state.set_mode("s1", "on", None, ner_degrade=True)
        state.set_mode("s1", "on", state.token, ner_degrade=True)
        assert state.session("s1").ner_degrade
        state.set_mode("s1", "on")
        assert not state.session("s1").ner_degrade


def test_a_recognizer_crash_still_fails_the_request_closed(tmp_path) -> None:
    state = make_state(tmp_path, Seen(fail=RuntimeError("worker crashed")))
    with pytest.raises(MaskingFailed) as exc:
        run(state, RemaskMemo(), [user("Mail from someone about an invoice.")], ner_degrade=True)
    assert (
        exc.value.code == "mask_error"
    )  # even in dictionary-only mode: only 'incomplete' degrades


def test_threads_do_not_corrupt_the_history_flag(tmp_path) -> None:
    state = make_state(tmp_path, Seen())
    memo = RemaskMemo()
    errors: list[BaseException] = []

    def go() -> None:
        try:
            run(state, memo, [user("a plain sentence with several words in it.")])
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and memo.history_scanned
