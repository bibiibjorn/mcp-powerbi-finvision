"""Client Guard detector: fake model (INFO.VIEW + paged DISTINCT values), synthetic data only."""

from __future__ import annotations

import re
from typing import Any

import pytest

from core.client_guard.detector import (
    DetectionResult,
    detect,
    is_local_phone,
    page_query,
    shape_prefix,
)
from core.client_guard.lexicon import (
    has_ident_word,
    has_party_word,
    name_hints,
    prefix_for_names,
    tokenise,
)


# ------------------------------------------------------------------------------------ helpers
def nrn(base: int) -> str:
    """A checksum-valid Belgian national number (11 digits) from a 9-digit base."""
    return f"{base:09d}{97 - base % 97:02d}"


_PAGE_RE = re.compile(
    r"DISTINCT\(SELECTCOLUMNS\('((?:[^']|'')+)', \"v\", '(?:[^']|'')+'\[((?:[^\]]|\]\])+)\]\)\)"
    r"\nORDER BY \[v\] ASC(?:\nSTART AT (.+))?$"
)


def _parse_literal(text: str) -> Any:
    if text.startswith('"'):
        return text[1:-1].replace('""', '"')
    return float(text) if "." in text else int(text)


class FakeModel:
    """Serves INFO.VIEW metadata and ordered, START AT-paged DISTINCT values."""

    def __init__(self) -> None:
        self.columns: list[tuple[str, str, str, str, str]] = []  # table, col, type, cat, desc
        self.values: dict[tuple[str, str], list[Any]] = {}
        self.measures: list[str] = []
        self.measure_tables: dict[str, str] = {}  # measure -> table (when a test needs it)
        self.calc_tables: set[str] = set()
        self.perms: list[str] = []
        self.rels: list[tuple[str, str, str, str]] = []
        self.page_queries: dict[tuple[str, str], int] = {}
        self.fail_on: set[tuple[str, str, int]] = set()  # (table, col, page number to fail)
        self.fail_columns_query = False
        self.fail_message = "page failed"
        self.rich_columns_unsupported = False

    def col(
        self,
        table: str,
        column: str,
        values: list[Any],
        dtype: str = "Text",
        category: str = "",
        description: str = "",
    ) -> None:
        self.columns.append((table, column, dtype, category, description))
        self.values[(table, column)] = list(values)

    # the detector's runner
    def __call__(self, query: str, max_rows: int) -> list[dict[str, Any]]:
        if "INFO.VIEW.COLUMNS" in query:
            if self.fail_columns_query:
                raise RuntimeError("boom")
            if self.rich_columns_unsupported and '"DC"' in query:
                raise RuntimeError("no DataCategory")
            rows = [(t, c, dt, dc, d) for t, c, dt, dc, d in self.columns]
            return [dict(zip(["T", "C", "DT", "DC", "D"], r)) for r in rows]
        if "INFO.VIEW.TABLES" in query:
            tables = {c[0] for c in self.columns}
            return [{"T": t, "X": "NAMEOF" if t in self.calc_tables else None} for t in tables]
        if "INFO.VIEW.MEASURES" in query:
            if '"T"' in query:
                return [{"N": m, "T": self.measure_tables.get(m, "")} for m in self.measures]
            return [{"N": m} for m in self.measures]
        if "INFO.VIEW.RELATIONSHIPS" in query:
            return [dict(zip(["F", "FC", "T", "TC"], r)) for r in self.rels]
        if "INFO.TABLEPERMISSIONS" in query:
            return [{"F": p} for p in self.perms]
        m = _PAGE_RE.search(query)
        assert m, query
        table, column = m.group(1).replace("''", "'"), m.group(2).replace("]]", "]")
        key = (table, column)
        page = self.page_queries.get(key, 0) + 1
        self.page_queries[key] = page
        if (table, column, page) in self.fail_on:
            raise RuntimeError(self.fail_message)
        distinct = sorted(
            {v for v in self.values[key]},
            key=lambda v: (v is not None, str(v).casefold() if isinstance(v, str) else v),
        )
        if m.group(3) is not None:
            cursor = _parse_literal(m.group(3))
            distinct = [
                v
                for v in distinct
                if v is not None
                and (
                    v >= cursor
                    if not isinstance(v, str)
                    else (v.casefold() >= str(cursor).casefold())
                )
            ]
        return [{"[v]": v} for v in distinct[:max_rows]]


def decisions(result: DetectionResult) -> dict[tuple[str, str], Any]:
    return {(d.table, d.column): d for d in result.decisions}


def masked_values(result: DetectionResult) -> set[str]:
    return {v for b in result.batches for v in b.values}


# ------------------------------------------------------------------------------------ tokenising
@pytest.mark.parametrize(
    "name, tokens",
    [
        ("d_Customer", ["d", "customer"]),
        ("UserPrincipalName", ["user", "principal", "name"]),
        ("EmailAddress", ["email", "address"]),
        ("CompanyName", ["company", "name"]),
        ("IBANNumber", ["iban", "number"]),
        ("AccountHolderName", ["account", "holder", "name"]),
        ("Family ID", ["family", "id"]),
        ("d Assetinstrument Entity Label", ["d", "assetinstrument", "entity", "label"]),
        ("e-mail", ["e", "mail"]),
        ("Entité", ["entite"]),
        ("RLS Column1", ["rls", "column", "1"]),
        ("XMLParser", ["xml", "parser"]),
    ],
)
def test_tokenise(name: str, tokens: list[str]) -> None:
    assert tokenise(name) == tokens


