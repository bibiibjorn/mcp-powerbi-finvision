"""
Shared PBIP model cache.
Thread-safe LRU cache for parsed PBIP models and dependencies.
Used by pbip_operations_handler and hybrid_analysis_handler.

2026-09-22 review fixes (A-12 / A-13 / A-35 / G-16 / G-17 / G-06 / G-10):

* **Scope.** A path is resolved through ``core.pbip.pbir_model.resolve_pbip``
  (``.pbip -> .Report -> definition.pbir byPath -> .SemanticModel``). A
  ``.pbip`` / ``.Report`` path parses THAT report + its model only; a
  ``.SemanticModel`` / model-only ``.pbip`` the model + every sibling report
  bound to it; a folder its single model + bound reports (several models =
  an explicit ambiguity error). Thin reports bound by ``byPath`` -- the
  G01/G02 layout, where the old scanner found no model at all -- now work.
* **Signature, not max-mtime.** Staleness is a per-tree
  ``(file_count, dir_count, total_bytes, max_file_mtime_ns, max_dir_mtime_ns)``
  over the model ``definition/`` and each report ``definition/`` (os.scandir,
  pruning ``StaticResources``/``CustomVisuals``/``.pbi``), plus the pointer
  files (``.pbip``, ``definition.pbir``, ``definition.pbism``). Deletes,
  mtime-preserving copies and renames change it; a key can no longer go
  "backwards" onto an older entry (one entry per scope, compared by value).
* **Cheap hits.** The signature is memoised for ``signature_ttl_s`` (default
  5 s) so a hit inside the window costs ~no I/O; every write through
  ``core.utilities.pbip_utils`` (``save_json_file`` / ``save_json_minimal`` /
  ``update_json_file``) calls ``invalidate(path)`` via a write listener, so
  our own edits are never served stale. External edits (Desktop save, git)
  are picked up once the memo expires (<= TTL); call ``invalidate`` to force.
* **Parse-time edits.** The entry stores the PRE-parse signature and the memo
  is refreshed with a POST-parse walk: an edit during the parse makes the
  next call re-parse instead of caching a half-old parse under the new key.
* **In-flight de-dup.** Concurrent callers for the same scope + signature
  share ONE parse (per-key event) instead of each running the multi-second
  parse.

2026-10-03 speed pass (P-01 / P-06 / P-14):

* **Parallel walk.** ``_walk_trees`` scans the first directory levels of every
  tree serially, then walks the remaining sub-trees on a small shared pool
  (the cost is per-directory syscalls, which release the GIL). The result is
  value-identical to the serial ``_walk_tree``.
* **Watcher-validated memo.** ``server.pbip_watcher.DirWatcher``
  (``ReadDirectoryChangesW``) watches the scope's trees and pointer files; as
  long as it reports no change the memoised signature is reused with NO walk,
  however old (re-verified by a walk every ``WATCH_REVALIDATE_S``). If a
  watcher cannot be opened (non-Windows, no pywin32, UNC/network path) the TTL
  memo above is used unchanged. ``FINVISION_PBIP_WATCH=0`` disables watchers.
* **One signature service.** ``signature_for_trees`` serves any set of tree
  roots through the same walker + memo + watcher + write-listener
  invalidation; ``core.graph`` and the 09 debug handler use it instead of
  their own copies of the walk.
"""

import copy
import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.utilities.pbip_utils import normalize_path, register_write_listener

logger = logging.getLogger(__name__)

# Folders that never affect a parse (images, custom-visual packages, Desktop's
# local cache) -- pruned from the signature walk.
_SIGNATURE_PRUNE = frozenset({"StaticResources", "CustomVisuals", ".pbi", ".git"})

TreeSignature = Tuple[int, int, int, int, int]
ScopeSignature = Tuple[Any, ...]


@dataclass(frozen=True)
class PbipProject:
    """Typed wrapper for a parsed PBIP project.

    Provides attribute access instead of dict bracket notation.
    Returned by PbipModelCache.get_or_parse().
    """

    project_info: Dict[str, Any]
    model_data: Dict[str, Any]
    typed_model: Any  # TmdlModel from core.tmdl.models
    report_data: Optional[Dict[str, Any]]
    dependencies: Dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        """Dict-style access for backward compatibility."""
        return getattr(self, key)


@dataclass(frozen=True)
class PbipScope:
    """What one cache entry covers: a model plus the reports parsed with it."""

    key: str
    input_path: str
    model_dir: str
    report_dirs: Tuple[str, ...]
    trees: Tuple[str, ...]
    pointer_files: Tuple[str, ...]
    project_info: Dict[str, Any] = field(default_factory=dict, compare=False, hash=False)


