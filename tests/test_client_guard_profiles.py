"""Phase 4a: masking profiles (``generic`` vs ``finvision``) and the weak states they make visible.

The ``finvision`` behaviour itself is pinned by the recorded golden test
(``test_client_guard_profiles_golden.py``); here: profile selection and what ``generic`` produces
(the gateway side is in ``test_client_gateway_profiles.py``). Synthetic data only.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.client_guard import detector, profiles
from core.client_guard.engine import SessionMap, normalise
from core.client_guard.lexicon import prefix_for_names, tokenise
from core.client_guard.profiles import FINVISION, GENERIC, resolve
from core.client_guard.shapes import find_shapes
from core.client_guard.tokens import token_fn_from_key
from tests._masking_golden import _iban_be, _kbo, _nrn
from tests.test_client_guard_detector import FakeModel


def _none(key: str, default: Any) -> Any:
    return default


def _cfg(**values: Any):
    return lambda key, default: values.get(key.replace(".", "__"), default)


@pytest.fixture()
def clean_env(monkeypatch) -> None:
    monkeypatch.delenv(profiles.ENV_PROFILE, raising=False)


# ---------------------------------------------------------------------------------- selection
def test_default_profile_is_generic(clean_env) -> None:
    res = resolve(_none)
    assert res.profile is GENERIC and res.shape_packs == frozenset() and res.warnings == ()


def test_shipped_config_says_generic(clean_env) -> None:
    import json
    from pathlib import Path

    cfg = json.loads(
        (Path(profiles.__file__).parents[2] / "config" / "default_config.json").read_text("utf-8")
    )
    assert cfg["client_guard"]["profile"] == "generic"


def test_profile_from_env(monkeypatch) -> None:
    monkeypatch.setenv(profiles.ENV_PROFILE, "finvision")
    res = resolve(_none)
    assert res.profile is FINVISION and res.shape_packs == frozenset({"be"})


def test_profile_from_config(clean_env) -> None:
    res = resolve(_cfg(client_guard__profile="FinVision"))
    assert res.profile is FINVISION and res.warnings == ()


def test_env_wins_over_config(monkeypatch) -> None:
    monkeypatch.setenv(profiles.ENV_PROFILE, "generic")
    assert resolve(_cfg(client_guard__profile="finvision")).profile is GENERIC


@pytest.mark.parametrize("source", ["env", "config"])
def test_unknown_profile_is_generic_plus_a_warning(source, monkeypatch) -> None:
    if source == "env":
        monkeypatch.setenv(profiles.ENV_PROFILE, "acme")
        res = resolve(_none)
    else:
        monkeypatch.delenv(profiles.ENV_PROFILE, raising=False)
        res = resolve(_cfg(client_guard__profile="acme"))
    assert res.profile is GENERIC and res.warnings == ("profile_unknown",)


def test_active_profile_reads_the_real_config_default(clean_env) -> None:
    assert profiles.active_profile() is GENERIC


@pytest.mark.parametrize("value", [["be"], "be", "BE, be", ("be",)])
def test_shape_pack_from_config(value, clean_env) -> None:
    res = resolve(_cfg(client_guard__shape_packs=value))
    assert res.shape_packs == frozenset({"be"}) and res.warnings == ()


def test_unknown_shape_pack_warns_and_is_ignored(clean_env) -> None:
    res = resolve(_cfg(client_guard__shape_packs=["be", "xx", "yy"]))
    assert res.shape_packs == frozenset({"be"}) and res.warnings == ("shape_pack_unknown",)


def test_a_pack_adds_to_the_profiles_own(monkeypatch) -> None:
    monkeypatch.setenv(profiles.ENV_PROFILE, "finvision")
    assert resolve(_cfg(client_guard__shape_packs=[])).shape_packs == frozenset({"be"})


# ---------------------------------------------------------------------------------- generic: names
@pytest.mark.parametrize(
    "table, column, expected",
    [
        ("d_Customer", "Name", "CUS"),
        ("d_Vendor", "Supplier Code", "VEN"),
        ("d_Employee", "Voornaam", "PER"),
        ("d_Company", "Firma", "ORG"),
        ("d_Family", "Family Name", "PER"),  # no FAM in generic
        ("d_Entity", "Entity Name", "ORG"),  # no FAM in generic
        ("d_Counterparty", "Name", "VAL"),  # party of an unknown kind
        ("d_Bank", "IBAN", "ACC"),
        ("d_User", "UserPrincipalName", "USR"),
    ],
)
def test_generic_prefixes_by_name(table, column, expected) -> None:
    assert prefix_for_names(tokenise(table), tokenise(column), GENERIC) == expected


@pytest.mark.parametrize(
    "table, column",
    [
        ("d_Account", "Account Name"),
        ("d_Profit Center", "Profit Center Name"),
        ("d_Enterprise", "Name"),
        ("d_Product", "Product Name"),
    ],
)
def test_generic_dimension_prefix_is_val(table, column) -> None:
    assert detector.dimension_prefix(table, column, GENERIC) == "VAL"


def test_generic_never_produces_a_finvision_prefix() -> None:
    produced = {p for _s, p in GENERIC.prefix_stems} | {
        GENERIC.party_default_prefix,
        GENERIC.other_shape_prefix,
        GENERIC.dimension_default_prefix,
        *GENERIC.list_prefixes,
    }
    assert not produced & {"FAM", "GLA", "PCC", "ENT", "DIM", "CLI"}
    assert produced <= {"CUS", "VEN", "PER", "ORG", "ACC", "USR", "VAL"}


# ---------------------------------------------------------------------------------- generic: lists
def test_generic_allow_list_is_software_only() -> None:
    assert {"microsoft", "power bi", "excel", "fabric"} <= GENERIC.allow_terms
    assert not GENERIC.allow_terms & {"finvision", "finticx", "silverfin", "kbo", "itaa"}
    assert GENERIC.allow_terms < FINVISION.allow_terms
    assert {"finvision", "finticx", "silverfin"} <= FINVISION.allow_terms


def test_a_recognizer_takes_the_profiles_allow_list(monkeypatch) -> None:
    from core.client_guard.ner import NerRecognizer

    monkeypatch.setenv(profiles.ENV_PROFILE, "generic")
    gen = NerRecognizer(lambda w: False, allow=["Acme Tool"], loader=lambda n: None)
    assert "microsoft" in gen.allow and "acme tool" in gen.allow and "finvision" not in gen.allow
    monkeypatch.setenv(profiles.ENV_PROFILE, "finvision")
    fin = NerRecognizer(lambda w: False, loader=lambda n: None)
    assert "finvision" in fin.allow


def test_generic_has_no_naming_convention_safe_tables() -> None:
    assert GENERIC.safe_table_patterns == ()
    facts = detector._Facts([], frozenset(), frozenset(), frozenset())
    patterns = detector._compile_safe(None, GENERIC)
    assert patterns == []
    for name in ("s_Anything", "d_Period", "Report Lines", "c_Helper", "Number Format"):
        assert not detector.table_is_safe(name, facts, patterns, GENERIC)
        assert detector.table_is_safe(
            name, facts, detector._compile_safe(None, FINVISION), FINVISION
        )


def test_configured_safe_tables_still_apply_under_generic() -> None:
    facts = detector._Facts([], frozenset(), frozenset(), frozenset())
    patterns = detector._compile_safe([r"^lookup_"], GENERIC)
    assert detector.table_is_safe("lookup_codes", facts, patterns, GENERIC)
    assert not detector.table_is_safe("d_Product", facts, patterns, GENERIC)


# ---------------------------------------------------------------------------------- generic: shapes
def _hits(text: str, packs: Any, other: str = "VAL") -> list[tuple[int, int, str]]:
    return find_shapes(normalise(text), packs, other)


def test_core_shapes_work_without_any_pack() -> None:
    text = f"mail a@zorblax.example iban {_iban_be(539007547034)} call +32 470 12 34 56"
    prefixes = [p for _a, _b, p in _hits(text, frozenset())]
    assert prefixes == ["USR", "ACC", "VAL"]


def test_belgian_numbers_need_the_be_pack() -> None:
    text = f"nrn {_nrn(123456)} kbo be {_kbo(123456)}"
    assert _hits(text, frozenset()) == []
    assert [p for _a, _b, p in _hits(text, frozenset({"be"}))] == ["VAL", "VAL"]
    assert [p for _a, _b, p in _hits(text, frozenset({"be"}), "CLI")] == ["CLI", "CLI"]
    assert len(find_shapes(normalise(text))) == 2  # no packs argument: every shape (leak scans)


def test_generic_session_map_masks_core_shapes_only(monkeypatch) -> None:
    monkeypatch.setenv(profiles.ENV_PROFILE, "generic")
    smap = SessionMap(token_fn=token_fn_from_key(bytes(32)))
    text = f"id {_nrn(123456)} phone +32 470 12 34 56 mail a@zorblax.example"
    masked, count = smap.mask_text(text)
    assert count == 2 and _nrn(123456) in masked
    assert "VAL_" in masked and "USR_" in masked and "CLI_" not in masked
    assert smap.unmask_text(masked)[0] == text


def test_session_map_follows_explicit_packs_and_profile() -> None:
    smap = SessionMap(profile=GENERIC, shape_packs={"be"})
    masked, count = smap.mask_text(f"id {_nrn(123456)}")
    assert count == 1 and "VAL_" in masked
    fin = SessionMap(profile=FINVISION)
    assert "CLI_" in fin.mask_text(f"id {_nrn(123456)}")[0]
    assert SessionMap(profile=GENERIC).dimension_prefix == "VAL" == GENERIC.dimension_default_prefix
    assert fin.dimension_prefix == "DIM"


def test_a_val_phone_is_an_identity_not_report_vocabulary() -> None:
    smap = SessionMap(profile=GENERIC)
    smap.add_values("VAL", ["Wobbly Counterparty Co"], "t", strong=True)
    smap.add_values("VAL", ["Insurance"], "t")  # a dimension value: whole cell only
    masked, count = smap.mask_text("Wobbly Counterparty Co paid for insurance cost")
    assert count == 1 and masked.endswith("insurance cost")


# ---------------------------------------------------------------------------------- generic: detect
class _GenericModel(FakeModel):
    """FakeModel + the two optional structure queries of the generic profile."""

    def __init__(self) -> None:
        super().__init__()
        self.date_tables: set[str] = set()
        self.calc_groups: set[str] = set()
        self.structure_queries = 0
        self.fail_structure = False

    def __call__(self, query: str, max_rows: int) -> list[dict[str, Any]]:
        if self.fail_structure and (
            "INFO.CALCULATIONGROUPS" in query or '[DataCategory] = "Time"' in query
        ):
            raise RuntimeError("no such DMV")
        if "INFO.CALCULATIONGROUPS" in query:
            self.structure_queries += 1
            return [{"T": t} for t in sorted(self.calc_groups)]
        if '[DataCategory] = "Time"' in query:
            self.structure_queries += 1
            return [{"T": t} for t in sorted(self.date_tables)]
        return super().__call__(query, max_rows)


def _generic_model() -> _GenericModel:
    m = _GenericModel()
    m.col("d_Customer", "Customer Name", ["Zorblax Holdings NV", "Quillfeather Logistics BV"])
    m.col("d_Counterparty", "Name", ["Wobbly Counterparty Co", "Plinky Counterparty Co"])
    m.col("d_Account", "Account Name", ["Testbank current account", "Dummy savings account"])
    m.col("d_Contact", "National Number", [int(_nrn(123456)), int(_nrn(654321))], "Int64")
    m.col("Calendar", "Weekday Label", ["Mondayish", "Tuesdayish"])
    m.col("Time Intelligence", "Item", ["Current period", "Year to date"])
    m.col("s_Period", "Month Label", ["Spring window", "Autumn window"])
    m.col("Metrics", "Metric", ["Revenue metric", "Cost metric"])
    m.col("Metrics", "Dummy", ["x"])
    m.date_tables.add("Calendar")
    m.calc_groups.add("Time Intelligence")
    m.calc_tables = {"Metrics"}  # NAMEOF field parameter
    return m


def test_generic_detection(clean_env) -> None:
    model = _generic_model()
    result = detector.detect(model, policy="dimensions", profile=GENERIC)
    by = {(d.table, d.column): d for d in result.decisions}
    assert model.structure_queries == 2
    # identity columns keep neutral kind prefixes; unknown party kind -> VAL
    assert by[("d_Customer", "Customer Name")].prefix == "CUS"
    assert by[("d_Counterparty", "Name")].prefix == "VAL"
    # a dimension column is VAL, with no ledger-account class
    assert by[("d_Account", "Account Name")].prefix == "VAL"
    # date table (DataCategory Time), calculation group and field parameter are structure
    for key in (
        ("Calendar", "Weekday Label"),
        ("Time Intelligence", "Item"),
        ("Metrics", "Metric"),
    ):
        assert key not in by
    # no naming convention: "s_Period" is NOT a helper table any more
    assert by[("s_Period", "Month Label")].prefix == "VAL"
    # the national number is a Belgian-pack checksum: no pack, no integer column masked
    assert ("d_Contact", "National Number") not in by
    prefixes = {b.prefix for b in result.batches}
    assert prefixes <= {"CUS", "VAL"}
    # a VAL batch of a party column is an identity (strong) batch
    party = [b for b in result.batches if b.prefix == "VAL" and b.strong]
    assert party and "Wobbly Counterparty Co" in party[0].values


def test_generic_detection_with_the_be_pack(clean_env) -> None:
    model = _generic_model()
    result = detector.detect(
        model, policy="dimensions", profile=GENERIC, shape_packs=frozenset({"be"})
    )
    by = {(d.table, d.column): d for d in result.decisions}
    assert by[("d_Contact", "National Number")].mask
    assert by[("d_Contact", "National Number")].prefix == "VAL"


def test_finvision_asks_no_structure_queries(clean_env) -> None:
    model = _generic_model()
    detector.detect(model, policy="dimensions", profile=FINVISION)
    assert model.structure_queries == 0


def test_structure_queries_failing_do_not_break_detection(clean_env) -> None:
    model = _generic_model()
    model.fail_structure = True
    result = detector.detect(model, policy="dimensions", profile=GENERIC)
    by = {(d.table, d.column): d for d in result.decisions}
    assert result.ready and result.masked
    # nothing known about structure: the date table is then masked like any dimension (safe side)
    assert ("Calendar", "Weekday Label") in by


def test_detect_follows_the_active_profile_by_default(monkeypatch) -> None:
    monkeypatch.setenv(profiles.ENV_PROFILE, "generic")
    gen = detector.detect(_generic_model(), policy="dimensions")
    monkeypatch.setenv(profiles.ENV_PROFILE, "finvision")
    fin = detector.detect(_generic_model(), policy="dimensions")
    assert {b.prefix for b in gen.batches} <= {"CUS", "VAL"}
    assert {"GLA", "CLI"} <= {b.prefix for b in fin.batches}
