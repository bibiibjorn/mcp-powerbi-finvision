"""Copy-pasted DAX detection by normalised token structure (2026-10-03 deep review, F-15).

``05_DAX_Intelligence find_clones`` and ``15_DAX_Refactor extract_udf`` share this module.

Normalisation. The lexer token stream (comments / whitespace dropped) is turned into *units*:
string / number / datetime literals, column references (``Sales[Amount]``, ``'T'[c]``), bare
``[Name]`` references, table names and free identifiers are abstracted to a placeholder; VAR
names are numbered by declaration order (renaming a VAR is not a difference); function names,
keywords, operators and punctuation stay literal. Two expressions with the same unit-key
sequence are *structural clones*; the units where their concrete values differ are the
candidate parameters of a UDF.

Groups
- ``exact_duplicate``: same structure and identical values (BPA AVOID_DUPLICATE_MEASURES finds
  these too);
- ``structural``: same structure, some literals / references differ -> UDF signature proposal
  (or a calculation-group item when only the base MEASURE differs);
- ``near``: structures differ a little (an extra filter argument ...): reported with the
  differing tokens, no proposal (align the bodies first);
- fragments (``scope: "fragment"``): the same call sub-tree inside different objects; reported
  with a UDF proposal, but ``extract_udf`` only rewrites whole measure bodies.

UDF parameter modes follow the canon (``dax-modern-features.md`` section 3): a literal is a
``val`` scalar (a constant is the same in any context); a column / table reference is the typed
reference (``ColumnRef`` / ``TableRef``, lazy ``expr`` by definition); a differing MEASURE is
``val`` only when every occurrence sits in the caller's own context (no CALCULATE / iterator
frame, not in a conditional branch), otherwise ``MeasureRef`` (``expr``: it must be evaluated at
the site inside the body, exactly where the original measure reference was).

Pure functions over plain data - no connection, no CLR.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from core.codemods import dax_text
from core.dax.tokenizer import Token, TokenType

MIN_UDF_COMPATIBILITY_LEVEL = 1702

PARAM_KINDS = frozenset({"str", "num", "dt", "col", "bare", "table", "ident"})

# Identifiers that are enumeration words, not names (kept literal in the structure).
_ENUM_WORDS = frozenset(
    {
        "ASC",
        "DESC",
        "ROWS",
        "COLUMNS",
        "BOTH",
        "NONE",
        "ABS",
        "REL",
        "DENSE",
        "SKIP",
        "KEEP",
        "DEFAULT",
        "FIRST",
        "LAST",
        "HIGHESTPARENT",
        "LOWESTPARENT",
        "TEXTMATCHING",
        "FUZZYMATCHING",
        "PHRASEMATCHING",
        "FULL",
        "SELF",
        "PARENT",
        "ESCAPED",
        "UNESCAPED",
        "MINIMALLYESCAPED",
        "EXPLICIT",
        "INFERRED",
    }
)

_NAME_TYPES = (TokenType.FUNCTION, TokenType.KEYWORD, TokenType.IDENTIFIER)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    kind: str  # str num dt col bare table ident var fn kw op punct
    key: str  # structural key (placeholder for parameter kinds)
    value: str  # normalised concrete value (what "differs")
    text: str
    start: int
    end: int
    tok: int  # first code-token index
    tok_end: int  # last code-token index (inclusive)


@dataclass
class Normalized:
    expression: str
    code: List[Token]
    units: List[Unit]
    var_decl_unit: Dict[str, int] = field(default_factory=dict)  # VAR name (upper) -> unit idx
    _frames: Optional[List[Tuple[Tuple[str, int], ...]]] = None

    @property
    def signature(self) -> Tuple[str, ...]:
        return tuple(u.key for u in self.units)

    def frames(self) -> List[Tuple[Tuple[str, int], ...]]:
        if self._frames is None:
            self._frames = dax_text.frame_stacks(self.code)
        return self._frames

    def unit_of_token(self, tok_idx: int) -> Optional[int]:
        lo, hi = 0, len(self.units) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            u = self.units[mid]
            if tok_idx < u.tok:
                hi = mid - 1
            elif tok_idx > u.tok_end:
                lo = mid + 1
            else:
                return mid
        return None


def _num_value(text: str) -> str:
    try:
        return repr(float(text))
    except ValueError:
        return text


def normalise(expression: str) -> Normalized:
    """Lex *expression* into structural units (see the module docstring)."""
    from core.dax.dax_names import split_qualified, unbracket, unquote_table

    code = dax_text.code_tokens(expression or "")
    var_order: Dict[str, int] = {}
    for i, t in enumerate(code[:-1]):
        if t.type == TokenType.KEYWORD and t.value.upper() == "VAR":
            nm = code[i + 1].value.upper()
            var_order.setdefault(nm, len(var_order))
    units: List[Unit] = []
    var_decl: Dict[str, int] = {}
    n = len(code)
    i = 0
    while i < n:
        t = code[i]
        tt = t.type
        nxt = code[i + 1] if i + 1 < n else None
        if (
            tt == TokenType.IDENTIFIER
            and nxt is not None
            and nxt.type == TokenType.COLUMN_REF
            and nxt.start == t.end
        ):
            val = f"{dax_text.norm_name(t.value)}|{dax_text.norm_name(unbracket(nxt.value))}"
            units.append(
                Unit("col", "$C", val, expression[t.start : nxt.end], t.start, nxt.end, i, i + 1)
            )
            i += 2
            continue
        if tt == TokenType.QUALIFIED_REF:
            tb, c = split_qualified(t.value)
            val = f"{dax_text.norm_name(tb)}|{dax_text.norm_name(c)}"
            units.append(Unit("col", "$C", val, t.value, t.start, t.end, i, i))
        elif tt == TokenType.COLUMN_REF:
            val = dax_text.norm_name(unbracket(t.value))
            units.append(Unit("bare", "$R", val, t.value, t.start, t.end, i, i))
        elif tt == TokenType.TABLE_REF:
            val = dax_text.norm_name(unquote_table(t.value))
            units.append(Unit("table", "$T", val, t.value, t.start, t.end, i, i))
        elif tt == TokenType.STRING:
            units.append(Unit("str", "$S", t.value, t.value, t.start, t.end, i, i))
        elif tt == TokenType.NUMBER:
            units.append(Unit("num", "$N", _num_value(t.value), t.value, t.start, t.end, i, i))
        elif tt == TokenType.DATETIME:
            units.append(Unit("dt", "$D", t.value[2:], t.value, t.start, t.end, i, i))
        elif tt in _NAME_TYPES:
            up = t.value.upper()
            is_call = tt == TokenType.FUNCTION or (
                nxt is not None and nxt.type == TokenType.PAREN_OPEN
            )
            if is_call and not (tt == TokenType.KEYWORD and up in ("VAR", "RETURN", "IN")):
                units.append(Unit("fn", "F:" + up, up, t.value, t.start, t.end, i, i))
            elif tt == TokenType.KEYWORD:
                units.append(Unit("kw", up, up, t.value, t.start, t.end, i, i))
                if up == "VAR" and nxt is not None:
                    var_decl[nxt.value.upper()] = len(units)  # the name unit that follows
            elif up in var_order:
                units.append(Unit("var", f"$V{var_order[up]}", up, t.value, t.start, t.end, i, i))
            elif up in _ENUM_WORDS:
                units.append(Unit("kw", up, up, t.value, t.start, t.end, i, i))
            else:
                val = dax_text.norm_name(t.value)
                units.append(Unit("ident", "$T", val, t.value, t.start, t.end, i, i))
        elif tt in (TokenType.WHITESPACE, TokenType.NEWLINE):
            pass
        else:
            up = t.value.upper()
            kind = "op" if tt == TokenType.OPERATOR else "punct"
            units.append(Unit(kind, up, up, t.value, t.start, t.end, i, i))
        i += 1
    return Normalized(expression or "", code, units, var_decl)


# ---------------------------------------------------------------------------
# Members and groups
# ---------------------------------------------------------------------------


@dataclass
class Member:
    """One occurrence: a whole object body (lo..hi covers every unit) or a call fragment."""

    kind: str  # measure | calc_item | calc_column | ...
    table: str
    name: str
    norm: Normalized
    lo: int = 0
    hi: int = -1  # exclusive; -1 = all units
    scope: str = "measure"  # measure | fragment

    def __post_init__(self) -> None:
        if self.hi < 0:
            self.hi = len(self.norm.units)

    @property
    def ref(self) -> str:
        return f"{self.table}[{self.name}]" if self.table else self.name

    @property
    def units(self) -> List[Unit]:
        return self.norm.units[self.lo : self.hi]

    @property
    def keys(self) -> Tuple[str, ...]:
        return tuple(u.key for u in self.units)

    @property
    def values(self) -> Tuple[str, ...]:
        return tuple(u.value for u in self.units)

    def source(self) -> str:
        us = self.units
        return self.norm.expression[us[0].start : us[-1].end] if us else ""

    def char_range(self) -> Tuple[int, int]:
        us = self.units
        return (us[0].start, us[-1].end) if us else (0, 0)


def members_from_objects(objects: Iterable[Dict[str, Any]], min_tokens: int = 12) -> List[Member]:
    """``objects``: dicts ``{kind, table, name, expression}``. Bodies shorter than
    ``min_tokens`` units are skipped (a one-line SUM is not worth a function)."""
    out: List[Member] = []
    for o in objects:
        expr = o.get("expression") or ""
        if not str(expr).strip():
            continue
        nz = normalise(expr)
        if len(nz.units) < max(2, int(min_tokens)):
            continue
        out.append(
            Member(
                str(o.get("kind") or "measure"),
                str(o.get("table") or ""),
                str(o.get("name") or ""),
                nz,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Parameter analysis
# ---------------------------------------------------------------------------

_TYPE_WORDS = {"str": "String", "num": "Numeric", "dt": "DateTime"}
_BASE_NAMES = {
    "str": "textValue",
    "num": "numberValue",
    "dt": "dateValue",
    "col": "columnRef",
    "table": "tableRef",
    "bare": "measureRef",
    "ident": "tableRef",
}


@dataclass
class Param:
    name: str
    kind: str  # str num dt col bare table ident
    mode: str  # val | expr
    type_hint: str
    positions: List[int]  # unit offsets (relative to the member's lo)
    values: Dict[str, str]  # member ref -> source text
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "name": self.name,
            "kind": {"bare": "measure", "ident": "table"}.get(self.kind, self.kind),
            "mode": self.mode,
            "type_hint": self.type_hint,
            "occurrences": len(self.positions),
            "values": self.values,
        }
        if self.note:
            out["note"] = self.note
        return out


def _taken_names(members: Sequence[Member], measure_names: Optional[Set[str]]) -> Set[str]:
    taken: Set[str] = set()
    for m in members:
        for u in m.units:
            if u.kind in ("var", "fn"):
                taken.add(u.value.lower())
        for nm in m.norm.var_decl_unit:
            taken.add(nm.lower())
    taken |= {x.lower() for x in (measure_names or set())}
    return taken


def analyse_group(
    members: Sequence[Member],
    *,
    measure_names: Optional[Set[str]] = None,
    parameter_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Parameters of a structurally identical group (all members share one key sequence).

    Returns ``{"params": [Param], "match": exact_duplicate|structural, "calc_group_only": bool,
    "diff_positions": [...]}``.
    """
    first = members[0]
    n = len(first.units)
    names = {x.lower() for x in (measure_names or set())}
    frames = first.norm.frames()
    diff: List[int] = []
    for p in range(n):
        u = first.units[p]
        if u.kind not in PARAM_KINDS:
            continue
        if len({m.units[p].value for m in members}) > 1:
            diff.append(p)
    clusters: Dict[Tuple[str, ...], List[int]] = {}
    for p in diff:
        clusters.setdefault(tuple(m.units[p].value for m in members), []).append(p)

    taken = _taken_names(members, measure_names)
    used_names: Set[str] = set()
    counts: Dict[str, int] = {}
    params: List[Param] = []
    override = list(parameter_names or [])
    for positions in sorted(clusters.values(), key=lambda ps: ps[0]):
        u0 = first.units[positions[0]]
        kind = u0.kind
        vals = {m.ref: m.units[positions[0]].text for m in members}
        note = ""
        if kind in ("str", "num", "dt"):
            mode, hint = "val", f"Scalar {_TYPE_WORDS[kind]} Val"
        elif kind == "col":
            mode, hint = "expr", "ColumnRef"
        elif kind in ("table", "ident"):
            mode, hint = "expr", "TableRef"
        else:  # bare [Name]
            all_measures = bool(names) and all(
                m.units[positions[0]].value in names for m in members
            )
            caller_ctx = all(
                dax_text.is_hoistable(frames[first.units[p].tok])
                and dax_text.is_unconditional(frames[first.units[p].tok])
                for p in positions
            )
            if all_measures and caller_ctx:
                mode, hint = "val", "AnyVal Val"
                note = "every use is in the caller's own context: evaluated once, eagerly"
            elif all_measures:
                mode, hint = "expr", "MeasureRef"
                note = (
                    "evaluated lazily at its use inside the body (a CALCULATE / iterator / "
                    "conditional branch), exactly where the original reference was"
                )
            else:
                mode, hint = "expr", "AnyVal Expr"
                note = "not every value is a known measure: kept lazy (expr)"
        if override:
            pname = str(override.pop(0)).strip()
        else:
            base = _BASE_NAMES[kind]
            counts[base] = counts.get(base, 0) + 1
            pname = base if counts[base] == 1 else f"{base}{counts[base]}"
            while pname.lower() in taken or pname.lower() in used_names:
                counts[base] += 1
                pname = f"{base}{counts[base]}"
        used_names.add(pname.lower())
        params.append(Param(pname, kind, mode, hint, list(positions), vals, note))

    if not diff:
        match = "exact_duplicate"
    else:
        match = "structural"
    calc_group_only = (
        len(params) == 1
        and params[0].kind == "bare"
        and bool(names)
        and all(m.units[params[0].positions[0]].value in names for m in members)
    )
    return {"params": params, "match": match, "calc_group_only": calc_group_only, "diff": diff}


