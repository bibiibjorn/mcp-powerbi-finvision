"""Model-level TOM property read/write (Model.ValueFilterBehavior et al).

Sibling of ``ModelRefreshManager``: both operate on the model object itself
rather than a child collection. Uses the same ``amo_helpers.get_server_db_model``
acquisition helper and the same SaveChanges-once discipline.

``ValueFilterBehavior`` (compat level 1600+) controls how SUMMARIZECOLUMNS
resolves multiple filters that target the same table:

- ``Independent`` — each value filter is evaluated independently. Correct default
  for models authored 2025+.
- ``Coalesced`` — the legacy behaviour; filters are coalesced, which produces
  surprising SUMMARIZECOLUMNS results when several filters hit one table.
- ``Automatic`` — resolves to ``Coalesced`` on models that predate the setting,
  so it is NOT a safe "leave it alone" value.

Older AMO SDK builds (and models below compat level 1600) do not expose the
property at all. Every access is guarded so the manager degrades to a clear
structured error instead of raising a CLR/AttributeError.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

_CULTURE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


def _as_bool(value: Any) -> Optional[bool]:
    """True/False for a bool or a 'true'/'false' string, else None."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    return None


from core.operations.write_lane import commit as _lane_commit  # noqa: E402
from core.operations.write_lane import safe_undo  # noqa: E402
from core.operations.amo_mixins import AmoSessionMixin  # noqa: E402


def supported_compatibility_levels() -> Optional[List[int]]:
    """Compatibility levels the loaded TOM build knows (``Constants.CompatibilityLevel``), sorted.

    None when the assemblies / the internal constant are unavailable (tests, other builds): the
    caller then skips the allow-list check. Offline there is no engine to reject an unknown level
    (N-21), so this is the only line of defence there.
    """
    try:
        from core.infrastructure.dll_paths import load_amo_assemblies

        if not load_amo_assemblies():
            return None
        import Microsoft.AnalysisServices.Tabular as tom  # type: ignore
        from System.Reflection import BindingFlags  # type: ignore

        asm = tom.Database().GetType().Assembly
        holder = asm.GetType(
            "Microsoft.AnalysisServices.Tabular.Metadata.Constants+CompatibilityLevel"
        )
        field = holder.GetField(
            "SupportedCompatibilityLevels",
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static,
        )
        levels = sorted(int(x) for x in field.GetValue(None))
        return levels or None
    except Exception as e:  # noqa: BLE001 - CLR interop; skip the check rather than block
        logger.debug("supported compatibility levels unavailable: %s", e)
        return None


# Compatibility level that introduced Model.ValueFilterBehavior.
VALUE_FILTER_BEHAVIOR_MIN_CL = (
    1606  # Model.ValueFilterBehavior: CL 1606+ (Microsoft docs, TOM 19.117)
)

# Canonical enum member names, keyed by lower-case for case-insensitive input.
_BEHAVIORS = {
    "automatic": "Automatic",
    "independent": "Independent",
    "coalesced": "Coalesced",
}

VALID_BEHAVIORS = ["Automatic", "Independent", "Coalesced"]


