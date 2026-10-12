"""Unit tests for PbiDesktopUIAutomation window selection + dirty detection.

Regression: modern Power BI Desktop titles the main window with just the
report name (e.g. 'DQ_R01' / '*DQ_R01'), dropping the legacy
' - Power BI Desktop' suffix. The old _main_windows() required the literal
'Power BI' substring and returned nothing -> save failed with
'window_not_found'. These tests pin the corrected, suffix-agnostic behavior.
No pywinauto / live Desktop needed — windows are faked.
"""

from core.autonomous.ui_automation import PbiDesktopUIAutomation


class _Rect:
    def __init__(self, w, h):
        self.left, self.top, self.right, self.bottom = 0, 0, w, h


class _FakeWin:
    def __init__(self, title, w=1600, h=900, visible=True, enabled=True):
        self._title = title
        self._rect = _Rect(w, h)
        self._visible = visible
        self._enabled = enabled

    def is_visible(self):
        return self._visible

    def is_enabled(self):
        return self._enabled

    def window_text(self):
        return self._title

    def rectangle(self):
        return self._rect


class _FakeApp:
    def __init__(self, wins):
        self._wins = wins

    def windows(self):
        return self._wins


def _uia():
    return PbiDesktopUIAutomation()


def test_modern_title_without_suffix_is_found():
    # The exact case that broke: title is just the report name.
    app = _FakeApp([_FakeWin("DQ_R01")])
    wins = _uia()._main_windows(app)
    assert len(wins) == 1
    assert wins[0].window_text() == "DQ_R01"


def test_legacy_suffix_window_ranks_first():
    legacy = _FakeWin("Report - Power BI Desktop", w=1600, h=900)
    other = _FakeWin("DQ_R01", w=1600, h=900)
    app = _FakeApp([other, legacy])
    wins = _uia()._main_windows(app)
    assert wins[0].window_text() == "Report - Power BI Desktop"


def test_largest_window_wins_among_non_legacy():
    big = _FakeWin("DQ_R01", w=1920, h=1080)
    small_dialog = _FakeWin("Save As", w=400, h=200)
    app = _FakeApp([small_dialog, big])
    wins = _uia()._main_windows(app)
    assert wins[0].window_text() == "DQ_R01"


def test_invisible_or_disabled_windows_skipped():
    hidden = _FakeWin("DQ_R01", visible=False)
    disabled = _FakeWin("DQ_R01", enabled=False)
    app = _FakeApp([hidden, disabled])
    assert _uia()._main_windows(app) == []


# ---------------------------------------------------------------------------
# Foreground safety — Ctrl+S is a GLOBAL keystroke: if the PBI window can't be
# confirmed foreground, sending it could save a file in a different app.
# ---------------------------------------------------------------------------
class _FocusWin(_FakeWin):
    def __init__(self, title="DQ_R01", focus_raises=False, active=True, active_raises=False):
        super().__init__(title)
        self._focus_raises = focus_raises
        self._active = active
        self._active_raises = active_raises
        self.typed = []

    def set_focus(self):
        if self._focus_raises:
            raise RuntimeError("focus denied")

    def is_active(self):
        if self._active_raises:
            raise RuntimeError("no rect")
        return self._active

    def type_keys(self, keys, **kw):
        self.typed.append(keys)


def test_ensure_foreground_true_when_focus_ok():
    assert _uia()._ensure_foreground(_FocusWin()) is True


def test_ensure_foreground_trusts_set_focus_when_active_unreadable():
    win = _FocusWin(active_raises=True)
    assert _uia()._ensure_foreground(win) is True


def test_ensure_foreground_false_when_focus_fails_and_not_active():
    win = _FocusWin(focus_raises=True, active=False)
    assert _uia()._ensure_foreground(win) is False


def test_ensure_foreground_true_when_already_active_despite_focus_failure():
    win = _FocusWin(focus_raises=True, active=True)
    assert _uia()._ensure_foreground(win) is True


def test_save_aborts_with_focus_failed_before_sending_keys(monkeypatch):
    import core.autonomous.ui_automation as uia_mod

    win = _FocusWin(focus_raises=True, active=False)

    class _App:
        def windows(self):
            return [win]

    class _Application:
        def __init__(self, backend=None):
            pass

        def connect(self, process=None, timeout=None):
            return _App()

    monkeypatch.setattr(uia_mod, "_try_import_pywinauto", lambda: _Application)
    result = _uia().save(pid=7)
    assert result["success"] is False
    assert result["error_type"] == "focus_failed"
    assert win.typed == []  # the keystroke was never sent


# ---------------------------------------------------------------------------
# Handle-targeted save keystroke — Ctrl+S must be delivered to the specific
# window HWND (PostMessage), NOT sent as a global SendInput keystroke that
# could land on a different window if focus is stolen mid-send.
# ---------------------------------------------------------------------------
class _HwndWin(_FakeWin):
    def __init__(self, title="DQ_R01", hwnd=12345):
        super().__init__(title)
        self._hwnd = hwnd
        self.type_keys_called = False

    def set_focus(self):
        pass

    def is_active(self):
        return True

    def handle(self):  # some pywinauto wrappers expose .handle()
        return self._hwnd

    @property
    def handle_attr(self):
        return self._hwnd

    def type_keys(self, keys, **kw):
        self.type_keys_called = True