def build_udf(
    members: Sequence[Member],
    params: Sequence[Param],
    function_name: str,
) -> Dict[str, Any]:
    """The function text + one call per member. The body is the FIRST member's source text
    (layout and comments kept) with every parameter position replaced by its name."""
    first = members[0]
    lo_char = first.units[0].start
    edits = []
    for prm in params:
        for p in prm.positions:
            u = first.units[p]
            edits.append((u.start - lo_char, u.end - lo_char, prm.name))
    body = dax_text.splice(first.source(), edits).strip()
    header = ", ".join(f"{p.name} : {p.type_hint}" for p in params)
    raw = f"({header}) =>\n{body}" if "\n" in body else f"({header}) => {body}"
    expression = _pretty(raw)
    calls = []
    for m in members:
        args = ", ".join(m.units[p.positions[0]].text for p in params)
        calls.append({"member": m.ref, "call": _pretty(f"{function_name}({args})")})
    return {
        "name": function_name,
        "signature": f"({header})",
        "expression": expression,
        "parameters": [p.to_dict() for p in params],
        "calls": calls,
        "compatibility_level_required": MIN_UDF_COMPATIBILITY_LEVEL,
    }


def _pretty(text: str) -> str:
    """Layout through the local formatter when it can prove the result equivalent."""
    try:
        from core.dax.local_formatter import format_dax_local

        res = format_dax_local(text)
        if res.get("success") and res.get("formatted"):
            return res["formatted"]
    except Exception:  # noqa: BLE001 - cosmetic only
        pass
    return text


