"""
Model Comparison Orchestrator

Compares two open Power BI Desktop models: full TMSL of each ->
:func:`normalize_tmsl` -> :class:`ModelDiffer` -> HTML (+ optional JSON) report.

2026-09-22 review:

- I-07: the input is the full ``JsonSerializer`` TMSL (the BPA snapshot), not
  ``export_tmdl_structure`` whose keys the differ never read — relationship
  cardinality / cross-filter, RLS filters, calculated-column DAX, partitions,
  calculation items, perspectives, roles and model properties are compared.
- I-21: only the ports THIS call connected are released (multi_instance_manager
  reference-counts, so a concurrent compare on the same ports keeps its
  connection); nothing is written to temp files, so no deleted paths are
  returned; the default report goes to the repo ``exports/model_diffs``;
  labels come from the Desktop instance (file name), not "Model (Port N)".
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.comparison.model_diff_engine import ModelDiffer
from core.comparison.model_diff_report import ModelDiffReportV2
from core.comparison.tmsl_normalizer import normalize_tmsl
from core.infrastructure.multi_instance_manager import multi_instance_manager

logger = logging.getLogger(__name__)

# core/comparison/<this file> -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = _REPO_ROOT / "exports" / "model_diffs"

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _is_uuid(value: Any) -> bool:
    return bool(_UUID_RE.match(str(value or "")))


class ModelComparisonOrchestrator:
    """Orchestrates the complete model comparison workflow."""

    def __init__(self, exporter_factory=None):
        """``exporter_factory(connection) -> exporter`` is injectable for tests."""
        if exporter_factory is None:

            def exporter_factory(connection):
                from core.model.model_exporter import ModelExporter

                return ModelExporter(connection)

        self._exporter_factory = exporter_factory

    def _export(self, port: int) -> Dict[str, Any]:
        conn_manager = multi_instance_manager.get_instance(port)
        if not conn_manager:
            raise RuntimeError(f"No connection manager for port {port}")
        exported = self._exporter_factory(conn_manager.get_connection()).export_tmsl(
            include_full_model=True
        )
        if not exported.get("success") or not isinstance(exported.get("model"), dict):
            raise RuntimeError(
                f"TMSL export failed for port {port}: {exported.get('error', 'no model returned')}"
            )
        return exported

    @staticmethod
    def _label(
        explicit: Optional[str], conn_result: Dict[str, Any], exported: Dict[str, Any], port: int
    ) -> str:
        if explicit:
            return explicit
        info = conn_result.get("instance_info") or {}
        for candidate in (
            info.get("display_name"),
            info.get("database"),
            conn_result.get("database_name"),
            exported.get("database_name"),
        ):
            if candidate and not _is_uuid(candidate):
                return re.sub(r"\s*\(Port\s+\d+\)\s*$", "", str(candidate))
        return f"Model (Port {port})"

    def compare_targets(
        self,
        old: Any,
        new: Any,
        output_path: Optional[str] = None,
        generate_json: bool = False,
        loader=None,
        report: bool = True,
        include_diff: bool = False,
    ) -> Dict[str, Any]:
        """F-17: compare any two targets (port | xmla | pbip_path | git_ref + pbip_path).

        Same engine as :meth:`compare_models`; the result adds ``affects_results`` /
        ``cosmetic`` (see ``compare_targets.classify_changes``). ``loader(kind, spec)`` is
        injectable for tests; a port side is exported from its running Desktop instance.
        ``include_diff=True`` also returns the raw differ result as ``comparison`` (with
        ``has_changes``): the shape ``07_PBIP_Operations compare_models`` returns.
        """
        from core.comparison import compare_targets as ct

        loader = loader or ct.load_target
        acquired: List[int] = []
        try:
            sides = {}
            for side, raw in (("old", old), ("new", new)):
                kind, spec = ct.parse_target(raw, side)
                if kind == "port":
                    port = spec["port"]
                    conn = multi_instance_manager.connect_to_instance(port)
                    if not conn.get("success"):
                        raise ct.TargetError(
                            f"Failed to connect to {side} instance (port {port}): {conn.get('error')}",
                            "connection_failed",
                        )
                    acquired.append(port)
                    exported = self._export(port)
                    sides[side] = {
                        "model": exported["model"],
                        "compatibility_level": exported.get("compatibility_level"),
                        "label": self._label(None, conn, exported, port),
                        "source": {"port": port},
                    }
                else:
                    sides[side] = loader(kind, spec)
            o, n = sides["old"], sides["new"]
            model1 = normalize_tmsl(
                o["model"], name=o["label"], compatibility_level=o.get("compatibility_level")
            )
            model2 = normalize_tmsl(
                n["model"], name=n["label"], compatibility_level=n.get("compatibility_level")
            )
            diff_result = ModelDiffer(model1, model2).compare()
            diff_result["summary"]["model1_name"] = o["label"]
            diff_result["summary"]["model2_name"] = n["label"]
            classified = ct.classify_changes(diff_result)
            out: Dict[str, Any] = {
                "success": True,
                "summary": {
                    "old": o["label"],
                    "new": n["label"],
                    "total_changes": diff_result["summary"]["total_changes"],
                    "affects_results": classified["affects_results_count"],
                    "cosmetic": classified["cosmetic_count"],
                    "changes_by_category": diff_result["summary"]["changes_by_category"],
                },
                "sources": {"old": o["source"], "new": n["source"]},
                "affects_results": classified["affects_results"],
                "cosmetic": classified["cosmetic"],
            }
            if classified["truncated"]:
                out["truncated"] = True
            if include_diff:
                out["comparison"] = {
                    "has_changes": bool(diff_result["summary"]["total_changes"]),
                    **{k: v for k, v in diff_result.items() if k != "summary"},
                }
            warnings = [w for w in (o.get("warning"), n.get("warning")) if w]
            if warnings:
                out["warnings"] = warnings
            if report:
                if output_path is None:
                    DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    output_path = str(DEFAULT_REPORT_DIR / f"model_diff_{stamp}.html")
                else:
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                html = ModelDiffReportV2(
                    diff_result, tmdl1_data=model1, tmdl2_data=model2
                ).generate_html(output_path)
                json_path = None
                if generate_json:
                    json_path = str(Path(html).with_suffix(".json"))
                    with open(json_path, "w", encoding="utf-8") as fh:
                        json.dump(diff_result, fh, indent=2, ensure_ascii=False, default=str)
                out["reports"] = {"html": html, "json": json_path}
            return out
        except ct.TargetError as exc:
            return {"success": False, "error": str(exc), "error_type": exc.error_type, **exc.extra}
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Target comparison failed: {exc}", exc_info=True)
            return {"success": False, "error": str(exc), "error_type": "operation_failed"}
        finally:
            for port in acquired:
                try:
                    multi_instance_manager.disconnect_instance(port)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Error releasing instance {port}: {exc}")

    def compare_models(
        self,
        port1: int,
        port2: int,
        output_path: Optional[str] = None,
        generate_json: bool = False,
        model1_label: Optional[str] = None,
        model2_label: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Compare two Power BI models and generate a diff report.

        Args:
            port1: Port of the BASE/OLD instance
            port2: Port of the NEW instance
            output_path: HTML report path (caller path-guards it); default
                ``<repo>/exports/model_diffs/model_diff_<ts>.html``
            generate_json: Also write the diff as JSON next to the HTML
            model1_label / model2_label: display names (default: Desktop file names)
        """
        logger.info(f"Starting model comparison: port {port1} vs port {port2}")
        acquired = []
        try:
            conn1 = multi_instance_manager.connect_to_instance(port1)
            if not conn1.get("success"):
                raise RuntimeError(
                    f"Failed to connect to instance 1 (port {port1}): {conn1.get('error')}"
                )
            acquired.append(port1)
            conn2 = multi_instance_manager.connect_to_instance(port2)
            if not conn2.get("success"):
                raise RuntimeError(
                    f"Failed to connect to instance 2 (port {port2}): {conn2.get('error')}"
                )
            acquired.append(port2)

            exported1 = self._export(port1)
            exported2 = self._export(port2)
            label1 = self._label(model1_label, conn1, exported1, port1)
            label2 = self._label(model2_label, conn2, exported2, port2)

            model1 = normalize_tmsl(
                exported1["model"],
                name=label1,
                compatibility_level=exported1.get("compatibility_level"),
            )
            model2 = normalize_tmsl(
                exported2["model"],
                name=label2,
                compatibility_level=exported2.get("compatibility_level"),
            )

            diff_result = ModelDiffer(model1, model2).compare()
            diff_result["summary"]["model1_name"] = label1
            diff_result["summary"]["model2_name"] = label2
            total_changes = diff_result["summary"]["total_changes"]

            if output_path is None:
                DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = str(DEFAULT_REPORT_DIR / f"model_diff_{timestamp}.html")
            else:
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            html_report_path = ModelDiffReportV2(
                diff_result, tmdl1_data=model1, tmdl2_data=model2
            ).generate_html(output_path)

            json_report_path = None
            if generate_json:
                json_path = Path(html_report_path).with_suffix(".json")
                with open(json_path, "w", encoding="utf-8") as fh:
                    json.dump(diff_result, fh, indent=2, ensure_ascii=False, default=str)
                json_report_path = str(json_path)

            return {
                "success": True,
                "summary": {
                    "model1_name": label1,
                    "model2_name": label2,
                    "total_changes": total_changes,
                    "cosmetic_changes": diff_result["summary"].get("cosmetic_changes", 0),
                    "changes_by_category": diff_result["summary"]["changes_by_category"],
                },
                "reports": {"html": html_report_path, "json": json_report_path},
                "models": {
                    "model1": {"port": port1, "statistics": exported1.get("statistics")},
                    "model2": {"port": port2, "statistics": exported2.get("statistics")},
                },
            }
        except Exception as e:
            logger.error(f"Model comparison failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            # Release exactly the references this call took (I-21).
            for port in acquired:
                try:
                    multi_instance_manager.disconnect_instance(port)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Error releasing instance {port}: {e}")
