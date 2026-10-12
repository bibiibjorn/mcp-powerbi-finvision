"""
Dependency and usage analyzer for Power BI models.
Tracks dependencies and usage patterns across measures, columns, and relationships.
"""

import logging
import time
from typing import Dict, List, Tuple, Any
from core.config.config_manager import config

# Import from dedicated parser module (breaks circular dependency)
from core.dax.dax_reference_parser import DaxReferenceIndex, parse_dax_references

logger = logging.getLogger(__name__)


class DependencyAnalyzer:
    """Analyzes usage patterns and dependencies in Power BI models."""

    def __init__(self, query_executor):
        self.query_executor = query_executor
        self._ref_index = None
        # Review D-22: the measure/column index goes stale after live TOM edits
        # (new/renamed measures misclassified). Rebuild it after a short TTL and
        # key parse results on the index generation so they go stale with it.
        self._ref_index_built_at = 0.0
        self._ref_index_generation = 0
        self._ref_index_ttl = float(config.get("performance.reference_index_ttl", 30))

        # DAX Parse Cache: (measure_name, expression_hash) -> parsed_references
        # This cache avoids re-parsing the same DAX expressions repeatedly
        self._parse_cache: Dict[Tuple[str, int, int], Dict[str, Any]] = {}
        self._parse_cache_timestamps: Dict[Tuple[str, int, int], float] = {}
        self._cache_ttl = config.get("performance.dependency_cache_ttl", 600)  # 10 minutes default

        # Cache statistics
        self._cache_hits = 0
        self._cache_misses = 0

    def _ensure_reference_index(self):
        """Lazily build the reference index"""
        if (
            self._ref_index is not None
            and time.time() - self._ref_index_built_at <= self._ref_index_ttl
        ):
            return self._ref_index

        measures_result = self.query_executor.execute_info_query("MEASURES")
        columns_result = self.query_executor.execute_info_query("COLUMNS")

        measure_rows = measures_result.get("rows", []) if measures_result.get("success") else []
        column_rows = columns_result.get("rows", []) if columns_result.get("success") else []

        self._ref_index = DaxReferenceIndex(measure_rows, column_rows)
        self._ref_index_built_at = time.time()
        self._ref_index_generation += 1
        return self._ref_index

    def _parse_dax_cached(self, measure_name: str, expression: str) -> Dict[str, Any]:
        """
        Parse DAX expression with caching to avoid re-parsing the same expressions.

        Args:
            measure_name: Name of the measure (for cache key)
            expression: DAX expression to parse

        Returns:
            Parsed references dictionary
        """
        # Create cache key from measure name and expression hash
        ref_index = self._ensure_reference_index()
        cache_key = (measure_name, hash(expression), self._ref_index_generation)

        # Check if cache is valid
        if cache_key in self._parse_cache:
            # Check TTL
            timestamp = self._parse_cache_timestamps.get(cache_key, 0)
            age = time.time() - timestamp

            if age <= self._cache_ttl:
                self._cache_hits += 1
                logger.debug(f"DAX parse cache hit for {measure_name} (age: {age:.1f}s)")
                return self._parse_cache[cache_key]
            else:
                # Expired, remove from cache
                del self._parse_cache[cache_key]
                del self._parse_cache_timestamps[cache_key]

        # Cache miss - parse the DAX
        self._cache_misses += 1
        refs = parse_dax_references(expression, ref_index)

        # Store in cache with timestamp
        self._parse_cache[cache_key] = refs
        self._parse_cache_timestamps[cache_key] = time.time()

        logger.debug(
            f"DAX parse cache miss for {measure_name} (cache size: {len(self._parse_cache)})"
        )
        return refs

    def clear_cache(self) -> Dict[str, Any]:
        """Clear the DAX parse cache and reference index."""
        cache_size = len(self._parse_cache)
        self._parse_cache.clear()
        self._parse_cache_timestamps.clear()
        self._ref_index = None  # Force rebuild of reference index
        logger.info(f"Cleared DAX parse cache ({cache_size} entries) and reference index")

        return {
            "success": True,
            "cleared_entries": cache_size,
            "message": f"Cleared {cache_size} cached parse results and reference index",
        }

    def analyze_measure_dependencies(
        self, table: str, measure: str, include_diagram: bool = False
    ) -> Dict:
        """Analyze dependencies for a specific measure

        Args:
            table: Table name containing the measure
            measure: Measure name
            include_diagram: If True, include a Mermaid diagram in the response

        Returns:
            Dictionary with dependency information and optionally a Mermaid diagram
        """
        # Use get_measure_details_with_fallback for proper table+measure filtering
        result = self.query_executor.get_measure_details_with_fallback(table, measure)
        if not result.get("success"):
            return {"success": False, "error": result.get("error")}

        rows = result.get("rows", [])
        if not rows:
            return {"success": False, "error": f"Measure {table}[{measure}] not found"}

        measure_row = rows[0]
        expression = measure_row.get("Expression", "")

        # Use cached parsing (PERFORMANCE IMPROVEMENT)
        measure_key = f"{table}[{measure}]"
        refs = self._parse_dax_cached(measure_key, expression)

        response = {
            "success": True,
            "measure": {"table": table, "name": measure},
            "expression": expression,
            "dependencies": refs,
            "referenced_measures": refs.get("measures", []),
            "referenced_columns": refs.get("columns", []),
            "referenced_tables": refs.get("tables", []),
        }

        # Include Mermaid diagram if requested
        if include_diagram:
            diagram_result = self.generate_dependency_mermaid(
                table, measure, direction="upstream", depth=3
            )
            if diagram_result.get("success"):
                response["mermaid_diagram"] = diagram_result.get("mermaid", "")

        return response

    def _measures_snapshot(self) -> Dict[str, Any]:
        """One INFO.MEASURES read shared by every lookup in a request (C-28).

        Reused while the model generation is unchanged and for at most
        ``performance.measure_snapshot_ttl`` seconds (default 5), so a
        dependency-tree / impact walk costs ONE round trip instead of one per
        visited measure. The reverse index (who references whom) is built from
        the same snapshot on first use.
        """
        try:
            from core.infrastructure.connection_state import connection_state

            generation = getattr(connection_state, "model_generation", 0)
        except Exception:  # noqa: BLE001
            generation = 0
        ttl = float(config.get("performance.measure_snapshot_ttl", 5))
        snap = getattr(self, "_measure_snapshot", None)
        if (
            snap is not None
            and snap["generation"] == generation
            and time.time() - snap["at"] <= ttl
        ):
            return snap
        result = self.query_executor.execute_info_query("MEASURES")
        snap = {
            "generation": generation,
            "at": time.time(),
            "result": result,
            "reverse": None,
        }
        self._measure_snapshot = snap
        return snap

    def _reverse_index(self, snap: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        if snap.get("reverse") is None:
            reverse: Dict[str, List[Dict[str, Any]]] = {}
            for m in snap["result"].get("rows", []) or []:
                m_table = m.get("Table", "")
                m_name = m.get("Name", "")
                refs = self._parse_dax_cached(f"{m_table}[{m_name}]", m.get("Expression", "") or "")
                seen = set()
                for ref_table, ref_name in refs.get("measures", []):
                    key = str(ref_name).lower()
                    if (key, ref_table) in seen:
                        continue
                    seen.add((key, ref_table))
                    reverse.setdefault(key, []).append(
                        {
                            "ref_table": ref_table,
                            "table": m_table,
                            "measure": m_name,
                            "display_folder": m.get("DisplayFolder", "") or "",
                        }
                    )
            snap["reverse"] = reverse
        return snap["reverse"]

    def find_measure_usage(self, table: str, measure: str) -> Dict:
        """Find where a measure is used by other measures."""
        snap = self._measures_snapshot()
        measures_result = snap["result"]
        if not measures_result.get("success"):
            return {"success": False, "error": measures_result.get("error")}

        usage_list = []
        seen = set()
        for entry in self._reverse_index(snap).get(str(measure).lower(), []):
            if entry["table"] == table and entry["measure"] == measure:
                continue
            if entry["ref_table"] not in (table, ""):
                continue
            key = (entry["table"], entry["measure"])
            if key in seen:
                continue
            seen.add(key)
            usage_list.append(
                {
                    "table": entry["table"],
                    "measure": entry["measure"],
                    "display_folder": entry["display_folder"],
                }
            )

        return {
            "success": True,
            "measure": {"table": table, "name": measure},
            "used_by": usage_list,
            "usage_count": len(usage_list),
        }

    def build_dependency_tree(self, table: str, measure: str, max_depth: int = 5) -> Dict:
        """Build a full dependency tree for a measure"""

        def recurse(tbl, msr, depth, visited):
            if depth > max_depth:
                return {"table": tbl, "measure": msr, "max_depth_reached": True}

            key = f"{tbl}|{msr}"
            if key in visited:
                return {"table": tbl, "measure": msr, "circular": True}

            visited.add(key)
            deps_result = self.analyze_measure_dependencies(tbl, msr)

            if not deps_result.get("success"):
                return {"table": tbl, "measure": msr, "error": deps_result.get("error")}

            children = []
            for dep_table, dep_name in deps_result.get("referenced_measures", []):
                if dep_table:
                    child = recurse(dep_table, dep_name, depth + 1, visited.copy())
                    children.append(child)

            return {
                "table": tbl,
                "measure": msr,
                "expression": deps_result.get("expression", ""),
                "dependencies": children,
                "depth": depth,
            }

        tree = recurse(table, measure, 0, set())
        return {"success": True, "tree": tree}

    def analyze_dependencies(
        self, table: str, measure: str, depth: int = 3, include_diagram: bool = True
    ) -> Dict:
        """
        Alias for analyze_measure_dependencies with optional depth parameter.
        Used by tool handlers for backward compatibility.

        Args:
            table: Table name containing the measure
            measure: Measure name
            depth: Maximum depth for tree traversal (default: 3)
            include_diagram: If True, include a Mermaid diagram in the response (default: True)
        """
        result = self.analyze_measure_dependencies(table, measure, include_diagram=include_diagram)
        if not result.get("success"):
            return result

        # If depth > 1, build full dependency tree
        if depth > 1:
            tree_result = self.build_dependency_tree(table, measure, depth)
            if tree_result.get("success"):
                result["dependency_tree"] = tree_result.get("tree")

        return result

    def get_measure_impact(self, table: str, measure: str, depth: int = 3) -> Dict:
        """
        Alias that combines find_measure_usage with dependency analysis.
        Returns both upstream dependencies and downstream usage.
        """
        # Get downstream usage (what uses this measure)
        usage_result = self.find_measure_usage(table, measure)

        # Get upstream dependencies (what this measure uses)
        deps_result = self.analyze_measure_dependencies(table, measure)

        # Combine results
        result = {
            "success": True,
            "measure": {"table": table, "name": measure},
            "downstream_usage": (
                usage_result.get("used_by", []) if usage_result.get("success") else []
            ),
            "downstream_count": (
                usage_result.get("usage_count", 0) if usage_result.get("success") else 0
            ),
            "upstream_dependencies": {
                "measures": (
                    deps_result.get("referenced_measures", []) if deps_result.get("success") else []
                ),
                "columns": (
                    deps_result.get("referenced_columns", []) if deps_result.get("success") else []
                ),
                "tables": (
                    deps_result.get("referenced_tables", []) if deps_result.get("success") else []
                ),
            },
        }

        return result

    def generate_dependency_mermaid(
        self,
        table: str,
        measure: str,
        direction: str = "upstream",
        depth: int = 5,
        include_columns: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate a Mermaid flowchart diagram of measure dependencies with subgraphs by table.

        Args:
            table: Table name containing the measure
            measure: Measure name
            direction: "downstream" (what depends on this) or "upstream" (what this depends on)
            depth: Maximum depth to traverse (default: 5)
            include_columns: Include column references in the diagram (default: True)

        Returns:
            Dictionary with Mermaid diagram code and metadata

        Example output:
            graph TB
                M_m_Amount_in_selected_currency["<b>Amount in selected currency</b><br/>(m)"]

                subgraph T1_s_Reporting_Currency["s Reporting Currency<br/>(Selection Table)"]
                    C1_0_Reporting_Currency["Reporting Currency"]
                end

                subgraph T2_f_Valtrans["f Valtrans<br/>(Fact Table)"]
                    C2_0_Amount_Base_Curr["Amount (Base Curr.)"]
                    C2_1_Amount_EUR["Amount (EUR)"]
                end

                M_m_Amount_in_selected_currency -->|"SELECTEDVALUE"| T1_s_Reporting_Currency
                M_m_Amount_in_selected_currency -->|"SUMX Iterator"| T2_f_Valtrans

                %% Styling
                classDef measureClass fill:#e1f5fe,stroke:#01579b,stroke-width:3px,color:#000
                classDef tableClass fill:#fff3e0,stroke:#e65100,stroke-width:2px,color:#000
        """
        try:
            measure_key = f"{table}[{measure}]"
            nodes_by_table: Dict[str, List[Tuple[str, str, str]]] = (
                {}
            )  # table -> [(node_key, node_name, node_type)]
            edges = []  # (source, target, label)
            visited = set()
            column_nodes = set()  # Track which nodes are columns

            from core.utilities.mermaid_utils import sanitize_node_id as _sanitize_node_id

            def sanitize_node_id(name: str, prefix: str = "M") -> str:
                return _sanitize_node_id(name, prefix=prefix)

            def get_table_type(tbl_name: str) -> str:
                """Determine table type hint based on naming convention"""
                tbl_lower = tbl_name.lower()
                if "_s_" in tbl_lower or tbl_lower.startswith("s_") or "selection" in tbl_lower:
                    return "(Selection Table)"
                elif "_f_" in tbl_lower or tbl_lower.startswith("f_") or "fact" in tbl_lower:
                    return "(Fact Table)"
                elif "_d_" in tbl_lower or tbl_lower.startswith("d_") or "dim" in tbl_lower:
                    return "(Dimension Table)"
                return ""

            def add_node_to_table(tbl: str, node_key: str, node_name: str, node_type: str):
                """Add a node to its table's subgraph"""
                if tbl not in nodes_by_table:
                    nodes_by_table[tbl] = []
                # Check if node already exists
                for existing_key, _, _ in nodes_by_table[tbl]:
                    if existing_key == node_key:
                        return
                nodes_by_table[tbl].append((node_key, node_name, node_type))

            # Build measure name lookup for resolving unqualified references
            from core.utilities.mermaid_utils import (
                build_measure_name_lookup,
                resolve_measure_table as _resolve,
            )

            measures_result = self.query_executor.execute_info_query("MEASURES")
            measure_name_to_table = build_measure_name_lookup(measures_result)
            logger.debug(
                f"Built measure lookup with {len(measure_name_to_table)} entries for {len(measures_result.get('rows', []))} measures"
            )

            def resolve_measure_table(dep_tbl: str, dep_name: str) -> str:
                return _resolve(dep_tbl, dep_name, measure_name_to_table)

            if direction == "upstream":
                # Get what this measure depends on
                def traverse_upstream(tbl: str, msr: str, current_depth: int):
                    if current_depth > depth:
                        return
                    key = f"{tbl}[{msr}]"
                    if key in visited:
                        return
                    visited.add(key)

                    # Add this measure to its table
                    add_node_to_table(tbl, key, msr, "measure")

                    deps_result = self.analyze_measure_dependencies(tbl, msr)
                    if deps_result.get("success"):
                        # Add measure dependencies
                        for dep_table, dep_name in deps_result.get("referenced_measures", []):
                            if dep_name:  # Only need name, table can be empty
                                effective_table = resolve_measure_table(dep_table, dep_name)
                                dep_key = f"{effective_table}[{dep_name}]"
                                add_node_to_table(effective_table, dep_key, dep_name, "measure")
                                edges.append((dep_key, key, ""))  # dependency -> measure
                                if (
                                    effective_table and effective_table != "Unknown"
                                ):  # Only recurse if we have a valid table
                                    traverse_upstream(effective_table, dep_name, current_depth + 1)

                        # Add column dependencies
                        if include_columns:
                            for col_table, col_name in deps_result.get("referenced_columns", []):
                                col_key = f"{col_table}[{col_name}]"
                                add_node_to_table(col_table, col_key, col_name, "column")
                                column_nodes.add(col_key)
                                edges.append((col_key, key, ""))

                traverse_upstream(table, measure, 0)

            else:  # downstream
                # Get what depends on this measure
                def traverse_downstream(tbl: str, msr: str, current_depth: int):
                    if current_depth > depth:
                        return
                    key = f"{tbl}[{msr}]"
                    if key in visited:
                        return
                    visited.add(key)

                    # Add this measure to its table
                    add_node_to_table(tbl, key, msr, "measure")

                    usage_result = self.find_measure_usage(tbl, msr)
                    if usage_result.get("success"):
                        for dep in usage_result.get("used_by", []):
                            dep_table = dep.get("table", "")
                            dep_name = dep.get("measure", "")
                            if dep_table and dep_name:
                                dep_key = f"{dep_table}[{dep_name}]"
                                add_node_to_table(dep_table, dep_key, dep_name, "measure")
                                edges.append((key, dep_key, ""))  # measure -> dependent
                                traverse_downstream(dep_table, dep_name, current_depth + 1)

                traverse_downstream(table, measure, 0)

            # Generate Mermaid code with subgraphs
            lines = ["graph TB"]

            # Add root measure node first (outside subgraphs for emphasis)
            root_id = sanitize_node_id(measure, "M_m")
            lines.append(f'    {root_id}["<b>{measure}</b><br/>(m)"]')
            lines.append("")

            # Track node IDs for styling
            column_node_ids = []
            measure_node_ids = [root_id]

            # Add subgraphs for each table
            table_counter = 1
            node_id_map = {}  # Map original keys to Mermaid node IDs

            for tbl_name, nodes in sorted(nodes_by_table.items()):
                if tbl_name == table and len(nodes) == 1:
                    # Skip the root measure's table if it only contains the root
                    node_id_map[f"{tbl_name}[{nodes[0][1]}]"] = root_id
                    continue

                table_id = sanitize_node_id(tbl_name, f"T{table_counter}")
                table_type = get_table_type(tbl_name)

                lines.append(f'    subgraph {table_id}["{tbl_name}<br/>{table_type}"]')

                node_counter = 0
                for node_key, node_name, node_type in nodes:
                    # Skip root measure - already added above
                    if node_key == measure_key:
                        node_id_map[node_key] = root_id
                        continue

                    node_id = sanitize_node_id(node_name, f"C{table_counter}_{node_counter}")
                    node_id_map[node_key] = node_id

                    if node_type == "column":
                        lines.append(f'        {node_id}["{node_name}"]')
                        column_node_ids.append(node_id)
                    else:
                        lines.append(f'        {node_id}["{node_name}"]')
                        measure_node_ids.append(node_id)

                    node_counter += 1

                lines.append("    end")
                lines.append("")
                table_counter += 1

            # Add edges
            for source, target, label in edges:
                source_id = node_id_map.get(
                    source, sanitize_node_id(source.split("[")[1].rstrip("]"), "M")
                )
                target_id = node_id_map.get(
                    target, sanitize_node_id(target.split("[")[1].rstrip("]"), "M")
                )

                if label:
                    lines.append(f'    {source_id} -->|"{label}"| {target_id}')
                else:
                    lines.append(f"    {source_id} --> {target_id}")

            # Add styling
            lines.append("")
            lines.append("    %% Styling")
            lines.append(
                "    classDef measureClass fill:#e1f5fe,stroke:#01579b,stroke-width:3px,color:#000"
            )
            lines.append(
                "    classDef tableClass fill:#fff3e0,stroke:#e65100,stroke-width:2px,color:#000"
            )
            lines.append(
                "    classDef columnClass fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px,color:#000"
            )

            # Apply measure class to root
            lines.append(f"    class {root_id} measureClass")

            # Apply column class
            if column_node_ids:
                lines.append(f"    class {','.join(column_node_ids)} columnClass")

            mermaid_code = "\n".join(lines)

            # Return raw Mermaid code (no markdown fence) for API rendering
            # Markdown wrapping can be done at presentation layer if needed
            return {
                "success": True,
                "measure": {"table": table, "name": measure},
                "direction": direction,
                "depth": depth,
                "mermaid": mermaid_code,
                "node_count": sum(len(nodes) for nodes in nodes_by_table.values()),
                "edge_count": len(edges),
                "format": "mermaid_flowchart",
            }

        except Exception as e:
            logger.error(f"Error generating Mermaid diagram for {table}[{measure}]: {e}")
            return {"success": False, "error": str(e)}

    def generate_impact_mermaid(self, table: str, measure: str) -> Dict[str, Any]:
        """
        Generate a bidirectional Mermaid diagram showing both upstream and downstream dependencies.

        Args:
            table: Table name containing the measure
            measure: Measure name

        Returns:
            Dictionary with comprehensive Mermaid diagram
        """
        try:
            # Combine into single diagram
            measure_key = f"{table}[{measure}]"
            all_nodes = set()
            column_nodes = set()  # Track column nodes for different styling
            upstream_edges = []
            downstream_edges = []
            visited_upstream = set()
            visited_downstream = set()

            from core.utilities.mermaid_utils import sanitize_node_id

            def sanitize_label(name: str) -> str:
                return name.replace('"', '\\"')

            # Build measure name lookup for resolving unqualified references
            from core.utilities.mermaid_utils import (
                build_measure_name_lookup,
                resolve_measure_table as _resolve_impact,
            )

            measures_result = self.query_executor.execute_info_query("MEASURES")
            measure_name_to_table = build_measure_name_lookup(measures_result)
            logger.debug(f"Impact mermaid: Built lookup with {len(measure_name_to_table)} entries")

            def resolve_table(dep_tbl: str, dep_name: str) -> str:
                return _resolve_impact(
                    dep_tbl, dep_name, measure_name_to_table, context="Impact mermaid"
                )

            # Collect upstream (measures AND columns)
            def collect_upstream(tbl: str, msr: str, depth: int):
                if depth > 3:
                    return
                key = f"{tbl}[{msr}]"
                if key in visited_upstream:
                    return
                visited_upstream.add(key)
                all_nodes.add(key)

                deps = self.analyze_measure_dependencies(tbl, msr)
                if deps.get("success"):
                    # Add referenced measures
                    for dep_tbl, dep_name in deps.get("referenced_measures", []):
                        if dep_name:  # Only need name, table can be empty
                            effective_tbl = resolve_table(dep_tbl, dep_name)
                            dep_key = f"{effective_tbl}[{dep_name}]"
                            all_nodes.add(dep_key)
                            upstream_edges.append((dep_key, key))
                            if (
                                effective_tbl and effective_tbl != "Unknown"
                            ):  # Recurse if we have a valid table
                                collect_upstream(effective_tbl, dep_name, depth + 1)
                    # Add referenced columns (only at depth 0 to avoid clutter)
                    if depth == 0:
                        for col_tbl, col_name in deps.get("referenced_columns", []):
                            if col_tbl:
                                col_key = f"{col_tbl}[{col_name}]"
                                all_nodes.add(col_key)
                                column_nodes.add(col_key)
                                upstream_edges.append((col_key, key))

            # Collect downstream
            def collect_downstream(tbl: str, msr: str, depth: int):
                if depth > 3:
                    return
                key = f"{tbl}[{msr}]"
                if key in visited_downstream:
                    return
                visited_downstream.add(key)
                all_nodes.add(key)

                usage = self.find_measure_usage(tbl, msr)
                if usage.get("success"):
                    for dep in usage.get("used_by", []):
                        dep_tbl = dep.get("table", "")
                        dep_name = dep.get("measure", "")
                        if dep_tbl and dep_name:
                            dep_key = f"{dep_tbl}[{dep_name}]"
                            all_nodes.add(dep_key)
                            downstream_edges.append((key, dep_key))
                            collect_downstream(dep_tbl, dep_name, depth + 1)

            collect_upstream(table, measure, 0)
            collect_downstream(table, measure, 0)

            # Generate combined diagram
            lines = ["flowchart LR"]
            lines.append("    %% Impact Analysis Diagram")
            lines.append("    %% Left = Dependencies (upstream), Right = Dependents (downstream)")
            lines.append("")

            # Subgraphs for organization
            upstream_nodes = set(e[0] for e in upstream_edges) - {measure_key}
            downstream_nodes = set(e[1] for e in downstream_edges) - {measure_key}
            upstream_measures = upstream_nodes - column_nodes
            upstream_columns = upstream_nodes & column_nodes

            if upstream_nodes:
                lines.append("    subgraph Dependencies")
                lines.append("    direction TB")
                # Add measure nodes
                for node in sorted(upstream_measures):
                    node_id = sanitize_node_id(node)
                    lines.append(f'    {node_id}["{sanitize_label(node)}"]:::upstream')
                # Add column nodes with different style
                for node in sorted(upstream_columns):
                    node_id = sanitize_node_id(node)
                    lines.append(f'    {node_id}["{sanitize_label(node)}"]:::column')
                lines.append("    end")
                lines.append("")

            # Root measure
            root_id = sanitize_node_id(measure_key)
            lines.append(f'    {root_id}["{sanitize_label(measure_key)}"]:::root')
            lines.append("")

            if downstream_nodes:
                lines.append("    subgraph Dependents")
                lines.append("    direction TB")
                for node in sorted(downstream_nodes):
                    node_id = sanitize_node_id(node)
                    lines.append(f'    {node_id}["{sanitize_label(node)}"]:::downstream')
                lines.append("    end")
                lines.append("")

            # Edges
            lines.append("    %% Dependency edges (upstream)")
            for source, target in upstream_edges:
                lines.append(f"    {sanitize_node_id(source)} --> {sanitize_node_id(target)}")

            lines.append("")
            lines.append("    %% Impact edges (downstream)")
            for source, target in downstream_edges:
                lines.append(f"    {sanitize_node_id(source)} --> {sanitize_node_id(target)}")

            # Styles
            lines.append("")
            lines.append(
                "    classDef root fill:#2196F3,stroke:#1565C0,color:#fff,stroke-width:3px"
            )
            lines.append("    classDef upstream fill:#4CAF50,stroke:#388E3C,color:#fff")
            lines.append("    classDef downstream fill:#FF9800,stroke:#F57C00,color:#fff")
            lines.append("    classDef column fill:#9C27B0,stroke:#7B1FA2,color:#fff")

            mermaid_code = "\n".join(lines)

            # Calculate total counts
            total_edges = len(upstream_edges) + len(downstream_edges)
            total_nodes = len(all_nodes)

            # Return raw Mermaid code (no markdown fence) for API rendering
            return {
                "success": True,
                "measure": {"table": table, "name": measure},
                "mermaid": mermaid_code,
                "upstream_count": len(upstream_nodes),
                "downstream_count": len(downstream_nodes),
                "node_count": total_nodes,
                "edge_count": total_edges,
                "format": "mermaid_flowchart_bidirectional",
            }

        except Exception as e:
            logger.error(f"Error generating impact Mermaid diagram for {table}[{measure}]: {e}")
            return {"success": False, "error": str(e)}
