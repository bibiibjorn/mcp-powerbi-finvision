"""Permanent regression fixture for the 2026-10-03 deep review, slice D.

Two tiers (D-29), both run through the live analysis path
(``get_shared_analyzer().analyze`` - the call behind ``05 analyze`` and ``lint_model``):

1. every canon-GOOD snippet must be clean: no finding above ``info`` except the rule ids its
   corpus entry tolerates (``ok``). Canon-good = the canon's own GOOD examples, MS preferred
   forms and the remedies of the anti-patterns;
2. every canon-BAD snippet must draw the rules its entry lists in ``fire``.

The scoreboard test pins the numbers so a rule change cannot silently regress them. Snippets
that exercise a canon anti-pattern static analysis cannot see are listed in ``STATIC_LIMIT``
with the reason.
"""

import pytest

from core.dax.analyzer.models import AnalysisContext
from core.dax.analyzer.unified_analyzer import get_shared_analyzer
from tests._dax_corpus_data import C, SCHEMAS

_BY_ID = {e["id"]: e for e in C}
_GOOD = [e for e in C if e["kind"] == "good"]
_BAD = [e for e in C if e["kind"] == "bad"]

_ANALYZER = get_shared_analyzer()


def _analyze(entry):
    return _ANALYZER.analyze(entry["dax"], AnalysisContext(schema=SCHEMAS[entry["schema"]]))


# Canon anti-patterns with no live rule, and why static analysis cannot (or should not) see them.
STATIC_LIMIT = {
    "B43": "AP-48: the UDF definition alone is not wrong - the failure is at the call site (B44), "
    "which needs the UDF signature from the model",
}


@pytest.mark.parametrize("entry", _GOOD, ids=[e["id"] for e in _GOOD])
def test_canon_good_snippet_is_clean(entry):
    res = _analyze(entry)
    noisy = [
        (i.rule_id, i.severity)
        for i in res.issues
        if i.severity != "info" and i.rule_id not in entry["ok"]
    ]
    assert not noisy, f"{entry['id']} ({entry['ref']}): {entry['dax']!r} -> {noisy}"


@pytest.mark.parametrize("entry", _BAD, ids=[e["id"] for e in _BAD])
def test_canon_bad_snippet_fires_expected_rules(entry):
    res = _analyze(entry)
    fired = {i.rule_id for i in res.issues}
    missing = [r for r in entry["fire"] if r not in fired]
    assert not missing, f"{entry['id']} ({entry['ref']}): {entry['dax']!r} fired {sorted(fired)}"


def test_scoreboard_good_snippets_with_medium_or_higher_findings_is_zero():
    offenders = {
        e["id"]: [
            (i.rule_id, i.severity)
            for i in _analyze(e).issues
            if i.severity in ("medium", "high", "critical") and i.rule_id not in e["ok"]
        ]
        for e in _GOOD
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert offenders == {}, offenders


def test_scoreboard_bad_snippets_without_a_detecting_rule():
    """Each bad snippet has at least one rule in ``fire`` (the detection) except the documented
    static limits."""
    undetected = sorted(e["id"] for e in _BAD if not e["fire"])
    assert undetected == sorted(STATIC_LIMIT), undetected
    # and nothing listed as a limit is silently detected already (keep the list honest)
    for bid in STATIC_LIMIT:
        assert not _BY_ID[bid]["fire"]


def test_corpus_has_the_reviewed_size():
    assert len(C) == 108
    assert len(_GOOD) == 63 and len(_BAD) == 45
