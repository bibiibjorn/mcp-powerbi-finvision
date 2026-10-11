"""Model analysis orchestration for 06_Analysis_Operations (simple / full).

2026-09-22 review (slice I):

- I-06 / I-29: ``full`` serialises the model ONCE (TMSL) and feeds that single
  snapshot to BPA, the topology validator, the M-practices scan and the
  relationship overview. Topology reads the TMSL relationships (the INFO path
  is only the fallback and now reads the right ``data`` key).
- I-14: findings from every analysis are flattened into ``result["issues"]``
  (ranked, capped) so the handler's business-impact enrichment runs, and BPA
  severities are read correctly for the recommendation.
- I-15: ``simple`` normalises INFO.* enums/columns once (``info_normalize``):
  bidirectional relationships, calculated columns, RLS table permissions and
  the compatibility level are now reported from real data.
- I-20 / I-24: BPA runs are pure (external rules per call) and ``max_seconds``
  bounds the whole ``full`` run, ``deep`` included.
- I-27: the unreachable legacy entry points (``validate_best_practices``,
  ``full_analysis``, ``analyze_*_unified``, ``profile_columns`` …) were deleted.
"""

import copy
import logging
import re
import time
from typing import Any, Dict, List, Optional

from core.analysis import info_normalize as infn

from .base_orchestrator import BaseOrchestrator
from core.orchestration.orchestrator_simple import OrchestratorSimpleMixin
from core.orchestration.orchestrator_report import OrchestratorReportMixin

logger = logging.getLogger(__name__)

# BPA budgets per depth (the engine runs 1,500 measures x 70 rules in < 1 s, so
# these are generous; ``max_seconds`` from the caller caps the whole run).
_DEPTH_CFG: Dict[str, Dict[str, Any]] = {
    "fast": {"max_seconds": 10, "per_rule_max_ms": 1000, "severity_at_least": "WARNING"},
    "balanced": {"max_seconds": 30, "per_rule_max_ms": 3000},
    "deep": {"max_seconds": 120},
}

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_BPA_SEVERITY = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}
_M_SEVERITY = {"error": "high", "warning": "medium", "info": "low"}

# Time-intelligence name tokens (word-bounded: "Supply"/"Monthly" are not TI).
_TI_RE = re.compile(
    r"(?<![A-Za-z0-9])(YTD|MTD|QTD|PY|LY|YOY|MOM|PYTD|SPLY)(?![A-Za-z0-9])", re.IGNORECASE
)


def is_time_intelligence_name(name: str) -> bool:
    """True when *name* carries a TI token (``Sales YTD``, ``Margin PY %``)."""
    text = str(name or "")
    return bool(_TI_RE.search(text)) or "last year" in text.lower()


