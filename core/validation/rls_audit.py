"""RLS audit engine (F-07): ``04_Query_Operations rls_audit`` and the ``rls_audit`` suite test type.

For every role and a set of PROBE IDENTITIES (a known UPN, a user with zero grants, the empty
string, a casing variant, a B2B ``#EXT#`` guest form) the role's table filters are run through the
same rewrite ``simulate_as_user`` uses (``core.dax.rls_rewriter``: ``USERPRINCIPALNAME()`` and
friends become literals, then ``CALCULATETABLE`` + one ``FILTER`` per filtered table) and each
RLS-filtered table's VISIBLE row count is compared with its UNFILTERED count.

Findings (``severity`` critical | warn | info):

* ``fail_open`` (critical) -- an identity that must see nothing (zero-grant, empty, B2B guest of
  nobody) sees rows. The 2026-09 S02 review found exactly this by hand.
* ``sees_everything`` (critical) -- the zero-grant identity sees every row of a filtered table.
* ``fail_closed_for_everyone`` (warn) -- the KNOWN identity sees no row in any filtered table.
* ``static_filter`` (info) -- a filter that never calls USERPRINCIPALNAME / USERNAME /
  USEROBJECTID / CUSTOMDATA: every identity sees the same rows.
* ``filter_allows_all`` (warn) -- the filter is a literal TRUE.
* ``case_sensitivity_mismatch`` / ``b2b_identity_mismatch`` (warn) -- the casing variant / the
  guest form of the known identity sees something different from the known identity.
* ``role_without_members`` (warn) -- no members assigned (Desktop-only models legitimately have
  none; the flag is informational there).
* ``bidirectional_leak_path`` (warn) -- tables reachable from an RLS-filtered table ONLY through
  bidirectional relationships (security filtering can leak around a filter / into other dims).

The simulation is an APPROXIMATION (``simulation: "filter_rewrite"``): Power BI Desktop rejects
``EffectiveUserName``, filters here propagate over every relationship direction and ALL/REMOVEFILTERS
inside measures can remove them. The result says so (``approximation.known_gaps``).

Engine functions take their collaborators (roles, query runner, relationships) as arguments so they
are unit-testable with fakes; :func:`audit_from_connection` is the thin glue used by the handler and
the suite runner.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from core.infrastructure.adomd_utils import engine_message

SIMULATION = "filter_rewrite"
SEVERITIES = ("info", "warn", "critical")
_SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}
_IDENTITY_FUNC_RE = re.compile(
    r"\b(USERPRINCIPALNAME|USERNAME|USEROBJECTID|CUSTOMDATA)\s*\(", re.IGNORECASE
)
_TRIVIAL_TRUE = {"true", "true()", "1=1", "1 = 1", "(true)", "(true())"}
#: Identity that is substituted while rewriting, then replaced by the empty DAX string. The
#: shared rewriter treats ``as_user=""`` as "no identity" and leaves USERPRINCIPALNAME() alone.
_EMPTY_SENTINEL = "\x01__rls_empty__\x01"
_ZERO_GRANT_UPN = "no.such.user@rls-audit.invalid"

RunQuery = Callable[[str], Dict[str, Any]]


# --------------------------------------------------------------------------- probes
def build_probes(
    known_users: Sequence[str] = (),
    extra: Sequence[str] = (),
    *,
    zero_grant: Optional[str] = None,
) -> List[Dict[str, str]]:
    """The identities to probe, each ``{label, identity, expect}`` (``expect`` = "rows" | "none" | "same_as_known")."""
    probes: List[Dict[str, str]] = []
    users = [str(u).strip() for u in known_users if str(u or "").strip()]
    for i, u in enumerate(users):
        probes.append(
            {
                "label": "known" if len(users) == 1 else f"known_{i + 1}",
                "identity": u,
                "expect": "rows",
            }
        )
    probes.append(
        {"label": "zero_grant", "identity": zero_grant or _ZERO_GRANT_UPN, "expect": "none"}
    )
    probes.append({"label": "empty", "identity": "", "expect": "none"})
    if users:
        first = users[0]
        swapped = first.upper() if first != first.upper() else first.lower()
        probes.append({"label": "case_variant", "identity": swapped, "expect": "same_as_known"})
        b2b = _b2b_form(first)
        if b2b:
            probes.append({"label": "b2b_ext", "identity": b2b, "expect": "none"})
    for j, ident in enumerate(extra):
        if str(ident or "").strip():
            probes.append({"label": f"extra_{j + 1}", "identity": str(ident), "expect": "info"})
    return probes


def _b2b_form(upn: str) -> Optional[str]:
    """``jane@contoso.com`` -> ``jane_contoso.com#EXT#@guesttenant.onmicrosoft.com``."""
    if "@" not in upn:
        return None
    local, domain = upn.split("@", 1)
    return f"{local}_{domain}#EXT#@guesttenant.onmicrosoft.com"