@pytest.mark.parametrize(
    "name",
    [
        "UserPrincipalName",
        "EmailAddress",
        "CompanyName",
        "IBANNumber",
        "AccountHolderName",
        "Relatie",
        "Deelnemer",
        "Eigenaar",
        "Klant",
        "KlantNaam",
        "Leverancier",
        "Fournisseur",
        "Counterparty",
        "d_Customer",
        "Family Name",
        "Bankrekening",
        "Bank Account",
        "OrgUnit",
        "Org Unit",
        "Voornaam",
        "Last Name",
    ],
)
def test_party_words_in_nl_fr_en_de_names(name: str) -> None:
    assert has_party_word(tokenise(name)), name


@pytest.mark.parametrize("name", ["Naam", "Nom", "Name", "Code", "Label", "Group", "Nummer"])
def test_identifier_words(name: str) -> None:
    tok = tokenise(name)
    assert has_ident_word(tok) and not has_party_word(tok)


@pytest.mark.parametrize(
    "name",
    ["Member", "d_Member", "Members Count", "GL Account", "Account Code", "Profit Centre", "Name"],
)
def test_member_and_ordinary_words_are_not_party_words(name: str) -> None:
    assert not has_party_word(tokenise(name)), name


def test_name_hints_rules() -> None:
    assert name_hints("d_Customer", "Customer Name").party_via == "column-name"
    assert name_hints("d_Customer", "Name").party_via == "table-name"
    assert name_hints("d_Product", "Name").party is False
    assert name_hints("d_Place", "Where", data_category="Address").party_via == "data-category"
    assert name_hints("d_X", "Label", description="Name of the customer").party_via == "description"
    assert name_hints("d_X", "Colour", description="Name of the customer").party is False
    assert name_hints("d_Member", "Member Name").party is False


def test_prefix_for_names() -> None:
    def pfx(t: str, c: str) -> str:
        return prefix_for_names(tokenise(t), tokenise(c))

    assert pfx("d Family", "Family ID") == "FAM"
    assert pfx("d Assetinstrument", "Entity Name") == "FAM"
    assert pfx("d Participant", "Participant Name") == "PER"
    assert pfx("d_Customer", "Customer Code") == "CUS"
    assert pfx("d_Vendor", "Name") == "VEN"
    assert pfx("d_OrgUnit", "Company Name") == "ORG"
    assert pfx("RLS", "UserPrincipalName") == "USR"
    assert pfx("d_Vendor", "Bank Account") == "ACC"
    assert pfx("d_Customer", "Contact Name") == "PER"
    assert pfx("d_Thing", "Whatever") == "CLI"


# ------------------------------------------------------------------------------------ shapes
def test_shape_prefix_scores_before_the_numeric_skip() -> None:
    assert shape_prefix("jan.janssens@example.com") == "USR"
    assert shape_prefix("BE68 5390 0754 7034") == "ACC"
    assert shape_prefix(nrn(850101123)) == "CLI"
    formatted = nrn(850101123)
    assert (
        shape_prefix(
            f"{formatted[:2]}.{formatted[2:4]}.{formatted[4:6]}-{formatted[6:9]}.{formatted[9:]}"
        )
        == "CLI"
    )
    assert shape_prefix("+32 475 12 34 56") == "CLI"
    assert shape_prefix("85010112399") is None  # wrong checksum
    assert shape_prefix("123456") is None
    assert shape_prefix("Alpha Holding NV") is None
    assert is_local_phone("0475 12 34 56") and not is_local_phone("12345")


def test_page_query_escapes_names_and_cursor() -> None:
    q = page_query("O'Neil", "A]B", 'say "hi"')
    assert "'O''Neil'" in q and "[A]]B]" in q
    assert q.endswith('START AT "say ""hi"""')
    assert "START AT" not in page_query("T", "C")


