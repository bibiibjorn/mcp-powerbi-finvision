"""07_Visual_Operations:set_action — real PBIR ``visualLink`` shape (H-14).

Desktop stores a button / shape action at
``visual.visualContainerObjects.visualLink[0].properties`` =
``{show, type, navigationSection | bookmark | webUrl | drillthroughSection,
dataFunction?}`` (R0105 / R0201 census: 2,186 visualLink entries, ZERO
``action`` keys). The previous version of this file pinned the invented
``vCO.action{type, destination}`` shape; it now asserts the real one:

  - page targets are resolved to the page FOLDER id, bookmark targets to the
    bookmark id (display names accepted);
  - DataFunction writes the item-reference shape Desktop uses
    (``kind: ItemLocation`` + ``byReference{itemId, workspaceId}``); the
    name / Power Fx args have no verified location and are reported back as
    ``unwritten_args`` instead of being written to invented keys;
  - the legacy invalid ``vCO.action`` key is removed on write;
  - unrelated ``visualLink`` props survive (P1-127).

A real minimal PBIR tree under ``tmp_path``, real reads / writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from server.handlers.visual_config_handler import ACTION_TYPES, _op_set_action


def _build_definition(
    tmp_path: Path,
    *,
    existing_link: Any = None,
    legacy_action: Any = None,
) -> Path:
    """Report with pages ``p1`` (Actions, has the button) and ``p2`` (Detail) + one bookmark."""
    defn = tmp_path / "R.Report" / "definition"
    pages_dir = defn / "pages"
    for pid, name in (("p1", "Actions"), ("p2", "Detail")):
        (pages_dir / pid / "visuals").mkdir(parents=True)
        (pages_dir / pid / "page.json").write_text(
            json.dumps({"name": pid, "displayName": name, "displayOption": "FitToPage"}),
            encoding="utf-8",
        )
    (pages_dir / "pages.json").write_text(
        json.dumps({"pageOrder": ["p1", "p2"], "activePageName": "p1"}), encoding="utf-8"
    )
    bm_dir = defn / "bookmarks"
    bm_dir.mkdir()
    (bm_dir / "bm01.bookmark.json").write_text(
        json.dumps({"name": "bm01", "displayName": "Bookmark1", "explorationState": {}}),
        encoding="utf-8",
    )
    (bm_dir / "bookmarks.json").write_text(
        json.dumps({"items": [{"name": "bm01"}]}), encoding="utf-8"
    )

    vco: Dict[str, Any] = {}
    if existing_link is not None:
        vco["visualLink"] = existing_link
    if legacy_action is not None:
        vco["action"] = legacy_action
    btn = pages_dir / "p1" / "visuals" / "btn1"
    btn.mkdir()
    (btn / "visual.json").write_text(
        json.dumps(
            {
                "name": "btn1",
                "position": {"x": 0, "y": 0, "width": 120, "height": 40, "z": 0},
                "visual": {
                    "visualType": "actionButton",
                    "objects": {},
                    "visualContainerObjects": vco,
                },
            }
        ),
        encoding="utf-8",
    )
    return defn


def _vco(defn: Path) -> Dict[str, Any]:
    data = json.loads(
        (defn / "pages" / "p1" / "visuals" / "btn1" / "visual.json").read_text(encoding="utf-8")
    )
    return data["visual"]["visualContainerObjects"]


def _link_props(defn: Path) -> Optional[Dict[str, Any]]:
    link = _vco(defn).get("visualLink")
    return link[0]["properties"] if link else None


def _literal(props: Dict[str, Any], key: str) -> str:
    return props[key]["expr"]["Literal"]["Value"]


BASE = {"page_name": "Actions", "visual_name": "btn1"}


class TestDataFunction:
    def test_writes_item_reference_shape(self, tmp_path: Path) -> None:
        defn = _build_definition(tmp_path)
        res = _op_set_action(
            {
                **BASE,
                "action_type": "DataFunction",
                "data_function_id": "udf-1234",
                "data_function_workspace_id": "ws-9",
            },
            defn,
        )
        assert res["success"] is True
        assert res["status"] == "changed"
        props = _link_props(defn)
        assert _literal(props, "type") == "'DataFunction'"
        assert _literal(props, "show") == "true"
        df = props["dataFunction"]
        assert df["kind"] == "ItemLocation"
        assert df["byReference"]["itemId"]["expr"]["Literal"]["Value"] == "'udf-1234'"
        assert df["byReference"]["workspaceId"]["expr"]["Literal"]["Value"] == "'ws-9'"
        # No invented keys.
        assert "$unverifiedShape" not in df
        assert "powerFx" not in df and "name" not in df

    def test_name_and_power_fx_reported_not_written(self, tmp_path: Path) -> None:
        defn = _build_definition(tmp_path)
        res = _op_set_action(
            {
                **BASE,
                "action_type": "DataFunction",
                "data_function_id": "udf-1",
                "data_function_name": "ApproveTrade",
                "power_fx": "Approve(ThisRecord.TradeId)",
            },
            defn,
        )
        assert res["success"] is True
        assert set(res["unwritten_args"]) == {"data_function_name", "power_fx"}
        assert "warning" in res
        text = json.dumps(_vco(defn))
        assert "ApproveTrade" not in text and "Approve(" not in text

    def test_payload_omitted_when_no_reference_args(self, tmp_path: Path) -> None:
        defn = _build_definition(tmp_path)
        res = _op_set_action({**BASE, "action_type": "DataFunction"}, defn)
        assert res["success"] is True
        props = _link_props(defn)
        assert _literal(props, "type") == "'DataFunction'"
        assert "dataFunction" not in props

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        defn = _build_definition(tmp_path)
        res = _op_set_action(
            {**BASE, "action_type": "DataFunction", "data_function_id": "u", "dry_run": True},
            defn,
        )
        assert res["dry_run"] is True
        assert res["status"] == "would_change"
        assert _link_props(defn) is None

    def test_data_function_is_in_accepted_types(self) -> None:
        assert "DataFunction" in ACTION_TYPES


class TestUnknownActionType:
    def test_rejected(self, tmp_path: Path) -> None:
        defn = _build_definition(tmp_path)
        res = _op_set_action({**BASE, "action_type": "Teleport"}, defn)
        assert res["success"] is False
        assert res["error_type"] == "invalid_action_type"
        assert "Teleport" in res["error"]

    def test_nothing_written_on_reject(self, tmp_path: Path) -> None:
        defn = _build_definition(tmp_path)
        _op_set_action({**BASE, "action_type": "Teleport"}, defn)
        assert _link_props(defn) is None

    def test_missing_action_type_still_rejected(self, tmp_path: Path) -> None:
        defn = _build_definition(tmp_path)
        res = _op_set_action({**BASE}, defn)
        assert res["success"] is False
        assert "action_type" in res["error"]


class TestActionTypes:
    @pytest.mark.parametrize(
        "action_type,target,key,expected",
        [
            # page display name -> page FOLDER id
            ("PageNavigation", "Detail", "navigationSection", "'p2'"),
            ("PageNavigation", "p2", "navigationSection", "'p2'"),
            ("Drillthrough", "Detail", "drillthroughSection", "'p2'"),
            # bookmark display name -> bookmark id
            ("Bookmark", "Bookmark1", "bookmark", "'bm01'"),
            ("WebUrl", "https://example.com/a'b", "webUrl", "'https://example.com/a''b'"),
        ],
    )
    def test_target_written_to_real_key(
        self, tmp_path: Path, action_type: str, target: str, key: str, expected: str
    ) -> None:
        defn = _build_definition(tmp_path)
        res = _op_set_action({**BASE, "action_type": action_type, "action_target": target}, defn)
        assert res["success"] is True, res
        props = _link_props(defn)
        assert _literal(props, "type") == f"'{action_type}'"
        assert _literal(props, key) == expected
        assert "destination" not in props
        assert "dataFunction" not in props
        # Nothing lands in the invented top-level / vCO.action locations.
        assert "action" not in _vco(defn)

    @pytest.mark.parametrize("action_type", ["Back", "ClearAllSlicers"])
    def test_targetless_types(self, tmp_path: Path, action_type: str) -> None:
        defn = _build_definition(tmp_path)
        res = _op_set_action({**BASE, "action_type": action_type}, defn)
        assert res["success"] is True
        props = _link_props(defn)
        assert _literal(props, "type") == f"'{action_type}'"
        assert set(props) == {"show", "type"}

    def test_missing_target_is_an_error(self, tmp_path: Path) -> None:
        defn = _build_definition(tmp_path)
        res = _op_set_action({**BASE, "action_type": "PageNavigation"}, defn)
        assert res["success"] is False
        assert "action_target" in res["error"]

    def test_unknown_page_target_is_an_error(self, tmp_path: Path) -> None:
        defn = _build_definition(tmp_path)
        res = _op_set_action(
            {**BASE, "action_type": "PageNavigation", "action_target": "Nope"}, defn
        )
        assert res["success"] is False
        assert res["error_type"] == "page_not_found"
        assert _link_props(defn) is None

    def test_unknown_bookmark_target_is_an_error(self, tmp_path: Path) -> None:
        defn = _build_definition(tmp_path)
        res = _op_set_action({**BASE, "action_type": "Bookmark", "action_target": "Nope"}, defn)
        assert res["success"] is False
        assert res["error_type"] == "bookmark_not_found"

    def test_data_function_args_ignored_for_other_types(self, tmp_path: Path) -> None:
        defn = _build_definition(tmp_path)
        _op_set_action(
            {
                **BASE,
                "action_type": "Bookmark",
                "action_target": "Bookmark1",
                "power_fx": "Go()",
                "data_function_id": "udf-1",
            },
            defn,
        )
        assert "dataFunction" not in _link_props(defn)

    def test_sibling_props_preserved(self, tmp_path: Path) -> None:
        """P1-127 regression guard — unrelated visualLink props survive."""
        existing = [
            {
                "properties": {
                    "show": {"expr": {"Literal": {"Value": "true"}}},
                    "type": {"expr": {"Literal": {"Value": "'Bookmark'"}}},
                    "tooltip": {"expr": {"Literal": {"Value": "'Keep me'"}}},
                }
            }
        ]
        defn = _build_definition(tmp_path, existing_link=existing)
        _op_set_action({**BASE, "action_type": "PageNavigation", "action_target": "Detail"}, defn)
        props = _link_props(defn)
        assert _literal(props, "type") == "'PageNavigation'"
        assert _literal(props, "tooltip") == "'Keep me'"

    def test_legacy_action_key_removed(self, tmp_path: Path) -> None:
        legacy = [{"properties": {"type": {"expr": {"Literal": {"Value": "'Back'"}}}}}]
        defn = _build_definition(tmp_path, legacy_action=legacy)
        res = _op_set_action({**BASE, "action_type": "Back"}, defn)
        assert res["success"] is True
        assert "action" not in _vco(defn)
        assert _literal(_link_props(defn), "type") == "'Back'"

    def test_action_types_tuple_contents(self) -> None:
        assert set(ACTION_TYPES) == {
            "PageNavigation",
            "Bookmark",
            "Drillthrough",
            "WebUrl",
            "Back",
            "ClearAllSlicers",
            "DataFunction",
        }


class TestSchemaEnumInSync:
    def test_schema_enum_matches_handler(self) -> None:
        from server.tool_schemas import TOOL_SCHEMAS

        enum = TOOL_SCHEMAS["visual_operations"]["properties"]["action_type"]["enum"]
        assert set(enum) == set(ACTION_TYPES)

    def test_companion_args_declared_in_schema(self) -> None:
        from server.tool_schemas import TOOL_SCHEMAS

        props = TOOL_SCHEMAS["visual_operations"]["properties"]
        for key in (
            "data_function_id",
            "data_function_workspace_id",
            "data_function_name",
            "power_fx",
        ):
            assert key in props, f"{key} missing from the visual_operations schema"

    def test_visual_not_found_is_an_error(self, tmp_path: Path) -> None:
        defn = _build_definition(tmp_path)
        res = _op_set_action(
            {"page_name": "Actions", "visual_name": "nope", "action_type": "Back"}, defn
        )
        assert res["success"] is False
        assert "no visual matches" in res["error"].lower() or "not found" in res["error"].lower()
