"""
Relationship CRUD Manager for MCP-PowerBi-Finvision
Provides comprehensive relationship management: create, update, delete, activate, deactivate
"""

import logging
from typing import Dict, Any, Optional
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

AMO_AVAILABLE = False
AMOServer = None
SingleColumnRelationship = None

try:
    from core.infrastructure.dll_paths import load_amo_assemblies

    load_amo_assemblies()
    from Microsoft.AnalysisServices.Tabular import (  # noqa: F401
        Server as AMOServer,
        SingleColumnRelationship,
        RelationshipEndCardinality,
        CrossFilteringBehavior,
    )

    AMO_AVAILABLE = True
    logger.info("AMO available for relationship CRUD operations")
except Exception as e:
    logger.warning(f"AMO not available for relationship CRUD: {e}")


# Try to load ADOMD
AdomdConnection = None
AdomdCommand = None

try:
    from core.infrastructure.dll_paths import load_adomd_assembly

    if load_adomd_assembly():
        from Microsoft.AnalysisServices.AdomdClient import (  # noqa: F401
            AdomdConnection,
            AdomdCommand,
        )
except Exception:
    pass
from core.operations.amo_mixins import AmoModelMixin  # noqa: E402


def _same(a, b) -> bool:
    """CLR identity: pythonnet returns a fresh proxy per access, so `is` never
    matches two references to one TOM object — compare with Equals."""
    if a is None or b is None:
        return False
    if a is b:
        return True
    try:
        return bool(a == b)
    except Exception:  # noqa: BLE001
        return False