# --------------------------------------------------------------------------- rewrite
def rewrite_filters_for_identity(
    role_filters: Sequence[Dict[str, Any]],
    identity: str,
    *,
    custom_data: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Role table filters with the identity functions replaced by literals (``""`` supported)."""
    from core.dax.rls_rewriter import rewrite_filter_expression

    out: List[Dict[str, str]] = []
    for f in role_filters:
        table = f.get("table") or f.get("table_name")
        expr = f.get("filter_expression") or f.get("expression") or ""
        if not table or not expr:
            continue
        probe = identity if identity != "" else _EMPTY_SENTINEL
        rewritten = rewrite_filter_expression(expr, probe, object_id=probe, custom_data=custom_data)
        if identity == "":
            rewritten = rewritten.replace('"' + _EMPTY_SENTINEL + '"', '""')
        out.append({"table": str(table), "filter_expression": rewritten})
    return out


def _count_query(tables: Sequence[str]) -> str:
    from core.dax.dax_names import dax_string, quote_table

    parts = ", ".join(
        f"{dax_string(f't{i}')}, COUNTROWS({quote_table(t)})" for i, t in enumerate(tables)
    )
    return f"EVALUATE ROW({parts})"


def _read_counts(
    res: Dict[str, Any], tables: Sequence[str]
) -> Tuple[Dict[str, Optional[int]], Optional[str]]:
    if not res.get("success"):
        return {}, engine_message(res.get("error") or "query failed")[:300]
    rows = res.get("rows") or []
    row = rows[0] if rows else {}
    counts: Dict[str, Optional[int]] = {}
    for i, t in enumerate(tables):
        val = None
        for key in (f"t{i}", f"[t{i}]"):
            if key in row:
                val = row[key]
                break
        else:
            for k, v in row.items():
                if isinstance(k, str) and k.endswith(f"[t{i}]"):
                    val = v
                    break
        counts[t] = 0 if val is None else int(val)  # COUNTROWS of an empty table is BLANK
    return counts, None


def count_visible_rows(
    run_query: RunQuery,
    tables: Sequence[str],
    role_filters: Sequence[Dict[str, Any]],
    identity: str,
    *,
    custom_data: Optional[str] = None,
) -> Tuple[Dict[str, Optional[int]], Optional[str], str]:
    """``(counts per table, error, executed_query)`` for the role's filters under *identity*."""
    from core.dax.rls_rewriter import build_as_user_query

    filters = rewrite_filters_for_identity(role_filters, identity, custom_data=custom_data)
    query = build_as_user_query(_count_query(tables), filters, "rls-audit", custom_data=custom_data)
    counts, err = _read_counts(run_query(query), tables)
    return counts, err, query


# --------------------------------------------------------------------------- relationships
def _edges(rel: Dict[str, Any]) -> List[Tuple[str, str, bool]]:
    """Filter-propagation edges ``(src, dst, only_via_bidi)`` of one active relationship."""
    if rel.get("isActive", True) is False:
        return []
    frm, to = str(rel.get("fromTable") or ""), str(rel.get("toTable") or "")
    if not frm or not to or frm == to:
        return []
    fc = str(rel.get("fromCardinality") or "Many")
    tc = str(rel.get("toCardinality") or "One")
    bidi = str(rel.get("crossFilteringBehavior") or "OneDirection") == "BothDirections"
    if fc == "One" and tc == "One":
        return [(frm, to, False), (to, frm, False)]
    # Filters flow from the One side to the Many side; TOM's OneDirection default is To -> From.
    if fc == "One" and tc != "One":
        forward, backward = (frm, to), (to, frm)
    else:
        forward, backward = (to, frm), (frm, to)
    edges = [(forward[0], forward[1], False)]
    if bidi:
        edges.append((backward[0], backward[1], True))
    return edges


def analyze_relationship_paths(
    filtered_tables: Iterable[str], relationships: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    """Tables a role filter reaches, and the ones it reaches ONLY through bidirectional hops."""
    restricted = set(filtered_tables)
    adj_single: Dict[str, set] = {}
    adj_all: Dict[str, set] = {}
    bidi_edges: List[Dict[str, str]] = []
    for rel in relationships:
        for src, dst, via_bidi in _edges(rel):
            adj_all.setdefault(src, set()).add(dst)
            if not via_bidi:
                adj_single.setdefault(src, set()).add(dst)
            else:
                bidi_edges.append({"from": src, "to": dst})

    def reach(adj: Dict[str, set]) -> set:
        seen, stack = set(restricted), list(restricted)
        while stack:
            cur = stack.pop()
            for nxt in adj.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen - restricted

    single, full = reach(adj_single), reach(adj_all)
    return {
        "reached_single_direction": sorted(single),
        "reached_only_via_bidirectional": sorted(full - single),
        "bidirectional_hops": bidi_edges,
    }


# --------------------------------------------------------------------------- audit
def _finding(severity: str, code: str, role: str, message: str, **extra: Any) -> Dict[str, Any]:
    out = {"severity": severity, "code": code, "role": role, "message": message}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def _filter_uses_identity(expr: str) -> bool:
    return bool(_IDENTITY_FUNC_RE.search(expr or ""))


def audit_role(
    role: Dict[str, Any],
    probes: Sequence[Dict[str, str]],
    run_query: RunQuery,
    *,
    unfiltered: Optional[Dict[str, Optional[int]]] = None,
    relationships: Optional[Sequence[Dict[str, Any]]] = None,
    custom_data: Optional[str] = None,
) -> Dict[str, Any]:
    name = str(role.get("name") or "?")
    perms = [p for p in (role.get("table_permissions") or []) if p.get("filter_expression")]
    filtered = [str(p.get("table")) for p in perms]
    findings: List[Dict[str, Any]] = []
    out: Dict[str, Any] = {
        "role": name,
        "members_count": role.get("members_count"),
        "filtered_tables": filtered,
        "results": {},
    }
    if role.get("members_count") == 0:
        findings.append(
            _finding("warn", "role_without_members", name, f"role '{name}' has no members")
        )
    if not perms:
        out["note"] = "role has no table filters"
        out["findings"] = findings
        return out

    for p in perms:
        expr = str(p.get("filter_expression") or "")
        t = str(p.get("table"))
        if expr.strip().lower() in _TRIVIAL_TRUE:
            findings.append(
                _finding(
                    "warn", "filter_allows_all", name, f"filter on '{t}' is a literal TRUE", table=t
                )
            )
        if not _filter_uses_identity(expr):
            findings.append(
                _finding(
                    "info",
                    "static_filter",
                    name,
                    f"filter on '{t}' does not depend on the user: every identity sees the same rows",
                    table=t,
                )
            )

    # Tables to count: every filtered table + whatever the relationship graph says it reaches.
    rel_analysis = (
        analyze_relationship_paths(filtered, relationships or []) if relationships else None
    )
    tables = list(filtered)
    if rel_analysis:
        for t in (
            rel_analysis["reached_single_direction"]
            + rel_analysis["reached_only_via_bidirectional"]
        ):
            if t not in tables:
                tables.append(t)
        out["relationship_analysis"] = rel_analysis
        if rel_analysis["reached_only_via_bidirectional"]:
            findings.append(
                _finding(
                    "warn",
                    "bidirectional_leak_path",
                    name,
                    "tables reached from an RLS-filtered table only through bidirectional "
                    f"relationships: {rel_analysis['reached_only_via_bidirectional']}",
                    tables=rel_analysis["reached_only_via_bidirectional"],
                )
            )
    if unfiltered is None:
        unfiltered, err = _read_counts(run_query(_count_query(tables)), tables)
        if err:
            out["error"] = f"unfiltered counts failed: {err}"
            out["findings"] = findings
            return out
    else:
        # the caller counted a superset once; keep only this role's tables
        unfiltered = {t: unfiltered.get(t) for t in tables if t in unfiltered}
        missing = [t for t in tables if t not in unfiltered]
        if missing:
            more, err = _read_counts(run_query(_count_query(missing)), missing)
            if err:
                out["error"] = f"unfiltered counts failed: {err}"
                out["findings"] = findings
                return out
            unfiltered.update(more)
    out["unfiltered"] = unfiltered

    by_label: Dict[str, Dict[str, Optional[int]]] = {}
    for pr in probes:
        counts, err, _q = count_visible_rows(
            run_query, tables, perms, pr["identity"], custom_data=custom_data
        )
        if err:
            out["results"][pr["label"]] = {"identity": pr["identity"], "error": err}
            findings.append(
                _finding(
                    "warn",
                    "probe_failed",
                    name,
                    f"probe '{pr['label']}' failed: {err}",
                    probe=pr["label"],
                )
            )
            continue
        by_label[pr["label"]] = counts
        out["results"][pr["label"]] = {
            "identity": pr["identity"],
            "tables": {t: {"visible": counts[t], "unfiltered": unfiltered.get(t)} for t in tables},
        }

    uses_identity = any(_filter_uses_identity(str(p.get("filter_expression"))) for p in perms)
    known = by_label.get("known") or by_label.get("known_1")
    for pr in probes:
        seen = by_label.get(pr["label"])
        if seen is None:
            continue
        counts = seen
        if pr["expect"] == "none" and uses_identity:
            leaking = {t: counts[t] for t in filtered if (counts.get(t) or 0) > 0}
            if leaking:
                everything = [
                    t
                    for t, n in leaking.items()
                    if (unfiltered.get(t) or 0) > 0 and (n or 0) >= (unfiltered.get(t) or 0)
                ]
                code = (
                    "sees_everything" if everything and pr["label"] == "zero_grant" else "fail_open"
                )
                findings.append(
                    _finding(
                        "critical",
                        code,
                        name,
                        f"identity '{pr['label']}' ({pr['identity']!r}) sees rows of {sorted(leaking)} "
                        "although it holds no grant (fail-open)",
                        probe=pr["label"],
                        tables=leaking,
                    )
                )
        elif pr["expect"] == "same_as_known" and known is not None:
            diff = {
                t: [known.get(t), counts.get(t)] for t in filtered if known.get(t) != counts.get(t)
            }
            if diff:
                findings.append(
                    _finding(
                        "warn",
                        "case_sensitivity_mismatch",
                        name,
                        "the casing variant of the known identity sees different rows (known vs variant): "
                        f"{diff}",
                        probe=pr["label"],
                        tables=diff,
                    )
                )
    if known is not None and uses_identity:
        if all((known.get(t) or 0) == 0 for t in filtered):
            findings.append(
                _finding(
                    "warn",
                    "fail_closed_for_everyone",
                    name,
                    "the known identity sees no row in any RLS-filtered table (role filter matches nobody)",
                )
            )
        b2b = by_label.get("b2b_ext")
        if b2b is not None and any(
            (known.get(t) or 0) > 0 and (b2b.get(t) or 0) == 0 for t in filtered
        ):
            findings.append(
                _finding(
                    "info",
                    "b2b_identity_mismatch",
                    name,
                    "the B2B guest form of the known identity sees nothing (guest accounts need their "
                    "#EXT# UPN in the mapping)",
                    probe="b2b_ext",
                )
            )
    out["findings"] = findings
    return out


def run_rls_audit(
    roles: Sequence[Dict[str, Any]],
    run_query: RunQuery,
    *,
    role_names: Optional[Sequence[str]] = None,
    known_users: Sequence[str] = (),
    extra_identities: Sequence[str] = (),
    relationships: Optional[Sequence[Dict[str, Any]]] = None,
    custom_data: Optional[str] = None,
) -> Dict[str, Any]:
    """Audit every role (or *role_names*) under the probe identities. Never writes anything."""
    from core.dax.rls_rewriter import KNOWN_GAPS

    wanted = {str(r) for r in role_names} if role_names else None
    selected = [r for r in roles if wanted is None or r.get("name") in wanted]
    unknown = sorted(wanted - {str(r.get("name")) for r in roles}) if wanted else []
    probes = build_probes(known_users, extra_identities)
    results = [
        audit_role(r, probes, run_query, relationships=relationships, custom_data=custom_data)
        for r in selected
    ]
    findings = [f for r in results for f in r.get("findings", [])]
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
    res: Dict[str, Any] = {
        "success": True,
        "simulation": SIMULATION,
        "approximation": {
            "exact": False,
            "known_gaps": list(KNOWN_GAPS),
            "note": "Desktop rejects EffectiveUserName: role filters are applied by rewriting "
            "USERPRINCIPALNAME()/USERNAME()/USEROBJECTID()/CUSTOMDATA() to the probe identity.",
        },
        "probes": [{"label": p["label"], "identity": p["identity"]} for p in probes],
        "roles_audited": len(results),
        "roles": results,
        "findings": findings,
        "summary": counts,
        "fail_open": any(f["code"] in ("fail_open", "sees_everything") for f in findings),
        "ols_audit": "not_implemented",
    }
    if unknown:
        res["unknown_roles"] = unknown
    if not known_users:
        res["known_identity_missing"] = (
            "no as_user given: fail_closed_for_everyone / case / B2B checks need a known UPN"
        )
    return res


def audit_passes(
    result: Dict[str, Any], fail_on: str = "critical", allow: Sequence[str] = ()
) -> Tuple[bool, List[str]]:
    """Suite verdict: ``(ok, failure messages)`` for findings at or above *fail_on* (minus *allow* codes)."""
    if not result.get("success"):
        return False, [f"rls_audit failed: {str(result.get('error'))[:300]}"]
    threshold = _SEV_RANK.get(fail_on, _SEV_RANK["critical"])
    bad = [
        f
        for f in result.get("findings", [])
        if _SEV_RANK.get(f["severity"], 0) >= threshold and f["code"] not in set(allow)
    ]
    errors = [r["error"] for r in result.get("roles", []) if r.get("error")]
    msgs = [f"[{f['severity']}] {f['role']}: {f['code']} - {f['message']}" for f in bad]
    msgs += [f"role {r['role']}: {r['error']}" for r in result.get("roles", []) if r.get("error")]
    if result.get("unknown_roles"):
        msgs.append(f"unknown role(s): {result['unknown_roles']}")
    return (not bad and not errors and not result.get("unknown_roles")), msgs


# --------------------------------------------------------------------------- glue
def _split_users(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v).strip() for v in value if str(v or "").strip()]


def audit_from_connection(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run the audit against the connected model. ``args``: ``roles`` / ``role_name`` (subset),
    ``as_user`` (known UPN(s)), ``probe_identities`` (extra), ``custom_data``."""
    from core.infrastructure.connection_state import connection_state

    if not connection_state.is_connected():
        return {"success": False, "error": "Not connected", "error_type": "not_connected"}
    rls_mgr = getattr(connection_state, "rls_manager", None)
    qe = getattr(connection_state, "query_executor", None)
    if rls_mgr is None or qe is None:
        return {
            "success": False,
            "error": "RLS manager / query engine not available",
            "error_type": "manager_unavailable",
        }
    listed = rls_mgr.list_roles()
    if not listed.get("success", True):
        return listed

    def run_query(dax: str) -> Dict[str, Any]:
        return qe.validate_and_execute_dax(dax, top_n=0, bypass_cache=True)

    names = args.get("roles") or args.get("role_name")
    result = run_rls_audit(
        listed.get("roles", []),
        run_query,
        role_names=_split_users(names) if names else None,
        known_users=_split_users(args.get("as_user")),
        extra_identities=_split_users(args.get("probe_identities")),
        relationships=_fetch_relationships(),
        custom_data=args.get("custom_data"),
    )
    return result


def _fetch_relationships() -> Optional[List[Dict[str, Any]]]:
    """Normalised relationships of the connected model, None when unavailable (best effort)."""
    try:
        from core.analysis.info_normalize import normalize_relationship
        from core.dax.info_view import info_view

        res = info_view("relationships", top_n=5000)
        rows = res.get("rows") if isinstance(res, dict) else None
        if not res.get("success") or not rows:
            return None
        return [normalize_relationship(r) for r in rows]
    except Exception:  # noqa: BLE001 - relationship analysis is an extra, never a blocker
        return None
