"""
Visual Query Builder

Builds executable DAX queries that reproduce visual behavior by combining:
- PBIP report/page/visual filter definitions
- Slicer selections (saved state from PBIP)
- Live model query execution
"""

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.dax.dax_names import bracket, column_ref, dax_string, quote_table

from .filter_to_dax import (
    VALUE_PLACEHOLDER,
    FilterExpression,
    FilterToDaxConverter,
    calculate_filter_args,
)

# C-17: leaf definitions live in core.debug.visual_query_types (re-exported here).
from core.debug.visual_query_types import (  # noqa: F401
    DATA_VISUAL_TYPES,
    DEFAULT_TOPN,
    FilterContext,
    MeasureDefinition,
    SLICER_VISUAL_TYPES,
    SUBTOTAL_VISUAL_TYPES,
    SlicerState,
    UI_VISUAL_TYPES,
    VISUAL_TYPE_TOPN,
    VisualInfo,
    VisualQueryResult,
    _AGGREGATION_LABELS,
    _aggregation_expression,
    _aggregation_label,
    _iter_projections,
    _sanitize_alias,
    _walk_for_measure_refs,
    is_grand_total_row,
    row_value,
)
from core.debug.visual_report_reader import VisualReportReaderMixin
from core.debug.visual_measure_lookup import VisualMeasureLookupMixin

logger = logging.getLogger(__name__)


