"""DAX user-defined-function (UDF) rules — GA syntax hygiene (CL 1702).

DAX UDFs went GA in June 2026 with typed parameters (scalar subtypes, TABLE,
reference types), parameter passing modes ``VAL`` (eager) / ``EXPR`` (lazy),
optional parameters with default expressions, and ``///`` doc comments.

These rules read the parameter list off the tokenized ``FUNCTION <name> = (...)``
header. ``DaxLexer.tokenize_code`` strips comments but preserves the whole
signature, so the header survives tokenization:

    FUNCTION Margin = (sales: NUMERIC VAL, rate) => ...
    KEYWORD(FUNCTION) IDENTIFIER(Margin) OPERATOR(=) PAREN_OPEN
    IDENTIFIER(sales) COLON(:) IDENTIFIER(NUMERIC) IDENTIFIER(VAL) COMMA
    IDENTIFIER(rate) PAREN_CLOSE ...

Both rules are no-ops on anything that is not a UDF definition (a measure body
has no ``FUNCTION <name> = (`` header), so they are inert for the ~all of the
existing measure-linting surface.
"""

from typing import List, Optional, Tuple

from core.dax.tokenizer import TokenType
from core.dax.analyzer.models import AnalysisIssue
from .base import PythonRule

# Parameter passing modes (GA). VAL = eager scalar, EXPR = lazy expression.
_PASSING_MODES = {"VAL", "EXPR"}


def _find_signature(tokens) -> Optional[Tuple[int, int, str]]:
    """Locate the UDF header's parameter list.

    Returns ``(open_idx, close_idx, function_name)`` for the ``(`` / ``)`` of the
    parameter list, or None when the token stream is not a UDF definition.
    Requires the full ``FUNCTION <name> = ( ... ) =>`` shape so a measure that
    merely mentions the word "function" cannot match. The name may be namespaced
    (``Sales.AddMargin``), which the lexer emits as IDENTIFIER DOT IDENTIFIER.
    """
    meaningful = [
        (i, t)
        for i, t in enumerate(tokens)
        if t.type not in (TokenType.WHITESPACE, TokenType.NEWLINE)
    ]
    for pos, (idx, tok) in enumerate(meaningful):
        if tok.value.upper() != "FUNCTION" or tok.type not in (
            TokenType.IDENTIFIER,
            TokenType.KEYWORD,
            TokenType.FUNCTION,
        ):
            continue
        # FUNCTION <name[.name...]> = (
        cursor = pos + 1
        name_parts: List[str] = []
        while cursor < len(meaningful):
            part = meaningful[cursor][1]
            if part.type == TokenType.DOT or part.value == ".":
                name_parts.append(".")
                cursor += 1
                continue
            if name_parts and name_parts[-1] != ".":
                break
            if part.type not in (TokenType.IDENTIFIER, TokenType.KEYWORD, TokenType.FUNCTION):
                break
            name_parts.append(part.value)
            cursor += 1
        if not name_parts or cursor + 1 >= len(meaningful):
            continue
        eq_tok = meaningful[cursor][1]
        open_idx, open_tok = meaningful[cursor + 1]
        if eq_tok.value != "=" or open_tok.type != TokenType.PAREN_OPEN:
            continue
        fn_name = "".join(name_parts)
        # Matching close paren for the parameter list.
        depth = 0
        for j in range(open_idx, len(tokens)):
            t = tokens[j]
            if t.type == TokenType.PAREN_OPEN:
                depth += 1
            elif t.type == TokenType.PAREN_CLOSE:
                depth -= 1
                if depth == 0:
                    return open_idx, j, fn_name
        return None
    return None


def _split_parameters(tokens, open_idx: int, close_idx: int) -> List[List]:
    """Split the parameter list into per-parameter token runs (top-level commas)."""
    params: List[List] = []
    current: List = []
    depth = 0
    for j in range(open_idx + 1, close_idx):
        t = tokens[j]
        if t.type in (TokenType.WHITESPACE, TokenType.NEWLINE):
            continue
        if t.type == TokenType.PAREN_OPEN:
            depth += 1
        elif t.type == TokenType.PAREN_CLOSE:
            depth -= 1
        if t.type == TokenType.COMMA and depth == 0:
            if current:
                params.append(current)
            current = []
            continue
        current.append(t)
    if current:
        params.append(current)
    return params


