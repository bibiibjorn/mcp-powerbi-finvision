"""Field Parameter Manager for MCP-PowerBi-Finvision.

Creates and manages "field parameter" calculated tables — the special PBI
construct used as slicer-driven measure/column pickers (the icon shown on the
value column in PBI Desktop's Fields pane).

A field parameter table is a calculated table whose source expression is the
literal table-constructor syntax  `{ ("Label", NAMEOF(<ref>), order, ...), ... }`.
Its columns must be:

* ``[Value1]`` → ``<table>``, ``[Value2]`` → ``<table> Fields``,
  ``[Value3]`` → ``<table> Order`` (explicit, ``IsNameInferred = false``)
* ``Fields`` and ``Order`` hidden; ``<table>`` and ``Fields`` sorted by ``Order``
* ``<table>`` column's ``RelatedColumnDetails.GroupByColumns`` → ``Fields``
* ``Fields`` column's extended property ``ParameterMetadata`` =
  ``{"version":3,"kind":2}`` — the marker PBI Desktop uses for the FP icon
* lineage tags + ``SummarizationSetBy`` annotations + table ``PBI_Id``.

Review C-07: the whole table is built in ONE SaveChanges with explicit
``CalculatedTableColumn`` objects (mirroring Desktop's own FP TMDL) plus a
Calculate refresh request, so a failure can no longer leave a half-shaped
table; any failure that did leave a table behind is rolled back on a FRESH
connection. C-09: :meth:`repair_field_parameter` applies the complete shape
idempotently to an existing (e.g. half-built) FP. C-08: row edits use a real
tuple tokenizer and refuse to write when any row could not be parsed.
"""

from __future__ import annotations

import logging
import re as _re_fp
import uuid
from typing import Any, Dict, List, Optional, Tuple

from core.operations.write_lane import commit, safe_undo
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)


PARAMETER_METADATA = '{"version":3,"kind":2}'


# --------------------------------------------------------------------- parsing


def _split_top_level(text: str, open_ch: str, close_ch: str) -> Optional[List[str]]:
    """Top-level comma-split of the content between the first *open_ch* and its
    matching *close_ch*. Strings (``"…"``/``""``), quoted names (``'…'``/``''``)
    and bracketed names (``[…]``/``]]``) are opaque. None when unbalanced."""
    start = text.find(open_ch)
    if start < 0:
        return None
    parts: List[str] = []
    depth = 0
    buf: List[str] = []
    i = start + 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n:
                if text[j] == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            buf.append(text[i : j + 1])
            i = j + 1
            continue
        if c in ("'", "["):
            close = "'" if c == "'" else "]"
            j = i + 1
            while j < n:
                if text[j] == close:
                    if j + 1 < n and text[j + 1] == close:
                        j += 2
                        continue
                    break
                j += 1
            buf.append(text[i : j + 1])
            i = j + 1
            continue
        if c in "({":
            depth += 1
        elif c in ")}":
            if depth == 0 and c == close_ch:
                parts.append("".join(buf).strip())
                return parts if any(p for p in parts) else []
            depth -= 1
            if depth < 0:
                return None
        elif c == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    return None


def _unquote_label(tok: str) -> Optional[str]:
    tok = tok.strip()
    if len(tok) >= 2 and tok.startswith('"') and tok.endswith('"'):
        return tok[1:-1].replace('""', '"')
    return None


_NAMEOF_RE = _re_fp.compile(r"^NAMEOF\s*\(", _re_fp.IGNORECASE)


def parse_fp_rows(expression: str) -> Tuple[List[Dict[str, Any]], int]:
    """``(rows, tuple_count)`` for a ``{ (…), (…) }`` field-parameter body.

    Each row: ``{label, nameof, order, extras}`` where ``nameof`` and every
    extra element are the verbatim source text. A tuple that is not
    ``("label", NAMEOF(…), <int>, …)`` counts in ``tuple_count`` but not in
    ``rows`` — callers must refuse to rewrite when the two differ (C-08).
    """
    tuples = _split_top_level(expression or "", "{", "}")
    if tuples is None:
        return [], -1
    rows: List[Dict[str, Any]] = []
    count = 0
    for raw in tuples:
        if not raw:
            continue
        count += 1
        elems = _split_top_level(raw, "(", ")")
        if not elems or len(elems) < 3:
            continue
        label = _unquote_label(elems[0])
        nameof = elems[1].strip()
        try:
            order = int(elems[2].strip())
        except ValueError:
            continue
        if label is None or not _NAMEOF_RE.match(nameof):
            continue
        rows.append({"label": label, "nameof": nameof, "order": order, "extras": elems[3:]})
    return rows, count