# ------------------------------------------------------------------------------------ S02 shape
def s02_model() -> FakeModel:
    m = FakeModel()
    m.measures = ["Total Sales", "Total Cost"]
    m.col("d_Customer", "Customer Code", ["CUS-10023", "CUS-10024", "CUS-10025"])
    m.col("d_Customer", "Customer Name", ["Alpha Trading NV", "Beta Retail BV", "Gamma Foods SA"])
    m.col("d_Customer", "Contact Email", ["ann@alpha.example", "bob@beta.example", "x@y.zz"])
    m.col("d_Customer", "Phone", ["+32 475 12 34 56", "+32 475 98 76 54", "+31 6 12345678"])
    m.col("d_Customer", "Country", ["Belgium", "Netherlands", "France"])
    m.col("d_Customer", "Segment", ["Wholesale", "Retail", "Online shop"])
    m.col("d_Vendor", "Vendor Name", ["Delta Supplies NV", "Epsilon Logistics BV"])
    m.col("d_Vendor", "Vendor Code", ["VEN-0001", "VEN-0002"])
    m.col("d_Vendor", "Contact", ["Eva Peeters", "Tom Willems"])
    m.col("d_Vendor", "IBAN", ["BE68 5390 0754 7034", "NL91ABNA0417164300"])
    m.col("d_OrgUnit", "Company Name", ["Zeta Holding NV", "Eta Services BV"])
    m.col("d_OrgUnit", "Region", ["North", "South"])
    m.col("d_GLAccount", "Account Code", ["400100", "400200", "610000"])
    m.col("d_GLAccount", "Account Name", ["Cash at bank", "Trade receivables", "Rent expense"])
    m.col("d_ProfitCenter", "Profit Centre Code", ["PC-FIN", "PC-OPS", "PC-HR1"])
    m.col("d_ProfitCenter", "Profit Centre Name", ["Finance", "Operations", "Human resources"])
    m.col("RLS Users", "UserPrincipalName", ["u1@client.example", "u2@client.example"])
    m.col("Aging", "Customer Name", ["Alpha Trading NV", "Theta Imports NV"])
    m.col("d_Member", "Member Name", ["Gold member", "Silver member"])
    m.perms = ["'d_GLAccount'[Account Code] IN VALUES('RLS Users'[UserPrincipalName])"]
    return m


def test_s02_shaped_decisions() -> None:
    result = detect(s02_model())
    d = decisions(result)
    masked = {k for k, v in d.items() if v.mask}
    assert masked == {
        ("d_Customer", "Customer Code"),
        ("d_Customer", "Customer Name"),
        ("d_Customer", "Contact Email"),
        ("d_Customer", "Phone"),
        ("d_Vendor", "Vendor Name"),
        ("d_Vendor", "Vendor Code"),
        ("d_Vendor", "Contact"),
        ("d_Vendor", "IBAN"),
        ("d_OrgUnit", "Company Name"),
        ("RLS Users", "UserPrincipalName"),
        ("Aging", "Customer Name"),
    }
    assert result.ready is True
    values = masked_values(result)
    assert "Alpha Trading NV" in values and "ann@alpha.example" in values
    assert "u1@client.example" in values and "BE68 5390 0754 7034" in values
    # kept: GL accounts, profit centres, dimension attributes, ordinary words
    for kept in ("400100", "Cash at bank", "PC-FIN", "Finance", "Belgium", "Wholesale", "North"):
        assert kept not in values
    assert ("d_Member", "Member Name") not in d
    assert ("d_Customer", "Country") not in d and ("d_ProfitCenter", "Profit Centre Code") not in d


def test_s02_prefixes_per_column() -> None:
    d = decisions(detect(s02_model()))
    assert d[("d_Customer", "Customer Name")].prefix == "CUS"
    assert d[("d_Vendor", "Vendor Name")].prefix == "VEN"
    assert d[("d_Customer", "Contact Email")].prefix == "USR"
    assert d[("d_Vendor", "IBAN")].prefix == "ACC"
    assert d[("d_OrgUnit", "Company Name")].prefix == "ORG"
    assert d[("RLS Users", "UserPrincipalName")].prefix == "USR"
    assert d[("d_Vendor", "Contact")].prefix == "PER"


def test_rls_only_corroborates_and_never_decides() -> None:
    result = detect(s02_model())
    d = decisions(result)
    gl = d[("d_GLAccount", "Account Name")]  # RLS-filtered table + identifying column
    assert gl.mask is False
    assert "rls-table" in gl.reasons and "(rls corroborates)" in gl.reasons
    assert not {"Cash at bank", "Trade receivables"} & masked_values(result)
    # RLS on a table does not turn a plain column into a candidate either
    assert ("d_GLAccount", "Account Code") not in {k for k, v in d.items() if v.mask}


def test_short_and_numeric_values_are_never_added_without_a_shape() -> None:
    # outside a party column: nothing short, nothing numeric
    m = FakeModel()
    m.col("d_Report", "Customer Name", ["ACME GROUP"])  # a party column makes the model a client
    m.col("d_Misc", "Remarks", ["AB", "123456", "1,5", "Zed"])
    result = detect(m)
    assert masked_values(result) == {"ACME GROUP"}


def test_a_party_column_keeps_acronyms_and_numeric_ids_but_not_noise() -> None:
    """G-16: a client called KBC or a numeric customer number is an identity, not noise."""
    m = FakeModel()
    m.col("d_Customer", "Customer Code", ["AB", "12", "123456", "1,5", "2024-01-31", "KBC", "ACME"])
    result = detect(m)
    assert masked_values(result) == {"123456", "KBC", "ACME"}


def test_a_party_table_dimension_keeps_acronyms_and_numeric_ids() -> None:
    m = FakeModel()
    m.col("d_Customer", "Region", ["VDK", "Antwerp", "98765", "12"])
    result = detect(m, policy="dimensions")
    assert {"VDK", "98765", "Antwerp"} <= masked_values(result)
    assert "12" not in masked_values(result)


