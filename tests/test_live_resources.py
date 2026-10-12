"""F-29: live resource templates + completion/complete (server/live_resources.py).

Read-only. The model side is faked at the ``model_readback`` boundary (no AMO / ADOMD); the
report side reads a tiny PBIR project from ``tmp_path``.
"""

from __future__ import annotations

import json

import pytest
from mcp import types

from core.operations import model_readback
from server import live_resources as live
from server.resources import ResourceManager
from tests._mcp_harness import make_app, make_dispatcher, run_session, tool


# ---------------------------------------------------------------- fakes
@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(model_readback, "_executor", lambda: object())
    tables = {1: "Sales", 2: "Date"}
    monkeypatch.setattr(model_readback, "table_names_by_id", lambda: tables)
    measures = [
        {
            "id": 10,
            "table": "Sales",
            "name": "Total Sales",
            "expression": "SUM ( Sales[Amount] )",
            "error": "",
            "state": 1,
            "display_folder": "KPI",
            "format_string": "#,##0",
        },
        {
            "id": 11,
            "table": "Sales",
            "name": "Sales YoY %",
            "expression": "[Total Sales] / 2",
            "error": "",
            "state": 1,
            "display_folder": "",
            "format_string": "0.0%",
        },
    ]
    monkeypatch.setattr(model_readback, "measure_rows", lambda: measures)

    def _find(table, name):
        for m in measures:
            if m["name"] == name and (not table or m["table"] == table):
                return m
        return {}

    monkeypatch.setattr(model_readback, "find_measure", _find)

    def _dmv(dmv, info=None):
        if dmv == "TMSCHEMA_COLUMNS":
            return [
                {"ID": 100, "TableID": 1, "ExplicitName": "Amount", "Type": 1},
                {"ID": 101, "TableID": 1, "ExplicitName": "DateKey", "Type": 1},
                {"ID": 102, "TableID": 2, "ExplicitName": "DateKey", "Type": 1},
                {"ID": 103, "TableID": 2, "ExplicitName": "RowNumber-xyz", "Type": 3},
            ]
        if dmv == "TMSCHEMA_RELATIONSHIPS":
            return [{"FromColumnID": 101, "ToColumnID": 102, "IsActive": True}]
        if dmv == "DISCOVER_CALC_DEPENDENCY":
            return [
                {
                    "OBJECT_TYPE": "MEASURE",
                    "OBJECT": "Sales YoY %",
                    "TABLE": "Sales",
                    "REFERENCED_OBJECT_TYPE": "MEASURE",
                    "REFERENCED_OBJECT": "Total Sales",
                    "REFERENCED_TABLE": "Sales",
                },
                {
                    "OBJECT_TYPE": "MEASURE",
                    "OBJECT": "Total Sales",
                    "TABLE": "Sales",
                    "REFERENCED_OBJECT_TYPE": "COLUMN",
                    "REFERENCED_OBJECT": "Amount",
                    "REFERENCED_TABLE": "Sales",
                },
            ]
        return []

    monkeypatch.setattr(model_readback, "dmv_rows", _dmv)