def _enum_name(value: Any) -> Optional[str]:
    """Render a TOM enum value (or already-string value) as its member name.

    pythonnet surfaces a CLR enum as an object whose ``str()`` is the member
    name, but some builds hand back the underlying int. Map ints back through
    the canonical ordering so the caller never sees a raw number.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        # TOM ValueFilterBehaviorType: Automatic=0, Independent=1, Coalesced=2
        by_ordinal = {0: "Automatic", 1: "Independent", 2: "Coalesced"}
        return by_ordinal.get(value)
    text = str(value).strip()
    if not text:
        return None
    return _BEHAVIORS.get(text.lower(), text)


class ModelPropertiesManager(AmoSessionMixin):
    """Read and write model-level TOM properties."""

    def __init__(self):
        self._connection_state = None

    @property
    def connection_state(self):
        if self._connection_state is None:
            from core.infrastructure.connection_state import connection_state

            self._connection_state = connection_state
        return self._connection_state

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _compatibility_level(db) -> Optional[int]:
        try:
            level = getattr(db, "CompatibilityLevel", None)
            return int(level) if level is not None else None
        except Exception:
            return None

    @staticmethod
    def _supports_value_filter_behavior(model) -> bool:
        """True when the loaded TOM SDK exposes Model.ValueFilterBehavior."""
        try:
            return hasattr(model, "ValueFilterBehavior")
        except Exception:
            return False

    @classmethod
    def _unsupported_response(cls, level: Optional[int]) -> Dict[str, Any]:
        return {
            "success": False,
            "error": (
                "Model.ValueFilterBehavior is not exposed by the loaded AMO/TOM "
                "assemblies. It requires a TOM SDK that supports compatibility "
                f"level {VALUE_FILTER_BEHAVIOR_MIN_CL}+ (update Power BI Desktop / "
                "the bundled AMO DLLs in lib/dotnet)."
            ),
            "error_type": "unsupported_by_amo",
            "required_compatibility_level": VALUE_FILTER_BEHAVIOR_MIN_CL,
            "compatibility_level": level,
        }

    @classmethod
    def _low_cl_response(cls, level: Optional[int]) -> Dict[str, Any]:
        return {
            "success": False,
            "error": (
                f"This model is at compatibility level {level}; "
                f"ValueFilterBehavior requires {VALUE_FILTER_BEHAVIOR_MIN_CL}+. "
                "Raise the model's compatibility level first."
            ),
            "error_type": "compatibility_level_too_low",
            "required_compatibility_level": VALUE_FILTER_BEHAVIOR_MIN_CL,
            "compatibility_level": level,
        }

    # ---- public ops ------------------------------------------------------

    def get_properties(self) -> Dict[str, Any]:
        """Read model-level properties (read-only, no SaveChanges).

        ``value_filter_behavior`` is the enum MEMBER NAME (never the raw CLR
        int). It is ``None`` with ``value_filter_behavior_supported=False`` when
        the SDK/model can't express it — callers should treat that as "unknown",
        not as "Automatic".
        """
        server, db, model, err = self._get_server_db_model()
        if err:
            return error_envelope("connection_error", err)

        try:
            level = self._compatibility_level(db)
            supported = self._supports_value_filter_behavior(model)
            behavior = None
            if supported:
                try:
                    behavior = _enum_name(getattr(model, "ValueFilterBehavior", None))
                except Exception as e:  # pragma: no cover - CLR-only path
                    logger.debug("Reading ValueFilterBehavior failed: %s", e)

            result: Dict[str, Any] = {
                "success": True,
                "value_filter_behavior": behavior,
                "value_filter_behavior_supported": bool(supported),
                "compatibility_level": level,
            }
            for key, attr in (("culture", "Culture"), ("default_mode", "DefaultMode")):
                try:
                    value = getattr(model, attr, None)
                except Exception:  # pragma: no cover - CLR-only path
                    value = None
                if value is not None:
                    result[key] = str(value)
            if behavior in ("Automatic", "Coalesced"):
                result["advisory"] = (
                    f"ValueFilterBehavior is '{behavior}'. 'Automatic' resolves to the "
                    "legacy 'Coalesced' behaviour on older models, which can make "
                    "SUMMARIZECOLUMNS return surprising results when several filters "
                    "target the same table. 'Independent' is the correct setting for "
                    "models authored 2025+."
                )
            return result
        except Exception as e:
            logger.error("get_properties failed: %s", e)
            return error_envelope("tom_error", str(e))
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def set_value_filter_behavior(self, behavior: Optional[str] = None) -> Dict[str, Any]:
        """Set Model.ValueFilterBehavior. Validates BEFORE touching the model."""
        canonical = _BEHAVIORS.get(str(behavior or "").strip().lower())
        if canonical is None:
            return {
                "success": False,
                "error": (
                    f"Invalid behavior {behavior!r}. Valid values (case-insensitive): "
                    f"{VALID_BEHAVIORS}"
                ),
                "error_type": "invalid_parameters",
                "valid_values": VALID_BEHAVIORS,
            }

        server, db, model, err = self._get_server_db_model()
        if err:
            return error_envelope("connection_error", err)

        try:
            level = self._compatibility_level(db)
            if not self._supports_value_filter_behavior(model):
                return self._unsupported_response(level)
            if level is not None and level < VALUE_FILTER_BEHAVIOR_MIN_CL:
                return self._low_cl_response(level)

            enum_type = self._resolve_enum_member(canonical)
            if enum_type is None:
                return self._unsupported_response(level)

            previous = _enum_name(getattr(model, "ValueFilterBehavior", None))
            try:
                model.ValueFilterBehavior = enum_type
                _lane_commit(model, label="model/set_property")
            except Exception as e:
                try:
                    safe_undo(model)
                except Exception:
                    pass
                logger.error("set_value_filter_behavior failed: %s", e)
                return error_envelope("tom_error", str(e))

            return {
                "success": True,
                "message": f"ValueFilterBehavior set to '{canonical}'",
                "value_filter_behavior": canonical,
                "previous_value_filter_behavior": previous,
                "compatibility_level": level,
                "note": (
                    "This changes query semantics for SUMMARIZECOLUMNS when multiple "
                    "filters target the same table. Re-verify affected measures."
                ),
            }
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def set_properties(
        self,
        description: Optional[str] = None,
        culture: Optional[str] = None,
        discourage_implicit_measures: Optional[Any] = None,
        compatibility_level: Optional[Any] = None,
        allow_compatibility_upgrade: Any = False,
    ) -> Dict[str, Any]:
        """Set model-level properties in ONE save (MP-01). Validates BEFORE touching the model.

        ``description``, ``culture`` (a culture name such as ``en-US``) and
        ``discourage_implicit_measures`` are ordinary edits. ``compatibility_level`` is
        an IRREVERSIBLE upgrade: it is refused unless ``allow_compatibility_upgrade`` is
        true (the handler additionally classifies it CATASTROPHIC = needs
        ``confirm_destructive``), refused when it would LOWER the level, and never done
        implicitly by any other operation.
        """
        updates: Dict[str, Any] = {}
        if description is not None:
            updates["description"] = str(description)
        if culture is not None:
            name = str(culture).strip()
            if not _CULTURE_RE.match(name):
                return error_envelope(
                    "invalid_parameters",
                    f"Invalid culture {culture!r}; expected a culture name such as 'en-US'.",
                )
            updates["culture"] = name
        if discourage_implicit_measures is not None:
            flag = _as_bool(discourage_implicit_measures)
            if flag is None:
                return error_envelope(
                    "invalid_parameters",
                    "discourage_implicit_measures must be true/false, got "
                    f"{discourage_implicit_measures!r}",
                )
            updates["discourage_implicit_measures"] = flag
        target_level: Optional[int] = None
        if compatibility_level is not None:
            try:
                target_level = int(compatibility_level)
            except (TypeError, ValueError):
                return error_envelope(
                    "invalid_parameters",
                    f"compatibility_level must be an integer, got {compatibility_level!r}",
                )
        if not updates and target_level is None:
            return error_envelope(
                "invalid_parameters",
                "Nothing to set: pass description, culture, discourage_implicit_measures "
                "and/or compatibility_level.",
            )

        server, db, model, err = self._get_server_db_model()
        if err:
            return error_envelope("connection_error", err)
        try:
            level = self._compatibility_level(db)
            if target_level is not None:
                if level is not None and target_level < level:
                    return {
                        "success": False,
                        "error": (
                            f"Refusing to lower the compatibility level ({level} -> {target_level}): "
                            "a model cannot be downgraded."
                        ),
                        "error_type": "compatibility_downgrade_refused",
                        "compatibility_level": level,
                    }
                known = supported_compatibility_levels()
                if known is not None and target_level != level and target_level not in known:
                    return {
                        "success": False,
                        "error": (
                            f"{target_level} is not a compatibility level the loaded TOM build knows; "
                            "writing it would produce a project Desktop cannot open."
                        ),
                        "error_type": "compatibility_level_unknown",
                        "compatibility_level": level,
                        "requested_compatibility_level": target_level,
                        "supported_levels": [lv for lv in known if level is None or lv >= level],
                    }
                if level is not None and target_level == level:
                    target_level = None  # already there: nothing to upgrade
                elif not _as_bool(allow_compatibility_upgrade):
                    return {
                        "success": False,
                        "error": (
                            f"Raising the compatibility level ({level} -> {target_level}) is "
                            "irreversible and needs allow_compatibility_upgrade=true (plus "
                            "confirm_destructive=true)."
                        ),
                        "error_type": "compatibility_upgrade_not_allowed",
                        "compatibility_level": level,
                        "requested_compatibility_level": target_level,
                    }
            if "discourage_implicit_measures" in updates and not hasattr(
                model, "DiscourageImplicitMeasures"
            ):
                return error_envelope(
                    "feature_unsupported_by_client",
                    "The loaded AMO/TOM assemblies do not expose "
                    "Model.DiscourageImplicitMeasures.",
                )

            previous: Dict[str, Any] = {}
            applied: List[str] = []
            try:
                if "description" in updates:
                    previous["description"] = getattr(model, "Description", None)
                    model.Description = updates["description"]
                    applied.append("description")
                if "culture" in updates:
                    previous["culture"] = getattr(model, "Culture", None)
                    model.Culture = updates["culture"]
                    applied.append("culture")
                if "discourage_implicit_measures" in updates:
                    previous["discourage_implicit_measures"] = getattr(
                        model, "DiscourageImplicitMeasures", None
                    )
                    model.DiscourageImplicitMeasures = updates["discourage_implicit_measures"]
                    applied.append("discourage_implicit_measures")
                if target_level is not None:
                    previous["compatibility_level"] = level
                    db.CompatibilityLevel = target_level
                    applied.append("compatibility_level")
                if not applied:
                    return {
                        "success": True,
                        "action": "noop",
                        "message": "Model already at the requested compatibility level.",
                        "compatibility_level": level,
                    }
                info = _lane_commit(model, label="model/set_properties")
            except Exception as e:
                try:
                    safe_undo(model)
                except Exception:
                    pass
                logger.error("set_properties failed: %s", e)
                return error_envelope("tom_error", str(e))

            out: Dict[str, Any] = {
                "success": True,
                "action": "updated",
                "updated": applied,
                "previous": previous,
                "compatibility_level": target_level if target_level is not None else level,
                **info,
            }
            if "compatibility_level" in applied:
                out["warning"] = (
                    "The compatibility level was raised and this is IRREVERSIBLE: the model can "
                    "no longer be opened by Desktop / Service builds that do not support "
                    f"level {target_level}. Verify with operation=get_properties."
                )
            return out
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    # ---- internals -------------------------------------------------------

    @staticmethod
    def _resolve_enum_member(canonical: str):
        """Resolve ValueFilterBehaviorType.<canonical> from the loaded TOM SDK.

        Returns None when the enum type (or the member) is absent, so the caller
        can emit ``unsupported_by_amo`` instead of raising.
        """
        try:
            import Microsoft.AnalysisServices.Tabular as TOM  # type: ignore

            enum_type = getattr(TOM, "ValueFilterBehaviorType", None)
            if enum_type is None:
                return None
            return getattr(enum_type, canonical, None)
        except Exception as e:
            logger.debug("ValueFilterBehaviorType unavailable: %s", e)
            return None
