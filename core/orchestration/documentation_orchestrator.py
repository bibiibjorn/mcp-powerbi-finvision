"""Documentation generation orchestration (08_Documentation_Word).

2026-09-22 review:

- I-09: ``output_path`` ending in ``.docx`` is the exact FILE to write (it used
  to become a directory named ``X.docx``). Every document gets its snapshot
  next to it as ``<stem>.snapshot.json``; ``update`` finds that snapshot from
  the ``.docx`` it is given (or an explicit ``snapshot_path``), so the change
  log is real, and by default rewrites the input document in place.
- I-17: generation errors (locked file, invalid characters) come back as a
  structured error instead of an exception.
- I-25: the lightweight fallback's no-op branch is gone.
- I-27: the unreachable entry points (``generate_docs_safe``,
  ``generate_documentation_profiled``, ``auto_document``, the interactive
  explorer exports — one of which imported a function that no longer existed)
  were deleted.
"""

import logging
from typing import Any, Dict, List, Optional

from .base_orchestrator import BaseOrchestrator

logger = logging.getLogger(__name__)


class DocumentationOrchestrator(BaseOrchestrator):
    """Handles documentation generation workflows."""

    def validate_best_practices(self, connection_state) -> Dict[str, Any]:
        """Lightweight checks used only when the BPA analyzer is unavailable."""
        from core.validation.error_handler import ErrorHandler

        if not connection_state.is_connected():
            return ErrorHandler.handle_not_connected()
        executor = connection_state.query_executor
        validator = getattr(connection_state, "model_validator", None)
        if not executor:
            return ErrorHandler.handle_manager_unavailable("query_executor")
        issues: List[Dict[str, Any]] = []
        if validator:
            integrity = validator.validate_model_integrity()
            if integrity.get("success"):
                issues.extend(integrity.get("issues", []))
        tables = executor.execute_info_query("TABLES")
        if tables.get("success"):
            for t in tables.get("rows", []):
                name = t.get("Name") or ""
                if name != name.strip():
                    issues.append(
                        {
                            "type": "naming",
                            "severity": "low",
                            "object": f"Table:{name}",
                            "description": "Leading/trailing spaces in table name",
                        }
                    )
        measures = executor.execute_info_query("MEASURES", exclude_columns=["Expression"])
        if measures.get("success"):
            for m in measures.get("rows", []):
                name = m.get("Name") or ""
                if len(name.strip()) < 2:
                    issues.append(
                        {
                            "type": "naming",
                            "severity": "low",
                            "object": f"Measure:{name}",
                            "description": "Very short measure name",
                        }
                    )
        return {"success": True, "issues": issues, "total_issues": len(issues)}

    def _collect(self, connection_state, include_hidden: bool, dependency_depth: int):
        from core.documentation.data_collector import collect_model_documentation

        lightweight = None
        if not getattr(connection_state, "bpa_analyzer", None):
            try:
                lightweight = self.validate_best_practices(connection_state)
            except Exception:  # noqa: BLE001
                lightweight = None
        return collect_model_documentation(
            connection_state,
            include_hidden=include_hidden,
            dependency_depth=dependency_depth,
            lightweight_best_practices=lightweight if isinstance(lightweight, dict) else None,
        )

    def generate_model_documentation_word(
        self,
        connection_state,
        output_dir: Optional[str] = None,
        include_hidden: bool = True,
        dependency_depth: int = 1,
    ) -> Dict[str, Any]:
        """Generate Word documentation. *output_dir* may be a directory or a ``.docx`` path."""
        from core.documentation.snapshot_manager import save_snapshot
        from core.documentation.utils import resolve_doc_target, snapshot_path_for
        from core.documentation.word_generator import render_word_report
        from core.validation.error_handler import ErrorHandler

        if not connection_state.is_connected():
            return ErrorHandler.handle_not_connected()

        context = self._collect(connection_state, include_hidden, dependency_depth)
        if not context.get("success"):
            return context

        directory, doc_file = resolve_doc_target(output_dir)
        try:
            doc_result = render_word_report(
                context,
                output_dir=directory,
                output_file=doc_file,
                change_summary=None,
                mode="full",
            )
        except Exception as e:  # noqa: BLE001 — never raise out of the tool
            logger.error(f"Word generation failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Word generation failed: {e}",
                "error_type": "render_error",
            }
        if not doc_result.get("success"):
            return doc_result

        doc_path = doc_result["doc_path"]
        snapshot_result = save_snapshot(context, path=snapshot_path_for(doc_path))
        response: Dict[str, Any] = {
            "success": True,
            "doc_path": doc_path,
            "snapshot_path": snapshot_result.get("snapshot_path"),
            "counts": (context.get("summary") or {}).get("counts"),
            "best_practices": _bp_brief(context.get("best_practices")),
        }
        if context.get("notes"):
            response["notes"] = context.get("notes")
        return response

    def update_model_documentation_word(
        self,
        connection_state,
        output_dir: Optional[str] = None,
        snapshot_path: Optional[str] = None,
        include_hidden: bool = True,
        dependency_depth: int = 1,
        input_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Regenerate an existing document with a change log against its snapshot.

        *input_path* is the existing ``.docx`` (or its ``.snapshot.json``);
        *snapshot_path* overrides where the previous snapshot is read from.
        Without *output_dir* the input ``.docx`` is rewritten in place.
        """
        from core.documentation.snapshot_manager import (
            compute_diff,
            load_snapshot,
            save_snapshot,
            snapshot_from_context,
        )
        from core.documentation.utils import resolve_doc_target, snapshot_path_for
        from core.documentation.word_generator import render_word_report
        from core.validation.error_handler import ErrorHandler

        if not connection_state.is_connected():
            return ErrorHandler.handle_not_connected()
        qe = getattr(connection_state, "query_executor", None)
        if not qe:
            return ErrorHandler.handle_manager_unavailable("query_executor")

        context = self._collect(connection_state, include_hidden, dependency_depth)
        if not context.get("success"):
            return context

        previous = load_snapshot(snapshot_path or input_path, None, context.get("database_name"))
        diff = compute_diff(previous, snapshot_from_context(context))

        in_place = False
        target = output_dir
        if not target and input_path and str(input_path).lower().endswith(".docx"):
            target = input_path
            in_place = True
        directory, doc_file = resolve_doc_target(target)
        try:
            doc_result = render_word_report(
                context,
                output_dir=directory,
                output_file=doc_file,
                change_summary=diff,
                mode="update",
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"Word update failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Word update failed: {e}",
                "error_type": "render_error",
            }
        if not doc_result.get("success"):
            return doc_result

        doc_path = doc_result["doc_path"]
        snapshot_result = save_snapshot(context, path=snapshot_path_for(doc_path))
        response: Dict[str, Any] = {
            "success": True,
            "doc_path": doc_path,
            "in_place": in_place,
            "snapshot_path": snapshot_result.get("snapshot_path"),
            "previous_snapshot": (previous or {}).get("_snapshot_path"),
            "change_summary": diff,
            "best_practices": _bp_brief(context.get("best_practices")),
        }
        if previous is None:
            response["message"] = (
                "No previous snapshot found for the input document; this run is the new "
                "baseline (the next update will report changes)."
            )
        elif not diff.get("changes_detected"):
            response["message"] = "No structural changes detected; documentation refreshed."
        if context.get("notes"):
            response["notes"] = context.get("notes")
        return response

    def generate_word_documentation(
        self,
        connection_state,
        output_dir: Optional[str] = None,
        include_hidden: bool = True,
        dependency_depth: int = 1,
    ) -> Dict[str, Any]:
        """Alias for :meth:`generate_model_documentation_word`."""
        return self.generate_model_documentation_word(
            connection_state,
            output_dir=output_dir,
            include_hidden=include_hidden,
            dependency_depth=dependency_depth,
        )

    def update_word_documentation(
        self,
        connection_state,
        snapshot_path: Optional[str] = None,
        output_dir: Optional[str] = None,
        include_hidden: bool = True,
        dependency_depth: int = 1,
        input_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Alias for :meth:`update_model_documentation_word`.

        Backward compatible: the old positional ``snapshot_path`` WAS the input
        document; it is treated as ``input_path`` when that is not given.
        """
        if (
            input_path is None
            and snapshot_path
            and not str(snapshot_path).lower().endswith(".json")
        ):
            input_path, snapshot_path = snapshot_path, None
        return self.update_model_documentation_word(
            connection_state,
            output_dir=output_dir,
            snapshot_path=snapshot_path,
            include_hidden=include_hidden,
            dependency_depth=dependency_depth,
            input_path=input_path,
        )


def _bp_brief(bp: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Best-practice summary for the tool response (the full list is in the doc)."""
    if not isinstance(bp, dict):
        return bp
    out = {k: v for k, v in bp.items() if k != "violations"}
    out["violations_in_document"] = min(len(bp.get("violations") or []), 200)
    return out
