"""Tests for PBIR vs legacy report-format detection (core/pbip/report_format.py)."""

from __future__ import annotations

import json

import pytest

from core.pbip.report_format import (
    FORMAT_LEGACY,
    FORMAT_PBIR,
    FORMAT_UNKNOWN,
    attach_format_warning,
    detect_report_format,
)


def _make_pbir_report(root, name="Demo.Report"):
    rdir = root / name
    (rdir / "definition" / "pages").mkdir(parents=True)
    (rdir / "definition" / "pages" / "pages.json").write_text("{}", encoding="utf-8")
    return rdir


def _make_legacy_report(root, name="Legacy.Report"):
    rdir = root / name
    rdir.mkdir(parents=True)
    (rdir / "report.json").write_text(json.dumps({"sections": []}), encoding="utf-8")
    return rdir


class TestDetectFormat:
    def test_pbir_report_detected(self, tmp_path):
        rdir = _make_pbir_report(tmp_path)
        res = detect_report_format(str(rdir))
        assert res["format"] == FORMAT_PBIR
        assert res["is_legacy"] is False
        assert res["report_dir"] == str(rdir)
        assert "definition" in res["reason"]
        # No legacy noise on the happy path.
        assert "warning" not in res

    def test_legacy_report_detected_with_actionable_advisory(self, tmp_path):
        rdir = _make_legacy_report(tmp_path)
        res = detect_report_format(str(rdir))
        assert res["format"] == FORMAT_LEGACY
        assert res["is_legacy"] is True
        # The warning must explain the auto-convert hazard, not just name it.
        assert "AUTO-CONVERT" in res["warning"]
        assert res["deprecated_since"] == "2026-06"
        assert "pbir report convert" in res["convert_with"]
        assert "Legacy.Report" in res["convert_with"]

    def test_both_present_is_pbir_with_remnant_flag(self, tmp_path):
        """A stale report.json beside definition/ must NOT read as legacy."""
        rdir = _make_pbir_report(tmp_path)
        (rdir / "report.json").write_text("{}", encoding="utf-8")
        res = detect_report_format(str(rdir))
        assert res["format"] == FORMAT_PBIR
        assert res["is_legacy"] is False
        assert res["legacy_remnant"] is True
        assert "stale report.json" in res["reason"]

    def test_neither_present_is_unknown(self, tmp_path):
        rdir = tmp_path / "Empty.Report"
        rdir.mkdir()
        res = detect_report_format(str(rdir))
        assert res["format"] == FORMAT_UNKNOWN
        assert res["is_legacy"] is False

    def test_missing_path_is_unknown_not_raising(self, tmp_path):
        res = detect_report_format(str(tmp_path / "nope"))
        assert res["format"] == FORMAT_UNKNOWN
        assert "does not exist" in res["reason"]


