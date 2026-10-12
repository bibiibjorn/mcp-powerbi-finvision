"""Variable extraction strategy — hoist a repeated measure reference into a VAR.

Review E-07: hoisting was blocked only inside a hard-coded list of functions,
so references inside TOTALYTD, FIRSTNONBLANK, TOPN, SUMMARIZECOLUMNS, UDF /
calc-group calls ... were hoisted (``TOTALYTD(_Sales, …)`` = the period value);
the replacement was a raw regex that also rewrote string literals; generated
names could be invalid (``_Margin%``); the VAR was inserted before the first
regex ``RETURN`` (possibly nested or inside a string).

The check is now INVERTED (``core.codemods.dax_text.is_hoistable``): a
reference is hoisted only when EVERY occurrence sits at depth 0 or inside
scalar-safe functions (IF, SWITCH, DIVIDE, arithmetic, ...). Replacement is
token-based, the name is sanitised to ``[A-Za-z0-9_]`` and deduplicated against
existing VARs, and the declaration is prepended to the whole expression.

Review D-02 (2026-10-03): scalar-safe is not enough - IF / SWITCH / COALESCE / IFERROR branches
(and DIVIDE's alternate result) are evaluated lazily, so a reference that occurs ONLY inside a
branch is never hoisted to the top (that installs canon AP-10: "VAR defeats short-circuit").
A reference is hoisted to the top only when it also occurs unconditionally (in the IF condition /
SWITCH expression / at depth 0); a reference repeated inside ONE branch gets its VAR declared
INSIDE that branch (``IF(c, VAR _x = [M] RETURN ...)``, canon rule 3 "ALSO GOOD"); references
spread over different branches / SWITCH lanes are left alone.
"""

import re
from collections import OrderedDict
from typing import List, Optional, Set

from core.codemods import dax_text
from core.dax.tokenizer import Token, TokenType
from core.dax.knowledge import DaxFunctionDatabase
from core.dax.analyzer.models import AnalysisIssue
from core.dax.optimizer.models import RewriteResult
from .base import RewriteStrategy


class _Site:
    __slots__ = ("start", "end", "hoistable", "unconditional", "branch", "cond_frame")

    def __init__(self, start, end, hoistable, unconditional, branch, cond_frame):
        self.start = start
        self.end = end
        self.hoistable = hoistable
        self.unconditional = unconditional
        self.branch = branch  # tuple of (open_idx, arg) of every enclosing conditional branch
        self.cond_frame = cond_frame  # innermost enclosing conditional CallFrame, or None


def _bare_ref_sites(dax: str) -> "OrderedDict[str, List[_Site]]":
    """norm(name) -> sites of every lone ``[X]`` reference, with their conditional context."""
    from core.dax.analyzer.structure import call_frames, is_conditional_arg
    from core.dax.dax_reference_parser import scan_references

    code = dax_text.code_tokens(dax)
    stacks = dax_text.frame_stacks(code)
    cframes = call_frames(code)
    by_start = {t.start: i for i, t in enumerate(code)}
    out: "OrderedDict[str, List[_Site]]" = OrderedDict()
    for ref in scan_references(dax, code=code):
        if ref.kind != "bracket":
            continue
        idx = by_start.get(ref.start)
        frames = stacks[idx] if idx is not None else ()
        cf = cframes[idx] if idx is not None else ()
        cond = [f for f in cf if is_conditional_arg(f.name, f.arg)]
        out.setdefault(dax_text.norm_name(ref.name), []).append(
            _Site(
                ref.start,
                ref.end,
                dax_text.is_hoistable(frames),
                dax_text.is_unconditional(frames),
                tuple((f.open_idx, f.arg) for f in cond),
                cond[-1] if cond else None,
            )
        )
    return out


