from server.handlers.dax_codemods_handler import handle_dax_codemods


def test_unknown_op_errors():
    assert handle_dax_codemods({"operation": "bogus"})["success"] is False


def test_list_transforms_returns_catalog():
    res = handle_dax_codemods({"operation": "list_transforms"})
    assert res["success"] is True
    names = {t["name"] for t in res["transforms"]}
    assert {
        "inline_measure",
        "extract_measure",
        "replace_pattern",
        "move_measure_batch",
        "swap_table_reference",
    } <= names


def test_preview_requires_transform_name():
    res = handle_dax_codemods({"operation": "preview"})
    assert res["success"] is False


def test_apply_requires_plan_hash():
    res = handle_dax_codemods(
        {"operation": "apply", "transform": "inline_measure", "args": {"measure_ref": "T[X]"}}
    )
    assert res["success"] is False