def _serialize_fp_rows(rows: List[Dict[str, Any]]) -> str:
    parts = []
    for r in rows:
        label = str(r["label"]).replace('"', '""')
        extras = r.get("extras") or []
        if isinstance(extras, str):  # legacy shape: ", x, y" suffix
            tail = extras
        else:
            tail = "".join(f", {e}" for e in extras)
        parts.append(f'    ("{label}", {r["nameof"]}, {r["order"]}{tail})')
    return "{\n" + ",\n".join(parts) + "\n}"


AMO_AVAILABLE = False

try:
    from core.infrastructure.dll_paths import load_amo_assemblies

    load_amo_assemblies()
    import Microsoft.AnalysisServices.Tabular  # type: ignore  # noqa: F401

    AMO_AVAILABLE = True
except Exception as e:  # pragma: no cover - CLR interop
    logger.warning(f"AMO not available for FieldParameterManager: {e}")
from core.operations.amo_mixins import AmoModelMixin  # noqa: E402


def _tom():
    import Microsoft.AnalysisServices.Tabular as TOM  # type: ignore

    return TOM


def _enum_none(enum_type):
    """C-15: pythonnet 3 exposes an enum member called ``None`` only via getattr."""
    return getattr(enum_type, "None")


def _find(coll, pred):
    for item in list(coll or []):
        try:
            if pred(item):
                return item
        except Exception:  # noqa: BLE001
            continue
    return None


