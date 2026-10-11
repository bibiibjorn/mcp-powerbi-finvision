"""
Relationship-Aware Query Generation

Analyzes model relationships and suggests DAX modifiers (USERELATIONSHIP, CROSSFILTER)
when queries involve inactive relationships or need bidirectional filtering.
"""

import logging
from typing import Dict, List, Set, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RelationshipInfo:
    """Information about a model relationship."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    is_active: bool
    cross_filter_direction: str  # 'Single', 'Both', 'None'
    cardinality: str  # 'OneToMany', 'ManyToOne', 'ManyToMany', 'OneToOne'


@dataclass
class RelationshipHint:
    """Suggestion for relationship handling in a query."""

    type: str  # 'use_relationship', 'crossfilter_both', 'ambiguous_path'
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    dax_modifier: str  # DAX to add to CALCULATE
    reason: str  # Human-readable explanation
    severity: str = "info"  # 'info', 'warning'


class RelationshipResolver:
    """
    Analyzes relationships and generates appropriate DAX modifiers.

    Detects:
    1. Inactive relationships that may need activation (USERELATIONSHIP)
    2. Single-direction relationships that may need bidirectional (CROSSFILTER)
    3. Ambiguous relationship paths
    """

    def __init__(self, query_executor=None):
        """
        query_executor: QueryExecutor for DMV queries.
        """
        self.qe = query_executor
        self._relationships: List[RelationshipInfo] = []
        self._loaded = False

        # Index for quick lookup
        self._by_from_table: Dict[str, List[RelationshipInfo]] = {}
        self._by_to_table: Dict[str, List[RelationshipInfo]] = {}
        self._inactive_pairs: Set[Tuple[str, str]] = set()  # (from_table, to_table)

    def load_relationships(self) -> bool:
        """
        Load relationships from the model; returns True on success.
        """
        if self._loaded:
            return True

        if not self.qe:
            logger.debug("No query executor available for relationship loading")
            return False

        try:
            result = self.qe.execute_info_query("RELATIONSHIPS")

            if not result.get("success"):
                logger.warning(f"Failed to load relationships: {result.get('error')}")
                return False

            for row in result.get("rows", []):
                rel = RelationshipInfo(
                    from_table=row.get("FromTable", row.get("[FromTable]", "")),
                    from_column=row.get("FromColumn", row.get("[FromColumn]", "")),
                    to_table=row.get("ToTable", row.get("[ToTable]", "")),
                    to_column=row.get("ToColumn", row.get("[ToColumn]", "")),
                    is_active=row.get("IsActive", row.get("[IsActive]", True)),
                    cross_filter_direction=row.get(
                        "CrossFilterDirection", row.get("[CrossFilterDirection]", "Single")
                    ),
                    cardinality=row.get("Cardinality", row.get("[Cardinality]", "OneToMany")),
                )

                self._relationships.append(rel)

                # Build indexes
                if rel.from_table not in self._by_from_table:
                    self._by_from_table[rel.from_table] = []
                self._by_from_table[rel.from_table].append(rel)

                if rel.to_table not in self._by_to_table:
                    self._by_to_table[rel.to_table] = []
                self._by_to_table[rel.to_table].append(rel)

                if not rel.is_active:
                    self._inactive_pairs.add((rel.from_table, rel.to_table))

            self._loaded = True
            logger.info(
                f"Loaded {len(self._relationships)} relationships ({len(self._inactive_pairs)} inactive)"
            )
            return True

        except Exception as e:
            logger.warning(f"Error loading relationships: {e}")
            return False

    def analyze_query_tables(
        self, measure_tables: List[str], filter_tables: List[str], grouping_tables: List[str]
    ) -> List[RelationshipHint]:
        """
        Analyze tables in a query and return RelationshipHint suggestions (inactive rels, crossfilter needs).
        """
        self.load_relationships()
        hints = []

        # Combine all tables involved in the query
        all_tables = set(measure_tables + filter_tables + grouping_tables)

        # Check for inactive relationships between query tables
        for rel in self._relationships:
            if not rel.is_active:
                # Check if both ends of the relationship are in the query
                if rel.from_table in all_tables and rel.to_table in all_tables:
                    hints.append(
                        RelationshipHint(
                            type="use_relationship",
                            from_table=rel.from_table,
                            from_column=rel.from_column,
                            to_table=rel.to_table,
                            to_column=rel.to_column,
                            dax_modifier=f"USERELATIONSHIP('{rel.from_table}'[{rel.from_column}], '{rel.to_table}'[{rel.to_column}])",
                            reason=f"Inactive relationship between {rel.from_table} and {rel.to_table} may need activation",
                            severity="warning",
                        )
                    )

        # Check for potential bidirectional filter needs
        for rel in self._relationships:
            if not rel.is_active:
                continue

            if rel.cross_filter_direction == "Single":
                # Check if filtering from "many" side to "one" side
                # In Power BI, relationships typically filter from "one" (to_table) to "many" (from_table)

                # If we're filtering by from_table and need to affect to_table measures
                if rel.from_table in filter_tables and rel.to_table in measure_tables:
                    hints.append(
                        RelationshipHint(
                            type="crossfilter_both",
                            from_table=rel.from_table,
                            from_column=rel.from_column,
                            to_table=rel.to_table,
                            to_column=rel.to_column,
                            dax_modifier=f"CROSSFILTER('{rel.from_table}'[{rel.from_column}], '{rel.to_table}'[{rel.to_column}], BOTH)",
                            reason=f"Filter on {rel.from_table} may need bidirectional propagation to {rel.to_table}",
                            severity="info",
                        )
                    )

        # Check for ambiguous paths (multiple relationships between same tables)
        table_pairs = {}
        for rel in self._relationships:
            pair = (min(rel.from_table, rel.to_table), max(rel.from_table, rel.to_table))
            if pair not in table_pairs:
                table_pairs[pair] = []
            table_pairs[pair].append(rel)

        for pair, rels in table_pairs.items():
            if len(rels) > 1:
                from_t, to_t = pair
                if from_t in all_tables and to_t in all_tables:
                    active_rels = [r for r in rels if r.is_active]
                    inactive_rels = [r for r in rels if not r.is_active]

                    if active_rels and inactive_rels:
                        hints.append(
                            RelationshipHint(
                                type="ambiguous_path",
                                from_table=from_t,
                                from_column="",
                                to_table=to_t,
                                to_column="",
                                dax_modifier="",
                                reason=f"Multiple relationships between {from_t} and {to_t}. "
                                f"Using active relationship on [{active_rels[0].from_column}]. "
                                f"Consider USERELATIONSHIP if different path needed.",
                                severity="info",
                            )
                        )

        return hints

    def get_dax_modifiers(
        self, measure_tables: List[str], filter_tables: List[str], grouping_tables: List[str]
    ) -> Tuple[List[str], List[RelationshipHint]]:
        """
        Returns (dax_modifiers, hints) for the query's table set.
        """
        hints = self.analyze_query_tables(measure_tables, filter_tables, grouping_tables)

        # Only return modifiers for high-confidence suggestions
        modifiers = [
            h.dax_modifier for h in hints if h.dax_modifier and h.type == "use_relationship"
        ]

        return modifiers, hints

    def get_relationships_for_tables(self, tables: List[str]) -> List[RelationshipInfo]:
        """Get all relationships involving the specified tables."""
        self.load_relationships()

        result = []
        for rel in self._relationships:
            if rel.from_table in tables or rel.to_table in tables:
                result.append(rel)

        return result

    def has_inactive_relationships(self, tables: List[str]) -> bool:
        """Check if any inactive relationships exist between the given tables."""
        self.load_relationships()

        table_set = set(tables)
        for from_t, to_t in self._inactive_pairs:
            if from_t in table_set and to_t in table_set:
                return True
        return False
