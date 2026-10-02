"""Unit tests for scripts/pre_production_checklist.py.

Only the pure aggregation/formatting logic is exercised here — the actual
checks need live PostgreSQL/Supabase and are deliberately not run in CI.
Loading the module at import time also proves the script imports cleanly
with no database configured/reachable.
"""

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "pre_production_checklist.py"

_spec = importlib.util.spec_from_file_location("pre_production_checklist", _SCRIPT_PATH)
checklist = importlib.util.module_from_spec(_spec)
# Register before exec: dataclasses resolves cls.__module__ via sys.modules.
sys.modules[_spec.name] = checklist
_spec.loader.exec_module(checklist)  # must not require a DB to import


def result(name: str, status: str, detail: str = "") -> "checklist.CheckResult":
    return checklist.CheckResult(name=name, status=status, detail=detail)


# ----------------------------------------------------------------------------
# summarize_exit_code
# ----------------------------------------------------------------------------


def test_exit_code_zero_when_all_pass_or_skip():
    results = [
        result("db", checklist.PASS, "connected"),
        result("health", checklist.SKIP, "no url"),
    ]
    assert checklist.summarize_exit_code(results) == 0


def test_exit_code_one_when_any_check_fails():
    results = [
        result("db", checklist.PASS),
        result("jwks", checklist.FAIL, "keys empty"),
        result("health", checklist.SKIP),
    ]
    assert checklist.summarize_exit_code(results) == 1


def test_exit_code_zero_for_empty_results():
    assert checklist.summarize_exit_code([]) == 0


# ----------------------------------------------------------------------------
# format_results_table
# ----------------------------------------------------------------------------


def test_table_contains_every_check_with_status_and_detail():
    results = [
        result("database & migrations", checklist.PASS, "connected; schema at head abc123"),
        result("LISTEN/NOTIFY round-trip", checklist.FAIL, "timed out"),
        result("/health scheduler liveness", checklist.SKIP, "no --api-base-url provided"),
    ]

    table = checklist.format_results_table(results)

    assert "CHECK" in table and "STATUS" in table and "DETAIL" in table
    for row in results:
        assert row.name in table
        assert row.detail in table
    assert "PASS" in table and "FAIL" in table and "SKIP" in table


def test_table_columns_are_aligned():
    results = [
        result("short", checklist.PASS, "ok"),
        result("a much longer check name", checklist.SKIP, "reason"),
    ]

    lines = checklist.format_results_table(results).splitlines()

    # STATUS starts at the same column on every row.
    status_columns = {line.index(status) for line, status in zip(lines[2:], ["PASS", "SKIP"])}
    assert len(status_columns) == 1


def test_skip_rows_keep_their_reason():
    table = checklist.format_results_table(
        [result("repository SQL smoke", checklist.SKIP, "DATABASE_URL not configured")]
    )
    assert "DATABASE_URL not configured" in table


# ----------------------------------------------------------------------------
# _one_line detail normalization
# ----------------------------------------------------------------------------


def test_one_line_collapses_newlines_and_runs_of_whitespace():
    messy = "ProgrammingError: relation\n   does not\texist\n\nLINE 3"
    assert checklist._one_line(messy) == "ProgrammingError: relation does not exist LINE 3"


def test_one_line_truncates_very_long_details():
    flattened = checklist._one_line("x" * 1000)
    assert len(flattened) == checklist.MAX_DETAIL_LENGTH
    assert flattened.endswith("...")