class FieldParameterManager(AmoModelMixin):
    """Create field parameter tables via TOM."""

    def __init__(self, connection=None) -> None:
        self.connection = connection

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _valid_identifier(s: Optional[str]) -> bool:
        return bool(s) and 0 < len(str(s).strip()) <= 128 and "\0" not in str(s)

    def _connection_error_msg(self) -> str:
        cause = getattr(self, "_last_amo_error", None) or "unknown cause"
        return f"Could not connect to model: {cause}"

    @staticmethod
    def _rollback_fp_table(model, table_name: str) -> bool:
        """Remove a field-parameter table and persist the removal.

        Returns True on a clean rollback (including nothing-to-remove), False
        if the rollback itself failed — the caller reports rollback_failed.
        """
        try:
            target = next((t for t in model.Tables if getattr(t, "Name", None) == table_name), None)
            if target is None:
                return True  # already clean
            model.Tables.Remove(target)
            commit(model, label="field_parameter/rollback")
            return True
        except Exception as e:  # noqa: BLE001 - rollback must never raise
            logger.error(f"Rollback of field-parameter table '{table_name}' failed: {e}")
            return False

    def _rollback_on_fresh_connection(self, table_name: str) -> Dict[str, Any]:
        """C-07: roll a leftover FP table back on a NEW session (the failed one
        may be desynced from the server after a client-side parse error)."""
        from core.operations import model_readback

        exists = model_readback.table_exists(table_name)
        if exists is False:
            return {"rolled_back": True, "note": "no table was left behind"}
        server, _db, model = self._get_server_db_model()
        if not model:
            return {"rolled_back": False, "error": self._connection_error_msg()}
        try:
            ok = self._rollback_fp_table(model, table_name)
            return {"rolled_back": ok, "fresh_connection": True}
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    # --------------------------------------------------------- pbi annotations

    @staticmethod
    def _set_annotation(obj, name: str, value: str, *, overwrite: bool = True) -> bool:
        """Set a TOM annotation; returns True when created. With
        ``overwrite=False`` an existing value is kept (C-09: PBI_Id is stable)."""
        try:
            existing = None
            try:
                existing = obj.Annotations[name]
            except Exception:
                existing = None
            if existing is not None:
                if overwrite:
                    existing.Value = value
                return False
            ann = _tom().Annotation()
            ann.Name = name
            ann.Value = value
            obj.Annotations.Add(ann)
            return True
        except Exception as e:  # pragma: no cover - CLR
            logger.warning(f"Could not set annotation {name}: {e}")
            return False

    @classmethod
    def _apply_pbi_annotations(cls, fp_table) -> List[str]:
        """The metadata PBI Desktop adds to FP tables/columns (FP icon needs it):
        table ``PBI_Id`` (kept when present) + LineageTag; per non-RowNumber
        column ``SummarizationSetBy = Automatic`` + LineageTag."""
        applied: List[str] = []
        if cls._set_annotation(fp_table, "PBI_Id", uuid.uuid4().hex, overwrite=False):
            applied.append("table PBI_Id")
        try:
            if not getattr(fp_table, "LineageTag", None):
                fp_table.LineageTag = str(uuid.uuid4())
                applied.append("table lineageTag")
        except Exception as e:  # pragma: no cover
            logger.warning(f"Could not set table LineageTag: {e}")
        for c in list(fp_table.Columns):
            sc = getattr(c, "SourceColumn", None) or ""
            if str(sc).startswith("RowNumber") or str(c.Name or "").startswith("RowNumber"):
                continue
            if cls._set_annotation(c, "SummarizationSetBy", "Automatic", overwrite=False):
                applied.append(f"{c.Name} SummarizationSetBy")
            try:
                if not getattr(c, "LineageTag", None):
                    c.LineageTag = str(uuid.uuid4())
                    applied.append(f"{c.Name} lineageTag")
            except Exception as e:  # pragma: no cover
                logger.warning(f"Could not set column LineageTag for {c.Name}: {e}")
        return applied

    # --------------------------------------------------------- expression build

    @staticmethod
    def _build_expression(fields: List[Dict[str, Any]]) -> str:
        """Build the `{ ("Label", NAMEOF(<ref>), <order>), ... }` literal."""
        rows: List[str] = []
        for i, f in enumerate(fields):
            label = str(f.get("label", "")).replace('"', '""')
            ref = str(f.get("reference", "")).strip()
            order = f.get("order", i)
            if not label or not ref:
                raise ValueError(f"field[{i}] requires 'label' and 'reference' (got {f!r})")
            rows.append(f'    ("{label}", NAMEOF({ref}), {int(order)})')
        return "{\n" + ",\n".join(rows) + "\n}"

    # ------------------------------------------------------------ shape apply

    @staticmethod
    def _shape_columns(
        table,
        table_name: str,
        *,
        extra_columns: Optional[List[Dict[str, Any]]] = None,
        extra_count: int = 0,
    ) -> Dict[str, Any]:
        """Apply the complete FP column shape to `table` (idempotent, no save).

        Missing ``[ValueN]`` columns are created as explicit
        ``CalculatedTableColumn`` objects; existing ones are renamed / fixed in
        place. Returns ``{"applied": [...], "columns": {...}}``.
        """
        TOM = _tom()
        applied: List[str] = []

        def by_source(src: str):
            return _find(table.Columns, lambda c: getattr(c, "SourceColumn", None) == src)

        def ensure(src: str, name: str):
            col = by_source(src)
            if col is None:
                col = TOM.CalculatedTableColumn()
                col.Name = name
                col.SourceColumn = src
                try:
                    col.IsDataTypeInferred = True
                except Exception:  # noqa: BLE001
                    pass
                table.Columns.Add(col)
                applied.append(f"created {src} as '{name}'")
            elif col.Name != name:
                applied.append(f"renamed {src} '{col.Name}' -> '{name}'")
                col.Name = name
            try:
                if getattr(col, "IsNameInferred", False):
                    col.IsNameInferred = False
                    applied.append(f"{name} IsNameInferred=false")
            except Exception:  # noqa: BLE001
                pass
            return col

        name_col = ensure("[Value1]", table_name)
        fields_col = ensure("[Value2]", f"{table_name} Fields")
        order_col = ensure("[Value3]", f"{table_name} Order")

        for col, hidden in ((fields_col, True), (order_col, True)):
            if not bool(getattr(col, "IsHidden", False)) and hidden:
                col.IsHidden = True
                applied.append(f"{col.Name} hidden")
        for col in (name_col, fields_col):
            current = getattr(col, "SortByColumn", None)
            if current is None or getattr(current, "Name", None) != order_col.Name:
                col.SortByColumn = order_col
                applied.append(f"{col.Name} sortBy Order")
        none_agg = _enum_none(TOM.AggregateFunction)
        for col, agg in (
            (name_col, none_agg),
            (fields_col, none_agg),
            (order_col, TOM.AggregateFunction.Sum),
        ):
            try:
                if str(getattr(col, "SummarizeBy", "")) != str(agg):
                    col.SummarizeBy = agg
                    applied.append(f"{col.Name} summarizeBy")
            except Exception as e:  # noqa: BLE001
                logger.debug("SummarizeBy on %s: %s", col.Name, e)
        if getattr(order_col, "FormatString", None) != "0":
            order_col.FormatString = "0"
            applied.append("Order formatString 0")

        # GroupByColumns on the value column → Fields (FP selector recognition).
        rcd = getattr(name_col, "RelatedColumnDetails", None)
        if rcd is None:
            rcd = TOM.RelatedColumnDetails()
            name_col.RelatedColumnDetails = rcd
        has_group = any(
            getattr(getattr(g, "GroupingColumn", None), "Name", None) == fields_col.Name
            for g in list(getattr(rcd, "GroupByColumns", None) or [])
        )
        if not has_group:
            gbc = TOM.GroupByColumn()
            gbc.GroupingColumn = fields_col
            rcd.GroupByColumns.Add(gbc)
            applied.append("groupByColumn -> Fields")

        # ParameterMetadata on the Fields column (the FP icon marker).
        existing_ep = None
        try:
            existing_ep = fields_col.ExtendedProperties["ParameterMetadata"]
        except Exception:
            existing_ep = _find(
                fields_col.ExtendedProperties,
                lambda ep: getattr(ep, "Name", None) == "ParameterMetadata",
            )
        if existing_ep is None:
            ep = TOM.JsonExtendedProperty()
            ep.Name = "ParameterMetadata"
            ep.Value = PARAMETER_METADATA
            fields_col.ExtendedProperties.Add(ep)
            applied.append("ParameterMetadata")

        # Value4..ValueN: explicit columns (renamed via extra_columns), no summarization.
        renames = {
            str(s.get("source", "")).strip(): s for s in (extra_columns or []) if s.get("source")
        }
        extra_report: List[Dict[str, Any]] = []
        for k in range(4, 4 + max(0, int(extra_count))):
            src = f"[Value{k}]"
            spec = renames.pop(src, None) or {}
            existing = by_source(src)
            target_name = (
                str(spec.get("name") or "").strip()
                or (existing.Name if existing is not None else "")
                or f"Value{k}"
            )
            col = ensure(src, target_name)
            if spec.get("hidden") is not None:
                col.IsHidden = bool(spec.get("hidden"))
            try:
                col.SummarizeBy = none_agg
            except Exception:  # noqa: BLE001
                pass
            if spec:
                extra_report.append(
                    {
                        "source": src,
                        "name": col.Name,
                        "hidden": bool(getattr(col, "IsHidden", False)),
                    }
                )
        for src, spec in renames.items():
            extra_report.append({"source": src, "renamed": False, "reason": "not_found"})

        return {
            "applied": applied,
            "extra_columns": extra_report,
            "columns": {
                "value": name_col.Name,
                "fields_hidden": fields_col.Name,
                "order_hidden": order_col.Name,
            },
        }

    @staticmethod
    def _request_calculate(table) -> None:
        try:
            table.RequestRefresh(_tom().RefreshType.Calculate)
        except Exception as e:  # noqa: BLE001
            logger.debug("FP calculate refresh request failed: %s", e)

    # --------------------------------------------------------- public: create

    def create_field_parameter(
        self,
        table_name: str,
        fields: Optional[List[Dict[str, Any]]] = None,
        description: Optional[str] = None,
        hidden: bool = False,
        expression: Optional[str] = None,
        extra_columns: Optional[List[Dict[str, Any]]] = None,
        clone_from: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a field parameter table in ONE SaveChanges (C-07).

        Exactly one of ``fields`` (``{label, reference, order?}``),
        ``expression`` (a ``{ (…), … }`` body, or — C-09 — a data-driven table
        expression) or ``clone_from`` (an existing FP to copy) is required.
        """
        if not AMO_AVAILABLE:
            return error_envelope(
                "amo_unavailable", "AMO not available - cannot create field parameters"
            )
        if not self._valid_identifier(table_name):
            return error_envelope(
                "invalid_parameters", "table_name must be non-empty and <=128 chars"
            )
        has_fields = bool(fields)
        has_expr = bool(expression and str(expression).strip())
        has_clone = bool(clone_from and str(clone_from).strip())
        if sum([has_fields, has_expr, has_clone]) != 1:
            return error_envelope(
                "invalid_parameters",
                "Provide exactly one of `fields`, `expression`, or `clone_from`",
            )

        notes: List[str] = []
        cloned_extras: List[Dict[str, Any]] = []
        if has_clone:
            src = str(clone_from).strip()
            tmp_server, _tmp_db, tmp_model = self._get_server_db_model()
            if not tmp_model:
                return {
                    "success": False,
                    "error": self._connection_error_msg(),
                    "error_type": "connection_error",
                }
            try:
                src_table = next((t for t in tmp_model.Tables if t.Name == src), None)
                if not src_table:
                    return error_envelope(
                        "source_not_found", f"clone_from source table '{src}' not found"
                    )
                src_part = next(iter(src_table.Partitions), None)
                src_expr = getattr(getattr(src_part, "Source", None), "Expression", None)
                if not src_expr:
                    return error_envelope(
                        "source_invalid",
                        f"clone_from source '{src}' has no calculated partition expression",
                    )
                expr_body = str(src_expr).strip()
                for c in src_table.Columns:
                    sc = getattr(c, "SourceColumn", None) or ""
                    if sc.startswith("[Value") and sc not in ("[Value1]", "[Value2]", "[Value3]"):
                        cloned_extras.append(
                            {"source": sc, "name": c.Name, "hidden": bool(c.IsHidden)}
                        )
            finally:
                try:
                    tmp_server.Disconnect()
                except Exception:
                    pass
        elif has_fields:
            try:
                expr_body = self._build_expression(fields)
            except ValueError as ve:
                return error_envelope("invalid_parameters", str(ve))
        else:
            expr_body = str(expression).strip()
            if not expr_body.startswith("{"):
                notes.append(
                    "Data-driven field parameter: the columns are shaped for [Value1..3]; "
                    "add_field/remove_field/reorder_fields only work on { (…) } bodies."
                )
        if has_clone and not extra_columns and cloned_extras:
            extra_columns = cloned_extras

        rows, tuple_count = parse_fp_rows(expr_body)
        extra_count = max((len(r["extras"]) for r in rows), default=0)
        if extra_columns:
            extra_count = max(
                extra_count,
                max(
                    (
                        int(_re_fp.sub(r"\D", "", str(s.get("source", ""))) or 0) - 3
                        for s in extra_columns
                    ),
                    default=0,
                ),
            )

        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }

        committed = False
        try:
            if any(str(t.Name).lower() == str(table_name).lower() for t in model.Tables):
                return error_envelope("table_exists", f"Table '{table_name}' already exists")
            TOM = _tom()
            table = TOM.Table()
            table.Name = table_name
            if description:
                table.Description = description
            if hidden:
                table.IsHidden = True
            partition = TOM.Partition()
            partition.Name = table_name
            partition.Mode = TOM.ModeType.Import
            calc_source = TOM.CalculatedPartitionSource()
            calc_source.Expression = expr_body
            partition.Source = calc_source
            table.Partitions.Add(partition)

            shape = self._shape_columns(
                table, table_name, extra_columns=extra_columns, extra_count=extra_count
            )
            self._apply_pbi_annotations(table)
            model.Tables.Add(table)
            self._request_calculate(table)

            try:
                info = commit(
                    model,
                    label="field_parameter/create",
                    verify=lambda: self._fp_landed(table_name),
                )
            except Exception as save_err:
                try:
                    safe_undo(model)
                except Exception:  # noqa: BLE001
                    pass
                rollback = self._rollback_on_fresh_connection(table_name)
                return {
                    "success": False,
                    "error": f"Field parameter create failed: {save_err}",
                    "error_type": "creation_error",
                    "table": table_name,
                    "rolled_back": bool(rollback.get("rolled_back")),
                    "rollback": rollback,
                }
            committed = True

            result = {
                "success": True,
                "action": "created",
                "table": table_name,
                "field_count": len(rows) if tuple_count >= 0 else None,
                "columns": shape["columns"],
                "expression": expr_body,
                "message": (
                    f"Created field parameter '{table_name}' in one transaction. The Fields "
                    "column carries ParameterMetadata, the value column groups by Fields."
                ),
                **info,
            }
            if shape["extra_columns"]:
                result["extra_columns"] = shape["extra_columns"]
            if notes:
                result["notes"] = notes
            return result

        except Exception as e:
            logger.error(f"Error creating field parameter: {e}", exc_info=True)
            out = error_envelope("creation_error", str(e))
            if not committed:
                try:
                    safe_undo(model)
                except Exception:  # noqa: BLE001
                    pass
            return out
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    @staticmethod
    def _fp_landed(table_name: str) -> Optional[bool]:
        """Read-back: the FP table AND its Fields column exist on the server."""
        from core.operations import model_readback

        exists = model_readback.table_exists(table_name)
        if not exists:
            return exists
        return model_readback.column_exists(table_name, f"{table_name} Fields")

    # ----------------------------------------------- public: repair / backfill

    def repair_field_parameter(
        self,
        table_name: str,
        fields_column: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply the complete FP shape to an existing table, idempotently (C-09).

        Detects the columns by ``SourceColumn`` (so a half-built table whose
        columns are still ``Value1..3`` is found), restores names
        (``IsNameInferred=false``), hidden flags, sort-by, SummarizeBy,
        ``RelatedColumnDetails.GroupByColumns``, ParameterMetadata, lineage tags
        and annotations (an existing ``PBI_Id`` is kept), requests a Calculate
        refresh and saves once. ``fields_column`` is accepted for backward
        compatibility (the Fields column is located by ``[Value2]``).
        """
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")
        if not self._valid_identifier(table_name):
            return error_envelope(
                "invalid_parameters", "table_name must be non-empty and <=128 chars"
            )

        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }
        try:
            tbl = next((t for t in model.Tables if t.Name == table_name), None)
            if not tbl:
                return error_envelope("table_not_found", f"Table '{table_name}' not found")
            part = next(iter(tbl.Partitions), None)
            expr = getattr(getattr(part, "Source", None), "Expression", None) or ""
            rows, _count = parse_fp_rows(expr)
            extra_count = max((len(r["extras"]) for r in rows), default=0)
            extras = [
                {"source": c.SourceColumn, "name": c.Name}
                for c in list(tbl.Columns)
                if str(getattr(c, "SourceColumn", "") or "").startswith("[Value")
                and getattr(c, "SourceColumn", None) not in ("[Value1]", "[Value2]", "[Value3]")
            ]
            if fields_column:
                col = _find(tbl.Columns, lambda c: c.Name == fields_column)
                if col is not None and getattr(col, "SourceColumn", None) != "[Value2]":
                    return error_envelope(
                        "invalid_parameters",
                        f"'{fields_column}' is not the [Value2] (Fields) column of "
                        f"'{table_name}' — omit column_name to auto-detect it.",
                    )
            shape = self._shape_columns(
                tbl, table_name, extra_columns=extras, extra_count=extra_count
            )
            annotations = self._apply_pbi_annotations(tbl)
            applied = shape["applied"] + annotations
            if not applied:
                return {
                    "success": True,
                    "action": "noop",
                    "table": table_name,
                    "columns": shape["columns"],
                    "message": "Field parameter already has the complete shape.",
                }
            self._request_calculate(tbl)
            info = commit(model, label="field_parameter/repair")
            return {
                "success": True,
                "action": "repaired",
                "table": table_name,
                "column": shape["columns"]["fields_hidden"],
                "columns": shape["columns"],
                "repaired": applied,
                "message": (
                    f"Repaired field parameter '{table_name}' ({len(applied)} fix(es)). "
                    "Save the .pbip in Desktop to persist it to disk."
                ),
                **info,
            }
        except Exception as e:
            logger.error(f"Error repairing field parameter: {e}", exc_info=True)
            try:
                safe_undo(model)
            except Exception:  # noqa: BLE001
                pass
            return error_envelope("update_error", str(e))
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def set_parameter_metadata(
        self, table_name: str, fields_column: Optional[str] = None
    ) -> Dict[str, Any]:
        """Backward-compatible name for :meth:`repair_field_parameter`."""
        return self.repair_field_parameter(table_name, fields_column=fields_column)

    # ----------------------------------------------- public: in-place mutations

    def _load_fp_partition(self, table_name: str):
        """Return (server, model, partition) on success, or (server, None, None)
        when the table or partition can't be found. Caller is responsible for
        server.Disconnect() in a finally block."""
        server, _db, model = self._get_server_db_model()
        if not model:
            return server, None, None
        table = next((t for t in model.Tables if t.Name == table_name), None)
        if not table:
            return server, None, None
        partition = next(iter(table.Partitions), None)
        if partition is None:
            return server, None, None
        return server, model, partition

    def _mutate_rows(self, table_name: str, label: str, mutate) -> Dict[str, Any]:
        """Shared load → parse (refuse on partial parse) → mutate → save."""
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")
        server, model, partition = self._load_fp_partition(table_name)
        if not partition:
            try:
                if server:
                    server.Disconnect()
            except Exception:
                pass
            return error_envelope("fp_not_found", f"Field parameter table '{table_name}' not found")
        try:
            rows, tuple_count = parse_fp_rows(partition.Source.Expression)
            if tuple_count < 0 or len(rows) != tuple_count:
                return {
                    "success": False,
                    "error": (
                        f"Could not parse every row of '{table_name}' ({len(rows)} of "
                        f"{max(tuple_count, 0)} understood) — refusing to rewrite the "
                        "partition, which would drop the unparsed rows."
                    ),
                    "error_type": "fp_parse_incomplete",
                }
            out = mutate(rows)
            if out.get("success") is False:
                return out
            partition.Source.Expression = _serialize_fp_rows(out.pop("_rows"))
            table = next((t for t in model.Tables if t.Name == table_name), None)
            if table is not None:
                self._request_calculate(table)
            out.update(commit(model, label=label))
            return out
        except Exception as e:
            logger.error(f"{label} failed: {e}", exc_info=True)
            return error_envelope("fp_mutation_error", str(e))
        finally:
            try:
                if server:
                    server.Disconnect()
            except Exception:
                pass

    def add_field(self, table_name: str, field: Dict[str, Any]) -> Dict[str, Any]:
        """Append a field row to an existing field-parameter table."""
        label = (field or {}).get("label")
        ref = (field or {}).get("reference")
        if not label or not ref:
            return error_envelope("invalid_parameters", "field requires 'label' and 'reference'")

        def mutate(rows):
            if any(r["label"] == label for r in rows):
                return error_envelope(
                    "duplicate_label", f"Label '{label}' already exists in '{table_name}'"
                )
            next_order = (max((r["order"] for r in rows), default=-1) + 1) if rows else 0
            order = int(field.get("order", next_order))
            width = max((len(r["extras"]) for r in rows), default=0)
            rows.append(
                {
                    "label": label,
                    "nameof": f"NAMEOF({ref})",
                    "order": order,
                    # keep the tuple arity of the table (extra columns → BLANK())
                    "extras": ["BLANK()"] * width,
                }
            )
            return {
                "success": True,
                "action": "added",
                "label": label,
                "order": order,
                "field_count": len(rows),
                "_rows": rows,
            }

        return self._mutate_rows(table_name, "field_parameter/add_field", mutate)

    def remove_field(self, table_name: str, label: str) -> Dict[str, Any]:
        """Remove a row by label and renumber the remaining rows 0..N-1."""

        def mutate(rows):
            kept = [r for r in rows if r["label"] != label]
            if len(kept) == len(rows):
                return error_envelope(
                    "label_not_found", f"Label '{label}' not found in '{table_name}'"
                )
            for i, r in enumerate(kept):
                r["order"] = i
            return {
                "success": True,
                "action": "removed",
                "label": label,
                "field_count": len(kept),
                "_rows": kept,
            }

        return self._mutate_rows(table_name, "field_parameter/remove_field", mutate)

    def reorder_fields(self, table_name: str, labels_in_order: List[str]) -> Dict[str, Any]:
        """Reassign orders 0..N-1 according to ``labels_in_order``.

        ``labels_in_order`` must be a permutation of the table's existing
        labels. The response uses caller-perspective semantics:

        - ``missing``: labels supplied but not present in the model.
        - ``extra``: labels in the model that the caller didn't include.
        """

        def mutate(rows):
            by_label = {r["label"]: r for r in rows}
            extra = [lbl for lbl in by_label if lbl not in labels_in_order]
            missing = [lbl for lbl in labels_in_order if lbl not in by_label]
            if missing or extra:
                return {
                    "success": False,
                    "error": "labels_in_order must be a permutation of existing labels",
                    "error_type": "invalid_permutation",
                    "missing": missing,
                    "extra": extra,
                }
            new_rows = []
            for i, lbl in enumerate(labels_in_order):
                r = by_label[lbl]
                r["order"] = i
                new_rows.append(r)
            return {
                "success": True,
                "action": "reordered",
                "field_count": len(new_rows),
                "_rows": new_rows,
            }

        return self._mutate_rows(table_name, "field_parameter/reorder_fields", mutate)