class TestPathResolution:
    def test_project_root_with_single_report_folder(self, tmp_path):
        _make_legacy_report(tmp_path)
        res = detect_report_format(str(tmp_path))
        assert res["format"] == FORMAT_LEGACY

    def test_pbip_file_resolves_to_sibling_report(self, tmp_path):
        _make_pbir_report(tmp_path)
        pbip = tmp_path / "Demo.pbip"
        pbip.write_text("{}", encoding="utf-8")
        res = detect_report_format(str(pbip))
        assert res["format"] == FORMAT_PBIR

    def test_pbip_resolves_via_artifacts_path_amid_many_siblings(self, tmp_path):
        """A repo folder normally holds SEVERAL *.Report siblings (G02 has four),
        so a .pbip must resolve through its declared artifacts path rather than
        the exactly-one-child heuristic."""
        _make_pbir_report(tmp_path, "R0201-Financial Reporting.Report")
        _make_legacy_report(tmp_path, "R0202-FINREP2.Report")
        _make_pbir_report(tmp_path, "R0200-SemanticModel.Report")
        pbip = tmp_path / "R0201-Financial Reporting.pbip"
        pbip.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "artifacts": [{"report": {"path": "R0201-Financial Reporting.Report"}}],
                }
            ),
            encoding="utf-8",
        )
        res = detect_report_format(str(pbip))
        assert res["format"] == FORMAT_PBIR
        assert res["report_dir"].endswith("R0201-Financial Reporting.Report")

    def test_pbip_artifacts_path_can_point_at_a_legacy_report(self, tmp_path):
        _make_pbir_report(tmp_path, "A.Report")
        _make_legacy_report(tmp_path, "B.Report")
        pbip = tmp_path / "B.pbip"
        pbip.write_text(
            json.dumps({"artifacts": [{"report": {"path": "B.Report"}}]}),
            encoding="utf-8",
        )
        res = detect_report_format(str(pbip))
        assert res["format"] == FORMAT_LEGACY
        assert res["is_legacy"] is True

    def test_pbip_falls_back_to_filename_stem(self, tmp_path):
        """Malformed/unreadable artifacts must still resolve Foo.pbip -> Foo.Report."""
        _make_pbir_report(tmp_path, "Solo.Report")
        _make_pbir_report(tmp_path, "Other.Report")
        pbip = tmp_path / "Solo.pbip"
        pbip.write_text("not json at all", encoding="utf-8")
        res = detect_report_format(str(pbip))
        assert res["format"] == FORMAT_PBIR
        assert res["report_dir"].endswith("Solo.Report")

    def test_pbip_pointing_nowhere_is_unknown(self, tmp_path):
        _make_pbir_report(tmp_path, "A.Report")
        _make_pbir_report(tmp_path, "B.Report")
        pbip = tmp_path / "Ghost.pbip"
        pbip.write_text(
            json.dumps({"artifacts": [{"report": {"path": "Missing.Report"}}]}),
            encoding="utf-8",
        )
        res = detect_report_format(str(pbip))
        assert res["format"] == FORMAT_UNKNOWN

    def test_ambiguous_root_with_two_reports_is_unknown(self, tmp_path):
        _make_pbir_report(tmp_path, "A.Report")
        _make_legacy_report(tmp_path, "B.Report")
        res = detect_report_format(str(tmp_path))
        assert res["format"] == FORMAT_UNKNOWN
        assert "single" in res["reason"]

    def test_case_insensitive_report_suffix(self, tmp_path):
        rdir = tmp_path / "Mixed.REPORT"
        (rdir / "definition").mkdir(parents=True)
        res = detect_report_format(str(rdir))
        assert res["format"] == FORMAT_PBIR


class TestAttachFormatWarning:
    def test_attaches_on_legacy(self, tmp_path):
        rdir = _make_legacy_report(tmp_path)
        out = attach_format_warning({"success": True, "pages": []}, str(rdir))
        assert out["report_format"] == FORMAT_LEGACY
        assert "AUTO-CONVERT" in out["format_warning"]
        assert out["success"] is True  # original payload preserved

    def test_noop_on_pbir(self, tmp_path):
        rdir = _make_pbir_report(tmp_path)
        out = attach_format_warning({"success": True}, str(rdir))
        assert "format_warning" not in out
        assert "report_format" not in out

    def test_flags_stale_remnant_without_claiming_legacy(self, tmp_path):
        rdir = _make_pbir_report(tmp_path)
        (rdir / "report.json").write_text("{}", encoding="utf-8")
        out = attach_format_warning({"success": True}, str(rdir))
        assert out["report_format"] == FORMAT_PBIR
        assert "stale report.json" in out["format_warning"]

    def test_never_overwrites_handler_supplied_keys(self, tmp_path):
        rdir = _make_legacy_report(tmp_path)
        out = attach_format_warning({"success": True, "report_format": "HANDLER_SET"}, str(rdir))
        assert out["report_format"] == "HANDLER_SET"

    @pytest.mark.parametrize("bad", [None, "", "  " and None])
    def test_noop_without_path(self, bad):
        payload = {"success": True}
        assert attach_format_warning(payload, bad) is payload

    def test_noop_on_non_dict(self):
        assert attach_format_warning("not a dict", "/x") == "not a dict"

    def test_safe_on_error_envelope(self, tmp_path):
        rdir = _make_legacy_report(tmp_path)
        out = attach_format_warning(
            {"success": False, "error_type": "boom", "error": "x"}, str(rdir)
        )
        # Advisory attaches without disturbing the error contract.
        assert out["success"] is False
        assert out["error_type"] == "boom"
        assert out["report_format"] == FORMAT_LEGACY
