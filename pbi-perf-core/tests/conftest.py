"""Shared fixtures.

The reference capture is a real DAX Studio export taken 2026-09-02 09:34 from
the R0201 "P&L Summary" matrix visual against the S02 semantic model.  It is the
oracle for every parity test: DAX Studio already computed the right answer from
these exact events, so our code must reproduce it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def server_timings_export() -> dict:
    with open(FIXTURES / "servertimings.json", encoding="utf-8-sig") as handle:
        return json.load(handle)


@pytest.fixture(scope="session")
def query_plan_export() -> dict:
    with open(FIXTURES / "queryplan.json", encoding="utf-8-sig") as handle:
        return json.load(handle)


@pytest.fixture(scope="session")
def acceptance_query() -> str:
    return (FIXTURES / "pls_matrix_query.dax").read_text(encoding="utf-8")