def calc_group_item(members: Sequence[Member], param: Param) -> Dict[str, Any]:
    """Item expression: the first member's body with the base MEASURE replaced by
    ``SELECTEDMEASURE()``; every member maps to its base measure."""
    first = members[0]
    lo_char = first.units[0].start
    edits = [
        (first.units[p].start - lo_char, first.units[p].end - lo_char, "SELECTEDMEASURE()")
        for p in param.positions
    ]
    expr = dax_text.splice(first.source(), edits).strip()
    return {
        "type": "calc_group_item",
        "item_expression": _pretty(expr),
        "replaces": [{"measure": m.ref, "base_measure": param.values[m.ref]} for m in members],
        "notes": [
            "One calculation item replaces the whole family: apply it to the base measures "
            "through a calculation group (calc items transform MEASURE REFERENCES, so every "
            "caller that referenced the old measures must now select the item).",
            "Check calculation-group precedence and format strings (SELECTEDMEASUREFORMATSTRING) "
            "against any other group on the model.",
            "A UDF can carry the shared logic and the item can call it (UDF = logic, "
            "calc group = user-facing choice).",
        ],
    }


def suggest_function_name(members: Sequence[Member]) -> str:
    """A PascalCase name from the words every member name shares (prefix, else suffix)."""
    words = [re.findall(r"[A-Za-z0-9]+", m.name) for m in members]
    if not all(words):
        return "SharedLogic"
    common: List[str] = []
    for col in zip(*words):
        if len({w.lower() for w in col}) == 1:
            common.append(col[0])
        else:
            break
    if not common:
        rev = [list(reversed(w)) for w in words]
        for col in zip(*rev):
            if len({w.lower() for w in col}) == 1:
                common.insert(0, col[0])
            else:
                break
    name = "".join(w[:1].upper() + w[1:] for w in common)
    if not name or not re.match(r"^[A-Za-z_]", name):
        name = "SharedLogic" + (name or "")
    return name


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _group_id(match: str, members: Sequence[Member]) -> str:
    body = match + "|" + "|".join(sorted(f"{m.ref}@{m.lo}" for m in members))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:10]