# ------------------------------------------------------------------------------------ S01 shape
def s01_model() -> FakeModel:
    m = FakeModel()
    m.measures = ["Total Return", "Net Worth"]
    m.col("d Family", "Family ID", ["FAM-ALPHA", "FAM-BETA"])
    m.col("d Family", "Family Name", ["Janssens Family", "Peeters Family"])
    m.col("d Family", "Family Label", ["Janssens", "Peeters"])
    m.col("d Assetinstrument", "Entity ID", ["ENT-001A", "ENT-002B"])
    m.col("d Assetinstrument", "Entity Name", ["Janssens Holding", "Peeters Invest"])
    m.col("d Assetinstrument", "Entity Label", ["JH", "PI NV"])
    m.col("d Assetinstrument", "Entity Group", ["Group Janssens", "Group Peeters"])
    m.col("d Assetinstrument", "IBAN", ["BE68 5390 0754 7034", "NL91ABNA0417164300"])
    m.col("d Assetinstrument", "Asset Class", ["Equities", "Bonds"])
    m.col("d Participant", "Participant", ["Jan Janssens", "An Peeters"])
    m.col("d Participant", "Participant Name", ["Jan Janssens", "An Peeters"])
    m.col("RLS", "User", ["jan@family.example", "an@family.example"])
    m.col("RLS", "Column1", ["Total Return", "d Family", "Family Name"])
    m.col("d Participant Picker", "Participant Name", ["Total Return", "Net Worth"])
    m.perms = ["'RLS'[User] = USERPRINCIPALNAME()"]
    return m


def test_s01_shaped_decisions() -> None:
    result = detect(s01_model())
    d = decisions(result)
    masked = {k for k, v in d.items() if v.mask}
    assert masked == {
        ("d Family", "Family ID"),
        ("d Family", "Family Name"),
        ("d Family", "Family Label"),
        ("d Assetinstrument", "Entity ID"),
        ("d Assetinstrument", "Entity Name"),
        ("d Assetinstrument", "Entity Label"),
        ("d Assetinstrument", "Entity Group"),
        ("d Assetinstrument", "IBAN"),
        ("d Participant", "Participant"),
        ("d Participant", "Participant Name"),
        ("RLS", "User"),
    }
    assert ("RLS", "Column1") not in masked  # metadata values: kept
    picker = d[("d Participant Picker", "Participant Name")]
    assert picker.mask is False and "metadata-values" in picker.reasons
    assert "Total Return" not in masked_values(result)
    assert d[("d Family", "Family ID")].prefix == "FAM"
    assert d[("d Assetinstrument", "Entity Name")].prefix == "FAM"
    assert d[("d Participant", "Participant Name")].prefix == "PER"
    assert d[("RLS", "User")].prefix == "USR"


# ------------------------------------------------------------------------------------ signals
def test_shape_decides_regardless_of_the_column_name() -> None:
    m = FakeModel()
    m.col("t", "Remarks", ["a@b.example", "c@d.example", "e@f.example", "see file"])
    m.col("t", "Reference", ["BE68 5390 0754 7034", "NL91ABNA0417164300"])
    m.col("t", "Rijksregisternummer", [nrn(850101123), nrn(900202456), nrn(750303789)])
    m.col("t", "Notes", ["plain note one", "plain note two"])
    result = detect(m)
    d = decisions(result)
    assert d[("t", "Remarks")].mask and d[("t", "Remarks")].prefix == "USR"
    assert d[("t", "Reference")].mask and d[("t", "Reference")].prefix == "ACC"
    assert d[("t", "Rijksregisternummer")].mask
    assert nrn(850101123) in masked_values(result)  # numeric-only, kept by its checksum
    assert ("t", "Notes") not in d


def test_local_phone_needs_a_phone_name() -> None:
    m = FakeModel()
    m.col("t", "Telefoon", ["0475 12 34 56", "0476 65 43 21", "09 123 45 67"])
    m.col("t", "Lookup", ["0475 12 34 56", "0476 65 43 21"])
    d = decisions(detect(m))
    assert d[("t", "Telefoon")].mask
    assert ("t", "Lookup") not in d


def test_data_category_and_description_are_evidence() -> None:
    m = FakeModel()
    m.col("d_Site", "Where", ["Main street 1 Antwerp", "Side road 2 Ghent"], category="Address")
    m.col("d_Site", "Label", ["Alpha site", "Beta site"], description="Name of the customer")
    m.col("d_Site", "Tag", ["Alpha tag", "Beta tag"], description="Name of the customer")
    d = decisions(detect(m))
    assert d[("d_Site", "Where")].mask and "party-data-category" in d[("d_Site", "Where")].reasons
    assert d[("d_Site", "Label")].mask and "party-description" in d[("d_Site", "Label")].reasons
    assert ("d_Site", "Tag") not in d  # "Tag" is no identifying word


def test_an_empty_or_all_short_party_column_masks_nothing() -> None:
    m = FakeModel()
    m.col("d_Customer", "Customer Code", ["AB", "12", "1234"])
    m.col("d_Customer", "Customer Name", [])
    result = detect(m)
    d = decisions(result)
    assert not d[("d_Customer", "Customer Code")].mask
    assert "no-values" in d[("d_Customer", "Customer Code")].reasons
    assert result.masked == [] and result.batches == []


