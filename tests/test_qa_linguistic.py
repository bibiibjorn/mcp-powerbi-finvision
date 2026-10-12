"""Tests for Q&A linguistic schema read/write."""

import json
from pathlib import Path


from core.operations.qa_linguistic_manager import QaLinguisticManager

SAMPLE_SCHEMA = {
    "Version": "1.0.0",
    "Language": "en-US",
    "Entities": {
        "Sales": {
            "Definition": {"Binding": {"ConceptualEntity": "Sales"}},
            "State": "Generated",
            "Terms": [{"Sales": {"State": "Generated"}}],
        },
        "OrphanEntity": {
            "Definition": {"Binding": {}},
            "State": "Generated",
            "Terms": [{"orphan": {"State": "Generated"}}, {"alt": {"State": "Authored"}}],
        },
    },
    "Relationships": {},
    "PhraseTables": {},
    "Examples": [],
}


def _make_pbip(tmp_path: Path) -> Path:
    sm = tmp_path / "Model.SemanticModel"
    cult = sm / "definition" / "cultures" / "en-US"
    cult.mkdir(parents=True)
    (cult / "linguisticMetadata.json").write_text(json.dumps(SAMPLE_SCHEMA), encoding="utf-8")
    return tmp_path


def _schema_after(tmp_path: Path) -> dict:
    return json.loads(
        (
            tmp_path
            / "Model.SemanticModel"
            / "definition"
            / "cultures"
            / "en-US"
            / "linguisticMetadata.json"
        ).read_text(encoding="utf-8")
    )


def test_load_returns_schema(tmp_path):
    proj = _make_pbip(tmp_path)
    mgr = QaLinguisticManager()
    result = mgr.load(pbip_path=str(proj), culture="en-US")
    assert result["success"] is True
    assert result["schema"]["Version"] == "1.0.0"
    assert "Sales" in result["schema"]["Entities"]


def test_load_unknown_culture(tmp_path):
    proj = _make_pbip(tmp_path)
    mgr = QaLinguisticManager()
    result = mgr.load(pbip_path=str(proj), culture="fr-FR")
    assert result["success"] is False
    assert "linguistic" in result["error"].lower() or "not found" in result["error"].lower()


def test_audit_reports_missing_synonyms(tmp_path):
    proj = _make_pbip(tmp_path)
    mgr = QaLinguisticManager()
    result = mgr.audit(pbip_path=str(proj), culture="en-US")
    assert result["success"] is True
    # Sales has only one term ("Sales" itself) — counts as bare
    assert "Sales" in result["entities_without_extra_synonyms"]
    # OrphanEntity has 2 terms, so should NOT be flagged for missing synonyms
    assert "OrphanEntity" not in result["entities_without_extra_synonyms"]
    # OrphanEntity has empty Binding -> unbound list
    assert "OrphanEntity" in result["entities_without_binding"]
    assert result["entity_count"] == 2


def test_set_include_in_qa_writes_flag(tmp_path):
    proj = _make_pbip(tmp_path)
    mgr = QaLinguisticManager()
    result = mgr.set_include_in_qa(
        pbip_path=str(proj),
        culture="en-US",
        entity="Sales",
        include=False,
    )
    assert result["success"] is True
    schema = _schema_after(proj)
    assert schema["Entities"]["Sales"]["IncludeInQnA"] is False


def test_set_include_in_qa_unknown_entity(tmp_path):
    proj = _make_pbip(tmp_path)
    mgr = QaLinguisticManager()
    result = mgr.set_include_in_qa(
        pbip_path=str(proj),
        culture="en-US",
        entity="DoesNotExist",
        include=False,
    )
    assert result["success"] is False
    assert "not in schema" in result["error"].lower()


def test_add_synonym_appends_term(tmp_path):
    proj = _make_pbip(tmp_path)
    mgr = QaLinguisticManager()
    result = mgr.add_synonym(
        pbip_path=str(proj),
        culture="en-US",
        entity="Sales",
        synonym="Revenue",
    )
    assert result["success"] is True
    assert result["action"] == "added"
    schema = _schema_after(proj)
    terms = schema["Entities"]["Sales"]["Terms"]
    assert any(isinstance(t, dict) and "Revenue" in t for t in terms)


def test_add_synonym_idempotent(tmp_path):
    proj = _make_pbip(tmp_path)
    mgr = QaLinguisticManager()
    # "Sales" already has term {"Sales": ...}
    result = mgr.add_synonym(
        pbip_path=str(proj),
        culture="en-US",
        entity="Sales",
        synonym="Sales",
    )
    assert result["success"] is True
    assert result["action"] == "noop"


def test_remove_synonym_drops_term(tmp_path):
    proj = _make_pbip(tmp_path)
    mgr = QaLinguisticManager()
    result = mgr.remove_synonym(
        pbip_path=str(proj),
        culture="en-US",
        entity="OrphanEntity",
        synonym="alt",
    )
    assert result["success"] is True
    assert result["action"] == "removed"
    schema = _schema_after(proj)
    terms = schema["Entities"]["OrphanEntity"]["Terms"]
    assert not any(isinstance(t, dict) and "alt" in t for t in terms)


def test_remove_synonym_not_present(tmp_path):
    proj = _make_pbip(tmp_path)
    mgr = QaLinguisticManager()
    result = mgr.remove_synonym(
        pbip_path=str(proj),
        culture="en-US",
        entity="Sales",
        synonym="DoesNotExist",
    )
    assert result["success"] is False
    assert "not present" in result["error"].lower()


def test_load_handles_pbip_file_path(tmp_path):
    """`pbip_path` may be a .pbip file path; resolver climbs to its parent."""
    proj = _make_pbip(tmp_path)
    pbip_file = proj / "demo.pbip"
    pbip_file.write_text("{}", encoding="utf-8")
    mgr = QaLinguisticManager()
    result = mgr.load(pbip_path=str(pbip_file), culture="en-US")
    assert result["success"] is True


def test_handler_dispatches_operations(tmp_path):
    """Handler-level dispatch covers all 5 ops + missing-arg error paths."""
    from server.handlers.qa_linguistic_handler import handle_qa_linguistic

    proj = _make_pbip(tmp_path)
    base = {"pbip_path": str(proj), "culture": "en-US"}

    # load
    r = handle_qa_linguistic({**base, "operation": "load"})
    assert r["success"] is True

    # audit
    r = handle_qa_linguistic({**base, "operation": "audit"})
    assert r["success"] is True

    # set_include_in_qa missing entity
    r = handle_qa_linguistic({**base, "operation": "set_include_in_qa"})
    assert r["success"] is False
    assert "entity" in r["error"]

    # add_synonym missing args
    r = handle_qa_linguistic({**base, "operation": "add_synonym", "entity": "Sales"})
    assert r["success"] is False

    # unknown operation
    r = handle_qa_linguistic({**base, "operation": "bogus"})
    assert r["success"] is False
    assert "unknown" in r["error"].lower()

    # missing pbip_path
    r = handle_qa_linguistic({"operation": "load"})
    assert r["success"] is False
    assert "pbip_path" in r["error"]