def _member_dict(m: Member) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "kind": m.kind,
        "table": m.table,
        "name": m.name,
        "ref": m.ref,
        "tokens": len(m.units),
    }
    if m.scope == "fragment":
        d["char_range"] = list(m.char_range())
    return d


def _udf_notes(members: Sequence[Member], scope: str) -> List[str]:
    notes = [
        "UDFs are expanded inline at the call site: consolidation is performance-neutral, it is a "
        "maintenance lever (benchmark before claiming a speed-up).",
        "A UDF body gets NO automatic context transition: a call made from a row context (an "
        "iterator, a calculated column) does not behave like a measure reference there - wrap the "
        "call in CALCULATE (or the bare column reads in the body).",
    ]
    if len(members) < 3:
        notes.append(
            "Only 2 copies: the canon recommends a UDF from 3+ copies of the same logic "
            "(or a house convention worth centralising)."
        )
    if any(m.kind != "measure" for m in members):
        notes.append(
            "Members include non-measure objects (calc items / calculated columns): only whole "
            "MEASURE bodies can be rewritten by extract_udf."
        )
    if any(u.value == "SELECTEDMEASURE" for m in members for u in m.units):
        notes.append(
            "The body uses SELECTEDMEASURE(): it belongs to a calculation item, "
            "not a plain measure."
        )
    if scope == "fragment":
        notes.append(
            "Fragment clone: extract_udf does not rewrite fragments (v1). Create the function by "
            "hand (02_Model_Operations object_type=function) and replace the call sites after "
            "verifying values."
        )
    return notes


