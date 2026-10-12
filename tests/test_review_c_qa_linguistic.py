"""Review slice C — 02_QA_Linguistic on real TMDL projects (C-17 / G-23).

TMDL PBIP projects keep the Q&A schema INSIDE ``definition/cultures/<c>.tmdl``
(``linguisticMetadata =`` + an indented JSON block + ``contentType: json``);
the old manager only looked for ``cultures/<c>/linguisticMetadata.json`` and
failed with "not found" on every real project. Uses the synthetic golden
fixture (copied to tmp_path before any write).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.operations.qa_linguistic_manager import QaLinguisticManager

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"


@pytest.fixture
def project(tmp_path):
    dst = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, dst)
    return dst


def _culture_file(project: Path) -> Path:
    return project / "MDL-Core.SemanticModel" / "definition" / "cultures" / "en-US.tmdl"


@pytest.mark.parametrize(
    "target",
    ["MDL-Core.SemanticModel", "MDL-Core.pbip", "RPT-Sales.pbip"],
)
def test_c17_load_reads_the_culture_tmdl_block(project, target):
    out = QaLinguisticManager().load(str(project / target))
    assert out["success"] is True, out
    assert out["storage"] == "tmdl" and out["culture"] == "en-US"
    assert "sales" in out["schema"]["Entities"]


def test_c17_write_round_trips_and_keeps_the_tmdl_frame(project):
    path = _culture_file(project)
    before = path.read_bytes()
    mgr = QaLinguisticManager()
    out = mgr.add_synonym(str(project / "MDL-Core.SemanticModel"), None, "Sales", "revenue")
    assert out["success"] is True and out["action"] == "added", out
    after = path.read_bytes()
    assert after != before
    text = after.decode("utf-8")
    assert text.startswith("cultureInfo en-US")
    assert "\tlinguisticMetadata =" in text and "\t\tcontentType: json" in text
    assert ("\r\n" in before.decode("utf-8")) == ("\r\n" in text)  # EOL style kept
    reread = mgr.load(str(project / "MDL-Core.SemanticModel"))
    terms = reread["schema"]["Entities"]["sales"]["Terms"]
    assert {"revenue": {"State": "Authored"}} in terms
    # A second identical add is a no-op and does not rewrite the file.
    again = mgr.add_synonym(str(project / "MDL-Core.SemanticModel"), None, "Sales", "revenue")
    assert again["action"] == "noop" and path.read_bytes() == after


def test_c17_audit_and_remove_on_tmdl(project):
    mgr = QaLinguisticManager()
    root = str(project / "MDL-Core.SemanticModel")
    audit = mgr.audit(root)
    assert audit["success"] is True and audit["entity_count"] >= 1
    mgr.add_synonym(root, None, "sales", "turnover")
    out = mgr.remove_synonym(root, None, "sales", "turnover")
    assert out["success"] is True and out["action"] == "removed"


def test_c17_unknown_culture_names_the_available_ones(project):
    out = QaLinguisticManager().load(str(project / "MDL-Core.SemanticModel"), culture="fr-FR")
    assert out["success"] is False and "en-US" in out["error"]


def test_c17_legacy_json_layout_still_works(tmp_path):
    model = tmp_path / "Legacy.SemanticModel"
    (model / "definition" / "cultures" / "en-US").mkdir(parents=True)
    (model / "definition" / "cultures" / "en-US" / "linguisticMetadata.json").write_text(
        '{"Version": "4.1.0", "Entities": {"x": {"Terms": []}}}', encoding="utf-8"
    )
    out = QaLinguisticManager().load(str(model))
    assert out["success"] is True and out["storage"] == "json"
