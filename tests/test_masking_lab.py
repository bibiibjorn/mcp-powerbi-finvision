"""The masking lab (tools/masking_lab/lab.py): refusal rules, seeding, child environment.

Nothing here starts a process or opens a port: the child spawn and every HTTP call are replaced,
and a tripwire fixture fails the test if a real ``Popen`` is attempted.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from client_gateway.detectors import parse_client_list
from tools.masking_lab import lab

LAB_PORT = 18890


@pytest.fixture(autouse=True)
def _no_real_spawn(monkeypatch):
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a real subprocess was started")

    monkeypatch.setattr(subprocess, "Popen", refuse)


@pytest.fixture()
def fake_local_appdata(monkeypatch, tmp_path):
    """The REAL masking home of this test run lives under a temp LOCALAPPDATA."""
    base = tmp_path / "localappdata"
    base.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(base))
    return base / "FinvisionMasking"


@pytest.fixture()
def fakes(monkeypatch):
    """No process, no HTTP: records the spawn and answers health from a script."""
    calls: dict[str, Any] = {"spawn": [], "http": [], "health_after_spawn": True, "up": False}

    def spawn(argv: list[str], env: dict[str, str], cwd: Path, log_path: Path) -> int:
        calls["spawn"].append({"argv": argv, "env": env, "cwd": cwd, "log": log_path})
        calls["up"] = calls["health_after_spawn"]
        return 4242

    def http(
        port: int, method: str, path: str, token: str | None = None, timeout: float = 3.0
    ) -> tuple[int, dict[str, Any]] | None:
        calls["http"].append({"port": port, "method": method, "path": path, "token": token})
        if not calls["up"]:
            return None
        if path == "/__gateway/health":
            return 200, {"ok": True, "port": port, "pid": 4242, "version": "test"}
        if path == "/__gateway/shutdown":
            calls["up"] = False
            return 200, {"ok": True}
        if path == "/__gateway/status":
            return 200, {"ok": True, "dictionary": {"values": 20}}
        return 404, {}

    monkeypatch.setattr(lab, "spawn_child", spawn)
    monkeypatch.setattr(lab, "http_call", http)
    monkeypatch.setattr(lab.time, "sleep", lambda _s: None)
    monkeypatch.setattr(lab, "configured_port", lambda: 8890)
    return calls


# ---------------------------------------------------------------------------------- fixture
def test_synthetic_fixture_has_about_twenty_fake_names_across_prefixes():
    lines = lab.FIXTURE.read_text(encoding="utf-8").splitlines()
    parsed = parse_client_list([ln for ln in lines if ln.strip() and not ln.startswith("#")])
    assert sum(len(v) for v in parsed.values()) >= 18
    assert {"CUS", "VEN", "PER", "ORG"} <= set(parsed)
    assert "Zorblax Holdings NV" in parsed["CUS"]
    assert "Quentin Testermans" in parsed["PER"]


# ---------------------------------------------------------------------------------- refusals
def test_real_masking_home_ignores_the_env_override(fake_local_appdata, monkeypatch, tmp_path):
    monkeypatch.setenv("FINVISION_MASKING_HOME", str(tmp_path / "somewhere_else"))
    assert lab.real_masking_home() == fake_local_appdata


def test_refuses_the_production_port(tmp_path, fakes):
    with pytest.raises(lab.LabRefusal, match="8890"):
        lab.validate(tmp_path / "lab", 8890)


def test_refuses_the_configured_production_port(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(lab, "configured_port", lambda: 19001)
    with pytest.raises(lab.LabRefusal, match="19001"):
        lab.validate(tmp_path / "lab", 19001)


@pytest.mark.parametrize("port", [0, -1, 65536, 70000])
def test_refuses_an_out_of_range_port(tmp_path, fakes, port):
    with pytest.raises(lab.LabRefusal, match="port"):
        lab.validate(tmp_path / "lab", port)


def test_refuses_a_home_equal_to_the_real_masking_home(tmp_path, fakes, fake_local_appdata):
    with pytest.raises(lab.LabRefusal, match="real masking home"):
        lab.validate(tmp_path / "lab", LAB_PORT, home=fake_local_appdata)


def test_refuses_a_home_that_resolves_to_the_real_home_through_dots(
    tmp_path, fakes, fake_local_appdata
):
    sneaky = fake_local_appdata.parent / "x" / ".." / "FinvisionMasking"
    with pytest.raises(lab.LabRefusal, match="real masking home"):
        lab.validate(tmp_path / "lab", LAB_PORT, home=sneaky)


def test_refuses_a_lab_dir_inside_the_real_masking_home(tmp_path, fakes, fake_local_appdata):
    with pytest.raises(lab.LabRefusal, match="real masking home"):
        lab.validate(fake_local_appdata / "lab", LAB_PORT)


def test_refuses_a_home_outside_the_lab_dir(tmp_path, fakes, fake_local_appdata):
    with pytest.raises(lab.LabRefusal, match="outside"):
        lab.validate(tmp_path / "lab", LAB_PORT, home=tmp_path / "elsewhere")


def test_refuses_a_home_that_only_shares_a_name_prefix_with_the_lab_dir(
    tmp_path, fakes, fake_local_appdata
):
    with pytest.raises(lab.LabRefusal, match="outside"):
        lab.validate(tmp_path / "lab", LAB_PORT, home=tmp_path / "lab-evil" / "home")


def test_valid_arguments_give_the_home_under_the_lab_dir(tmp_path, fakes, fake_local_appdata):
    home = lab.validate(tmp_path / "lab", LAB_PORT)
    assert home == (tmp_path / "lab" / "home").resolve()


# ---------------------------------------------------------------------------------- start
def test_start_refusal_exits_2_and_touches_nothing(tmp_path, fakes, fake_local_appdata, capsys):
    target = tmp_path / "lab"
    assert lab.main(["start", "--dir", str(target), "--port", "8890"]) == 2
    assert "refused" in capsys.readouterr().err
    assert not target.exists()
    assert fakes["spawn"] == []
    assert fakes["http"] == []


def test_start_refusal_for_the_configured_port_exits_2(
    tmp_path, fakes, fake_local_appdata, monkeypatch
):
    monkeypatch.setattr(lab, "configured_port", lambda: 19001)
    assert lab.main(["start", "--dir", str(tmp_path / "lab"), "--port", "19001"]) == 2
    assert fakes["spawn"] == []


def test_dir_is_required():
    with pytest.raises(SystemExit) as exc:
        lab.main(["start"])
    assert exc.value.code == 2


def test_start_seeds_the_client_list_into_the_lab_home(tmp_path, fakes, fake_local_appdata):
    target = tmp_path / "lab"
    assert lab.main(["start", "--dir", str(target)]) == 0
    seeded = target / "home" / "client_names.txt"
    assert seeded.read_bytes() == lab.FIXTURE.read_bytes()
    assert not fake_local_appdata.exists()  # the real home was never created or written


def test_start_does_not_overwrite_an_edited_client_list(tmp_path, fakes, fake_local_appdata):
    target = tmp_path / "lab"
    (target / "home").mkdir(parents=True)
    (target / "home" / "client_names.txt").write_text("CUS: Mine Only\n", encoding="utf-8")
    assert lab.main(["start", "--dir", str(target)]) == 0
    assert (target / "home" / "client_names.txt").read_text(encoding="utf-8") == "CUS: Mine Only\n"


def test_start_passes_the_lab_environment_and_argv_to_the_child(
    tmp_path, fakes, fake_local_appdata, monkeypatch
):
    monkeypatch.setenv("FINVISION_MASKING_HOME", str(fake_local_appdata))  # the real home
    monkeypatch.setenv("FINVISION_GATEWAY_PORT", "8890")
    monkeypatch.setenv("FINVISION_GATEWAY_AUTOSTART", "1")
    monkeypatch.setenv("FINVISION_GATEWAY_NER", "1")
    target = tmp_path / "lab"
    assert lab.main(["start", "--dir", str(target), "--port", str(LAB_PORT)]) == 0
    (call,) = fakes["spawn"]
    env = call["env"]
    assert env["FINVISION_MASKING_HOME"] == str((target / "home").resolve())
    assert env["FINVISION_GATEWAY_PORT"] == str(LAB_PORT)
    assert env["FINVISION_GATEWAY_AUTOSTART"] == "0"
    assert env["FINVISION_GATEWAY_NER"] == "0"
    argv = call["argv"]
    assert argv[1:4] == ["-m", "client_gateway", "serve"]
    assert "--no-provider" in argv  # R8: the owner's open Desktop files are never queried
    assert argv[argv.index("--port") + 1] == str(LAB_PORT)
    assert call["cwd"] == lab.REPO_ROOT
    assert call["log"].parent == target.resolve()


def test_start_never_sets_up_claude_settings_registry_or_certificates(
    tmp_path, fakes, fake_local_appdata, monkeypatch
):
    claude_dir = tmp_path / "claude_config"
    claude_dir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    assert lab.main(["start", "--dir", str(tmp_path / "lab")]) == 0
    assert list(claude_dir.iterdir()) == []
    env = fakes["spawn"][0]["env"]
    assert env["CLAUDE_CONFIG_DIR"] == str(claude_dir)  # inherited, not redirected or written


def test_start_waits_for_health_on_the_lab_port_and_reports_it(
    tmp_path, fakes, fake_local_appdata, capsys
):
    assert lab.main(["start", "--dir", str(tmp_path / "lab"), "--port", "18891"]) == 0
    assert {c["port"] for c in fakes["http"]} == {18891}
    assert any(c["path"] == "/__gateway/health" for c in fakes["http"])
    assert "18891" in capsys.readouterr().out


def test_start_when_the_child_never_answers_exits_1(
    tmp_path, fakes, fake_local_appdata, monkeypatch
):
    fakes["health_after_spawn"] = False
    monkeypatch.setattr(lab, "START_WAIT_S", 0.0)
    assert lab.main(["start", "--dir", str(tmp_path / "lab")]) == 1


def test_start_when_the_lab_gateway_already_runs_does_not_spawn_again(
    tmp_path, fakes, fake_local_appdata
):
    fakes["up"] = True
    assert lab.main(["start", "--dir", str(tmp_path / "lab")]) == 0
    assert fakes["spawn"] == []


def test_start_records_the_port_for_stop_and_status(tmp_path, fakes, fake_local_appdata):
    target = tmp_path / "lab"
    lab.main(["start", "--dir", str(target), "--port", "18893"])
    info = json.loads((target / "lab.json").read_text(encoding="utf-8"))
    assert info["port"] == 18893
    assert info["home"] == str((target / "home").resolve())


# ---------------------------------------------------------------------------------- stop / status
def _lab_with_token(tmp_path: Path, port: int = LAB_PORT) -> Path:
    target = tmp_path / "lab"
    (target / "home").mkdir(parents=True)
    (target / "home" / "gateway.token").write_text("t" * 43, encoding="ascii")
    (target / "lab.json").write_text(json.dumps({"port": port}), encoding="utf-8")
    return target


def test_stop_posts_shutdown_with_the_lab_homes_token(tmp_path, fakes, fake_local_appdata):
    target = _lab_with_token(tmp_path)
    fakes["up"] = True
    assert lab.main(["stop", "--dir", str(target)]) == 0
    posts = [c for c in fakes["http"] if c["path"] == "/__gateway/shutdown"]
    assert len(posts) == 1
    assert posts[0]["method"] == "POST"
    assert posts[0]["token"] == "t" * 43
    assert posts[0]["port"] == LAB_PORT
    assert fakes["up"] is False


def test_stop_uses_the_recorded_port_unless_one_is_given(tmp_path, fakes, fake_local_appdata):
    target = _lab_with_token(tmp_path, port=18895)
    fakes["up"] = True
    lab.main(["stop", "--dir", str(target)])
    assert {c["port"] for c in fakes["http"]} == {18895}


def test_stop_refuses_the_production_port(tmp_path, fakes, fake_local_appdata, capsys):
    target = _lab_with_token(tmp_path)
    assert lab.main(["stop", "--dir", str(target), "--port", "8890"]) == 2
    assert fakes["http"] == []


def test_stop_of_a_lab_that_is_not_running_is_a_clean_no_op(
    tmp_path, fakes, fake_local_appdata, capsys
):
    target = _lab_with_token(tmp_path)
    assert lab.main(["stop", "--dir", str(target)]) == 0
    assert [c["path"] for c in fakes["http"]] == ["/__gateway/health"]
    assert "not running" in capsys.readouterr().out


def test_stop_without_a_lab_token_exits_1_and_does_not_post(tmp_path, fakes, fake_local_appdata):
    target = tmp_path / "lab"
    (target / "home").mkdir(parents=True)
    fakes["up"] = True
    assert lab.main(["stop", "--dir", str(target)]) == 1
    assert not [c for c in fakes["http"] if c["path"] == "/__gateway/shutdown"]


def test_status_prints_health_and_the_token_authed_status(
    tmp_path, fakes, fake_local_appdata, capsys
):
    target = _lab_with_token(tmp_path)
    fakes["up"] = True
    assert lab.main(["status", "--dir", str(target)]) == 0
    paths = [(c["path"], c["token"]) for c in fakes["http"]]
    assert ("/__gateway/health", None) in paths
    assert ("/__gateway/status", "t" * 43) in paths
    out = json.loads(capsys.readouterr().out)
    assert out["health"]["port"] == LAB_PORT
    assert out["status"]["dictionary"]["values"] == 20


def test_status_of_a_lab_that_is_not_running_exits_1(tmp_path, fakes, fake_local_appdata, capsys):
    target = _lab_with_token(tmp_path)
    assert lab.main(["status", "--dir", str(target)]) == 1
    assert "not running" in capsys.readouterr().out


def test_status_refuses_the_production_port(tmp_path, fakes, fake_local_appdata):
    target = _lab_with_token(tmp_path)
    assert lab.main(["status", "--dir", str(target), "--port", "8890"]) == 2
    assert fakes["http"] == []