def test_member_is_not_a_party_word() -> None:
    m = FakeModel()
    m.col("d_Member", "Member", ["Gold member", "Silver member"])
    m.col("d_Member", "Member Name", ["Gold member", "Silver member"])
    assert detect(m).decisions == []


def test_nl_fr_named_columns_are_masked() -> None:
    m = FakeModel()
    m.col("Relaties", "Naam", ["Bakkerij Janssens", "Slagerij Peeters"])
    m.col("Deelnemers", "Deelnemer", ["Jan Janssens", "An Peeters"])
    m.col("Fournisseurs", "Nom", ["Boulangerie Dupont", "Charcuterie Martin"])
    m.col("d_Produit", "Nom", ["Pain complet", "Jambon blanc"])
    d = decisions(detect(m))
    assert d[("Relaties", "Naam")].mask
    assert d[("Deelnemers", "Deelnemer")].mask
    assert d[("Fournisseurs", "Nom")].mask
    assert ("d_Produit", "Nom") not in d


def test_integer_columns_need_a_name_hint_and_a_checksum() -> None:
    m = FakeModel()
    ok = [int(nrn(b)) for b in (850101123, 900202456, 50101123, 750303789)]  # one with a leading 0
    m.col("d_Person", "National Number", ok, dtype="Int64")
    m.col("d_Person", "Rijksregisternummer Copy", ok, dtype="Int64")
    m.col("d_Person", "Seq", ok, dtype="Int64")  # right shape, no name hint
    m.col("d_GL", "Account Number", [400100, 400200, 610000, 610100], dtype="Int64")
    m.col("d_GL", "Customer Id", [1, 2, 3, 4], dtype="Int64")
    result = detect(m)
    d = decisions(result)
    assert d[("d_Person", "National Number")].mask
    assert ("d_Person", "Seq") not in d
    assert ("d_GL", "Account Number") not in d and ("d_GL", "Customer Id") not in d
    assert nrn(50101123) in masked_values(result)  # leading zero restored
    assert str(int(nrn(50101123))) not in masked_values(
        result
    )  # the 10-digit int form is not added


# ------------------------------------------------------------------------------------ paging
def paged_model(n: int) -> FakeModel:
    m = FakeModel()
    m.col("d_Customer", "Customer Name", [f"Client number {i:04d}" for i in range(n)])
    return m


def test_paging_across_three_pages() -> None:
    m = paged_model(6)
    result = detect(m, page_size=3)
    # START AT is inclusive: pages are [0,1,2] [2,3,4] [4,5]; the cursor row is dropped each time
    assert m.page_queries[("d_Customer", "Customer Name")] == 3
    assert result.ready and not result.decisions[0].not_ready
    values = masked_values(result)
    assert len(values) == 6 and "Client number 0000" in values and "Client number 0005" in values


def test_paging_when_the_last_page_is_exactly_full() -> None:
    m = paged_model(7)
    result = detect(m, page_size=3)
    assert len(masked_values(result)) == 7 and result.ready
    assert m.page_queries[("d_Customer", "Customer Name")] == 4  # the last page is the cursor row


def test_truncation_makes_the_column_and_the_result_not_ready() -> None:
    m = paged_model(8)
    result = detect(m, page_size=3, max_pages=2)
    d = result.decisions[0]
    assert d.truncated and d.not_ready and not d.mask
    assert result.ready is False


def test_a_page_error_makes_the_column_not_ready() -> None:
    m = paged_model(8)
    m.fail_on = {("d_Customer", "Customer Name", 2)}
    result = detect(m, page_size=3)
    assert result.decisions[0].error and result.ready is False


def test_unreadable_plain_column_is_only_a_probe_error() -> None:
    m = FakeModel()
    m.col("t", "Notes", ["plain note"])
    m.fail_on = {("t", "Notes", 1)}
    result = detect(m)
    assert result.ready is True and result.probe_errors == 1 and result.decisions == []


def test_plain_columns_are_only_probed_on_the_first_page() -> None:
    m = FakeModel()
    m.col("t", "Notes", [f"note number {i:04d}" for i in range(10)])
    result = detect(m, page_size=3)
    assert m.page_queries[("t", "Notes")] == 1
    assert result.sampled_columns == 1 and result.ready


def test_a_plain_column_with_shapes_is_read_to_the_end() -> None:
    m = FakeModel()
    m.col("t", "Remarks", [f"user{i:03d}@client.example" for i in range(6)])
    result = detect(m, page_size=3)
    assert m.page_queries[("t", "Remarks")] == 3
    assert len(masked_values(result)) == 6


def test_metadata_failure_is_not_ready() -> None:
    m = s02_model()
    m.fail_columns_query = True
    result = detect(m)
    assert result.ready is False and result.decisions == []


def test_falls_back_when_data_category_is_unavailable() -> None:
    m = s02_model()
    m.rich_columns_unsupported = True
    m.fail_columns_query = False
    # the plain query has no "DC" column, so the fake serves the same rows
    assert detect(m).ready is True


