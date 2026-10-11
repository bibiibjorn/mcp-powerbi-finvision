"""Tests for semantic per-object git diff parsing in PbipGitAnalyzer."""

from unittest.mock import patch


from core.pbip.pbip_git_analyzer import PbipGitAnalyzer

SAMPLE_DIFF = """\
diff --git a/Model.SemanticModel/definition/tables/Sales.tmdl b/Model.SemanticModel/definition/tables/Sales.tmdl
@@ -10,3 +10,3 @@
-		measure 'Total Sales' = SUM('Sales'[Amount])
+		measure 'Total Sales' = SUMX('Sales', 'Sales'[Amount] * 'Sales'[Qty])
@@ -50,0 +51,4 @@
+		measure 'Margin %' = DIVIDE([Profit], [Total Sales])
+			formatString: 0.0%
"""


@patch.object(PbipGitAnalyzer, "_run_git")
@patch.object(PbipGitAnalyzer, "_find_git_root")
def test_diff_tmdl_file_extracts_added_and_modified_measures(mock_root, mock_run, tmp_path):
    mock_root.return_value = tmp_path
    mock_run.return_value = SAMPLE_DIFF
    a = PbipGitAnalyzer(str(tmp_path))

    result = a._diff_tmdl_file(
        "Model.SemanticModel/definition/tables/Sales.tmdl",
        from_ref="HEAD~1",
        to_ref="HEAD",
    )
    assert "Margin %" in result["measures"]["added"]
    assert "Total Sales" in result["measures"]["modified"]


@patch.object(PbipGitAnalyzer, "_run_git")
@patch.object(PbipGitAnalyzer, "_find_git_root")
def test_diff_tmdl_file_handles_relationships(mock_root, mock_run, tmp_path):
    mock_root.return_value = tmp_path
    mock_run.return_value = (
        "@@ -2,3 +2,3 @@\n"
        "-relationship abc123\n"
        "+relationship abc123-renamed\n"
        "@@ -10,0 +12,2 @@\n"
        "+relationship def456\n"
    )
    a = PbipGitAnalyzer(str(tmp_path))
    result = a._diff_tmdl_file(
        "Model.SemanticModel/definition/relationships.tmdl",
        from_ref="HEAD~1",
        to_ref="HEAD",
    )
    assert "def456" in result["relationships"]["added"]
    assert "abc123-renamed" in result["relationships"]["added"]
    assert "abc123" in result["relationships"]["removed"]


@patch.object(PbipGitAnalyzer, "_run_git")
@patch.object(PbipGitAnalyzer, "_find_git_root")
def test_diff_tmdl_file_returns_clean_error_on_git_failure(mock_root, mock_run, tmp_path):
    mock_root.return_value = tmp_path
    mock_run.side_effect = RuntimeError("git failed: no commits")
    a = PbipGitAnalyzer(str(tmp_path))
    result = a._diff_tmdl_file("foo.tmdl", from_ref="X", to_ref="Y")
    assert "error" in result
    assert "git failed" in result["error"]


@patch.object(PbipGitAnalyzer, "_run_git")
@patch.object(PbipGitAnalyzer, "_find_git_root")
def test_analyze_commit_diff_includes_per_file_details(mock_root, mock_run, tmp_path):
    mock_root.return_value = tmp_path

    def fake_run_git(args, **kwargs):
        if "--name-status" in args:
            return "M\tModel.SemanticModel/definition/tables/Sales.tmdl"
        if "--unified=0" in args:
            return SAMPLE_DIFF
        return ""

    mock_run.side_effect = fake_run_git

    a = PbipGitAnalyzer(str(tmp_path))
    result = a.analyze_commit_diff(from_ref="HEAD~1", to_ref="HEAD")
    assert "details" in result
    assert isinstance(result["details"], list)
    assert len(result["details"]) >= 1
    file_detail = result["details"][0]
    assert file_detail["file"].endswith("Sales.tmdl")
    assert "Total Sales" in file_detail["measures"]["modified"]
    assert "Margin %" in file_detail["measures"]["added"]


@patch.object(PbipGitAnalyzer, "_run_git")
@patch.object(PbipGitAnalyzer, "_find_git_root")
def test_analyze_working_changes_includes_details(mock_root, mock_run, tmp_path):
    mock_root.return_value = tmp_path

    def fake_run_git(args, **kwargs):
        # Simulate one staged modified .tmdl file via diff --cached --name-status
        if "--cached" in args and "--name-status" in args:
            return "M\tModel.SemanticModel/definition/tables/Sales.tmdl"
        if "--name-status" in args:
            return ""
        if "ls-files" in args:
            return ""
        if "--unified=0" in args:
            return SAMPLE_DIFF
        return ""

    mock_run.side_effect = fake_run_git

    a = PbipGitAnalyzer(str(tmp_path))
    result = a.analyze_working_changes()
    assert "details" in result
    assert any(d.get("file", "").endswith("Sales.tmdl") for d in result["details"])


@patch.object(PbipGitAnalyzer, "_run_git")
@patch.object(PbipGitAnalyzer, "_find_git_root")
def test_diff_tmdl_file_handles_columns_and_roles(mock_root, mock_run, tmp_path):
    mock_root.return_value = tmp_path
    mock_run.return_value = (
        "@@ -1,1 +1,1 @@\n"
        "-		column 'OldCol'\n"
        "+		column 'NewCol'\n"
        "@@ -10,0 +12,2 @@\n"
        "+		column 'AddedCol'\n"
        "@@ -20,1 +22,1 @@\n"
        "-role 'Admins'\n"
        "+role 'Administrators'\n"
    )
    a = PbipGitAnalyzer(str(tmp_path))
    result = a._diff_tmdl_file("foo.tmdl", "X", "Y")
    assert "AddedCol" in result["columns"]["added"]
    assert "OldCol" in result["columns"]["removed"]
    assert "NewCol" in result["columns"]["added"]
    assert "Admins" in result["roles"]["removed"]
    assert "Administrators" in result["roles"]["added"]


@patch.object(PbipGitAnalyzer, "_run_git")
@patch.object(PbipGitAnalyzer, "_find_git_root")
def test_summarize_pr_changes_includes_details(mock_root, mock_run, tmp_path):
    mock_root.return_value = tmp_path

    def fake_run_git(args, **kwargs):
        if "merge-base" in args:
            return "abc1234\n"
        if "--name-status" in args:
            return "M\tModel.SemanticModel/definition/tables/Sales.tmdl"
        if "log" in args:
            return ""
        if "--unified=0" in args:
            return SAMPLE_DIFF
        return ""

    mock_run.side_effect = fake_run_git

    a = PbipGitAnalyzer(str(tmp_path))
    result = a.summarize_pr_changes(base_branch="main")
    assert "details" in result
    assert len(result["details"]) >= 1
    assert "Total Sales" in result["details"][0]["measures"]["modified"]
