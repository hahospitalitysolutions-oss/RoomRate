"""Unit tests for scripts/backfill_owned_room_types.py (no database needed).

The module is loaded with ``dotenv.load_dotenv`` replaced by a recorder, so
importing it never copies a developer's real .env into this test process.
"""

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import dotenv
import pytest

from api.tests._fakes import FakeConnection, FakeEngine, FakeResult

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "backfill_owned_room_types.py"

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")
JOB_ID = UUID("00000000-0000-0000-0000-000000000777")


@pytest.fixture
def backfill(monkeypatch):
    dotenv_calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: dotenv_calls.append((args, kwargs)))
    spec = importlib.util.spec_from_file_location("backfill_owned_room_types", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    # Register before exec: dataclasses resolves cls.__module__ via sys.modules.
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.dotenv_calls = dotenv_calls
    return module


def _package(room_type: str, price: str) -> dict:
    return {
        "owned_property_id": OWNED_PROPERTY_ID,
        "run_job_id": JOB_ID,
        "room_type": room_type,
        "meals": None,
        "free_cancellation": None,
        "facilities": None,
        "price_per_night_eur": Decimal(price),
    }


def _history_results() -> list[FakeResult]:
    return [
        FakeResult(rows=[{"account_id": ACCOUNT_ID}]),  # accounts with an active property
        FakeResult(rows=[
            _package("Δίκλινο Δωμάτιο με 2 Μονά Κρεβάτια, Ντους και Μπαλκόνι", "60.00"),
            _package("Δίκλινο Δωμάτιο με Ντους και Μπαλκόνι 1/11", "55.00"),
            _package("Μονόκλινο Δωμάτιο με Ντους και Μπαλκόνι", "40.00"),
        ]),
        FakeResult(rows=[{"owned_property_id": OWNED_PROPERTY_ID,
                          "room_type": "Δίκλινο Δωμάτιο με 2 Μονά Κρεβάτια, Ντους και Μπαλκόνι"}]),
    ]


def test_script_loads_dotenv_with_utf8_sig(backfill):
    assert backfill.dotenv_calls
    args, kwargs = backfill.dotenv_calls[0]
    assert kwargs.get("encoding") == "utf-8-sig"
    assert str(args[0]).endswith(".env")


def test_dry_run_reports_counts_and_never_writes(backfill, capsys):
    engine = FakeEngine(FakeConnection(_history_results()))

    totals = backfill.run_backfill(engine, account_id=None, dry_run=True)

    assert (totals.accounts, totals.room_types_seen, totals.room_types_added) == (1, 3, 2)
    executed = [sql for sql, _ in engine.connection.calls]
    assert not any("INSERT INTO" in sql for sql in executed)
    # A dry run never opens a write transaction.
    assert engine.begin_calls == 0
    # Every completed run of the account, not one job.
    assert "sr.status = 'completed'" in executed[1]
    output = capsys.readouterr().out
    assert "would add=2" in output
    # Counts only: no account id or room name is printed.
    assert str(ACCOUNT_ID) not in output
    assert "Δωμάτιο" not in output


def test_apply_inserts_the_missing_rooms_in_a_transaction(backfill, capsys):
    engine = FakeEngine(
        FakeConnection(_history_results() + [FakeResult(rows=[{"id": UUID(int=1)}]), FakeResult(rows=[{"id": UUID(int=2)}])])
    )

    totals = backfill.run_backfill(engine, account_id=ACCOUNT_ID, dry_run=False)

    assert totals.room_types_added == 2
    assert engine.begin_calls == 1
    accounts_sql, accounts_params = engine.connection.calls[0]
    assert "account_id = :account_id" in accounts_sql
    assert accounts_params == {"account_id": ACCOUNT_ID}
    inserted = [params["room_type"] for sql, params in engine.connection.calls if "INSERT INTO" in sql]
    assert inserted == ["Δίκλινο Δωμάτιο με Ντους και Μπαλκόνι", "Μονόκλινο Δωμάτιο με Ντους και Μπαλκόνι"]
    assert "added=2" in capsys.readouterr().out
