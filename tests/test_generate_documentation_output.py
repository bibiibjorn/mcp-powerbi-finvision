"""M5: generate_documentation writes to file, never inlines the full markdown."""

from unittest.mock import patch, MagicMock


@patch("server.handlers.pbip_operations_handler._generate_markdown")
@patch("server.handlers.pbip_operations_handler._cache")
@patch("server.handlers.pbip_operations_handler._normalize_pbip_path", side_effect=lambda p: p)
def test_no_output_path_writes_file_not_inline(mock_norm, mock_cache, mock_gen, tmp_path):
    from server.handlers.pbip_operations_handler import _handle_generate_documentation

    big = "# Doc\n" + ("x" * 50000)
    mock_gen.return_value = big
    parsed = MagicMock()
    parsed.model_data = {"name": "MyModel"}
    parsed.dependencies = {}
    mock_cache.get_or_parse.return_value = parsed

    res = _handle_generate_documentation({"pbip_path": str(tmp_path)})

    assert res["success"] is True
    assert "file" in res
    assert "documentation" not in res  # no giant inline blob
    assert res["length_chars"] == len(big)
    # the file actually exists and contains the markdown
    from pathlib import Path

    assert Path(res["file"]).read_text(encoding="utf-8") == big


@patch("server.handlers.pbip_operations_handler._generate_markdown")
@patch("server.handlers.pbip_operations_handler._cache")
@patch("server.handlers.pbip_operations_handler._normalize_pbip_path", side_effect=lambda p: p)
def test_explicit_output_path_still_works(mock_norm, mock_cache, mock_gen, tmp_path):
    from server.handlers.pbip_operations_handler import _handle_generate_documentation

    mock_gen.return_value = "# Doc"
    parsed = MagicMock()
    parsed.model_data = {"name": "M"}
    parsed.dependencies = {}
    mock_cache.get_or_parse.return_value = parsed
    target = tmp_path / "out.md"
    res = _handle_generate_documentation({"pbip_path": str(tmp_path), "output_path": str(target)})
    assert res["success"] is True
    assert res["file"] == str(target)
    assert target.read_text(encoding="utf-8") == "# Doc"
