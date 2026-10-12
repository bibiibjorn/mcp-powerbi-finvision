"""
Model Validator for PBIXRay MCP Server
Validates model integrity and data quality
"""

import logging
import random
import time
from typing import Dict, Any, List, Optional, Tuple
from core.config.config_manager import config
from core.execution.dmv_helper import get_field_value
from core.dax.dax_names import column_ref, quote_table


def _scalar(row: Dict[str, Any], name: str, default: Any = 0) -> Any:
    """Row value by name, bracketed or not (ROW() results come back as [Name])."""
    if not isinstance(row, dict):
        return default
    for key in (name, f"[{name}]"):
        if key in row and row[key] is not None:
            return row[key]
    return default


def _is_many(value: Any) -> bool:
    """True for a Many relationship end (enum name or its numeric value 2)."""
    return str(value).strip().split(".")[-1].lower() in {"many", "2"}


logger = logging.getLogger(__name__)

#: Scalar expressions evaluated per ``ROW(...)`` round trip (P-04). One query per
#: relationship / key column used to cost up to 4R sequential full-column scans.
INTEGRITY_BATCH_SIZE = 20


class ModelValidator:
    """Validate model integrity and data quality."""

    def __init__(self, query_executor):
        """Initialize with query executor."""
        self.executor = query_executor
        self._sampling_warnings = []
        self._deadline: Optional[float] = None
        self._budget_exhausted = False

    def _over_budget(self) -> bool:
        if self._deadline is not None and time.time() > self._deadline:
            self._budget_exhausted = True
        return self._budget_exhausted

    def _eval_scalars(self, specs: List[Tuple[str, str]]) -> Dict[str, Any]:
        """Evaluate ``[(name, scalar DAX expression), ...]`` in batched ``ROW(...)`` queries.

        P-04: ``INTEGRITY_BATCH_SIZE`` expressions per round trip (``bypass_cache``
        as before). A batch the engine rejects is retried one expression at a time
        so a single bad column only loses ITS check (as the old per-item query
        did). Stops between round trips once the integrity budget (the
        orchestrator's ``max_seconds`` deadline) is exhausted. Names missing from
        the result had no value (failed / skipped) -- callers treat them as "no
        issue", exactly like the old per-item ``success and rows`` test.
        """
        values: Dict[str, Any] = {}

        def run(batch: List[Tuple[str, str]]) -> bool:
            cols = ", ".join(f'"{name}", {expr}' for name, expr in batch)
            result = self.executor.validate_and_execute_dax(
                f"EVALUATE\nROW({cols})", 0, bypass_cache=True
            )
            if result.get("success") and result.get("rows"):
                row = result["rows"][0]
                for name, _expr in batch:
                    values[name] = _scalar(row, name, 0)
                return True
            return False

        for start in range(0, len(specs), INTEGRITY_BATCH_SIZE):
            if self._over_budget():
                break
            batch = specs[start : start + INTEGRITY_BATCH_SIZE]
            if run(batch) or len(batch) == 1:
                continue
            for spec in batch:  # retry singly: one bad column must not hide the others
                if self._over_budget():
                    break
                run([spec])
        return values

    def _get_sampled_relationships(
        self, relationships: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Apply sampling to relationships list based on configuration.

        Args:
            relationships: Full list of relationships from INFO.RELATIONSHIPS()

        Returns:
            Sampled list (or full list if no limit configured)
        """
        max_items = config.get("validation.max_relationships_check", None)
        warn_on_sampling = config.get("validation.warn_on_sampling", True)

        # If no limit, return all relationships
        if max_items is None:
            return relationships

        # If limit is greater than or equal to actual count, return all
        if len(relationships) <= max_items:
            return relationships

        # Sample relationships
        sampled = random.sample(relationships, max_items)

        # Add warning about sampling
        if warn_on_sampling:
            warning_msg = f"Sampled {max_items} of {len(relationships)} relationships for validation (full validation disabled)"
            self._sampling_warnings.append(warning_msg)
            logger.warning(warning_msg)

        return sampled

    def validate_model_integrity(self, deadline: Optional[float] = None) -> Dict[str, Any]:
        """
        Comprehensive model validation.

        Args:
            deadline: optional ``time.time()`` timestamp; the data checks stop
                issuing queries after it and the result is marked ``partial``.

        Returns:
            Validation results with issues found
        """
        issues = []
        self._sampling_warnings = []  # Reset warnings for this validation run
        self._deadline = deadline
        self._budget_exhausted = False

        try:
            # Check 1: Relationships with missing keys
            logger.info("Checking relationships...")
            rel_issues = self._check_relationship_integrity()
            if rel_issues:
                issues.extend(rel_issues)

            # Check 2: Duplicate keys in dimension tables
            logger.info("Checking for duplicate keys...")
            dup_issues = self._check_duplicate_keys()
            if dup_issues:
                issues.extend(dup_issues)

            # Check 3: Null values in key columns
            logger.info("Checking for null keys...")
            null_issues = self._check_null_keys()
            if null_issues:
                issues.extend(null_issues)

            # Check 4: Invalid measure references
            logger.info("Checking measure references...")
            measure_issues = self._check_measure_references()
            if measure_issues:
                issues.extend(measure_issues)

            # Check 5: Circular relationships
            logger.info("Checking for circular relationships...")
            circular_issues = self._check_circular_relationships()
            if circular_issues:
                issues.extend(circular_issues)

            # Categorize by severity
            critical = [i for i in issues if i["severity"] == "critical"]
            high = [i for i in issues if i["severity"] == "high"]
            medium = [i for i in issues if i["severity"] == "medium"]
            low = [i for i in issues if i["severity"] == "low"]

            is_valid = len(critical) == 0 and len(high) == 0

            result = {
                "success": True,
                "is_valid": is_valid,
                "total_issues": len(issues),
                "issues": issues,
                "summary": {
                    "critical_issues": len(critical),
                    "high_issues": len(high),
                    "medium_issues": len(medium),
                    "low_issues": len(low),
                },
                "recommendation": (
                    "Fix critical and high issues before deploying"
                    if not is_valid
                    else "Model passed validation"
                ),
            }

            # Include sampling warnings if any
            if self._sampling_warnings:
                result["warnings"] = self._sampling_warnings
                result["validation_scope"] = "sampled"
            else:
                result["validation_scope"] = "full"
            if self._budget_exhausted:
                result["warnings"] = list(result.get("warnings", [])) + [
                    "Integrity budget exhausted: some key / orphan checks did not run "
                    "(raise max_seconds or narrow the scope)"
                ]
                result["validation_scope"] = "partial"
                result["budget_exhausted"] = True

            return result

        except Exception as e:
            logger.error(f"Validation error: {e}")
            return {"success": False, "error": str(e)}

    def _check_relationship_integrity(self) -> List[Dict[str, Any]]:
        """Check for orphaned records in relationships."""
        issues = []

        try:
            rels_result = self.executor.execute_info_query("RELATIONSHIPS", top_n=5000)
            if not rels_result.get("success"):
                return issues

            # Apply sampling based on configuration (default: unlimited)
            relationships = self._get_sampled_relationships(rels_result["rows"])

            work: List[Tuple[str, str, str, str, str]] = []
            specs: List[Tuple[str, str]] = []
            for rel in relationships:
                from_table = get_field_value(rel, ["FromTable"])
                from_col = get_field_value(rel, ["FromColumn"])
                to_table = get_field_value(rel, ["ToTable"])
                to_col = get_field_value(rel, ["ToColumn"])
                if not from_table or not from_col or not to_table or not to_col:
                    # Skip incomplete rows
                    continue

                # C-34: key-set difference instead of RELATED(), which fails
                # (silently skipped) on INACTIVE relationships. Counts distinct
                # non-blank "many"-side keys with no match on the "one" side.
                fk = column_ref(from_table, from_col)
                pk = column_ref(to_table, to_col)
                name = f"o{len(work)}"
                expr = (
                    f"COUNTROWS(EXCEPT("
                    f'FILTER(DISTINCT(SELECTCOLUMNS({quote_table(from_table)}, "k", {fk})), NOT ISBLANK([k])), '
                    f'DISTINCT(SELECTCOLUMNS({quote_table(to_table)}, "k", {pk})))) + 0'
                )
                work.append((name, from_table, from_col, to_table, to_col))
                specs.append((name, expr))

            values = self._eval_scalars(specs)
            for name, from_table, from_col, to_table, to_col in work:
                if name not in values:
                    continue
                orphaned = values[name] or 0
                if orphaned > 0:
                    issues.append(
                        {
                            "type": "orphaned_records",
                            "severity": "high",
                            "relationship": f"{from_table}[{from_col}] -> {to_table}[{to_col}]",
                            "description": f"{orphaned} key value(s) in {from_table}[{from_col}] have no match in {to_table}[{to_col}]",
                            "recommendation": f"Add records to {to_table} or filter out orphaned records",
                        }
                    )

        except Exception as e:
            logger.warning(f"Error checking relationship integrity: {e}")

        return issues

    def _check_duplicate_keys(self) -> List[Dict[str, Any]]:
        """Check for duplicate keys in dimension tables."""
        issues = []

        try:
            # Get relationships to identify dimension tables
            rels_result = self.executor.execute_info_query("RELATIONSHIPS", top_n=5000)
            if not rels_result.get("success"):
                return issues

            # Apply sampling based on configuration (default: unlimited)
            relationships = self._get_sampled_relationships(rels_result["rows"])

            # Check "to" side of relationships (dimension side)
            checked_tables = set()
            work: List[Tuple[int, str, str]] = []
            specs: List[Tuple[str, str]] = []

            for rel in relationships:
                to_table = get_field_value(rel, ["ToTable"])
                to_col = get_field_value(rel, ["ToColumn"])
                if not to_table or not to_col:
                    continue
                # C-34: only a ONE-sided end must be unique; many-to-many "to"
                # sides legitimately repeat keys.
                if _is_many(get_field_value(rel, ["ToCardinality"])):
                    continue

                table_col = f"{to_table}[{to_col}]"
                if table_col in checked_tables:
                    continue

                checked_tables.add(table_col)

                i = len(work)
                work.append((i, to_table, to_col))
                specs.append((f"t{i}", f"COUNTROWS({quote_table(to_table)})"))
                specs.append((f"u{i}", f"DISTINCTCOUNT({column_ref(to_table, to_col)})"))

            values = self._eval_scalars(specs)
            for i, to_table, to_col in work:
                if f"t{i}" not in values or f"u{i}" not in values:
                    continue
                total = values[f"t{i}"] or 0
                unique = values[f"u{i}"] or 0

                if total != unique and total > 0:
                    issues.append(
                        {
                            "type": "duplicate_keys",
                            "severity": "critical",
                            "table": to_table,
                            "column": to_col,
                            "description": f"Duplicate keys found in {to_table}[{to_col}]",
                            "duplicate_count": total - unique,
                            "recommendation": f"Remove duplicates from {to_table} or use SUMMARIZE to create unique keys",
                        }
                    )

        except Exception as e:
            logger.warning(f"Error checking duplicate keys: {e}")

        return issues

    def _check_null_keys(self) -> List[Dict[str, Any]]:
        """Check for null values in key columns."""
        issues = []

        try:
            rels_result = self.executor.execute_info_query("RELATIONSHIPS", top_n=5000)
            if not rels_result.get("success"):
                return issues

            # Apply sampling based on configuration (default: unlimited)
            relationships = self._get_sampled_relationships(rels_result["rows"])

            checked = set()
            work: List[Tuple[str, str, str]] = []
            specs: List[Tuple[str, str]] = []

            for rel in relationships:
                # Check both sides
                for side in ["From", "To"]:
                    table = get_field_value(rel, [f"{side}Table"])
                    col = get_field_value(rel, [f"{side}Column"])
                    if not table or not col:
                        continue

                    table_col = f"{table}[{col}]"
                    if table_col in checked:
                        continue

                    checked.add(table_col)
                    name = f"n{len(work)}"
                    work.append((name, table, col))
                    specs.append((name, f"COUNTBLANK({column_ref(table, col)})"))

            values = self._eval_scalars(specs)
            for name, table, col in work:
                if name not in values:
                    continue
                null_count = values[name] or 0
                if null_count > 0:
                    issues.append(
                        {
                            "type": "null_keys",
                            "severity": "high",
                            "table": table,
                            "column": col,
                            "description": f"{null_count} null values in key column {table}[{col}]",
                            "recommendation": f"Replace null values in {table}[{col}] with valid keys or 'Unknown'",
                        }
                    )

        except Exception as e:
            logger.warning(f"Error checking null keys: {e}")

        return issues

    def _check_measure_references(self) -> List[Dict[str, Any]]:
        """Check for invalid measure references using basic bracket-reference extraction."""
        import re

        issues = []

        try:
            measures_result = self.executor.execute_info_query("MEASURES", top_n=5000)
            if not measures_result.get("success"):
                return issues

            # Build set of all known measure names
            measure_names = set()
            for m in measures_result["rows"]:
                name = m.get("Name")
                if name:
                    measure_names.add(name)

            # Also get column names to exclude from measure-ref checks
            columns_result = self.executor.execute_info_query("COLUMNS", top_n=5000)
            column_names = set()
            if columns_result.get("success"):
                for c in columns_result["rows"]:
                    col_name = c.get("Name") or c.get("[ExplicitName]")
                    if col_name:
                        column_names.add(col_name)

            # Pattern: standalone [Name] references (not preceded by table name or ')
            # This is a heuristic — a full DAX parser would be more accurate
            bracket_ref_pattern = re.compile(r'(?<![\'"\w])\[([^\]]+)\]')

            for measure in measures_result["rows"]:
                expr = measure.get("Expression", "")
                if not expr:
                    continue

                measure_name = measure.get("Name", "")
                table_name = measure.get("TableName", "") or measure.get("[TableID]", "")

                # Extract bracket references from the expression
                refs = bracket_ref_pattern.findall(expr)
                for ref in refs:
                    # Skip known columns, the measure itself, and common DAX keywords
                    if ref in column_names or ref == measure_name:
                        continue
                    if ref.upper() in ("VALUE", "VALUES", "ROWS", "COLUMNS"):
                        continue

                    # If ref looks like a measure name but isn't in our set, flag it
                    if ref not in measure_names and not ref.startswith("$"):
                        issues.append(
                            {
                                "type": "invalid_measure_reference",
                                "severity": "medium",
                                "table": table_name,
                                "measure": measure_name,
                                "reference": ref,
                                "description": f"Measure '{measure_name}' references unknown '[{ref}]' — may be a renamed or deleted measure",
                                "recommendation": f"Verify that '[{ref}]' exists in the model or update the reference",
                            }
                        )

        except Exception as e:
            logger.warning(f"Error checking measure references: {e}")

        return issues

    def _check_circular_relationships(self) -> List[Dict[str, Any]]:
        """Check for circular relationship chains."""
        issues = []

        try:
            rels_result = self.executor.execute_info_query("RELATIONSHIPS", top_n=5000)
            if not rels_result.get("success"):
                return issues

            # Build relationship graph
            graph = {}
            for rel in rels_result["rows"]:
                from_table = rel.get("FromTable")
                to_table = rel.get("ToTable")

                if from_table not in graph:
                    graph[from_table] = []
                graph[from_table].append(to_table)

            # Check for cycles using DFS
            def has_cycle(node, visited, rec_stack):
                visited.add(node)
                rec_stack.add(node)

                if node in graph:
                    for neighbor in graph[node]:
                        if neighbor not in visited:
                            if has_cycle(neighbor, visited, rec_stack):
                                return True
                        elif neighbor in rec_stack:
                            return True

                rec_stack.remove(node)
                return False

            visited = set()
            for node in graph:
                if node not in visited:
                    if has_cycle(node, visited, set()):
                        issues.append(
                            {
                                "type": "circular_relationship",
                                "severity": "critical",
                                "description": "Circular relationship chain detected",
                                "recommendation": "Review relationship directions and remove circular chains",
                            }
                        )
                        break  # Only report once

        except Exception as e:
            logger.warning(f"Error checking circular relationships: {e}")

        return issues

    def validate_model(self, deadline: Optional[float] = None) -> Dict[str, Any]:
        """
        Alias for validate_model_integrity for backward compatibility.

        Returns:
            Validation results with issues found
        """
        return self.validate_model_integrity(deadline=deadline)