@dataclass
class _Entry:
    scope: PbipScope
    signature: ScopeSignature
    project: Any


@dataclass
class _Flight:
    signature: ScopeSignature
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Optional[BaseException] = None


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _is_within(path_n: str, root_n: str) -> bool:
    """True when normalised *path_n* equals or is inside normalised *root_n*."""
    if path_n == root_n:
        return True
    root = root_n if root_n.endswith(os.sep) else root_n + os.sep
    return path_n.startswith(root)


_MISSING_TREE: TreeSignature = (-1, -1, -1, -1, -1)  # tree missing: distinct from "empty"

#: Parallel-walk tuning: the first levels are scanned serially until the
#: frontier holds this many directories, then sub-trees go to the pool.
_SPLIT_MIN_UNITS = 32
_SPLIT_MAX_DEPTH = 4
_SIG_WORKERS = 4
_sig_pool: Optional[ThreadPoolExecutor] = None
_sig_pool_lock = threading.Lock()


def _get_sig_pool() -> ThreadPoolExecutor:
    global _sig_pool
    if _sig_pool is None:
        with _sig_pool_lock:
            if _sig_pool is None:
                _sig_pool = ThreadPoolExecutor(
                    max_workers=_SIG_WORKERS, thread_name_prefix="pbip-sig"
                )
    return _sig_pool


def _scan_level(folder: str) -> Tuple[int, int, int, int, int, List[str]]:
    """One directory level: (files, dirs, bytes, max_file_mtime, max_dir_mtime, subdirs)."""
    files = dirs = total = max_f = max_d = 0
    subdirs: List[str] = []
    try:
        it = os.scandir(folder)
    except OSError:
        return (0, 0, 0, 0, 0, subdirs)
    with it:
        for e in it:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name in _SIGNATURE_PRUNE:
                        continue
                    st = e.stat(follow_symlinks=False)
                    dirs += 1
                    if st.st_mtime_ns > max_d:
                        max_d = st.st_mtime_ns
                    subdirs.append(e.path)
                else:
                    st = e.stat(follow_symlinks=False)
                    files += 1
                    total += st.st_size
                    if st.st_mtime_ns > max_f:
                        max_f = st.st_mtime_ns
            except OSError:
                continue
    return (files, dirs, total, max_f, max_d, subdirs)


def _walk_subtrees(folders: Sequence[str]) -> Tuple[int, int, int, int, int]:
    """Serial full walk below *folders* (the folders themselves are not counted)."""
    files = dirs = total = max_f = max_d = 0
    stack = list(folders)
    while stack:
        f, d, t, mf, md, subs = _scan_level(stack.pop())
        files += f
        dirs += d
        total += t
        if mf > max_f:
            max_f = mf
        if md > max_d:
            max_d = md
        stack.extend(subs)
    return (files, dirs, total, max_f, max_d)


def _walk_tree(root: str) -> TreeSignature:
    """(file_count, dir_count, total_bytes, max_file_mtime_ns, max_dir_mtime_ns).

    Serial reference walk; ``_walk_trees`` is the parallel equivalent.
    """
    try:
        max_d = os.stat(root).st_mtime_ns
    except OSError:
        return _MISSING_TREE
    files, dirs, total, max_f, sub_d = _walk_subtrees([root])
    return (files, dirs, total, max_f, max(max_d, sub_d))