def _proposal(
    members: Sequence[Member],
    analysis: Dict[str, Any],
    scope: str,
    measure_names: Optional[Set[str]],
) -> Dict[str, Any]:
    params: List[Param] = analysis["params"]
    if analysis["match"] == "exact_duplicate":
        return {
            "type": None,
            "reason": "identical bodies: keep one measure and point the others at it "
            "(or delete the duplicates after checking report bindings)",
        }
    fn_name = suggest_function_name(members)
    udf = build_udf(members, params, fn_name)
    out: Dict[str, Any] = {"type": "udf", "udf": udf, "suggested_function_name": fn_name}
    if analysis["calc_group_only"] and scope == "measure":
        out["type"] = "calc_group_item"
        out["calc_group"] = calc_group_item(members, params[0])
        out["alternative"] = "udf"
    out["notes"] = _udf_notes(members, scope)
    return out


def _extractable(members: Sequence[Member], scope: str, analysis: Dict[str, Any]) -> bool:
    return (
        scope == "measure"
        and analysis["match"] == "structural"
        and all(m.kind == "measure" for m in members)
        and not any(u.value == "SELECTEDMEASURE" for m in members for u in m.units)
    )


def _structural_group(
    members: List[Member],
    scope: str,
    measure_names: Optional[Set[str]],
) -> Dict[str, Any]:
    analysis = analyse_group(members, measure_names=measure_names)
    rec: Dict[str, Any] = {
        "group_id": _group_id(analysis["match"], members),
        "scope": scope,
        "match": analysis["match"],
        "similarity": 1.0,
        "size": len(members),
        "tokens": len(members[0].units),
        "members": [_member_dict(m) for m in members],
        "parameters": [p.to_dict() for p in analysis["params"]],
        "proposal": _proposal(members, analysis, scope, measure_names),
        "extractable_by_transform": _extractable(members, scope, analysis),
        "recommended": len(members) >= 3 and analysis["match"] == "structural",
    }
    return rec


