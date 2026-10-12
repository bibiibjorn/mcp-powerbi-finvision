"""Annotation CRUD for any NamedMetadataObject in the TOM model.

Annotations are key/value string pairs attached to TOM objects — used for
field parameters, date table markers, PBI_* system tags, TE-compatible tagging,
and custom tooling metadata. This manager provides list/get/set/delete on:

  - model
  - table (+ its subobjects)
  - column  (target_type='column', requires table_name + column_name)
  - measure (target_type='measure', requires table_name + measure_name)
  - partition (target_type='partition', requires table_name + partition_name)
  - relationship (target_type='relationship', requires relationship_name)
  - role (target_type='role', requires role_name)
  - named_expression (target_type='named_expression', requires expression_name)
  - hierarchy (target_type='hierarchy', requires table_name + hierarchy_name)
  - calculation_item (requires group_name + item_name)
  - perspective (requires perspective_name)
  - culture (requires culture_name)
"""

import logging
from typing import Any, Dict

from core.operations.amo_helpers import get_server_db_model, safe_disconnect

logger = logging.getLogger(__name__)

from core.operations.write_lane import commit as _lane_commit  # noqa: E402

try:
    from core.infrastructure.dll_paths import load_amo_assemblies

    if load_amo_assemblies():
        from Microsoft.AnalysisServices.Tabular import Annotation
    else:
        Annotation = None
except Exception as e:
    logger.debug(f"AMO not loaded: {e}")
    Annotation = None


