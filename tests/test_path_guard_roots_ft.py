"""F-11: the path guard also allows the offline project folder and ``FINVISION_ALLOWED_ROOTS`` (headless CLI)."""

from __future__ import annotations

import os

from core.infrastructure import offline_model
from core.validation import path_guard


def test_offline_project_folder_is_an_allowed_root(monkeypatch, tmp_path):
    project = tmp_path / "Proj"
    definition = project / "M.SemanticModel" / "definition"
    definition.mkdir(parents=True)
    monkeypatch.setenv("FINVISION_ALLOWED_ROOTS", "")
    monkeypatch.setattr(offline_model, "is_active", lambda: True)
    monkeypatch.setattr(offline_model, "info", lambda: {"definition_folder": str(definition)})
    assert project.resolve() in path_guard._resolved_root_list()
    ok, err = path_guard.guard_or_error(str(project / "tests" / "model.suite.json"), "suite_path")
    assert err is None and ok is not None


def test_offline_inactive_adds_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("FINVISION_ALLOWED_ROOTS", "")
    monkeypatch.setattr(offline_model, "is_active", lambda: False)
    assert tmp_path.resolve() not in path_guard._resolved_root_list()


def test_env_roots_are_added_and_missing_dirs_ignored(monkeypatch, tmp_path):
    extra = tmp_path / "ci"
    extra.mkdir()
    monkeypatch.setenv(
        "FINVISION_ALLOWED_ROOTS", os.pathsep.join([str(extra), str(tmp_path / "gone")])
    )
    roots = path_guard._resolved_root_list()
    assert extra.resolve() in roots and (tmp_path / "gone").resolve() not in roots
