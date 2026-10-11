"""Tabular Editor BPA ``FixExpression`` -> patch-plan translation (F-03, I-30 #1).

A BPA rule may carry a ``FixExpression``: a one-line C# / Dynamic-LINQ assignment that Tabular
Editor runs on every violating object (``IsHidden = true``, ``Delete()``). This module translates
the machine-applicable SUBSET into the same property-patch the ``03_Batch_Operations
object_type=patch`` engine applies (:mod:`core.analysis.patch_engine`), so a BPA finding can be
fixed through the same preview -> ``plan_hash`` -> one ``write_lane.commit`` path.

Supported subset (everything else is reported as ``fix_unsupported`` with the expression text,
never guessed):

* ``Delete()``                               -> ``{"Delete()": true}``
* ``<Property> = "<string literal>"``        -> C# escapes ``\\u003B`` / ``\\\\`` decoded
* ``<Property> = true | false | <number> | null``
* ``<Property> = <EnumType>.<Member>``       (``SummarizeBy = AggregateFunction.None``)
* ``<Property> = it.<Member>[.<Member>...]`` -> a ``{Member}`` placeholder (``Name = it.Name``)

``<Property>`` must be on the shared allow-list of :mod:`core.operations.tom_properties` for the
object kind (``Name``, ``DataType``, ``IsKey`` ... are structural and refused). A ``;``-separated
list of several statements is not part of the TE FixExpression grammar and is refused too.

Prose "fixes" (``Rename the table to follow your convention``) never parse as an assignment, so
they are never offered as machine-applicable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Optional

from core.analysis.bpa_expression import BpaExpressionError, compile_expression, ENUM_TYPES

__all__ = ["FixPlan", "translate_fix_expression", "fix_is_machine_applicable", "DELETE_KEY"]

#: Reserved ``patch`` key that deletes the selected objects (mirrors TE's ``Delete()``).
DELETE_KEY = "Delete()"

_DELETE_RE = re.compile(r"^\s*(?:it\.)?Delete\s*\(\s*\)\s*;?\s*$", re.IGNORECASE)
_ASSIGN_RE = re.compile(r"^\s*(?:it\.)?([A-Za-z_]\w*)\s*=(?!=)\s*(.+?)\s*;?\s*$", re.DOTALL)
_UNI_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


@dataclass(frozen=True)
class FixPlan:
    """The translation of one FixExpression for one object kind."""

    expression: str
    delete: bool = False
    #: property -> value (a ``str`` may carry ``{Name}`` / ``{Table.Name}`` placeholders)
    patch: Dict[str, Any] = field(default_factory=dict)
    #: why the expression is outside the supported subset (None = machine-applicable)
    unsupported: Optional[str] = None

    @property
    def supported(self) -> bool:
        return self.unsupported is None and (self.delete or bool(self.patch))

    def as_patch(self) -> Dict[str, Any]:
        """The ``patch`` mapping the engine takes (``{"Delete()": True}`` for a delete)."""
        return {DELETE_KEY: True} if self.delete else dict(self.patch)


def _unescape(text: str) -> str:
    text = _UNI_RE.sub(lambda m: chr(int(m.group(1), 16)), text)
    return text.replace("\\\\", "\\")


def _member_path(node: Any) -> Optional[str]:
    """``it.A.B`` / ``A.B`` / ``A`` -> ``"A.B"``; None when the AST is anything else."""
    if not isinstance(node, tuple):
        return None
    if node[0] == "ident":
        return None if str(node[1]).lower() == "it" else str(node[1])
    if node[0] == "member":
        inner = node[1]
        if isinstance(inner, tuple) and inner[0] == "ident" and str(inner[1]).lower() == "it":
            return str(node[2])
        head = _member_path(inner)
        return f"{head}.{node[2]}" if head else None
    return None


def _rhs_value(rhs: str) -> Any:
    """Value of the right-hand side, or raise ``ValueError(reason)``."""
    try:
        ast = compile_expression(rhs).ast
    except BpaExpressionError as exc:
        raise ValueError(f"right-hand side is not a literal or reference: {exc}") from exc
    if ast[0] == "lit":
        value = ast[1]
        if isinstance(value, str):
            # a literal is not a template: escape braces so the engine renders them verbatim
            return _unescape(value).replace("{", "{{").replace("}", "}}")
        return value
    if (
        ast[0] == "unary"
        and ast[1] == "-"
        and ast[2][0] == "lit"
        and isinstance(ast[2][1], (int, float))
    ):
        return -ast[2][1]
    if (
        ast[0] == "member"
        and ast[1][0] == "ident"
        and str(ast[1][1]).lower() in ENUM_TYPES
        and str(ast[1][1]).lower() != "it"
    ):
        return str(ast[2])
    path = _member_path(ast)
    if path:
        return "{" + path + "}"
    raise ValueError(
        "right-hand side is a computed expression (not a literal, enum or it.<member>)"
    )


@lru_cache(maxsize=512)
def translate_fix_expression(expression: str, tom_kind: Optional[str] = None) -> FixPlan:
    """Translate a TE FixExpression for objects of ``tom_kind`` (``measure``, ``column`` ...).

    ``tom_kind=None`` skips the allow-list check (syntax-only translation).
    """
    text = str(expression or "").strip()
    if not text:
        return FixPlan(expression="", unsupported="no FixExpression")
    if _DELETE_RE.match(text):
        return FixPlan(expression=text, delete=True)
    m = _ASSIGN_RE.match(text)
    if not m:
        return FixPlan(
            expression=text, unsupported="not a single `Property = value` assignment or Delete()"
        )
    prop, rhs = m.group(1), m.group(2)
    if ";" in _strip_strings(rhs):
        return FixPlan(
            expression=text, unsupported="several statements (a FixExpression is one assignment)"
        )
    canon = prop
    if tom_kind is not None:
        from core.operations import tom_properties as tp

        canon = tp._canonical(tom_kind, prop)  # noqa: SLF001 - shared allow-list, not modified
        if canon is None:
            why = tp._DENY_REASONS.get(
                tp._norm(prop), "not on the patch allow-list"
            )  # noqa: SLF001
            return FixPlan(
                expression=text,
                unsupported=f"property {prop!r} cannot be patched on a {tom_kind}: {why}",
            )
    try:
        value = _rhs_value(rhs)
    except ValueError as exc:
        return FixPlan(expression=text, unsupported=str(exc))
    return FixPlan(expression=text, patch={canon: value})


def _strip_strings(text: str) -> str:
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)


def fix_is_machine_applicable(expression: Optional[str], tom_kind: Optional[str] = None) -> bool:
    return bool(expression) and translate_fix_expression(str(expression), tom_kind).supported
