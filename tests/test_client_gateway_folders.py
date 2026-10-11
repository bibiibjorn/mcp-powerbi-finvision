"""Routed folders: settings.local.json merge, git exclusion without tracked changes, registry."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from client_gateway import folders
from client_gateway.control_client import gateway_url

URL = "http://127.0.0.1:8890"
# unrouting writes the direct address, never deletes the key: a running Claude Code session keeps
# a removed key until a relaunch and would go on talking to a stopped gateway (2026-10-08)
DIRECT = {"ANTHROPIC_BASE_URL": folders.DIRECT_URL}
PRIVACY_ENV = {
    "DISABLE_FEEDBACK_COMMAND": "1",
    "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1",
}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FINVISION_MASKING_HOME", str(home))
    monkeypatch.delenv("FINVISION_GATEWAY_PORT", raising=False)
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    claude = tmp_path / "claude-config"  # never the developer's own ~/.claude/settings.json
    claude.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    return home


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.org")
    _git(path, "config", "user.name", "t")
    # the developer's own global ignore file must not hide what these tests look at
    empty = path.parent / (path.name + ".noexcludes")
    empty.write_text("", encoding="utf-8")
    _git(path, "config", "core.excludesFile", str(empty).replace("\\", "/"))
    return path


def _settings(folder: Path) -> dict:
    return json.loads((folder / ".claude" / "settings.local.json").read_text(encoding="utf-8"))


def _ignored(folder: Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(folder), "check-ignore", "-q", "--", ".claude/settings.local.json"]
    )
    return proc.returncode == 0


def test_gateway_url_default():
    assert gateway_url() == URL


def test_route_creates_settings_and_excludes_in_git(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "init")
    out = folders.route_folder(repo)
    assert _settings(repo) == {
        "env": {"ANTHROPIC_BASE_URL": URL, **PRIVACY_ENV},
        "skipWebFetchPreflight": True,
    }
    assert out["base_url"] == URL
    assert out["git"]["exclude_added"] == ".claude/settings.local.json"
    assert out["git"]["ignored"] is True
    assert _ignored(repo)
    assert ".claude/settings.local.json" in (repo / ".git" / "info" / "exclude").read_text()
    # the working tree shows no change at all: no tracked file touched, new file ignored
    assert _git(repo, "status", "--porcelain").strip() == ""


def test_route_preserves_other_keys_and_other_env(tmp_path):
    folder = tmp_path / "plain"
    (folder / ".claude").mkdir(parents=True)
    (folder / ".claude" / "settings.local.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(ls)"]}, "env": {"FOO": "1"}}),
        encoding="utf-8",
    )
    out = folders.route_folder(folder)
    assert out["git"] == {"git": False}
    assert _settings(folder) == {
        "permissions": {"allow": ["Bash(ls)"]},
        "env": {"FOO": "1", "ANTHROPIC_BASE_URL": URL, **PRIVACY_ENV},
        "skipWebFetchPreflight": True,
    }


def test_route_is_idempotent_and_keeps_one_exclude_line(tmp_path):
    repo = _repo(tmp_path / "repo")
    folders.route_folder(repo)
    folders.route_folder(repo)
    exclude = (repo / ".git" / "info" / "exclude").read_text().splitlines()
    assert exclude.count(".claude/settings.local.json") == 1
    assert len(folders.list_folders()) == 1


def test_already_ignored_by_gitignore_leaves_exclude_alone(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / ".gitignore").write_text(".claude/settings.local.json\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore")
    before = (repo / ".git" / "info" / "exclude").read_text()
    out = folders.route_folder(repo)
    assert out["git"]["exclude_added"] is None
    assert out["git"]["ignored"] is True
    assert (repo / ".git" / "info" / "exclude").read_text() == before


def test_subfolder_of_repo_gets_a_root_relative_exclude_line(tmp_path):
    repo = _repo(tmp_path / "repo")
    sub = repo / "G01-Clients"
    sub.mkdir()
    out = folders.route_folder(sub)
    assert out["git"]["exclude_added"] == "G01-Clients/.claude/settings.local.json"
    assert out["git"]["ignored"] is True
    assert _git(repo, "status", "--porcelain").strip() == ""


def test_tracked_settings_file_is_reported(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.local.json").write_text("{}", encoding="utf-8")
    _git(repo, "add", "-f", ".claude/settings.local.json")
    _git(repo, "commit", "-q", "-m", "oops")
    out = folders.route_folder(repo)
    assert out["git"]["tracked"] is True
    assert "tracked by git" in out["git"]["warning"]


def test_crlf_file_stays_crlf(tmp_path):
    folder = tmp_path / "crlf"
    (folder / ".claude").mkdir(parents=True)
    path = folder / ".claude" / "settings.local.json"
    path.write_bytes(b'{\r\n  "a": 1\r\n}\r\n')
    folders.route_folder(folder)
    raw = path.read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert json.loads(raw)["a"] == 1


def test_invalid_json_is_never_overwritten(tmp_path):
    folder = tmp_path / "bad"
    (folder / ".claude").mkdir(parents=True)
    path = folder / ".claude" / "settings.local.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(folders.FolderError):
        folders.route_folder(folder)
    assert path.read_text(encoding="utf-8") == "{ not json"
    assert folders.list_folders() == []


def test_missing_or_blank_folder_is_an_error(tmp_path):
    with pytest.raises(folders.FolderError):
        folders.route_folder(tmp_path / "nope")
    with pytest.raises(folders.FolderError):
        folders.route_folder("")
    with pytest.raises(folders.FolderError):
        folders.route_folder(None)


def test_unroute_removes_only_our_key(tmp_path):
    folder = tmp_path / "f"
    (folder / ".claude").mkdir(parents=True)
    (folder / ".claude" / "settings.local.json").write_text(
        json.dumps({"theme": "dark", "env": {"FOO": "1"}}), encoding="utf-8"
    )
    folders.route_folder(folder)
    out = folders.unroute_folder(folder)
    assert out["unrouted"] is True and out["settings_removed"] is False
    assert _settings(folder) == {"theme": "dark", "env": {"FOO": "1", **DIRECT}}
    assert folders.list_folders() == []


def test_unroute_parks_the_direct_address_in_a_file_we_created(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    folders.route_folder(folder)
    out = folders.unroute_folder(folder)
    assert out["unrouted"] is True and out["base_url"] == folders.DIRECT_URL
    assert _settings(folder) == {"env": DIRECT}


def test_unroute_parks_the_project_or_user_base_url_when_one_is_set(tmp_path, monkeypatch):
    folder = tmp_path / "f"
    (folder / ".claude").mkdir(parents=True)
    (folder / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://corp-proxy.example.org"}}),
        encoding="utf-8",
    )
    folders.route_folder(folder)
    folders.unroute_folder(folder)
    assert _settings(folder)["env"]["ANTHROPIC_BASE_URL"] == "https://corp-proxy.example.org"


def test_unroute_keeps_an_empty_file_we_did_not_create(tmp_path):
    folder = tmp_path / "f"
    (folder / ".claude").mkdir(parents=True)
    path = folder / ".claude" / "settings.local.json"
    path.write_text("{}", encoding="utf-8")
    folders.route_folder(folder)
    folders.unroute_folder(folder)
    assert path.is_file() and json.loads(path.read_text(encoding="utf-8")) == {"env": DIRECT}


def test_unroute_restores_a_replaced_base_url(tmp_path):
    folder = tmp_path / "f"
    (folder / ".claude").mkdir(parents=True)
    path = folder / ".claude" / "settings.local.json"
    path.write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://proxy.example.org"}}),
        encoding="utf-8",
    )
    out = folders.route_folder(folder)
    assert out["replaced_base_url"] is True
    folders.route_folder(folder)  # a second call must not forget the original
    out = folders.unroute_folder(folder)
    assert out["restored_base_url"] is True
    assert _settings(folder)["env"]["ANTHROPIC_BASE_URL"] == "https://proxy.example.org"


def test_unroute_leaves_a_foreign_unregistered_url_alone(tmp_path):
    folder = tmp_path / "f"
    (folder / ".claude").mkdir(parents=True)
    path = folder / ".claude" / "settings.local.json"
    path.write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://proxy.example.org"}}),
        encoding="utf-8",
    )
    out = folders.unroute_folder(folder)
    assert out["unrouted"] is False and "not ours" in out["note"]
    assert _settings(folder)["env"]["ANTHROPIC_BASE_URL"] == "https://proxy.example.org"


def test_unroute_of_a_deleted_folder_drops_the_registry_entry(tmp_path):
    folder = tmp_path / "gone"
    folder.mkdir()
    folders.route_folder(folder)
    (folder / ".claude" / "settings.local.json").unlink()
    folder.joinpath(".claude").rmdir()
    folder.rmdir()
    out = folders.unroute_folder(folder)
    assert out["was_registered"] is True and out["unrouted"] is False
    assert folders.list_folders() == []


def test_list_folders_reports_active(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    folders.route_folder(a)
    folders.route_folder(b)
    (b / ".claude" / "settings.local.json").write_text("{}", encoding="utf-8")
    listed = {Path(x["path"]).name: x["active"] for x in folders.list_folders()}
    assert listed == {"a": True, "b": False}


def test_registry_file_is_in_the_masking_home_and_survives_garbage(tmp_path, _isolated):
    folder = tmp_path / "f"
    folder.mkdir()
    folders.route_folder(folder)
    reg = _isolated / "gateway_folders.json"
    assert reg.is_file()
    reg.write_text("garbage", encoding="utf-8")
    assert folders.list_folders() == []  # never raises
    folders.route_folder(folder)  # and is rebuilt
    assert len(folders.list_folders()) == 1


def test_files_are_lf_only():
    for name in ("folders.py", "control_client.py"):
        raw = (Path(folders.__file__).parent / name).read_bytes()
        assert b"\r\n" not in raw


# ---------------------------------------------------------------- channels that bypass the base URL


def test_route_closes_the_channels_that_ignore_the_base_url(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    out = folders.route_folder(folder)
    data = _settings(folder)
    assert data["skipWebFetchPreflight"] is True  # top-level key, not env
    for name, value in PRIVACY_ENV.items():
        assert data["env"][name] == value
    assert "skipWebFetchPreflight" not in data["env"]
    assert out["privacy"]["settings"] == ["skipWebFetchPreflight"]
    assert out["privacy"]["env"] == sorted(PRIVACY_ENV)


def test_unroute_puts_back_what_the_privacy_settings_replaced_and_removes_the_rest(tmp_path):
    folder = tmp_path / "f"
    (folder / ".claude").mkdir(parents=True)
    path = folder / ".claude" / "settings.local.json"
    path.write_text(
        json.dumps(
            {
                "skipWebFetchPreflight": False,
                "env": {"DISABLE_FEEDBACK_COMMAND": "0", "FOO": "1"},
                "theme": "dark",
            }
        ),
        encoding="utf-8",
    )
    folders.route_folder(folder)
    folders.route_folder(folder)  # a second call must not record our own values as the user's
    assert _settings(folder)["env"]["DISABLE_FEEDBACK_COMMAND"] == "1"
    out = folders.unroute_folder(folder)
    assert out["privacy_restored"] is True
    assert _settings(folder) == {
        "skipWebFetchPreflight": False,
        "env": {"DISABLE_FEEDBACK_COMMAND": "0", "FOO": "1", **DIRECT},
        "theme": "dark",
    }


def test_unroute_leaves_a_privacy_value_the_user_changed_since(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    folders.route_folder(folder)
    path = folder / ".claude" / "settings.local.json"
    data = _settings(folder)
    data["env"]["DISABLE_FEEDBACK_COMMAND"] = "custom"
    path.write_text(json.dumps(data), encoding="utf-8")
    folders.unroute_folder(folder)
    assert _settings(folder) == {"env": {"DISABLE_FEEDBACK_COMMAND": "custom", **DIRECT}}


def test_a_privacy_value_the_user_already_had_stays(tmp_path):
    folder = tmp_path / "f"
    (folder / ".claude").mkdir(parents=True)
    path = folder / ".claude" / "settings.local.json"
    path.write_text(json.dumps({"env": {"DISABLE_FEEDBACK_COMMAND": "1"}}), encoding="utf-8")
    folders.route_folder(folder)
    folders.unroute_folder(folder)
    assert _settings(folder) == {"env": {"DISABLE_FEEDBACK_COMMAND": "1", **DIRECT}}


def test_route_all_writes_the_user_settings_and_unroute_all_restores_them(tmp_path, monkeypatch):
    cfg = tmp_path / "claude"
    cfg.mkdir()
    (cfg / "settings.json").write_text(
        json.dumps({"env": {"KEEP": "1"}, "theme": "dark"}), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    out = folders.route_all()
    data = json.loads((cfg / "settings.json").read_text(encoding="utf-8"))
    assert out["scope"] == "every chat" and folders.routes_all()
    assert data["env"]["ANTHROPIC_BASE_URL"] == URL and data["env"]["KEEP"] == "1"
    assert data["skipWebFetchPreflight"] is True and data["theme"] == "dark"
    assert all(f["path"] != folders.ALL_KEY for f in folders.list_folders())
    folders.unroute_all()
    data = json.loads((cfg / "settings.json").read_text(encoding="utf-8"))
    assert data == {"env": {"KEEP": "1", **DIRECT}, "theme": "dark"} and not folders.routes_all()


# ---------------------------------------------------------------- masking on demand (2026-10-08)
def test_masked_chats_are_recorded_per_folder_and_survive_a_second_route(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    folders.route_folder(folder)
    assert folders.add_session(folder, "s1") == 1
    assert folders.add_session(folder, "s2") == 2
    assert folders.add_session(folder, "s1") == 2  # idempotent
    folders.route_folder(folder)  # a second masking on keeps the chats
    assert folders.drop_session(folder, "s1") == 1
    assert folders.drop_session(folder, None) == 0


def test_a_chat_is_not_recorded_on_a_folder_that_is_not_routed(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    assert folders.add_session(folder, "s1") == 0


def test_routed_folder_of_finds_the_closest_routed_parent(tmp_path):
    folder = tmp_path / "repo"
    sub = folder / "a" / "b"
    sub.mkdir(parents=True)
    folders.route_folder(folder)
    assert folders.routed_folder_of(sub) == folder
    assert folders.routed_folder_of(tmp_path) is None


def test_anything_routed_follows_the_folders_and_every_chat(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    assert folders.anything_routed() is False
    folders.route_folder(folder)
    assert folders.anything_routed() is True
    folders.unroute_folder(folder)
    assert folders.anything_routed() is False
    folders.route_all()
    assert folders.anything_routed() is True
    folders.unroute_all()
    assert folders.anything_routed() is False
