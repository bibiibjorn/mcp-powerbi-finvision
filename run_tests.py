#!/usr/bin/env python3
"""Single entrypoint for running the test suite.

Usage:
    python run_tests.py                # all tests, terse output
    python run_tests.py --fast         # skip slow tests (marked in tests/conftest.py: gateway CLI/hook/images/server, headless CLI, golden sweep)
    python run_tests.py --verbose      # full per-test output
    python run_tests.py --cov          # with coverage report (HTML + terminal)
    python run_tests.py --file <name>  # one test file (basename match)
    python run_tests.py --marker slow  # only tests with given mark
    python run_tests.py -- <pytest args...>   # passthrough after `--`

Examples:
    python run_tests.py --file wait_conditions
    python run_tests.py --file model_operations_handler --verbose
    python run_tests.py --fast --cov
    python run_tests.py -- -k "rename and not slow"

The script always runs from the repo root and respects pytest.ini /
python/pyproject.toml. It exits with pytest's exit code so CI can use it.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
TESTS_DIR = REPO_ROOT / "tests"


def _resolve_test_target(name: str) -> str:
    """Map --file <foo> to tests/test_foo.py (or tests/<foo> if exact)."""
    candidates = [
        TESTS_DIR / name,
        TESTS_DIR / f"{name}.py",
        TESTS_DIR / f"test_{name}.py",
    ]
    for c in candidates:
        if c.is_file():
            return str(c.relative_to(REPO_ROOT))
    matches = sorted(TESTS_DIR.glob(f"*{name}*.py"))
    if len(matches) == 1:
        return str(matches[0].relative_to(REPO_ROOT))
    if len(matches) > 1:
        sys.stderr.write(
            f"--file '{name}' matches multiple files:\n  "
            + "\n  ".join(str(m.relative_to(REPO_ROOT)) for m in matches)
            + "\n"
        )
        sys.exit(2)
    sys.stderr.write(f"--file '{name}' did not match any tests/test_*.py\n")
    sys.exit(2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the MCP-PowerBi-Finvision test suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in (__doc__ or "") else None,
    )
    parser.add_argument("--fast", action="store_true", help="skip slow tests")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="verbose pytest output"
    )
    parser.add_argument(
        "--cov", action="store_true", help="emit coverage report (HTML + terminal)"
    )
    parser.add_argument(
        "--file",
        metavar="NAME",
        help="run a single test file (basename, with or without test_ prefix)",
    )
    parser.add_argument(
        "--marker",
        "-m",
        metavar="EXPR",
        help="pytest -m marker expression (e.g. 'not slow', 'integration')",
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="list collected tests without running",
    )
    parser.add_argument(
        "passthrough",
        nargs=argparse.REMAINDER,
        help="extra args forwarded to pytest after '--'",
    )

    args = parser.parse_args()
    cmd: list[str] = [sys.executable, "-m", "pytest"]

    if args.verbose:
        cmd.append("-v")
    else:
        cmd.append("-q")

    if args.fast:
        cmd.extend(["-m", "not slow"])
    elif args.marker:
        cmd.extend(["-m", args.marker])

    if args.cov:
        cmd.extend(
            [
                "--cov=core",
                "--cov=src",
                "--cov=server",
                "--cov-report=html",
                "--cov-report=term",
            ]
        )
    else:
        # Default config in pyproject.toml enables --cov. When invoked from
        # the repo root via plain `pytest`, the .ini wins and coverage is OFF.
        # We mirror that: no coverage unless --cov is explicit.
        pass

    if args.collect_only:
        cmd.append("--collect-only")

    if args.file:
        cmd.append(_resolve_test_target(args.file))

    # Forward anything after `--` directly to pytest
    extra = args.passthrough or []
    if extra and extra[0] == "--":
        extra = extra[1:]
    cmd.extend(extra)

    print(f"$ {' '.join(shlex.quote(c) for c in cmd)}", flush=True)
    return subprocess.call(cmd, cwd=str(REPO_ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
