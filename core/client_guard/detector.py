"""Client Guard detector: decide, from a connected model, which columns hold client identifiers.

Spec section 4. ``detect(run_dax)`` reads the model's metadata and the DISTINCT values of its
columns through an injected ``run_dax(query, max_rows) -> list[dict]`` (so it is unit-testable and
CLR-free) and returns per-column decisions plus the value batches to mask.

Signals (nothing is configured per model):

* **value shape**, scored BEFORE the numeric / short-value skip: e-mail, IBAN (MOD-97), phone,
  and the country packs the profile turns on (``be``: Belgian national number and enterprise
  number, checksums). A column whose values are mostly shapes is masked, whatever it is called;
* **party lexicon** on tokenised names (``lexicon.py``): a party word in the column name, or an
  identifying column (name / code / id ...) of a party table; plus DataCategory and description
  as evidence where INFO.VIEW exposes them;
* **integer columns** only with a name hint AND a national / enterprise number checksum (pack
  ``be``; without a country pack an integer column is never masked);
* **RLS** (a table filtered by a role, or mapped from an RLS bridge) only CORROBORATES: it never
  decides alone (S02 puts RLS on every dimension, which over-masked GL accounts);
* **metadata values**: a column whose values are mostly names of model objects (field parameter
  and picker tables) is never masked.

Numeric-only and under-4-character values never go into the dictionary unless a shape checksum
matches them. Values are read in pages of ``PAGE_SIZE`` (ORDER BY + START AT keyset, no silent
row cap). A candidate column that cannot be read completely is ``not_ready`` and the result is
``ready=False``. Columns that are no candidate by name are only PROBED (first page) for shapes:
they are read to the end only when the probe shows shapes (``sampled_columns`` counts them).

Policy ``dimensions`` (policy A, for the privacy gateway) masks MORE: besides the identity
detection, every Text column of every table that is not safe has its distinct values masked, under
the profile's dimension prefix (``finvision``: PCC profit / cost centers, GLA ledger accounts, ENT
entities, else DIM; ``generic``: VAL). Safe tables are the profile's naming conventions
(``finvision``: ``DEFAULT_SAFE_TABLES``), the configured patterns, field parameter and measure
tables and, under ``generic``, date tables (DataCategory Time) and calculation groups. A column
with more than ``LARGE_DISTINCT`` distinct values is skipped and counted (``skipped_large``); it
never makes the detection not ready. A column that cannot be read completely still does.

The profile is ``core.client_guard.profiles.active_profile()`` unless ``detect(profile=...)`` is
given.

Decisions carry counts and fixed labels only. A value is never part of a decision, a log line or
an exception message.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any

from core.client_guard.engine import normalise
from core.client_guard.lexicon import (
    NameHints,
    has_party_word,
    name_hints,
    prefix_for_names,
    tokenise,
)
from core.client_guard.profiles import (
    FINVISION_SAFE_TABLES,
    GENERIC_PREFIXES,
    Profile,
    Resolved,
    resolve,
)
from core.client_guard.shapes import (
    be_kbo_is_valid,
    be_national_number_is_valid,
    find_shapes,
)

PAGE_SIZE = 50_000
MAX_PAGES = 400
SHAPE_RATIO = 0.5
META_RATIO = 0.5
MIN_VALUE_LEN = 4
PARTY_MIN_VALUE_LEN = 3  # a party column also holds acronyms ("KBC", "VDK", "ING")
PARTY_MIN_DIGITS = 5  # ... and numeric IDs (customer / account numbers), as whole words only
LARGE_DISTINCT = 50_000
POLICIES = ("identity", "dimensions")

# Tables whose values are never masked under policy 'dimensions' by naming convention: the
# ``finvision`` profile's patterns (``profiles.py`` holds the words and the reasoning behind them).
DEFAULT_SAFE_TABLES: tuple[str, ...] = FINVISION_SAFE_TABLES

RunDax = Callable[[str, int], list[dict[str, Any]]]

_NUMERIC = re.compile(r"^[\d\s.,/:+-]+$")
_LOCAL_PHONE = re.compile(r"^0\d[\d\s./-]{6,14}\d$")
_DATE_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}([ T][\d:.]+)?Z?$")
# Period labels of date and slicer tables ("JAN 23", "Sep-2026", "maart 2024", "Q3 2025",
# "2025 Q3", "FY24", "H1 2025", "W12 2026", "P03 2025", "2025M03"): report text, never a client.
# Always with a year: a bare "June", "April" or "Mars" can be a name.
_MONTH = (
    r"jan(?:uary|uari|v(?:ier)?)?|feb(?:ruary|ruari|rier)?|f[eé]v(?:r(?:ier)?)?|m[aä]r(?:ch|s|t|z)?"
    r"|maart|apr(?:il)?|avr(?:il)?|ma[iy]|mei|jun[ei]?|juin|jul[iy]?|juil(?:let)?"
    r"|aug(?:ust(?:us)?)?|ao[uû]t?|sep(?:t(?:ember|embre)?)?|o[ck]t(?:ober|obre)?"
    r"|nov(?:ember|embre)?|de[cz](?:ember|embre)?|d[eé]c(?:embre)?"
)
_YEAR = r"'?\d{2}(?:\d{2})?"
_PERIOD_LABEL = re.compile(
    rf"^(?:(?:{_MONTH})\.?[\s\-/']*(?:{_YEAR})|(?:{_YEAR})[\s\-/.]*(?:{_MONTH})\.?"
    rf"|(?:q|h|t|s|w|wk|p|fy|cy)\s?\d{{1,2}}[\s\-/']*(?:{_YEAR})"
    rf"|(?:{_YEAR})[\s\-/]*(?:q|h|m|w|p)\s?\d{{1,2}}|fy\s?{_YEAR}(?:\s?[-/]\s?{_YEAR})?)$",
    re.IGNORECASE,
)
_TEXT_TYPES = frozenset({"text", "string"})
_INT_TYPES = frozenset({"int64", "integer", "int", "whole number"})
_ROW_NUMBER = re.compile(r"^rownumber-", re.IGNORECASE)
_RLS_REF = re.compile(r"'([^']+)'\s*\[|(\b[A-Za-z_][\w ]*)\[")


class DetectionError(Exception):
    """Metadata could not be read; the message never contains a value."""


@dataclass(frozen=True)
class ColumnMeta:
    table: str
    column: str
    data_type: str
    data_category: str = ""
    description: str = ""


@dataclass(frozen=True)
class ColumnDecision:
    """Outcome for one column. Counts and labels only (no values)."""

    table: str
    column: str
    mask: bool
    reasons: tuple[str, ...]
    prefix: str
    distinct: int
    truncated: bool = False
    error: bool = False

    @property
    def not_ready(self) -> bool:
        """The column could not be read completely: its decision cannot be trusted."""
        return self.truncated or self.error


@dataclass(frozen=True)
class ValueBatch:
    """Values to mask under one token prefix (``repr`` shows the count only)."""

    prefix: str
    values: tuple[str, ...]
    strong: bool = False  # a DIM / GLA value of a party table: identity matching

    def __repr__(self) -> str:
        return f"ValueBatch(prefix={self.prefix!r}, n={len(self.values)}, strong={self.strong})"


@dataclass
class DetectionResult:
    decisions: list[ColumnDecision] = field(default_factory=list)
    batches: list[ValueBatch] = field(default_factory=list)
    ready: bool = True
    columns_seen: int = 0
    probe_errors: int = 0
    sampled_columns: int = 0
    queries: int = 0
    skipped_large: int = 0

    @property
    def masked(self) -> list[ColumnDecision]:
        return [d for d in self.decisions if d.mask]

    @property
    def value_count(self) -> int:
        return sum(len(b.values) for b in self.batches)

    def counts(self) -> dict[str, int]:
        """Counts only, for logs and status."""
        return {
            "columns": self.columns_seen,
            "masked_columns": len(self.masked),
            "not_ready_columns": sum(d.not_ready for d in self.decisions),
            "values": self.value_count,
            "probe_errors": self.probe_errors,
            "sampled_columns": self.sampled_columns,
            "skipped_large": self.skipped_large,
            "queries": self.queries,
            "ready": int(self.ready),
        }


@dataclass(frozen=True)
class _Ctx:
    """The profile and shape packs one detection run works under."""

    profile: Profile
    packs: frozenset[str]

    @property
    def other(self) -> str:
        """Prefix of a phone / national number / enterprise number value."""
        return self.profile.other_shape_prefix


def _ctx(profile: Profile | None, shape_packs: Collection[str] | None) -> _Ctx:
    if profile is None:
        resolved: Resolved = resolve()
        packs = resolved.shape_packs if shape_packs is None else frozenset(shape_packs)
        return _Ctx(resolved.profile, packs)
    return _Ctx(profile, profile.shape_packs if shape_packs is None else frozenset(shape_packs))


# ---------------------------------------------------------------------------------- value shapes
def shape_prefix(
    value: str, packs: Collection[str] | None = None, other_prefix: str = "CLI"
) -> str | None:
    """Token prefix when the WHOLE value is an e-mail / IBAN / phone (or, with the pack, a
    national / enterprise number), else None. Checksums apply, so ordinary numbers are not shapes.
    ``packs=None`` runs every country pack."""
    norm = normalise(value).strip(" ")
    if not norm:
        return None
    for start, end, prefix in find_shapes(norm, packs, other_prefix):
        if start == 0 and end == len(norm):
            return prefix
    return None


def is_local_phone(value: str) -> bool:
    digits = sum(c.isdigit() for c in value)
    return bool(_LOCAL_PHONE.match(value.strip())) and 9 <= digits <= 11


def _skipped(value: str, party: bool = False) -> bool:
    """Numeric-only or under ``MIN_VALUE_LEN`` characters: never added without a shape hit.

    In a PARTY column (customer / vendor / person names, or any text column of a party table) the
    values are identities, so two kinds of value that would be noise elsewhere are kept: a 3-letter
    value with at least two letters (an acronym; the engine matches it as a whole cell, or in running
    text only in capitals) and a numeric ID of ``PARTY_MIN_DIGITS`` or more digits (matched as a
    whole word, never inside another number). Dates stay out."""
    v = value.strip()
    if party:
        if _DATE_TEXT.match(v):
            return True
        letters = sum(c.isalpha() for c in v)
        if len(v) >= PARTY_MIN_VALUE_LEN and letters >= 2:
            return False
        digits = sum(c.isdigit() for c in v)
        if v.isdigit() and digits >= PARTY_MIN_DIGITS:
            return False
    return len(v) < MIN_VALUE_LEN or bool(_NUMERIC.match(v))


# ---------------------------------------------------------------------------------- DAX plumbing
# C-15: ``_q_table`` / ``_q_column`` stay local ON PURPOSE. They are byte-identical to
# ``core.dax.dax_names.quote_table`` / ``bracket`` (pinned by tests/test_quoting_canonical_c15.py),
# but this package must stay stdlib-only: importing ``core.dax.dax_names`` first runs
# ``core/dax/__init__`` (analyzers, optimizer, ...), which the masking gateway must never load.
def _q_table(table: str) -> str:
    return "'" + table.replace("'", "''") + "'"


def _q_column(column: str) -> str:
    return "[" + column.replace("]", "]]") + "]"


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE()" if value else "FALSE()"
    if isinstance(value, (int, float)):
        return repr(value)
    return '"' + str(value).replace('"', '""') + '"'


def page_query(table: str, column: str, cursor: Any = None) -> str:
    """One page of DISTINCT values, ordered, starting at ``cursor`` (inclusive)."""
    ref = f"{_q_table(table)}{_q_column(column)}"
    query = f'EVALUATE\nDISTINCT(SELECTCOLUMNS({_q_table(table)}, "v", {ref}))\nORDER BY [v] ASC'
    if cursor is not None:
        query += f"\nSTART AT {_literal(cursor)}"
    return query


@dataclass
class _Read:
    values: list[Any] = field(default_factory=list)
    complete: bool = False
    error: bool = False
    truncated: bool = False
    next_cursor: Any = None
    pages: int = 0


# the engine's message for a calculated column in error (read only to classify, never stored)
NO_DATA = re.compile(r"does not hold any data because there is an error in its expression", re.I)
NO_DATA_CODE = "calc_column_in_error"  # what a backend raises for it (the message is never kept)


class _Reader:
    """Keyset paging over one column; ``more()`` continues after the first page."""

    def __init__(self, run_dax: RunDax, table: str, column: str, page_size: int, max_pages: int):
        self._run = run_dax
        self._table, self._column = table, column
        self._page_size, self._max_pages = page_size, max_pages
        self.read = _Read()
        self.queries = 0

    def _page(self) -> bool:
        """Read one page; False when reading is over (complete or failed)."""
        r = self.read
        if r.pages >= self._max_pages:
            r.truncated = True
            return False
        self.queries += 1
        try:
            rows = self._run(page_query(self._table, self._column, r.next_cursor), self._page_size)
        except Exception as exc:  # noqa: BLE001 - any failure makes the column not ready
            if NO_DATA_CODE in str(exc) or NO_DATA.search(str(exc)):
                # a calculated column whose expression is in error holds NO data at all (Desktop
                # says so): nothing to mask, so it must not hold every masked chat back
                r.complete = True
                return False
            r.error = True
            return False
        r.pages += 1
        values = [next(iter(row.values()), None) for row in rows]
        if r.next_cursor is not None and values and _same(values[0], r.next_cursor):
            values = values[1:]  # START AT is inclusive: drop the cursor row itself
        values = [v for v in values if v is not None and str(v).strip() != ""]
        r.values.extend(values)
        if len(rows) < self._page_size:
            r.complete = True
            return False
        if not values:
            r.truncated = True  # a full page that made no progress: never loop
            return False
        r.next_cursor = values[-1]
        return True

    def first_page(self) -> _Read:
        if self.read.pages == 0 and not self.read.error:
            self._page()
        return self.read

    def up_to(self, limit: int) -> _Read:
        """Read pages until the column is complete, failed or holds MORE than ``limit`` values."""
        while not (self.read.complete or self.read.error or self.read.truncated):
            if len(self.read.values) > limit:
                break
            self._page()
        return self.read

    def all(self) -> _Read:
        while not (self.read.complete or self.read.error or self.read.truncated):
            self._page()
        return self.read


def _same(a: Any, b: Any) -> bool:
    return a == b or str(a).casefold() == str(b).casefold()


def _as_text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ---------------------------------------------------------------------------------- model facts
def _rows(run_dax: RunDax, query: str, result: DetectionResult) -> list[list[Any]]:
    result.queries += 1
    return [list(r.values()) for r in run_dax(query, 100_000)]


def _read_columns(run_dax: RunDax, result: DetectionResult) -> list[ColumnMeta]:
    rich = (
        'EVALUATE SELECTCOLUMNS(INFO.VIEW.COLUMNS(), "T",[Table],"C",[Name],"DT",[DataType],'
        '"DC",[DataCategory],"D",[Description])'
    )
    plain = 'EVALUATE SELECTCOLUMNS(INFO.VIEW.COLUMNS(), "T",[Table],"C",[Name],"DT",[DataType])'
    try:
        rows = _rows(run_dax, rich, result)
    except Exception:  # noqa: BLE001 - older engines lack DataCategory / Description
        try:
            rows = _rows(run_dax, plain, result)
        except Exception as exc:  # noqa: BLE001
            raise DetectionError("columns") from exc
    out: list[ColumnMeta] = []
    for r in rows:
        if len(r) < 3 or r[0] in (None, "") or r[1] in (None, ""):
            continue
        if _ROW_NUMBER.match(str(r[1])):
            continue
        out.append(
            ColumnMeta(
                str(r[0]),
                str(r[1]),
                str(r[2] or ""),
                str(r[3] or "") if len(r) > 3 else "",
                str(r[4] or "") if len(r) > 4 else "",
            )
        )
    return out


@dataclass
class _Facts:
    columns: list[ColumnMeta]
    object_names: frozenset[str]
    calc_tables: frozenset[str]
    rls_tables: frozenset[str]
    measure_tables: frozenset[str] = frozenset()
    param_tables: frozenset[str] = frozenset()
    column_counts: dict[str, int] = field(default_factory=dict)
    date_tables: frozenset[str] = frozenset()  # DataCategory Time (marked as date table)
    calc_groups: frozenset[str] = frozenset()  # calculation group tables


# tables marked as date table (DataCategory Time) and calculation group tables (TMSCHEMA DMVs)
_DATE_TABLES_QUERY = (
    'EVALUATE SELECTCOLUMNS(FILTER(INFO.TABLES(), [DataCategory] = "Time"), "T",[Name])'
)
_CALC_GROUPS_QUERY = (
    "EVALUATE SELECTCOLUMNS(FILTER(INFO.TABLES(), [ID] IN "
    'SELECTCOLUMNS(INFO.CALCULATIONGROUPS(), "I",[TableID])), "T",[Name])'
)


def _try_rows(run_dax: RunDax, query: str, result: DetectionResult) -> list[list[Any]]:
    try:
        return _rows(run_dax, query, result)
    except Exception:  # noqa: BLE001 - optional metadata
        return []


def _model_facts(run_dax: RunDax, result: DetectionResult, structural: bool = False) -> _Facts:
    columns = _read_columns(run_dax, result)
    tables = _try_rows(
        run_dax,
        'EVALUATE SELECTCOLUMNS(INFO.VIEW.TABLES(), "T",[Name],"X",[Expression])',
        result,
    )
    measures = _try_rows(
        run_dax,
        'EVALUATE SELECTCOLUMNS(INFO.VIEW.MEASURES(), "N",[Name],"T",[Table])',
        result,
    ) or _try_rows(run_dax, 'EVALUATE SELECTCOLUMNS(INFO.VIEW.MEASURES(), "N",[Name])', result)
    rels = _try_rows(
        run_dax,
        'EVALUATE SELECTCOLUMNS(INFO.VIEW.RELATIONSHIPS(), "F",[FromTable],"FC",[FromColumn],'
        '"T",[ToTable],"TC",[ToColumn])',
        result,
    )
    perms = _try_rows(
        run_dax,
        'EVALUATE SELECTCOLUMNS(INFO.TABLEPERMISSIONS(), "F",[FilterExpression])',
        result,
    )
    object_names = {str(r[0]).lower() for r in tables if r}
    object_names |= {c.column.lower() for c in columns}
    object_names |= {str(r[0]).lower() for r in measures if r}
    calc_tables = {str(r[0]) for r in tables if len(r) > 1 and r[1]}

    rls: set[str] = set()
    for r in perms:
        for m in _RLS_REF.finditer(str(r[0] or "") if r else ""):
            rls.add((m.group(1) or m.group(2) or "").strip())
    bridges = {c.table for c in columns if c.column.lower() == "user"}
    bridge_keys = {
        c.column.lower() for c in columns if c.table in bridges and c.column.lower().endswith("key")
    }
    for c in columns:
        if c.column.lower() in bridge_keys and c.table not in bridges:
            rls.add(c.table)
    for r in rels:
        if len(r) >= 3 and str(r[0]) in bridges:
            rls.add(str(r[2]))
    measure_tables = {str(r[1]) for r in measures if len(r) > 1 and r[1]}
    param_tables = {str(r[0]) for r in tables if len(r) > 1 and "NAMEOF" in str(r[1] or "").upper()}
    column_counts: dict[str, int] = {}
    for c in columns:
        column_counts[c.table] = column_counts.get(c.table, 0) + 1
    date_tables: frozenset[str] = frozenset()
    calc_groups: frozenset[str] = frozenset()
    if structural:  # profiles that skip date tables and calculation groups (optional metadata)
        date_tables = frozenset(
            str(r[0]) for r in _try_rows(run_dax, _DATE_TABLES_QUERY, result) if r
        )
        calc_groups = frozenset(
            str(r[0]) for r in _try_rows(run_dax, _CALC_GROUPS_QUERY, result) if r
        )
    return _Facts(
        columns,
        frozenset(object_names),
        frozenset(calc_tables),
        frozenset(rls),
        frozenset(measure_tables),
        frozenset(param_tables),
        column_counts,
        date_tables,
        calc_groups,
    )


# ---------------------------------------------------------------------------------- decisions
def _kept(
    values: list[Any], ctx: _Ctx, phone_hint: bool = False, party: bool = False
) -> tuple[list[str], list[str | None]]:
    """Values that survive the numeric / short skip (shape hits survive), plus their shapes.

    A local phone format (``0475 12 34 56``) only counts as a shape in a column that is named
    like a phone column. ``party``: the column holds identities (see ``_skipped``).
    """
    kept: list[str] = []
    shapes: list[str | None] = []
    for raw in values:
        text = _as_text(raw)
        shape = shape_prefix(text, ctx.packs, ctx.other)
        if shape is None and phone_hint and is_local_phone(text):
            shape = ctx.other
        if shape is None and _skipped(text, party):
            continue
        kept.append(text)
        shapes.append(shape)
    return kept, shapes


def _decide_text(
    meta: ColumnMeta,
    hints: NameHints,
    facts: _Facts,
    read: Callable[[bool], _Read],
    result: DetectionResult,
    ctx: _Ctx,
) -> ColumnDecision | None:
    security = meta.table in facts.rls_tables and hints.ident
    candidate = hints.party or hints.phone or security
    first = read(False)
    shape_ratio = _shape_ratio(first.values, hints.phone, ctx)
    needs_full = candidate or shape_ratio >= SHAPE_RATIO
    probed_only = not needs_full
    r = read(True) if needs_full else first

    if r.error or r.truncated:
        if not needs_full:
            result.probe_errors += int(r.error)
            return None
        return ColumnDecision(
            meta.table,
            meta.column,
            False,
            ("party-name",) if hints.party else ("probe",),
            prefix_for_names(list(hints.table_tokens), list(hints.column_tokens), ctx.profile),
            len(r.values),
            truncated=r.truncated,
            error=r.error,
        )

    kept, shapes = _kept(r.values, ctx, hints.phone, bool(hints.party))
    n = len(kept) or 1
    ratios = {p: sum(s == p for s in shapes) / n for p in ("USR", "ACC", ctx.other)}
    shape_hit = max(ratios.values()) >= SHAPE_RATIO
    meta_ratio = sum(v.lower() in facts.object_names for v in kept) / n
    metadata = meta_ratio >= META_RATIO
    reasons: list[str] = []
    if shape_hit:
        reasons.append("shape")
    if hints.party:
        reasons.append(f"party-{hints.party_via}")
    if security:
        reasons.append("rls-table")
    if metadata:
        reasons.append("metadata-values")
    if meta.table in facts.calc_tables:
        reasons.append("helper-table")
    mask = shape_hit or (hints.party and not metadata)
    if mask and not kept:
        mask = False  # nothing to mask: an empty party column must not flag a client model
        reasons.append("no-values")
    if security:
        reasons.append("(rls corroborates)")
    if probed_only and not r.complete:
        result.sampled_columns += 1
    if not (mask or hints.party or security or shape_hit):
        return None
    prefix = _prefix(ratios, hints, ctx)
    decision = ColumnDecision(
        meta.table,
        meta.column,
        mask,
        tuple(reasons),
        prefix,
        len(kept),
    )
    if mask:
        # a prefix the engine treats as generic vocabulary (VAL) still holds identities here
        _stash(result, prefix, kept, prefix in GENERIC_PREFIXES)
    return decision


def _prefix(ratios: dict[str, float], hints: NameHints, ctx: _Ctx) -> str:
    if ratios["USR"] >= SHAPE_RATIO:
        return "USR"
    if ratios["ACC"] >= SHAPE_RATIO:
        return "ACC"
    return prefix_for_names(list(hints.table_tokens), list(hints.column_tokens), ctx.profile)


def _shape_ratio(values: list[Any], phone_hint: bool, ctx: _Ctx) -> float:
    kept, shapes = _kept(values, ctx, phone_hint)
    return sum(sh is not None for sh in shapes) / len(kept) if kept else 0.0


def _nrn_forms(value: Any) -> list[str]:
    """Checksum-valid national / enterprise number forms of an integer (leading zero restored)."""
    digits = _as_text(value)
    if not digits.isdigit():
        return []
    out: list[str] = []
    padded = digits.zfill(11)
    if be_national_number_is_valid(padded):
        out.append(padded)
    padded_kbo = digits.zfill(10)
    if be_kbo_is_valid(padded_kbo):
        out.append(padded_kbo)
    return out


def _decide_integer(
    meta: ColumnMeta,
    hints: NameHints,
    read: Callable[[bool], _Read],
    result: DetectionResult,
    ctx: _Ctx,
) -> ColumnDecision | None:
    if "be" not in ctx.packs or not hints.number_hint:
        return None  # the checksums are the Belgian pack's: without it no integer is masked
    r = read(False)
    if r.error:
        result.probe_errors += 1
        return None
    forms = [_nrn_forms(v) for v in r.values]
    hits = [f for f in forms if f]
    if not r.values or len(hits) / len(r.values) < SHAPE_RATIO:
        return None
    full = read(True)
    if full.error or full.truncated:
        return ColumnDecision(
            meta.table,
            meta.column,
            False,
            ("name-hint", "checksum-shape"),
            ctx.other,
            len(full.values),
            truncated=full.truncated,
            error=full.error,
        )
    values: list[str] = []
    for v in full.values:
        values.extend(_nrn_forms(v))
    _stash(result, ctx.other, values, ctx.other in GENERIC_PREFIXES)
    return ColumnDecision(
        meta.table, meta.column, True, ("name-hint", "checksum-shape"), ctx.other, len(values)
    )


def _stash(result: DetectionResult, prefix: str, values: list[str], strong: bool = False) -> None:
    """Add values to the batch of ``(prefix, strong)`` (one batch each, casefold-deduplicated)."""
    for i, batch in enumerate(result.batches):
        if batch.prefix == prefix and batch.strong == strong:
            seen = {v.casefold() for v in batch.values}
            extra = []
            for v in values:
                key = v.casefold()
                if key not in seen:
                    seen.add(key)
                    extra.append(v)
            result.batches[i] = ValueBatch(prefix, batch.values + tuple(extra), strong)
            return
    seen2: set[str] = set()
    uniq: list[str] = []
    for v in values:
        key = v.casefold()
        if key not in seen2:
            seen2.add(key)
            uniq.append(v)
    result.batches.append(ValueBatch(prefix, tuple(uniq), strong))


# ---------------------------------------------------------------------------------- policy A
def dimension_prefix(table: str, column: str, profile: Profile | None = None) -> str:
    """Token prefix of a dimension column under the profile: ``finvision`` says PCC (profit / cost
    centers), GLA (ledger accounts), ENT (entities / companies), else DIM (column words win over
    table words; the words of a period table do not count); ``generic`` says VAL."""
    prof = profile or resolve().profile
    if prof.dimension_classifier is None:
        return prof.dimension_default_prefix
    kind = prof.dimension_classifier(tokenise(table), tokenise(column))
    return kind or prof.dimension_default_prefix


def _compile_safe(
    extra: list[str] | tuple[str, ...] | None, profile: Profile | None = None
) -> list[re.Pattern[str]]:
    """The profile's safe-table patterns plus the configured ones. An invalid configured pattern
    is ignored (the table is then masked, the safe direction)."""
    prof = profile or resolve().profile
    out: list[re.Pattern[str]] = []
    for pattern in (*prof.safe_table_patterns, *(extra or ())):
        try:
            out.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            continue
    return out


def table_is_safe(
    table: str,
    facts: _Facts,
    patterns: list[re.Pattern[str]],
    profile: Profile | None = None,
) -> bool:
    """True when the values of ``table`` are never masked under policy 'dimensions'."""
    prof = profile or resolve().profile
    if table in facts.param_tables:
        return True
    if table in facts.measure_tables and facts.column_counts.get(table, 0) <= 1:
        return True  # a measure table: measures plus at most a dummy column
    if prof.structural_skips and (table in facts.date_tables or table in facts.calc_groups):
        return True  # a date table / calculation group holds model structure, not clients
    tokens = tokenise(table)
    if has_party_word(tokens):
        return any(p.search(table) for p in patterns if p.pattern not in prof.safe_table_patterns)
    forms = (table, " ".join(tokens))
    return any(p.search(f) for p in patterns for f in forms)


def _dimension_value(text: str, party: bool = False) -> bool:
    """A generic dimension value worth masking: not short, not a date or period label, and with a
    letter (amounts, band labels such as "> 50.000" and numeric codes are report text, not
    names). In a party table (``party``) every value describes a party: acronyms and numeric IDs
    count (see ``_skipped``)."""
    v = text.strip()
    if party and _PERIOD_LABEL.match(v) is None and not _skipped(text, True):
        return True
    return (
        not _skipped(text)
        and _DATE_TEXT.match(v) is None
        and _PERIOD_LABEL.match(v) is None
        and any(c.isalpha() for c in text)
    )


def _decide_dimension(
    meta: ColumnMeta,
    reader: _Reader,
    facts: _Facts,
    result: DetectionResult,
    ctx: _Ctx,
) -> ColumnDecision | None:
    prefix = dimension_prefix(meta.table, meta.column, ctx.profile)
    r = reader.up_to(LARGE_DISTINCT)
    if len(r.values) > LARGE_DISTINCT:
        result.skipped_large += 1
        return ColumnDecision(
            meta.table, meta.column, False, ("skipped-large",), prefix, len(r.values)
        )
    if r.error or r.truncated:
        return ColumnDecision(
            meta.table,
            meta.column,
            False,
            ("dimension",),
            prefix,
            len(r.values),
            truncated=r.truncated,
            error=r.error,
        )
    strong = has_party_word(tokenise(meta.table))
    kept = [t for t in (_as_text(v) for v in r.values) if _dimension_value(t, strong)]
    if not kept:
        return None  # numbers, dates, short codes: nothing to hide
    if sum(v.lower() in facts.object_names for v in kept) / len(kept) >= META_RATIO:
        return None  # names of model objects (picker columns)
    # any value of a party table describes a party (an address, a document number, a key):
    # identity matching, not the report-vocabulary rules. A security filter alone does not make a
    # table a party table (RLS on cost dimensions); a name that is also a dimension value gets
    # the identity rules through its own batch (the engine's precedence).
    _stash(result, prefix, kept, strong)
    reasons = ("dimension", "party-table") if strong else ("dimension",)
    return ColumnDecision(meta.table, meta.column, True, reasons, prefix, len(kept))


# ---------------------------------------------------------------------------------- entry point
def detect(
    run_dax: RunDax,
    *,
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
    policy: str = "identity",
    safe_tables: list[str] | tuple[str, ...] | None = None,
    profile: Profile | None = None,
    shape_packs: Collection[str] | None = None,
) -> DetectionResult:
    """Detect client-identifier columns of the model behind ``run_dax``.

    ``run_dax(query, max_rows)`` returns the rows (dicts, values in column order) and raises on a
    failed query. The result is ``ready=False`` when metadata could not be read or any candidate
    column was truncated / errored.

    ``policy='identity'`` masks client identifiers only. ``policy='dimensions'`` also masks the
    distinct values of every Text column of a table that is not safe: the profile's naming
    conventions (``DEFAULT_SAFE_TABLES`` under ``finvision``), field parameter / measure tables,
    date tables and calculation groups (``generic``) plus the regexes in ``safe_tables`` (config
    key ``client_guard.safe_tables``).

    ``profile`` / ``shape_packs`` default to the active profile and its effective packs.
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown policy (expected one of {', '.join(POLICIES)})")
    ctx = _ctx(profile, shape_packs)
    result = DetectionResult()
    try:
        facts = _model_facts(run_dax, result, ctx.profile.structural_skips)
    except DetectionError:
        result.ready = False
        return result
    result.columns_seen = len(facts.columns)
    safe_patterns = _compile_safe(safe_tables, ctx.profile) if policy == "dimensions" else []
    for meta in facts.columns:
        dtype = meta.data_type.strip().lower()
        if dtype not in _TEXT_TYPES and dtype not in _INT_TYPES:
            continue
        hints = name_hints(meta.table, meta.column, meta.description, meta.data_category)
        reader = _Reader(run_dax, meta.table, meta.column, page_size, max_pages)

        def read(full: bool, _reader: _Reader = reader) -> _Read:
            return _reader.all() if full else _reader.first_page()

        decision = (
            _decide_text(meta, hints, facts, read, result, ctx)
            if dtype in _TEXT_TYPES
            else _decide_integer(meta, hints, read, result, ctx)
        )
        if policy == "dimensions" and dtype in _TEXT_TYPES:
            decided = decision is not None and (decision.mask or decision.not_ready)
            helper = decision is not None and "metadata-values" in decision.reasons
            if (
                not decided
                and not helper
                and not table_is_safe(meta.table, facts, safe_patterns, ctx.profile)
            ):
                dim = _decide_dimension(meta, reader, facts, result, ctx)
                if dim is not None:
                    decision = dim
        result.queries += reader.queries
        if decision is not None:
            result.decisions.append(decision)
            if decision.not_ready:
                result.ready = False
    return result