class VisualQueryBuilder(VisualMeasureLookupMixin, VisualReportReaderMixin):
    """
    Builds executable DAX queries that reproduce visual behavior.

    Combines PBIP analysis with live model capabilities to:
    - Extract complete filter context for any visual
    - Generate DAX queries with all filters applied
    - Support measure comparison and detail drilling
    """

    def __init__(self, pbip_folder_path: str):
        """pbip_folder_path: path to the PBIP project folder (contains definition/)."""
        self.pbip_folder = Path(pbip_folder_path)

        # Use the canonical resolver so VQB never drifts from handler-side
        # path resolution (was DUP-4: ad-hoc resolution missed nested
        # ``.Report`` and ``.pbip``-file inputs).
        from core.utilities.pbip_utils import find_definition_folder

        resolved = find_definition_folder(str(self.pbip_folder))
        if resolved is not None:
            self.definition_path = resolved
        else:
            # Fall back to the previous best-effort layout so callers that
            # construct VQB on a non-existent path during tests still get a
            # consistent attribute (debug methods will fail later with a
            # clearer error than AttributeError).
            self.definition_path = self.pbip_folder / "definition"
            if not self.definition_path.exists():
                report_path = self.pbip_folder / "report.json"
                if report_path.exists():
                    self.definition_path = self.pbip_folder
            logger.warning(
                "VisualQueryBuilder: could not resolve definition folder under %s",
                self.pbip_folder,
            )

        self.converter = FilterToDaxConverter()
        self.logger = logging.getLogger(__name__)
        #: F-38: one builder is shared by concurrent 09_* calls -- every cache
        #: mutation below happens under this lock.
        self._lock = threading.RLock()

        # Cache
        self._report_filters_cache: Optional[List[Dict]] = None
        self._slicers_cache: Dict[str, List[SlicerState]] = {}  # Per-page slicer cache
        self._page_filters_cache: Dict[str, List[Dict]] = {}  # Per-page filter cache
        self._page_path_cache: Dict[str, Optional[Path]] = {}  # Page name -> path cache
        self._column_types_loaded: bool = False
        self._query_executor = None  # Reference to connected query executor
        self._measure_cache: Dict[str, MeasureDefinition] = {}  # Cache measure definitions
        self._all_measures_loaded: bool = False  # Flag for batch measure loading
        #: Model generation the measure/format caches were loaded at (F-21c).
        self._model_generation: Optional[int] = None
        #: F-34: measure name -> has a dynamic format string (FormatStringDefinition).
        self._format_string_flags: Dict[str, bool] = {}
        self._format_string_flags_source: Optional[str] = None
        #: F-18: (table, column) -> sort-by column; None = not resolved yet.
        self._sort_by_map: Optional[Dict[Tuple[str, str], str]] = None
        self._semantic_model_path: Optional[Path] = None
        self._semantic_model_resolved = False
        #: P-15: TMDL file -> (size, mtime_ns, lower-cased text), validated by ``stat``
        #: on every use so an edited file is re-read.
        self._tmdl_text_cache: Dict[str, Tuple[int, int, str]] = {}

        # Advanced analysis components (lazy initialized)
        self._semantic_classifier = None
        self._relationship_resolver = None
        self._aggregation_matcher = None

    def _init_semantic_classifier(self):
        """Lazy initialize semantic classifier."""
        if self._semantic_classifier is None and self._query_executor:
            from .semantic_classifier import SemanticFilterClassifier

            self._semantic_classifier = SemanticFilterClassifier(self._query_executor)
        return self._semantic_classifier

    def _init_relationship_resolver(self):
        """Lazy initialize relationship resolver."""
        if self._relationship_resolver is None and self._query_executor:
            from .relationship_resolver import RelationshipResolver

            self._relationship_resolver = RelationshipResolver(self._query_executor)
        return self._relationship_resolver

    def _init_aggregation_matcher(self):
        """Lazy initialize aggregation matcher."""
        if self._aggregation_matcher is None and self._query_executor:
            from .aggregation_matcher import AggregationMatcher

            self._aggregation_matcher = AggregationMatcher(self._query_executor)
        return self._aggregation_matcher

    def _detect_format_string_measures_via_tom(
        self, measure_names: List[str], connection_string: str
    ) -> List[str]:
        """
        Detect dynamic format string companion measures via TOM/AMO.

        Power BI creates a hidden companion measure named `_<Name> FormatString`
        for every measure that has a dynamic format string set. This is exposed
        in TOM as Measure.FormatStringDefinition (compat level 1470+).

        Using AMO directly is more reliable than the DMV INFO.MEASURES() approach
        because Power BI Desktop may not surface private/hidden companion measures
        through the DMV.
        """
        # F-34: the flags are resolved ONCE per builder (and model generation):
        # INFO.MEASURES()[FormatStringDefinitionID] when the batch measure load
        # exposed it, else a single AMO walk -- never an AMO connect per visual.
        self._load_all_measures_from_dmv()
        with self._lock:
            source = self._format_string_flags_source
        if source is None:
            flags = self._format_string_flags_via_tom(connection_string)
            with self._lock:
                if flags is not None:
                    self._format_string_flags.update(flags)
                    self._format_string_flags_source = "tom"
                else:
                    self._format_string_flags_source = "unavailable"
        with self._lock:
            flags = dict(self._format_string_flags)
            tables = {k: v.table for k, v in self._measure_cache.items()}
        refs: List[str] = []
        for name in measure_names:
            entry = flags.get(name)
            if entry is None:
                entry = flags.get(name.lower())
            if not entry:
                continue
            table = (
                entry if isinstance(entry, str) else tables.get(name) or tables.get(name.lower())
            )
            if table:
                refs.append(quote_table(table) + bracket(f"_{name} FormatString"))
        return refs

    def _format_string_flags_via_tom(self, connection_string: str) -> Optional[Dict[str, str]]:
        """measure name -> table for every measure with a FormatStringDefinition (one walk)."""
        try:
            from core.infrastructure.dll_paths import load_amo_assemblies

            load_amo_assemblies()
            from Microsoft.AnalysisServices.Tabular import Server as _AMOServer
        except Exception:
            return None

        found: Dict[str, str] = {}
        server = _AMOServer()
        from core.infrastructure.xmla_target import prepare_amo_server

        prepare_amo_server(server)
        try:
            server.Connect(connection_string)
            if server.Databases.Count == 0:
                return None
            model = server.Databases[0].Model
            for table in model.Tables:
                for measure in table.Measures:
                    # FormatStringDefinition is only available at compat 1470+
                    if getattr(measure, "FormatStringDefinition", None) is not None:
                        found[str(measure.Name)] = str(table.Name)
                        found[str(measure.Name).lower()] = str(table.Name)
        except Exception as e:
            self.logger.debug(f"TOM format string detection failed: {e}")
            return None
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass
        return found

    def load_column_types(self, query_executor) -> int:
        """
        Load column data types from the connected model.

        Improves type detection for filter values (e.g., string "0" not int 0).

        F-38: the builder is shared across calls, so it binds only to the
        process-wide (admin) executor. An RLS-impersonated executor passed by a
        ``roles=[...]`` call is used for this load only and never stored --
        otherwise later admin calls would classify filters and load measures
        through a role-restricted connection.
        """
        if query_executor is None:
            return 0
        with self._lock:
            if self._is_shared_executor(query_executor):
                self._query_executor = query_executor
            self._check_model_generation()
            if self._column_types_loaded:
                return 0

        count = self.converter.load_column_types_from_model(query_executor)
        if count > 0:
            with self._lock:
                self._column_types_loaded = True
        return count

    @staticmethod
    def _is_shared_executor(query_executor) -> bool:
        """False for an RLS-impersonated executor (``roles=[...]`` calls)."""
        try:
            from core.infrastructure.impersonated_executor import ImpersonatedExecutor

            return not isinstance(query_executor, ImpersonatedExecutor)
        except Exception:  # noqa: BLE001
            return True

    def _check_model_generation(self) -> None:
        """Drop the measure / format-string / sort-by caches after a model write (F-21c)."""
        try:
            from core.infrastructure.connection_state import connection_state as _cs

            generation = getattr(_cs, "model_generation", None)
        except Exception:  # noqa: BLE001
            generation = None
        if not isinstance(generation, int):
            return
        with self._lock:
            if self._model_generation is None:
                self._model_generation = generation
                return
            if generation != self._model_generation:
                self._model_generation = generation
                self._measure_cache.clear()
                self._all_measures_loaded = False
                self._format_string_flags.clear()
                self._format_string_flags_source = None
                self._sort_by_map = None
                self._column_types_loaded = False
                self._semantic_classifier = None
                self._aggregation_matcher = None
                self._relationship_resolver = None

    def get_visual_filter_context(
        self,
        page_name: str,
        visual_id: Optional[str] = None,
        visual_name: Optional[str] = None,
        include_slicers: bool = True,
    ) -> Tuple[Optional[VisualInfo], FilterContext]:
        """
        Get complete filter context for a visual; returns (VisualInfo, FilterContext) or (None, empty).
        """
        filter_context = FilterContext()

        # Find the page
        page_path = self._find_page_by_name(page_name)
        if not page_path:
            self.logger.warning(f"Page not found: {page_name}")
            return None, filter_context

        # Find the visual
        visual_info = self._find_visual(page_path, visual_id, visual_name)
        if not visual_info:
            self.logger.warning(f"Visual not found: id={visual_id}, name={visual_name}")
            return None, filter_context

        def _convert(entries, source, target_list, visual_json=None):
            for entry in entries:
                expr, issue = self.converter.convert_filter_detailed(
                    entry, source=source, visual_json=visual_json
                )
                if expr is not None:
                    target_list.append(expr)
                elif issue is not None:
                    bucket = (
                        filter_context.inactive
                        if issue.get("inactive")
                        else (filter_context.unsupported)
                    )
                    bucket.append(issue)

        # 1. Report-level filters
        _convert(self._get_report_filters(), "report", filter_context.report_filters)

        # 2. Page-level filters
        _convert(self._get_page_filters(page_path), "page", filter_context.page_filters)

        # 3. Visual-level filters (top-level filterConfig.filters, F-03) -- the
        # visual JSON is passed so a visual-level inverted flag flips polarity.
        _convert(
            visual_info.filters,
            "visual",
            filter_context.visual_filters,
            visual_json=visual_info.visual_json or None,
        )

        # 4. Get slicer selections
        if include_slicers:
            slicers = self._get_page_slicers(page_path, page_name)
            no_filter_sources = self._no_filter_sources(page_path, visual_info)
            for slicer in slicers:
                if slicer.slicer_id in no_filter_sources:
                    # visualInteractions type NoFilter: this slicer does not
                    # filter the target visual (F-39(4)).
                    filter_context.inactive.append(
                        {
                            "name": slicer.slicer_id,
                            "source": "slicer",
                            "table": slicer.table,
                            "column": slicer.column,
                            "reason": "interaction set to NoFilter for this visual",
                            "inactive": True,
                        }
                    )
                    continue
                slicer_info = {
                    "entity": slicer.table,
                    "property": slicer.column,
                    "selected_values": slicer.selected_values,
                    "is_inverted_selection": slicer.is_inverted,
                    "selection_mode": slicer.selection_mode,
                    "extra_columns": slicer.extra_columns,
                    "composite_values": slicer.composite_values,
                }
                expr = self.converter.convert_slicer_selection(slicer_info)
                if expr:
                    filter_context.slicer_filters.append(expr)

        return visual_info, filter_context

    def _no_filter_sources(self, page_path: Path, visual_info: VisualInfo) -> set:
        """Source visual names whose interaction with the target is ``NoFilter``."""
        try:
            from core.pbip.pbir_model import get_page_interactions

            with open(page_path / "page.json", "r", encoding="utf-8") as fh:
                page_json = json.load(fh)
        except (OSError, ValueError):
            return set()
        target_names = {visual_info.visual_id, visual_info.visual_name}
        out = set()
        for item in get_page_interactions(page_json):
            if item.get("target") in target_names and str(item.get("type")) == "NoFilter":
                out.add(item.get("source"))
        return out

    @staticmethod
    def _as_measure_ref(m: str) -> str:
        """``Name`` -> ``[Name]``; bracketed refs and implicit-measure expressions as-is."""
        m = m.strip()
        if m.startswith("[") or m.startswith("'") or "(" in m:
            return m
        return bracket(m)

    def build_visual_query(
        self,
        page_name: str,
        visual_id: Optional[str] = None,
        visual_name: Optional[str] = None,
        measure_name: Optional[str] = None,
        include_slicers: bool = True,
        expand_measures: bool = True,
        measures: Optional[List[str]] = None,
    ) -> Optional[VisualQueryResult]:
        """
        Build a DAX query that reproduces what a visual shows.
        """
        visual_info, filter_context = self.get_visual_filter_context(
            page_name, visual_id, visual_name, include_slicers
        )

        if not visual_info:
            return None

        # Determine measures to use (explicit list > single name > visual auto-discovery)
        if measures:
            target_measures = [self._as_measure_ref(m) for m in measures]
        elif measure_name:
            target_measures = [self._as_measure_ref(measure_name)]
        elif visual_info.measures:
            target_measures = [self._as_measure_ref(m) for m in visual_info.measures]
        else:
            self.logger.warning("No measure specified and visual has no measures")
            return None

        # Get grouping columns from the visual
        grouping_columns = list(visual_info.columns) if visual_info.columns else []

        # Resolve SortByColumn — PBI includes sort columns in group-by, TOPN, ORDER BY.
        # grouping_levels keeps a sort-by helper in the same subtotal level as its target.
        grouping_levels: List[List[str]] = [[c] for c in grouping_columns]
        if grouping_columns:
            sort_pairs = self._resolve_sort_by_columns(grouping_columns)
            if sort_pairs:
                expanded: List[str] = []
                grouping_levels = []
                added = set()
                for col in grouping_columns:
                    level: List[str] = []
                    for sort_ref, target_ref in sort_pairs:
                        if target_ref == col and sort_ref not in added:
                            expanded.append(sort_ref)
                            level.append(sort_ref)
                            added.add(sort_ref)
                    expanded.append(col)
                    level.append(col)
                    grouping_levels.append(level)
                grouping_columns = expanded

        # All filters assembled here; _build_visual_dax_query handles classification
        all_filters = filter_context.all_filters()

        # For backward compatibility, use first measure as the target
        target_measure = target_measures[0]

        # Table-qualified refs straight from the visual's own projections (works offline).
        measure_table_map: Dict[str, str] = {}
        for prop, entity in (getattr(visual_info, "measure_entities", {}) or {}).items():
            if entity:
                measure_table_map[prop] = entity

        # Include grouped object measures (data labels, conditional formatting)
        # These are measures bound to visual formatting properties, not in queryState.
        # PBI wraps these in IGNORE() inside SUMMARIZECOLUMNS so they don't participate
        # in auto-exist filtering (rows aren't removed when these return BLANK).
        object_measure_table_map: Dict[str, str] = {}
        object_measure_names: set = set()  # Track which measures need IGNORE()
        if visual_info.object_measures:
            for entity, prop in visual_info.object_measures:
                ref = bracket(prop)
                if ref not in target_measures:
                    target_measures.append(ref)
                    object_measure_names.add(prop)
                    if entity:
                        object_measure_table_map[prop] = entity

        # Scalar object measures (titles, axis bounds, reference lines) go in second EVALUATE
        scalar_measure_refs: List[str] = []
        scalar_measure_table_map: Dict[str, str] = {}
        if visual_info.scalar_measures:
            for entity, prop in visual_info.scalar_measures:
                ref = bracket(prop)
                if ref not in target_measures and ref not in scalar_measure_refs:
                    scalar_measure_refs.append(ref)
                    if entity:
                        scalar_measure_table_map[prop] = entity

        # Fetch actual measure expressions from the model (needed for table-qualified
        # refs, format-string detection, and the expanded query)
        measure_definitions = []
        expanded_query = None
        format_string_measures: List[str] = []
        # Must be initialized OUTSIDE the block below: it is consumed unconditionally by
        # _build_visual_dax_query(). Offline / no-executor paths skip the block entirely.
        scalar_format_string_measures: List[str] = []
        measure_table_map.update(object_measure_table_map)

        if expand_measures and self._query_executor:
            measure_defs = self.get_measure_expressions(
                [m for m in target_measures if m.startswith("[")]
            )
            measure_definitions = list(measure_defs.values())

            # Table-qualified measure references (e.g. 'm Measure'[Net Asset Value])
            for m in measure_definitions:
                if m.table:
                    measure_table_map.setdefault(m.name, m.table)

            # Detect dynamic format string companion measures.
            # Primary: INFO.MEASURES FormatStringDefinitionID / one AMO walk (cached).
            # Fallback: TMDL file scanning (PBIP offline projects, no live connection needed).
            conn_str = None
            try:
                from core.infrastructure.connection_state import connection_state as _cs

                cm = getattr(_cs, "connection_manager", None)
                if cm:
                    conn_str = getattr(cm, "connection_string", None)
            except Exception:
                pass

            all_measure_names = [m.name for m in measure_definitions]
            scalar_measure_names = [self._unbracket(ref) for ref in scalar_measure_refs]
            all_detect_names = all_measure_names + scalar_measure_names

            if conn_str and all_detect_names:
                all_format_strings = self._detect_format_string_measures_via_tom(
                    all_detect_names, conn_str
                )
            else:
                all_format_strings = []

            if not all_format_strings and measure_definitions:
                all_format_strings = self._detect_dynamic_format_string_measures(
                    measure_definitions
                )

            # Split format strings: scalar measure companions go to second EVALUATE
            scalar_fs_set = {n for n in scalar_measure_names}
            for fs_ref in all_format_strings:
                base = self._format_string_base(fs_ref)
                if base is not None and base in scalar_fs_set:
                    scalar_format_string_measures.append(fs_ref)
                else:
                    format_string_measures.append(fs_ref)

            # Merge scalar measure table map into main for format string resolution
            measure_table_map.update(scalar_measure_table_map)

            # Build expanded query with actual measure DAX
            if measure_definitions:
                expanded_query = self._build_expanded_dax_query(
                    measure_definitions, grouping_columns, all_filters
                )

        # Use ROLLUPADDISSUBTOTAL for matrix/table visuals (they have grand total rows)
        include_grand_total = getattr(visual_info, "visual_type", None) in SUBTOTAL_VISUAL_TYPES

        # Resolve TOPN limit from visual type (PBI uses different defaults per type)
        row_limit = VISUAL_TYPE_TOPN.get(visual_info.visual_type, DEFAULT_TOPN)

        # Build the PBI-accurate DEFINE/EVALUATE query with TREATAS filter vars
        build_kwargs: Dict[str, Any] = dict(
            measure_table_map=measure_table_map,
            include_grand_total=include_grand_total,
            row_limit=row_limit,
            format_string_measures=format_string_measures,
            scalar_measures=scalar_measure_refs,
            scalar_format_string_measures=scalar_format_string_measures,
            scalar_measure_table_map=scalar_measure_table_map,
            ignore_measure_names=object_measure_names,
            visual_type=visual_info.visual_type,
            grouping_levels=grouping_levels,
            row_columns=visual_info.row_columns,
            sort_definition=visual_info.sort_definition,
            measure_aliases=visual_info.measure_aliases,
            row_subtotals=visual_info.row_subtotals,
        )
        dax_query, build_meta = self._build_visual_dax_query_ex(
            target_measures, grouping_columns, all_filters, **build_kwargs
        )
        build_kwargs["measures"] = list(target_measures)
        build_kwargs["grouping_columns"] = list(grouping_columns)

        def _breakdown(items: List[FilterExpression]) -> List[Dict[str, Any]]:
            return [
                {
                    "table": f.table,
                    "column": f.column,
                    "type": f.condition_type,
                    "values": f.values,
                    "dax": f.dax,
                    "polarity": getattr(f, "polarity", "include"),
                }
                for f in items
            ]

        # Build filter breakdown for documentation
        filter_breakdown = {
            "report_level": _breakdown(filter_context.report_filters),
            "page_level": _breakdown(filter_context.page_filters),
            "visual_level": _breakdown(filter_context.visual_filters),
            "slicer": _breakdown(filter_context.slicer_filters),
        }

        notes: List[str] = list(build_meta.get("notes", []))
        for f in all_filters:
            for note in getattr(f, "notes", []) or []:
                if note not in notes:
                    notes.append(note)
        unsupported = list(filter_context.unsupported)
        if visual_info.roles.get("_unscanned"):
            notes.append(f"unrecognised query roles skipped: {visual_info.roles['_unscanned']}")

        return VisualQueryResult(
            visual_info=visual_info,
            filter_context=filter_context,
            dax_query=dax_query,
            measure_name=target_measure,
            measure_definitions=measure_definitions,
            expanded_query=expanded_query,
            filter_breakdown=filter_breakdown,
            format_string_measures=format_string_measures,
            fidelity="partial" if unsupported else "full",
            unsupported_filters=unsupported,
            fidelity_notes=notes,
            measure_aliases=build_meta.get("aliases", {}),
            build_kwargs=build_kwargs,
        )

    def rebuild_query(
        self,
        result: "VisualQueryResult",
        filters: List,
        *,
        measures: Optional[List[str]] = None,
    ) -> str:
        """Rebuild *result*'s query with a different filter list (F-06).

        Keeps everything else the visual query was built with -- grand-total
        rollup, the per-type row limit, format-string companions, IGNORE()
        object measures, sort order -- so a manual-filter or retry query differs
        from the original ONLY in its filters.
        """
        kwargs = dict(result.build_kwargs or {})
        base_measures = kwargs.pop("measures", None) or [
            self._as_measure_ref(m) for m in (result.visual_info.measures or [result.measure_name])
        ]
        grouping = kwargs.pop("grouping_columns", None)
        if grouping is None:
            grouping = list(result.visual_info.columns or [])
        query, _meta = self._build_visual_dax_query_ex(
            measures if measures is not None else base_measures, grouping, filters, **kwargs
        )
        return query

    @staticmethod
    def _unbracket(ref: str) -> str:
        from core.dax.dax_names import unbracket

        return unbracket(ref)

    @staticmethod
    def _format_string_base(fs_ref: str) -> Optional[str]:
        """``'T'[_WF6-Minimum FormatString]`` -> ``WF6-Minimum``."""
        from core.dax.dax_names import split_qualified

        _table, col = split_qualified(fs_ref)
        if not col.startswith("_") or not col.endswith(" FormatString"):
            return None
        return col[1 : -len(" FormatString")]

    @staticmethod
    def _merge_same_column_filters(filters: list) -> list:
        """INTERSECT include-list filters on the same table+column (F-05).

        Separate filters on one column (report + page + visual + slicer levels,
        or two slicers) are ANDed by Power BI, so the combined value set is the
        INTERSECTION -- never the union. Only plain single-column include lists
        (TREATAS shapes) are merged, purely for readability; everything else is
        emitted as its own filter argument and the engine intersects them. An
        empty intersection is left as separate filters (the engine returns no
        rows, exactly like the visual).
        """
        from collections import OrderedDict

        def _literals(f) -> Optional[List[str]]:
            lits = getattr(f, "literals", None)
            if lits is not None:
                return list(lits)
            if f.values is None:
                return None
            return [_format_literal(v) for v in f.values]

        def _format_literal(v: Any) -> str:
            return VisualQueryBuilder._format_value_static(v)

        groups: "OrderedDict[tuple, list]" = OrderedDict()
        order: List[Any] = []
        for f in filters:
            mergeable = (
                not f.composite_columns
                and f.composite_tuples is None
                and f.condition_type == "In"
                and getattr(f, "polarity", "include") == "include"
                and not getattr(f, "is_field_parameter", False)
                and not getattr(f, "is_measure_filter", False)
                and f.table
                and f.column
            )
            if not mergeable:
                order.append(f)
                continue
            key = (f.table, f.column)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(f)

        result: list = []
        for item in order:
            if not isinstance(item, tuple):
                result.append(item)
                continue
            group = groups[item]
            if len(group) == 1:
                result.append(group[0])
                continue
            sets = [_literals(f) for f in group]
            if any(s is None for s in sets):
                result.extend(group)
                continue
            common = [lit for lit in sets[0] if all(lit in s for s in sets[1:])]
            has_null = all(getattr(f, "has_null_values", False) for f in group)
            if not common and not has_null:
                result.extend(group)
                continue
            base = group[0]
            ref = column_ref(base.table, base.column)
            values_set = ", ".join(common + (["BLANK()"] if has_null else []))
            parts = []
            if has_null:
                parts.append(f"ISBLANK({ref})")
            if common:
                parts.append(f"{ref} IN {{{', '.join(common)}}}")
            dax = parts[0] if len(parts) == 1 else "(" + " || ".join(parts) + ")"
            common_values = [v for v, lit in zip(base.values or [], sets[0]) if lit in common]
            result.append(
                FilterExpression(
                    dax=dax,
                    source="+".join(dict.fromkeys(f.source for f in group)),
                    table=base.table,
                    column=base.column,
                    condition_type="In",
                    values=common_values,
                    original=base.original,
                    is_field_parameter=False,
                    classification=base.classification,
                    has_null_values=has_null,
                    polarity="include",
                    filter_table_dax=f"TREATAS({{{values_set}}}, {ref})",
                    literals=common,
                    columns=[ref],
                    notes=[f"{len(group)} filters on {ref} intersected"],
                )
            )
        return result

    @staticmethod
    def _format_value_static(val: Any) -> str:
        return VisualQueryBuilder._format_value_for_treatas(None, val)  # type: ignore[arg-type]

    def _format_value_for_treatas(self, val: Any) -> str:
        """Format a single value for inclusion in a TREATAS set literal.

        Handles TypedValue wrappers (duck-typed) as well as raw Python scalars.
        """
        # Duck-type TypedValue (has .value + .value_type attributes)
        if hasattr(val, "value") and hasattr(val, "value_type"):
            if val.value is None:
                return "BLANK()"
            raw, vt = val.value, val.value_type
            if vt == "string":
                escaped = str(raw).replace('"', '""')
                return f'"{escaped}"'
            if vt == "date":
                s = str(raw)
                if len(s) >= 10:
                    return f"DATE({s[:4]}, {int(s[5:7])}, {int(s[8:10])})"
                return f'"{s}"'
            if vt == "boolean":
                return "TRUE" if str(raw).lower() == "true" else "FALSE"
            if vt in ("integer", "decimal"):
                return str(raw)
            val = raw  # unknown sub-type — fall through
        if val is None:
            return "BLANK()"
        if isinstance(val, bool):
            return "TRUE" if val else "FALSE"
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return str(val)
        # Catch raw datetime literals that weren't wrapped in TypedValue
        str_val = str(val)
        dt_match = re.match(r"datetime'(\d{4})-(\d{2})-(\d{2})T[\d:.]+'", str_val)
        if dt_match:
            return f"DATE({dt_match.group(1)}, {int(dt_match.group(2))}, {int(dt_match.group(3))})"
        escaped = str_val.replace('"', '""')
        return f'"{escaped}"'

    def _format_values_as_treatas_set(self, values: List[Any], include_blank: bool = False) -> str:
        """Format a list of filter values as a DAX set literal: {v1, v2, ...}"""
        parts = [self._format_value_for_treatas(v) for v in (values or [])]
        if include_blank and "BLANK()" not in parts:
            parts.append("BLANK()")
        return "{" + ", ".join(parts) + "}" if parts else "{BLANK()}"

    def _build_composite_treatas(self, f) -> str:
        """Build a multi-column composite TREATAS expression.

        Produces:
            TREATAS(
                {("Cat1", "Field1"), ("Cat2", "Field2")},
                'Table'[Column1],
                'Table'[Column2]
            )
        """
        rows = []
        for tuple_vals in f.composite_tuples or []:
            parts = [self._format_value_for_treatas(v) for v in tuple_vals]
            rows.append("(" + ", ".join(parts) + ")")

        values_set = "{" + ",\n                ".join(rows) + "}" if rows else "{}"
        col_refs = ",\n            ".join(f.composite_columns)
        return f"TREATAS(\n            {values_set},\n            {col_refs}\n        )"

    def _filter_var_expression(self, f) -> Optional[str]:
        """The filter-table DAX for one column filter, or None when it is not one."""
        if f.composite_columns and f.composite_tuples is not None and not f.filter_table_dax:
            return self._build_composite_treatas(f)
        if getattr(f, "filter_table_dax", None):
            return f.filter_table_dax
        # Hand-built FilterExpression without the new metadata (legacy callers).
        if f.table and f.column:
            ref = column_ref(f.table, f.column)
            if f.condition_type == "In" and getattr(f, "polarity", "include") == "include":
                values_set = self._format_values_as_treatas_set(
                    f.values, include_blank=f.has_null_values
                )
                return f"TREATAS({values_set}, {ref})"
            if f.dax:
                return f"FILTER(KEEPFILTERS(VALUES({ref})), {f.dax})"
        return None

    def _prepare_filter_vars(
        self, filters: List
    ) -> Tuple[List[Tuple[str, str]], List, List, Dict[str, int]]:
        """Classify *filters* and build the filter-var declarations.

        Returns ``(filter_vars, value_filters, manual_filters, counts)`` where
        ``filter_vars`` is ``[(var_name, table_expression)]`` -- column filters
        (merged same-column include lists) followed by TopN filters, which are
        evaluated under the column filters.
        """
        from .filter_to_dax import FilterClassification

        data_filters: List = []
        field_param_filters: List = []
        topn_filters: List = []
        value_filters: List = []
        manual_filters: List = []

        for f in filters:
            classification = getattr(f, "classification", FilterClassification.DATA)
            if classification == FilterClassification.UI_CONTROL:
                continue  # formatting/display only — never affects data
            if f.condition_type == "Manual":
                if f.dax:
                    manual_filters.append(f)
                continue
            if getattr(f, "is_measure_filter", False):
                value_filters.append(f)
                continue
            if getattr(f, "topn", None):
                topn_filters.append(f)
                continue
            if not (f.dax or getattr(f, "filter_table_dax", None) or f.composite_columns):
                continue
            if classification == FilterClassification.FIELD_PARAMETER:
                field_param_filters.append(f)
            else:
                data_filters.append(f)

        # Data filters first, then field parameter filters (matches PBI ordering)
        active_filters = self._merge_same_column_filters(data_filters + field_param_filters)

        filter_vars: List[Tuple[str, str]] = []

        def _next_var() -> str:
            n = len(filter_vars)
            return "__DS0FilterTable" if n == 0 else f"__DS0FilterTable{n + 1}"

        for f in active_filters:
            expr = self._filter_var_expression(f)
            if expr is None:
                if f.dax:
                    manual_filters.append(f)
                continue
            filter_vars.append((_next_var(), expr))

        # TopN filters see the other column filters (evaluated after them).
        for f in topn_filters:
            spec = f.topn
            col = spec["column_ref"]
            others = ", ".join(v for v, _ in filter_vars)
            inner = f"SUMMARIZECOLUMNS({col}{', ' + others if others else ''}, "
            inner += f"\"__TopNValue\", {spec['order_expression']})"
            direction = "DESC" if spec.get("descending", True) else "ASC"
            _t, col_name = self._split_ref(col)
            expr = (
                f"SELECTCOLUMNS(TOPN({int(spec['n'])}, {inner}, [__TopNValue], {direction}), "
                f"{dax_string(col_name)}, {col})"
            )
            filter_vars.append((_next_var(), expr))

        counts = {
            "manual_filters": len(manual_filters),
            "value_filters": len(value_filters),
            "topn_filters": len(topn_filters),
        }
        return filter_vars, value_filters, manual_filters, counts

    def build_total_query(
        self,
        result: "VisualQueryResult",
        measure: Optional[str] = None,
        *,
        filters: Optional[List] = None,
    ) -> Tuple[str, str]:
        """``(query, alias)`` evaluating *measure* over everything the visual shows (F-15).

        One ``ROW`` = the measure in the visual's full filter context: column
        filters, TopN, manual filters and value filters. A value filter keeps
        the visual's GROUPED semantics -- it is built over the visual's group-by
        columns and passed as a CALCULATE filter, so the total covers exactly
        the rows/categories the visual displays. Use this instead of
        ``list(row.values())[0]`` of the visual query, which is the first
        GROUP-BY column (or BLANK on a matrix grand-total row).
        """
        kwargs = result.build_kwargs or {}
        m = self._as_measure_ref(measure or result.measure_name)
        table_map = kwargs.get("measure_table_map") or {}
        m_name = self._unbracket(m) if m.startswith("[") else m
        m_ref = (
            column_ref(table_map[m_name], m_name)
            if m.startswith("[") and m_name in table_map
            else m
        )
        grouping = list(kwargs.get("grouping_columns") or result.visual_info.columns or [])
        if filters is None:
            filters = result.filter_context.all_filters()
        filter_vars, value_filters, manual_filters, _counts = self._prepare_filter_vars(filters)
        var_refs = [vn for vn, _ in filter_vars]
        manual_args = [f"KEEPFILTERS({f.dax})" for f in manual_filters]
        define = [f"    VAR {vn} =\n        {expr}" for vn, expr in filter_vars]
        calc_args = var_refs + manual_args
        conditions: List[str] = []
        for i, vf in enumerate(value_filters):
            pred = (vf.value_predicate or "TRUE()").replace(VALUE_PLACEHOLDER, f"[__VF{i}]")
            if grouping:
                args = [", ".join(grouping)] + var_refs + [f'"__VF{i}"', vf.measure_expression]
                body = f"SUMMARIZECOLUMNS({', '.join(args)})"
                if manual_args:
                    body = f"CALCULATETABLE({body}, {', '.join(manual_args)})"
                define.append(
                    f"    VAR __ValueFilterDM{i} =\n        FILTER(KEEPFILTERS({body}), {pred})"
                )
                calc_args.append(f"__ValueFilterDM{i}")
            else:
                inner = (
                    f"CALCULATE({vf.measure_expression}, {', '.join(var_refs + manual_args)})"
                    if var_refs or manual_args
                    else vf.measure_expression
                )
                define.append(
                    f'    VAR __ValueFilterDM{i} =\n        NOT ISEMPTY(FILTER(ROW("__VF{i}", '
                    f"{inner}), {pred}))"
                )
                conditions.append(f"__ValueFilterDM{i}")
        alias = "Value"
        value = f"CALCULATE({m_ref}, {', '.join(calc_args)})" if calc_args else m_ref
        if conditions:
            value = f"IF({' && '.join(conditions)}, {value})"
        body = f"EVALUATE\nROW({dax_string(alias)}, {value})"
        if define:
            return "DEFINE\n" + "\n".join(define) + "\n\n" + body, alias
        return body, alias

    def _build_visual_dax_query_ex(
        self,
        measures: List[str],
        grouping_columns: List[str],
        filters: List,
        fact_table: Optional[str] = None,
        measure_table_map: Optional[Dict[str, str]] = None,
        include_grand_total: bool = False,
        row_limit: int = DEFAULT_TOPN,
        format_string_measures: Optional[List[str]] = None,
        scalar_measures: Optional[List[str]] = None,
        scalar_format_string_measures: Optional[List[str]] = None,
        scalar_measure_table_map: Optional[Dict[str, str]] = None,
        ignore_measure_names: Optional[set] = None,
        *,
        visual_type: Optional[str] = None,
        grouping_levels: Optional[List[List[str]]] = None,
        row_columns: Optional[List[str]] = None,
        sort_definition: Optional[List[Dict[str, Any]]] = None,
        measure_aliases: Optional[Dict[str, str]] = None,
        row_subtotals: Optional[bool] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Build a DAX query matching Power BI's DEFINE/EVALUATE + SUMMARIZECOLUMNS structure.

        Filter arguments (F-02..F-06):

        * plain include lists -> ``VAR __DS0FilterTableN = TREATAS({...}, 'T'[C])``;
        * every other column predicate (NOT IN / inverted, comparisons, ranges,
          relative dates, blanks) -> ``FILTER(KEEPFILTERS(VALUES('T'[C])), <pred>)``;
        * TopN -> the top N members of the column under the other filters;
        * measure (value) filters -> ``__ValueFilterDM<n> = FILTER(KEEPFILTERS(
          SUMMARIZECOLUMNS(<group-by>, <filters>, "__VF<n>", <measure>)), <pred>)``;
        * manual predicates -> ``CALCULATETABLE(<core>, KEEPFILTERS(<dax>), ...)``.

        Filters on the same column are separate arguments, i.e. INTERSECTED (F-05).
        Card/KPI visuals (no grouping columns) emit a ROW() query instead.
        Returns ``(query, meta)``; meta carries ``aliases`` (measure ref -> column
        alias), ``notes`` and counters.
        """

        meta: Dict[str, Any] = {"notes": [], "aliases": {}}
        aliases_in = measure_aliases or {}

        filter_vars, value_filters, manual_filters, counts = self._prepare_filter_vars(filters)
        filter_var_refs = [vn for vn, _ in filter_vars]
        manual_args = [f"KEEPFILTERS({f.dax})" for f in manual_filters]
        meta.update(counts)

        # --- measure refs + aliases ---
        _ignore_names = ignore_measure_names or set()
        measure_entries: List[Tuple[str, str, str, bool]] = []  # (orig, ref, alias, ignore)
        used_aliases: set = set()
        for m in measures:
            m_name = self._unbracket(m) if m.startswith("[") else m
            alias = aliases_in.get(m) or _sanitize_alias(m_name)
            base_alias = alias
            k = 2
            while alias in used_aliases:
                alias = f"{base_alias}_{k}"
                k += 1
            used_aliases.add(alias)
            if m.startswith("[") and measure_table_map and m_name in measure_table_map:
                m_ref = column_ref(measure_table_map[m_name], m_name)
            else:
                m_ref = m
            measure_entries.append((m, m_ref, alias, m_name in _ignore_names))
            meta["aliases"][m] = alias

        def _fs_alias(fs_ref: str) -> str:
            _t, fs_name = self._split_ref(fs_ref)
            return "v_" + _sanitize_alias(fs_name) if fs_name else "v_FormatString"

        # --- 3. No grouping columns → ROW() query for card/KPI visuals ---
        if not grouping_columns:
            define_lines = [f"    VAR {vn} =\n        {expr}" for vn, expr in filter_vars]
            calc_args = filter_var_refs + manual_args
            condition_vars: List[str] = []
            for i, vf in enumerate(value_filters):
                cond_var = f"__ValueFilterDM{i}"
                inner_m = (
                    f"CALCULATE({vf.measure_expression}, {', '.join(calc_args)})"
                    if calc_args
                    else vf.measure_expression
                )
                pred = (vf.value_predicate or "TRUE()").replace(VALUE_PLACEHOLDER, f"[__VF{i}]")
                define_lines.append(
                    f'    VAR {cond_var} =\n        NOT ISEMPTY(FILTER(ROW("__VF{i}", '
                    f"{inner_m}), {pred}))"
                )
                condition_vars.append(cond_var)
            row_parts = []
            for _orig, m_ref, alias, _ign in measure_entries:
                value = f"CALCULATE({m_ref}, {', '.join(calc_args)})" if calc_args else m_ref
                if condition_vars:
                    value = f"IF({' && '.join(condition_vars)}, {value})"
                row_parts.append(f"    {dax_string(alias)}, {value}")
            row_body = ",\n".join(row_parts)
            if define_lines:
                return (
                    "DEFINE\n" + "\n".join(define_lines) + f"\n\nEVALUATE\nROW(\n{row_body}\n)",
                    meta,
                )
            return f"EVALUATE\nROW(\n{row_body}\n)", meta

        # --- 4. SUMMARIZECOLUMNS group-by ---
        levels = grouping_levels or [[c] for c in grouping_columns]
        known = {c for lvl in levels for c in lvl}
        levels = levels + [[c] for c in grouping_columns if c not in known]
        rows_set = set(row_columns or [])
        subtotal_flags: List[Tuple[str, List[str]]] = []  # (flag, level columns)
        sc_args: List[str] = []

        def _group(cols: List[str]) -> str:
            return cols[0] if len(cols) == 1 else f"ROLLUPGROUP({', '.join(cols)})"

        if include_grand_total:
            is_matrix = (visual_type or "") in ("pivotTable", "matrix")
            row_levels = (
                [lvl for lvl in levels if any(c in rows_set for c in lvl)]
                if (is_matrix and rows_set)
                else list(levels)
            )
            other_levels = [lvl for lvl in levels if lvl not in row_levels]
            per_level = is_matrix and row_subtotals is not False and len(row_levels) > 1
            if per_level:
                parts = []
                for i, lvl in enumerate(row_levels):
                    flag = "IsGrandTotalRowTotal" if i == 0 else f"IsDM{i}Total"
                    subtotal_flags.append((flag, lvl))
                    parts.append(f"{_group(lvl)}, {dax_string(flag)}")
                sc_args.append(f"        ROLLUPADDISSUBTOTAL({', '.join(parts)})")
                meta["notes"].append("matrix row subtotals emitted per level (IsDM<n>Total)")
            else:
                flat = [c for lvl in row_levels for c in lvl]
                if flat:
                    subtotal_flags.append(("IsGrandTotalRowTotal", flat))
                    sc_args.append(
                        f"        ROLLUPADDISSUBTOTAL({_group(flat)}, "
                        f"{dax_string('IsGrandTotalRowTotal')})"
                    )
            for lvl in other_levels:
                for col in lvl:
                    sc_args.append(f"        {col}")
        else:
            for col in grouping_columns:
                sc_args.append(f"        {col}")

        # 4b. Filter table references (direct SUMMARIZECOLUMNS args — not CALCULATETABLE)
        for var_name in filter_var_refs:
            sc_args.append(f"        {var_name}")

        # 4c. Value (measure) filters on the visual's grouped rows.
        value_define: List[str] = []
        plain_group = ", ".join(grouping_columns)
        for i, vf in enumerate(value_filters):
            vf_var = f"__ValueFilterDM{i}"
            args = [plain_group] + filter_var_refs + [f'"__VF{i}"', vf.measure_expression]
            body = f"SUMMARIZECOLUMNS({', '.join(args)})"
            if manual_args:
                body = f"CALCULATETABLE({body}, {', '.join(manual_args)})"
            pred = (vf.value_predicate or "TRUE()").replace(VALUE_PLACEHOLDER, f"[__VF{i}]")
            value_define.append(f"    VAR {vf_var} =\n        FILTER(KEEPFILTERS({body}), {pred})")
            sc_args.append(f"        {vf_var}")

        # 4d. Measure name-expression pairs (table-qualified when available)
        # Object measures (data labels, conditional formatting) are wrapped in IGNORE()
        # so they don't participate in auto-exist filtering — PBI removes rows where
        # non-IGNORE measures return BLANK, but keeps rows for IGNORE-wrapped ones.
        for _orig, m_ref, alias, ignore in measure_entries:
            if ignore:
                sc_args.append(f"        {dax_string(alias)}, IGNORE({m_ref})")
            else:
                sc_args.append(f"        {dax_string(alias)}, {m_ref}")

        # 4e. Format string companion measures wrapped in IGNORE()
        for fs_ref in format_string_measures or []:
            sc_args.append(f"        {dax_string(_fs_alias(fs_ref))}, IGNORE({fs_ref})")

        sc_body = ",\n".join(sc_args)

        # --- 5. Build DEFINE block ---
        define_lines: List[str] = []
        for var_name, expr in filter_vars:
            define_lines.append(f"    VAR {var_name} =\n        {expr}")
        define_lines.extend(value_define)

        core = f"SUMMARIZECOLUMNS(\n{sc_body}\n        )"
        if manual_args:
            core = f"CALCULATETABLE(\n        {core},\n        {', '.join(manual_args)}\n        )"
        define_lines.append(f"    VAR __DS0Core =\n        {core}")

        # --- TOPN window: subtotal flags, the visual's sortDefinition, then group-by ---
        alias_by_name = {
            self._unbracket(o) if o.startswith("[") else o: a for o, _r, a, _i in measure_entries
        }
        topn_sort_parts: List[str] = []
        order_by_parts: List[str] = []
        sorted_cols: set = set()

        if subtotal_flags and len(subtotal_flags) == 1:
            topn_sort_parts.append(f"[{subtotal_flags[0][0]}], 0")
            order_by_parts.append(f"[{subtotal_flags[0][0]}] DESC")

        for entry in sort_definition or []:
            fld = entry.get("field") or {}
            descending = str(entry.get("direction", "Ascending")).lower().startswith("desc")
            key = None
            if "Measure" in fld:
                alias = alias_by_name.get(fld["Measure"].get("Property", ""))
                if alias:
                    key = f"[{alias}]"
            elif "Column" in fld:
                col = fld["Column"]
                ent = ((col.get("Expression") or {}).get("SourceRef") or {}).get("Entity", "")
                ref = column_ref(ent, col.get("Property", "")) if ent else None
                if ref in grouping_columns:
                    key = ref
                    sorted_cols.add(ref)
            if key is None:
                meta["notes"].append("a sortDefinition key is not in the query and was ignored")
                continue
            if len(subtotal_flags) > 1:
                continue  # per-level matrices keep the level order below
            topn_sort_parts.append(f"{key}, {0 if descending else 1}")
            order_by_parts.append(f"{key} DESC" if descending else key)

        if len(subtotal_flags) > 1:
            for flag, lvl in subtotal_flags:
                topn_sort_parts.append(f"[{flag}], 0")
                order_by_parts.append(f"[{flag}] DESC")
                for col in lvl:
                    topn_sort_parts.append(f"{col}, 1")
                    order_by_parts.append(col)
                    sorted_cols.add(col)
        for col in grouping_columns:
            if col not in sorted_cols:
                topn_sort_parts.append(f"{col}, 1")
                order_by_parts.append(col)

        define_lines.append(
            f"    VAR __DS0PrimaryWindowed =\n"
            f"        TOPN({row_limit}, __DS0Core, {', '.join(topn_sort_parts)})"
        )
        query = (
            "DEFINE\n" + "\n".join(define_lines) + "\n\n"
            "EVALUATE __DS0PrimaryWindowed\n"
            f"ORDER BY {', '.join(order_by_parts)}"
        )

        # --- 6. Second EVALUATE block for scalar measures (titles, axis bounds, ref lines) ---
        # PBI generates a separate EVALUATE with no group-by columns; all scalar measures
        # and their format strings are wrapped in IGNORE() inside SUMMARIZECOLUMNS.
        effective_scalar = scalar_measures or []
        effective_scalar_fs = scalar_format_string_measures or []
        sc_table_map = scalar_measure_table_map or {}

        if effective_scalar:
            sc2_args: List[str] = [f"        {vn}" for vn in filter_var_refs]
            for m in effective_scalar:
                m_name = self._unbracket(m)
                alias = _sanitize_alias(m_name)
                if m_name in sc_table_map:
                    m_ref = column_ref(sc_table_map[m_name], m_name)
                elif measure_table_map and m_name in measure_table_map:
                    m_ref = column_ref(measure_table_map[m_name], m_name)
                else:
                    m_ref = m
                sc2_args.append(f"        {dax_string(alias)}, IGNORE({m_ref})")
            for fs_ref in effective_scalar_fs:
                sc2_args.append(f"        {dax_string(_fs_alias(fs_ref))}, IGNORE({fs_ref})")

            sc2_body = ",\n".join(sc2_args)
            second = f"SUMMARIZECOLUMNS(\n{sc2_body}\n    )"
            if manual_args:
                second = f"CALCULATETABLE({second}, {', '.join(manual_args)})"
            query += f"\n\nEVALUATE\n    {second}"

        return query, meta

    @staticmethod
    def _split_ref(ref: str) -> Tuple[str, str]:
        from core.dax.dax_names import split_qualified

        return split_qualified(ref)

    def _build_expanded_dax_query(
        self,
        measure_definitions: List[MeasureDefinition],
        grouping_columns: List[str],
        filters: List,
    ) -> str:
        """
        Build a DAX query with actual measure expressions expanded inline (useful for debugging).

        Uses the same CALCULATE-argument form of every DATA filter as the other
        consumers (``FilterExpression.calculate_arg``) -- never a re-derived
        ``'T'[C] = value`` that would drop exclude lists (F-02). Measure (value)
        filters are listed in a leading comment: they cannot be CALCULATE args.
        """
        filter_dax_list, skipped = calculate_filter_args(filters)

        # Build measure parts with actual expressions
        measure_parts = []
        for measure_def in measure_definitions:
            # Defense in depth: a measure with no expression (calc-group placeholder,
            # NULL DMV row) must be skipped, not crash the whole query build.
            expr = (measure_def.expression or "").strip()
            if not expr:
                self.logger.debug(
                    f"Skipping measure '{measure_def.name}' in expanded query: empty expression"
                )
                continue
            name_lit = dax_string(measure_def.name)
            # Wrap in CALCULATE if we have filters (newline before ")" keeps a
            # trailing // comment in the body from swallowing the paren)
            if filter_dax_list:
                filters_str = ",\n        ".join(filter_dax_list)
                measure_parts.append(
                    f"{name_lit},\n    CALCULATE(\n        {expr}\n        ,\n"
                    f"        {filters_str}\n    )"
                )
            else:
                measure_parts.append(f"{name_lit},\n    {expr}\n")

        if not measure_parts:
            return ""

        header = ""
        if skipped:
            header = "".join(
                f"// not applied: {s['table']}[{s['column']}] ({s['reason']})\n" for s in skipped
            )

        measures_str = ",\n    ".join(measure_parts)
        if grouping_columns:
            columns_str = ",\n        ".join(grouping_columns)
            query = f"EVALUATE\nSUMMARIZECOLUMNS(\n    {columns_str},\n    {measures_str}\n)"
        else:
            query = f"EVALUATE\nROW(\n    {measures_str}\n)"

        return header + query

    def build_detail_rows_query(
        self,
        page_name: str,
        visual_id: Optional[str] = None,
        visual_name: Optional[str] = None,
        fact_table: Optional[str] = None,
        columns: Optional[List[str]] = None,
        limit: int = 100,
        include_slicers: bool = True,
    ) -> Optional[str]:
        """
        Build a TOPN detail-rows query with the visual's full filter context applied.
        """
        visual_info, filter_context = self.get_visual_filter_context(
            page_name, visual_id, visual_name, include_slicers
        )

        if not visual_info and not fact_table:
            return None

        # Determine table to query
        if fact_table:
            table = fact_table
        elif visual_info and visual_info.columns:
            table, _col = self._split_ref(visual_info.columns[0])
            if not table:
                self.logger.warning("Could not determine fact table")
                return None
        else:
            self.logger.warning("No fact table specified")
            return None

        table_ref = quote_table(table)
        filter_args, _skipped = calculate_filter_args(filter_context.all_filters())
        if filter_args:
            filter_dax = ",\n        ".join(filter_args)
            return (
                f"EVALUATE\nTOPN(\n    {int(limit)},\n    CALCULATETABLE(\n        {table_ref},\n"
                f"        {filter_dax}\n    )\n)"
            )
        return f"EVALUATE\nTOPN(\n    {int(limit)},\n    {table_ref}\n)"