def _walk_trees(trees: Sequence[str]) -> List[TreeSignature]:
    """``[_walk_tree(t) for t in trees]``, value-identical, on a small thread pool.

    Each tree's first levels are scanned here until ~``_SPLIT_MIN_UNITS``
    directories are known; the rest are walked in chunks on the shared pool
    (PBIR keeps one directory per visual: the cost is per-directory syscalls,
    which release the GIL, so 4 threads give ~2-2.5x).
    """
    out: List[Optional[TreeSignature]] = [None] * len(trees)
    acc: Dict[int, List[int]] = {}
    work: List[Tuple[int, List[str]]] = []
    for i, root in enumerate(trees):
        try:
            max_d = os.stat(root).st_mtime_ns
        except OSError:
            out[i] = _MISSING_TREE
            continue
        a = [0, 0, 0, 0, max_d]
        frontier = [root]
        depth = 0
        while (
            frontier
            and (depth == 0 or len(frontier) < _SPLIT_MIN_UNITS)
            and (depth < _SPLIT_MAX_DEPTH)
        ):
            nxt: List[str] = []
            for folder in frontier:
                f, d, t, mf, md, subs = _scan_level(folder)
                a[0] += f
                a[1] += d
                a[2] += t
                a[3] = max(a[3], mf)
                a[4] = max(a[4], md)
                nxt.extend(subs)
            frontier = nxt
            depth += 1
        acc[i] = a
        if frontier:
            step = max(4, -(-len(frontier) // (_SIG_WORKERS * 2)))
            for k in range(0, len(frontier), step):
                work.append((i, frontier[k : k + step]))
    if len(work) > 1:
        try:
            results = list(_get_sig_pool().map(lambda w: _walk_subtrees(w[1]), work))
        except RuntimeError:  # pool shut down (interpreter exit): walk inline
            results = [_walk_subtrees(w[1]) for w in work]
    else:
        results = [_walk_subtrees(w[1]) for w in work]
    for (i, _chunk), (f, d, t, mf, md) in zip(work, results):
        a = acc[i]
        a[0] += f
        a[1] += d
        a[2] += t
        a[3] = max(a[3], mf)
        a[4] = max(a[4], md)
    for i, a in acc.items():
        out[i] = (a[0], a[1], a[2], a[3], a[4])
    return [o if o is not None else _MISSING_TREE for o in out]


def _stat_file(path: str) -> Tuple[int, int]:
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return (-1, -1)


class PbipModelCache:
    """Thread-safe cache for parsed PBIP models and dependencies."""

    #: Default memo window for scope signatures (seconds). A hit inside the
    #: window does no filesystem walk; our own writes invalidate immediately.
    DEFAULT_SIGNATURE_TTL_S = 5.0

    #: With a healthy directory watcher the memo has no TTL; it is still
    #: re-verified by a real walk this often (belt and braces against a lost
    #: notification).
    WATCH_REVALIDATE_S = 300.0

    #: Seconds before a scope whose watcher could not be started is retried.
    WATCH_RETRY_S = 60.0

    def __init__(
        self,
        max_entries: int = 3,
        signature_ttl_s: Optional[float] = None,
        watch: Optional[bool] = None,
    ):
        self._lock = threading.RLock()
        self._cache: "OrderedDict[str, _Entry]" = OrderedDict()
        self._max_entries = max_entries
        self._signature_ttl_s = (
            signature_ttl_s if signature_ttl_s is not None else self.DEFAULT_SIGNATURE_TTL_S
        )
        # scope.key -> (signature, monotonic time computed, watcher version or None)
        self._sig_memo: Dict[str, Tuple[ScopeSignature, float, Optional[int]]] = {}
        self._scopes: Dict[str, PbipScope] = {}
        self._inflight: Dict[str, _Flight] = {}
        if watch is None:
            watch = os.environ.get("FINVISION_PBIP_WATCH", "1").strip().lower() not in (
                "0",
                "false",
                "off",
                "no",
            )
        self._watch_enabled = bool(watch)
        self._watch_lock = threading.Lock()
        self._watchers: "OrderedDict[str, Any]" = OrderedDict()
        self._watch_failed: Dict[str, float] = {}
        # (name, key) -> (tree signatures, result): see ``memoize_by_trees``
        self._result_memo: "OrderedDict[Tuple[str, Any], Tuple[Any, Any]]" = OrderedDict()
        self._result_memo_max = 16

    # ------------------------------------------------------------------ scope

    @staticmethod
    def _resolve_scope(path: str) -> PbipScope:
        """Resolve *path* to the model + reports one cache entry covers.

        Raises ValueError when there is no local semantic model or the choice
        is ambiguous (several models in a folder / reports bound to different
        models) -- never silently picks an arbitrary one (H-13).
        """
        from core.pbip.pbir_model import resolve_pbip
        from core.pbip.pbip_project_scanner import project_info_from_resolution

        res = resolve_pbip(path)
        target = res["target"]
        models = [m for m in res["models"] if m.get("model_dir")]
        if target == "none" or not models:
            detail = "; ".join(res["warnings"]) if res["warnings"] else ""
            raise ValueError(
                f"No PBIP semantic models found in: {path}" + (f" ({detail})" if detail else "")
            )
        if len(models) > 1:
            names = ", ".join(m["name"] for m in models)
            raise ValueError(
                f"Ambiguous PBIP path {path}: {len(models)} semantic models ({names}). "
                "Pass the .pbip / .Report / .SemanticModel path of the one to analyse."
            )
        model = dict(models[0])
        model_dir = model["model_dir"]
        bound = list(model["report_dirs"])
        warnings = list(res["warnings"])
        if target == "folder":
            # A report with NO binding at all (no definition.pbir, no same-stem
            # model) in a single-model folder is analysed with that model, as
            # the pre-2026-09 cache did. Reports bound elsewhere (byConnection,
            # a different/missing byPath) are never merged in.
            for r in res["reports"]:
                if r["dataset_reference"] == "none" and r["report_dir"] not in bound:
                    bound.append(r["report_dir"])
                    warnings.append(
                        f"{r['name']}: no dataset binding; analysed with {model['name']}"
                    )
        model["report_dirs"] = bound
        report_dirs = tuple(sorted(bound, key=_norm))
        trees: List[str] = []
        model_def = os.path.join(model_dir, "definition")
        trees.append(model_def if os.path.isdir(model_def) else model_dir)
        pointers: List[str] = [os.path.join(model_dir, "definition.pbism")]
        if not os.path.isdir(model_def):
            pointers.append(os.path.join(model_dir, "model.bim"))
        for rd in report_dirs:
            trees.append(os.path.join(rd, "definition"))
            pointers.append(os.path.join(rd, "definition.pbir"))
        if res.get("pbip_file"):
            pointers.append(res["pbip_file"])
        key = "model=" + _norm(model_dir) + "|reports=" + ";".join(_norm(r) for r in report_dirs)
        base_dir = path if os.path.isdir(path) else os.path.dirname(path)
        info = project_info_from_resolution(
            {
                "target": target,
                "warnings": warnings,
                "models": [model],
                "reports": [r for r in res["reports"] if r["report_dir"] in report_dirs],
            },
            base_dir,
        )
        return PbipScope(
            key=key,
            input_path=path,
            model_dir=model_dir,
            report_dirs=report_dirs,
            trees=tuple(trees),
            pointer_files=tuple(pointers),
            project_info=info,
        )

    @staticmethod
    def _compute_signature(scope: PbipScope) -> ScopeSignature:
        return tuple(_walk_trees(list(scope.trees))) + tuple(
            _stat_file(p) for p in scope.pointer_files
        )

    # ---------------------------------------------------------------- watcher

    @staticmethod
    def _watch_specs(scope: PbipScope) -> List[Any]:
        from server.pbip_watcher import WatchSpec

        specs: List[Any] = [WatchSpec(t, True, prune=_SIGNATURE_PRUNE) for t in scope.trees]
        flat: Dict[str, set] = {}
        for p in scope.pointer_files:
            flat.setdefault(os.path.dirname(p), set()).add(os.path.basename(p).lower())
        for folder, names in flat.items():
            specs.append(WatchSpec(folder, False, names=frozenset(names)))
        return specs

    def _ensure_watcher(self, scope: PbipScope) -> Optional[Any]:
        """A healthy watcher for *scope*, or ``None`` (the TTL memo then applies)."""
        if not self._watch_enabled or self._signature_ttl_s <= 0:
            return None
        key = scope.key
        with self._watch_lock:
            w = self._watchers.get(key)
            if w is not None and w.healthy:
                self._watchers.move_to_end(key)
                return w
            if w is not None:
                w.stop()
                del self._watchers[key]
            failed_at = self._watch_failed.get(key)
            if failed_at is not None and time.monotonic() - failed_at < self.WATCH_RETRY_S:
                return None
            try:
                from server.pbip_watcher import DirWatcher

                w = DirWatcher(self._watch_specs(scope))
                ok = w.start()
            except Exception as e:  # noqa: BLE001 -- the watcher is an optimisation only
                logger.debug(f"PBIP watcher unavailable for {key}: {e}")
                ok = False
            if not ok:
                self._watch_failed[key] = time.monotonic()
                return None
            self._watch_failed.pop(key, None)
            self._watchers[key] = w
            while len(self._watchers) > self._max_entries * 2 + 2:
                _k, old = self._watchers.popitem(last=False)
                old.stop()
            return w

    def close(self) -> None:
        """Stop every directory watcher (tests / shutdown)."""
        with self._watch_lock:
            for w in self._watchers.values():
                w.stop()
            self._watchers.clear()

    def _signature_ex(
        self, scope: PbipScope, max_age_s: Optional[float] = None
    ) -> Tuple[ScopeSignature, Optional[int]]:
        """``(signature, watcher version it was validated at)``.

        Reuses the memo when a healthy watcher saw no change since it was
        taken (any age up to ``WATCH_REVALIDATE_S``), or -- without a watcher
        -- while younger than *max_age_s* (default ``signature_ttl_s``).
        """
        ttl = self._signature_ttl_s if max_age_s is None else max_age_s
        now = time.monotonic()
        with self._lock:
            memo = self._sig_memo.get(scope.key)
            w = self._watchers.get(scope.key)
        if memo is not None:
            sig, ts, wver = memo
            if w is not None and wver is not None and w.healthy:
                if w.version == wver and now - ts < self.WATCH_REVALIDATE_S:
                    return sig, wver
            elif now - ts < ttl:
                return sig, wver
        # Start (or reuse) the watcher BEFORE walking: a change during the walk
        # moves ``version`` past ``wver`` and the memo is not trusted next time.
        w = self._ensure_watcher(scope)
        wver = w.version if w is not None else None
        sig = self._compute_signature(scope)
        with self._lock:
            self._sig_memo[scope.key] = (sig, time.monotonic(), wver)
            self._scopes[scope.key] = scope
            self._trim_memos()
        return sig, wver

    #: Distinct scopes / tree sets remembered (parse entries are bounded separately).
    _MEMO_MAX = 128

    def _trim_memos(self) -> None:
        """Bound ``_sig_memo`` / ``_scopes`` (caller holds ``_lock``): oldest first,
        never a scope that still has a cached parse."""
        if len(self._sig_memo) <= self._MEMO_MAX:
            return
        for key in sorted(self._sig_memo, key=lambda k: self._sig_memo[k][1]):
            if len(self._sig_memo) <= self._MEMO_MAX // 2:
                break
            if key in self._cache or key in self._inflight:
                continue
            self._sig_memo.pop(key, None)
            self._scopes.pop(key, None)

    def signature_for_trees(
        self,
        trees: Sequence[str],
        max_age_s: Optional[float] = None,
        fresh: bool = False,
    ) -> Tuple[TreeSignature, ...]:
        """Memoised per-tree signatures for any set of tree roots.

        The one signature service: ``core.graph`` (report trees) and the 09
        debug handler call this instead of walking themselves. Same walker,
        memo, watcher and write-listener invalidation as a project scope.
        *max_age_s* overrides the TTL for the no-watcher case (``0`` = only a
        watcher-validated memo is reused, otherwise walk). ``fresh=True``
        bypasses every memo and walks now (parallel); the result is not stored.
        """
        roots = tuple(sorted({os.path.abspath(str(t)) for t in trees}, key=_norm))
        if not roots:
            return ()
        if fresh:
            return tuple(_walk_trees(list(roots)))
        key = "trees=" + ";".join(_norm(t) for t in roots)
        scope = PbipScope(
            key=key,
            input_path=roots[0],
            model_dir=roots[0],
            report_dirs=roots[1:],
            trees=roots,
            pointer_files=(),
        )
        return self._signature_ex(scope, max_age_s)[0]  # type: ignore[return-value]

    def memoize_by_trees(self, name: str, key: Any, trees: Sequence[str], factory: Any) -> Any:
        """``factory()`` memoised until one of *trees* changes (P-10).

        For audit-style ops that re-read the same files on every call
        (``scan_broken_refs``, ``07_Report_Operations info``, ``validate_model``).
        The result is reused while ``signature_for_trees(trees)`` -- the same
        watcher / TTL memo the parse cache uses, dropped by every PBIR write
        through ``pbip_utils`` -- is unchanged. The signature is taken BEFORE the
        factory runs, so an edit during the run just makes the next call recompute.
        Results are deep-copied in and out (callers mutate them). An exception is
        never memoised.
        """
        sig = self.signature_for_trees(list(trees))
        memo_key = (name, key)
        with self._lock:
            hit = self._result_memo.get(memo_key)
            if hit is not None and hit[0] == sig:
                self._result_memo.move_to_end(memo_key)
                return copy.deepcopy(hit[1])
        value = factory()
        with self._lock:
            self._result_memo[memo_key] = (sig, copy.deepcopy(value))
            self._result_memo.move_to_end(memo_key)
            while len(self._result_memo) > self._result_memo_max:
                self._result_memo.popitem(last=False)
        return value

    # ------------------------------------------------------------- public API

    def get_or_parse(self, pbip_path: str) -> PbipProject:
        """Get the cached project for *pbip_path* or parse it fresh.

        *pbip_path* may be a ``.pbip`` file, a ``.Report`` / ``.SemanticModel``
        / ``definition`` folder or a project folder (see the module docstring
        for how each is scoped). WSL ``/mnt/c/...`` and Git-Bash ``/c/...``
        paths are normalised first (T91 / G-17).

        Pass the caller's ORIGINAL path to get report scoping: the legacy
        ``normalize_pbip_path`` maps a ``.pbip`` to its parent folder (and so to
        every report bound to the model).
        """
        resolved = _canonical_input(pbip_path)
        scope = self._resolve_scope(resolved)
        sig, wver = self._signature_ex(scope)

        with self._lock:
            entry = self._cache.get(scope.key)
            if entry is not None and entry.signature == sig:
                logger.debug(f"PBIP cache hit for {resolved}")
                self._cache.move_to_end(scope.key)
                return entry.project
            flight = self._inflight.get(scope.key)
            owner = flight is None or flight.signature != sig
            if owner:
                flight = _Flight(signature=sig)
                self._inflight[scope.key] = flight
        assert flight is not None

        if not owner:
            # A-35: another caller is already parsing this exact state -- share it.
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            return flight.result

        try:
            result = self._parse_pbip(scope)
        except BaseException as e:
            flight.error = e
            with self._lock:
                if self._inflight.get(scope.key) is flight:
                    del self._inflight[scope.key]
            flight.event.set()
            raise

        # Post-parse signature: if anything changed DURING the parse, the memo
        # now differs from the stored (pre-parse) signature and the next call
        # re-parses (G-17: never cache a half-old parse under the new state).
        # With a healthy watcher that saw nothing since ``sig`` was validated,
        # the post-parse state IS ``sig`` (no second walk).
        w = self._watchers.get(scope.key)
        if w is not None and wver is not None and w.healthy and w.version == wver:
            post_sig, post_wver = sig, wver
        else:
            w = self._ensure_watcher(scope)
            post_wver = w.version if w is not None else None
            post_sig = self._compute_signature(scope)
        with self._lock:
            self._cache[scope.key] = _Entry(scope=scope, signature=sig, project=result)
            self._cache.move_to_end(scope.key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
            self._sig_memo[scope.key] = (post_sig, time.monotonic(), post_wver)
            self._scopes[scope.key] = scope
            if self._inflight.get(scope.key) is flight:
                del self._inflight[scope.key]
        flight.result = result
        flight.event.set()
        if post_sig != sig:
            logger.info(f"PBIP changed while parsing {resolved}; next call will re-parse")
        return result

    def invalidate(self, pbip_path: str) -> None:
        """Drop cached entries (and signature memos) affected by *pbip_path*.

        *pbip_path* may be an entry's input path, a file or folder INSIDE a
        cached model/report tree (what the ``pbip_utils`` write listener
        passes after every save), or a parent folder (evicts every entry under
        it). Matching is on path boundaries, so ``C:\\Repo\\R01`` never evicts
        ``C:\\Repo\\R010`` (G-17). WSL / Git-Bash paths are normalised first.
        """
        target = _norm(_canonical_input(pbip_path))
        with self._lock:
            for key in list(self._scopes):
                scope = self._scopes[key]
                if self._scope_affected(scope, target):
                    self._sig_memo.pop(key, None)
                    self._cache.pop(key, None)
                    self._scopes.pop(key, None)

    @staticmethod
    def _scope_affected(scope: PbipScope, target_n: str) -> bool:
        roots = (
            [scope.input_path, scope.model_dir]
            + list(scope.report_dirs)
            + list(scope.trees)
            + list(scope.pointer_files)
        )
        for root in roots:
            if not root:
                continue
            root_n = _norm(root)
            if _is_within(target_n, root_n) or _is_within(root_n, target_n):
                return True
        return False

    # Legacy helper kept for callers/tests that want a single "freshness"
    # number. Uses the same scoped os.scandir walk as the signature.
    @classmethod
    def _get_max_mtime(cls, path: str) -> float:
        """Most recent mtime (seconds) across the scope's model/report trees."""
        try:
            scope = cls._resolve_scope(_canonical_input(path))
            roots = list(scope.trees)
            pointers = list(scope.pointer_files)
        except ValueError:
            roots, pointers = [path], []
        max_ns = 0
        for r in roots:
            sig = _walk_tree(r)
            max_ns = max(max_ns, sig[3], sig[4])
        for p in pointers:
            max_ns = max(max_ns, _stat_file(p)[1])
        return max_ns / 1e9

    @staticmethod
    def _parse_pbip(scope: "PbipScope") -> PbipProject:
        """Parse the model + reports of *scope* into a PbipProject.

        Several reports are merged into a single combined report_data and
        dependencies result. Pages from each report are prefixed with the
        report name for disambiguation. Unused columns/measures are
        intersected across all reports.
        """
        from core.pbip.pbip_model_analyzer import TmdlModelAnalyzer
        from core.pbip.pbip_dependency_engine import PbipDependencyEngine

        project_info = scope.project_info
        model_folder = scope.model_dir

        analyzer = TmdlModelAnalyzer()
        typed_model = analyzer.analyze_model_typed(model_folder)
        model_data = typed_model.to_dict()
        model_data["model_folder"] = model_folder

        # Parse the scope's reports
        parsed_reports: List[Dict[str, Any]] = []
        failed_reports: List[Dict[str, str]] = []  # WP8 / G-08: never drop silently
        for report_folder in scope.report_dirs:
            try:
                from core.pbip.pbip_report_analyzer import PbirReportAnalyzer

                rd = PbirReportAnalyzer().analyze_report(report_folder)
                rd["_report_name"] = os.path.basename(report_folder)
                parsed_reports.append(rd)
            except Exception as e:
                logger.warning(f"Failed to parse report {report_folder}: {e}")
                failed_reports.append({"report": os.path.basename(report_folder), "error": str(e)})
        if failed_reports and isinstance(project_info, dict):
            project_info.setdefault("warnings", []).extend(
                f"report {f['report']} failed to parse: {f['error']}" for f in failed_reports
            )

        # Merge reports and build dependencies
        if not parsed_reports:
            # No reports — model-only analysis
            dep_engine = PbipDependencyEngine(model_data, None)
            dependencies = dep_engine.analyze_all_dependencies()
            _mark_failed_reports(dependencies, failed_reports)
            return PbipProject(
                project_info=project_info,
                model_data=model_data,
                typed_model=typed_model,
                report_data=None,
                dependencies=dependencies,
            )

        if len(parsed_reports) == 1:
            # Single report — original behavior, no prefixing needed
            report_data = parsed_reports[0]
            dep_engine = PbipDependencyEngine(model_data, report_data)
            dependencies = dep_engine.analyze_all_dependencies()
        else:
            # Multi-report: run per-report analysis, then merge
            report_data, dependencies = _merge_multi_report(model_data, parsed_reports)
        _mark_failed_reports(dependencies, failed_reports)

        return PbipProject(
            project_info=project_info,
            model_data=model_data,
            typed_model=typed_model,
            report_data=report_data,
            dependencies=dependencies,
        )


def _merge_multi_report(
    model_data: Dict[str, Any],
    parsed_reports: List[Dict[str, Any]],
) -> tuple:
    """
    Merge multiple parsed reports into combined report_data and dependencies.

    Strategy:
    - Pages from each report are prefixed with report name for disambiguation
    - Report-level filters are merged from all reports
    - PbipDependencyEngine runs per-report; unused lists are intersected
    - Visual/page dependencies are merged from all per-report runs

    Args:
        model_data: Shared semantic model data
        parsed_reports: List of parsed report dicts (each from PbirReportAnalyzer)

    Returns:
        Tuple of (merged_report_data, merged_dependencies)
    """
    from core.pbip.pbip_dependency_engine import PbipDependencyEngine

    # Build merged report_data with pages from all reports
    merged_pages: List[Dict[str, Any]] = []
    merged_report_filters: List[Dict[str, Any]] = []
    report_names: List[str] = []

    for rd in parsed_reports:
        report_name = rd.get("_report_name", "")
        report_names.append(report_name)

        # Prefix each page's display_name with report name
        for page in rd.get("pages", []):
            prefixed_page = dict(page)
            original_name = page.get("display_name", page.get("id", ""))
            prefixed_page["display_name"] = f"[{report_name}] {original_name}"
            merged_pages.append(prefixed_page)

        # Collect report-level filters
        report_info = rd.get("report", {})
        for filt in report_info.get("filters", []):
            merged_report_filters.append(filt)

    merged_report_data: Dict[str, Any] = {
        "report": {
            "name": ", ".join(report_names),
            "filters": merged_report_filters,
        },
        "pages": merged_pages,
        "bookmarks": [],
        "_report_names": report_names,
    }

    # Run dependency engine per report and collect results
    all_unused_cols: List[set] = []
    all_unused_measures: List[set] = []
    all_unused_funcs: List[set] = []
    merged_deps: Dict[str, Any] = {}

    for rd in parsed_reports:
        engine = PbipDependencyEngine(model_data, rd)
        deps = engine.analyze_all_dependencies()

        all_unused_cols.append(set(deps.get("unused_columns", [])))
        all_unused_measures.append(set(deps.get("unused_measures", [])))

        # Merge additive dependency maps
        if not merged_deps:
            merged_deps = deps
        else:
            report_name = rd.get("_report_name", "")

            # Merge visual_dependencies (prefix keys with report name)
            for vk, vv in deps.get("visual_dependencies", {}).items():
                merged_deps.setdefault("visual_dependencies", {})[f"[{report_name}] {vk}"] = vv

            # Merge page_dependencies (prefix keys with report name)
            for pk, pv in deps.get("page_dependencies", {}).items():
                merged_deps.setdefault("page_dependencies", {})[f"[{report_name}] {pk}"] = pv

            # Merge filter_pane_data
            _merge_filter_pane(
                merged_deps.setdefault("filter_pane_data", {}),
                deps.get("filter_pane_data", {}),
                report_name,
            )

            # Merge column_to_measure (union of measure lists per column; set
            # membership — the list scan was O(n^2), review G-25)
            existing = merged_deps.setdefault("column_to_measure", {})
            for ck, measures in deps.get("column_to_measure", {}).items():
                cur = existing.setdefault(ck, [])
                seen = set(cur)
                cur.extend(m for m in measures if m not in seen and not seen.add(m))

            # Union of usage reasons (WP8 / G-28)
            reasons = merged_deps.setdefault("usage_reasons", {})
            for k, lst in (deps.get("usage_reasons") or {}).items():
                cur = reasons.setdefault(k, [])
                for r in lst:
                    if r not in cur and len(cur) < 20:
                        cur.append(r)
        all_unused_funcs.append(set(deps.get("unused_functions", []) or []))

    # Intersect unused: only unused if unused in ALL report analyses
    if all_unused_cols:
        merged_deps["unused_columns"] = sorted(
            all_unused_cols[0].intersection(*all_unused_cols[1:])
        )
    if all_unused_measures:
        merged_deps["unused_measures"] = sorted(
            all_unused_measures[0].intersection(*all_unused_measures[1:])
        )

    if all_unused_funcs:
        merged_deps["unused_functions"] = sorted(
            all_unused_funcs[0].intersection(*all_unused_funcs[1:])
        )
    cov = merged_deps.setdefault("coverage", {})
    cov["reports_analyzed"] = report_names

    # Update summary counts
    summary = merged_deps.get("summary", {})
    summary["unused_columns"] = len(merged_deps.get("unused_columns", []))
    summary["unused_measures"] = len(merged_deps.get("unused_measures", []))
    summary["total_pages"] = len(merged_pages)
    summary["total_visuals"] = sum(len(p.get("visuals", [])) for p in merged_pages)
    summary["reports_analyzed"] = report_names
    merged_deps["summary"] = summary

    return merged_report_data, merged_deps


def _mark_failed_reports(dependencies: Dict[str, Any], failed: List[Dict[str, str]]) -> None:
    """Record reports that failed to parse; unused lists are then partial."""
    cov = dependencies.setdefault("coverage", {})
    cov["reports_failed"] = list(failed)
    if failed:
        cov["complete"] = False


def _merge_filter_pane(
    target: Dict[str, Any],
    source: Dict[str, Any],
    report_name: str,
) -> None:
    """Merge filter pane data from a source report into target, prefixing keys."""
    # Report filters — append
    target.setdefault("report_filters", []).extend(source.get("report_filters", []))

    # Page filters — prefix page keys
    for pk, pv in source.get("page_filters", {}).items():
        target.setdefault("page_filters", {})[f"[{report_name}] {pk}"] = pv

    # Visual filters — prefix visual keys
    for vk, vv in source.get("visual_filters", {}).items():
        target.setdefault("visual_filters", {})[f"[{report_name}] {vk}"] = vv

    # Summary — add counts
    src_summary = source.get("summary", {})
    tgt_summary = target.setdefault(
        "summary",
        {
            "total_report_filters": 0,
            "total_page_filters": 0,
            "total_visual_filters": 0,
            "pages_with_filters": 0,
            "visuals_with_filters": 0,
        },
    )
    for key in tgt_summary:
        tgt_summary[key] = tgt_summary.get(key, 0) + src_summary.get(key, 0)


def _canonical_input(raw_path: str) -> str:
    """WSL / Git-Bash / Windows path -> absolute resolved path (no PBIP mapping)."""
    normalized = normalize_path(str(raw_path))
    try:
        return str(Path(normalized).resolve())
    except OSError:  # pragma: no cover
        return str(Path(normalized).absolute())


def normalize_pbip_path(raw_path: str) -> str:
    """Normalize PBIP path (handle WSL/Unix paths on Windows).

    Legacy mapping kept for existing callers: a ``.pbip`` file maps to its
    parent folder and a ``.Report`` folder to its same-stem ``.SemanticModel``
    sibling (or the parent folder). ``get_or_parse`` accepts the ORIGINAL path
    too -- pass that to scope an analysis to one report.

    G-17: Git-Bash ``/c/Users/...`` now maps to ``C:\\Users\\...`` (it used to
    become ``C:\\c\\Users\\...``); WSL ``/mnt/c/...`` as before.
    """
    normalized = normalize_path(str(raw_path))

    p = Path(normalized)
    if p.is_file() and p.suffix == ".pbip":
        return str(p.parent.resolve())

    # Auto-fix: if path points to a .Report folder, resolve to .SemanticModel or parent
    resolved = p.resolve()
    if resolved.is_dir() and resolved.name.endswith(".Report"):
        sm_sibling = resolved.parent / resolved.name.replace(".Report", ".SemanticModel")
        if sm_sibling.is_dir():
            return str(sm_sibling)
        return str(resolved.parent)

    return str(resolved)


# Global shared cache instance
pbip_cache = PbipModelCache()

# A-12 / G-17: every PBIR write through core.utilities.pbip_utils invalidates
# the affected cache entries immediately (core/ cannot import server/, so the
# hook is registered from this side).
register_write_listener(pbip_cache.invalidate)