@pytest.fixture
def pbip(tmp_path, monkeypatch):
    report = tmp_path / "Demo.Report"
    defn = report / "definition"
    for pid, name in (("p1", "Overview"), ("p2", "Detail: 2024")):
        folder = defn / "pages" / pid
        (folder / "visuals" / f"v_{pid}").mkdir(parents=True)
        (folder / "page.json").write_text(
            json.dumps({"displayName": name, "width": 1280, "height": 720}), encoding="utf-8"
        )
        (folder / "visuals" / f"v_{pid}" / "visual.json").write_text(
            json.dumps(
                {
                    "position": {"x": 1, "y": 2, "width": 3, "height": 4},
                    "visual": {
                        "visualType": "cardVisual",
                        "visualContainerObjects": {
                            "title": [
                                {
                                    "properties": {
                                        "text": {"expr": {"Literal": {"Value": "'Revenue'"}}}
                                    }
                                }
                            ]
                        },
                        "query": {
                            "queryState": {
                                "Data": {"projections": [{"queryRef": "Sales.Total Sales"}]}
                            }
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
    (defn / "pages" / "pages.json").write_text(
        json.dumps({"pageOrder": ["p2", "p1"], "activePageName": "p1"}), encoding="utf-8"
    )
    from core.infrastructure import connection_state as cs_mod

    monkeypatch.setattr(cs_mod.connection_state, "get_pbip_folder_path", lambda: str(report))
    return report


# ---------------------------------------------------------------- model resources
def test_measure_resource_has_dax_format_and_dependencies(model):
    payload = json.loads(live.measure_resource("Sales", "Sales YoY %"))
    assert payload["expression"] == "[Total Sales] / 2"
    assert payload["format_string"] == "0.0%"
    assert payload["dependencies"]["references"] == ["[Total Sales]"]
    total = json.loads(live.measure_resource("Sales", "Total Sales"))
    assert total["dependencies"]["referenced_by"] == ["[Sales YoY %]"]
    assert total["display_folder"] == "KPI"


def test_unknown_measure_is_a_lookup_error_with_similar_names(model):
    with pytest.raises(LookupError, match="Similar"):
        live.measure_resource("Sales", "Total")


def test_not_connected_is_graceful(monkeypatch):
    monkeypatch.setattr(model_readback, "_executor", lambda: None)
    with pytest.raises(LookupError, match="01_Connection"):
        live.measure_resource("Sales", "X")
    offline = json.loads(live.schema_resource())
    assert offline["available"] is False
    assert live.complete("powerbi://model/measure/{table}/{name}", "table", "")[0] == []


def test_schema_resource_lists_tables_columns_measures_relationships(model):
    schema = json.loads(live.schema_resource())
    sales = next(t for t in schema["tables"] if t["name"] == "Sales")
    assert sales["columns"] == ["Amount", "DateKey"]
    assert sales["measures"] == ["Sales YoY %", "Total Sales"]
    date = next(t for t in schema["tables"] if t["name"] == "Date")
    assert date["columns"] == ["DateKey"]  # the RowNumber system column is hidden
    assert schema["relationships"] == [
        {"from": "Sales[DateKey]", "to": "Date[DateKey]", "active": True}
    ]


# ---------------------------------------------------------------- report resources
def test_pages_resource_in_view_order(pbip):
    pages = json.loads(live.pages_resource())
    assert [p["display_name"] for p in pages["pages"]] == ["Detail: 2024", "Overview"]
    assert pages["pages"][0]["visual_count"] == 1
    assert pages["active_page"] == "p1"


def test_page_visuals_resolves_by_id_or_display_name(pbip):
    by_id = json.loads(live.page_visuals_resource("p1"))
    by_name = json.loads(live.page_visuals_resource("detail: 2024"))
    assert by_id["visuals"][0]["type"] == "cardVisual"
    assert by_id["visuals"][0]["title"] == "Revenue"
    assert by_id["visuals"][0]["fields"] == ["Data: Sales.Total Sales"]
    assert by_name["page_id"] == "p2"


def test_unknown_page_lists_the_known_ones(pbip):
    with pytest.raises(LookupError, match="Overview"):
        live.page_visuals_resource("Nope")


def test_no_pbip_is_graceful(monkeypatch):
    from core.infrastructure import connection_state as cs_mod

    monkeypatch.setattr(cs_mod.connection_state, "get_pbip_folder_path", lambda: None)
    assert json.loads(live.pages_resource())["available"] is False
    assert live.complete("prompt:debug-visual", "page_name", "")[0] == []


# ---------------------------------------------------------------- captures
def test_capture_resource_omits_raw_plan_text():
    from core.query_plan import capturer

    capturer.remember(
        {
            "capture_id": "cap-test-1",
            "headline": "ok",
            "physical_plan_raw": "P" * 5000,
            "big": ["x" * 100] * 100,
        }
    )
    payload = json.loads(live.capture_resource("cap-test-1"))
    assert payload["headline"] == "ok"
    assert "physical_plan_raw" not in payload
    assert "physical_plan_raw" in payload["_omitted"] and "big" in payload["_omitted"]
    assert "cap-test-1" in live.capture_ids()
    with pytest.raises(LookupError):
        live.capture_resource("nope")


# ---------------------------------------------------------------- completion
def test_completion_prefix_then_substring_and_context(model):
    values, total, more = live.complete("powerbi://model/measure/{table}/{name}", "table", "sa")
    assert values == ["Sales"] and total == 1 and more is False
    names, _, _ = live.complete(
        "powerbi://model/measure/{table}/{name}", "name", "yo", {"table": "Sales"}
    )
    assert names == ["Sales YoY %"]
    # `name` outside the measure template is not a measure
    assert live.complete("powerbi://docs/tools/{tool}", "name", "")[0] == []
    # prompt arguments share the same candidate lists
    assert "Total Sales" in live.complete("prompt:create-measure", "measure_name", "")[0]


def test_completion_values_are_capped(monkeypatch):
    monkeypatch.setattr(
        live, "completion_candidates", lambda *a, **k: [f"T{i}" for i in range(250)]
    )
    values, total, more = live.complete("x", "table", "")
    assert len(values) == 100 and total == 250 and more is True


# ---------------------------------------------------------------- protocol surface
def _session_app():
    from server.mcp_app import seed_resources

    d = make_dispatcher(tool("04_Run_DAX", lambda a: {"success": True}))
    rm = ResourceManager()
    seed_resources(rm, d)
    app, _ = make_app(d, resource_manager=rm)
    return app


def test_templates_are_listed_with_declared_mime_types():
    app = _session_app()

    async def body(client):
        return (await client.list_resource_templates()).resourceTemplates

    templates = {t.uriTemplate: t for t in run_session(app, body)}
    for uri in (
        "powerbi://model/measure/{table}/{name}",
        "powerbi://report/page/{page}/visuals",
        "powerbi://capture/{capture_id}",
    ):
        assert templates[uri].mimeType == "application/json"


def test_resources_read_over_the_protocol_with_json_mime(model, pbip):
    app = _session_app()

    async def body(client):
        listed = {str(r.uri): r for r in (await client.list_resources()).resources}
        schema = await client.read_resource("powerbi://model/schema")
        pages = await client.read_resource("powerbi://report/pages")
        # a measure name with a space, '%' (percent-encoded) and ':' in a page name
        measure = await client.read_resource("powerbi://model/measure/Sales/Sales%20YoY%20%25")
        visuals = await client.read_resource("powerbi://report/page/Detail%3A%202024/visuals")
        return listed, schema, pages, measure, visuals

    listed, schema, pages, measure, visuals = run_session(app, body)
    assert listed["powerbi://model/schema"].mimeType == "application/json"
    assert schema.contents[0].mimeType == "application/json"
    assert json.loads(pages.contents[0].text)["count"] == 2
    assert json.loads(measure.contents[0].text)["name"] == "Sales YoY %"
    assert json.loads(visuals.contents[0].text)["page_id"] == "p2"


def test_template_parameters_cannot_escape(model, pbip):
    app = _session_app()

    async def body(client):
        for uri in (
            "powerbi://report/page/..%2F..%2Fsecret/visuals",
            "powerbi://model/measure/Sales/..%5Cx",
            "powerbi://model/measure/Sales/a%00b",
        ):
            with pytest.raises(Exception):
                await client.read_resource(uri)
        return True

    assert run_session(app, body)


def test_completion_over_the_protocol(model, pbip):
    app = _session_app()

    async def body(client):
        ref = types.ResourceTemplateReference(
            type="ref/resource", uri="powerbi://model/measure/{table}/{name}"
        )
        tables = await client.complete(ref, {"name": "table", "value": "Da"})
        names = await client.complete(
            ref, {"name": "name", "value": ""}, context_arguments={"table": "Sales"}
        )
        pref = types.PromptReference(type="ref/prompt", name="debug-visual")
        pages = await client.complete(pref, {"name": "page_name", "value": "ov"})
        return tables, names, pages

    tables, names, pages = run_session(app, body)
    assert tables.completion.values == ["Date"]
    assert names.completion.values == ["Sales YoY %", "Total Sales"]
    assert pages.completion.values == ["Overview"]


def test_completion_is_empty_not_an_error_when_nothing_is_connected(monkeypatch):
    monkeypatch.setattr(model_readback, "_executor", lambda: None)
    app = _session_app()

    async def body(client):
        ref = types.ResourceTemplateReference(
            type="ref/resource", uri="powerbi://model/measure/{table}/{name}"
        )
        return await client.complete(ref, {"name": "table", "value": ""})

    assert run_session(app, body).completion.values == []
