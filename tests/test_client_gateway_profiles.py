"""Phase 4a gateway side: the profile drives the detectors (client list, NER default, status) and
``GatewayState.warnings()`` names the weak states. Synthetic data only."""

from __future__ import annotations

from typing import Any

from client_gateway.detectors import Detectors, parse_client_list
from client_gateway.state import GatewayState
from core.client_guard import profiles
from core.client_guard.profiles import FINVISION, GENERIC


def test_client_list_prefixes_follow_the_profile() -> None:
    lines = ["PCC: A Center", "ENT: An Entity", "VEN: A Vendor"]
    assert parse_client_list(lines, GENERIC.list_prefixes) == {
        "ORG": ["PCC: A Center", "ENT: An Entity"],
        "VEN": ["A Vendor"],
    }
    assert parse_client_list(lines, FINVISION.list_prefixes) == {
        "PCC": ["A Center"],
        "ENT": ["An Entity"],
        "VEN": ["A Vendor"],
    }


# ---------------------------------------------------------------------------------- NER default
def _detectors(tmp_path, monkeypatch, profile: str, **env: str) -> Detectors:
    monkeypatch.setenv(profiles.ENV_PROFILE, profile)
    monkeypatch.delenv("FINVISION_GATEWAY_NER", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Detectors(tmp_path)


def test_ner_default_follows_the_profile(tmp_path, monkeypatch) -> None:
    import client_gateway.detectors as mod

    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda name: None)
    gen = _detectors(tmp_path, monkeypatch, "generic")
    assert gen.ner is None and not gen.ner_unavailable and gen.warnings() == []
    fin = _detectors(tmp_path, monkeypatch, "finvision")
    assert fin.ner_unavailable and fin.warnings() == ["ner_unavailable"]
    assert fin.status()["ner"] == {"enabled": False, "error": "unavailable"}


def test_explicit_ner_config_wins_over_the_profile(tmp_path, monkeypatch) -> None:
    import client_gateway.detectors as mod

    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda name: None)
    seen: dict[str, Any] = {}

    def fake_cfg(key: str, default: Any) -> Any:
        seen[key] = default
        return {"client_guard.ner.enabled": True}.get(key, default)

    monkeypatch.setattr(mod, "cfg", fake_cfg)
    gen = _detectors(tmp_path, monkeypatch, "generic")
    assert gen.ner_unavailable and seen["client_guard.ner.enabled"] is False  # profile default
    off = {"client_guard.ner.enabled": False}
    monkeypatch.setattr(mod, "cfg", lambda key, default: off.get(key, default))
    assert not _detectors(tmp_path, monkeypatch, "finvision").ner_unavailable


def test_env_switch_still_turns_ner_off(tmp_path, monkeypatch) -> None:
    det = _detectors(tmp_path, monkeypatch, "finvision", FINVISION_GATEWAY_NER="0")
    assert det.ner is None and det.warnings() == []


def test_a_recognizer_that_cannot_be_built_is_reported(tmp_path, monkeypatch) -> None:
    import client_gateway.detectors as mod
    import core.client_guard.ner as ner_mod

    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda name: object())

    class _Boom:
        def __init__(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("no model")

    monkeypatch.setattr(ner_mod, "NerRecognizer", _Boom)
    det = _detectors(tmp_path, monkeypatch, "finvision")
    assert det.ner is None and det.warnings() == ["ner_load_failed"]


def test_a_recognizer_whose_model_failed_is_reported(tmp_path, monkeypatch) -> None:
    det = _detectors(tmp_path, monkeypatch, "generic")

    class _Failed:
        state = "failed (OSError)"

    det.ner = _Failed()
    assert det.warnings() == ["ner_load_failed"]


# ---------------------------------------------------------------------------------- warnings()
def _state(tmp_path) -> GatewayState:
    return GatewayState(home=tmp_path, default_mode="on")


def test_state_reports_an_unknown_profile_and_pack(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(profiles.ENV_PROFILE, "acme")
    monkeypatch.setattr(
        profiles,
        "_config_get",
        lambda key, default: ["zz"] if key == profiles.PACKS_KEY else default,
    )
    warnings = _state(tmp_path).warnings()
    assert "profile_unknown" in warnings and "shape_pack_unknown" in warnings
    assert "ner_unavailable" not in warnings  # NER is off in the suite (FINVISION_GATEWAY_NER=0)


def test_state_reports_an_ephemeral_pseudonym_key(tmp_path, monkeypatch) -> None:
    assert "token_key_ephemeral" not in _state(tmp_path).warnings()

    import core.client_guard.tokens as tokens

    def _broken(home: Any) -> Any:
        raise OSError("read-only")

    monkeypatch.setattr(tokens, "stable_token_fn", _broken)
    state = _state(tmp_path)
    assert state.token_key_ephemeral and "token_key_ephemeral" in state.warnings()
    # still masks: the fallback key is per process
    assert state.smap.mask_text("a@zorblax.example")[1] == 1


def test_state_status_carries_the_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(profiles.ENV_PROFILE, "generic")
    det = _state(tmp_path).detectors.status()
    assert det["profile"] == "generic" and det["shape_packs"] == []
    monkeypatch.setenv(profiles.ENV_PROFILE, "finvision")
    det = _state(tmp_path).detectors.status()
    assert det["profile"] == "finvision" and det["shape_packs"] == ["be"]