def _param_name(run: List) -> Optional[str]:
    """Parameter name; a leading passing mode (``VAL x: …``) is skipped."""
    for t in run:
        if t.value.upper() in _PASSING_MODES:
            continue
        return t.value
    return None


def _has_type_annotation(run: List) -> bool:
    """True when the parameter carries a ``: <TYPE>`` annotation.

    The lexer emits the colon as a COLON token; look for it anywhere after the
    parameter name (before any ``=`` default).
    """
    for t in run[1:]:
        if t.value == "=":
            break
        if t.value == ":":
            return True
    return False


def _passing_mode(run: List) -> Optional[str]:
    """Return 'VAL' / 'EXPR' when the parameter declares a passing mode."""
    for t in run[1:]:
        if t.value == "=":
            break
        upper = t.value.upper()
        if upper in _PASSING_MODES:
            return upper
    return None


_SCALAR_TYPES = {
    "SCALAR",
    "NUMERIC",
    "INT64",
    "DOUBLE",
    "DECIMAL",
    "STRING",
    "DATETIME",
    "BOOLEAN",
    "VARIANT",
}


def _declared_types(run: List) -> List[str]:
    """Upper-case type words after the ``:`` (passing modes excluded)."""
    out: List[str] = []
    seen_colon = False
    for t in run[1:]:
        if t.value == "=":
            break
        if t.value == ":":
            seen_colon = True
            continue
        if seen_colon and t.value.upper() not in _PASSING_MODES:
            out.append(t.value.upper())
    return out


def udf_signature(text: str):
    """``(function_name, [passing mode per parameter])`` of a ``FUNCTION <name> = (...) =>``
    definition ("expr" / "val" / "" when unspecified), or None when *text* is not a UDF header.

    Feeds ``ModelSchema.udf_signatures`` so call-site rules (CORR_UDF_EXPR_BARE_COLUMN) know which
    parameters are lazily evaluated expressions (canon AP-48)."""
    from core.dax.tokenizer import DaxLexer

    tokens = DaxLexer().tokenize_code(text or "")
    sig = _find_signature(tokens)
    if sig is None:
        return None
    open_idx, close_idx, fn_name = sig
    modes = [
        (_passing_mode(run) or "").lower() for run in _split_parameters(tokens, open_idx, close_idx)
    ]
    return fn_name, modes


class UdfUntypedParameterRule(PythonRule):
    """A UDF parameter declared with no type annotation.

    An untyped parameter falls back to the implicit/variant type: the engine
    loses compile-time checking and can coerce the argument differently than the
    body assumes, silently changing semantics. GA syntax lets every parameter
    carry a type (``sales: NUMERIC``, ``t: TABLE``, ``e: ANYREF``).
    """

    rule_id = "PY_UDF_UNTYPED_PARAMETER"
    category = "maintainability"
    severity = "low"
    title = "UDF parameter has no type annotation"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        sig = _find_signature(tokens)
        if sig is None:
            return issues
        open_idx, close_idx, fn_name = sig

        untyped = [
            name
            for run in _split_parameters(tokens, open_idx, close_idx)
            if not _has_type_annotation(run) and (name := _param_name(run))
        ]
        if not untyped:
            return issues

        listed = ", ".join(untyped)
        issues.append(
            self._make_issue(
                f"UDF '{fn_name}' declares parameter(s) without a type annotation: "
                f"{listed}. An untyped parameter defaults to ANYVAL (the documented default): "
                "it works, but typing it gives compile-time checking and documents intent "
                "(style recommendation).",
                "Annotate each parameter with its type, e.g. "
                "`(sales: NUMERIC VAL, dim: TABLE)`. Use ANYREF/EXPR for a lazily "
                "evaluated expression parameter.",
                location=fn_name,
                match_text=listed,
            )
        )
        return issues