def test_decisions_and_batches_never_expose_values_in_repr() -> None:
    result = detect(s02_model())
    text = repr(result.batches) + repr(result.decisions) + repr(result.counts())
    assert "Alpha Trading" not in text and "alpha.example" not in text
    assert result.counts()["masked_columns"] == 11


# ------------------------------------------------------------------------------------ policy A
def dims_model() -> FakeModel:
    m = FakeModel()
    m.measures = ["Total Sales"]
    m.measure_tables = {"Total Sales": "Measures"}
    m.col("Measures", "Dummy", ["x"])
    m.col("d_ProfitCenter", "Profit Centre Name", ["Finance", "Operations", "Human resources"])
    m.col("d_ProfitCenter", "PC Group", ["Corporate services", "Field operations"])
    m.col("d_GLAccount", "Account Code", ["400100", "400200", "610000"])
    m.col("d_GLAccount", "Account Name", ["Cash at bank", "Trade receivables", "Rent expense"])
    m.col("d_Subsidiary", "Short Label", ["Acme Holdings", "Globex Industries"])
    m.col("d_Product", "Product Name", ["Widget Alpha", "Gadget Beta"])
    m.col("d_Product", "Code", ["A1", "B2", "C3"])
    m.col("d_Product", "Launch", ["2024-01-31", "2024-02-29"])
    m.col("d_Product", "Weight", ["12.5", "13"])
    m.col("d_Customer", "Customer Name", ["Alpha Trading NV", "Beta Retail BV"])
    m.col("d_Period", "Month Name", ["January", "February"])
    m.col("Calendar", "Quarter Label", ["Quarter one", "Quarter two"])
    m.col("d_Currency", "Currency Name", ["Euro", "US Dollar"])
    m.col("FX Rates", "Pair", ["EUR to USD", "EUR to GBP"])
    m.col("d_Scenario", "Scenario Name", ["Actual figures", "Budget figures"])
    m.col("d_Version", "Version Name", ["Version one", "Version two"])
    m.col("ReportLines", "Line Name", ["Revenue total", "Operating result"])
    m.col("TagMap", "Tag", ["Tag number one", "Tag number two"])
    m.col("DimensionMapping", "Mapping", ["Mapped value one", "Mapped value two"])
    m.col("KPI Setup", "KPI Name", ["Gross margin", "Net margin"])
    m.col("s_Selector", "Choice", ["Choice alpha", "Choice beta"])
    m.col("fp_Metric", "Label", ["Metric alpha", "Metric beta"])
    m.col("Number Format", "Pattern", ["Pattern alpha", "Pattern beta"])
    m.col("Axis Picker", "Pick", ["Total Sales", "Total Sales"])
    m.col("Candidate", "Remark", ["Remark alpha", "Remark beta"])
    m.col("Field Parameter", "Parameter", ["Param alpha", "Param beta"])
    m.calc_tables = {"Field Parameter"}
    return m


def test_dimensions_policy_masks_dimension_values_by_kind() -> None:
    result = detect(dims_model(), policy="dimensions")
    d = decisions(result)
    assert result.ready is True
    assert d[("d_ProfitCenter", "Profit Centre Name")].prefix == "PCC"
    assert d[("d_ProfitCenter", "PC Group")].prefix == "PCC"
    assert d[("d_GLAccount", "Account Name")].prefix == "GLA"
    assert d[("d_Subsidiary", "Short Label")].prefix == "ENT"
    assert d[("d_Product", "Product Name")].prefix == "DIM"
    assert d[("Candidate", "Remark")].prefix == "DIM"  # "Candidate" is not a date table
    values = masked_values(result)
    for v in (
        "Finance",
        "Human resources",
        "Field operations",
        "Cash at bank",
        "Rent expense",
        "Acme Holdings",
        "Widget Alpha",
        "Remark alpha",
    ):
        assert v in values
    by = {b.prefix: set(b.values) for b in result.batches}
    assert by["PCC"] >= {"Finance", "Corporate services"}
    assert by["GLA"] == {"Cash at bank", "Trade receivables", "Rent expense"}
    assert by["ENT"] == {"Acme Holdings", "Globex Industries"}


def test_dimensions_policy_keeps_identity_prefixes() -> None:
    d = decisions(detect(dims_model(), policy="dimensions"))
    assert d[("d_Customer", "Customer Name")].prefix == "CUS"
    assert "dimension" not in d[("d_Customer", "Customer Name")].reasons


