"""Detection layers on top of the exact dictionary: ambiguous names, typos, recognizers (NER),
the remembered store, the client and allow lists, and the evaluation corpus as a regression bar."""

from __future__ import annotations

import os

import pytest

from client_gateway.detectors import Detectors, parse_client_list
from client_gateway.store import ValueStore
from core.client_guard.engine import SessionMap, ordinary_word
from core.client_guard.fuzzy import allowed_edits
from core.client_guard.ner import NerRecognizer, keep, prose_segments


def _map(**kw: object) -> SessionMap:
    return SessionMap(token_fn=lambda prefix, key: 60_000, **kw)  # type: ignore[arg-type]


# ------------------------------------------------------------------ ambiguous names
class TestAmbiguousNames:
    def test_an_ordinary_word_name_is_masked_in_its_own_case_only(self) -> None:
        m = _map()
        m.add_values("CUS", ["Horizon"], "t")
        assert m.mask_text("Klant Horizon vraagt een kredietnota")[1] == 1
        assert m.mask_text("HORIZON paid late")[1] == 1
        assert m.mask_text("over a five-year horizon the forecast is flat")[1] == 0
        assert m.mask_text("horizon")[1] == 1  # a whole cell is always the name

    def test_a_three_letter_acronym_is_masked_in_running_text(self) -> None:
        m = _map()
        m.add_values("VEN", ["VDK"], "t")
        assert m.mask_text("We paid VDK twice in Q3.")[1] == 1
        assert m.mask_text("the vdk folder")[1] == 0

    def test_a_short_title_case_value_stays_whole_cell_only(self) -> None:
        # "Jan" as a first name would otherwise hit every "Jan 2025"
        m = _map()
        m.add_values("PER", ["Jan"], "t")
        assert m.mask_text("Jan 2025 closing")[1] == 0
        assert m.mask_text("Jan")[1] == 1

    def test_the_leak_check_applies_the_same_case_rule(self) -> None:
        m = _map()
        m.add_values("CUS", ["Horizon"], "t")
        masked, _ = m.mask_text("Horizon over the horizon")
        assert m.count_leaks(masked) == 0  # the lower-case word is not a leak


# ------------------------------------------------------------------ typos
class TestTypos:
    def test_tolerance_scales_with_length(self) -> None:
        assert [allowed_edits(n) for n in (3, 4, 5, 8, 9, 20)] == [0, 0, 1, 1, 2, 2]

    @pytest.mark.parametrize(
        "variant",
        [
            "Janssen Holding",
            "Janssens Holdng",
            "Jansens Holding",
            "Janssnes Holding",
            "Van den Broeck & Leclrecq",
        ],
    )
    def test_misspelt_identity_values_are_masked(self, variant: str) -> None:
        m = _map()
        m.add_values("CUS", ["Janssens Holding", "Van den Broeck & Leclercq"], "t")
        out, n = m.mask_text(f"Kun je {variant} nakijken?")
        assert n == 1 and variant not in out

    def test_prose_and_generic_values_are_not_fuzzed(self) -> None:
        m = _map()
        m.add_values("CUS", ["Janssens Holding"], "t")
        m.add_values("GLA", ["Bank charges"], "t")
        for text in ("the holding company pays", "Bank chargés rose", "janssen holding"):
            assert m.mask_text(text)[1] == 0, text

    def test_a_typo_gets_its_own_pseudonym_and_comes_back_as_typed(self) -> None:
        m = _map()
        m.add_values("CUS", ["Janssens Holding"], "t")
        out, _ = m.mask_text("Janssen Holding")
        assert m.unmask_text(out)[0] == "Janssen Holding"

    def test_fuzzy_can_be_switched_off(self) -> None:
        m = _map(fuzzy=False)
        m.add_values("CUS", ["Janssens Holding"], "t")
        assert m.mask_text("Janssen Holding paid")[1] == 0


# ------------------------------------------------------------------ recognizers
class _FakeRecognizer:
    def __init__(self, found: list[tuple[str, str]]) -> None:
        self.found = found
        self.calls = 0

    def find(self, text: str) -> list[tuple[str, str]]:
        self.calls += 1
        return [f for f in self.found if f[0] in text]


