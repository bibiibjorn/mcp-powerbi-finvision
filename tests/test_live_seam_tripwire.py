"""The suite-wide live-seam tripwire (tests/conftest.py, review C-05) really trips."""

import socket
import subprocess

import pytest

from tests.conftest import LiveSeamError, _is_bridge_argv, _is_local_address


@pytest.mark.parametrize(
    "addr,local",
    [
        (("127.0.0.1", 80), True),
        (("localhost", 80), True),
        (("::1", 80, 0, 0), True),
        (("0.0.0.0", 80), True),
        ("some-unix-socket-path", True),
        (("93.184.216.34", 443), False),
        (("api.anthropic.com", 443), False),
    ],
)
def test_is_local_address(addr, local):
    assert _is_local_address(addr) is local


def test_bridge_argv_detection(tmp_path):
    assert _is_bridge_argv(["node", "C:/npm/node_modules/@microsoft/powerbi-desktop/dist/cli.js"])
    assert _is_bridge_argv(["node", "C:/repo/tools/bridge_lib.mjs", "reload-model"])
    assert not _is_bridge_argv(["git", "status"])
    # a fake CLI built under the temp dir by a hermetic test is allowed
    assert not _is_bridge_argv(["node", str(tmp_path / "powerbi-desktop" / "dist" / "cli.js")])


def test_non_loopback_connect_trips(_live_seam_tripwire):
    with pytest.raises(LiveSeamError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("93.184.216.34", 443))
    assert _live_seam_tripwire  # recorded for the teardown assertion
    _live_seam_tripwire.clear()


def test_bridge_cli_spawn_trips(_live_seam_tripwire):
    with pytest.raises(LiveSeamError):
        subprocess.run(["powerbi-desktop", "status"], capture_output=True)
    assert _live_seam_tripwire
    _live_seam_tripwire.clear()


def test_adomd_connection_constructor_trips(_live_seam_tripwire):
    from core.infrastructure import connection_manager as cm

    if cm.AdomdConnection is None:
        pytest.skip("ADOMD.NET not loadable here: nothing to guard")
    with pytest.raises(LiveSeamError):
        cm.AdomdConnection("Data Source=localhost:1")
    _live_seam_tripwire.clear()


def test_loopback_server_still_works():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cli = socket.socket()
    cli.connect(srv.getsockname())
    cli.close()
    srv.close()


@pytest.mark.live_seam
def test_opt_out_marker_disables_the_guard():
    assert socket.socket.connect.__name__ == "connect"