def test_dimensions_policy_leaves_safe_tables_and_codes_alone() -> None:
    result = detect(dims_model(), policy="dimensions")
    d = decisions(result)
    values = masked_values(result)
    for table, column in (
        ("d_Period", "Month Name"),
        ("Calendar", "Quarter Label"),
        ("d_Currency", "Currency Name"),
        ("FX Rates", "Pair"),
        ("d_Scenario", "Scenario Name"),
        ("d_Version", "Version Name"),
        ("ReportLines", "Line Name"),
        ("TagMap", "Tag"),
        ("DimensionMapping", "Mapping"),
        ("KPI Setup", "KPI Name"),
        ("s_Selector", "Choice"),
        ("fp_Metric", "Label"),
        ("Number Format", "Pattern"),
        ("Field Parameter", "Parameter"),
        ("Measures", "Dummy"),
        ("d_GLAccount", "Account Code"),
        ("d_Product", "Code"),
        ("d_Product", "Launch"),
        ("d_Product", "Weight"),
    ):
        assert (table, column) not in d, (table, column)
    for v in (
        "January",
        "Quarter one",
        "Euro",
        "EUR to USD",
        "Actual figures",
        "Version one",
        "Revenue total",
        "Tag number one",
        "Gross margin",
        "Choice alpha",
        "Metric alpha",
        "Pattern alpha",
        "Param alpha",
        "A1",
        "2024-01-31",
        "400100",
        "12.5",
    ):
        assert v not in values


def test_object_name_columns_are_not_masked_under_dimensions() -> None:
    d = decisions(detect(dims_model(), policy="dimensions"))
    assert ("Axis Picker", "Pick") not in d


def test_identity_policy_is_unchanged() -> None:
    explicit = detect(dims_model(), policy="identity")
    default = detect(dims_model())
    assert explicit.batches == default.batches
    assert {k for k, v in decisions(default).items() if v.mask} == {("d_Customer", "Customer Name")}
    assert default.skipped_large == 0 and default.counts()["skipped_large"] == 0


def test_unknown_policy_is_rejected() -> None:
    with pytest.raises(ValueError):
        detect(dims_model(), policy="everything")


def test_safe_tables_are_configurable_and_bad_patterns_ignored() -> None:
    result = detect(dims_model(), policy="dimensions", safe_tables=["^d_Product$", "(unclosed"])
    values = masked_values(result)
    assert "Widget Alpha" not in values
    assert "Cash at bank" in values  # a bad pattern must not switch anything off


def test_large_columns_are_skipped_and_counted_not_blocking(monkeypatch) -> None:
    from core.client_guard import detector

    monkeypatch.setattr(detector, "LARGE_DISTINCT", 5)
    m = FakeModel()
    m.col("d_Big", "Big Label", [f"Big label number {i:03d}" for i in range(8)])
    m.col("d_Small", "Small Label", ["Small label one", "Small label two"])
    result = detect(m, policy="dimensions", page_size=3)
    big = decisions(result)[("d_Big", "Big Label")]
    assert result.ready is True and result.skipped_large == 1
    assert result.counts()["skipped_large"] == 1
    assert big.reasons == ("skipped-large",) and not big.mask
    assert masked_values(result) == {"Small label one", "Small label two"}


def test_large_column_that_fits_one_page_is_still_skipped(monkeypatch) -> None:
    from core.client_guard import detector

    monkeypatch.setattr(detector, "LARGE_DISTINCT", 5)
    m = FakeModel()
    m.col("d_Big", "Big Label", [f"Big label number {i:03d}" for i in range(8)])
    result = detect(m, policy="dimensions")
    assert result.skipped_large == 1 and result.ready is True and not result.batches


def test_dimension_read_failure_is_not_ready() -> None:
    m = FakeModel()
    m.col("d_Product", "Product Name", [f"Product name {i:02d}" for i in range(8)])
    m.fail_on = {("d_Product", "Product Name", 2)}
    result = detect(m, page_size=3, policy="dimensions")
    assert result.ready is False
    assert decisions(result)[("d_Product", "Product Name")].error


def test_dimension_probe_failure_is_not_ready_under_dimensions_only() -> None:
    m = FakeModel()
    m.col("d_Product", "Product Name", ["Widget Alpha", "Gadget Beta"])
    m.fail_on = {("d_Product", "Product Name", 1)}
    assert detect(m).ready is True  # identity policy: just a probe error
    m.page_queries.clear()
    assert detect(m, policy="dimensions").ready is False


def test_dimension_truncation_is_not_ready() -> None:
    m = FakeModel()
    m.col("d_Product", "Product Name", [f"Product name {i:02d}" for i in range(8)])
    result = detect(m, page_size=3, max_pages=2, policy="dimensions")
    assert result.ready is False


def test_dimension_values_are_not_in_repr() -> None:
    result = detect(dims_model(), policy="dimensions")
    text = repr(result.batches) + repr(result.decisions) + repr(result.counts())
    assert "Cash at bank" not in text and "Widget Alpha" not in text


@pytest.mark.parametrize(
    "table, column, prefix",
    [
        ("d_Cost Center", "Name", "PCC"),
        ("Dim", "CostCentre", "PCC"),
        ("d_PC", "Description", "PCC"),
        ("d_Ledger", "Description", "GLA"),
        ("d_GL", "Description", "GLA"),
        ("d_Legal", "Description", "ENT"),
        ("d_Region", "Description", "DIM"),
        ("d_Accounting Entity", "Short", "ENT"),
    ],
)
def test_dimension_prefix(table: str, column: str, prefix: str) -> None:
    from core.client_guard.detector import dimension_prefix

    assert dimension_prefix(table, column) == prefix


# ---------------------------------------------------------------- safe table names