def _near_pairs(
    members: List[Member], min_similarity: float, max_objects: int
) -> List[Tuple[int, int, float]]:
    """Candidate near-clone pairs (shingle prefilter, then SequenceMatcher ratio)."""
    idx = members[:max_objects]
    shingles: List[Set[Tuple[str, ...]]] = []
    for m in idx:
        k = m.keys
        shingles.append({k[i : i + 3] for i in range(max(0, len(k) - 2))})
    inverted: Dict[Tuple[str, ...], List[int]] = {}
    for i, sh in enumerate(shingles):
        for s in sh:
            inverted.setdefault(s, []).append(i)
    pairs: List[Tuple[int, int, float]] = []
    seen: Set[Tuple[int, int]] = set()
    for i, sh in enumerate(shingles):
        counts: Dict[int, int] = {}
        for s in sh:
            lst = inverted[s]
            if len(lst) > 200:  # a shingle every body has (VAR ... RETURN) says nothing
                continue
            for j in lst:
                if j > i:
                    counts[j] = counts.get(j, 0) + 1
        for j, c in counts.items():
            if (i, j) in seen:
                continue
            union = len(sh) + len(shingles[j]) - c
            if union and c / union < 0.5:
                continue
            seen.add((i, j))
            a, b = idx[i].keys, idx[j].keys
            # the best possible ratio of two sequences of these lengths
            if 2 * min(len(a), len(b)) / max(1, len(a) + len(b)) < min_similarity:
                continue
            ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
            if ratio >= min_similarity:
                pairs.append((i, j, ratio))
    return pairs


