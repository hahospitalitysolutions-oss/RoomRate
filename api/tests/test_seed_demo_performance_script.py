"""scripts/seed_demo_performance.py without a database: which room types it uses, and where it refuses to write."""

import importlib.util
import sys
from pathlib import Path
from uuid import UUID

import pytest

from api.config import settings

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo_performance.py"


@pytest.fixture
def seed():
    spec = importlib.util.spec_from_file_location("seed_demo_performance", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def room(number, label, price):
    return {"id": UUID(int=number), "room_type": label, "sample_price_per_night_eur": price}


def test_up_to_four_room_types_cheapest_first_priced_from_the_scrape(seed):
    picked = seed.demo_room_types([
        room(1, "Suite", 260), room(2, "Double", 95), room(3, "Unpriced", None),
        room(4, "Family", 150), room(5, "Sea view", 120),
    ])

    assert [(room_type_id.int, demo.label, demo.rooms, demo.adr_eur) for room_type_id, demo in picked] == [
        (2, "Double", 4, 95.0),
        (5, "Sea view", 5, 120.0),
        (4, "Family", 2, 150.0),
        (1, "Suite", 1, 260.0),
    ]
    assert picked[2][1].family


def test_a_database_off_this_computer_needs_yes(seed, monkeypatch, capsys):
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg2://user:secret@ep-demo.neon.tech/roomrate")
    monkeypatch.setattr(sys, "argv", ["seed_demo_performance.py"])

    assert seed.main() == 1
    output = capsys.readouterr().out
    assert "ep-demo.neon.tech" in output and "--yes" in output
    assert "secret" not in output
