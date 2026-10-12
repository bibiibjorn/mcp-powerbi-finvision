"""
PBI Desktop UI Automation

Used only when we need PBIDesktop itself to write the file (PBIX path, or
PBIP with live TOM changes still in memory). pywinauto is the preferred
backend because it can locate the Power BI window by PID and dispatch
keystrokes without stealing global focus for long.

Optional dependency: import is lazy so the rest of the autonomous package
works even on systems where pywinauto is not installed.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class UIAutomationUnavailable(RuntimeError):
    """Raised when pywinauto / pywin32 are not importable."""


def _own_writes_since(t0: float) -> set:
    """Normalised paths this server wrote since *t0* (excluded from save receipts)."""
    try:
        from core.autonomous.edit_ledger import recent_own_writes

        return recent_own_writes(t0)
    except Exception:  # noqa: BLE001
        return set()


def _try_import_pywinauto():
    try:
        from pywinauto import Application  # type: ignore

        return Application
    except ImportError as e:
        raise UIAutomationUnavailable(
            "pywinauto is not installed. Install with `pip install pywinauto`."
        ) from e


class PbiDesktopUIAutomation:
    """
    Save / close a PBIDesktop window via UI automation.

    Window discovery is by PID. Operations are best-effort: the methods return
    structured success/failure dicts rather than raising.
    """

    SAVE_TIMEOUT_DEFAULT = 30.0  # seconds to wait for the file write to settle

    def __init__(self, save_timeout: float = SAVE_TIMEOUT_DEFAULT) -> None:
        self.save_timeout = save_timeout
        # Set by _send_save_keystroke so callers can report WHICH mechanism
        # worked -- essential for telling a real UIA invoke apart from a legacy
        # fallback that historically never landed.
        self.last_save_mechanism = None
        # PBIP trees to watch for disk confirmation; populated by save().
        self._pbip_trees = []

    # ------------------------------------------------------------------
    # Disk verification (the only authoritative save signal)
    # ------------------------------------------------------------------
    _IGNORE_DIRS = {".git", ".pbi", "cache", ".vs", "__pycache__"}
    _IGNORE_EXT = {".tmp", ".lock", ".log"}

    @classmethod
    def _disk_signature(cls, trees):
        """(max_mtime, file_count, total_bytes) across the PBIP trees.

        Three components because a save can rewrite files in place (moves
        mtime), add/remove them (moves count), or change their size. Any one
        alone misses a real save shape.
        """
        if not trees:
            return None
        newest, count, total = 0.0, 0, 0
        for root in trees:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in cls._IGNORE_DIRS]
                for fn in filenames:
                    if os.path.splitext(fn)[1].lower() in cls._IGNORE_EXT:
                        continue
                    try:
                        st = os.stat(os.path.join(dirpath, fn))
                    except OSError:
                        continue  # mid-write churn; the next poll catches it
                    newest = max(newest, st.st_mtime)
                    count += 1
                    total += st.st_size
        return (newest, count, total)

    @staticmethod
    def _resolve_pbip_trees(pbip_path):
        """The .Report/ and .SemanticModel/ folders belonging to a .pbip.

        Resolved by FOLLOWING THE POINTERS, not by guessing from folder names:
            <name>.pbip            -> artifacts[].report.path
            <report>/definition.pbir -> datasetReference.byPath.path

        Name-guessing is wrong in exactly the case that matters here: several
        reports share ONE model whose folder stem differs from theirs (e.g.
        R0103-Reporting Alychlo.Report -> ../S01-SemanticModel.SemanticModel).
        Guessing found only the report tree, so a model-only save looked like
        "no change on disk"; the old listdir fallback over-corrected and would
        have watched every unrelated project in the folder.
        """
        if not pbip_path or not os.path.exists(pbip_path):
            return []
        import json as _json

        p = os.path.abspath(pbip_path)
        base = p if os.path.isdir(p) else os.path.dirname(p)
        out = []

        if os.path.isfile(p) and p.lower().endswith(".pbip"):
            try:
                with open(p, encoding="utf-8-sig") as fh:
                    doc = _json.load(fh)
                for art in doc.get("artifacts", []):
                    rel = (art.get("report") or {}).get("path")
                    if rel:
                        cand = os.path.normpath(os.path.join(base, rel))
                        if os.path.isdir(cand):
                            out.append(cand)
            except Exception:
                pass

        if not out:
            stem = (
                os.path.basename(p)
                if os.path.isdir(p)
                else os.path.splitext(os.path.basename(p))[0]
            )
            cand = os.path.join(base, stem + ".Report")
            if os.path.isdir(cand):
                out.append(cand)
            elif os.path.isdir(p) and p.lower().endswith((".report", ".semanticmodel", ".dataset")):
                out.append(p)
            elif os.path.isdir(p):
                # A plain project folder: watch it only when it holds exactly
                # ONE report (J-08 -- watching a folder with several projects
                # turns any unrelated write into a false "saved").
                try:
                    from core.pbip.pbir_model import resolve_pbip

                    reports = resolve_pbip(p).get("reports") or []
                except Exception:  # noqa: BLE001
                    reports = []
                if len(reports) == 1 and reports[0].get("report_dir"):
                    out.append(str(reports[0]["report_dir"]))
            else:
                # Model-only project: the .pbip names a .Report folder that does
                # not exist on disk (e.g. S01-SemanticModel.pbip), so the
                # pointer chain dead-ends. Watch the sibling .SemanticModel
                # sharing the stem -- otherwise we would silently fall back to
                # the window title, which is a null signal.
                sm = os.path.join(base, stem + ".SemanticModel")
                if os.path.isdir(sm):
                    out.append(sm)

        # Follow each report to its semantic model. A model-only save (the
        # common case after live-TOM edits) touches ONLY this tree.
        for report_dir in list(out):
            pbir = os.path.join(report_dir, "definition.pbir")
            if not os.path.isfile(pbir):
                continue
            try:
                with open(pbir, encoding="utf-8-sig") as fh:
                    ref = _json.load(fh)
                rel = ((ref.get("datasetReference") or {}).get("byPath") or {}).get("path")
                if rel:
                    model = os.path.normpath(os.path.join(report_dir, rel))
                    if os.path.isdir(model) and model not in out:
                        out.append(model)
            except Exception:
                continue

        return out

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    @classmethod
    def _disk_snapshot(cls, trees) -> Dict[str, tuple]:
        """``{normalised path: (mtime, size)}`` of every watched file."""
        snap: Dict[str, tuple] = {}
        for root in trees or []:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in cls._IGNORE_DIRS]
                for fn in filenames:
                    if os.path.splitext(fn)[1].lower() in cls._IGNORE_EXT:
                        continue
                    full = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue  # mid-write churn; the next poll catches it
                    snap[os.path.normcase(os.path.abspath(full))] = (st.st_mtime, st.st_size)
        return snap

    @staticmethod
    def _changed_since(
        baseline: Dict[str, tuple], current: Dict[str, tuple], t0: float, exclude: set
    ) -> List[str]:
        """Files created / rewritten at or after *t0* (or deleted) vs *baseline*.

        J-08: an unrelated write stamped BEFORE the save was triggered is not a
        receipt, and neither is one of this server's own writes (*exclude*).
        """
        out: List[str] = []
        for path, stat in current.items():
            if path.lower() in exclude:
                continue
            if baseline.get(path) == stat:
                continue
            if stat[0] >= t0 - 0.05:
                out.append(path)
        for path in baseline:
            if path not in current and path.lower() not in exclude:
                out.append(path)
        return out

    def save(
        self, pid: int, pbip_path: Optional[str] = None, required_tree: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Trigger Save in the PBIDesktop main window for the given PID and verify
        it ON DISK.

        ``required_tree``: ``"model"`` when the save must rewrite the
        ``.SemanticModel`` tree (live-TOM writes are pending) — a change in the
        report tree alone then does not count. The disk baseline and ``t0`` are
        recorded BEFORE the save is triggered (J-08): a fast save that lands
        before the first poll is still detected, and writes stamped before the
        trigger (or made by this server) are ignored.
        """
        self._pbip_trees = self._resolve_pbip_trees(pbip_path)
        try:
            Application = _try_import_pywinauto()
        except UIAutomationUnavailable as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": "ui_automation_unavailable",
            }

        try:
            app = Application(backend="uia").connect(process=pid, timeout=10)
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "error": f"Could not attach to PID {pid}: {e}",
                "error_type": "attach_failed",
            }

        windows = self._main_windows(app)
        if not windows:
            return {
                "success": False,
                "error": f"No top-level windows found for PID {pid}",
                "error_type": "window_not_found",
            }

        target = windows[0]
        title_before = self._safe_title(target)

        required_roots = [
            t
            for t in self._pbip_trees
            if required_tree == "model" and t.lower().endswith((".semanticmodel", ".dataset"))
        ]
        # Baseline BEFORE the trigger (J-08).
        t0 = time.time()
        baseline = self._disk_snapshot(self._pbip_trees) if self._pbip_trees else {}

        # Foreground is required only by the KEYSTROKE mechanisms. UIA
        # InvokePattern calls the bound command directly and needs no focus, so
        # a focus failure must not abort before we have tried it. On that path
        # click_input (moves the REAL mouse onto whatever covers the button) is
        # never used (J-17).
        has_focus = self._ensure_foreground(target)
        if not has_focus:
            ok, detail = self.invoke_save_control(target, allow_click=False)
            if ok:
                self.last_save_mechanism = detail + " (no foreground)"
            else:
                return {
                    "success": False,
                    "error": (
                        "Could not bring the Power BI window to the foreground, "
                        "and no Save control was reachable in the UIA tree; "
                        "refusing to send a global Ctrl+S keystroke. "
                        "If the workstation is LOCKED no automation can take "
                        "foreground -- unlock it, or save manually."
                    ),
                    "error_type": "focus_failed",
                    "invoke_attempt": detail,
                    "title_before": title_before,
                }

        try:
            # If the no-foreground path above already invoked Save, don't fire
            # a second time -- a duplicate invoke can open a Save-As dialog.
            already_invoked = (
                not has_focus
                and self.last_save_mechanism
                and "no foreground" in self.last_save_mechanism
            )
            if not already_invoked and not self._send_save_keystroke(target):
                return {
                    "success": False,
                    "error": (
                        "Could not deliver Save to the Power BI window: no Save control "
                        "was reachable, and Ctrl+S was not sent because the window lost "
                        "foreground or a modifier key was held."
                    ),
                    "error_type": "send_keys_failed",
                    "title_before": title_before,
                }
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "error": f"Send keys failed: {e}",
                "error_type": "send_keys_failed",
                "title_before": title_before,
            }

        # Wait for the save to land. DISK is authoritative; the title is not --
        # PBIP window titles often carry no asterisk at all. When project trees
        # are known we watch the bytes and only fall back to the title when
        # there is nothing to watch.
        deadline = time.time() + self.save_timeout
        last_title = title_before
        stable_since: Optional[float] = None
        last_snap: Optional[Dict[str, tuple]] = None
        changed: List[str] = []
        req_prefixes = [os.path.normcase(os.path.abspath(r)) for r in required_roots]

        first = True
        while time.time() < deadline:
            if not first:
                # 0.5s: a full PBIP tree scan measured ~260ms on a real report.
                # The stability window below provides the precision.
                time.sleep(0.5)
            first = False

            if self._pbip_trees:
                snap = self._disk_snapshot(self._pbip_trees)
                changed = self._changed_since(baseline, snap, t0, _own_writes_since(t0))
                relevant = [
                    c
                    for c in changed
                    if not req_prefixes or any(c.startswith(r) for r in req_prefixes)
                ]
                if snap != last_snap:
                    last_snap, stable_since = snap, time.time()
                    continue
                # PBIP rewrites many files: require a relevant post-trigger
                # change AND a stable tree, or a partial flush reads as success.
                if relevant and stable_since and time.time() - stable_since >= 1.5:
                    return {
                        "success": True,
                        "verified": "disk",
                        "mechanism": self.last_save_mechanism,
                        "changed_files": len(relevant),
                        "title_before": title_before,
                        "title_after": self._safe_title(target),
                        "elapsed_seconds": round(self.save_timeout - (deadline - time.time()), 2),
                    }
                continue

            try:
                last_title = self._safe_title(target)
            except Exception:
                continue
            if not self._is_dirty(last_title):
                return {
                    "success": True,
                    "verified": "title_only",
                    "mechanism": self.last_save_mechanism,
                    "warning": (
                        "No PBIP project tree could be resolved, so this was verified "
                        "only by window title -- a NULL signal on PBIP (titles often "
                        "carry no asterisk). Confirm on disk before trusting it."
                    ),
                    "title_before": title_before,
                    "title_after": last_title,
                    "elapsed_seconds": round(self.save_timeout - (deadline - time.time()), 2),
                }

        if self._pbip_trees:
            if changed and req_prefixes:
                why = (
                    "files changed after the trigger, but none in the .SemanticModel tree "
                    "that must receive the pending live-model writes"
                )
            elif changed:
                why = "the project trees kept changing (never stable for 1.5s)"
            else:
                why = "no file in the project trees changed after the save was triggered"
            error = (
                f"Save timeout after {self.save_timeout}s: {why}. The save may have opened "
                "a dialog (e.g. a file picker), or Desktop ignored the trigger."
            )
            mode = "disk"
        else:
            error = (
                "Save timeout: the window title still shows the unsaved marker after "
                f"{self.save_timeout}s. The save may have shown a dialog "
                "(e.g. file picker for new file)."
            )
            mode = "title"
        return {
            "success": False,
            "error": error,
            "error_type": "save_timeout",
            "verification_mode": mode,
            "watched_trees": list(self._pbip_trees),
            "title_before": title_before,
            "title_after": last_title,
        }

    # ------------------------------------------------------------------
    # Save keystroke delivery
    # ------------------------------------------------------------------
    # Win32 message + virtual-key constants (avoid importing win32con so this
    # module still imports on a machine without pywin32 — resolution is lazy).
    _WM_KEYDOWN = 0x0100
    _WM_KEYUP = 0x0101
    _VK_CONTROL = 0x11
    _VK_S = 0x53
    # KEYUP lParam sets the transition-state (bit 31) and previous-key (bit 30)
    # bits; 0xC0000001 = (1<<31)|(1<<30)|repeat-count 1.
    _KEYUP_LPARAM = 0xC0000001
    _KEYDOWN_LPARAM = 0x00000001

    @staticmethod
    def _resolve_hwnd(window):
        """Best-effort native window handle (HWND) for a pywinauto wrapper.

        pywinauto exposes the handle via ``.handle`` (attribute) on HwndWrapper
        and, on some versions, a ``.handle()`` method or ``.element_info.handle``.
        Returns an int HWND or None if it can't be determined.
        """
        for getter in (
            lambda w: getattr(w, "handle", None),
            lambda w: w.handle() if callable(getattr(w, "handle", None)) else None,
            lambda w: getattr(getattr(w, "element_info", None), "handle", None),
        ):
            try:
                h = getter(window)
            except Exception:
                h = None
            if callable(h):
                try:
                    h = h()
                except Exception:
                    h = None
            if isinstance(h, int) and h:
                return h
        return None

    @staticmethod
    def _post_key_to_hwnd(hwnd: int, msg: int, wparam: int, lparam: int) -> bool:
        """Post a single Win32 keyboard message to a specific window handle."""
        try:
            import win32api  # type: ignore
        except Exception:
            return False
        try:
            win32api.PostMessage(hwnd, msg, wparam, lparam)
            return True
        except Exception:
            return False

    # Automation ids / names the Save control has carried across PBI Desktop
    # builds. Ordered most-specific first. Matched case-insensitively against
    # both AutomationId and Name.
    _SAVE_CONTROL_HINTS = (
        "SaveButton",
        "FileSaveButton",
        "QatSaveButton",
        "Save",
        "Opslaan",  # nl-BE build
    )

    def invoke_save_control(self, window, allow_click: bool = True):
        """Ask the UIA tree to INVOKE the real Save control.

        This is the mechanism that actually works on WPF, and the one this class
        was missing: it connected with backend="uia" and then only ever sent
        keystrokes, never using the automation tree it had already paid for.

        Why keystrokes fail here: Power BI Desktop's shell is WPF hosting a
        WebView2. PostMessage(WM_KEYDOWN) delivers a synthetic message to a
        window's queue, but WPF resolves accelerators like Ctrl+S through its
        own input manager, which reads real input state -- it does not honour
        posted WM_KEYDOWN. And a Ctrl+S that reaches the WebView is swallowed
        there, because Save belongs to the native shell, not the document host.
        Invoking the control goes through neither path: it calls the command
        the button is bound to.

        Returns (True, detail) when a control was invoked, else (False, reason).
        """
        try:
            from pywinauto.findbestmatch import MatchError  # noqa: F401
        except Exception:
            pass

        # 1) InvokePattern on a control that looks like Save.
        for hint in self._SAVE_CONTROL_HINTS:
            for kwargs in ({"auto_id": hint}, {"title": hint}):
                try:
                    ctrl = window.child_window(**kwargs, control_type="Button")
                    if not ctrl.exists(timeout=0.4):
                        continue
                    try:
                        ctrl.invoke()  # InvokePattern -- no focus needed
                        return True, f"invoked Button {kwargs}"
                    except Exception:
                        if not allow_click:
                            # click_input moves the REAL mouse and clicks
                            # whatever covers the button (J-17).
                            continue
                        ctrl.click_input()  # bound-command fallback
                        return True, f"clicked Button {kwargs}"
                except Exception:
                    continue

        # 2) Some builds expose Save as a MenuItem / SplitButton instead.
        for ctype in ("MenuItem", "SplitButton", "Custom"):
            for hint in self._SAVE_CONTROL_HINTS:
                try:
                    ctrl = window.child_window(title=hint, control_type=ctype)
                    if not ctrl.exists(timeout=0.3):
                        continue
                    ctrl.invoke()
                    return True, f"invoked {ctype} {hint}"
                except Exception:
                    continue

        return False, "no Save control found in the UIA tree"

    @staticmethod
    def _send_input_ctrl_s() -> bool:
        """Ctrl+S as REAL input via SendInput.

        Unlike PostMessage, SendInput injects at the same level as physical
        typing, so WPF's input manager sees genuine key state and the
        accelerator fires. Cost: it is global -- it goes to whatever window has
        foreground -- so callers MUST confirm foreground first.
        """
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            return False

        VK_CONTROL, VK_S, KEYEVENTF_KEYUP = 0x11, 0x53, 0x0002

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        def key(vk, up=False):
            return INPUT(
                type=1,
                u=_U(
                    ki=KEYBDINPUT(
                        wVk=vk,
                        wScan=0,
                        dwFlags=KEYEVENTF_KEYUP if up else 0,
                        time=0,
                        dwExtraInfo=None,
                    )
                ),
            )

        seq = (key(VK_CONTROL), key(VK_S), key(VK_S, True), key(VK_CONTROL, True))
        arr = (INPUT * len(seq))(*seq)
        sent = ctypes.windll.user32.SendInput(len(seq), ctypes.byref(arr), ctypes.sizeof(INPUT))
        return sent == len(seq)

    def _send_save_keystroke(self, window) -> bool:
        """Trigger Save, strongest mechanism first.

        Ordering is by measured reliability on WPF, not convenience:
          1. UIA InvokePattern on the Save control  -- calls the bound command.
          2. SendInput Ctrl+S                       -- real input state.
          3. type_keys / PostMessage                -- legacy, kept as a floor.

        The previous implementation ran (3) only, which is why the measured
        success rate was 0/158.
        """
        ok, detail = self.invoke_save_control(window)
        if ok:
            self.last_save_mechanism = detail
            return True

        # SendInput is GLOBAL: re-verify, immediately before sending, that the
        # PBI window still has foreground and that no modifier is physically
        # held (a held Shift turns Ctrl+S into Ctrl+Shift+S = Save As) -- the
        # control search above can take seconds (J-17).
        if self._foreground_is(window) and not self._modifiers_held():
            if self._send_input_ctrl_s():
                self.last_save_mechanism = "SendInput ^s"
                return True

        hwnd = self._resolve_hwnd(window)
        if hwnd:
            posts = [
                (self._WM_KEYDOWN, self._VK_CONTROL, self._KEYDOWN_LPARAM),
                (self._WM_KEYDOWN, self._VK_S, self._KEYDOWN_LPARAM),
                (self._WM_KEYUP, self._VK_S, self._KEYUP_LPARAM),
                (self._WM_KEYUP, self._VK_CONTROL, self._KEYUP_LPARAM),
            ]
            if any(self._post_key_to_hwnd(hwnd, m, vk, lp) for m, vk, lp in posts):
                self.last_save_mechanism = "PostMessage ^s (legacy)"
                return True

        # No type_keys floor: it is a global keystroke that returned True
        # unconditionally (J-17). Report the failure instead.
        self.last_save_mechanism = None
        return False

    @classmethod
    def _foreground_is(cls, window) -> bool:
        """True when *window* is the foreground window RIGHT NOW (Win32 check)."""
        hwnd = cls._resolve_hwnd(window)
        if not hwnd:
            return False
        try:
            import ctypes

            fg = ctypes.windll.user32.GetForegroundWindow()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return False
        return bool(fg) and int(fg) == int(hwnd)

    @staticmethod
    def _modifiers_held() -> bool:
        """True when Shift / Ctrl / Alt / Win is physically down (GetAsyncKeyState)."""
        try:
            import ctypes

            gaks = ctypes.windll.user32.GetAsyncKeyState  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return True  # cannot tell -> do not send a global keystroke
        for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C):  # SHIFT, CONTROL, MENU, LWIN, RWIN
            try:
                if gaks(vk) & 0x8000:
                    return True
            except Exception:  # noqa: BLE001
                return True
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _ensure_foreground(window) -> bool:
        """Bring the window forward and confirm it is the active window.

        Returns True when the window can be trusted to receive keystrokes:
        set_focus() succeeded (and is_active(), when readable, agrees), or the
        window was already active despite a set_focus failure.
        """
        focused = True
        try:
            window.set_focus()
        except Exception:
            focused = False
        try:
            return bool(window.is_active())
        except Exception:
            # Can't verify activity — trust set_focus's outcome.
            return focused

    def _main_windows(self, app) -> List[Any]:
        """Return top-level visible windows likely to be the PBI main window.

        The app is already pinned to the PBIDesktop PID, so every window here
        belongs to Power BI. Recent Desktop builds title the main window with
        just the report name (e.g. ``DQ_R01`` / ``*DQ_R01``) — the older
        ``... - Power BI Desktop`` suffix is gone. So we no longer require the
        literal ``"Power BI"`` substring (that filter dropped the real main
        window and caused ``window_not_found``). Instead we rank candidates:

          priority 0 — title endswith 'Power BI Desktop' (legacy main window)
          priority 1 — any other visible/enabled window (modern main window;
                       also the fallback when the title is just the report name)

        Child dialogs (Save As, Query Editor) are still possible at priority 1,
        but the main window is virtually always the largest by area, so we
        tie-break on window area (largest first) before title length. Ctrl+S
        sent into a dialog is silently lost, hence the care in ordering.
        """
        candidates: List[tuple] = []  # (priority, -area, -title_len, window)
        try:
            for w in app.windows():
                try:
                    if not w.is_visible() and not self._restore_if_minimized(w):
                        continue
                    if not w.is_enabled():
                        continue
                    title = self._safe_title(w)
                    # priority 0 = legacy main-window pattern; 1 = everything else.
                    priority = 0 if title.endswith("Power BI Desktop") else 1
                    candidates.append((priority, -self._win_area(w), -len(title), w))
                except Exception:
                    continue
        except Exception as e:  # noqa: BLE001
            logger.debug("window enum failed: %s", e)
        # Lowest priority, then largest area, then longest title.
        candidates.sort(key=lambda t: (t[0], t[1], t[2]))
        return [w for _, _, _, w in candidates]

    @staticmethod
    def _restore_if_minimized(window) -> bool:
        """Restore a minimized window (UIA reports it off-screen = not visible).

        A minimized PBI window used to be filtered out and never restored, so
        save failed with ``window_not_found`` (J-17). Returns True when restored.
        """
        try:
            is_min = getattr(window, "is_minimized", None)
            if not callable(is_min) or not is_min():
                return False
        except Exception:  # noqa: BLE001
            return False
        try:
            restore = getattr(window, "restore", None)
            if callable(restore):
                restore()
                return True
        except Exception:  # noqa: BLE001
            pass
        hwnd = PbiDesktopUIAutomation._resolve_hwnd(window)
        if not hwnd:
            return False
        try:
            import ctypes

            ctypes.windll.user32.ShowWindow(hwnd, 9)  # type: ignore[attr-defined]  # SW_RESTORE
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _win_area(window) -> int:
        """Best-effort window area in px² (0 if the rect can't be read)."""
        try:
            r = window.rectangle()
            return max(0, (r.right - r.left)) * max(0, (r.bottom - r.top))
        except Exception:
            return 0

    @staticmethod
    def _safe_title(window) -> str:
        try:
            return window.window_text() or ""
        except Exception:
            return ""

    @staticmethod
    def _is_dirty(title: str) -> bool:
        """PBIDesktop appends an asterisk after the filename when dirty."""
        if not title:
            return False
        # Examples:
        #   "*MyReport - Power BI Desktop"
        #   "MyReport.pbix - Power BI Desktop"
        before_dash = title.split(" - ")[0].strip()
        return before_dash.startswith("*") or before_dash.endswith("*")

    @classmethod
    def availability(cls) -> Dict[str, Any]:
        """Quick capability check used by the handler."""
        try:
            _try_import_pywinauto()
            return {"available": True}
        except UIAutomationUnavailable as e:
            return {"available": False, "reason": str(e)}