def _differences(a: Member, b: Member, limit: int = 8) -> List[Dict[str, Any]]:
    ua, ub = a.units, b.units
    sm = difflib.SequenceMatcher(None, [u.key for u in ua], [u.key for u in ub], autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        out.append(
            {
                "op": tag,
                a.ref: " ".join(u.text for u in ua[i1:i2])[:160],
                b.ref: " ".join(u.text for u in ub[j1:j2])[:160],
            }
        )
        if len(out) >= limit:
            break
    return out


def _fragments(
    members: List[Member], min_units: int, max_units: int
) -> Dict[Tuple[str, ...], List[Member]]:
    """Call sub-trees (function + balanced parens) by structural signature."""
    from core.dax.tokenizer import DaxLexer

    out: Dict[Tuple[str, ...], List[Member]] = {}
    lexer = DaxLexer()
    for m in members:
        nz = m.norm
        pmap = lexer.build_paren_map(nz.code)
        total = len(nz.units)
        for ui, u in enumerate(nz.units):
            if u.kind != "fn":
                continue
            open_tok = u.tok_end + 1
            if open_tok >= len(nz.code) or nz.code[open_tok].type != TokenType.PAREN_OPEN:
                continue
            close_tok = pmap.get(open_tok)
            if close_tok is None:
                continue
            hi_u = nz.unit_of_token(close_tok)
            if hi_u is None:
                continue
            lo, hi = ui, hi_u + 1
            size = hi - lo
            if size < min_units or size > max_units or (lo == 0 and hi == total):
                continue
            # a VAR declared outside the fragment cannot be referenced from a function body
            outside = False
            for x in nz.units[lo:hi]:
                if x.kind == "var":
                    decl = nz.var_decl_unit.get(x.value)
                    if decl is None or decl < lo:
                        outside = True
                        break
            if outside:
                continue
            frag = Member(m.kind, m.table, m.name, nz, lo, hi, scope="fragment")
            out.setdefault(frag.keys, []).append(frag)
    return out


def find_clones(
    objects: Iterable[Dict[str, Any]],
    *,
    min_tokens: int = 12,
    min_similarity: float = 0.85,
    min_group_size: int = 2,
    include_fragments: bool = True,
    include_near: bool = True,
    measure_names: Optional[Set[str]] = None,
    max_groups: int = 50,
    max_near_objects: int = 1500,
) -> Dict[str, Any]:
    """Clone groups over *objects* (dicts ``{kind, table, name, expression}``)."""
    members = members_from_objects(objects, min_tokens)
    names = {x.lower() for x in (measure_names or set())} or None
    groups: List[Dict[str, Any]] = []

    by_sig: Dict[Tuple[str, ...], List[Member]] = {}
    for m in members:
        by_sig.setdefault(m.keys, []).append(m)
    grouped: Set[int] = set()
    for sig, ms in by_sig.items():
        if len(ms) >= max(2, min_group_size):
            groups.append(_structural_group(ms, "measure", names))
            grouped.update(id(m) for m in ms)

    if include_near:
        rest = [m for m in members if id(m) not in grouped]
        parent = list(range(len(rest[:max_near_objects])))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        edges = _near_pairs(rest, float(min_similarity), max_near_objects)
        ratios: Dict[Tuple[int, int], float] = {}
        for i, j, r in edges:
            ratios[(i, j)] = r
            parent[find(i)] = find(j)
        clusters: Dict[int, List[int]] = {}
        for i in range(len(parent)):
            clusters.setdefault(find(i), []).append(i)
        for ids in clusters.values():
            if len(ids) < max(2, min_group_size):
                continue
            ms = [rest[i] for i in ids]
            sims = [r for (i, j), r in ratios.items() if i in ids and j in ids]
            base = ms[0]
            groups.append(
                {
                    "group_id": _group_id("near", ms),
                    "scope": "measure",
                    "match": "near",
                    "similarity": round(min(sims) if sims else float(min_similarity), 3),
                    "size": len(ms),
                    "tokens": len(base.units),
                    "members": [_member_dict(m) for m in ms],
                    "differences": {m.ref: _differences(base, m) for m in ms[1:6]},
                    "parameters": [],
                    "proposal": {
                        "type": None,
                        "reason": "the structures differ (an extra / missing argument, another "
                        "function): align the bodies, then run find_clones again - or extract "
                        "the common part by hand",
                    },
                    "extractable_by_transform": False,
                    "recommended": False,
                }
            )

    if include_fragments:
        # a fragment shared only by objects that are already one whole-body clone group adds nothing
        body_sets = [
            {(m.table, m.name) for m in ms}
            for sig, ms in by_sig.items()
            if len(ms) >= max(2, min_group_size)
        ]
        frags = _fragments(members, max(8, int(min_tokens)), 400)
        kept: List[Tuple[Tuple[str, ...], List[Member]]] = []
        for sig, fms in frags.items():
            objs_of = {(f.table, f.name) for f in fms}
            if len(objs_of) < 2 or any(objs_of <= b for b in body_sets):
                continue
            kept.append((sig, fms))
        # maximal first: a group is dropped when every occurrence sits inside an already kept one
        kept.sort(key=lambda kv: -len(kv[0]))
        taken: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}
        for sig, fms in kept:
            if all(
                any(a <= f.lo and f.hi <= b for a, b in taken.get((f.table, f.name), ()))
                for f in fms
            ):
                continue
            for f in fms:
                taken.setdefault((f.table, f.name), []).append((f.lo, f.hi))
            groups.append(_structural_group(fms, "fragment", names))
            _annotate_fragment_context(groups[-1], fms)

    groups.sort(key=lambda g: (-(g["size"] * g["tokens"]), g["group_id"]))
    total = len(groups)
    by_match: Dict[str, int] = {}
    for g in groups:
        key = g["match"] if g["scope"] == "measure" else "fragment"
        by_match[key] = by_match.get(key, 0) + 1
    out: Dict[str, Any] = {
        "objects_scanned": len(members),
        "total_groups": total,
        "by_match": by_match,
        "groups": groups[: max(0, int(max_groups))],
    }
    if total > len(out["groups"]):
        out["omitted_groups"] = total - len(out["groups"])
    return out