class UdfEagerEvalRiskRule(PythonRule):
    """A ``VAL`` (eager) parameter used as a CALCULATE/CALCULATETABLE filter.

    CONSERVATIVE by construction. It fires only on the one pattern where the
    passing mode is unambiguously wrong from the text alone: a parameter declared
    ``VAL`` (eagerly evaluated to a scalar value at call time, in the CALLER's
    filter context) that is then handed to CALCULATE / CALCULATETABLE in a
    FILTER-argument position. A filter argument must be a lazily evaluated
    expression (``EXPR`` / ``ANYREF``); an eager scalar there either errors or
    silently applies the caller-context value instead of the intended predicate.

    Deliberately NOT flagged (would be noisy / not decidable from text):
      - a ``VAL`` parameter used as the CALCULATE *first* argument (the measure
        expression) — passing a pre-computed scalar there is legitimate;
      - a ``VAL`` parameter merely referenced inside an iterator (SUMX etc.) —
        an eager scalar is the NORMAL and usually correct choice there;
      - an ``EXPR`` parameter used as a plain scalar — lazy re-evaluation is a
        performance question, not a correctness one, and depends on the model;
      - a ``VAL`` parameter used as a comparison VALUE inside a boolean filter
        (``CALCULATE([S], Sales[Amount] > threshold)``) — the normal, correct use.
        Only a filter argument that IS the parameter is flagged.
    """

    rule_id = "PY_UDF_EAGER_EVAL_RISK"
    category = "correctness"
    severity = "medium"
    title = "Eager (VAL) UDF parameter used as a CALCULATE filter"

    _FILTER_FUNCTIONS = {"CALCULATE", "CALCULATETABLE"}

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        sig = _find_signature(tokens)
        if sig is None:
            return issues
        open_idx, close_idx, fn_name = sig

        # Only explicitly scalar-typed VAL parameters: a TABLE VAL / ANYVAL / untyped VAL value
        # is a valid CALCULATE filter (an eager table is a normal table filter - D-11).
        val_params = {
            name.upper()
            for run in _split_parameters(tokens, open_idx, close_idx)
            if _passing_mode(run) == "VAL"
            and (name := _param_name(run))
            and (set(_declared_types(run)) & _SCALAR_TYPES)
            and "TABLE" not in _declared_types(run)
        }
        if not val_params:
            return issues

        flagged: List[str] = []
        for idx in self._find_functions(tokens, self._FILTER_FUNCTIONS):
            if idx <= close_idx:
                # Part of the signature/default expressions, not the body.
                continue
            args = self._get_function_args(tokens, idx)
            # Filter arguments are everything AFTER the first argument. Only a
            # filter argument that IS the parameter is wrong (review D-08): a VAL
            # scalar compared inside a boolean filter (`Sales[Amount] > threshold`)
            # is the normal, correct use.
            for arg in args[1:]:
                sig = [
                    t
                    for t in arg
                    if t.type
                    not in (
                        TokenType.WHITESPACE,
                        TokenType.NEWLINE,
                        TokenType.COMMENT_LINE,
                        TokenType.COMMENT_BLOCK,
                    )
                ]
                if (
                    len(sig) == 1
                    and sig[0].type == TokenType.IDENTIFIER
                    and sig[0].value.upper() in val_params
                    and sig[0].value not in flagged
                ):
                    flagged.append(sig[0].value)

        if not flagged:
            return issues

        listed = ", ".join(flagged)
        issues.append(
            self._make_issue(
                f"UDF '{fn_name}' passes eagerly evaluated (VAL) parameter(s) "
                f"{listed} into a CALCULATE/CALCULATETABLE filter argument. A VAL "
                "parameter is collapsed to a single value in the CALLER's filter "
                "context before the body runs, so it cannot act as a predicate — "
                "the filter either errors or silently applies the caller-context "
                "value instead of the intended condition.",
                f"Declare {listed} as a lazily evaluated expression parameter "
                "(EXPR, e.g. `myFilter: ANYREF EXPR`) so CALCULATE receives the "
                "predicate itself rather than a pre-computed value.",
                location=fn_name,
                match_text=listed,
            )
        )
        return issues