class VariableExtractionStrategy(RewriteStrategy):
    """Extract a repeated measure reference into a VAR declaration."""

    strategy_name = "variable_extraction"

    def can_apply(
        self,
        issue: AnalysisIssue,
        tokens: List[Token],
        function_db: DaxFunctionDatabase,
    ) -> bool:
        if issue.rewrite_strategy == "variable_extraction":
            return True
        if "MEASURE_REF" in (issue.rule_id or ""):
            return True
        return False

    def apply(
        self,
        dax: str,
        tokens: List[Token],
        issue: AnalysisIssue,
        function_db: DaxFunctionDatabase,
    ) -> Optional[RewriteResult]:
        sites = _bare_ref_sites(dax)
        top: dict = {}
        in_branch: dict = {}
        for name, occ in sites.items():
            if len(occ) < 2 or not all(o.hoistable for o in occ):
                continue
            if any(o.unconditional for o in occ):
                top[name] = occ  # evaluated eagerly anyway: a top-level VAR is neutral
            elif len({o.branch for o in occ}) == 1:
                in_branch[name] = occ  # one branch only: VAR inside that branch
        if top:
            target = max(top, key=lambda n: len(top[n]))
            return self._hoist_to_top(dax, issue, top[target])
        if in_branch:
            target = max(in_branch, key=lambda n: len(in_branch[n]))
            return self._declare_in_branch(dax, issue, in_branch[target])
        return None

    def _hoist_to_top(self, dax: str, issue: AnalysisIssue, occ: List[_Site]):
        ref_text = dax[occ[0].start : occ[0].end]
        var_name = self._generate_var_name(ref_text, set(dax_text.declared_var_names(dax)))
        body = dax_text.splice(dax, [(o.start, o.end, var_name) for o in occ])
        rewritten = self._prepend_var(body, var_name, ref_text)
        valid = self._validate_rewrite(dax, rewritten)
        conditional = any(not o.unconditional for o in occ)
        return RewriteResult(
            strategy=self.strategy_name,
            rule_id=issue.rule_id,
            original_fragment=ref_text,
            rewritten_fragment=f"VAR {var_name} = {ref_text}",
            full_rewritten_dax=rewritten,
            explanation=(
                f"Extracted {len(occ)}x repeated reference {ref_text} into VAR {var_name} "
                "(every occurrence is evaluated in the same filter context"
                + (
                    "; some sit in conditional branches, but the reference is also evaluated "
                    "unconditionally, so the VAR adds no eager work)."
                    if conditional
                    else ")."
                )
            ),
            # a hoist that touches a conditional branch is never reported as high confidence
            confidence="medium" if conditional else "high",
            estimated_improvement="Avoids repeated measure evaluation",
            validation_passed=valid,
        )

    def _declare_in_branch(self, dax: str, issue: AnalysisIssue, occ: List[_Site]):
        """All occurrences live in ONE conditional branch: declare the VAR inside it."""
        from core.dax.analyzer.structure import arg_slices

        frame = occ[0].cond_frame
        if frame is None:
            return None
        code = dax_text.code_tokens(dax)
        slices = arg_slices(code, frame.name_idx)
        if frame.arg >= len(slices):
            return None
        a, b = slices[frame.arg]
        if b <= a:
            return None
        ref_text = dax[occ[0].start : occ[0].end]
        var_name = self._generate_var_name(ref_text, set(dax_text.declared_var_names(dax)))
        edits = [(code[a].start, code[a].start, f"VAR {var_name} = {ref_text} RETURN ")]
        edits += [(o.start, o.end, var_name) for o in occ]
        rewritten = dax_text.splice(dax, edits)
        valid = self._validate_rewrite(dax, rewritten)
        return RewriteResult(
            strategy=self.strategy_name,
            rule_id=issue.rule_id,
            original_fragment=ref_text,
            rewritten_fragment=f"VAR {var_name} = {ref_text} RETURN ...  (inside the branch)",
            full_rewritten_dax=rewritten,
            explanation=(
                f"{ref_text} is repeated {len(occ)}x inside ONE conditional branch: the VAR "
                f"{var_name} is declared inside that branch, so it is only evaluated when the "
                "branch is taken (a top-level VAR would defeat short-circuit evaluation - canon "
                "rule 3 / AP-10)."
            ),
            confidence="medium",
            estimated_improvement="Avoids repeated measure evaluation inside the branch",
            validation_passed=valid,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_var_name(expression: str, taken: Optional[Set[str]] = None) -> str:
        """``[Sales Amount]`` -> ``_SalesAmount``; ``[Margin %]`` -> ``_Margin``.

        Only ``[A-Za-z0-9_]`` survives; deduplicated (case-insensitively)
        against *taken* (upper-cased existing VAR names) with a numeric suffix.
        """
        clean = expression.strip("[] '\"")
        if "." in clean:
            clean = clean.split(".")[-1]
        words = re.split(r"[^A-Za-z0-9]+", clean)
        camel = "".join(w[:1].upper() + w[1:] for w in words if w)
        base = f"_{camel}" if camel else "_Var"
        taken = {t.upper() for t in (taken or set())}
        name, n = base, 2
        while name.upper() in taken:
            name = f"{base}{n}"
            n += 1
        return name

    @staticmethod
    def _prepend_var(body: str, var_name: str, ref_text: str) -> str:
        code = dax_text.code_tokens(body)
        starts_with_var = (
            bool(code) and code[0].type == TokenType.KEYWORD and (code[0].value.upper() == "VAR")
        )
        if starts_with_var:
            return f"VAR {var_name} = {ref_text}\n{body}"
        return f"VAR {var_name} = {ref_text}\nRETURN\n{body}"

    @classmethod
    def _insert_var_and_replace(cls, dax: str, target_ref: str, var_name: str) -> Optional[str]:
        """Token-based replacement of every lone *target_ref* by *var_name* plus the
        VAR declaration (qualified ``'T'[X]`` / ``T[X]`` refs are never touched)."""
        sites = _bare_ref_sites(dax).get(dax_text.norm_name(target_ref.strip()[1:-1]), [])
        if not sites:
            return None
        body = dax_text.splice(dax, [(o.start, o.end, var_name) for o in sites])
        return cls._prepend_var(body, var_name, target_ref)