def _annotate_fragment_context(group: Dict[str, Any], frags: Sequence[Member]) -> None:
    """Flag fragment sites that sit in a row context (a UDF call there loses the context
    transition of a measure reference)."""
    rows = []
    for f in frags:
        frames = f.norm.frames()
        if dax_text.has_row_context(frames[f.units[0].tok]):
            rows.append(f.ref)
    if rows:
        group["row_context_sites"] = rows
        group["proposal"].setdefault("notes", []).append(
            "Some occurrences sit in a row context or a CALCULATE filter argument "
            f"({', '.join(sorted(set(rows))[:5])}): a UDF call there does not get the "
            "context transition of a measure reference - wrap the call in CALCULATE and "
            "value-check at the real grain before rebinding."
        )


# ---------------------------------------------------------------------------
# Whole-body group analysis for extract_udf
# ---------------------------------------------------------------------------


def analyse_measures(
    objects: Sequence[Dict[str, Any]],
    *,
    measure_names: Optional[Set[str]] = None,
    parameter_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Structural analysis of a hand-picked set of measures (the extract_udf input).

    ``{"ok": True, "members", "analysis"}`` when every body shares one structure, else
    ``{"ok": False, "reason", "differences"}``.
    """
    members = [
        Member(
            "measure",
            str(o.get("table") or ""),
            str(o.get("name") or ""),
            normalise(o["expression"]),
        )
        for o in objects
    ]
    if len(members) < 2:
        return {"ok": False, "reason": "at least two measures are needed"}
    sigs = {m.keys for m in members}
    if len(sigs) > 1:
        base = members[0]
        return {
            "ok": False,
            "reason": "the measures do not share one structure (find_clones lists near clones)",
            "differences": {
                m.ref: _differences(base, m) for m in members[1:6] if m.keys != base.keys
            },
        }
    names = {x.lower() for x in (measure_names or set())} or None
    analysis = analyse_group(members, measure_names=names, parameter_names=parameter_names)
    return {"ok": True, "members": members, "analysis": analysis}
