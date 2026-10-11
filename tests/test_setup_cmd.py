"""``python -m client_gateway setup`` (phase 4b.1, rules R7, R8, R10). Fakes only: an in-memory
certificate store and Internet Settings, a fake DPAPI, a fake managed-device probe, every client config
in a temp dir. Nothing here touches the real system: the routed flow (``--desktop-apps``) under pytest is
refused by the guard, the default (Claude Code masking on demand: config + hooks) only writes into the
injected temp paths, and a plan (no ``--yes``) provably writes nothing. ``CLAUDE_CONFIG_DIR`` and
``FINVISION_MASKING_HOME`` always point into the temp dir (``folders.*`` reads them).
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from client_gateway import cli, folders, setup_cmd
from client_gateway.routing.backends import Backends
from client_gateway.routing.ca import CAStore
from client_gateway.routing.certstore import FRIENDLY_NAME, CAInstaller, MemoryCertStore
from client_gateway.routing.guard import ENV as REAL_SYSTEM_ENV
from client_gateway.routing.managed import MemoryManagedProbe
from client_gateway.routing.winproxy import InternetSettings, MemoryInternetSettings
from client_gateway.routing.wpad import MemoryWpadProbe
from client_gateway.setup_cmd import (
    EXIT_FAILED,
    EXIT_MANAGED,
    EXIT_OK,
    EXIT_REFUSED,
    SERVER_NAME,
    Options,
    SetupPaths,
)
from tests._routing_fakes import fake_crypto

PAC_URL = "http://127.0.0.1:8890/proxy.pac"
# event -> (word, timeout, statusMessage, matcher): the shapes of a real install
HOOKS: dict[str, tuple[str, int, str, str | None]] = {
    "SessionStart": (
        "session",
        120,
        "Client masking (gateway)",
        "startup|resume|clear|compact",
    ),
    "UserPromptSubmit": ("prompt", 120, "Client masking (on/off)", None),
    "PreToolUse": ("pretool", 30, "Client masking (tool guard)", None),
}


# ---------------------------------------------------------------------------------- environment
class Env:
    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch | None = None) -> None:
        self.tmp = tmp
        if monkeypatch is not None:  # folders.* reads these: never the real ~/.claude or home
            monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp / "user" / ".claude"))
            monkeypatch.setenv("FINVISION_MASKING_HOME", str(tmp / "home"))
            monkeypatch.setenv("FINVISION_GATEWAY_PORT", "18999")
        self.certs = MemoryCertStore()
        self.settings = MemoryInternetSettings.with_values(enable=0)
        self.managed = MemoryManagedProbe()
        self.wpad = MemoryWpadProbe("found")  # a probe that would discover a company PAC
        self.out: list[str] = []
        appdata = tmp / "appdata"
        (appdata / "Claude").mkdir(parents=True)
        (appdata / "Code" / "User").mkdir(parents=True)
        (tmp / "user").mkdir()
        (tmp / "repo" / "config").mkdir(parents=True)
        self.paths = SetupPaths(
            home=tmp / "home",
            config_file=tmp / "repo" / "config" / "local_config.json",
            repo_root=tmp / "repo",
            claude_desktop=appdata / "Claude" / "claude_desktop_config.json",
            vscode=appdata / "Code" / "User" / "mcp.json",
            claude_code=tmp / "user" / ".claude.json",
            claude_settings=tmp / "user" / ".claude" / "settings.json",
        )
        self.paths.claude_code.write_text(
            json.dumps({"numStartups": 7, "mcpServers": {"other": {"command": "x"}}}),
            encoding="utf-8",
        )

    @property
    def backends(self) -> Backends:
        return Backends(self.certs, self.settings, fake_crypto(), self.managed, self.wpad)

    def run(self, backends: Backends | None = None, gateway: Any = None, **opts: Any) -> int:
        return setup_cmd.run(
            Options(**opts),
            backends if backends is not None else self.backends,
            self.paths,
            self.out.append,
            lambda: gateway,
        )

    def run_no_backends(self, gateway: Any = None, **opts: Any) -> int:
        """``open_backends()`` failed: backends are None."""
        return setup_cmd.run(
            Options(**opts), None, self.paths, self.out.append, lambda: gateway, "no system access"
        )

    @property
    def settings_file(self) -> Path:
        assert self.paths.claude_settings is not None
        return self.paths.claude_settings

    def hook_group(self, event: str) -> dict[str, Any]:
        """The exact group setup adds for ``event`` (the repo here has no venv: sys.executable)."""
        word, timeout, status, matcher = HOOKS[event]
        group: dict[str, Any] = {} if matcher is None else {"matcher": matcher}
        python = Path(sys.executable).as_posix()
        script = (self.paths.repo_root / "hooks" / "client_masking_hook.py").as_posix()
        group["hooks"] = [
            {
                "type": "command",
                "command": f'"{python}" "{script}" {word}',
                "timeout": timeout,
                "statusMessage": status,
            }
        ]
        return group

    @property
    def text(self) -> str:
        return "\n".join(self.out)

    def tree(self) -> dict[str, bytes]:
        """Every file under the temp dir and its bytes."""
        return {
            str(p.relative_to(self.tmp)): p.read_bytes()
            for p in sorted(self.tmp.rglob("*"))
            if p.is_file()
        }

    def system_untouched(self) -> bool:
        return self.certs.calls == [] and self.settings.writes == [] and self.settings.notified == 0

    def json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)


# ---------------------------------------------------------------------------------- the plan
def test_a_plan_changes_nothing(env: Env):
    before = env.tree()
    # with the backends as they are (not wrapped): the plan itself must not write
    assert env.run() == EXIT_OK
    assert env.run(desktop=True) == EXIT_OK
    assert env.run(remove=True) == EXIT_OK
    assert env.run(register=True) == EXIT_OK
    assert env.run(status=True) == EXIT_OK
    assert env.run_no_backends() == EXIT_OK
    assert env.tree() == before
    assert not env.paths.home.exists()
    assert env.system_untouched()


def test_the_default_plan_describes_masking_on_demand(env: Env):
    assert env.run() == EXIT_OK
    text = env.text
    assert "PLAN" in text and "ON DEMAND" in text and "type masking on" in text
    assert "client_gateway.mcp_masking=true and client_gateway.routing=false" in text
    assert "hooks:    missing (SessionStart, UserPromptSubmit, PreToolUse)" in text
    assert "--desktop-apps" in text and "Copilot / ChatGPT cannot start the gateway" in text
    assert "proxy:" not in text and "managed:" not in text and "CA:       " not in text
    assert "on demand: masking on / masking off" in text  # the Claude Code coverage line
    assert "Nothing was changed. Apply with: python -m client_gateway setup --yes" in text
    assert env.out[-1].endswith("client_gateway setup --yes")  # no --desktop-apps in the default


def test_the_desktop_plan_keeps_the_routed_situation(env: Env):
    assert env.run(desktop=True) == EXIT_OK
    text = env.text
    assert "setup --yes --desktop-apps" in text and "proxy:    no proxy today" in text
    assert "managed:  no sign of Intune" in text and "CA:       none yet" in text
    assert (
        "Nothing was changed. Apply with: python -m client_gateway setup --yes --desktop-apps"
        in text
    )
    assert "the gateway runs while the Power BI MCP runs" in text


def test_a_plan_names_what_yes_would_do(env: Env):
    assert env.run(register=True, desktop=True) == EXIT_OK
    text = env.text
    assert "PLAN" in text and "nothing is changed" in text
    assert "proxy:    no proxy today" in text
    assert "managed:  no sign of Intune" in text
    assert "CA:       none yet" in text
    assert "CurrentUser\\Root" in text and "client_gateway.routing=true" in text
    assert "claude.ai" in text and "githubcopilot.com" in text
    assert "Claude Desktop" in text and "detected: would add the entry" in text
    assert "VS Code" in text and "Claude Code" in text
    assert "INCLUDES" in text  # --register is in this plan
    assert "ChatGPT" in text and "OpenAI's own connector setup" in text
    assert (
        "Nothing was changed. Apply with: python -m client_gateway setup --yes --desktop-apps "
        "--register" in text
    )


def test_a_plan_shows_the_proxy_situation_and_the_managed_check(env: Env):
    env.settings = MemoryInternetSettings.with_values(locked=("HKLM_policy_sets_proxy",))
    env.managed = MemoryManagedProbe(("intune_enrollment",))
    env.run(desktop=True)
    assert "locked by policy: routing is REFUSED" in env.text
    assert "MANAGED (intune_enrollment)" in env.text and "--managed-ok" in env.text
    env.out.clear()
    env.settings = MemoryInternetSettings.with_values(detect=1)
    env.run(desktop=True)
    assert "WPAD" in env.text and "REFUSED" in env.text
    env.out.clear()
    env.settings = MemoryInternetSettings.with_values(enable=1, server="corp:8080")
    env.run(desktop=True)
    assert "a static proxy" in env.text
    env.out.clear()
    env.settings = MemoryInternetSettings.with_values(pac="http://corp/wpad.pac")
    env.run(desktop=True)
    assert "a PAC script" in env.text


def test_a_plan_shows_the_wpad_probe_result_for_every_outcome(env: Env):
    env.settings = MemoryInternetSettings.with_values(detect=1)
    shown = {}
    for answer in ("found", "none", "unknown"):
        env.wpad = MemoryWpadProbe(answer)
        env.out.clear()
        assert env.run(desktop=True) == EXIT_OK
        shown[answer] = [ln for ln in env.out if ln.startswith("  wpad:")]
        assert env.wpad.calls == 1 and env.system_untouched()
    assert len(shown["found"]) == len(shown["none"]) == len(shown["unknown"]) == 1
    assert "FOUND - automatic proxy detection discovered a PAC script" in shown["found"][0]
    assert "routing is REFUSED" in shown["found"][0]
    assert "NONE - automatic proxy detection finds no PAC script" in shown["none"][0]
    assert "counts as direct" in shown["none"][0] and "restores it exactly" in shown["none"][0]
    assert "UNKNOWN - the probe could not tell" in shown["unknown"][0]
    assert "fail safe" in shown["unknown"][0]


def test_the_proxy_line_follows_the_probe_and_none_is_not_refused(env: Env):
    env.settings = MemoryInternetSettings.with_values(detect=1)
    env.wpad = MemoryWpadProbe("none")
    env.run(desktop=True)
    assert "proxy:    no proxy today" in env.text and "REFUSED" not in env.text
    assert "proxy:    automatic" not in env.text
    env.out.clear()
    env.wpad = MemoryWpadProbe("found")
    env.run(desktop=True)
    assert "found a PAC script on this network" in env.text
    env.out.clear()
    env.wpad = MemoryWpadProbe("unknown")
    env.run(desktop=True)
    assert "could not tell whether it finds a PAC script" in env.text


def test_a_plan_does_not_probe_when_auto_detect_is_off(env: Env):
    env.settings = MemoryInternetSettings.with_values(detect=0)
    env.run(desktop=True)
    assert env.wpad.calls == 0 and "  wpad:" not in env.text


def test_the_read_only_wrappers_refuse_every_write(env: Env):
    ro = setup_cmd.read_only(env.backends)
    for call in (
        lambda: ro.certs.install(b"x"),
        lambda: ro.certs.remove("AA"),
        lambda: ro.settings.apply_pac("u"),
        lambda: ro.settings.restore(env.settings.read()),
        lambda: ro.settings.notify(),
    ):
        with pytest.raises(PermissionError):
            call()
    assert ro.settings.read().proxy_enable == 0 and ro.certs.list_ours() == []
    assert env.system_untouched()


# ---------------------------------------------------------------------------------- the guard (R8)
@pytest.fixture()
def cli_env(env: Env, monkeypatch: pytest.MonkeyPatch) -> Env:
    """``cli.main`` with every path in the temp dir and a port nobody uses."""
    monkeypatch.setattr(setup_cmd, "default_paths", lambda repo_root=None: env.paths)
    return env


def test_setup_without_yes_through_the_cli_changes_nothing(cli_env: Env, capsys):
    before = cli_env.tree()
    assert cli.main(["setup"]) == EXIT_OK
    assert "Nothing was changed" in capsys.readouterr().out
    assert cli.main(["setup", "--desktop-apps"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Nothing was changed" in out and "not read" in out  # the guard refused the system reads
    assert cli.main(["setup", "--remove"]) == EXIT_OK
    assert cli.main(["setup", "--status"]) == EXIT_OK
    capsys.readouterr()
    assert cli_env.tree() == before
    assert REAL_SYSTEM_ENV not in os.environ


@pytest.mark.parametrize(
    "extra",
    [
        ["--desktop-apps"],
        ["--desktop-apps", "--register"],
        ["--remove"],
        ["--desktop-apps", "--managed-ok"],
    ],
)
def test_the_routed_flow_under_pytest_is_refused_by_the_guard(cli_env: Env, capsys, extra):
    before = cli_env.tree()
    assert cli.main(["setup", "--yes", *extra]) == EXIT_REFUSED
    out = capsys.readouterr().out
    assert "refused" in out and "never under test" in out and "Nothing was changed" in out
    assert cli_env.tree() == before
    assert not cli_env.paths.home.exists()
    assert REAL_SYSTEM_ENV not in os.environ  # the permission was never left behind


def test_the_permission_is_only_set_while_the_backends_are_built(monkeypatch):
    seen: list[str | None] = []

    def fake_real():
        seen.append(os.environ.get(REAL_SYSTEM_ENV))
        return "backends"

    monkeypatch.setattr(setup_cmd, "real_backends", fake_real)
    assert setup_cmd.open_backends() == ("backends", "")
    assert seen == ["1"] and REAL_SYSTEM_ENV not in os.environ
    monkeypatch.setenv(REAL_SYSTEM_ENV, "0")
    setup_cmd.open_backends()
    assert os.environ[REAL_SYSTEM_ENV] == "0"  # put back exactly as it was


@pytest.mark.parametrize("extra", [["--yes"], ["--desktop-apps"], ["--remove"], ["--register"]])
def test_status_takes_no_other_option(cli_env: Env, capsys, extra):
    before = cli_env.tree()
    assert cli.main(["setup", "--status", *extra]) == EXIT_REFUSED
    assert "read-only" in capsys.readouterr().out
    assert cli_env.tree() == before


def test_setup_never_talks_to_the_system_or_starts_a_process():
    tree = ast.parse(Path(setup_cmd.__file__).read_text(encoding="utf-8"))
    imported = {
        (n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])
    }
    assert not imported & {"winreg", "ctypes", "subprocess", "psutil"}  # R8: backends only
    lines = Path(setup_cmd.__file__).read_text(encoding="utf-8").splitlines()
    code = " ".join(ln for ln in lines if not ln.lstrip().startswith(("#", '"')))
    assert not re.search(r"certutil|schtasks|os\.system|shell=True", code)  # R10: no shell


# ---------------------------------------------------------------------------------- apply: the CA
def test_yes_creates_and_installs_the_ca_and_records_it(env: Env):
    assert env.run(yes=True, desktop=True, managed_ok=False) == EXIT_OK, env.text
    store = CAStore(env.paths.home, crypto=fake_crypto())
    ca = store.load()
    assert ca is not None
    assert env.certs.installed(ca.thumbprint)
    assert env.certs.certs[ca.thumbprint][1] == FRIENDLY_NAME
    record = env.json(env.paths.home / "setup_record.json")
    assert record["ca"]["thumbprint"] == ca.thumbprint
    assert CAInstaller(env.paths.home, env.certs).recorded() == [ca.thumbprint]
    # the proxy settings are not touched by setup (the gateway does that, with a backup)
    assert env.settings.writes == [] and env.settings.notified == 0
    assert "CA created" in env.text and "installed in CurrentUser\\Root" in env.text
    assert "setup --remove --yes" in env.text and "ChatGPT" in env.text


def test_a_second_yes_changes_nothing_more(env: Env):
    assert env.run(yes=True, desktop=True) == EXIT_OK
    installs = [c for c in env.certs.calls if c[0] == "install"]
    config = env.paths.config_file.read_bytes()
    settings = env.settings_file.read_bytes()
    env.out.clear()
    assert env.run(yes=True, desktop=True, register=True) == EXIT_OK
    assert [c for c in env.certs.calls if c[0] == "install"] == installs  # not installed twice
    assert "was already installed" in env.text
    assert env.paths.config_file.read_bytes() == config
    assert env.settings_file.read_bytes() == settings  # the hooks are not added twice


def test_a_declined_windows_dialog_leaves_nothing_behind(env: Env):
    env.certs = MemoryCertStore(decline=True)
    assert env.run(yes=True, desktop=True) == EXIT_FAILED
    assert "answered No" in env.text and "Nothing else was changed" in env.text
    assert not env.paths.config_file.exists() and not env.settings_file.exists()
    assert not (env.paths.home / "setup_record.json").exists()
    assert env.certs.certs == {}


def test_an_unfit_ca_is_rotated_and_the_old_one_removed(env: Env):
    old_store = CAStore(env.paths.home, crypto=fake_crypto(), domains=("example.com",))
    old = old_store.ensure()
    installer = CAInstaller(env.paths.home, env.certs)
    installer.install(old)
    assert env.run(yes=True, desktop=True) == EXIT_OK, env.text
    new = CAStore(env.paths.home, crypto=fake_crypto()).load()
    assert new is not None and new.thumbprint != old.thumbprint
    assert env.certs.list_ours() == [new.thumbprint]  # the old one is gone, never two
    assert "unfit" in env.text


# ---------------------------------------------------------------------------------- apply: refusals
@pytest.mark.parametrize(
    ("settings", "word"),
    [
        (MemoryInternetSettings.with_values(locked=("ie_policy_locks_proxy",)), "locked by policy"),
        (MemoryInternetSettings.with_values(detect=1), "WPAD"),
    ],
)
def test_locked_or_wpad_proxy_settings_refuse_before_anything_changes(env: Env, settings, word):
    env.settings = settings
    before = env.tree()
    assert env.run(yes=True, desktop=True, register=True, managed_ok=True) == EXIT_REFUSED
    assert word in env.text and "Nothing was changed" in env.text
    assert env.tree() == before and env.certs.calls == [] and env.settings.writes == []


def test_wpad_that_finds_nothing_lets_setup_go_ahead(env: Env):
    env.settings = MemoryInternetSettings.with_values(detect=1)
    env.wpad = MemoryWpadProbe("none")
    assert env.run(yes=True, desktop=True) == EXIT_OK, env.text
    assert "wpad: NONE" in env.text and len(env.certs.certs) == 1
    assert (
        env.settings.writes == []
    )  # setup never touches the proxy; the gateway does, with a backup


@pytest.mark.parametrize("answer", ["found", "unknown"])
def test_wpad_found_or_unknown_refuses_with_a_clear_reason(env: Env, answer):
    env.settings = MemoryInternetSettings.with_values(detect=1)
    env.wpad = MemoryWpadProbe(answer)
    before = env.tree()
    assert env.run(yes=True, desktop=True, managed_ok=True) == EXIT_REFUSED
    expected = "found a PAC script" if answer == "found" else "could not tell"
    assert expected in env.text and "Nothing was changed" in env.text
    assert env.tree() == before and env.certs.calls == []


def test_a_missing_probe_refuses_auto_detect_like_before(env: Env):
    env.settings = MemoryInternetSettings.with_values(detect=1)
    backends = Backends(env.certs, env.settings, fake_crypto(), env.managed, None)
    assert env.run(backends, yes=True, desktop=True, managed_ok=True) == EXIT_REFUSED
    assert "WPAD" in env.text and env.certs.calls == []


def test_the_read_only_wrapper_keeps_the_probe(env: Env):
    assert setup_cmd.read_only(env.backends).wpad is env.wpad


def test_a_managed_pc_needs_managed_ok(env: Env):
    env.managed = MemoryManagedProbe(("intune_enrollment", "defender_for_endpoint"))
    before = env.tree()
    assert env.run(yes=True, desktop=True) == EXIT_MANAGED
    assert (
        re.search("clear the certificate with IT first", env.text, re.I)
        and "--managed-ok" in env.text
    )
    assert env.tree() == before and env.certs.calls == []
    env.out.clear()
    assert env.run(yes=True, desktop=True, managed_ok=True) == EXIT_OK
    assert "managed device acknowledged" in env.text and len(env.certs.certs) == 1


def test_an_unavailable_managed_check_is_treated_like_a_managed_pc(env: Env):
    backends = Backends(env.certs, env.settings, fake_crypto(), None)
    assert env.run(backends, yes=True, desktop=True) == EXIT_MANAGED
    assert "could not run" in env.text and env.certs.calls == []


def test_the_default_install_has_no_managed_or_proxy_check_and_no_ca(env: Env):
    env.managed = MemoryManagedProbe(("intune_enrollment", "defender_for_endpoint"))
    env.settings = MemoryInternetSettings.with_values(locked=("HKLM_policy_sets_proxy",))
    assert env.run(yes=True) == EXIT_OK, env.text  # would be EXIT_REFUSED / EXIT_MANAGED if routed
    assert env.certs.calls == [] and env.settings.writes == [] and env.settings.notified == 0
    assert CAStore(env.paths.home, crypto=fake_crypto()).load() is None


@pytest.mark.parametrize("desktop", [False, True])
def test_an_unreadable_local_config_is_never_overwritten(env: Env, desktop: bool):
    env.paths.config_file.write_text("{not json", encoding="utf-8")
    before = env.tree()
    assert env.run(yes=True, desktop=desktop) == EXIT_REFUSED
    assert env.paths.config_file.read_text(encoding="utf-8") == "{not json"
    assert env.certs.calls == [] and env.tree() == before  # no hooks written either


# ---------------------------------------------------------------------------------- apply: config
def test_the_config_is_merged_and_backed_up(env: Env):
    env.paths.config_file.write_text(
        json.dumps({"server": {"x": 1}, "client_gateway": {"port": 8899, "routing": False}}),
        encoding="utf-8",
    )
    original = env.paths.config_file.read_bytes()
    assert env.run(yes=True, desktop=True) == EXIT_OK
    data = env.json(env.paths.config_file)
    assert data["client_gateway"] == {"port": 8899, "routing": True, "mcp_masking": True}
    assert data["server"] == {"x": 1}
    backups = list(env.paths.config_file.parent.glob("local_config.json.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_a_missing_config_is_created_without_a_backup(env: Env):
    assert not env.paths.config_file.exists()
    assert env.run(yes=True, desktop=True) == EXIT_OK
    assert env.json(env.paths.config_file) == {
        "client_gateway": {"routing": True, "mcp_masking": True}
    }
    assert list(env.paths.config_file.parent.glob("*.bak-*")) == []
    assert "new file" in env.text


# ---------------------------------------------------------------------------------- apply: on demand
def _our_hooks(env: Env) -> dict[str, list[Any]]:
    return env.json(env.settings_file)["hooks"]


def test_the_default_install_writes_the_config_the_hooks_and_a_record(env: Env):
    """backends None = ``open_backends()`` failed: the on-demand install needs none of them."""
    env.settings_file.parent.mkdir(parents=True)
    env.settings_file.write_text(
        json.dumps(
            {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}
        ),
        encoding="utf-8",
    )
    original = env.settings_file.read_bytes()
    env.paths.config_file.write_text(
        json.dumps({"server": {"x": 1}, "client_gateway": {"port": 8899, "routing": True}}),
        encoding="utf-8",
    )
    config_before = env.paths.config_file.read_bytes()
    assert env.run_no_backends(yes=True) == EXIT_OK, env.text
    config = env.json(env.paths.config_file)
    assert config["client_gateway"] == {"port": 8899, "routing": False, "mcp_masking": True}
    assert config["server"] == {"x": 1}
    settings = env.json(env.settings_file)
    assert settings["model"] == "opus"
    assert settings["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "x"}]}]
    for event in HOOKS:
        assert settings["hooks"][event] == [env.hook_group(event)]
    # the exact shapes of a real install (forward slashes, quoted python and script, event word)
    session = settings["hooks"]["SessionStart"][0]
    assert (
        list(session) == ["matcher", "hooks"]
        and session["matcher"] == "startup|resume|clear|compact"
    )
    assert "matcher" not in settings["hooks"]["UserPromptSubmit"][0]
    assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] == 30
    assert "\\" not in session["hooks"][0]["command"]
    assert session["hooks"][0]["command"].endswith('client_masking_hook.py" session')
    # backups of both files, byte for byte
    (cfg_bak,) = env.paths.config_file.parent.glob("local_config.json.bak-*")
    assert cfg_bak.read_bytes() == config_before
    (set_bak,) = env.settings_file.parent.glob("settings.json.bak-*")
    assert set_bak.read_bytes() == original
    # the record holds the previous config values and every added group
    record = env.json(env.paths.home / "setup_record.json")
    assert record["config"]["previous"] == {
        "client_gateway.mcp_masking": None,
        "client_gateway.routing": True,
    }
    assert record["hooks"]["file"] == str(env.settings_file)
    assert [(h["event"], h["group"]) for h in record["hooks"]["added"]] == [
        (event, env.hook_group(event)) for event in HOOKS
    ]
    assert "ca" not in record and not (env.paths.home / "ca").exists()
    assert "masking on" in env.text and "--desktop-apps" in env.text and "Undo with" in env.text


def test_the_default_install_without_any_file_creates_them(env: Env):
    assert not env.settings_file.exists() and not env.paths.config_file.exists()
    assert env.run(yes=True) == EXIT_OK, env.text
    assert env.json(env.paths.config_file) == {
        "client_gateway": {"mcp_masking": True, "routing": False}
    }
    assert list(_our_hooks(env)) == ["SessionStart", "UserPromptSubmit", "PreToolUse"]
    assert list(env.settings_file.parent.glob("*.bak-*")) == []  # a new file: nothing to back up
    assert "new file" in env.text


def test_the_hook_runs_with_the_repo_venv_python_when_it_exists(env: Env):
    python = env.paths.repo_root / "venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    assert env.run(yes=True) == EXIT_OK
    command = _our_hooks(env)["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert command.startswith(f'"{python.as_posix()}" "')
    assert command.endswith('/hooks/client_masking_hook.py" prompt')


def test_a_second_default_install_adds_nothing(env: Env):
    assert env.run(yes=True) == EXIT_OK
    settings = env.settings_file.read_bytes()
    record = env.json(env.paths.home / "setup_record.json")["hooks"]["added"]
    env.out.clear()
    assert env.run(yes=True) == EXIT_OK
    assert env.settings_file.read_bytes() == settings
    assert len(list(env.settings_file.parent.glob("settings.json.bak-*"))) == 0  # nothing rewritten
    assert env.json(env.paths.home / "setup_record.json")["hooks"]["added"] == record
    assert env.text.count("already installed, left alone") == 3


def test_an_existing_hook_of_ours_with_a_lower_timeout_is_raised_not_duplicated(env: Env):
    env.settings_file.parent.mkdir(parents=True)
    mine = {
        "hooks": [
            {
                "type": "command",
                "command": '"C:/old/python.exe" "D:/elsewhere/hooks/client_masking_hook.py" prompt',
                "timeout": 5,
            }
        ]
    }
    other = {"hooks": [{"type": "command", "command": "keep-me", "timeout": 1}]}
    longer = {
        "hooks": [
            {
                "type": "command",
                "command": '"p" "q/client_masking_hook.py" pretool',
                "timeout": 999,
            }
        ]
    }
    env.settings_file.write_text(
        json.dumps({"hooks": {"UserPromptSubmit": [other, mine], "PreToolUse": [longer]}}),
        encoding="utf-8",
    )
    assert env.run(yes=True) == EXIT_OK, env.text
    hooks = _our_hooks(env)
    assert len(hooks["UserPromptSubmit"]) == 2 and hooks["UserPromptSubmit"][0] == other
    raised = hooks["UserPromptSubmit"][1]["hooks"][0]
    assert raised["timeout"] == 120 and raised["command"].startswith('"C:/old/python.exe"')
    assert hooks["PreToolUse"] == [longer]  # a higher timeout stays
    assert hooks["SessionStart"] == [env.hook_group("SessionStart")]
    assert "timeout raised from 5 to 120" in env.text
    added = env.json(env.paths.home / "setup_record.json")["hooks"]["added"]
    assert [h["event"] for h in added] == ["SessionStart"]  # only what setup ADDED is recorded


def test_an_event_word_that_is_not_ours_does_not_count_as_installed(env: Env):
    env.settings_file.parent.mkdir(parents=True)
    other_word = {
        "hooks": [{"type": "command", "command": '"p" "q/client_masking_hook.py" session'}]
    }
    env.settings_file.write_text(
        json.dumps({"hooks": {"PreToolUse": [other_word]}}), encoding="utf-8"
    )
    assert env.run(yes=True) == EXIT_OK
    pre = _our_hooks(env)["PreToolUse"]
    assert len(pre) == 2 and pre[0] == other_word and pre[1] == env.hook_group("PreToolUse")


def test_an_unreadable_claude_settings_file_is_never_overwritten(env: Env):
    env.settings_file.parent.mkdir(parents=True)
    env.settings_file.write_text("{not json", encoding="utf-8")
    assert env.run(yes=True) == EXIT_FAILED
    assert env.settings_file.read_text(encoding="utf-8") == "{not json"
    assert "FAILED" in env.text and "the hooks were not installed" in env.text
    assert list(env.settings_file.parent.glob("*.bak-*")) == []
    # the config step before it still happened
    assert env.json(env.paths.config_file)["client_gateway"]["mcp_masking"] is True


def test_a_hooks_value_of_the_wrong_type_is_left_alone(env: Env):
    env.settings_file.parent.mkdir(parents=True)
    env.settings_file.write_text(json.dumps({"hooks": {"PreToolUse": "oops"}}), encoding="utf-8")
    before = env.settings_file.read_bytes()
    assert env.run(yes=True) == EXIT_FAILED
    assert env.settings_file.read_bytes() == before and "is not a list" in env.text


def test_without_a_claude_settings_path_the_hooks_are_skipped(env: Env):
    paths = setup_cmd.SetupPaths(
        env.paths.home,
        env.paths.config_file,
        env.paths.repo_root,
        env.paths.claude_desktop,
        env.paths.vscode,
        env.paths.claude_code,
    )
    assert paths.claude_settings is None
    env.paths = paths
    assert env.run(yes=True) == EXIT_OK
    assert "no settings file known, skipped" in env.text
    assert not (env.tmp / "user" / ".claude").exists()


def test_the_default_paths_point_at_the_claude_code_user_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert setup_cmd.default_paths(tmp_path).claude_settings == tmp_path / "cfg" / "settings.json"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert (
        setup_cmd.default_paths(tmp_path).claude_settings
        == Path.home() / ".claude" / "settings.json"
    )


def test_an_old_every_chat_routed_install_is_switched_back_to_direct(env: Env):
    folders.route_all(env.paths.home)
    assert folders.routes_all()
    other_hook = {"hooks": [{"type": "command", "command": "keep-me"}]}
    settings = env.json(env.settings_file)
    settings["hooks"] = {"Stop": [other_hook]}
    env.settings_file.write_text(json.dumps(settings), encoding="utf-8")
    env.out.clear()
    assert env.run(yes=True) == EXIT_OK, env.text
    assert not folders.routes_all()
    after = env.json(env.settings_file)
    assert after["env"]["ANTHROPIC_BASE_URL"] == folders.DIRECT_URL  # written back, never deleted
    assert after["hooks"]["Stop"] == [other_hook]
    assert [e for e in HOOKS if after["hooks"].get(e)] == list(
        HOOKS
    )  # the hooks survived the migration
    assert "every chat was routed through the gateway" in env.text and "direct" in env.text


def test_the_plan_and_status_say_when_every_chat_is_routed(env: Env):
    folders.route_all(env.paths.home)
    assert env.run() == EXIT_OK
    assert "every chat routed (old install): YES, would be switched back to direct" in env.text
    env.out.clear()
    assert env.run(status=True) == EXIT_OK
    assert "every chat routed (old install): YES" in env.text


def test_the_desktop_flow_keeps_the_old_steps_and_also_installs_the_hooks(env: Env):
    folders.route_all(env.paths.home)  # --desktop-apps does not migrate it
    assert env.run(yes=True, desktop=True) == EXIT_OK, env.text
    assert env.json(env.paths.config_file)["client_gateway"] == {
        "routing": True,
        "mcp_masking": True,
    }
    assert len(env.certs.certs) == 1
    assert list(_our_hooks(env)) == list(HOOKS)
    assert folders.routes_all()
    assert "Copilot and ChatGPT cannot start it" in env.text and "masking on" in env.text


def test_desktop_apps_and_remove_are_refused_without_backends_but_the_default_is_not(env: Env):
    before = env.tree()
    assert env.run_no_backends(yes=True, desktop=True) == EXIT_REFUSED
    assert env.run_no_backends(yes=True, remove=True) == EXIT_REFUSED
    assert env.tree() == before and "no system access" in env.text
    assert env.run_no_backends(yes=True) == EXIT_OK
    assert env.tree() != before


def test_main_runs_the_default_without_opening_the_system_backends(
    env: Env, monkeypatch: pytest.MonkeyPatch
):
    def forbidden():
        raise AssertionError("the on-demand install opened the system backends")

    monkeypatch.setattr(setup_cmd, "open_backends", forbidden)
    monkeypatch.setattr(setup_cmd, "default_paths", lambda repo_root=None: env.paths)
    assert cli.main(["setup", "--yes"]) == EXIT_OK
    assert list(_our_hooks(env)) == list(HOOKS)
    assert env.json(env.paths.config_file)["client_gateway"]["routing"] is False
    assert REAL_SYSTEM_ENV not in os.environ


# ---------------------------------------------------------------------------------- apply: register
def test_register_adds_the_mcp_to_detected_clients_with_backups(env: Env):
    desktop_before = {"theme": "dark"}
    env.paths.claude_desktop.write_text(json.dumps(desktop_before), encoding="utf-8")
    assert env.run(yes=True, register=True) == EXIT_OK, env.text
    desktop = env.json(env.paths.claude_desktop)
    assert desktop["theme"] == "dark"
    entry = desktop["mcpServers"][SERVER_NAME]
    assert entry["args"][0].endswith("run_server.py") and entry["env"] == {
        "PYTHONIOENCODING": "utf-8"
    }
    assert "type" not in entry  # Claude Desktop's format has no type
    vscode = env.json(env.paths.vscode)
    assert vscode["servers"][SERVER_NAME]["type"] == "stdio"  # VS Code: "servers", typed
    code = env.json(env.paths.claude_code)
    assert code["numStartups"] == 7 and "other" in code["mcpServers"]
    assert code["mcpServers"][SERVER_NAME]["type"] == "stdio"
    assert list(env.paths.claude_desktop.parent.glob("*.bak-*"))  # the file existed: backed up
    assert list(env.paths.claude_code.parent.glob(".claude.json.bak-*"))
    assert not list(env.paths.vscode.parent.glob("*.bak-*"))  # a new file: nothing to back up
    record = env.json(env.paths.home / "setup_record.json")
    assert {r["client"] for r in record["registrations"]} == {
        "claude_desktop",
        "vscode",
        "claude_code",
    }


def test_register_never_overwrites_an_existing_entry_and_skips_what_it_cannot_read(env: Env):
    mine = {"command": "my-own", "args": ["--keep"]}
    env.paths.claude_desktop.write_text(
        json.dumps({"mcpServers": {SERVER_NAME: mine}}), encoding="utf-8"
    )
    env.paths.vscode.write_text("{broken", encoding="utf-8")
    assert env.run(yes=True, register=True) == EXIT_OK
    assert env.json(env.paths.claude_desktop)["mcpServers"][SERVER_NAME] == mine
    assert env.paths.vscode.read_text(encoding="utf-8") == "{broken"
    assert "already registered, left alone" in env.text and "cannot be read" in env.text
    record = env.json(env.paths.home / "setup_record.json")
    assert [r["client"] for r in record["registrations"]] == ["claude_code"]


def test_an_undetected_client_is_skipped(env: Env):
    env.paths.claude_code.unlink()
    (env.paths.vscode.parent).rmdir()
    assert env.run(yes=True, register=True) == EXIT_OK
    assert not env.paths.claude_code.exists() and not env.paths.vscode.exists()
    assert "VS Code: not detected, skipped" in env.text
    record = env.json(env.paths.home / "setup_record.json")
    assert [r["client"] for r in record["registrations"]] == ["claude_desktop"]


# ---------------------------------------------------------------------------------- status
def test_status_reports_the_claude_code_hooks(env: Env):
    assert env.run(status=True) == EXIT_OK
    assert "claude code hooks: missing (SessionStart, UserPromptSubmit, PreToolUse)" in env.text
    env.out.clear()
    assert env.run(yes=True) == EXIT_OK
    env.out.clear()
    assert env.run(status=True) == EXIT_OK
    assert "claude code hooks: installed" in env.text
    assert "routing flag:  False" in env.text and "mcp_masking flag: True" in env.text
    # one hook gone: missing, and only that one
    hooks = env.json(env.settings_file)
    del hooks["hooks"]["PreToolUse"]
    env.settings_file.write_text(json.dumps(hooks), encoding="utf-8")
    env.out.clear()
    assert env.run(status=True) == EXIT_OK
    assert "claude code hooks: missing (PreToolUse)" in env.text


def test_status_reports_what_is_installed_and_changes_nothing(env: Env):
    env.run(yes=True, desktop=True, register=True)
    ca = CAStore(env.paths.home, crypto=fake_crypto()).load()
    assert ca is not None
    env.out.clear()
    before = env.tree()
    calls = list(env.certs.calls)
    assert env.run(status=True) == EXIT_OK
    text = env.text
    assert ca.thumbprint in text and "trusted in CurrentUser" in text
    assert "claude code hooks: installed" in text
    assert "routing flag:  True" in text and "mcp_masking flag: True" in text
    assert "registered (added by setup)" in text
    assert "profile:" in text and "gateway:       not running" in text
    assert "coverage:" in text and "Claude Desktop" in text and "pending" in text
    assert "tested app versions:" in text and "none recorded yet" in text
    assert env.tree() == before and env.certs.calls == calls


def test_status_shows_a_running_gateways_routing_state(env: Env):
    gateway = {"version": "9.9", "routing": {"enabled": False, "reason": "ca_not_trusted"}}
    gateway["warnings"] = ["routing_inactive:ca_not_trusted"]
    env.run(status=True, gateway=gateway)
    assert "running (version 9.9)" in env.text and "NOT enabled (ca_not_trusted)" in env.text
    assert "routing_inactive:ca_not_trusted" in env.text


# ---------------------------------------------------------------------------------- remove
def _installed(env: Env) -> tuple[str, bytes]:
    env.paths.config_file.write_text(
        json.dumps({"keep": 1, "client_gateway": {"port": 8899}}), encoding="utf-8"
    )
    assert env.run(yes=True, desktop=True, register=True) == EXIT_OK
    ca = CAStore(env.paths.home, crypto=fake_crypto()).load()
    assert ca is not None
    env.out.clear()
    return ca.thumbprint, env.paths.claude_desktop.read_bytes()


def test_remove_undoes_exactly_what_setup_did(env: Env):
    thumb, _ = _installed(env)
    foreign = env.certs.add_foreign(b"someone elses certificate", "Somebody else")
    ours_unrecorded = env.certs.add_foreign(b"our name but never recorded", FRIENDLY_NAME)
    assert env.run(yes=True, remove=True) == EXIT_OK, env.text
    assert not env.certs.installed(thumb)  # our CA, by its recorded thumbprint
    assert env.certs.installed(foreign) and env.certs.installed(ours_unrecorded)
    assert "hooks" not in env.json(
        env.settings_file
    )  # setup's three groups, nothing else was there
    assert "removed from" in env.text and list(env.settings_file.parent.glob("settings.json.bak-*"))
    config = env.json(env.paths.config_file)
    assert config["client_gateway"] == {"port": 8899, "routing": False, "mcp_masking": False}
    assert config["keep"] == 1
    assert SERVER_NAME not in env.json(env.paths.claude_desktop).get("mcpServers", {})
    assert SERVER_NAME not in env.json(env.paths.vscode).get("servers", {})
    code = env.json(env.paths.claude_code)
    assert SERVER_NAME not in code["mcpServers"] and "other" in code["mcpServers"]
    assert code["numStartups"] == 7
    assert not (env.paths.home / "setup_record.json").exists()
    assert list(env.paths.config_file.parent.glob("local_config.json.bak-*"))  # backups are kept
    assert "Every backup was kept" in env.text


def test_remove_leaves_a_registration_the_user_changed(env: Env):
    _installed(env)
    desktop = env.json(env.paths.claude_desktop)
    desktop["mcpServers"][SERVER_NAME]["args"].append("--edited-by-user")
    env.paths.claude_desktop.write_text(json.dumps(desktop), encoding="utf-8")
    assert env.run(yes=True, remove=True) == EXIT_OK
    assert SERVER_NAME in env.json(env.paths.claude_desktop)["mcpServers"]
    assert "changed since setup, left alone" in env.text
    record = env.json(env.paths.home / "setup_record.json")
    assert [r["client"] for r in record["registrations"]] == ["claude_desktop"]


def test_remove_is_refused_while_a_gateway_runs(env: Env):
    _installed(env)
    before = env.tree()
    calls = list(env.certs.calls)
    assert env.run(yes=True, remove=True, gateway={"ok": True}) == EXIT_REFUSED
    assert "Stop it first" in env.text and "Nothing was changed" in env.text
    assert env.tree() == before and env.certs.calls == calls


def test_remove_restores_the_proxy_backup_exactly(env: Env):
    env.settings = MemoryInternetSettings.with_values(
        enable=1, server="corp:8080", override="<local>", detect=0
    )
    original = env.settings.read()
    _installed(env)
    # a gateway that crashed while its rule was set: our PAC in force, the backup on disk
    InternetSettings(env.paths.home, env.settings).enable(PAC_URL)
    assert env.settings.read().auto_config_url == PAC_URL
    assert env.run(yes=True, remove=True) == EXIT_OK, env.text
    assert env.settings.read().same_values(original)
    assert not InternetSettings(env.paths.home, env.settings).backup_exists()
    assert "restored exactly" in env.text


def test_remove_does_not_touch_settings_that_are_not_our_rule(env: Env):
    _installed(env)
    InternetSettings(env.paths.home, env.settings).enable(PAC_URL)
    env.settings.values["AutoConfigURL"] = None  # the user changed it since
    env.settings.values["ProxyServer"] = setup_value("elsewhere:3128")
    before = dict(env.settings.values)
    writes = list(env.settings.writes)
    assert env.run(yes=True, remove=True) == EXIT_OK
    assert env.settings.values == before and env.settings.writes == writes
    assert "proxy backup kept" in env.text
    assert InternetSettings(env.paths.home, env.settings).backup_exists()


def setup_value(data: str):
    from client_gateway.routing.winproxy import REG_SZ, RegValue

    return RegValue(REG_SZ, data)


def test_remove_with_nothing_installed_is_harmless(env: Env):
    before = env.tree()
    assert env.run(yes=True, remove=True) == EXIT_OK
    assert "CA: none recorded" in env.text and "no backup" in env.text
    assert env.tree() == before and env.system_untouched()


def test_the_remove_plan_lists_the_recorded_pieces(env: Env):
    thumb, _ = _installed(env)
    before = env.tree()
    assert env.run(remove=True) == EXIT_OK
    assert thumb in env.text and "claude_desktop" in env.text and "stop first" in env.text
    assert (
        "Nothing was changed. Apply with: python -m client_gateway setup --remove --yes" in env.text
    )
    assert env.tree() == before
    assert "Claude Code hook groups" in env.text and "SessionStart, UserPromptSubmit" in env.text


def test_remove_of_a_default_install_takes_out_the_hooks_and_resets_the_config(env: Env):
    other = {"hooks": [{"type": "command", "command": "keep-me"}]}
    env.settings_file.parent.mkdir(parents=True)
    env.settings_file.write_text(
        json.dumps({"model": "opus", "hooks": {"Stop": [other], "PreToolUse": [other]}}),
        encoding="utf-8",
    )
    assert env.run(yes=True) == EXIT_OK
    assert len(_our_hooks(env)["PreToolUse"]) == 2
    assert env.run(yes=True, remove=True) == EXIT_OK, env.text
    after = env.json(env.settings_file)
    assert after == {"model": "opus", "hooks": {"Stop": [other], "PreToolUse": [other]}}
    assert env.json(env.paths.config_file)["client_gateway"] == {
        "mcp_masking": False,
        "routing": False,
    }
    assert not (env.paths.home / "setup_record.json").exists()
    assert "Every backup was kept" in env.text


def test_remove_leaves_a_hook_group_the_user_changed_and_keeps_it_recorded(env: Env):
    assert env.run(yes=True) == EXIT_OK
    settings = env.json(env.settings_file)
    settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["timeout"] = 7  # edited by the user
    env.settings_file.write_text(json.dumps(settings), encoding="utf-8")
    env.out.clear()
    assert env.run(yes=True, remove=True) == EXIT_OK, env.text
    hooks = _our_hooks(env)
    assert (
        list(hooks) == ["UserPromptSubmit"]
        and hooks["UserPromptSubmit"][0]["hooks"][0]["timeout"] == 7
    )
    assert "hook UserPromptSubmit: changed since setup, left alone" in env.text
    record = env.json(env.paths.home / "setup_record.json")
    assert [h["event"] for h in record["hooks"]["added"]] == ["UserPromptSubmit"]
    assert env.json(env.paths.config_file)["client_gateway"]["mcp_masking"] is False


def test_remove_forgets_a_hook_group_that_is_already_gone(env: Env):
    assert env.run(yes=True) == EXIT_OK
    settings = env.json(env.settings_file)
    del settings["hooks"]["SessionStart"]
    env.settings_file.write_text(json.dumps(settings), encoding="utf-8")
    env.out.clear()
    assert env.run(yes=True, remove=True) == EXIT_OK
    assert "hook SessionStart: already gone" in env.text
    assert "hooks" not in env.json(env.settings_file)
    assert not (env.paths.home / "setup_record.json").exists()


def test_remove_does_not_touch_an_unreadable_settings_file(env: Env):
    assert env.run(yes=True) == EXIT_OK
    env.settings_file.write_text("{broken", encoding="utf-8")
    assert env.run(yes=True, remove=True) == EXIT_OK
    assert env.settings_file.read_text(encoding="utf-8") == "{broken"
    assert "cannot be read" in env.text
    record = env.json(env.paths.home / "setup_record.json")
    assert len(record["hooks"]["added"]) == 3  # still recorded for a later --remove


def test_remove_is_refused_while_a_gateway_runs_and_keeps_the_hooks(env: Env):
    assert env.run(yes=True) == EXIT_OK
    before = env.tree()
    assert env.run(yes=True, remove=True, gateway={"ok": True}) == EXIT_REFUSED
    assert "Stop it first" in env.text and env.tree() == before
