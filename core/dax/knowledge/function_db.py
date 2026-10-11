"""
DAX Function Database — thread-safe lazy singleton that loads function metadata
from functions.json and provides query methods for SE/FE classification,
callback risk, alternatives, and more.
"""

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


@dataclass(frozen=True)
class Alternative:
    """Describes when to use an alternative DAX function."""

    when: str
    use: str
    improvement: str


@dataclass(frozen=True)
class DaxFunction:
    """Metadata for a single DAX function."""

    name: str
    category: str
    return_type: str
    se_pushable: str
    creates_row_context: bool
    creates_filter_context: bool
    parameters: List[Dict[str, Any]]
    callback_risk: str
    performance_notes: str
    alternatives: List[Alternative]
    references: List[Dict[str, str]]


class DaxFunctionDatabase:
    """Thread-safe lazy singleton providing lookup over 200+ DAX functions.

    Usage::

        db = DaxFunctionDatabase.get()
        func = db.lookup("SUMX")
    """

    _instance: Optional["DaxFunctionDatabase"] = None
    _lock: threading.Lock = threading.Lock()

    # ── Singleton lifecycle ─────────────────────────────────────────

    @classmethod
    def get(cls) -> "DaxFunctionDatabase":
        """Return the singleton instance, creating it on first call."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls.__new__(cls)
                    cls._instance._init()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    # ── Internal init ───────────────────────────────────────────────

    def _init(self) -> None:
        """Load function catalog from JSON."""
        json_path = Path(__file__).parent / "functions.json"
        with open(json_path, "r", encoding="utf-8") as f:
            raw: Dict[str, Any] = json.load(f)

        self._functions: Dict[str, DaxFunction] = {}
        for key, entry in raw.items():
            alternatives = [
                Alternative(
                    when=a.get("when", ""),
                    use=a.get("use", ""),
                    improvement=a.get("improvement", ""),
                )
                for a in entry.get("alternatives", [])
            ]
            func = DaxFunction(
                name=entry["name"],
                category=entry["category"],
                return_type=entry.get("return_type", "scalar"),
                se_pushable=entry.get("se_pushable", "unknown"),
                creates_row_context=entry.get("creates_row_context", False),
                creates_filter_context=entry.get("creates_filter_context", False),
                parameters=entry.get("parameters", []),
                callback_risk=entry.get("callback_risk", "none"),
                performance_notes=entry.get("performance_notes", ""),
                alternatives=alternatives,
                references=entry.get("references", []),
            )
            self._functions[key.upper()] = func

        self._name_set: Set[str] = set(self._functions.keys())

    # ── Public API ──────────────────────────────────────────────────

    def lookup(self, name: str) -> Optional[DaxFunction]:
        """Look up a function by name (case-insensitive)."""
        return self._functions.get(name.upper())

    def is_function(self, name: str) -> bool:
        """Return True if *name* is a known DAX function."""
        return name.upper() in self._name_set

    def get_se_classification(self, name: str) -> str:
        """Return SE classification: se_safe, fe_only, expression_dependent, unknown."""
        func = self.lookup(name)
        return func.se_pushable if func else "unknown"

    def get_alternatives(self, name: str) -> List[Alternative]:
        """Return performance-oriented alternatives for *name*."""
        func = self.lookup(name)
        return func.alternatives if func else []

    def creates_row_context(self, name: str) -> bool:
        """Return True if the function creates an iterator row context."""
        func = self.lookup(name)
        return func.creates_row_context if func else False

    def creates_filter_context(self, name: str) -> bool:
        """Return True if the function creates/modifies filter context."""
        func = self.lookup(name)
        return func.creates_filter_context if func else False

    def get_by_category(self, category: str) -> List[DaxFunction]:
        """Return all functions belonging to *category*."""
        cat = category.lower()
        return [f for f in self._functions.values() if f.category == cat]

    def get_callback_risk(self, name: str) -> str:
        """Return callback risk level: none, low, medium, high."""
        func = self.lookup(name)
        return func.callback_risk if func else "none"

    def count(self) -> int:
        """Return total number of cataloged functions."""
        return len(self._functions)

    def all_functions(self) -> List[DaxFunction]:
        """Return all cataloged functions."""
        return list(self._functions.values())

    def get_function_names(self) -> Set[str]:
        """Return the set of all uppercase function names."""
        return set(self._name_set)