def test_send_save_prefers_uia_invoke_over_keystrokes(monkeypatch):
    """UIA InvokePattern must be tried FIRST.

    Power BI Desktop's shell is WPF hosting a WebView2. WPF resolves Ctrl+S
    through its own input manager, which reads real key state and ignores a
    posted WM_KEYDOWN; a Ctrl+S reaching the WebView is swallowed there because
    Save belongs to the native shell. Keystroke-only delivery measured 0
    successes in 158 attempts, so invoking the bound command must come first.
    """
    posted = []
    win = _HwndWin(hwnd=99001)
    uia = _uia()
    monkeypatch.setattr(uia, "_post_key_to_hwnd", lambda *a: posted.append(a) or True)
    monkeypatch.setattr(
        uia, "invoke_save_control", lambda w: (True, "invoked Button {'auto_id': 'SaveButton'}")
    )

    assert uia._send_save_keystroke(win) is True
    assert "invoked" in uia.last_save_mechanism
    assert not posted, "must not fall back to keystrokes once invoke succeeded"
    assert win.type_keys_called is False


def test_send_save_falls_back_to_sendinput_then_postmessage(monkeypatch):
    """With no Save control, use SendInput (real input) before PostMessage."""
    posted = []
    win = _HwndWin(hwnd=99001)
    uia = _uia()
    monkeypatch.setattr(uia, "invoke_save_control", lambda w: (False, "not found"))
    monkeypatch.setattr(uia, "_post_key_to_hwnd", lambda *a: posted.append(a) or True)
    monkeypatch.setattr(uia, "_send_input_ctrl_s", staticmethod(lambda: True))
    # J-17: SendInput is only sent after a fresh foreground + modifier check.
    monkeypatch.setattr(uia, "_foreground_is", lambda w: True)
    monkeypatch.setattr(uia, "_modifiers_held", staticmethod(lambda: False))

    assert uia._send_save_keystroke(win) is True
    assert uia.last_save_mechanism == "SendInput ^s"
    assert not posted, "SendInput must be preferred over PostMessage"


def test_send_save_keystroke_has_no_type_keys_floor(monkeypatch):
    """J-17: no control, no SendInput, no HWND -> FAIL.

    The old last-resort ``type_keys("^s", set_foreground=False)`` was a GLOBAL
    keystroke that returned True unconditionally (this test used to pin it).
    """

    class _NoHwndWin(_FakeWin):
        def __init__(self):
            super().__init__("DQ_R01")
            self.type_keys_called = False

        def type_keys(self, keys, **kw):
            self.type_keys_called = True

    win = _NoHwndWin()
    uia = _uia()
    monkeypatch.setattr(uia, "invoke_save_control", lambda w: (False, "not found"))
    monkeypatch.setattr(uia, "_send_input_ctrl_s", staticmethod(lambda: False))
    monkeypatch.setattr(uia, "_resolve_hwnd", lambda w: None)

    assert uia._send_save_keystroke(win) is False
    assert win.type_keys_called is False


def test_resolve_pbip_trees_follows_pointers(tmp_path):
    """Resolve report AND model by following pointers, not by name-guessing.

    Several reports share one model whose folder stem differs from theirs, so
    stem-guessing silently misses the model tree -- and a model-only save then
    looks like "no change on disk".
    """
    import json

    rep = tmp_path / "R0103-Alychlo.Report"
    rep.mkdir()
    model = tmp_path / "S01-SemanticModel.SemanticModel"
    model.mkdir()
    (rep / "definition.pbir").write_text(
        json.dumps({"datasetReference": {"byPath": {"path": "../S01-SemanticModel.SemanticModel"}}})
    )
    pbip = tmp_path / "R0103-Alychlo.pbip"
    pbip.write_text(json.dumps({"artifacts": [{"report": {"path": "R0103-Alychlo.Report"}}]}))

    trees = _uia()._resolve_pbip_trees(str(pbip))
    assert str(rep) in trees
    assert str(model) in trees, "model tree must be reached via definition.pbir"


def test_disk_signature_detects_content_change(tmp_path):
    """Signature must move when bytes change, since disk is the only receipt."""
    d = tmp_path / "X.Report"
    d.mkdir()
    f = d / "visual.json"
    f.write_text("a")
    uia = _uia()
    before = uia._disk_signature([str(d)])
    f.write_text("a much longer body")
    assert uia._disk_signature([str(d)]) != before


def test_is_dirty_handles_suffixless_title():
    uia = _uia()
    assert uia._is_dirty("*DQ_R01") is True
    assert uia._is_dirty("DQ_R01") is False
    # Legacy form still works.
    assert uia._is_dirty("*Report - Power BI Desktop") is True
    assert uia._is_dirty("Report - Power BI Desktop") is False