class TestRecognizers:
    def test_a_recognized_name_is_masked_now_and_everywhere_afterwards(self) -> None:
        rec = _FakeRecognizer([("Lotte Vanhoutte", "PER")])
        m = _map(recognizers=[rec])
        out, n = m.mask_text("Mail van Lotte Vanhoutte over de factuur")
        assert n == 1 and "Vanhoutte" not in out
        rec.found = []
        assert m.mask_text("LOTTE VANHOUTTE belde")[1] == 1  # now a dictionary value
        assert m.count_leaks("Lotte Vanhoutte") == 1

    def test_recognizers_do_not_run_on_tool_definitions(self) -> None:
        rec = _FakeRecognizer([("Lotte Vanhoutte", "PER")])
        m = _map(recognizers=[rec])
        assert m.mask_text("Lotte Vanhoutte", whole=False)[1] == 0
        assert rec.calls == 0

    def test_a_failing_recognizer_fails_closed(self) -> None:
        class Broken:
            def find(self, text: str) -> list[tuple[str, str]]:
                raise RuntimeError("model crashed")

        from core.client_guard.engine import MaskingError

        with pytest.raises(MaskingError):
            _map(recognizers=[Broken()]).mask_text("Mail van Lotte")

    def test_recognize_adds_without_masking(self) -> None:
        m = _map(recognizers=[_FakeRecognizer([("Wouter Thys", "PER")])])
        assert m.recognize("Groeten, Wouter Thys") == 1
        assert m.mask_text("Wouter Thys")[1] == 1


