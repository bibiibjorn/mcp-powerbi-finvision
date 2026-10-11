"""Turn raw xmSQL into the readable form DAX Studio shows.

The storage engine emits xmSQL with internal ids baked into every name, e.g.::

    [s Scenario Comparable Filter (106)].[Label Display (731)] AS [s ...$Label ...]

DAX Studio rewrites that to::

    's_Scenario Comparable_Filter'[Label Display]

The pipeline mirrors ``SimplifyXmSqlSyntax``: resolve ids to real names, then
strip the noise (aliases, lineage tags, RowNumber guids, premium tags).
"""

from __future__ import annotations

import re
from typing import Mapping

__all__ = ["XmSqlCleaner", "clean_xmsql", "build_name_maps"]

_PREMIUM_TAG_RE = re.compile(r"<pii>|</pii>|<ccon>|</ccon>")
_ALIAS_RE = re.compile(r" AS[\r\n\t\s]?\'[^\']*\'")
_ALIAS_BRACKET_RE = re.compile(r" AS[\r\n\t\s]?\[[^\]]*\]")
_LINEAGE_RE = re.compile(r" \( [0-9]+ \) \]")
_ROWNUMBER_GUID_RE = re.compile(r"\[RowNumber [0-9A-F ]*\]", re.IGNORECASE)
_DAX_GUID_RE = re.compile(
    r"\$?[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", re.IGNORECASE
)
_DOUBLE_BRACKET_RE = re.compile(r"\[\[([^\]]+)\]\]")
_EMPTY_ARGS_RE = re.compile(r"\(\s*\)")
#: ``[Table Name (13)]`` or ``[Column Name (466)]``
_ID_NAME_RE = re.compile(r"\[([^\]\[]*?) \((\d+)\)\]")
#: ``[table$column]`` composite references left behind after alias removal
_DOLLAR_REF_RE = re.compile(r"\[[^\]\[]*\$[^\]\[]*\]")
_INDEX_SIZE_RE = re.compile(r"\[Index size \(bytes\): (\d+)\]")


def build_name_maps(
    storage_columns: list[Mapping] | None = None,
    tables: list[Mapping] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build ``{id: name}`` maps from DMV rows.

    ``storage_columns`` comes from ``DISCOVER_STORAGE_TABLE_COLUMNS`` and
    ``tables`` from ``TMSCHEMA_TABLES``.  Both are optional - without them the
    cleaner still strips the noise, it just cannot restore the real names.
    """
    column_map: dict[str, str] = {}
    table_map: dict[str, str] = {}

    for row in storage_columns or []:
        col_id = row.get("COLUMN_ID") or row.get("ColumnID")
        name = row.get("ATTRIBUTE_NAME") or row.get("AttributeName")
        if col_id is None or not name:
            continue
        column_map[str(col_id)] = str(name)

    for row in tables or []:
        table_id = row.get("ID") or row.get("TableID") or row.get("Id")
        name = row.get("Name") or row.get("NAME")
        if table_id is None or not name:
            continue
        table_map[str(table_id)] = str(name)

    return column_map, table_map


class XmSqlCleaner:
    """Stateful cleaner - build the id maps once, reuse for every event."""

    def __init__(
        self,
        column_map: Mapping[str, str] | None = None,
        table_map: Mapping[str, str] | None = None,
    ) -> None:
        self.column_map = dict(column_map or {})
        self.table_map = dict(table_map or {})

    # -- name resolution --------------------------------------------------
    def _replace_names(self, text: str) -> str:
        """``[s ReportLines (13)]`` -> ``'s_ReportLines'`` / ``[Line Name]``."""

        def repl(match: re.Match) -> str:
            raw_name, obj_id = match.group(1), match.group(2)
            resolved = self.table_map.get(obj_id) or self.column_map.get(obj_id)
            if resolved:
                # A table reference is quoted; a column keeps square brackets.
                if obj_id in self.table_map:
                    return f"'{resolved}'"
                return f"[{resolved}]"
            # No map available: at least drop the numeric id.
            return f"[{raw_name}]"

        return _ID_NAME_RE.sub(repl, text)

    # -- pipeline ---------------------------------------------------------
    def clean(self, text: str) -> str:
        if not text:
            return ""
        out = _PREMIUM_TAG_RE.sub("", text)
        out = out.replace("\t", "    ")
        out = self._replace_names(out)
        out = _DAX_GUID_RE.sub("", out)
        out = _ALIAS_RE.sub("", out)
        out = _ALIAS_BRACKET_RE.sub("", out)
        out = _LINEAGE_RE.sub("]", out)
        out = _ROWNUMBER_GUID_RE.sub("[RowNumber]", out)
        out = _DOUBLE_BRACKET_RE.sub(r"[\1]", out)
        out = _DOLLAR_REF_RE.sub(
            lambda m: m.group(0).split("$")[-1].rstrip("]").join(["[", "]"]), out
        )
        out = _EMPTY_ARGS_RE.sub("()", out)
        out = _INDEX_SIZE_RE.sub(lambda m: f"[Index size: {int(m.group(1)):,} bytes]", out)
        # Collapse the blank lines the substitutions leave behind.
        out = re.sub(r"[ \t]+\n", "\n", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out.strip()

    __call__ = clean


def clean_xmsql(
    text: str,
    column_map: Mapping[str, str] | None = None,
    table_map: Mapping[str, str] | None = None,
) -> str:
    """One-shot convenience wrapper around :class:`XmSqlCleaner`."""
    return XmSqlCleaner(column_map, table_map).clean(text)
