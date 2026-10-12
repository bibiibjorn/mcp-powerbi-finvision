"""R8: nothing may touch the real system while this feature is built or tested (phase 3b).

A real backend (the Windows certificate store, the HKCU Internet Settings, WinINet) cannot be built unless
``FINVISION_MASKING_REAL_SYSTEM=1``, and never under pytest. The variable is deleted for every test by
``tests/conftest.py``. A static scan pins WHERE the real calls live, so a new one cannot appear elsewhere.
"""

from __future__ import annotations

import ast
import ctypes
import os
import re
import sys
from pathlib import Path

import pytest

from client_gateway.routing import guard
from client_gateway.routing.certstore import CertStoreBackend, MemoryCertStore, WindowsCertStore
from client_gateway.routing.managed import ManagedProbe, MemoryManagedProbe, WindowsManagedProbe
from client_gateway.routing.guard import (
    ENV,
    RealSystemForbidden,
    real_system_ok,
    require_real_system,
)
from client_gateway.routing.winproxy import (
    InternetSettingsBackend,
    MemoryInternetSettings,
    WindowsInternetSettings,
)
from client_gateway.routing.wpad import MemoryWpadProbe, WindowsWpadProbe, WpadProbe

ROOT = Path(__file__).resolve().parent.parent
ROUTING = ROOT / "client_gateway" / "routing"
REAL_BACKENDS = [WindowsCertStore, WindowsInternetSettings, WindowsManagedProbe, WindowsWpadProbe]


# ---------------------------------------------------------------------------------- the guard
def test_the_variable_is_gone_for_every_test_and_conftest_says_so():
    assert ENV not in os.environ
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert re.search(r'monkeypatch\.delenv\(\s*"FINVISION_MASKING_REAL_SYSTEM"', conftest)
    assert ENV == "FINVISION_MASKING_REAL_SYSTEM"


def test_the_real_system_is_not_ok_under_test_even_when_the_variable_is_set(monkeypatch):
    assert real_system_ok() is False
    monkeypatch.setenv(ENV, "1")
    assert os.environ.get(guard.UNDER_TEST_ENV)  # pytest marks a running test
    assert real_system_ok() is False  # the variable alone is not enough under pytest
    with pytest.raises(RealSystemForbidden):
        require_real_system("anything")


def test_outside_a_test_the_variable_alone_decides(monkeypatch):
    monkeypatch.delenv(guard.UNDER_TEST_ENV, raising=False)
    assert real_system_ok() is False
    for value in ("0", "", "true", "yes", "2", "1 "[:0]):
        monkeypatch.setenv(ENV, value)
        assert real_system_ok() is False, value
    monkeypatch.setenv(ENV, "1")
    assert real_system_ok() is True
    require_real_system("setup")  # no exception


def test_the_refusal_message_is_fixed_and_names_only_what_was_refused():
    with pytest.raises(RealSystemForbidden) as err:
        require_real_system("the thing")
    assert str(err.value).startswith("the thing: refused") and ENV in str(err.value)


# ---------------------------------------------------------------------------------- the backends
@pytest.mark.parametrize("backend", REAL_BACKENDS, ids=lambda b: b.__name__)
def test_a_real_backend_cannot_be_built_under_pytest(backend, monkeypatch):
    with pytest.raises(RealSystemForbidden):
        backend()
    monkeypatch.setenv(ENV, "1")  # ... not even with the variable
    with pytest.raises(RealSystemForbidden):
        backend()


@pytest.mark.parametrize("backend", REAL_BACKENDS, ids=lambda b: b.__name__)
def test_the_refusal_comes_before_any_ctypes_or_registry_access(backend, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("the system was touched before the guard refused")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setitem(sys.modules, "winreg", None)  # an import of winreg would raise ImportError
    with pytest.raises(RealSystemForbidden):
        backend()


def test_the_fakes_satisfy_the_interfaces():
    store: CertStoreBackend = MemoryCertStore()
    proxy: InternetSettingsBackend = MemoryInternetSettings()
    for name in ("installed", "install", "remove", "list_ours"):
        assert callable(getattr(store, name))
    for name in ("read", "apply_pac", "restore", "notify"):
        assert callable(getattr(proxy, name))
    for real, fake in (
        (WindowsCertStore, MemoryCertStore),
        (WindowsInternetSettings, MemoryInternetSettings),
    ):
        interface = {n for n in dir(fake) if not n.startswith("_")}
        assert {n for n in dir(real) if not n.startswith("_")} & interface


@pytest.mark.parametrize("backend", REAL_BACKENDS, ids=lambda b: b.__name__)
def test_a_real_backend_asks_the_guard_before_anything_else(backend):
    tree = ast.parse(Path(sys.modules[backend.__module__].__file__).read_text(encoding="utf-8"))
    cls = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == backend.__name__
    )
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    body = [
        n for n in init.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
    ]
    first = body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
    assert getattr(first.value.func, "id", "") == "require_real_system"


