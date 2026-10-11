"""``VisualQueryBuilder`` methods split out of ``core.debug.visual_query_builder`` (C-17): pure move, no behaviour change.

``VisualQueryBuilder`` inherits :class:`VisualMeasureLookupMixin`, so every ``self.<method>`` call and every import of
``core.debug.visual_query_builder.VisualQueryBuilder`` keeps working.
"""

from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
from core.dax.dax_names import bracket, column_ref, quote_table
from core.debug.visual_query_types import MeasureDefinition


class VisualMeasureLookupMixin:
    """Methods of :class:`VisualQueryBuilder` (visual_measure_lookup.py)."""

    def _load_all_measures_from_dmv(self) -> bool:
        """
        Batch load all measures from DMV in a single query.

        Much faster than querying measures individually; loads all in one DMV call.
        """
        self._check_model_generation()
        if self._all_measures_loaded or not self._query_executor:
            return self._all_measures_loaded

        try:
            # Query all measures in one call
            measures_result = self._query_executor.execute_info_query("MEASURES")
            if not measures_result.get("success") or not measures_result.get("rows"):
                return False

            # Query all tables once for ID -> name mapping
            table_id_to_name = {}
            tables_result = self._query_executor.execute_info_query("TABLES")
            if tables_result.get("success") and tables_result.get("rows"):
                for table_row in tables_result["rows"]:
                    tid = str(table_row.get("ID", table_row.get("[ID]", "")))
                    tname = table_row.get("Name", table_row.get("[Name]", ""))
                    if tid and tname:
                        table_id_to_name[tid] = tname

            # Populate the cache with all measures
            fs_flags: Dict[str, str] = {}
            fs_column_seen = False
            loaded: Dict[str, MeasureDefinition] = {}
            for row in measures_result["rows"]:
                name = row.get("Name", row.get("[Name]", ""))
                if not name:
                    continue

                # NOTE: dict.get(key, default) returns the STORED value when the key
                # exists, so a NULL from the DMV survives the "" default as None.
                # Coerce explicitly — downstream code calls .strip() on these.
                expression = row.get("Expression", row.get("[Expression]", "")) or ""
                table_id = str(row.get("TableID", row.get("[TableID]", "")))
                format_string = row.get("FormatString", row.get("[FormatString]", "")) or ""

                table_name = table_id_to_name.get(table_id) or row.get("Table") or None

                measure_def = MeasureDefinition(
                    name=name, expression=expression, table=table_name, format_string=format_string
                )
                # Cache by both original and lowercase name for case-insensitive lookup
                loaded[name] = measure_def
                loaded[name.lower()] = measure_def
                # F-34: dynamic format strings straight from INFO.MEASURES.
                if "FormatStringDefinitionID" in row:
                    fs_column_seen = True
                    fsd = row.get("FormatStringDefinitionID")
                    if fsd not in (None, 0, "", "0") and table_name:
                        fs_flags[name] = table_name
                        fs_flags[name.lower()] = table_name

            with self._lock:
                self._measure_cache.update(loaded)
                if fs_column_seen:
                    self._format_string_flags.update(fs_flags)
                    self._format_string_flags_source = "dmv"
                self._all_measures_loaded = True
            self.logger.debug(f"Batch loaded {len(measures_result['rows'])} measures from DMV")
            return True

        except Exception as e:
            self.logger.debug(f"Error batch loading measures: {e}")
            return False

    def get_measure_expression(self, measure_name: str) -> Optional[MeasureDefinition]:
        """
        Get the DAX expression for a measure from the model.

        Tries cache → batch DMV query → PBIP TMDL files (offline fallback).
        """
        # Clean measure name
        clean_name = measure_name.strip("[]")

        # Check cache first
        if clean_name in self._measure_cache:
            return self._measure_cache[clean_name]
        if clean_name.lower() in self._measure_cache:
            return self._measure_cache[clean_name.lower()]

        # Try batch loading all measures (much faster for multiple lookups)
        if self._query_executor and not self._all_measures_loaded:
            self._load_all_measures_from_dmv()
            # Check cache again after batch load
            if clean_name in self._measure_cache:
                return self._measure_cache[clean_name]
            if clean_name.lower() in self._measure_cache:
                return self._measure_cache[clean_name.lower()]

        # Try 2: Search PBIP TMDL files (offline fallback)
        measure_def = self._get_measure_from_tmdl(clean_name)
        if measure_def:
            self._measure_cache[clean_name] = measure_def
            return measure_def

        self.logger.debug(f"Could not find measure '{clean_name}' in model or TMDL files")
        return None

    def _get_measure_from_tmdl(self, measure_name: str) -> Optional[MeasureDefinition]:
        """
        Get measure expression from PBIP TMDL files.

        Searches all TMDL files under the semantic model for the given measure name.
        """
        try:
            # Find semantic model folder - look for .tmdl files
            semantic_model_path = self._find_semantic_model_path()
            if not semantic_model_path:
                self.logger.debug("No semantic model path found for TMDL lookup")
                return None

            # Search all .tmdl files for the measure
            tmdl_files = list(semantic_model_path.glob("**/*.tmdl"))
            self.logger.debug(
                f"Searching {len(tmdl_files)} TMDL files for measure '{measure_name}'"
            )
            # P-15: only files whose text contains the name can match the
            # ``measure <name> =`` patterns (both are literal + IGNORECASE), so the
            # per-file regex runs on those few instead of on every table file.
            tmdl_files = self._tmdl_candidates(tmdl_files, measure_name)

            for tmdl_file in tmdl_files:
                try:
                    result = self._parse_measure_from_tmdl(tmdl_file, measure_name)
                    if result:
                        return result
                except Exception as e:
                    self.logger.debug(f"Error parsing {tmdl_file}: {e}")
                    continue

            return None

        except Exception as e:
            self.logger.warning(f"Error searching TMDL files: {e}")
            return None

    def _tmdl_candidates(self, tmdl_files: List[Path], measure_name: str) -> List[Path]:
        """*tmdl_files* (same order) that can contain *measure_name*.

        Non-ASCII names and unreadable files are never filtered out (the caller's
        parse then behaves exactly as before). File texts are cached per builder,
        validated by ``(size, mtime_ns)`` on every call.
        """
        needle = (measure_name or "").lower()
        if not needle or not needle.isascii():
            return tmdl_files
        keep: List[Path] = []
        for tmdl_file in tmdl_files:
            key = str(tmdl_file)
            try:
                st = tmdl_file.stat()
                stamp = (st.st_size, st.st_mtime_ns)
                with self._lock:
                    cached = self._tmdl_text_cache.get(key)
                if cached is None or cached[:2] != stamp:
                    text = tmdl_file.read_text(encoding="utf-8").lower()
                    cached = (stamp[0], stamp[1], text)
                    with self._lock:
                        self._tmdl_text_cache[key] = cached
                if needle in cached[2]:
                    keep.append(tmdl_file)
            except Exception:  # noqa: BLE001 -- let the original parse report it
                keep.append(tmdl_file)
        return keep

    def _resolve_sort_by_columns(self, grouping_columns: List[str]) -> List[Tuple[str, str]]:
        """Resolve SortByColumn for each grouping column.

        Power BI includes sort-by columns in SUMMARIZECOLUMNS group-by, TOPN,
        and ORDER BY. E.g. if 's Waterfall Bucket'[Bucket] has sortByColumn = Sort Nr,
        then [Sort Nr] appears in the query as a group-by column before [Bucket].

        F-18: the live lane reads ``INFO.COLUMNS()`` (SortByColumnID -> column)
        through the executor that is already connected; the offline lane parses
        the bound semantic model's TMDL (found through ``definition.pbir``, so
        a thin report bound to a differently-named model works). Either map is
        built ONCE per builder / model generation.
        """
        from core.dax.dax_names import split_qualified

        if not grouping_columns:
            return []

        parsed_cols = []
        for col_ref in grouping_columns:
            table, col = split_qualified(col_ref)
            if table and col:
                parsed_cols.append((table, col, col_ref))
        if not parsed_cols:
            return []

        sort_map = self._get_sort_by_map()
        if not sort_map:
            return []

        pairs = []
        for table_name, col_name, col_ref in parsed_cols:
            sort_col = sort_map.get((table_name, col_name))
            if sort_col and sort_col != col_name:
                pairs.append((column_ref(table_name, sort_col), col_ref))
        return pairs

    def _get_sort_by_map(self) -> Dict[Tuple[str, str], str]:
        self._check_model_generation()
        with self._lock:
            if self._sort_by_map is not None:
                return self._sort_by_map
        sort_map = self._resolve_sort_by_via_dmv() if self._query_executor else {}
        if not sort_map:
            sort_map = self._resolve_sort_by_via_tmdl()
        with self._lock:
            self._sort_by_map = sort_map
        return sort_map

    def _resolve_sort_by_via_dmv(self, parsed_cols: Any = None) -> Dict[Tuple[str, str], str]:
        """(table, column) -> sort-by column name from INFO.COLUMNS()."""
        qe = self._query_executor
        if not qe or not hasattr(qe, "execute_info_query"):
            return {}
        try:
            result = qe.execute_info_query("COLUMNS")
            if not result.get("success"):
                return {}
            by_id: Dict[Any, Tuple[str, str]] = {}
            rows = result.get("rows") or []
            for row in rows:
                cid = row.get("ID")
                table = row.get("Table")
                name = row.get("ExplicitName") or row.get("InferredName") or row.get("Name")
                if cid is not None and table and name:
                    by_id[str(cid)] = (str(table), str(name))
            sort_map: Dict[Tuple[str, str], str] = {}
            for row in rows:
                sort_id = row.get("SortByColumnID")
                if sort_id in (None, 0, "", "0"):
                    continue
                me = by_id.get(str(row.get("ID")))
                target = by_id.get(str(sort_id))
                if me and target:
                    sort_map[me] = target[1]
            return sort_map
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"DMV SortByColumn resolution failed: {e}")
            return {}

    def _resolve_sort_by_via_tmdl(self) -> Dict[Tuple[str, str], str]:
        """Resolve SortByColumn from TMDL files (offline fallback)."""
        semantic_path = self._find_semantic_model_path()
        if not semantic_path:
            return {}

        try:
            from core.tmdl.unified_parser import UnifiedTmdlParser

            from core.dax.dax_names import unquote_table

            # F-18: the parser takes the path in its constructor; the old
            # ``UnifiedTmdlParser().parse_folder(...)`` raised TypeError, so the
            # offline lane never produced a single sort-by pair.
            model = UnifiedTmdlParser(str(semantic_path)).parse_full_model()
            if not model or not model.tables:
                return {}

            sort_map: Dict[Tuple[str, str], str] = {}
            for table in model.tables:
                for col in table.columns:
                    if col.sort_by_column:
                        sort_map[(table.name, col.name)] = unquote_table(col.sort_by_column)

            return sort_map

        except Exception as e:
            self.logger.debug(f"TMDL SortByColumn resolution failed: {e}")
            return {}

    def _find_semantic_model_path(self) -> Optional[Path]:
        """The ``definition`` folder of the semantic model this report is bound to.

        Resolution order (cached): the report's ``definition.pbir``
        ``datasetReference`` (``R0201-...Report -> ../S02-...SemanticModel``),
        the same-stem sibling, a model folder inside the PBIP folder, then any
        single ``*.SemanticModel`` next to the report (F-18).
        """
        with self._lock:
            if self._semantic_model_resolved:
                return self._semantic_model_path
        found = self._discover_semantic_model_path()
        with self._lock:
            self._semantic_model_path = found
            self._semantic_model_resolved = True
        return found

    def _discover_semantic_model_path(self) -> Optional[Path]:
        report_dir = self.definition_path.parent if self.definition_path else self.pbip_folder
        try:
            from core.pbip.pbir_model import read_dataset_reference

            ref = read_dataset_reference(str(report_dir))
            model_dir = ref.get("model_dir")
            if model_dir:
                definition_folder = Path(model_dir) / "definition"
                if definition_folder.exists():
                    return definition_folder
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"definition.pbir model lookup failed: {e}")

        if ".Report" in str(self.pbip_folder):
            semantic_folder = Path(str(self.pbip_folder).replace(".Report", ".SemanticModel"))
            if (semantic_folder / "definition").exists():
                return semantic_folder / "definition"

        for path_pattern in ["definition/tables", "definition/model", "model/definition/tables"]:
            model_path = self.pbip_folder / path_pattern
            if model_path.exists():
                return model_path.parent

        try:
            definition_tmdl = next((self.pbip_folder / "definition").glob("**/*.tmdl"), None)
        except OSError:
            definition_tmdl = None
        if definition_tmdl is not None:
            return self.pbip_folder / "definition"

        # Last resort: exactly one *.SemanticModel sibling with TMDL files.
        try:
            candidates = [
                c / "definition"
                for c in sorted(Path(report_dir).parent.iterdir())
                if c.is_dir() and c.name.endswith(".SemanticModel") and (c / "definition").exists()
            ]
        except OSError:
            candidates = []
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _parse_measure_from_tmdl(
        self, tmdl_file: Path, measure_name: str
    ) -> Optional[MeasureDefinition]:
        """
        Parse a measure definition from a TMDL file.

        Parses a single TMDL file for a measure definition.
        """
        try:
            content = tmdl_file.read_text(encoding="utf-8")

            # Get table name from file path (usually tables/TableName.tmdl)
            table_name = None
            if "tables" in str(tmdl_file):
                table_name = tmdl_file.stem

            # Search for measure definition
            # Patterns: measure 'Name' = ... or measure Name = ...
            import re

            # Pattern 1: measure 'Measure Name' = ...
            pattern_quoted = rf"measure\s+'({re.escape(measure_name)})'\s*=\s*"
            # Pattern 2: measure MeasureName = ...
            pattern_unquoted = rf"measure\s+({re.escape(measure_name)})\s*=\s*"

            match = None
            for pattern in [pattern_quoted, pattern_unquoted]:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    break

            if not match:
                return None

            # Found the measure - now extract the expression
            start_pos = match.end()

            # The expression continues until we hit certain keywords or end of section
            # Look for: formatString, displayFolder, another measure, or section end
            end_markers = [
                r"\n\s*measure\s+",  # Next measure
                r"\n\s*column\s+",  # Column definition
                r"\n\s*formatString\s*=",  # Format string property
                r"\n\s*displayFolder\s*=",  # Display folder property
                r"\n\s*description\s*=",  # Description property
                r"\n\s*isHidden\s*=",  # Hidden property
                r"\ntable\s+",  # Next table
                r"\n\s*\n\s*\n",  # Double newline (section break)
            ]

            expression_end = len(content)
            for marker in end_markers:
                marker_match = re.search(marker, content[start_pos:], re.IGNORECASE)
                if marker_match:
                    end_pos = start_pos + marker_match.start()
                    if end_pos < expression_end:
                        expression_end = end_pos

            expression = content[start_pos:expression_end].strip()

            # Extract format string if present
            format_string = None
            format_match = re.search(
                r"formatString\s*=\s*(['\"])(.+?)\1",
                content[expression_end : expression_end + 500],
                re.IGNORECASE,
            )
            if format_match:
                format_string = format_match.group(2)

            if expression:
                self.logger.debug(f"Found measure '{measure_name}' in {tmdl_file}")
                return MeasureDefinition(
                    name=measure_name,
                    expression=expression,
                    table=table_name,
                    format_string=format_string,
                )

            return None

        except Exception as e:
            self.logger.debug(f"Error parsing TMDL file {tmdl_file}: {e}")
            return None

    def _detect_dynamic_format_string_measures(
        self,
        measure_definitions: List[MeasureDefinition],
    ) -> List[str]:
        """
        Detect measures that have a dynamic format string (formatStringDefinition in TMDL).

        Power BI projects these as 'Table'[_MeasureName FormatString] in SUMMARIZECOLUMNS
        queries, wrapped in IGNORE() so they don't filter out rows. The companion
        measure is NOT a real model measure — it's a synthetic projection of the
        Measure.FormatStringDefinition.Expression TOM property, so INFO.MEASURES()
        will never return it. TMDL file scanning is the only reliable detection path.
        """
        if not measure_definitions:
            return []

        semantic_model_path = self._find_semantic_model_path()
        if not semantic_model_path:
            # Fallback: PBIP projects often have a separately-named .SemanticModel
            # sibling folder (e.g. Report=R0101-*.Report, Model=S01-*.SemanticModel).
            # Scan the parent directory for any .SemanticModel folder with TMDL files.
            parent = self.pbip_folder.parent
            try:
                for candidate in sorted(parent.iterdir()):
                    if candidate.is_dir() and candidate.name.endswith(".SemanticModel"):
                        def_path = candidate / "definition"
                        if def_path.exists() and any(def_path.glob("**/*.tmdl")):
                            semantic_model_path = def_path
                            break
            except Exception:
                pass
        if not semantic_model_path:
            return []

        import re

        result: List[str] = []
        tmdl_files = list(semantic_model_path.glob("**/*.tmdl"))

        for m_def in measure_definitions:
            if not m_def.name:
                continue

            for tmdl_file in tmdl_files:
                try:
                    content = tmdl_file.read_text(encoding="utf-8")

                    # Find the measure block (quoted or unquoted name)
                    pattern = (
                        rf"measure\s+'({re.escape(m_def.name)})'\s*="
                        rf"|measure\s+({re.escape(m_def.name)})\s*="
                    )
                    match = re.search(pattern, content, re.IGNORECASE)
                    if not match:
                        continue

                    # Extract the block up to the next measure definition
                    next_measure = re.search(
                        r"\n\s*measure\s+", content[match.end() :], re.IGNORECASE
                    )
                    block_end = (
                        (match.end() + next_measure.start()) if next_measure else len(content)
                    )
                    block = content[match.start() : block_end]

                    # Measure found — check for formatStringDefinition in its block
                    if not re.search(r"formatStringDefinition\s*=", block, re.IGNORECASE):
                        break  # Measure found but no dynamic format string

                    # Resolve table name: prefer m_def.table, fallback to file stem
                    table_name = m_def.table
                    if not table_name and "tables" in tmdl_file.parts:
                        table_name = tmdl_file.stem

                    if table_name:
                        result.append(
                            quote_table(table_name) + bracket(f"_{m_def.name} FormatString")
                        )
                    break  # Done with this measure

                except Exception as e:
                    self.logger.debug(f"Error scanning {tmdl_file} for format string: {e}")

        return result

    def get_measure_expressions(self, measure_names: List[str]) -> Dict[str, MeasureDefinition]:
        """
        Get DAX expressions for multiple measures; returns dict of name → MeasureDefinition.
        """
        result = {}
        for name in measure_names:
            measure_def = self.get_measure_expression(name)
            if measure_def:
                result[measure_def.name] = measure_def
        return result
