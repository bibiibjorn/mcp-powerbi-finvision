"""2026-09-22 review E-17..E-20 — one VertiPaq pipeline, H$ parsing, label quoting.

Synthetic DMV rows mirror the real shapes: storage ids carry an ``(id)`` suffix,
H$ structure ids are ``H$<Table> (id)$<Column> (id)`` and an H$ segment row's
column is the POS_TO_ID / ID_TO_POS structure (needs live confirmation — see
STATUS-WP6).
"""

from __future__ import annotations

from types import SimpleNamespace as NS

from core.dax import vpa_extractor as vpa
from core.dax.vertipaq_analyzer import VertiPaqAnalyzer
from core.dax.vertipaq_reporter import build_report
from core.dax.vertipaq_storage_report import VertiPaqStorageReport

MB = 1024 * 1024

STORAGE_TABLES = [{"TABLE_NAME": "Sales", "TABLE_ID": "Sales (12)", "ROWS_IN_TABLE": 1000}]
STORAGE_COLUMNS = [
    # A renamed column: its storage id no longer matches its name.
    {
        "TABLE_NAME": "Sales",
        "COLUMN_ID": "OldName (34)",
        "COLUMN_NAME": "Amount",
        "Data Type": "DBTYPE_WSTR",
        "DICTIONARY_SIZE_BYTES": 2 * MB,
        "COLUMN_ENCODING_INT": 1,
    },
    {
        "TABLE_NAME": "Sales",
        "COLUMN_ID": "Qty (35)",
        "COLUMN_NAME": "Qty",
        "Data Type": "Int64",
        "DICTIONARY_SIZE_BYTES": 0,
        "COLUMN_ENCODING_INT": 2,
    },
]
SEGMENTS = [
    {"TABLE_NAME": "Sales", "COLUMN_NAME": "OldName (34)", "USED_SIZE": 3 * MB, "BITS_COUNT": 9},
    {"TABLE_NAME": "Sales", "COLUMN_NAME": "Qty (35)", "USED_SIZE": 1 * MB, "BITS_COUNT": 4},
]
HIERARCHIES = [
    {
        "TABLE_NAME": "Sales",
        "STRUCTURE_NAME": "POS_TO_ID",
        "USED_SIZE": 256 * 1024,
        "COLUMN_HIERARCHY_ID": "H$Sales (12)$Amount (34)",
    },
    {
        "TABLE_NAME": "Sales",
        "STRUCTURE_NAME": "ID_TO_POS",
        "USED_SIZE": 256 * 1024,
        "COLUMN_HIERARCHY_ID": "H$Sales (12)$Amount (34)",
    },
]
CARDINALITY = [
    {
        "TABLE_NAME": "Sales",
        "COLUMN_HIERARCHY_ID": "H$Sales (12)$Amount (34)",
        "COLUMN_CARDINALITY": 777,
    },
    {
        "TABLE_NAME": "Sales",
        "COLUMN_HIERARCHY_ID": "H$Sales (12)$Qty (35)",
        "COLUMN_CARDINALITY": 12,
    },
]


class FakeQE:
    def __init__(self):
        self.dax = []

    def execute_dmv_query(self, q):
        if "DISCOVER_STORAGE_TABLES" in q and "'H$'" in q:
            return {"success": True, "rows": CARDINALITY}
        if "DISCOVER_STORAGE_TABLES" in q:
            return {"success": True, "rows": STORAGE_TABLES}
        if "DISCOVER_STORAGE_TABLE_COLUMNS " in q or "COLUMN_TYPE = 'BASIC_DATA'" in q:
            return {"success": True, "rows": STORAGE_COLUMNS}
        if "DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS" in q:
            if "'H$'" in q:
                return {"success": True, "rows": HIERARCHIES}
            if "'U$'" in q or "'R$'" in q:
                return {"success": True, "rows": []}
            return {"success": True, "rows": SEGMENTS}
        return {"success": True, "rows": []}

    def validate_and_execute_dax(self, q, top_n=0, **kw):
        self.dax.append(q)
        return {"success": True, "rows": []}


def _amount(model):
    return next(c for c in model.all_columns() if c.column_name == "Amount")


def test_e18_parse_hierarchy_id_forms():
    assert vpa.parse_hierarchy_id("H$Sales (12)$Amount (34)") == ("Sales", "Amount")
    assert vpa.parse_hierarchy_id("H$Fact (1) (12)$Col (x) (34)") == ("Fact (1)", "Col (x)")
    assert vpa.parse_hierarchy_id("H$Amount (1)", "Sales") == ("Sales", "Amount")
    assert vpa.parse_hierarchy_id("garbage") == ("", "")


def test_e18_hierarchy_size_cardinality_and_id_join():
    model = vpa.extract_model_metrics(FakeQE(), include_stats=False)["model"]
    amount = _amount(model)
    assert amount.data_size == 3 * MB  # segment joined by storage COLUMN_ID, not name
    assert amount.hierarchies_size == 512 * 1024  # POS_TO_ID + ID_TO_POS booked on Amount
    assert amount.total_size == 3 * MB + 2 * MB + 512 * 1024
    assert amount.cardinality == 777  # H$ fallback parsed from the full id


def test_e17_every_module_reports_the_same_encoding_and_size():
    qe = FakeQE()
    model = vpa.extract_model_metrics(qe, include_stats=False)["model"]
    assert _amount(model).encoding == "HASH"

    report = build_report(qe, top_n=10)
    col = next(c for c in report["columns"] if c["column"] == "Amount")
    assert col["encoding"] == "HASH"
    assert col["total_size_mb"] == round(_amount(model).total_size / MB, 3)

    storage = VertiPaqStorageReport(qe).generate_report().to_dict()
    scol = next(c for t in storage["tables"] for c in t["columns"] if c["column"] == "Amount")
    assert scol["encoding"] == "HASH" and scol["total_size_bytes"] == _amount(model).total_size

    cs = NS(is_connected=lambda: True, query_executor=qe)
    va = VertiPaqAnalyzer(cs)
    assert va.load_column_metrics() is True
    m = va.get_column_metrics("Sales[Amount]")
    assert m.encoding == "hash" and m.size_bytes == _amount(model).total_size
    assert m.cardinality == 777


def test_e17_hash_on_text_is_not_a_finding():
    storage = VertiPaqStorageReport(FakeQE()).generate_report().to_dict()
    assert not any(r.get("category") == "encoding" for r in storage["recommendations"])


def test_e20_labels_are_escaped_string_literals():
    qe = FakeQE()
    vpa.collect_table_stats(qe, ['Sales "EU"'])
    vpa.collect_column_stats(qe, [('Sales "EU"', 'Amt "net"')])
    assert '"Sales ""EU"""' in qe.dax[0]
    assert '"Amt ""net"""' in qe.dax[1]
    assert '\'Sales "EU"\'[Amt "net"]' in qe.dax[1]


def test_e19_analyzer_extracts_refs_with_the_lexer():
    va = VertiPaqAnalyzer(None)
    refs = va._extract_column_references("SUM(Sales[Amt]) + COUNTROWS('Sales EU') & \"T[x]\"")
    assert refs == {"Sales[Amt]"}
