"""L2 regression: paginate must not silently return has_more=False when a
continuation token names a key that's no longer in the result.

If a ``next_token`` of form ``"key:offset"`` references a list key that has
since vanished from ``result``, ``paginate`` previously fell through with
``has_more: False`` — silently truncating from the caller's view. It now
returns an explicit ``error_type == "stale_pagination_token"``.
"""

from core.validation.pagination_helpers import paginate


def test_stale_token_key_missing_returns_explicit_error():
    # Token names 'rows', but the result only has 'columns'.
    result = {"success": True, "columns": [1, 2, 3]}
    out = paginate(result, page_size=2, next_token="rows:2", list_keys=["rows", "columns"])

    assert out.get("error_type") == "stale_pagination_token"
    assert "rows" in out.get("error", "")
    # Must NOT pretend the data cleanly ended.
    assert out.get("has_more") is not False


def test_valid_token_present_key_paginates_normally():
    # Token names 'rows', which is present — normal pagination, no error_type.
    result = {"success": True, "rows": list(range(10))}
    out = paginate(result, page_size=3, next_token="rows:3", list_keys=["rows"])

    assert "error_type" not in out
    assert out["rows"] == [3, 4, 5]
    assert out["has_more"] is True
    assert out["next_token"] == "rows:6"


def test_valid_token_present_key_end_of_data():
    # Continuation reaching the end — present key, no error, clean has_more=False.
    result = {"success": True, "rows": list(range(5))}
    out = paginate(result, page_size=3, next_token="rows:3", list_keys=["rows"])

    assert "error_type" not in out
    assert out["rows"] == [3, 4]
    assert out["has_more"] is False
    assert "next_token" not in out
