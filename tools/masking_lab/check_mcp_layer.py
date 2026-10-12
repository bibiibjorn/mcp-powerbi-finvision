"""Lab check: the MCP masking layer against a running LAB gateway (``lab.py start`` first).

    python tools/masking_lab/check_mcp_layer.py --dir <lab dir>

Points this process at the lab gateway (its home and port, read from ``<lab dir>/lab.json``),
switches the MCP masking layer on for this process only, and checks with synthetic names that:
a tool result is masked and carries the lab install's marker as its first member, a pseudonym in
a tool argument comes back as the real name, and a log line is masked. Exit 0 = all checks passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NAMES = ("Zorblax Holdings NV", "Quillfeather Logistics BV")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", required=True, type=Path)
    args = parser.parse_args(argv)
    info = json.loads((args.dir / "lab.json").read_text(encoding="utf-8"))
    if int(info["port"]) == 8890:
        print("refused: that is the production port")
        return 2
    os.environ["FINVISION_MASKING_HOME"] = str(info["home"])
    os.environ["FINVISION_GATEWAY_PORT"] = str(info["port"])
    os.environ["FINVISION_MCP_MASKING"] = "1"
    sys.path.insert(0, str(REPO))

    from mcp import types  # noqa: PLC0415

    from core.client_guard.marker import MARKER_FIELD, marker  # noqa: PLC0415
    from server import masking_layer  # noqa: PLC0415

    failures: list[str] = []

    def check(ok: bool, what: str) -> None:
        print(("PASS " if ok else "FAIL ") + what)
        if not ok:
            failures.append(what)

    payload = {
        "success": True,
        "rows": [
            {"[Customer]": NAMES[0], "[Revenue]": 1200},
            {"[Customer]": NAMES[1], "[Revenue]": 800},
        ],
    }
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload))], isError=False
    )
    masked = masking_layer.mask_call_result("04_Run_DAX", result)
    text = masked.content[0].text
    check(not masked.isError, "result masked without error")
    check(all(n not in text for n in NAMES), "no synthetic client name left in the result")
    obj = json.loads(text)
    lab_marker = marker(Path(info["home"]))
    check(next(iter(obj)) == MARKER_FIELD, "marker is the first member")
    check(obj.get(MARKER_FIELD) == lab_marker, "marker is the lab install's marker")
    pseudonym = obj["rows"][0]["[Customer]"]
    check(pseudonym.startswith("CUS_"), f"customer became a pseudonym ({pseudonym})")

    query = f'EVALUATE FILTER(Customers, Customers[Name] = "{pseudonym}")'
    back = masking_layer.unmask_arguments({"query": query})
    check(NAMES[0] in back["query"], "pseudonym in a tool argument is the real name again")

    line = masking_layer.mask_log_message(f"slow query for {NAMES[1]}")
    check(line is not None and NAMES[1] not in line, "log line masked")

    print("ALL CHECKS PASSED" if not failures else f"{len(failures)} CHECK(S) FAILED")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
