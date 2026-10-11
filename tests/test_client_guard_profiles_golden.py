"""Phase 4a golden: under the ``finvision`` profile the masking stack behaves exactly as it did
before profiles existed (same prefixes, same pseudonyms for the same values).

``tests/fixtures/masking/finvision_golden.json`` was recorded by ``tests/_masking_golden_record.py``
at the base commit (fbfbf41, before any profile code). It must never be re-recorded to make this
test pass: a difference is a behaviour change of the owner's setup.
"""

from __future__ import annotations

import json

import pytest

from tests import _masking_golden as golden


@pytest.fixture(autouse=True)
def _finvision(monkeypatch) -> None:
    monkeypatch.setenv("FINVISION_MASKING_PROFILE", "finvision")


@pytest.fixture(scope="module")
def recorded() -> dict:
    return json.loads(golden.FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture()
def now() -> dict:
    # round-trip through JSON so tuples / lists compare like the recorded file
    return json.loads(json.dumps(golden.snapshot(), ensure_ascii=False, sort_keys=True))


@pytest.mark.parametrize("section", ["prefixes", "shapes", "config", "engine", "detect"])
def test_finvision_matches_the_recorded_base_behaviour(section, recorded, now) -> None:
    assert now[section] == recorded[section]


def test_the_recorded_pseudonyms_are_the_real_ones(recorded) -> None:
    """Guard the fixture itself: it holds actual Finvision tokens, not an empty shell."""
    masked = recorded["engine"]["masked"]
    for prefix in ("CUS_", "VEN_", "PER_", "ENT_", "PCC_", "FAM_", "CLI_", "DIM_", "GLA_"):
        assert prefix in masked
    assert recorded["engine"]["restored_ok"] is True