# ------------------------------------------------------------------ NER filters (no model)
class TestNerFilters:
    @pytest.mark.parametrize(
        "surface,label",
        [
            ("Anthropic", "organization"),
            ("Microsoft Fabric", "organization"),
            ("DAX Studio", "organization"),
            ("d_Vendor", "organization"),
            ("AP | Total (RC)", "organization"),
            ("De bakker op de hoek", "person"),
            ("Other Income", "organization"),
            ("Noord", "organization"),
        ],
    )
    def test_products_object_names_and_prose_are_dropped(self, surface: str, label: str) -> None:
        from core.client_guard.ner import DEFAULT_ALLOW

        assert not keep(surface, label, ordinary_word, DEFAULT_ALLOW)

    @pytest.mark.parametrize(
        "surface,label",
        [
            ("Lotte Vanhoutte", "person"),
            ("Dewulf Metaal BVBA", "organization"),
            ("Van den Broeck & Leclercq", "organization"),
            ("Tom Smit", "person"),
        ],
    )
    def test_names_are_kept(self, surface: str, label: str) -> None:
        from core.client_guard.ner import DEFAULT_ALLOW

        assert keep(surface, label, ordinary_word, DEFAULT_ALLOW)

    def test_only_prose_reaches_the_model(self) -> None:
        text = (
            '{"rows": [{"d_Vendor[VendorName]": "x"}]}\n'
            "EVALUATE SUMMARIZECOLUMNS('d_Vendor'[VendorName])\n"
            "Van: Lotte Vanhoutte <" + "lotte" + "@" + "example.invalid>\n"
            "Beste, de factuur van Dewulf Metaal staat nog open.\n"
        )
        segs = prose_segments(text)
        assert len(segs) == 1 and "Dewulf Metaal" in segs[0] and "Lotte" in segs[0]
        assert "EVALUATE" not in segs[0] and "rows" not in segs[0]

    def test_recognizer_with_a_fake_model_caches_and_maps_labels(self) -> None:
        class Model:
            calls = 0

            def predict_entities(self, text: str, labels: list[str], threshold: float):
                Model.calls += 1
                return [
                    {"text": "Lotte Vanhoutte", "label": "person", "score": 0.9},
                    {"text": "Anthropic", "label": "organization", "score": 0.9},
                ]

        ner = NerRecognizer(ordinary_word, loader=lambda name: Model())
        ner.start()
        text = "Mail van Lotte Vanhoutte, collega bij Anthropic, over de factuur."
        assert ner.find(text) == [("Lotte Vanhoutte", "PER")]
        assert ner.find(text) == [("Lotte Vanhoutte", "PER")] and Model.calls == 1
        assert ner.state == "ready"

    def test_uncached_prose_segments_are_predicted_in_one_batch(self) -> None:
        class Model:
            batches: list[list[str]] = []

            def batch_predict_entities(self, texts: list[str], labels: list[str], threshold: float):
                Model.batches.append(texts)
                return [
                    (
                        [{"text": "Lotte Vanhoutte", "label": "person"}]
                        if "Lotte Vanhoutte" in text
                        else []
                    )
                    for text in texts
                ]

            def predict_entities(self, text: str, labels: list[str], threshold: float):
                raise AssertionError("uncached prose should be batched")

        ner = NerRecognizer(ordinary_word, loader=lambda name: Model())
        text = (
            "Mail van Lotte Vanhoutte over de factuur.\n"
            'EVALUATE ROW("x", 1)\n'
            "De klant vraagt om een nieuwe levering."
        )
        assert ner.find(text) == [("Lotte Vanhoutte", "PER")]
        assert len(Model.batches) == 1 and len(Model.batches[0]) == 1
        assert "Lotte Vanhoutte" in Model.batches[0][0] and "nieuwe levering" in Model.batches[0][0]
        assert ner.find(text) == [("Lotte Vanhoutte", "PER")]
        assert len(Model.batches) == 1

    def test_separate_request_text_blocks_share_a_model_batch(self) -> None:
        class Model:
            batches: list[list[str]] = []

            def batch_predict_entities(self, texts: list[str], labels: list[str], threshold: float):
                Model.batches.append(texts)
                return [
                    (
                        [{"text": "Lotte Vanhoutte", "label": "person"}]
                        if "Lotte Vanhoutte" in text
                        else []
                    )
                    for text in texts
                ]

        ner = NerRecognizer(ordinary_word, loader=lambda name: Model())
        texts = [
            "Mail from Lotte Vanhoutte about the invoice.",
            "The client needs an updated invoice today.",
        ]
        assert ner.find_many(texts) == [("Lotte Vanhoutte", "PER")]
        assert len(Model.batches) == 1 and len(Model.batches[0]) == 1
        assert "Lotte Vanhoutte" in Model.batches[0][0] and "updated invoice" in Model.batches[0][0]
        assert ner.find_many(texts) == [("Lotte Vanhoutte", "PER")]
        assert len(Model.batches) == 1

    def test_one_long_prose_block_obeys_model_input_budget(self) -> None:
        from core.client_guard.ner import MAX_NEW_PER_CALL

        class Model:
            inputs = 0

            def batch_predict_entities(self, texts: list[str], labels: list[str], threshold: float):
                Model.inputs += len(texts)
                return [[] for _ in texts]

        ner = NerRecognizer(ordinary_word, loader=lambda name: Model())
        prose = "".join(
            f"The quarterly review item number {i} needs a separate written summary. "
            for i in range(500)
        )
        assert ner.find(prose) == []
        assert Model.inputs <= MAX_NEW_PER_CALL
        assert ner.stats["over_budget"] > 0

    def test_a_model_that_cannot_load_degrades_unless_strict(self) -> None:
        def broken(name: str):
            raise OSError("no model")

        ner = NerRecognizer(ordinary_word, loader=broken, wait_s=5)
        assert ner.find("Mail van Lotte Vanhoutte over de factuur.") == []
        assert ner.stats["skipped"] == 1 and ner.state.startswith("failed")
        strict = NerRecognizer(ordinary_word, loader=broken, wait_s=5, strict=True)
        with pytest.raises(RuntimeError):
            strict.find("Mail van Lotte Vanhoutte over de factuur.")