class RelationshipCRUDManager(AmoModelMixin):
    """Manage relationship CRUD operations using TOM."""

    def __init__(self, connection):
        """Initialize with ADOMD connection."""
        self.connection = connection

    def _valid_identifier(self, s: Optional[str]) -> bool:
        """Validate identifier (table name, column name, etc.)."""
        return bool(s) and len(str(s).strip()) > 0 and len(str(s)) <= 128 and "\0" not in str(s)

    def _connection_error_msg(self) -> str:
        """P1-32: build a connection error message that includes the real
        AMO cause captured in `_last_amo_error`, rather than the generic
        "Could not connect to model"."""
        cause = getattr(self, "_last_amo_error", None) or "unknown cause"
        return f"Could not connect to model: {cause}"

    @staticmethod
    def _parse_enum(enum_type, value, names, label, aliases=None):
        """Case-insensitive enum lookup; ValueError on anything else (C-25).

        The old parsers silently fell back to a default: to_cardinality="one"
        became Many (a silent many-to-many), "Both" became OneDirection.
        """
        key = str(value if value is not None else "").strip().lower()
        key = (aliases or {}).get(key, key)
        for name in names:
            if name.lower() == key:
                return getattr(enum_type, name)
        raise ValueError(f"Invalid {label} {value!r}. Valid: {', '.join(names)}")

    def _parse_cardinality(self, cardinality_str: str):
        """Parse cardinality string to TOM enum (case-insensitive, strict)."""
        from Microsoft.AnalysisServices.Tabular import RelationshipEndCardinality  # noqa: F811

        return self._parse_enum(
            RelationshipEndCardinality, cardinality_str, ("One", "Many"), "cardinality"
        )

    def _parse_cross_filtering(self, cross_filtering_str: str):
        """Parse cross filtering behavior string to TOM enum (case-insensitive, strict)."""
        from Microsoft.AnalysisServices.Tabular import CrossFilteringBehavior  # noqa: F811

        return self._parse_enum(
            CrossFilteringBehavior,
            cross_filtering_str,
            ("OneDirection", "BothDirections", "Automatic"),
            "cross_filtering_behavior",
            aliases={"single": "onedirection", "both": "bothdirections"},
        )

    def _parse_security_filtering(self, value: str):
        from Microsoft.AnalysisServices.Tabular import SecurityFilteringBehavior

        return self._parse_enum(
            SecurityFilteringBehavior,
            value,
            ("OneDirection", "BothDirections", "None"),
            "security_filtering_behavior",
            aliases={"single": "onedirection", "both": "bothdirections"},
        )

    def _parse_join_on_date(self, value: str):
        from Microsoft.AnalysisServices.Tabular import DateTimeRelationshipBehavior

        return self._parse_enum(
            DateTimeRelationshipBehavior,
            value,
            ("DatePartOnly", "DateAndTime"),
            "join_on_date_behavior",
        )

    @staticmethod
    def _rel_tables(rel):
        ft = getattr(getattr(rel, "FromTable", None), "Name", None) or getattr(
            getattr(getattr(rel, "FromColumn", None), "Table", None), "Name", None
        )
        tt = getattr(getattr(rel, "ToTable", None), "Name", None) or getattr(
            getattr(getattr(rel, "ToColumn", None), "Table", None), "Name", None
        )
        return ft, tt

    def _active_conflict(self, model, from_table, to_table, exclude=None):
        """C-42: another ACTIVE relationship between the same two tables (either
        direction) makes the path ambiguous; the engine refuses it or silently
        deactivates one on save."""
        pair = {from_table, to_table}
        for rel in list(getattr(model, "Relationships", None) or []):
            if _same(rel, exclude) or not bool(getattr(rel, "IsActive", False)):
                continue
            if set(self._rel_tables(rel)) == pair:
                return getattr(rel, "Name", "?")
        return None

    def create_relationship(
        self,
        from_table: str,
        from_column: str,
        to_table: str,
        to_column: str,
        name: Optional[str] = None,
        from_cardinality: str = "Many",
        to_cardinality: str = "One",
        cross_filtering_behavior: str = "OneDirection",
        is_active: bool = True,
        allow_ambiguous: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a new relationship between tables.

        Args:
            from_table: Source table name
            from_column: Source column name
            to_table: Target table name
            to_column: Target column name
            name: Relationship name (optional, auto-generated if not provided)
            from_cardinality: Source cardinality (Many, One)
            to_cardinality: Target cardinality (Many, One)
            cross_filtering_behavior: OneDirection, BothDirections, Automatic
            is_active: Whether relationship is active (default: True)

        Returns:
            Result dictionary with success status
        """
        if not AMO_AVAILABLE:
            return error_envelope(
                "amo_unavailable", "AMO not available - cannot create relationships"
            )

        if not all(
            [self._valid_identifier(x) for x in [from_table, from_column, to_table, to_column]]
        ):
            return error_envelope(
                "invalid_parameters", "Table and column names must be non-empty and <=128 chars"
            )

        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }

        try:
            res = self._apply_create_relationship(
                model,
                from_table=from_table,
                from_column=from_column,
                to_table=to_table,
                to_column=to_column,
                name=name,
                from_cardinality=from_cardinality,
                to_cardinality=to_cardinality,
                cross_filtering_behavior=cross_filtering_behavior,
                is_active=is_active,
                allow_ambiguous=allow_ambiguous,
            )
            if res.get("success"):
                from core.operations.write_lane import commit

                res.update(commit(model, label="relationship/create"))
                logger.info(
                    f"Created relationship from {from_table}[{from_column}] "
                    f"to {to_table}[{to_column}]"
                )
            return res

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error creating relationship: {error_msg}", exc_info=True)
            return {
                "success": False,
                "error": error_msg,
                "error_type": "creation_error",
                "suggestions": [
                    "Verify column data types are compatible",
                    "Check if relationship already exists between these columns",
                    "Ensure no circular dependencies in relationships",
                ],
            }

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def _apply_create_relationship(
        self,
        model,
        *,
        from_table,
        from_column,
        to_table,
        to_column,
        name=None,
        from_cardinality="Many",
        to_cardinality="One",
        cross_filtering_behavior="OneDirection",
        is_active=True,
        allow_ambiguous=False,
    ) -> Dict[str, Any]:
        """Mutate `model` to add a relationship. Does NOT SaveChanges or Disconnect."""
        try:
            from_card = self._parse_cardinality(from_cardinality)
            to_card = self._parse_cardinality(to_cardinality)
            cross = self._parse_cross_filtering(cross_filtering_behavior)
        except ValueError as e:
            return error_envelope("invalid_parameters", str(e))
        # Find tables
        from_table_obj = next((t for t in model.Tables if t.Name == from_table), None)
        to_table_obj = next((t for t in model.Tables if t.Name == to_table), None)

        if not from_table_obj:
            return error_envelope("table_not_found", f"Source table '{from_table}' not found")

        if not to_table_obj:
            return error_envelope("table_not_found", f"Target table '{to_table}' not found")

        # Find columns
        from_column_obj = next((c for c in from_table_obj.Columns if c.Name == from_column), None)
        to_column_obj = next((c for c in to_table_obj.Columns if c.Name == to_column), None)

        if not from_column_obj:
            return error_envelope(
                "column_not_found",
                f"Source column '{from_column}' not found in table '{from_table}'",
            )

        if not to_column_obj:
            return error_envelope(
                "column_not_found", f"Target column '{to_column}' not found in table '{to_table}'"
            )

        # C-42: duplicate / ambiguous-path checks BEFORE mutating.
        for rel in list(getattr(model, "Relationships", None) or []):
            if _same(getattr(rel, "FromColumn", None), from_column_obj) and _same(
                getattr(rel, "ToColumn", None), to_column_obj
            ):
                return error_envelope(
                    "duplicate_relationship",
                    f"A relationship {from_table}[{from_column}] -> {to_table}[{to_column}] "
                    f"already exists ('{rel.Name}')",
                )
        if is_active and not allow_ambiguous:
            clash = self._active_conflict(model, from_table, to_table)
            if clash:
                return error_envelope(
                    "ambiguous_path",
                    f"'{from_table}' and '{to_table}' already have an active relationship "
                    f"('{clash}'); a second active one makes the filter path ambiguous. "
                    "Create it with is_active=false (use USERELATIONSHIP), or pass "
                    "allow_ambiguous=true.",
                )

        # Create relationship
        from Microsoft.AnalysisServices.Tabular import SingleColumnRelationship  # noqa: F811

        relationship = SingleColumnRelationship()

        # Set name (auto-generate if not provided)
        if name and self._valid_identifier(name):
            relationship.Name = name
        else:
            # Auto-generate name
            relationship.Name = f"{from_table}_{from_column}_to_{to_table}_{to_column}"

        # Set columns
        relationship.FromColumn = from_column_obj
        relationship.ToColumn = to_column_obj

        # Set cardinality
        relationship.FromCardinality = from_card
        relationship.ToCardinality = to_card

        # Set cross filtering behavior
        relationship.CrossFilteringBehavior = cross

        # Set active state
        relationship.IsActive = is_active

        # Add to model
        model.Relationships.Add(relationship)

        return {
            "success": True,
            "action": "created",
            "name": relationship.Name,
            "from_table": from_table,
            "from_column": from_column,
            "to_table": to_table,
            "to_column": to_column,
            "from_cardinality": from_cardinality,
            "to_cardinality": to_cardinality,
            "cross_filtering_behavior": cross_filtering_behavior,
            "is_active": is_active,
            "message": f"Successfully created relationship '{relationship.Name}'",
        }

    def update_relationship(
        self,
        relationship_name: str,
        cross_filtering_behavior: Optional[str] = None,
        is_active: Optional[bool] = None,
        new_name: Optional[str] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        """
        Update an existing relationship.

        C-42: extra accepts from_cardinality, to_cardinality,
        security_filtering_behavior, rely_on_referential_integrity,
        join_on_date_behavior and allow_ambiguous.

        Args:
            relationship_name: Current relationship name
            cross_filtering_behavior: New cross filtering behavior (optional)
            is_active: New active state (optional)
            new_name: New relationship name (optional)

        Returns:
            Result dictionary with success status
        """
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")

        if not self._valid_identifier(relationship_name):
            return error_envelope(
                "invalid_parameters", "Relationship name must be non-empty and <=128 chars"
            )

        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }

        try:
            res = self._apply_update_relationship(
                model,
                relationship_name=relationship_name,
                cross_filtering_behavior=cross_filtering_behavior,
                is_active=is_active,
                new_name=new_name,
                **extra,
            )
            if res.get("success"):
                from core.operations.write_lane import commit

                res.update(commit(model, label="relationship/update"))
                logger.info(
                    f"Updated relationship '{relationship_name}': "
                    f"{', '.join(res.get('updates', []))}"
                )
            return res

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error updating relationship: {error_msg}", exc_info=True)
            return error_envelope("update_error", error_msg)

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def _apply_update_relationship(
        self,
        model,
        *,
        relationship_name,
        cross_filtering_behavior=None,
        is_active=None,
        new_name=None,
        from_cardinality=None,
        to_cardinality=None,
        security_filtering_behavior=None,
        rely_on_referential_integrity=None,
        join_on_date_behavior=None,
        allow_ambiguous=False,
    ) -> Dict[str, Any]:
        """Mutate `model` to update a relationship. Does NOT SaveChanges or Disconnect."""
        # Find relationship
        relationship = next((r for r in model.Relationships if r.Name == relationship_name), None)
        if not relationship:
            return error_envelope(
                "relationship_not_found", f"Relationship '{relationship_name}' not found"
            )

        updates = []

        # Parse every enum BEFORE mutating (C-25: no silent defaults).
        try:
            parsed = {}
            if cross_filtering_behavior is not None:
                parsed["CrossFilteringBehavior"] = self._parse_cross_filtering(
                    cross_filtering_behavior
                )
            if from_cardinality is not None:
                parsed["FromCardinality"] = self._parse_cardinality(from_cardinality)
            if to_cardinality is not None:
                parsed["ToCardinality"] = self._parse_cardinality(to_cardinality)
            if security_filtering_behavior is not None:
                parsed["SecurityFilteringBehavior"] = self._parse_security_filtering(
                    security_filtering_behavior
                )
            if join_on_date_behavior is not None:
                parsed["JoinOnDateBehavior"] = self._parse_join_on_date(join_on_date_behavior)
        except ValueError as e:
            return error_envelope("invalid_parameters", str(e))

        if is_active and not bool(getattr(relationship, "IsActive", False)) and not allow_ambiguous:
            ft, tt = self._rel_tables(relationship)
            clash = self._active_conflict(model, ft, tt, exclude=relationship)
            if clash:
                return error_envelope(
                    "ambiguous_path",
                    f"Activating '{relationship_name}' would make the path between "
                    f"'{ft}' and '{tt}' ambiguous (active: '{clash}'). Deactivate that "
                    "one first, or pass allow_ambiguous=true.",
                )

        for prop, value in parsed.items():
            setattr(relationship, prop, value)
            updates.append(prop)
        if rely_on_referential_integrity is not None:
            relationship.RelyOnReferentialIntegrity = bool(rely_on_referential_integrity)
            updates.append("RelyOnReferentialIntegrity")

        # Update active state
        if is_active is not None:
            relationship.IsActive = is_active
            updates.append("is_active")

        # Update name
        if new_name and self._valid_identifier(new_name):
            # Check if new name already exists
            if any(r.Name == new_name for r in model.Relationships if r != relationship):
                return error_envelope("name_conflict", f"Relationship '{new_name}' already exists")
            relationship.Name = new_name
            updates.append("name")

        return {
            "success": True,
            "action": "updated",
            "name": new_name if new_name else relationship_name,
            "original_name": relationship_name if new_name else None,
            "updates": updates,
            "message": f"Successfully updated relationship '{relationship_name}'",
        }

    def _apply_activate_relationship(self, model, *, relationship_name) -> Dict[str, Any]:
        """Mutate `model` to activate a relationship (toggle IsActive=True). Does NOT
        SaveChanges or Disconnect. Mirrors the public `activate_relationship`, which
        delegates to `update_relationship`; this helper exists for the single-
        connection atomic batch path."""
        return self._apply_update_relationship(
            model, relationship_name=relationship_name, is_active=True
        )

    def _apply_deactivate_relationship(self, model, *, relationship_name) -> Dict[str, Any]:
        """Mutate `model` to deactivate a relationship (toggle IsActive=False). Does
        NOT SaveChanges or Disconnect. Mirrors the public `deactivate_relationship`,
        which delegates to `update_relationship`; this helper exists for the single-
        connection atomic batch path."""
        return self._apply_update_relationship(
            model, relationship_name=relationship_name, is_active=False
        )

    def _apply_rename_relationship(self, model, *, relationship_name, new_name) -> Dict[str, Any]:
        """Mutate `model` to rename a relationship. Does NOT SaveChanges or Disconnect.
        Mirrors the public `rename_relationship`, which delegates to
        `update_relationship`; this helper exists for the single-connection atomic
        batch path."""
        return self._apply_update_relationship(
            model, relationship_name=relationship_name, new_name=new_name
        )

    def delete_relationship(self, relationship_name: str) -> Dict[str, Any]:
        """
        Delete a relationship.

        Args:
            relationship_name: Name of the relationship to delete

        Returns:
            Result dictionary with success status
        """
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")

        if not self._valid_identifier(relationship_name):
            return error_envelope(
                "invalid_parameters", "Relationship name must be non-empty and <=128 chars"
            )

        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }

        try:
            res = self._apply_delete_relationship(model, relationship_name=relationship_name)
            if res.get("success"):
                from core.operations.write_lane import commit

                commit(model, label="relationship/write")
                logger.info(f"Deleted relationship '{relationship_name}'")
            return res

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error deleting relationship: {error_msg}", exc_info=True)
            return error_envelope("deletion_error", error_msg)

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def _apply_delete_relationship(self, model, *, relationship_name) -> Dict[str, Any]:
        """Mutate `model` to remove a relationship. Does NOT SaveChanges or Disconnect."""
        # Find relationship
        relationship = next((r for r in model.Relationships if r.Name == relationship_name), None)
        if not relationship:
            return error_envelope(
                "relationship_not_found", f"Relationship '{relationship_name}' not found"
            )

        # Remove relationship
        model.Relationships.Remove(relationship)

        return {
            "success": True,
            "action": "deleted",
            "name": relationship_name,
            "message": f"Successfully deleted relationship '{relationship_name}'",
        }

    def activate_relationship(self, relationship_name: str) -> Dict[str, Any]:
        """
        Activate a relationship.

        Args:
            relationship_name: Name of the relationship to activate

        Returns:
            Result dictionary with success status
        """
        return self.update_relationship(relationship_name=relationship_name, is_active=True)

    def deactivate_relationship(self, relationship_name: str) -> Dict[str, Any]:
        """
        Deactivate a relationship.

        Args:
            relationship_name: Name of the relationship to deactivate

        Returns:
            Result dictionary with success status
        """
        return self.update_relationship(relationship_name=relationship_name, is_active=False)

    def rename_relationship(self, relationship_name: str, new_name: str) -> Dict[str, Any]:
        """
        Rename a relationship.

        Args:
            relationship_name: Current relationship name
            new_name: New relationship name

        Returns:
            Result dictionary with success status
        """
        return self.update_relationship(relationship_name=relationship_name, new_name=new_name)
