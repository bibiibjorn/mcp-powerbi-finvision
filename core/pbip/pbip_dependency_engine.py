"""
PBIP Dependency Engine - dependency + usage analysis for PBIP projects.

Review G-08 / G-25 / G-28 (2026-09-22) rewrite. Every DAX carrier of the
model is read with the lexer-driven :func:`core.dax.dax_reference_parser.
scan_references` (strings/comments never count, ``''`` / ``]]`` escapes,
quoted AND unquoted tables, ``NAMEOF ( T[C] )``):

* measures — expression, ``formatStringDefinition``, ``detailRowsDefinition``, KPI;
* calculated columns, calculation items (+ their format strings), calculated
  tables (field parameters), RLS table permissions (row context), UDF bodies
  (``functions.tmdl``) and report-level (extension) measures.

Structural usage: relationship ends (quoted or bare ``Table.Column``),
``sortByColumn`` pairs, hierarchy levels, field-parameter ``groupByColumn`` /
``ParameterMetadata`` columns, OLS ``columnPermission`` and ``alternateOf``.
Report usage: every field reference of every visual (all query roles incl.
Deneb ``dataset``, sort, conditional formatting, dynamic titles, the top-level
``filterConfig``), page/report filters, bookmark states and report measures.

Every used object carries the REASONS it is used (``usage_reasons``), and the
result states its ``coverage`` so a partial view is visible.
"""

from __future__ import annotations

import logging
from collections import deque
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    from core.dax.dax_reference_parser import DaxReferenceIndex, get_lexer, scan_references
    from core.dax.tokenizer.tokens import TokenType

    DAX_PARSER_AVAILABLE = True
except ImportError:  # pragma: no cover
    DAX_PARSER_AVAILABLE = False
    logging.warning("DAX parser not available; dependency analysis will be limited")

logger = logging.getLogger(__name__)

_MAX_REASONS = 12


@lru_cache(maxsize=16384)
def _lex_scan(expr: str, known_tables: frozenset) -> Tuple[tuple, tuple]:
    """``(scan_references result, identifiers called like functions)`` for *expr*.

    Pure in (text, known table names) — cached so the per-report engine runs of
    a multi-report project (and repeated analyses) tokenize each body once.
    """
    code = get_lexer().tokenize_code(expr)
    refs = tuple(scan_references(expr, known_tables=set(known_tables), code=code))
    calls = []
    for i in range(len(code) - 1):
        t = code[i]
        if (
            t.type in (TokenType.IDENTIFIER, TokenType.FUNCTION)
            and code[i + 1].type == TokenType.PAREN_OPEN
        ):
            calls.append(t.value)
    return refs, tuple(calls)


def _n(name: Any) -> str:
    return " ".join(str(name or "").lower().split())


def _key(table: str, name: str) -> str:
    return f"{table}[{name}]"


class _Refs:
    """References found in one DAX carrier."""

    __slots__ = ("measures", "columns", "tables", "udfs", "lone")

    def __init__(self) -> None:
        self.measures: List[str] = []
        self.columns: List[str] = []
        self.tables: List[str] = []
        self.udfs: List[str] = []
        self.lone: List[str] = []  # unresolved lone [x] -> column names


class _Index:
    def __init__(self, model: Dict[str, Any]) -> None:
        self.tables: Dict[str, str] = {}
        self.columns: Dict[Tuple[str, str], Tuple[str, str]] = {}
        self.columns_by_name: Dict[str, List[Tuple[str, str]]] = {}
        self.measures: Dict[str, Tuple[str, str]] = {}
        self.measure_keys: Dict[Tuple[str, str], Tuple[str, str]] = {}
        self.levels: Dict[Tuple[str, str], List[Tuple[str, str]]] = (
            {}
        )  # (t, hier) -> [(level, col)]
        self.udfs: Dict[str, str] = {}
        for t in model.get("tables", []) or []:
            tn = t.get("name", "")
            if not tn:
                continue
            self.tables[_n(tn)] = tn
            for c in t.get("columns", []) or []:
                cn = c.get("name", "")
                if cn:
                    self.columns[(_n(tn), _n(cn))] = (tn, cn)
                    self.columns_by_name.setdefault(_n(cn), []).append((tn, cn))
            for m in t.get("measures", []) or []:
                mn = m.get("name", "")
                if mn:
                    self.measures.setdefault(_n(mn), (tn, mn))
                    self.measure_keys[(_n(tn), _n(mn))] = (tn, mn)
            for h in t.get("hierarchies", []) or []:
                hn = h.get("name", "")
                self.levels[(_n(tn), _n(hn))] = [
                    (lv.get("name", ""), lv.get("column") or "") for lv in h.get("levels", []) or []
                ]
        for f in model.get("functions", []) or []:
            if f.get("name"):
                self.udfs[_n(f["name"])] = f["name"]

    def column(self, table: str, name: str) -> Optional[Tuple[str, str]]:
        return self.columns.get((_n(table), _n(name)))

    def field(self, table: str, name: str) -> Tuple[str, Optional[Tuple[str, str]]]:
        """Resolve ``table[name]`` to ("column"|"measure"|"", canonical)."""
        c = self.column(table, name)
        if c:
            return "column", c
        m = self.measure_keys.get((_n(table), _n(name)))
        if m:
            return "measure", m
        m2 = self.measures.get(_n(name))
        if m2:
            return "measure", m2
        return "", None


