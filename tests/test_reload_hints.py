from core.autonomous.reload_hints import attach_reload_hint, is_reload_required


def test_is_reload_required_true_for_visual_ops():
    assert is_reload_required("07_Visual_Operations", "update_formatting") is True
    assert is_reload_required("07_Visual_Operations", "add_field") is True


def test_is_reload_required_false_for_read_ops():
    assert is_reload_required("07_Visual_Operations", "list") is False
    assert is_reload_required("02_Model_Operations", "update") is False  # live TOM


def test_attach_reload_hint_injects_on_success():
    resp = {"success": True, "visual_id": "abc"}
    attach_reload_hint(resp, "07_Visual_Operations", "add_field")
    assert resp["reload_required"] is True
    assert "reload_hint" in resp
    # One composite call: reload + settle + screenshot in a single op.
    assert resp["reload_op_chain"] == ["17_Desktop_Bridge:reload_and_screenshot"]


def test_attach_reload_hint_noop_on_failure():
    resp = {"success": False, "error": "bad input"}
    attach_reload_hint(resp, "07_Visual_Operations", "add_field")
    assert "reload_required" not in resp


def test_attach_reload_hint_noop_for_live_tom_op():
    resp = {"success": True}
    attach_reload_hint(resp, "02_Model_Operations", "update")
    assert "reload_required" not in resp


def test_is_reload_required_for_previously_missing_ops():
    # These ops exist in handler dispatchers but were missing from
    # OPS_REQUIRING_RELOAD before the reconciliation fix.
    assert is_reload_required("07_Visual_Operations", "update_position") is True
    assert is_reload_required("07_Page_Operations", "clone") is True
    assert is_reload_required("07_Theme_Operations", "set_colors") is True
    assert is_reload_required("07_Bookmark_Operations", "rename") is True