# ------------------------------------------------------------------ remembered store
@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows only")
class TestValueStore:
    def test_round_trip_is_encrypted(self, tmp_path) -> None:
        store = ValueStore(tmp_path)
        assert (
            store.remember([("CUS", False, {"Janssens Holding"}), ("DIM", True, {"Kerk 1"})]) == 2
        )
        assert store.remember([("CUS", False, {"Janssens Holding"})]) == 0  # nothing new
        raw = (tmp_path / "dictionary.dpapi").read_bytes()
        assert b"Janssens" not in raw
        again = ValueStore(tmp_path).load()
        assert again == {("CUS", False): {"Janssens Holding"}, ("DIM", True): {"Kerk 1"}}

    def test_forget_deletes_it(self, tmp_path) -> None:
        store = ValueStore(tmp_path)
        store.remember([("CUS", False, {"X Holding"})])
        assert store.forget() and not (tmp_path / "dictionary.dpapi").exists()

    def test_a_corrupt_store_is_reported_not_fatal(self, tmp_path) -> None:
        (tmp_path / "dictionary.dpapi").write_bytes(b"not dpapi")
        store = ValueStore(tmp_path)
        assert store.load() == {} and store.error


# ------------------------------------------------------------------ detectors
class TestDetectors:
    def test_client_list_prefixes(self) -> None:
        parsed = parse_client_list(
            ["VEN: Dewulf Metaal BVBA", "Janssens Holding", "PER: Jan Thys", "XYZ: odd line"]
        )
        assert parsed == {
            "VEN": ["Dewulf Metaal BVBA"],
            "ORG": ["Janssens Holding", "XYZ: odd line"],
            "PER": ["Jan Thys"],
        }

    def test_static_sources_load_into_the_map(self, tmp_path) -> None:
        (tmp_path / "client_names.txt").write_text(
            "# klanten\nVEN: Dewulf Metaal BVBA\n", encoding="utf-8"
        )
        det = Detectors(tmp_path)
        assert det.ner is None  # FINVISION_GATEWAY_NER=0 in tests
        if det.store is not None and det.store.available:
            det.remember([("CUS", False, {"Janssens Holding"})])
        smap = det.session_map(None)
        det.load_static(smap)
        assert smap.mask_text("Dewulf Metaal BVBA belde")[1] == 1
        if det.store is not None and det.store.available:
            assert smap.mask_text("Janssens Holding betaalde")[1] == 1
        status = det.status()
        assert status["client_list"] == 1 and status["fuzzy"] is True


# ------------------------------------------------------------------ regression bar
def test_evaluation_corpus_without_ner_meets_the_bar() -> None:
    """The dictionary layers alone (no model): recall and over-masking must not regress."""
    from tools.masking_eval.corpus import build
    from tools.masking_eval.run import default_map, evaluate

    corpus = build()
    result = evaluate(default_map(corpus, lambda: _map()), corpus)
    assert result["dict_exact_prose"]["recall"] == 1.0
    assert result["dict_exact_json"]["recall"] == 1.0
    assert result["typo"]["recall"] >= 0.95
    assert result["short_identity"]["recall"] == 1.0
    assert result["negative_dev"]["keep_rate"] == 1.0
    assert result["_total"]["keep_rate"] >= 0.98


