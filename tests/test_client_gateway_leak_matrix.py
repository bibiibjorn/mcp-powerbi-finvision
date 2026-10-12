"""Mutation-based egress test (G-27): every dictionary value, written in many spellings, in every
request position, must not be readable in the bytes that leave the gateway. One harness for the
classes of leak the 2026-10-03 review found one by one (invisible characters, diacritics, short
first names, attachments inside reminders ...). Synthetic names only."""

from __future__ import annotations

import json
from typing import Any, Callable

import pytest

from client_gateway.masking import mask_request
from client_gateway.memo import RemaskMemo
from client_gateway.state import GatewayState
from core.client_guard.engine import normalise

COMPANY = "Zorvex Industries NV"
PERSON = "Jan Peeters"
# distinctive words that must not survive anywhere (compared on the normalised egress text)
SECRETS = ("zorvex", "peeters")
# an instructions block as long as a real one starts (the identity lines are far shorter)
INSTRUCTIONS = "You are an agent working with the user. Follow the instructions below. " * 8


def spellings(value: str) -> dict[str, str]:
    out = {
        "plain": value,
        "upper": value.upper(),
        "lower": value.lower(),
        "double_space": value.replace(" ", "  "),
        "nbsp": value.replace(" ", "\u00a0"),
        "zwsp": value.replace(" ", "\u200b"),
        "newline": value.replace(" ", "\n"),
        "soft_hyphen": value[:3] + "\u00ad" + value[3:],
        "zwnj": value[:3] + "\u200c" + value[3:],
        "zwj": value[:3] + "\u200d" + value[3:],
        "word_joiner": value[:3] + "\u2060" + value[3:],
        "bom": "\ufeff" + value,
        "fullwidth": value.replace("o", "\uff4f"),
        # cross-script look-alikes (Unicode TR39): NFKC leaves them alone
        "cyrillic_o": value.replace("o", "\u043e"),
        "greek_o": value.replace("o", "\u03bf"),
        "cyrillic_e": value.replace("e", "\u0435"),
        "slug_underscore": value.replace(" ", "_"),
        "slug_dash": value.replace(" ", "-"),
        "url": value.replace(" ", "%20"),
        "glued": value.replace(" ", ""),
    }
    if "Zorvex" in value:
        out["diacritic"] = value.replace("Zorvex", "Z\u00f6rvex")
        out["typo"] = value.replace("Zorvex", "Zorvx")
    if value == PERSON:
        out["cyrillic_a"] = value.replace("a", "а")
        out["last_first"] = "Peeters, Jan"
        out["initial"] = "J. Peeters"
        out["initial_glued"] = "J.Peeters"
        out["surname_only"] = "Peeters"
        out["surname_upper"] = "PEETERS"
        out["path"] = "c:/clients/peeters/report.xlsx"
    return out


POSITIONS: dict[str, Callable[[str], dict[str, Any]]] = {
    "user_text": lambda s: {"messages": [{"role": "user", "content": f"please check {s} now"}]},
    "user_block": lambda s: {
        "messages": [{"role": "user", "content": [{"type": "text", "text": f"see {s}."}]}]
    },
    # the instructions block of the top-level ``system`` field (folder and memory paths, MCP
    # server instructions); its leading short identity lines are the one verbatim exception
    # (a rewritten identity sentence got HTTP 429 on every request, 2026-10-08), see
    # test_only_the_leading_identity_lines_of_the_system_field_are_verbatim below
    "system_instructions": lambda s: {
        "system": [
            {"type": "text", "text": "x-app-billing-header: v=1;"},
            {"type": "text", "text": "You are the app."},
            {"type": "text", "text": INSTRUCTIONS + f"\nPrimary working directory: c:/work/{s}\n"},
        ],
        "messages": [],
    },
    "tool_description": lambda s: {
        "tools": [
            {"name": "t", "description": f"Works on {s}", "input_schema": {"type": "object"}}
        ],
        "messages": [],
    },
    "tool_use_value": lambda s: {
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {"filter": s}}],
            }
        ]
    },
    "tool_use_key": lambda s: {
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {s: 1}}],
            }
        ]
    },
    "tool_result_string": lambda s: {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": f"row: {s}; total 5"}
                ],
            }
        ]
    },
    "tool_result_blocks": lambda s: {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": s}],
                    }
                ],
            }
        ]
    },
    "reminder_attachment": lambda s: {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "summarise"},
                    {
                        "type": "text",
                        "text": f"<system-reminder>Result of calling the Read tool:\n{s} wrote "
                        "a long mail about the quarterly figures.</system-reminder>",
                    },
                ],
            }
        ]
    },
    "assistant_text": lambda s: {
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": f"I looked at {s} for you."}],
            }
        ]
    },
}


