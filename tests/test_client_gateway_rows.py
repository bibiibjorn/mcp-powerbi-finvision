"""Data tables in a masked request: cells of models the gateway never read are learned."""

from __future__ import annotations

import json

from client_gateway.masking import mask_request
from client_gateway.memo import RemaskMemo
from client_gateway.rows import cells_from_paste, cells_from_result, keep_cell, new_table_cells
from client_gateway.state import GatewayState

PC_A, PC_B, GL_A = "Kortrijk Zuidpoort", "Brugge Haven Oost", "Huurinkomsten Zandvliet"


def _request(tool: str, result: object, query: str = "EVALUATE x") -> dict:
    return {
        "messages": [
            {"role": "user", "content": "debug"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": tool, "input": {"query": query}}
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": json.dumps(result)}],
                    }
                ],
            },
        ]
    }


def test_cell_filter_keeps_names_and_drops_numbers_periods_and_code():
    assert keep_cell(PC_A) == PC_A
    for value in (
        "1.234,56",
        "€ 12k",
        "2026-03-31",
        "MAR 2026",
        "Q3 2025",
        "FY24",
        "true",
        "SUM ( 'Sales'[Amount] )",
        r"C:\data\x.csv",
        "ab",
        12,
        None,
    ):
        assert keep_cell(value) is None, value


def test_dax_result_learns_column_cells_not_measure_cells():
    text = json.dumps(
        {
            "success": True,
            "rows": [
                {"d_Profit_Center[Name]": PC_A, "[Revenue]": "1.234", "[Status SVG]": "Open"},
                {"d_Profit_Center[Name]": PC_B, "[Revenue]": "17", "[Status SVG]": "Open"},
            ],
        }
    )
    assert sorted(cells_from_result(text)) == sorted([PC_A, PC_B])


def test_only_data_tools_and_never_metadata_queries():
    rows = {"rows": [{"d_GL[Account]": GL_A}]}
    assert new_table_cells(_request("mcp__powerbi-finvision__04_Run_DAX", rows)) == [GL_A]
    assert new_table_cells(_request("mcp__ms-fabric-mcp__06_run_query", rows)) == [GL_A]
    assert new_table_cells(_request("Read", rows)) == []
    assert (
        new_table_cells(
            _request("mcp__powerbi-finvision__04_Run_DAX", rows, "EVALUATE INFO.VIEW.MEASURES()")
        )
        == []
    )


def test_pasted_excel_block_learns_cells_without_the_header():
    paste = f"Profit center\tAmount\n{PC_A}\t1.234\n{PC_B}\t17\n"
    assert cells_from_paste(paste) == [PC_A, PC_B]
    md = f"| Account | Amount |\n|---|---|\n| {GL_A} | 12 |\n"
    assert cells_from_paste(md) == [GL_A]


def test_a_never_read_model_is_masked_in_the_request(tmp_path):
    state = GatewayState(home=tmp_path, default_mode="on")
    body = _request(
        "mcp__powerbi-finvision__04_Run_DAX",
        {"rows": [{"d_Profit_Center[Name]": PC_A, "[Revenue]": 62}]},
    )
    body["messages"][-1]["content"].append(
        {"type": "text", "text": f"Why is {PC_A} at 62 in March?"}
    )
    out = mask_request(json.dumps(body).encode(), "application/json", state, RemaskMemo()).decode()
    assert PC_A not in out and "62" in out
    assert state.stats()["table_values_learned"] == 1