# ---------------------------------------------------------------------------------- where real calls live
REAL_CALL = re.compile(
    r"winreg|WinDLL|windll|InternetSetOption|CertAddEncodedCertificateToStore|CertOpenStore|"
    r"CertDeleteCertificateFromStore|certutil|reg\.exe|netsh"
)
ALLOWED = {
    "certstore.py",
    "winproxy.py",
    "managed.py",
    "wpad.py",
    "guard.py",
}  # the real backends: store, settings, the READ-ONLY managed-device and WPAD probes (+ the guard)


def test_real_system_calls_exist_only_in_the_two_guarded_backends():
    offenders = {}
    for path in sorted(ROUTING.glob("*.py")):
        if path.name in ALLOWED:
            continue
        found = sorted(set(REAL_CALL.findall(path.read_text(encoding="utf-8"))))
        if found:
            offenders[path.name] = found
    assert offenders == {}
    # and nowhere else in the gateway or the server code either
    for folder in ("client_gateway", "server", "core/client_guard", "tools/masking_lab"):
        for path in (ROOT / folder).rglob("*.py"):
            if ROUTING in path.parents:
                continue
            assert not re.search(
                r"InternetSetOption|CertAddEncodedCertificate|certutil",
                path.read_text(encoding="utf-8"),
            ), path


def test_no_autostart_service_scheduled_task_or_run_key_is_ever_written():
    forbidden = re.compile(
        r"CurrentVersion\\\\?Run\b|schtasks|sc\.exe|CreateService|Startup|RunOnce|TaskScheduler|"
        r"win32service",
        re.IGNORECASE,
    )
    for folder in ("client_gateway", "server/masking_layer.py", "core/client_guard"):
        base = ROOT / folder
        files = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in files:
            assert not forbidden.search(path.read_text(encoding="utf-8")), path


def test_the_registry_is_only_ever_written_under_hkcu_internet_settings():
    text = (ROUTING / "winproxy.py").read_text(encoding="utf-8")
    assert text.count("KEY_SET_VALUE") == 1  # one place opens a key for writing ...
    assert "w.HKEY_CURRENT_USER, SETTINGS_KEY, 0, w.KEY_SET_VALUE" in text  # ... and it is HKCU
    assert "CreateKey" not in text and "DeleteKey" not in text  # values only, never keys
    # HKLM is only ever READ: its one use is the policy lookup (KEY_READ in ``_read_values``)
    assert text.count("w.HKEY_LOCAL_MACHINE") == 1 and '("HKLM", w.HKEY_LOCAL_MACHINE)' in text
    assert text.count("SetValueEx") == 3 and text.count("DeleteValue") == 1


# ---------------------------------------------------------------------------------- the managed probe
def test_the_managed_probe_only_reads():
    text = (ROUTING / "managed.py").read_text(encoding="utf-8")
    assert (
        re.search(r"SetValue|CreateKey|DeleteKey|DeleteValue|KEY_SET_VALUE|KEY_WRITE", text) is None
    )
    assert "HKEY_CURRENT_USER" not in text  # HKLM Enrollments only
    assert text.count("w.HKEY_LOCAL_MACHINE") == 1 and "KEY_READ" in text


def test_the_managed_fake_satisfies_the_interface():
    probe: ManagedProbe = MemoryManagedProbe(("intune_enrollment",))
    assert probe.reasons() == ["intune_enrollment"]
    assert MemoryManagedProbe().reasons() == []
    assert callable(WindowsManagedProbe.reasons)


# ---------------------------------------------------------------------------------- the WPAD probe
def test_the_wpad_probe_only_reads_and_only_asks_winhttp():
    text = (ROUTING / "wpad.py").read_text(encoding="utf-8")
    # no registry at all (it does not even read it), no write of any kind, no process, no file
    assert (
        re.search(r"winreg|SetValue|CreateKey|DeleteKey|DeleteValue|KEY_SET_VALUE|KEY_WRITE", text)
        is None
    )
    assert re.search(r"subprocess|os\.system|Popen|open\(|write_|netsh|reg\.exe", text) is None
    assert (
        re.search(r"InternetSetOption|WinHttpSetOption|WinHttpSetDefaultProxyConfiguration", text)
        is None
    )
    # the one network question it asks: auto-detect (DHCP + DNS) for the fixed URL
    assert text.count("WinHttpGetProxyForUrl(") == 1
    assert "WINHTTP_AUTOPROXY_AUTO_DETECT" in text and 'PROBE_URL = "https://chatgpt.com/"' in text
    assert "WINHTTP_ACCESS_TYPE_NO_PROXY" in text  # the session itself uses no proxy


def test_the_wpad_fake_satisfies_the_interface():
    probe: WpadProbe = MemoryWpadProbe("found")
    assert probe.probe() == "found"
    assert MemoryWpadProbe().probe() == "none"
    assert callable(WindowsWpadProbe.probe)
    with pytest.raises(ValueError):
        MemoryWpadProbe("maybe")
