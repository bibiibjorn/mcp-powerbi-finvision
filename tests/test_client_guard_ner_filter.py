"""The recognizer's span filter after the model evaluation of 2026-10-08 (synthetic NL/FR/EN set):
a lower-case lead is cut off a span, a confident span of ordinary words is kept, and a name on a
line of its own (an e-mail signature) reaches the model."""

from __future__ import annotations

import pytest

from core.client_guard.engine import ordinary_word
from core.client_guard.ner import DEFAULT_ALLOW, NerRecognizer, keep, name_part, prose_segments


@pytest.mark.parametrize(
    "span,expected",
    [
        ("familie De Smet", "De Smet"),
        ("famille Delvaux", "Delvaux"),
        ("Le cabinet Moreau Avocats", "Moreau Avocats"),
        ("mevrouw Lotte Vanhoutte", "Lotte Vanhoutte"),
        ("Jan van der Berg", "Jan van der Berg"),  # particles stay
        ("van der Berg", "van der Berg"),
        ("Marie d'Ursel", "Marie d'Ursel"),
        ("De accountant", "De accountant"),  # nothing capitalised after the lead: keep() drops it
    ],
)
def test_name_part_cuts_a_lower_case_lead(span: str, expected: str) -> None:
    assert name_part(span) == expected


@pytest.mark.parametrize(
    "surface,label,score",
    [
        ("De Wit", "person", 0.96),
        ("Zwart Vastgoed", "organization", 0.91),
        ("Silver Lake Farms", "company", 0.92),
    ],
)
def test_a_confident_span_of_ordinary_words_is_kept(surface: str, label: str, score: float) -> None:
    assert keep(surface, label, ordinary_word, DEFAULT_ALLOW, score)
    assert not keep(surface, label, ordinary_word, DEFAULT_ALLOW)  # no score: the old rule


@pytest.mark.parametrize(
    "surface,label,score", [("Client", "person", 0.54), ("Ik", "person", 0.76)]
)
def test_an_unsure_span_of_ordinary_words_is_still_dropped(
    surface: str, label: str, score: float
) -> None:
    assert not keep(surface, label, ordinary_word, DEFAULT_ALLOW, score)


def test_products_stay_dropped_however_confident() -> None:
    assert not keep("Power BI", "organization", ordinary_word, DEFAULT_ALLOW, 0.99)
    assert not keep("klant", "person", ordinary_word, DEFAULT_ALLOW, 0.99)  # not a proper noun


def test_a_signature_line_reaches_the_model() -> None:
    mail = "Dear all,\n\nIsabella Moreno confirmed the transfer before year end.\n\nKind regards,\nGrace Liu"
    assert prose_segments(mail) == [
        "Isabella Moreno confirmed the transfer before year end.",
        "Grace Liu",
    ]


def test_the_recognizer_applies_both_with_the_model_score() -> None:
    class Model:
        def predict_entities(self, text: str, labels: list[str], threshold: float):
            return [
                {"text": "familie De Smet", "label": "person", "score": 0.7},
                {"text": "De Wit", "label": "person", "score": 0.97},
                {"text": "Client", "label": "person", "score": 0.54},
            ]

    rec = NerRecognizer(ordinary_word, loader=lambda name: Model(), wait_s=5, strict=True)
    rec.start()
    assert sorted(rec.find("Graag de rekeningen van de familie De Smet, zegt De Wit.")) == [
        ("De Smet", "PER"),
        ("De Wit", "PER"),
    ]


@pytest.mark.parametrize(
    "surface,label", [("Centrum", "organization"), ("Noord", "person"), ("Baker", "person")]
)
def test_one_bare_ordinary_word_is_dropped_however_confident(surface: str, label: str) -> None:
    # "while Noord stayed flat": a person at 0.99 (tools/ner_bench.py, urchade: 14 false hits)
    assert not keep(surface, label, ordinary_word, DEFAULT_ALLOW, 0.99)