def _safe(table: str, patterns=None) -> bool:
    from types import SimpleNamespace

    from core.client_guard import detector

    facts = SimpleNamespace(param_tables=set(), measure_tables=set(), column_counts={})
    return detector.table_is_safe(table, facts, detector._compile_safe(patterns))


@pytest.mark.parametrize(
    "table",
    [
        "d_Customer_Information",  # "inFORMATion" is not "format"
        "d Contact Information",
        "Client Information",
        "Stock Exchange",
        "d_Vendor Rates",
        "d_Rate Owner",
        "SFDR Funds",  # "sf" is a prefix only as "sf_" / "sf "
        "Time Entries",
        "Version Owners",
        "Scale Partners",
        "Decimal Holdings",
    ],
)
def test_names_that_only_contain_a_safe_word_are_not_safe(table) -> None:
    assert _safe(table) is False


@pytest.mark.parametrize(
    "table",
    [
        "d_Period",
        "Calendar",
        "Fiscal Calendar",
        "d_Period View",
        "d_Currency",
        "FX Rates",
        "Exchange Rates",
        "d_Scenario",
        "d_Version",
        "d_Date",
        "s_Selector",
        "sf_Pick",
        "fp_Metric",
        "Number Format",
        "KPI Setup",
        "ReportLines",
        "TagMap",
        "DimensionMapping",
    ],
)
def test_real_helper_and_period_tables_stay_safe(table) -> None:
    assert _safe(table) is True


def test_a_party_word_in_the_name_is_never_made_safe_by_a_default_pattern() -> None:
    assert _safe("d_Customer_Format") is False
    assert _safe("d_Customer_Format", ["^d_Customer_Format$"]) is True  # an explicit choice wins


def test_information_tables_are_masked_under_the_dimensions_policy() -> None:
    m = FakeModel()
    m.col("d_Customer_Information", "Relationship Manager", ["Rita Vandenbroeck", "Ward Maes"])
    m.col("Stock Exchange", "Venue", ["Brussels bourse", "Amsterdam beurs"])
    m.col("d_Period", "Month Name", ["January", "February"])
    result = detect(m, policy="dimensions")
    values = masked_values(result)
    assert {"Rita Vandenbroeck", "Ward Maes", "Brussels bourse"} <= values
    assert "January" not in values


def test_dimension_values_without_a_letter_are_not_masked() -> None:
    from core.client_guard.detector import _dimension_value

    for value in ("> 50.000", "50.000 - 100.000", "200,000", "12%", "(1.250)"):
        assert not _dimension_value(value), value
    for value in ("Zone Noord", "P3 Atelier", "BE-Retail 2"):
        assert _dimension_value(value), value


def test_period_labels_are_not_dimension_values_but_bare_month_names_are() -> None:
    from core.client_guard.detector import _dimension_value

    labels = (
        "JAN 23",
        "Sep-2026",
        "maart 2024",
        "févr. 2025",
        "Q3 2025",
        "2025 Q3",
        "FY24",
        "FY 2024/25",
        "H1 2025",
        "W12 2026",
        "P03 2025",
        "2025M03",
        "2026-Sep",
        "Okt '25",
    )
    for value in labels:
        assert not _dimension_value(value), value
    for value in ("June", "April", "Mars", "August Maes", "Q3 Atelier", "Mars Belgium"):
        assert _dimension_value(value), value


def test_period_table_words_give_no_identity_prefix() -> None:
    from core.client_guard.detector import dimension_prefix

    assert dimension_prefix("s_Enterprise_Period", "Month Year (MMMYY)") == "DIM"
    assert dimension_prefix("d_Enterprise", "Name") == "ENT"
    assert dimension_prefix("s_Enterprise_Period", "Enterprise Name") == "ENT"  # column words


def test_dimension_values_of_party_and_security_tables_are_strong() -> None:
    m = FakeModel()
    m.col("d_Vendor", "Address", ["Kerkstraat 12 Gent", "Stationsplein 4"])
    m.col("d_Zone", "Zone", ["Zone Noord", "Zone Zuid"])
    result = detect(m, policy="dimensions")
    strong = {v for b in result.batches if b.strong for v in b.values}
    weak = {v for b in result.batches if not b.strong for v in b.values}
    assert {"Kerkstraat 12 Gent", "Stationsplein 4"} <= strong
    assert {"Zone Noord", "Zone Zuid"} <= weak


def test_a_calculated_column_in_error_holds_no_data_and_does_not_block() -> None:
    """Desktop: 'column ... does not hold any data because there is an error in its expression'.
    There is nothing to mask in it, so the model is still read completely."""
    from core.client_guard.detector import NO_DATA_CODE

    m = FakeModel()
    m.col("d_Customer", "RLS Key 2", ["x"])
    m.col("d_Product", "Product Name", ["Widget Alpha", "Gadget Beta"])
    m.fail_on = {("d_Customer", "RLS Key 2", 1)}
    m.fail_message = NO_DATA_CODE
    result = detect(m, policy="dimensions")
    assert result.ready is True
    assert (
        not decisions(result).get(("d_Customer", "RLS Key 2"), None)
        or not decisions(result)[("d_Customer", "RLS Key 2")].not_ready
    )
