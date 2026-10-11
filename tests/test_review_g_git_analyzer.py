"""Review G-15: ``git_diff`` object-level semantics (PbipGitAnalyzer).

Pre-review: "modified" needed the object HEADER in the ``--unified=0`` output,
so DAX-body edits (the normal change) were never reported; only quoted names
matched; refs were passed positionally (``from_ref="--output=..."`` became a git
option); output was decoded with the locale codepage; no pathspec, so the whole
repo was reported for one project; working-tree mode saw unstaged edits only.
Every test below fails on base ``07fd722``. Uses a throw-away git repo in tmp_path.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from core.pbip.pbip_git_analyzer import PbipGitAnalyzer

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

_T = """table Sales
\tlineageTag: 00000000-0000-4000-8000-000000000001

\tmeasure 'Total Amount' =
\t\t\tVAR x = SUM(Sales[Amount])
\t\t\tRETURN
\t\t\t    x
\t\tlineageTag: 00000000-0000-4000-8000-000000000101

\tmeasure Margin = [Total Amount] * 0.1
\t\tlineageTag: 00000000-0000-4000-8000-000000000102

\tmeasure 'Łódź Sales' = [Total Amount]
\t\tlineageTag: 00000000-0000-4000-8000-000000000103

\tcolumn Amount
\t\tdataType: double
\t\tsourceColumn: Amount
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        cwd=str(cwd),
        capture_output=True,
        check=True,
        encoding="utf-8",
    ).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    tables = root / "Proj.SemanticModel" / "definition" / "tables"
    tables.mkdir(parents=True)
    (root / "Other.SemanticModel" / "definition" / "tables").mkdir(parents=True)
    (tables / "Sales.tmdl").write_text(_T, encoding="utf-8", newline="\n")
    (root / "Other.SemanticModel" / "definition" / "tables" / "X.tmdl").write_text(
        "table X\n\tcolumn A\n\t\tdataType: string\n", encoding="utf-8", newline="\n"
    )
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _sales(root: Path) -> Path:
    return root / "Proj.SemanticModel" / "definition" / "tables" / "Sales.tmdl"


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


def _detail(result, name="Sales.tmdl"):
    return next(d for d in result["details"] if d["file"].endswith(name))


def test_body_edit_is_modified(repo):
    _edit(_sales(repo), "SUM(Sales[Amount])", "SUMX(Sales, Sales[Amount])")
    _git(repo, "commit", "-qam", "body edit")
    res = PbipGitAnalyzer(str(repo / "Proj.SemanticModel")).analyze_commit_diff("HEAD~1", "HEAD")
    d = _detail(res)
    assert d["measures"]["modified"] == ["Total Amount"]
    assert d["measures"]["added"] == [] and d["measures"]["removed"] == []


def test_unquoted_and_non_ascii_names(repo):
    _edit(_sales(repo), "* 0.1", "* 0.2")
    _edit(_sales(repo), "measure 'Łódź Sales' = [Total Amount]", "measure 'Łódź Sales' = [Margin]")
    _git(repo, "commit", "-qam", "two edits")
    d = _detail(PbipGitAnalyzer(str(repo)).analyze_commit_diff("HEAD~1", "HEAD"))
    assert d["measures"]["modified"] == ["Margin", "Łódź Sales"]


def test_add_and_remove_by_owner(repo):
    _edit(
        _sales(repo),
        "\tmeasure Margin = [Total Amount] * 0.1\n"
        "\t\tlineageTag: 00000000-0000-4000-8000-000000000102\n\n",
        "",
    )
    _edit(
        _sales(repo),
        "\tcolumn Amount\n",
        "\tmeasure 'New One' = 1\n\t\tlineageTag: 00000000-0000-4000-8000-000000000109\n\n"
        "\tcolumn Amount\n",
    )
    _git(repo, "commit", "-qam", "add+remove")
    d = _detail(PbipGitAnalyzer(str(repo)).analyze_commit_diff("HEAD~1", "HEAD"))
    assert d["measures"]["added"] == ["New One"]
    assert d["measures"]["removed"] == ["Margin"]


@pytest.mark.parametrize(
    "bad", ["--output=pwned.txt", "HEAD..main", "HEAD:file", "a b", "no-such-ref"]
)
def test_option_like_or_unknown_refs_refused(repo, bad):
    an = PbipGitAnalyzer(str(repo))
    with pytest.raises(ValueError):
        an.analyze_commit_diff(bad, "HEAD")


def test_pathspec_scopes_to_the_project(repo):
    _edit(_sales(repo), "* 0.1", "* 0.3")
    other = repo / "Other.SemanticModel" / "definition" / "tables" / "X.tmdl"
    other.write_text("table X\n\tcolumn B\n\t\tdataType: string\n", encoding="utf-8", newline="\n")
    _git(repo, "commit", "-qam", "both projects")
    res = PbipGitAnalyzer(
        str(repo), pathspecs=[str(repo / "Proj.SemanticModel")]
    ).analyze_commit_diff("HEAD~1", "HEAD")
    assert [f["path"] for f in res["files"]] == ["Proj.SemanticModel/definition/tables/Sales.tmdl"]


def test_working_tree_covers_staged_unstaged_and_untracked(repo):
    _edit(_sales(repo), "* 0.1", "* 0.5")
    _git(repo, "add", "-A")  # staged
    _edit(_sales(repo), "SUM(Sales[Amount])", "SUMX(Sales, Sales[Amount])")  # + unstaged
    new = repo / "Proj.SemanticModel" / "definition" / "tables" / "New.tmdl"
    new.write_text("table New\n\tmeasure N = 1\n", encoding="utf-8", newline="\n")  # untracked
    res = PbipGitAnalyzer(str(repo / "Proj.SemanticModel")).analyze_working_changes()
    paths = {f["path"]: f["status"] for f in res["files"]}
    assert paths["Proj.SemanticModel/definition/tables/Sales.tmdl"] == "M"
    assert paths["Proj.SemanticModel/definition/tables/New.tmdl"] == "A"
    d = _detail(res)
    assert d["measures"]["modified"] == ["Margin", "Total Amount"]
    assert _detail(res, "New.tmdl")["measures"]["added"] == ["N"]


def test_rename_status_line_parsed():
    an = PbipGitAnalyzer.__new__(PbipGitAnalyzer)
    z = "R100\0old/tables/A.tmdl\0new/tables/B.tmdl\0M\0x/tables/C.tmdl\0"
    assert an._parse_name_status(z) == [("R", "new/tables/B.tmdl"), ("M", "x/tables/C.tmdl")]
    tab = "R100\told/tables/A.tmdl\tnew/tables/B.tmdl\n"
    assert an._parse_name_status(tab) == [("R", "new/tables/B.tmdl")]