# ------------------------------------------------------------------ partial names
class TestPartialNames:
    def test_a_distinctive_part_of_a_company_name_is_masked(self) -> None:
        m = _map()
        m.add_values(
            "CUS",
            ["Vanhoutte Bouwwerken NV", "Dewulf Bouwwerken BVBA", "Lauwers Bouwwerken NV"],
            "t",
        )
        out, n = m.mask_text("Heb je nog nieuws van Vanhoutte over de factuur?")
        assert n == 1 and "Vanhoutte" not in out
        # a stem shared by three names is not distinctive; legal forms never are
        assert m.mask_text("De Bouwwerken afdeling en de NV vergaderen.")[1] == 0

    def test_a_part_is_masked_only_capitalised_in_running_text(self) -> None:
        m = _map()
        m.add_values("CUS", ["Kompaszorg Advies BV"], "t")
        assert m.mask_text("Kompaszorg belde")[1] == 1
        assert m.mask_text("kompaszorg is a made-up word here")[1] == 0

    def test_a_first_name_alone_is_not_a_part(self) -> None:
        m = _map()
        m.add_values("PER", ["Bertrand Lemoyne"], "t")
        assert m.mask_text("Lemoyne asked")[1] == 1
        assert m.mask_text("Bertrand asked")[1] == 0

    def test_parts_come_back_as_typed(self) -> None:
        m = _map()
        m.add_values("CUS", ["Vanhoutte Bouwwerken NV"], "t")
        out, _ = m.mask_text("Vanhoutte")
        assert m.unmask_text(out)[0] == "Vanhoutte"

    def test_identity_hits_ignores_generic_values(self) -> None:
        m = _map()
        m.add_values("CUS", ["Vanhoutte Bouwwerken NV"], "t")
        m.add_values("GLA", ["Bank charges"], "t")
        assert m.identity_hits("Vanhoutte Bouwwerken NV and Bank charges") == {"CUS": 1}

    def test_an_everyday_word_part_is_masked_only_inside_a_sentence(self) -> None:
        m = _map()
        m.add_values("CUS", ["Lambert Bouwwerken NV", "Horizon Advies BV"], "t")
        assert m.mask_text("Any update from Lambert on the invoices?")[1] == 1
        assert m.mask_text("Heb je nog nieuws van Horizon over september?")[1] == 1
        # the first word of a sentence or a bullet, a heading, lower case: ordinary text
        assert m.mask_text("Horizon of the plan is three years.")[1] == 0
        assert m.mask_text("- Horizon: three years")[1] == 0
        assert m.mask_text("Planning Horizon And Scope Review")[1] == 0
        assert m.mask_text("the horizon of the plan")[1] == 0

    def test_a_curated_finance_word_is_never_a_part(self) -> None:
        m = _map()
        m.add_values("CUS", ["Vanmeulen Global Partners"], "t")
        assert m.mask_text("We compare the Global figures with Partners data.")[1] == 0


# ------------------------------------------------------------------ NER in a masked chat
class _SlowModel:
    """GLiNER stand-in: one name per chunk; ``delay`` seconds per batch."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.batches = 0

    def batch_predict_entities(self, texts, labels, threshold):
        import time as _time

        self.batches += 1
        _time.sleep(self.delay)
        return [[{"text": "Lotte Vanhoutte", "label": "person"}] for _ in texts]


class TestNerComplete:
    def _ner(self, model, **kw):
        ner = NerRecognizer(ordinary_word, loader=lambda name: model, wait_s=5, **kw)
        ner.start()
        assert ner._ready.wait(5)
        return ner

    def test_complete_mode_checks_every_new_segment(self) -> None:
        texts = [
            "Graag een update over de factuur van september voor klant nummer "
            f"{i} en de betaling."
            for i in range(40)
        ]
        ner = self._ner(_SlowModel())
        ner.find_many(["\n\n".join(texts)], complete=True)
        assert ner.stats["over_budget"] == 0 and ner.stats["incomplete"] == 0

    def test_complete_mode_fails_closed_when_time_runs_out(self) -> None:
        from core.client_guard.ner import NerIncomplete

        texts = [
            f"Paragraaf {i} " + "met gewone woorden over de rapportering " * 40 for i in range(12)
        ]
        ner = self._ner(_SlowModel(delay=0.2), max_s=0.1)
        with pytest.raises(NerIncomplete):
            ner.find_many(["\n\n".join(texts)], complete=True)
        assert ner.stats["incomplete"] == 1

    def test_complete_mode_fails_closed_while_the_model_loads(self) -> None:
        import threading

        from core.client_guard.ner import NerIncomplete

        gate = threading.Event()

        def slow_loader(name):
            gate.wait(5)
            return _SlowModel()

        ner = NerRecognizer(ordinary_word, loader=slow_loader, wait_s=0.05)
        try:
            with pytest.raises(NerIncomplete):
                ner.find_many(["Graag een update van de klant over de factuur."], complete=True)
        finally:
            gate.set()

    def test_the_safety_net_mode_still_skips_instead_of_failing(self) -> None:
        texts = [
            f"Paragraaf {i} " + "met gewone woorden over de rapportering " * 40 for i in range(12)
        ]
        ner = self._ner(_SlowModel(delay=0.2), budget_s=0.1)
        ner.find_many(["\n\n".join(texts)])  # no exception: the unmasked chat does not wait
        assert ner.stats["timeouts"] == 1