class AnalysisOrchestrator(OrchestratorReportMixin, OrchestratorSimpleMixin, BaseOrchestrator):
    """Handles performance and BPA analysis workflows."""

    # ── 06 full ──────────────────────────────────────────────────────────

    def comprehensive_analysis(
        self,
        connection_state,
        scope: str = "all",
        depth: str = "balanced",
        include_bpa: bool = True,
        include_performance: bool = True,
        include_integrity: bool = True,
        max_seconds: Optional[int] = None,
        *,
        extra_rules: Optional[List[Any]] = None,
        include_vertipaq: bool = False,
        issues_max: int = 500,
        violations_max: int = 2000,
    ) -> Dict[str, Any]:
        """
        Unified comprehensive model analysis combining best practices, performance,
        and integrity checks.

        Args:
            connection_state: Current connection state
            scope: "all", "best_practices", "performance", "integrity"
            depth: "fast", "balanced", "deep" (BPA budgets)
            include_bpa / include_performance / include_integrity: section toggles
            max_seconds: budget for the WHOLE run (sections after the deadline
                are skipped with a note; BPA gets what is left)
            extra_rules: external BPA rules for THIS call only (``rules_path``)
            include_vertipaq: annotate the model with VertiPaq cardinality / row
                counts first, so the ``Vertipaq_*`` annotation rules evaluate
            issues_max / violations_max: response caps (totals are always exact)
        """
        from core.validation.error_handler import ErrorHandler

        if not connection_state.is_connected():
            return ErrorHandler.handle_not_connected()

        scope = (scope or "all").lower()
        depth = (depth or "balanced").lower()
        if depth not in _DEPTH_CFG:
            depth = "balanced"
        started = time.time()
        deadline = started + float(max_seconds) if max_seconds is not None else None
        notes: List[str] = []

        results: Dict[str, Any] = {
            "success": True,
            "scope": scope,
            "depth": depth,
            "analyses": {},
        }

        run_bp = scope in ("all", "best_practices")
        run_perf = scope in ("all", "performance") and include_performance
        run_integrity = scope in ("all", "integrity") and include_integrity
        query_executor = getattr(connection_state, "query_executor", None)

        def over_budget(section: str) -> bool:
            if deadline is not None and time.time() > deadline:
                results["analyses"][section] = {
                    "success": False,
                    "skipped": True,
                    "error": f"max_seconds={max_seconds} budget exhausted before this section",
                }
                return True
            return False

        # 0. One TMSL snapshot for BPA + topology + M practices (I-29).
        model: Optional[Dict[str, Any]] = None
        if (run_bp or run_integrity) and query_executor is not None:
            model, tmsl_error = self._fetch_tmsl_model(query_executor)
            results["tmsl"] = {"success": model is not None}
            if tmsl_error:
                results["tmsl"]["error"] = tmsl_error

        # 1. Integrity, topology, model settings
        if run_integrity:
            if not over_budget("integrity"):
                try:
                    validator = connection_state.model_validator
                    if validator:
                        # P-04: the integrity pass honours the run's own deadline
                        results["analyses"]["integrity"] = validator.validate_model_integrity(
                            deadline=deadline
                        )
                    else:
                        results["analyses"]["integrity"] = {
                            "success": False,
                            "error": "Model validator not available",
                        }
                except Exception as e:
                    logger.error(f"Integrity validation failed: {e}")
                    results["analyses"]["integrity"] = {
                        "success": False,
                        "error": f"Integrity validation failed: {str(e)}",
                    }
            if not over_budget("topology"):
                results["analyses"]["topology"] = self._run_topology_analysis(
                    query_executor, connection_state, tmsl_model=model
                )
            if not over_budget("model_settings"):
                try:
                    results["analyses"]["model_settings"] = self._run_model_settings_analysis(
                        tmsl_model=model, query_executor=query_executor
                    )
                except Exception as e:
                    logger.error(f"Model settings analysis failed: {e}")
                    results["analyses"]["model_settings"] = {
                        "success": False,
                        "error": f"Model settings analysis failed: {str(e)}",
                    }

        # 2. Best Practice Analyzer
        if run_bp and include_bpa and not over_budget("bpa"):
            results["analyses"]["bpa"] = self._run_bpa(
                connection_state,
                model,
                results.get("tmsl", {}).get("error"),
                depth,
                deadline,
                extra_rules,
                include_vertipaq,
                violations_max,
            )

        # 3. M query practices (partitions + shared expressions, same snapshot)
        if run_bp and not over_budget("m_practices"):
            try:
                from core.analysis.m_practices import scan_m_practices

                if model is not None or query_executor is not None:
                    results["analyses"]["m_practices"] = scan_m_practices(
                        query_executor, tmsl=model
                    )
                else:
                    results["analyses"]["m_practices"] = {
                        "success": False,
                        "error": "Query executor not available",
                    }
            except Exception as e:
                logger.error(f"M practices scan failed: {e}")
                results["analyses"]["m_practices"] = {
                    "success": False,
                    "error": f"M practices scan failed: {str(e)}",
                }

        # 4. Performance (relationship cardinality)
        if run_perf and not over_budget("performance"):
            try:
                performance_optimizer = connection_state.performance_optimizer
                if performance_optimizer:
                    results["analyses"][
                        "performance"
                    ] = performance_optimizer.analyze_relationship_cardinality()
                else:
                    results["analyses"]["performance"] = {
                        "success": False,
                        "error": "Performance optimizer not available",
                    }
            except Exception as e:
                logger.error(f"Performance analysis failed: {e}")
                results["analyses"]["performance"] = {
                    "success": False,
                    "error": f"Performance analysis failed: {str(e)}",
                }

        # 5. Relationship overview (from the snapshot when we have one)
        try:
            if model is not None:
                rels = infn.relationships_from_tmsl(model)
                results["analyses"]["relationships"] = {
                    "success": True,
                    "source": "tmsl",
                    "count": len(rels),
                    "relationships": rels,
                }
            elif query_executor is not None:
                raw = query_executor.execute_info_query("RELATIONSHIPS")
                rels = [infn.normalize_relationship(r) for r in raw.get("rows", []) or []]
                results["analyses"]["relationships"] = {
                    "success": bool(raw.get("success")),
                    "source": "info",
                    "count": len(rels),
                    "relationships": rels,
                }
        except Exception as e:
            logger.error(f"Relationship overview failed: {e}")
            results["analyses"]["relationships"] = {
                "success": False,
                "error": f"Relationship overview failed: {str(e)}",
            }

        results["execution_time_seconds"] = round(time.time() - started, 2)
        if deadline is not None and time.time() > deadline:
            notes.append(f"max_seconds={max_seconds} reached; later sections may be skipped.")

        # Aggregate
        total_issues = 0
        successful = 0
        for name, res in results["analyses"].items():
            if not isinstance(res, dict):
                continue
            if res.get("success"):
                successful += 1
            total_issues += self._issue_count(name, res)

        issues = self._flatten_issues(results["analyses"])
        results["issues_total"] = len(issues)
        results["issues"] = issues[: max(int(issues_max), 0)] if issues_max else issues
        if len(issues) > len(results["issues"]):
            notes.append(
                f"issues truncated to {len(results['issues'])} of {len(issues)} "
                "(highest severity first); per-analysis sections hold the full lists."
            )
        results["summary"] = {
            "total_issues": total_issues,
            "successful_analyses": successful,
            "total_analyses": len(results["analyses"]),
            "recommendation": self._get_recommendation(total_issues, results["analyses"]),
        }
        if notes:
            results["notes"] = notes
        return results

    @staticmethod
    def _fetch_tmsl_model(query_executor) -> "tuple[Optional[Dict[str, Any]], Optional[str]]":
        """One ``get_tmsl_definition()`` round-trip, parsed once."""
        try:
            res = query_executor.get_tmsl_definition()
        except Exception as e:  # noqa: BLE001 — AMO may be absent
            return None, f"TMSL unavailable: {e}"
        if not isinstance(res, dict) or not res.get("success"):
            return (
                None,
                f"Failed to get TMSL: {(res or {}).get('error') if isinstance(res, dict) else res}",
            )
        from core.analysis.bpa_model import resolve_tmsl_model

        model = resolve_tmsl_model(res.get("tmsl"))
        if model is None:
            return None, "TMSL could not be parsed"
        return model, None

    def _run_bpa(
        self,
        connection_state,
        model: Optional[Dict[str, Any]],
        tmsl_error: Optional[str],
        depth: str,
        deadline: Optional[float],
        extra_rules: Optional[List[Any]],
        include_vertipaq: bool,
        violations_max: int,
    ) -> Dict[str, Any]:
        bpa_analyzer = getattr(connection_state, "bpa_analyzer", None)
        if not bpa_analyzer:
            reason = getattr(connection_state, "bpa_unavailable_reason", None)
            return {
                "success": True,
                "skipped": True,
                "reason": reason or "BPA analyzer not available",
                "violations": [],
                "violation_count": 0,
            }
        if model is None:
            return {"success": False, "error": tmsl_error or "Failed to get TMSL"}
        try:
            cfg = dict(_DEPTH_CFG.get(depth, _DEPTH_CFG["balanced"]))
            if deadline is not None:
                cfg["max_seconds"] = max(
                    1.0, min(float(cfg["max_seconds"]), deadline - time.time())
                )
            notes: List[str] = []
            if include_vertipaq:
                model, vp_note = self._annotate_vertipaq(connection_state.query_executor, model)
                notes.append(vp_note)
            run = bpa_analyzer.run(model, cfg, extra_rules=extra_rules)
            violations = [v.to_dict() for v in run.violations]
            out: Dict[str, Any] = {
                "success": True,
                "violation_count": len(violations),
                "violations": violations[:violations_max] if violations_max else violations,
                "summary": run.summary(),
                "coverage": run.coverage,
                "notes": notes + run.notes,
                "elapsed_seconds": run.elapsed_seconds,
            }
            if len(violations) > len(out["violations"]):
                out["violations_truncated"] = True
            return out
        except Exception as e:
            logger.error(f"BPA analysis failed: {e}", exc_info=True)
            return {"success": False, "error": f"BPA analysis failed: {str(e)}"}

    @staticmethod
    def _annotate_vertipaq(query_executor, model: Dict[str, Any]):
        """Copy of *model* with ``Vertipaq_Cardinality`` / ``Vertipaq_RowCount``
        annotations from the storage DMVs (I-30 idea 2: VertiPaq-aware BPA)."""
        try:
            from core.dax.vertipaq_storage_report import VertiPaqStorageReport

            report = VertiPaqStorageReport(query_executor).generate_report().to_dict()
        except Exception as e:  # noqa: BLE001
            return (
                model,
                f"include_vertipaq: storage DMVs unavailable ({e}); annotation rules stay vacuous",
            )
        tables = {t.get("table_name"): t for t in report.get("tables", []) or []}
        annotated = copy.copy(model)
        annotated["tables"] = []
        n_cols = 0
        for t in model.get("tables") or []:
            t2 = copy.copy(t)
            info = tables.get(t.get("name"))
            if info:
                t2["annotations"] = list(t.get("annotations") or []) + [
                    {"name": "Vertipaq_RowCount", "value": str(int(info.get("row_count") or 0))}
                ]
                cards = {
                    c.get("column"): c.get("cardinality") for c in info.get("columns", []) or []
                }
                cols = []
                for c in t.get("columns") or []:
                    c2 = copy.copy(c)
                    if c.get("name") in cards and cards[c.get("name")] is not None:
                        c2["annotations"] = list(c.get("annotations") or []) + [
                            {
                                "name": "Vertipaq_Cardinality",
                                "value": str(int(cards[c.get("name")])),
                            }
                        ]
                        n_cols += 1
                    cols.append(c2)
                t2["columns"] = cols
            annotated["tables"].append(t2)
        return annotated, f"include_vertipaq: annotated {len(tables)} tables / {n_cols} columns"

    @staticmethod
    def _issue_count(name: str, res: Dict[str, Any]) -> int:
        if name == "integrity":
            return int(res.get("total_issues", 0) or 0)
        if name in ("topology", "model_settings"):
            return int(res.get("finding_count", 0) or 0)
        if name == "bpa":
            return int(res.get("violation_count", 0) or 0)
        if name == "m_practices":
            val = res.get("total_issues")
            return int(val if val is not None else res.get("count", 0) or 0)
        if name == "performance":
            issues = res.get("issues", [])
            if isinstance(issues, list):
                return len(
                    [
                        i
                        for i in issues
                        if isinstance(i, dict) and i.get("severity") in ("high", "critical")
                    ]
                )
        return 0

    @staticmethod
    def _flatten_issues(analyses: Dict[str, Any]) -> List[Dict[str, Any]]:
        """One ranked list over every analysis (I-14) — the enrichment input."""
        out: List[Dict[str, Any]] = []
        bpa = analyses.get("bpa") or {}
        for v in bpa.get("violations", []) or []:
            out.append(
                {
                    "source": "bpa",
                    "rule_id": v.get("rule_id"),
                    "type": v.get("category"),
                    "severity": _BPA_SEVERITY.get(str(v.get("severity")).upper(), "low"),
                    "object_type": v.get("object_type"),
                    "object_name": v.get("object_name"),
                    "table": v.get("table_name"),
                    "description": v.get("description"),
                    "recommendation": v.get("fix_expression"),
                }
            )
        for section in ("topology", "model_settings"):
            for f in (analyses.get(section) or {}).get("findings", []) or []:
                out.append(
                    {
                        "source": section,
                        "rule_id": f.get("finding_id"),
                        "type": section,
                        "severity": str(f.get("severity") or "low").lower(),
                        "object_type": f.get("object_type"),
                        "object_name": f.get("object_name"),
                        "description": f.get("description"),
                        "recommendation": f.get("recommendation"),
                    }
                )
        for i in (analyses.get("integrity") or {}).get("issues", []) or []:
            if isinstance(i, dict):
                item = dict(i)
                item.setdefault("source", "integrity")
                item["severity"] = str(i.get("severity") or "medium").lower()
                out.append(item)
        for i in (analyses.get("m_practices") or {}).get("issues", []) or []:
            out.append(
                {
                    "source": "m_practices",
                    "rule_id": i.get("rule"),
                    "type": "power_query",
                    "severity": _M_SEVERITY.get(str(i.get("severity")).lower(), "low"),
                    "object_type": i.get("source_kind", "expression"),
                    "object_name": i.get("name"),
                    "table": i.get("table"),
                    "description": i.get("description"),
                }
            )
        out.sort(key=lambda x: _SEVERITY_RANK.get(x.get("severity"), 5))
        return out

    def _get_recommendation(self, total_issues: int, analyses: Dict[str, Any]) -> str:
        """Generate recommendation based on analysis results."""
        if total_issues == 0:
            return "Model is in good shape. No critical issues found."

        critical_count = 0
        high_count = 0

        integrity = analyses.get("integrity")
        if isinstance(integrity, dict):
            summary = integrity.get("summary", {})
            if isinstance(summary, dict):
                critical_count += summary.get("critical_issues", 0)
                high_count += summary.get("high_issues", 0)

        bpa = analyses.get("bpa")
        if isinstance(bpa, dict):
            by_sev = (bpa.get("summary") or {}).get("by_severity") or {}
            if by_sev:
                critical_count += int(by_sev.get("ERROR", 0) or 0)
                high_count += int(by_sev.get("WARNING", 0) or 0)
            else:
                for v in bpa.get("violations", []) or []:
                    sev = str(v.get("severity", "")).upper() if isinstance(v, dict) else ""
                    if sev in ("ERROR", "3"):
                        critical_count += 1
                    elif sev in ("WARNING", "2"):
                        high_count += 1

        for section in ("topology", "model_settings"):
            for f in (analyses.get(section) or {}).get("findings", []) or []:
                sev = str(f.get("severity") or "").lower()
                if sev == "critical":
                    critical_count += 1
                elif sev == "high":
                    high_count += 1

        if critical_count > 0:
            return (
                f"CRITICAL: Found {critical_count} critical issues. "
                "Address immediately before deployment."
            )
        if high_count > 0:
            return (
                f"WARNING: Found {high_count} high-priority issues. "
                "Review and fix before production use."
            )
        return (
            f"Found {total_issues} issues (low/medium severity). "
            "Consider addressing for optimization."
        )

    def _infer_table_type(self, table_name: str) -> str:
        """
        Infer table type from naming convention.

        Common prefixes in Power BI models:
        - d_: dimension tables
        - f_: fact tables
        - m_: measure tables
        - s_: support/slicer tables
        - c_: calculation groups
        - r_: RLS (Row-Level Security) tables
        - sfp_/dfp_: field parameters
        - dyn_: dynamic tables

        Args:
            table_name: Name of the table

        Returns:
            Table type category as string
        """
        name_lower = table_name.lower()

        if name_lower.startswith("d_"):
            return "dimension"
        elif name_lower.startswith("f_"):
            return "fact"
        elif name_lower.startswith("m_"):
            return "measure"
        elif name_lower.startswith("s_"):
            return "support"
        elif name_lower.startswith("c_"):
            return "calculation_group"
        elif name_lower.startswith("r_"):
            return "rls"
        elif name_lower.startswith("sfp_") or name_lower.startswith("dfp_"):
            return "field_parameter"
        elif name_lower.startswith("dyn_"):
            return "dynamic"
        else:
            return "other"

    def _query_and_map(
        self,
        connection_state,
        query: str,
        field_map: dict,
        response_key: str,
        query_kwargs: dict = None,
        filter_fn=None,
        post_process=None,
        operation: str = "List",
        message_fn=None,
    ) -> Dict[str, Any]:
        """Execute an info query, map rows via field_map, return standard response.

        field_map: output_key -> (row_key, default). Use post_process for richer per-row work.
        """
        from core.validation.error_handler import ErrorHandler

        executor = connection_state.query_executor
        if not executor:
            return ErrorHandler.handle_manager_unavailable("query_executor")
        try:
            result = executor.execute_info_query(query, **(query_kwargs or {}))
            if not result.get("success"):
                return result
            rows = result.get("rows", [])
            if filter_fn:
                rows = [r for r in rows if filter_fn(r)]
            data = [{k: row.get(v[0], v[1]) for k, v in field_map.items()} for row in rows]
            if post_process:
                data = post_process(data, rows)
            msg = message_fn(data) if message_fn else f"Found {len(data)} {response_key}"
            return {"success": True, "operation": operation, "message": msg, "data": data}
        except Exception as e:
            logger.error(f"{query} query failed: {e}")
            return {"success": False, "error": f"{query} query failed: {str(e)}"}

    def _run_topology_analysis(
        self, query_executor, connection_state=None, tmsl_model: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Run the star-schema / topology validator over the live model shape.

        Preferred input is the TMSL snapshot (relationships with TOM defaults,
        calculation-group / field-parameter / what-if flags). Without one, the
        INFO.* fallback reads ``TABLES`` + ``RELATIONSHIPS`` and normalises the
        enums — the old code read ``rel_result['relationships']`` from a result
        whose key is ``data`` and flagged every table as disconnected (I-06).
        """
        from core.analysis.star_schema_validator import StarSchemaValidator

        try:
            source = "tmsl"
            if tmsl_model is not None:
                tables = infn.tables_from_tmsl(tmsl_model)
                relationships = infn.relationships_from_tmsl(tmsl_model)
            else:
                source = "info"
                tables = []
                relationships = []
                if query_executor is not None:
                    tbl = query_executor.execute_info_query("TABLES")
                    for row in (tbl.get("rows", []) if tbl.get("success") else []) or []:
                        name = infn.pick(row, "Name", "TableName")
                        if not name:
                            continue
                        tables.append(
                            {
                                "name": name,
                                "dataCategory": infn.pick(row, "DataCategory"),
                                "isHidden": infn.to_bool(infn.pick(row, "IsHidden", default=False)),
                                "isPrivate": infn.to_bool(
                                    infn.pick(row, "IsPrivate", default=False)
                                ),
                                "isCalculationGroup": infn.pick(row, "CalculationGroupID")
                                is not None
                                or infn.to_bool(
                                    infn.pick(row, "IsCalculationGroup", default=False)
                                ),
                            }
                        )
                    rels = query_executor.execute_info_query("RELATIONSHIPS")
                    relationships = [
                        infn.normalize_relationship(r)
                        for r in (rels.get("rows", []) if rels.get("success") else []) or []
                    ]

            dax_read = infn.dax_referenced_tables(tmsl_model) if tmsl_model is not None else []
            findings = StarSchemaValidator().validate(
                {"tables": tables, "relationships": relationships, "daxReferencedTables": dax_read}
            )
            return {
                "success": True,
                "source": source,
                "finding_count": len(findings),
                "findings": [
                    {
                        "finding_id": f.finding_id,
                        "severity": f.severity,
                        "object_type": f.object_type,
                        "object_name": f.object_name,
                        "description": f.description,
                        "recommendation": f.recommendation,
                    }
                    for f in findings
                ],
            }
        except Exception as e:
            logger.error(f"Topology analysis failed: {e}")
            return {"success": False, "error": f"Topology analysis failed: {str(e)}"}

    @classmethod
    def _settings_from_tmsl(
        cls, tmsl_model: Optional[Dict[str, Any]], query_executor
    ) -> Optional[Dict[str, Any]]:
        """Model settings from the run's TMSL snapshot (I-29: no extra AMO session).

        TOM's JsonSerializer omits properties at their default, so an absent
        ``valueFilterBehavior`` on a model at CL >= 1600 is the default
        ``Automatic``. The compatibility level is a DATABASE property (not in the
        serialised model) and comes from DBSCHEMA_CATALOGS; when it cannot be
        read, returns None so the caller falls back to ModelPropertiesManager.
        """
        if not isinstance(tmsl_model, dict) or query_executor is None:
            return None
        level = cls._compatibility_level(query_executor)
        if level <= 0:
            return None
        supported = level >= 1600
        raw = tmsl_model.get("valueFilterBehavior")
        behavior = None
        if supported:
            behavior = str(raw) if raw not in (None, "") else "Automatic"
            behavior = behavior[:1].upper() + behavior[1:]
        return {
            "success": True,
            "value_filter_behavior": behavior,
            "value_filter_behavior_supported": supported,
            "compatibility_level": level,
            "source": "tmsl",
        }

    def _run_model_settings_analysis(
        self, properties_manager=None, *, tmsl_model=None, query_executor=None
    ) -> Dict[str, Any]:
        """Run the model-settings validator over the live model's properties.

        Prefers the run's TMSL snapshot (``tmsl_model`` + ``query_executor`` for
        the compatibility level); otherwise reads the properties via
        ModelPropertiesManager (the manager behind ``02_Model_Operations
        model/get_properties``). The manager is injectable for tests.
        """
        from core.analysis.model_settings_validator import ModelSettingsValidator

        try:
            props = None
            if properties_manager is None:
                props = self._settings_from_tmsl(tmsl_model, query_executor)
            if props is None:
                if properties_manager is None:
                    from core.operations.model_properties_manager import ModelPropertiesManager

                    properties_manager = ModelPropertiesManager()
                props = properties_manager.get_properties() or {}
            if not props.get("success"):
                return {
                    "success": False,
                    "error": props.get("error") or "Could not read model properties",
                    "error_type": props.get("error_type", "properties_unavailable"),
                }

            findings = ModelSettingsValidator().validate(props)
            return {
                "success": True,
                "finding_count": len(findings),
                "properties": {
                    "value_filter_behavior": props.get("value_filter_behavior"),
                    "compatibility_level": props.get("compatibility_level"),
                    "source": props.get("source", "tom"),
                },
                "findings": [
                    {
                        "finding_id": f.finding_id,
                        "severity": f.severity,
                        "object_type": f.object_type,
                        "object_name": f.object_name,
                        "description": f.description,
                        "recommendation": f.recommendation,
                    }
                    for f in findings
                ],
            }
        except Exception as e:
            logger.error(f"Model settings analysis failed: {e}")
            return {"success": False, "error": f"Model settings analysis failed: {str(e)}"}

    # ── 06 simple ────────────────────────────────────────────────────────

    @staticmethod
    def _compatibility_level(executor) -> int:
        """Database compatibility level of the connected model, 0 when unknown.

        Shares ``read_compatibility_level`` (DBSCHEMA_CATALOGS, then INFO.MODEL): with several
        catalogs the first row is not necessarily this model's, so only an unambiguous answer
        counts."""
        from core.dax.udf_support import read_compatibility_level

        return read_compatibility_level(executor) or 0