class PbipDependencyEngine:
    """Comprehensive dependency analysis for PBIP projects."""

    def __init__(self, model_data: Optional[Dict] = None, report_data: Optional[Dict] = None):
        self.model = model_data or {}
        self.report = report_data
        self.logger = logger

        self.measure_to_measure: Dict[str, List[str]] = {}
        self.measure_to_measure_reverse: Dict[str, List[str]] = {}
        self.measure_to_column: Dict[str, List[str]] = {}
        self.measure_to_table: Dict[str, Set[str]] = {}
        # column -> measures AND calc columns that reference it (P1-49: mixed referrers)
        self.column_to_measure: Dict[str, List[str]] = {}
        self.column_to_field_params: Dict[str, List[str]] = {}
        self.visual_dependencies: Dict[str, Dict[str, Any]] = {}
        self.page_dependencies: Dict[str, Dict[str, Any]] = {}
        self.filter_pane_data: Dict[str, Any] = {}
        self.usage_reasons: Dict[str, List[str]] = {}
        self.udf_dependencies: Dict[str, Dict[str, List[str]]] = {}
        self.coverage: Dict[str, Any] = {}

        self._idx = _Index(self.model)
        self._known_tables = frozenset(self._idx.tables)
        self._carrier_refs: List[Tuple[str, str, _Refs]] = []  # (kind, owner, refs)
        self._calc_col_refs: Dict[str, _Refs] = {}
        self._udf_refs: Dict[str, _Refs] = {}
        self._unresolved = 0

        # kept for callers that read it
        self.reference_index: Optional[Any] = None
        if DAX_PARSER_AVAILABLE:
            self._build_reference_index()

    def _build_reference_index(self) -> None:
        measure_rows, column_rows = [], []
        for table in self.model.get("tables", []) or []:
            tn = table.get("name", "")
            for m in table.get("measures", []) or []:
                measure_rows.append({"Table": tn, "Name": m.get("name", "")})
            for c in table.get("columns", []) or []:
                column_rows.append({"Table": tn, "Name": c.get("name", "")})
        self.reference_index = DaxReferenceIndex(measure_rows, column_rows)

    # ─── DAX scanning ────────────────────────────────────────────────

    def _scan(self, expr: Optional[str], home_table: str = "", row_context: bool = False) -> _Refs:
        out = _Refs()
        if not expr or not isinstance(expr, str) or not DAX_PARSER_AVAILABLE:
            return out
        try:
            refs, calls = _lex_scan(expr, self._known_tables)
        except Exception as e:  # noqa: BLE001 - never fail the whole analysis on one body
            self.logger.debug("DAX scan failed: %s", e)
            return out
        idx = self._idx
        for r in refs:
            if r.kind == "qualified":
                kind, canon = idx.field(r.table, r.name)
                if kind == "column":
                    out.columns.append(_key(*canon))
                elif kind == "measure":
                    out.measures.append(_key(*canon))
                else:
                    self._unresolved += 1
            elif r.kind == "bracket":
                m = idx.measures.get(_n(r.name))
                if m:
                    out.measures.append(_key(*m))
                    continue
                c = idx.column(home_table, r.name) if home_table else None
                if c and row_context:
                    out.columns.append(_key(*c))
                elif c:
                    out.lone.append(_key(*c))
                else:
                    for t, cn in idx.columns_by_name.get(_n(r.name), []):
                        out.lone.append(_key(t, cn))
            elif r.kind == "table":
                t = idx.tables.get(_n(r.table))
                if t:
                    out.tables.append(t)
        if idx.udfs:
            for name in calls:
                u = idx.udfs.get(_n(name))
                if u and u not in out.udfs:
                    out.udfs.append(u)
        return out

    # ─── main ────────────────────────────────────────────────────────

    def analyze_all_dependencies(self) -> Dict[str, Any]:
        """Dependency maps + unused objects (with reasons and coverage)."""
        self.logger.info("Starting comprehensive dependency analysis")
        self.measure_to_measure, self.measure_to_column, self.measure_to_table = {}, {}, {}
        self.column_to_measure, self.column_to_field_params = {}, {}
        self.visual_dependencies, self.page_dependencies, self.filter_pane_data = {}, {}, {}
        self.usage_reasons, self.udf_dependencies = {}, {}
        self._carrier_refs, self._calc_col_refs, self._udf_refs = [], {}, {}
        self._unresolved = 0

        self._analyze_measure_dependencies()
        self._analyze_column_usage()
        self._analyze_other_carriers()
        self._analyze_field_parameters()
        self._build_reverse_indices()

        if self.report:
            self._analyze_visual_dependencies()
            self._analyze_page_dependencies()
            self._analyze_filter_pane()

        unused = self._find_unused_objects()

        result = {
            "measure_to_measure": self.measure_to_measure,
            "measure_to_measure_reverse": self.measure_to_measure_reverse,
            "measure_to_column": self.measure_to_column,
            "measure_to_table": {k: sorted(v) for k, v in self.measure_to_table.items()},
            "column_to_measure": self.column_to_measure,
            "column_to_field_params": self.column_to_field_params,
            "udf_dependencies": self.udf_dependencies,
            "visual_dependencies": self.visual_dependencies,
            "page_dependencies": self.page_dependencies,
            "filter_pane_data": self.filter_pane_data,
            "unused_measures": unused["measures"],
            "unused_columns": unused["columns"],
            "unused_functions": unused["functions"],
            "usage_reasons": self.usage_reasons,
            "coverage": self.coverage,
            "summary": {
                "total_measures": self._count_measures(),
                "total_columns": self._count_columns(),
                "total_tables": len(self.model.get("tables", [])),
                "total_relationships": len(self.model.get("relationships", [])),
                "total_functions": len(self.model.get("functions", []) or []),
                "measures_with_dependencies": len(self.measure_to_measure),
                "columns_used_in_measures": len(self.column_to_measure),
                "unused_measures": len(unused["measures"]),
                "unused_columns": len(unused["columns"]),
            },
        }
        if self.report:
            result["summary"]["total_pages"] = len(self.report.get("pages", []))
            result["summary"]["total_visuals"] = sum(
                len(p.get("visuals", [])) for p in self.report.get("pages", [])
            )
        return result

    def _analyze_measure_dependencies(self) -> None:
        for table in self.model.get("tables", []) or []:
            tn = table.get("name", "")
            for m in table.get("measures", []) or []:
                mkey = _key(tn, m.get("name", ""))
                refs = _Refs()
                bodies = [
                    m.get("expression"),
                    m.get("format_string_definition"),
                    m.get("detail_rows_definition"),
                ]
                bodies.extend((m.get("kpi") or {}).values())
                for body in bodies:
                    r = self._scan(body, tn)
                    refs.measures += r.measures
                    refs.columns += r.columns + r.lone
                    refs.tables += r.tables
                    refs.udfs += r.udfs
                deps = [d for d in dict.fromkeys(refs.measures) if _n(d) != _n(mkey)]
                if deps:
                    self.measure_to_measure[mkey] = deps
                cols = list(dict.fromkeys(refs.columns))
                if cols:
                    self.measure_to_column[mkey] = cols
                if refs.tables:
                    self.measure_to_table.setdefault(mkey, set()).update(refs.tables)
                if refs.udfs:
                    self.udf_dependencies.setdefault(mkey, {})["udfs"] = list(
                        dict.fromkeys(refs.udfs)
                    )

    def _analyze_column_usage(self) -> None:
        """Calculated-column references (row context of the home table)."""
        seen: Dict[str, Set[str]] = {k: set(v) for k, v in self.column_to_measure.items()}
        for table in self.model.get("tables", []) or []:
            tn = table.get("name", "")
            for c in table.get("columns", []) or []:
                expr = c.get("expression")
                if not expr:
                    continue
                ckey = _key(tn, c.get("name", ""))
                r = self._scan(expr, tn, row_context=True)
                self._calc_col_refs[ckey] = r
                for ref in list(dict.fromkeys(r.columns + r.lone)):
                    if ref == ckey:
                        continue
                    s = seen.setdefault(ref, set())
                    if ckey not in s:
                        s.add(ckey)
                        self.column_to_measure.setdefault(ref, []).append(ckey)
                if r.tables:
                    self.measure_to_table.setdefault(ckey, set()).update(r.tables)

    def _analyze_other_carriers(self) -> None:
        """Calc items, calculated tables, RLS, UDF bodies, report measures."""
        for table in self.model.get("tables", []) or []:
            tn = table.get("name", "")
            for ci in table.get("calculation_items", []) or []:
                for body in (ci.get("expression"), ci.get("format_string_definition")):
                    self._carrier_refs.append(
                        ("calc_item", f"{tn}/{ci.get('name', '')}", self._scan(body, tn))
                    )
            for p in table.get("partitions", []) or []:
                if str(p.get("type") or "").lower() == "calculated" and p.get("source"):
                    self._carrier_refs.append(("calc_table", tn, self._scan(p.get("source"), tn)))
        for role in self.model.get("roles", []) or []:
            for perm in role.get("table_permissions", []) or []:
                tn = perm.get("table", "")
                r = self._scan(perm.get("filter_expression"), tn, row_context=True)
                self._carrier_refs.append(("rls", f"{role.get('name', '')}/{tn}", r))
        for f in self.model.get("functions", []) or []:
            r = self._scan(f.get("expression"))
            self._udf_refs[f.get("name", "")] = r
            self.udf_dependencies.setdefault(f"udf:{f.get('name', '')}", {}).update(
                {
                    "measures": list(dict.fromkeys(r.measures)),
                    "columns": list(dict.fromkeys(r.columns + r.lone)),
                    "udfs": list(dict.fromkeys(r.udfs)),
                }
            )
        if self.report:
            for rm in self.report.get("report_measures", []) or []:
                self._carrier_refs.append(
                    (
                        "report_measure",
                        f"{rm.get('table', '')}[{rm.get('name', '')}]",
                        self._scan(rm.get("expression"), rm.get("table", "")),
                    )
                )

    def _analyze_field_parameters(self) -> None:
        """Field-parameter (calculated table) references: NAMEOF + any other ref."""
        seen: Dict[str, Set[str]] = {}
        for kind, owner, r in self._carrier_refs:
            if kind != "calc_table":
                continue
            for ref in list(dict.fromkeys(r.columns + r.measures)):
                if ref.startswith(f"{owner}["):
                    continue  # the FP table's own columns
                s = seen.setdefault(ref, set())
                if owner not in s:
                    s.add(owner)
                    self.column_to_field_params.setdefault(ref, []).append(owner)

    def _build_reverse_indices(self) -> None:
        rev: Dict[str, Set[str]] = {}
        for mkey, deps in self.measure_to_measure.items():
            for d in deps:
                rev.setdefault(d, set()).add(mkey)
        col: Dict[str, Set[str]] = {k: set(v) for k, v in self.column_to_measure.items()}
        for mkey, cols in self.measure_to_column.items():
            for c in cols:
                col.setdefault(c, set()).add(mkey)
        self.measure_to_measure_reverse = {k: sorted(v) for k, v in rev.items()}
        self.column_to_measure = {k: sorted(v) for k, v in col.items()}

    # ─── report side ─────────────────────────────────────────────────

    def _analyze_visual_dependencies(self) -> None:
        for page in self.report.get("pages", []):
            page_name = page.get("display_name", page.get("id", ""))
            for visual in page.get("visuals", []):
                vid = visual.get("id", "")
                fields = visual.get("fields", {}) or {}
                deps: Dict[str, Any] = {
                    "page": page_name,
                    "visual_id": vid,
                    "visual_type": visual.get("visual_type", ""),
                    "measures": [],
                    "columns": [],
                    "tables": set(),
                }
                for col in fields.get("columns", []):
                    if col.get("table") and col.get("column"):
                        deps["columns"].append(_key(col["table"], col["column"]))
                        deps["tables"].add(col["table"])
                for meas in fields.get("measures", []):
                    if meas.get("table") and meas.get("measure"):
                        deps["measures"].append(_key(meas["table"], meas["measure"]))
                        deps["tables"].add(meas["table"])
                deps["tables"] = sorted(deps["tables"])
                self.visual_dependencies[f"{page_name}/{vid}"] = deps

    def _analyze_page_dependencies(self) -> None:
        for page in self.report.get("pages", []):
            page_name = page.get("display_name", page.get("id", ""))
            pd = {
                "measures": set(),
                "columns": set(),
                "tables": set(),
                "visual_count": len(page.get("visuals", [])),
                "filter_count": len(page.get("filters", [])),
            }
            for visual in page.get("visuals", []):
                fields = visual.get("fields", {}) or {}
                for col in fields.get("columns", []):
                    if col.get("table") and col.get("column"):
                        pd["columns"].add(_key(col["table"], col["column"]))
                        pd["tables"].add(col["table"])
                for meas in fields.get("measures", []):
                    if meas.get("table") and meas.get("measure"):
                        pd["measures"].add(_key(meas["table"], meas["measure"]))
                        pd["tables"].add(meas["table"])
            for filt in page.get("filters", []):
                field = filt.get("field", {}) or {}
                if field.get("table") and field.get("name"):
                    pd["tables"].add(field["table"])
                    if field.get("type") == "Column":
                        pd["columns"].add(_key(field["table"], field["name"]))
                    elif field.get("type") == "Measure":
                        pd["measures"].add(_key(field["table"], field["name"]))
            self.page_dependencies[page_name] = {
                "measures": sorted(pd["measures"]),
                "columns": sorted(pd["columns"]),
                "tables": sorted(pd["tables"]),
                "visual_count": pd["visual_count"],
                "filter_count": pd["filter_count"],
            }

    def _report_refs(self) -> Iterable[Tuple[Dict[str, Any], str]]:
        """``(ref dict, reason)`` for every report-side field reference."""
        if not self.report:
            return
        for filt in (self.report.get("report", {}) or {}).get("filters", []) or []:
            f = filt.get("field") or {}
            yield (
                {
                    "kind": (f.get("type") or "").lower(),
                    "table": f.get("table", ""),
                    "name": f.get("name", ""),
                    "level": f.get("level", ""),
                    "variation_column": f.get("variation_column", ""),
                },
                "report filter",
            )
        for page in self.report.get("pages", []):
            pname = page.get("display_name", page.get("id", ""))
            for filt in page.get("filters", []) or []:
                f = filt.get("field") or {}
                yield (
                    {
                        "kind": (f.get("type") or "").lower(),
                        "table": f.get("table", ""),
                        "name": f.get("name", ""),
                        "level": f.get("level", ""),
                        "variation_column": f.get("variation_column", ""),
                    },
                    f"page filter: {pname}",
                )
            for visual in page.get("visuals", []):
                vid = visual.get("id", "")
                refs = visual.get("field_refs")
                if refs is None:  # older report_data without field_refs
                    fields = visual.get("fields", {}) or {}
                    refs = [
                        {"kind": "column", "table": c.get("table"), "name": c.get("column")}
                        for c in fields.get("columns", [])
                    ]
                    refs += [
                        {"kind": "measure", "table": m.get("table"), "name": m.get("measure")}
                        for m in fields.get("measures", [])
                    ]
                    for filt in visual.get("filters", []) or []:
                        f = filt.get("field") or {}
                        refs.append(
                            {
                                "kind": (f.get("type") or "").lower(),
                                "table": f.get("table"),
                                "name": f.get("name"),
                                "location": "filter",
                            }
                        )
                for ref in refs:
                    yield ref, f"visual {pname}/{vid} ({ref.get('location', 'field')})"
        for bm in self.report.get("bookmarks", []) or []:
            for ref in bm.get("field_refs", []) or []:
                yield ref, f"bookmark: {bm.get('display_name') or bm.get('id')}"

    def _normalize_key(self, key: str) -> str:
        """Normalize ``Table[Name]`` for comparison (quotes, whitespace, case)."""
        if not key:
            return ""
        key = key.strip().strip("'\"")
        if "[" in key and "]" in key:
            i = key.index("[")
            table = " ".join(key[:i].strip().strip("'\"").lower().split())
            name = " ".join(key[i + 1 :].rstrip("]").strip().lower().split())
            return f"{table}[{name}]"
        return key.lower()

    # ─── unused ──────────────────────────────────────────────────────

    def _find_unused_objects(self) -> Dict[str, List[str]]:
        idx = self._idx
        N = self._normalize_key
        reasons: Dict[str, List[str]] = {}

        def why(key: str, reason: str) -> None:
            lst = reasons.setdefault(N(key), [])
            if len(lst) < _MAX_REASONS and reason not in lst:
                lst.append(reason)

        used_m: Set[str] = set()
        used_c: Set[str] = set()
        used_u: Set[str] = set()
        queue_m: deque = deque()
        queue_u: deque = deque()

        def use_measure(key: str, reason: str) -> None:
            why(key, reason)
            nk = N(key)
            if nk not in used_m:
                used_m.add(nk)
                queue_m.append(key)

        def use_udf(name: str, reason: str) -> None:
            why(f"udf:{name}", reason)
            if _n(name) not in used_u:
                used_u.add(_n(name))
                queue_u.append(name)

        def use_column(key: str, reason: str) -> None:
            why(key, reason)
            used_c.add(N(key))

        # --- report roots
        reports_refs = 0
        for ref, reason in self._report_refs():
            reports_refs += 1
            kind = ref.get("kind") or ""
            table = ref.get("table") or ""
            name = ref.get("name") or ""
            if kind in ("hierarchy", "hierarchy_level"):
                if ref.get("variation_column"):
                    c = idx.column(table, ref["variation_column"])
                    if c:
                        use_column(_key(*c), reason)
                    continue
                levels = idx.levels.get((_n(table), _n(name)), [])
                for lvl, col in levels:
                    if kind == "hierarchy" or _n(lvl) == _n(ref.get("level")):
                        c = idx.column(table, col)
                        if c:
                            use_column(_key(*c), reason)
                continue
            if not name:
                continue
            fkind, canon = idx.field(table, name)
            if fkind == "measure":
                use_measure(_key(*canon), reason)
            elif fkind == "column":
                use_column(_key(*canon), reason)

        # --- model-side roots
        for kind, owner, r in self._carrier_refs:
            label = {
                "calc_item": "calculation item",
                "calc_table": "calculated table",
                "rls": "RLS",
                "report_measure": "report measure",
            }[kind]
            reason = f"{label}: {owner}"
            for m in r.measures:
                use_measure(m, reason)
            for c in r.columns + r.lone:
                use_column(c, reason)
            for u in r.udfs:
                use_udf(u, reason)
        for fp_col, fps in self.column_to_field_params.items():
            for fp in fps:
                (use_measure if idx.field(*_split(fp_col))[0] == "measure" else use_column)(
                    fp_col, f"field parameter: {fp}"
                )

        # --- closure over measures and UDFs
        while queue_m or queue_u:
            while queue_m:
                mk = queue_m.popleft()
                for dep in self.measure_to_measure.get(mk, []):
                    use_measure(dep, f"measure: {mk}")
                for u in (self.udf_dependencies.get(mk) or {}).get("udfs", []):
                    use_udf(u, f"measure: {mk}")
            while queue_u:
                u = queue_u.popleft()
                r = self._udf_refs.get(u)
                if r is None:
                    continue
                for m in r.measures:
                    use_measure(m, f"udf: {u}")
                for u2 in r.udfs:
                    use_udf(u2, f"udf: {u}")

        # --- columns: any measure / UDF body references count
        for mkey, cols in self.measure_to_column.items():
            for c in cols:
                use_column(c, f"measure: {mkey}")
        for u, r in self._udf_refs.items():
            for c in r.columns + r.lone:
                use_column(c, f"udf: {u}")

        # --- structural
        for rel in self.model.get("relationships", []) or []:
            rid = rel.get("id", "")
            for t, c in (
                (rel.get("from_table"), rel.get("from_column_name")),
                (rel.get("to_table"), rel.get("to_column_name")),
            ):
                cc = idx.column(t or "", c or "")
                if cc:
                    use_column(_key(*cc), f"relationship: {rid}")
        for table in self.model.get("tables", []) or []:
            tn = table.get("name", "")
            is_fp = any(
                str(p.get("type") or "").lower() == "calculated"
                and "NAMEOF" in str(p.get("source") or "").upper()
                for p in table.get("partitions", []) or []
            )
            for col in table.get("columns", []) or []:
                cn = col.get("name", "")
                sb = col.get("sort_by_column")
                if sb:
                    s = idx.column(tn, sb)
                    if s:
                        use_column(_key(*s), f"sortBy of {_key(tn, cn)}")
                        use_column(_key(tn, cn), f"sorted by {_key(*s)}")
                for g in col.get("group_by_columns", []) or []:
                    gc = idx.column(tn, g)
                    if gc:
                        use_column(_key(*gc), f"groupBy of {_key(tn, cn)}")
                        use_column(_key(tn, cn), "field parameter display column")
                meta = (col.get("extended_properties") or {}).get("ParameterMetadata") or ""
                props = col.get("properties") or {}
                if _fp_kind(meta) == 2:
                    is_fp = True
                    use_column(_key(tn, cn), "field parameter (ParameterMetadata kind 2)")
                elif str(props.get('"kind"') or props.get("kind") or "") == "2":
                    is_fp = True
                    use_column(_key(tn, cn), "field parameter (ParameterMetadata kind 2)")
                for alt in props.get("_alternateOf", []) or []:
                    base = alt.get("baseColumn") if isinstance(alt, dict) else None
                    if base:
                        from core.tmdl.tmdl_tree import split_object_ref

                        bt, bc = split_object_ref(base)
                        b = idx.column(bt, bc)
                        if b:
                            use_column(_key(*b), f"alternateOf of {_key(tn, cn)}")
            if is_fp:
                for col in table.get("columns", []) or []:
                    if col.get("sort_by_column"):
                        use_column(_key(tn, col.get("name", "")), "field parameter structure")
            for h in table.get("hierarchies", []) or []:
                for lv in h.get("levels", []) or []:
                    c = idx.column(tn, lv.get("column") or "")
                    if c:
                        use_column(
                            _key(*c),
                            f"hierarchy level {tn}/{h.get('name', '')}/{lv.get('name', '')}",
                        )
        for role in self.model.get("roles", []) or []:
            for perm in role.get("table_permissions", []) or []:
                for cp in perm.get("column_permissions", []) or []:
                    c = idx.column(perm.get("table", ""), cp.get("column", ""))
                    if c:
                        use_column(_key(*c), f"OLS: {role.get('name', '')}")

        # --- calc columns: used columns propagate to what they reference
        work = deque(k for k in self._calc_col_refs if N(k) in used_c)
        while work:
            ck = work.popleft()
            r = self._calc_col_refs.get(ck)
            if r is None:
                continue
            for c in r.columns + r.lone:
                if N(c) not in used_c:
                    use_column(c, f"calculated column: {ck}")
                    if c in self._calc_col_refs:
                        work.append(c)
                else:
                    why(c, f"calculated column: {ck}")
            for m in r.measures:
                use_measure(m, f"calculated column: {ck}")
        # measures reached through calc columns may use more columns/UDFs — already
        # counted (any measure's columns count), UDF closure re-run for completeness
        while queue_m or queue_u:
            while queue_m:
                mk = queue_m.popleft()
                for dep in self.measure_to_measure.get(mk, []):
                    use_measure(dep, f"measure: {mk}")
                for u in (self.udf_dependencies.get(mk) or {}).get("udfs", []):
                    use_udf(u, f"measure: {mk}")
            while queue_u:
                u = queue_u.popleft()
                r = self._udf_refs.get(u)
                for m in (r.measures if r else []):
                    use_measure(m, f"udf: {u}")

        unused_m, unused_c = [], []
        for table in self.model.get("tables", []) or []:
            tn = table.get("name", "")
            for m in table.get("measures", []) or []:
                k = _key(tn, m.get("name", ""))
                if N(k) not in used_m:
                    unused_m.append(k)
            for c in table.get("columns", []) or []:
                k = _key(tn, c.get("name", ""))
                if N(k) not in used_c:
                    unused_c.append(k)
        unused_f = [
            f.get("name", "")
            for f in self.model.get("functions", []) or []
            if _n(f.get("name")) not in used_u
        ]

        self.usage_reasons = reasons
        roles = self.model.get("roles", []) or []
        self.coverage = {
            "report_attached": bool(self.report),
            "report_references": reports_refs,
            "pages": len((self.report or {}).get("pages", []) or []),
            "bookmarks": len((self.report or {}).get("bookmarks", []) or []),
            "udfs_parsed": len(self.model.get("functions", []) or []),
            "rls_filters_parsed": sum(
                1
                for r in roles
                for p in r.get("table_permissions", []) or []
                if p.get("filter_expression")
            ),
            "calc_items_parsed": sum(
                len(t.get("calculation_items", []) or [])
                for t in self.model.get("tables", []) or []
            ),
            "parse_issues": len(self.model.get("parse_issues", []) or []),
            "unresolved_dax_references": self._unresolved,
            "complete": bool(self.report) and not self.model.get("parse_issues"),
        }
        return {"measures": unused_m, "columns": unused_c, "functions": unused_f}

    # ─── misc API ────────────────────────────────────────────────────

    def _count_measures(self) -> int:
        return sum(len(t.get("measures", [])) for t in self.model.get("tables", []))

    def _count_columns(self) -> int:
        return sum(len(t.get("columns", [])) for t in self.model.get("tables", []))

    def get_measure_impact(self, measure_key: str) -> Dict[str, Any]:
        """What depends on *measure_key* (measures, visuals, pages)."""
        impact: Dict[str, Any] = {
            "measure": measure_key,
            "used_by_measures": [],
            "used_in_visuals": [],
            "used_in_pages": set(),
            "total_impact": 0,
        }
        nk = self._normalize_key(measure_key)
        for mk, deps in self.measure_to_measure.items():
            if any(self._normalize_key(d) == nk for d in deps):
                impact["used_by_measures"].append(mk)
        for vk, vd in self.visual_dependencies.items():
            if any(self._normalize_key(m) == nk for m in vd.get("measures", [])):
                impact["used_in_visuals"].append(vk)
                if vd.get("page"):
                    impact["used_in_pages"].add(vd["page"])
        impact["used_in_pages"] = sorted(impact["used_in_pages"])
        impact["total_impact"] = len(impact["used_by_measures"]) + len(impact["used_in_visuals"])
        return impact

    def calculate_dependency_depth(self, measure_key: str) -> int:
        """Maximum measure-dependency depth (memoised, cycle-safe)."""
        memo: Dict[str, int] = {}
        on_stack: Set[str] = set()

        def depth(key: str, guard: int) -> int:
            if guard > 1000:
                return 0
            if key in memo:
                return memo[key]
            if key in on_stack:
                return 0
            on_stack.add(key)
            deps = self.measure_to_measure.get(key, [])
            memo[key] = 0 if not deps else 1 + max(depth(d, guard + 1) for d in deps)
            on_stack.discard(key)
            return memo[key]

        return depth(measure_key, 0)

    def _analyze_filter_pane(self) -> None:
        """Filter pane data at report, page and visual level."""
        self.filter_pane_data = {
            "report_filters": [],
            "page_filters": {},
            "visual_filters": {},
            "summary": {
                "total_report_filters": 0,
                "total_page_filters": 0,
                "total_visual_filters": 0,
                "pages_with_filters": 0,
                "visuals_with_filters": 0,
            },
        }
        report_filters = (self.report.get("report", {}) or {}).get("filters", []) or []
        for filt in report_filters:
            self.filter_pane_data["report_filters"].append(
                self._build_filter_entry(filt, "report", "All Pages")
            )
        s = self.filter_pane_data["summary"]
        s["total_report_filters"] = len(report_filters)
        for page in self.report.get("pages", []):
            page_name = page.get("display_name", page.get("id", ""))
            page_filters = page.get("filters", []) or []
            if page_filters:
                s["pages_with_filters"] += 1
                s["total_page_filters"] += len(page_filters)
                self.filter_pane_data["page_filters"][page_name] = {
                    "page_id": page.get("id", ""),
                    "page_name": page_name,
                    "filters": [
                        self._build_filter_entry(f, "page", page_name) for f in page_filters
                    ],
                }
            for visual in page.get("visuals", []):
                vf = visual.get("filters", []) or []
                if not vf:
                    continue
                s["visuals_with_filters"] += 1
                s["total_visual_filters"] += len(vf)
                vkey = f"{page_name}/{visual.get('id', '')}"
                self.filter_pane_data["visual_filters"][vkey] = {
                    "visual_id": visual.get("id", ""),
                    "visual_type": visual.get("visual_type", ""),
                    "visual_name": visual.get("visual_name")
                    or visual.get("title")
                    or visual.get("visual_type", ""),
                    "page_name": page_name,
                    "filters": [self._build_filter_entry(f, "visual", vkey) for f in vf],
                }

    def _build_filter_entry(self, filt: Dict, level: str, context: str) -> Dict[str, Any]:
        field = filt.get("field", {}) or {}
        table = field.get("table", "")
        name = field.get("name", "")
        fv = filt.get("filter_values", {}) or {}
        return {
            "name": filt.get("name", ""),
            "field_key": _key(table, name) if table and name else "",
            "field_type": field.get("type", "Unknown"),
            "table": table,
            "field_name": name,
            "how_created": filt.get("how_created", ""),
            "filter_type": filt.get("filter_type", filt.get("type", "")),
            "polarity": filt.get("polarity"),
            "level": level,
            "context": context,
            "filter_values": fv.get("values", []) if fv else [],
            "condition_type": fv.get("condition_type") if fv else None,
            "has_values": fv.get("has_values", False) if fv else False,
        }


def _fp_kind(meta: str) -> Optional[int]:
    if not meta:
        return None
    import json

    try:
        val = json.loads(meta).get("kind")
        return int(val) if val is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _split(key: str) -> Tuple[str, str]:
    i = key.find("[")
    return (key[:i], key[i + 1 : -1]) if i >= 0 else ("", key)