@pytest.fixture(scope="module")
def state(tmp_path_factory) -> GatewayState:
    mp = pytest.MonkeyPatch()  # module scope: the suite's per-test env fixtures do not apply
    mp.setenv("FINVISION_GATEWAY_NER", "0")  # no model, no worker process
    try:
        st = GatewayState(
            home=tmp_path_factory.mktemp("leak"), default_mode="on", images_policy="withhold"
        )
    finally:
        mp.undo()
    st.smap.add_values("ORG", [COMPANY], "test")
    st.smap.add_values("PER", [PERSON], "test")
    return st


def egress(state: GatewayState, payload: dict[str, Any]) -> str:
    body = json.dumps({"model": "m", **payload}).encode()
    return mask_request(body, "application/json", state, RemaskMemo()).decode("utf-8")


CASES = [
    (value, name, pos)
    for value in (COMPANY, PERSON)
    for name in spellings(value)
    for pos in POSITIONS
]


@pytest.mark.parametrize("value,name,pos", CASES, ids=[f"{v[:5]}-{n}-{p}" for v, n, p in CASES])
def test_no_spelling_of_a_client_value_leaves_the_machine(
    state: GatewayState, value: str, name: str, pos: str
) -> None:
    text = spellings(value)[name]
    out = egress(state, POSITIONS[pos](text))
    seen = normalise(out)
    for secret in SECRETS:
        assert secret not in seen, (name, pos)
    # json-escaped copies (ensure_ascii escapes of the text) must not hold it either
    assert "peeters" not in out.lower().replace("\\u00ad", "").replace("\\n", " ")
    assert "zorvex" not in out.lower().replace("\\u00ad", "").replace("\\n", " ")


@pytest.mark.parametrize("system", ["The client is {s}.", [{"type": "text", "text": "Client {s}"}]])
def test_only_the_leading_identity_lines_of_the_system_field_are_verbatim(
    state: GatewayState, system: Any
) -> None:
    filled = (
        system.format(s=COMPANY)
        if isinstance(system, str)
        else [{**b, "text": b["text"].format(s=COMPANY)} for b in system]
    )
    before = state.stats().get("system_values_passed", 0)
    out = json.loads(egress(state, {"system": filled, "messages": []}))
    assert out["system"] == filled  # a short leading line: never rewritten, never refused
    assert state.stats()["system_values_passed"] == before + 1


def test_the_system_instructions_after_the_identity_lines_are_masked(state: GatewayState) -> None:
    identity = [
        {"type": "text", "text": "x-app-billing-header: v=1;"},
        {"type": "text", "text": "You are the app.", "cache_control": {"type": "ephemeral"}},
    ]
    long_block = {"type": "text", "text": INSTRUCTIONS + f" Folder: c:/clients/{COMPANY}.",
                  "cache_control": {"type": "ephemeral", "ttl": "1h"}}  # fmt: skip
    short_after = {"type": "text", "text": f"Client {COMPANY}"}
    out = json.loads(
        egress(state, {"system": [*identity, long_block, short_after], "messages": []})
    )
    assert out["system"][:2] == identity  # byte-identical identity lines
    assert out["system"][2]["cache_control"] == long_block["cache_control"]
    assert out["system"][2]["text"].startswith(INSTRUCTIONS)
    assert "zorvex" not in json.dumps(out["system"]).lower()  # also the short block after it
    again = json.loads(
        egress(state, {"system": [*identity, long_block, short_after], "messages": []})
    )
    assert again["system"] == out["system"]  # deterministic: the prompt cache keeps hitting
