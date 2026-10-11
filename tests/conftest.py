"""Shared pytest fixtures and path bootstrap."""

import ipaddress
import os
import socket
import subprocess
import sys
import tempfile

import pytest

# before anything imports the config singleton: the developer's own config/local_config.json
# (masking switched on, routing, a profile) must not change what the tests see
os.environ["MCP_PBI_NO_LOCAL_CONFIG"] = "1"
# a gateway a test starts must not stop itself because no folder is routed (client_gateway.reaper);
# the reaper's own tests clear it
os.environ["FINVISION_GATEWAY_KEEP"] = "1"

# Put repo root on sys.path so `import core.*` / `import server.*` resolves.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _clear_bridge_caches():
    """Best-effort reset of both desktop-bridge TTL caches."""
    try:
        from core.autonomous.desktop_bridge import (
            invalidate_manifest_cache,
            invalidate_status_cache,
        )

        invalidate_status_cache()
        invalidate_manifest_cache()
    except Exception:
        pass
    try:
        # WP11: the bridge's edit ledger (pending live-TOM writes / disk edits)
        # and the resolved-CLI cache are process state too.
        from core.autonomous import desktop_bridge, edit_ledger

        edit_ledger.reset()
        desktop_bridge.reset_cli_cache()
        desktop_bridge.clear_flag_stuck()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _client_gateway_isolated(monkeypatch, tmp_path_factory):
    """No test sees the developer's own gateway routing, masking home or Claude settings: the chat
    that runs the suite may itself be routed (ANTHROPIC_BASE_URL in this process's environment)."""
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setenv("FINVISION_MASKING_HOME", str(tmp_path_factory.mktemp("masking_home")))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude_config")))
    monkeypatch.setenv("FINVISION_GATEWAY_NER", "0")
    # R8: no test may build a real certificate-store / Internet Settings backend (the guard in
    # ``client_gateway.routing.guard`` refuses it without this variable, and under pytest regardless)
    monkeypatch.delenv("FINVISION_MASKING_REAL_SYSTEM", raising=False)
    # the masking suites were written for the Finvision behaviour (FAM/GLA/PCC prefixes, BE shapes,
    # S01/S02 safe tables); the shipped default is ``generic`` (tests/test_client_guard_profiles.py
    # sets its own profile)
    monkeypatch.setenv("FINVISION_MASKING_PROFILE", "finvision")
    # the gateway tests were written for the masking-on path; the real default (off) is tested
    # in test_client_gateway_state.py
    monkeypatch.setenv("FINVISION_GATEWAY_DEFAULT_MODE", "on")  # the NER model is tested on its own
    yield


@pytest.fixture(autouse=True)
def _reset_bridge_status_cache():
    """Invalidate the desktop-bridge status + manifest TTL caches around every
    test so a cached instance list / manifest from one test can't leak into the
    next (the manifest TTL is 60s — longer than a whole test session)."""
    _clear_bridge_caches()
    yield
    _clear_bridge_caches()


@pytest.fixture(autouse=True)
def _no_real_bridge_lib_or_tom_reads(monkeypatch):
    """Keep bridge tests hermetic: the node library helper (which would talk to a real
    Desktop pipe) and the model-change classifier's TOM/CLR reads are stubbed out for every
    test. A test that exercises them installs its own fake (applied later, so it wins)."""
    try:
        from core.autonomous import desktop_bridge, model_change_classifier
    except Exception:
        yield
        return
    monkeypatch.setattr(
        desktop_bridge,
        "run_bridge_lib",
        lambda command, args, **k: {
            "ok": False,
            "error_type": "bridge_lib_unavailable",
            "error": "bridge library helper disabled in tests",
        },
    )
    monkeypatch.setattr(
        model_change_classifier,
        "_read_disk_signature",
        lambda definition_dir: {"ok": False, "unavailable": True, "reason": "disabled in tests"},
    )
    monkeypatch.setattr(
        model_change_classifier,
        "_read_live_signature",
        lambda project_matches=None: {"ok": False, "reason": "disabled in tests"},
    )
    yield