class AnnotationManager:
    """Generic annotation CRUD across TOM objects."""

    def __init__(self):
        self._connection_state = None

    @property
    def connection_state(self):
        if self._connection_state is None:
            from core.infrastructure.connection_state import connection_state

            self._connection_state = connection_state
        return self._connection_state

    def _resolve_target(self, model, target_type: str, args: Dict[str, Any]):
        """Resolve the target TOM object. Returns (obj, error_message)."""
        tt = (target_type or "").strip().lower()
        if tt == "model":
            return model, None
        if tt == "table":
            table_name = args.get("table_name")
            if not table_name:
                return None, "table_name required for target_type=table"
            t = model.Tables.Find(table_name)
            return (t, None) if t is not None else (None, f"Table '{table_name}' not found")
        if tt == "column":
            table_name, column_name = args.get("table_name"), args.get("column_name")
            if not table_name or not column_name:
                return None, "table_name and column_name required for target_type=column"
            t = model.Tables.Find(table_name)
            if t is None:
                return None, f"Table '{table_name}' not found"
            c = t.Columns.Find(column_name)
            return (
                (c, None)
                if c is not None
                else (None, f"Column '{column_name}' not found on '{table_name}'")
            )
        if tt == "measure":
            table_name, measure_name = args.get("table_name"), args.get("measure_name")
            if not measure_name:
                return None, "measure_name required for target_type=measure"
            # Walk tables if table_name is omitted
            if table_name:
                t = model.Tables.Find(table_name)
                if t is None:
                    return None, f"Table '{table_name}' not found"
                m = t.Measures.Find(measure_name)
                return (
                    (m, None)
                    if m is not None
                    else (None, f"Measure '{measure_name}' not found on '{table_name}'")
                )
            for t in model.Tables:
                m = t.Measures.Find(measure_name)
                if m is not None:
                    return m, None
            return None, f"Measure '{measure_name}' not found in any table"
        if tt == "partition":
            table_name, partition_name = args.get("table_name"), args.get("partition_name")
            if not table_name or not partition_name:
                return None, "table_name and partition_name required for target_type=partition"
            t = model.Tables.Find(table_name)
            if t is None:
                return None, f"Table '{table_name}' not found"
            p = t.Partitions.Find(partition_name)
            return (
                (p, None)
                if p is not None
                else (None, f"Partition '{partition_name}' not found on '{table_name}'")
            )
        if tt == "relationship":
            rel_name = args.get("relationship_name") or args.get("name")
            if not rel_name:
                return None, "relationship_name required for target_type=relationship"
            r = model.Relationships.Find(rel_name)
            return (r, None) if r is not None else (None, f"Relationship '{rel_name}' not found")
        if tt == "role":
            role_name = args.get("role_name")
            if not role_name:
                return None, "role_name required for target_type=role"
            r = model.Roles.Find(role_name)
            return (r, None) if r is not None else (None, f"Role '{role_name}' not found")
        if tt == "named_expression":
            expr_name = args.get("expression_name")
            if not expr_name:
                return None, "expression_name required for target_type=named_expression"
            e = model.Expressions.Find(expr_name)
            return (
                (e, None) if e is not None else (None, f"Named expression '{expr_name}' not found")
            )
        if tt == "hierarchy":
            table_name, hier_name = args.get("table_name"), args.get("hierarchy_name")
            if not table_name or not hier_name:
                return None, "table_name and hierarchy_name required for target_type=hierarchy"
            t = model.Tables.Find(table_name)
            if t is None:
                return None, f"Table '{table_name}' not found"
            h = t.Hierarchies.Find(hier_name)
            return (
                (h, None)
                if h is not None
                else (None, f"Hierarchy '{hier_name}' not found" f" on '{table_name}'")
            )
        # C-42: calculation items, perspectives and cultures carry annotations too.
        if tt in ("calculation_item", "calc_item"):
            group = args.get("group_name") or args.get("table_name")
            item_name = args.get("item_name")
            if not group or not item_name:
                return None, "group_name and item_name required for target_type=calculation_item"
            t = model.Tables.Find(group)
            cg = getattr(t, "CalculationGroup", None) if t is not None else None
            if cg is None:
                return None, f"Calculation group '{group}' not found"
            it = cg.CalculationItems.Find(item_name)
            return (
                (it, None)
                if it is not None
                else (
                    None,
                    f"Calculation item '{item_name}' not found in '{group}'",
                )
            )
        if tt == "perspective":
            name = args.get("perspective_name")
            if not name:
                return None, "perspective_name required for target_type=perspective"
            p = model.Perspectives.Find(name)
            return (p, None) if p is not None else (None, f"Perspective '{name}' not found")
        if tt == "culture":
            name = args.get("culture_name")
            if not name:
                return None, "culture_name required for target_type=culture"
            c = model.Cultures.Find(name)
            return (c, None) if c is not None else (None, f"Culture '{name}' not found")
        if tt == "function":
            return None, (
                "target_type=function is not supported: UDFs are not exposed through AMO on "
                "these models (they are written via TMSL) — edit the function's TMDL instead."
            )
        return None, (
            f"Unsupported target_type '{target_type}'. Valid: model, table, column, measure, "
            "partition, relationship, role, named_expression, hierarchy, calculation_item, "
            "perspective, culture"
        )

    def list_annotations(self, target_type: str, args: Dict[str, Any]) -> Dict[str, Any]:
        server, db, model, err = get_server_db_model(self.connection_state)
        if err:
            return {"success": False, "error": err}
        try:
            obj, err = self._resolve_target(model, target_type, args)
            if err:
                return {"success": False, "error": err}
            annos = []
            if hasattr(obj, "Annotations"):
                for a in obj.Annotations:
                    annos.append({"name": a.Name, "value": a.Value})
            return {
                "success": True,
                "target_type": target_type,
                "annotations": annos,
                "count": len(annos),
            }
        except Exception as e:
            return {"success": False, "error": f"list_annotations failed: {e}"}
        finally:
            safe_disconnect(server)

    def get_annotation(
        self, target_type: str, annotation_name: str, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        server, db, model, err = get_server_db_model(self.connection_state)
        if err:
            return {"success": False, "error": err}
        try:
            obj, err = self._resolve_target(model, target_type, args)
            if err:
                return {"success": False, "error": err}
            if not hasattr(obj, "Annotations"):
                return {"success": False, "error": "Target does not support annotations"}
            a = obj.Annotations.Find(annotation_name)
            if a is None:
                return {"success": False, "error": f"Annotation '{annotation_name}' not found"}
            return {
                "success": True,
                "annotation": {"name": a.Name, "value": a.Value},
            }
        except Exception as e:
            return {"success": False, "error": f"get_annotation failed: {e}"}
        finally:
            safe_disconnect(server)

    def set_annotation(
        self,
        target_type: str,
        annotation_name: str,
        annotation_value: str,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create or update an annotation. Value must be a string."""
        if Annotation is None:
            return {"success": False, "error": "AMO not available"}
        if not annotation_name:
            return {"success": False, "error": "annotation_name is required"}
        if annotation_value is None:
            return {
                "success": False,
                "error": "annotation_value is required (use delete to remove)",
            }
        server, db, model, err = get_server_db_model(self.connection_state)
        if err:
            return {"success": False, "error": err}
        try:
            obj, err = self._resolve_target(model, target_type, args)
            if err:
                return {"success": False, "error": err}
            if not hasattr(obj, "Annotations"):
                return {"success": False, "error": "Target does not support annotations"}
            existing = obj.Annotations.Find(annotation_name)
            if existing is not None:
                existing.Value = str(annotation_value)
                action = "updated"
            else:
                a = Annotation()
                a.Name = annotation_name
                a.Value = str(annotation_value)
                obj.Annotations.Add(a)
                action = "created"
            _lane_commit(model, label="annotation")
            return {
                "success": True,
                "message": f"{action.capitalize()} annotation '{annotation_name}'",
                "action": action,
                "target_type": target_type,
                "annotation": {"name": annotation_name, "value": str(annotation_value)},
            }
        except Exception as e:
            return {"success": False, "error": f"set_annotation failed: {e}"}
        finally:
            safe_disconnect(server)

    def delete_annotation(
        self,
        target_type: str,
        annotation_name: str,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        server, db, model, err = get_server_db_model(self.connection_state)
        if err:
            return {"success": False, "error": err}
        try:
            obj, err = self._resolve_target(model, target_type, args)
            if err:
                return {"success": False, "error": err}
            if not hasattr(obj, "Annotations"):
                return {"success": False, "error": "Target does not support annotations"}
            existing = obj.Annotations.Find(annotation_name)
            if existing is None:
                return {"success": False, "error": f"Annotation '{annotation_name}' not found"}
            # C-42: PBI_* annotations are Desktop's own bookkeeping (PBI_Id,
            # PBI_ResultType, PBI_FormatHint ...); deleting one can break the
            # field-parameter icon or report bindings. Require force=true.
            if str(annotation_name).upper().startswith("PBI_") and args.get("force") is not True:
                return {
                    "success": False,
                    "error": (
                        f"'{annotation_name}' is a Power BI Desktop internal annotation; "
                        "pass force=true to delete it anyway."
                    ),
                    "error_type": "protected_annotation",
                }
            obj.Annotations.Remove(existing)
            _lane_commit(model, label="annotation")
            return {
                "success": True,
                "message": f"Deleted annotation '{annotation_name}'",
            }
        except Exception as e:
            return {"success": False, "error": f"delete_annotation failed: {e}"}
        finally:
            safe_disconnect(server)