@pytest.fixture(autouse=True)
def _reset_gate_policy_cache():
    """Invalidate the gate-policy cache around every test so monkeypatched
    MCP_AUTONOMOUS_GATE_POLICY values take effect."""
    try:
        from core.autonomous.gating import invalidate_policy_cache

        invalidate_policy_cache()
    except Exception:
        pass
    yield
    try:
        from core.autonomous.gating import invalidate_policy_cache

        invalidate_policy_cache()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _pin_gate_policy(monkeypatch):
    """K-14: pin the destructive-op gate policy to the documented default
    (LIGHT) so an exported MCP_AUTONOMOUS_GATE_POLICY=STRICT/OFF in the
    developer's shell cannot flip handler tests. Tests that need another
    policy override it with their own monkeypatch.setenv (applied later)."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    monkeypatch.delenv("MCP_GATE_ELICITATION", raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_dispatch_state():
    """Drop the process-wide server loop + orphaned-call registry between tests
    so a timed-out call in one test can't make another see 'desktop_unresponsive'."""
    yield
    try:
        from server import request_context

        request_context.set_server_loop(None)
    except Exception:
        pass
    try:
        import server.dispatch as _d

        with _d._ORPHANS_LOCK:
            _d._ORPHANS.clear()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolated_write_journal(tmp_path):
    """JR-01: keep the durable write journal OFF (and pointed at a temp dir) for every test so a
    commit made by an unrelated test never touches ``output/journal``. The journal tests switch it
    on with ``write_journal.configure(enabled=True, directory=...)``."""
    try:
        from core.operations import write_journal
    except Exception:
        yield
        return
    write_journal.reset()
    write_journal.configure(enabled=False, directory=tmp_path / "_journal")
    yield
    write_journal.reset()


# ---------------------------------------------------------------------------
# C-05: `--fast` must mean something + a suite-wide tripwire on live seams
# ---------------------------------------------------------------------------

#: Whole files that start processes / local servers / OCR engines or sleep for real. They are
#: the bulk of the wall-clock time (gateway CLI / hook / image / server / NER tests, the headless
#: CLI end-to-end run, ~5 minutes in total in the 2026-10-03 baseline) and are tagged ``slow`` here
#: rather than with a decorator in every file, so ``python run_tests.py --fast`` skips them.
_SLOW_FILES = frozenset(
    {
        "test_client_gateway_cli.py",
        "test_client_gateway_hook.py",
        "test_client_gateway_images.py",
        "test_client_gateway_mode_handler.py",
        "test_client_gateway_ner_worker.py",
        "test_client_gateway_server.py",
        "test_cli_headless.py",
        "test_review_h_golden_sweep.py",
        "test_visual_catalog_build_integration.py",
    }
)

#: Individual tests that take > 2 s in otherwise fast files (real 3 s waits).
_SLOW_TESTS = frozenset(
    {
        "test_preflight_never_writes_even_with_the_cli",
        "test_mismatch_after_reload_keeps_the_tmdl_ledger_and_says_so",
        "test_unreadable_model_after_reload_is_reported_not_verified",
        "test_j08_u2_own_write_during_save_is_not_a_receipt",
        "test_j08_report_only_change_does_not_verify_a_model_save",
        "test_mask_one_megabyte_against_20k_values_is_fast",
    }
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_seam: the test deliberately reaches a live seam (real Desktop connect / detection, "
        "the bridge CLI subprocess, a non-loopback socket); opts out of the tripwire fixture",
    )


def pytest_collection_modifyitems(config, items):
    slow = pytest.mark.slow
    for item in items:
        if item.get_closest_marker("slow") is not None:
            continue
        if os.path.basename(str(item.fspath)) in _SLOW_FILES or item.originalname in _SLOW_TESTS:
            item.add_marker(slow)


class LiveSeamError(AssertionError):
    """A test touched a live seam without ``@pytest.mark.live_seam``."""


_LOOPBACK_NAMES = {"", "localhost", "ip6-localhost", "<broadcast>"}


def _is_local_address(addr) -> bool:
    """True for AF_UNIX paths and loopback / unspecified IP literals."""
    if not isinstance(addr, tuple) or not addr:
        return True  # AF_UNIX path, bytes, or something that is not a host/port pair
    host = addr[0]
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    host = str(host).strip().lower()
    if host in _LOOPBACK_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False  # a real host name: it would leave the machine
    return ip.is_loopback or ip.is_unspecified


def _is_bridge_argv(argv) -> bool:
    """The powerbi-desktop bridge CLI / its node helper (what ``run_bridge*`` spawns)."""
    if isinstance(argv, (str, bytes, os.PathLike)):
        argv = [argv]
    try:
        parts = [os.fsdecode(a).lower().replace("\\", "/") for a in argv]
    except TypeError:
        return False
    tmp_roots = {
        os.path.normcase(r).replace("\\", "/")
        for r in (tempfile.gettempdir(), os.path.realpath(tempfile.gettempdir()))
    }
    if any(p.startswith(tuple(tmp_roots)) for p in parts):
        return False  # a fake CLI built under tmp_path by a hermetic test
    flat = " ".join(parts)
    return "powerbi-desktop" in flat or "bridge_lib.mjs" in flat


@pytest.fixture(autouse=True)
def _live_seam_tripwire(request, monkeypatch):
    """Fail any test that reaches Power BI Desktop, the bridge CLI or the internet.

    Seams guarded (a test that patches one itself wins - its patch is applied after this one;
    a test that really needs one carries ``@pytest.mark.live_seam``):

    * constructing a real ADOMD connection (what every ``ConnectionManager`` connect / reconnect,
      the XMLA target, the trace session and role impersonation do before they reach Desktop);
    * a subprocess whose argv is the powerbi-desktop bridge CLI or its node helper
      (``desktop_bridge.run_bridge`` / ``run_bridge_lib`` end up in ``subprocess.Popen``, which
      ``subprocess.run`` uses too) unless the CLI is a fake under the system temp dir;
    * ``socket.connect`` to any non-loopback address.

    The error is raised at the call site AND re-asserted at teardown, so a broad ``except`` in
    the code under test cannot hide it.
    """
    if request.node.get_closest_marker("live_seam") is not None:
        yield
        return
    tripped: list = []

    def _trip(name):
        def _f(*a, **k):
            tripped.append(name)
            raise LiveSeamError(f"live seam touched: {name} (mark the test @pytest.mark.live_seam)")

        return _f

    # The ADOMD connection constructors: nothing opens a connection to a (possibly running)
    # Desktop / XMLA endpoint without going through one of them. Tests that exercise the real
    # connect code install a fake ``AdomdConnection`` themselves (applied after this one).
    import importlib

    for mod_name in (
        "core.infrastructure.connection_manager",
        "core.infrastructure.xmla_target",
        "core.infrastructure.trace_session",
        "core.infrastructure.role_impersonation",
    ):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:  # not importable here: nothing to guard
            continue
        if getattr(mod, "AdomdConnection", None) is not None:
            monkeypatch.setattr(mod, "AdomdConnection", _trip(f"{mod_name}.AdomdConnection"))

    real_popen = subprocess.Popen

    class _GuardedPopen(real_popen):
        def __init__(self, *a, **k):
            argv = a[0] if a else k.get("args")
            if _is_bridge_argv(argv):
                tripped.append(f"subprocess.Popen {str(argv)[:80]}")
                raise LiveSeamError("live seam touched: the powerbi-desktop bridge CLI was spawned")
            super().__init__(*a, **k)

    monkeypatch.setattr(subprocess, "Popen", _GuardedPopen)

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def _guarded_connect(self, address):
        if not _is_local_address(address):
            tripped.append(f"socket.connect {address!r}")
            raise LiveSeamError(f"live seam touched: socket connect to {address!r}")
        return real_connect(self, address)

    def _guarded_connect_ex(self, address):
        if not _is_local_address(address):
            tripped.append(f"socket.connect_ex {address!r}")
            raise LiveSeamError(f"live seam touched: socket connect_ex to {address!r}")
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
    yield tripped  # the self-test (test_live_seam_tripwire.py) clears it after an expected trip
    assert not tripped, f"a live seam was touched: {tripped}"
