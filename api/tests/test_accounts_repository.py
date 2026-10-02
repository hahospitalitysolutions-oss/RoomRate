"""Unit tests for AccountsRepository.

Fake-connection style (no live DB). Two groups:

``get_or_create_account_for_identity`` — the fast path must resolve a known,
unchanged identity with a single read-only SELECT — no advisory lock, no
upsert. A miss or changed token fields must fall through to the original
lock + upsert write path.

``get_account_overview`` — what GET /me reports about onboarding state, and
therefore what setupGuard routes on.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from api.repositories.accounts_repository import AccountsRepository
from api.tests._fakes import FakeEngine, FakeResult, install_scripted_engine


ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000123")
IDENTITY_ID = UUID("00000000-0000-0000-0000-000000000999")


def _patch_engine(monkeypatch, results: list[FakeResult]) -> FakeEngine:
    return install_scripted_engine(monkeypatch, "api.repositories.accounts_repository", results)


def _resolve(email="owner@example.com", display_name="Owner") -> uuid.UUID:
    return AccountsRepository().get_or_create_account_for_identity(
        auth_provider="supabase",
        auth_subject="supabase-user-123",
        email=email,
        display_name=display_name,
    )


def _fast_row(email="owner@example.com", display_name="Owner") -> dict:
    return {"email": email, "display_name": display_name, "account_id": ACCOUNT_ID}


def test_known_unchanged_identity_resolves_with_single_readonly_select(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rows=[_fast_row()])])

    account_id = _resolve()

    assert account_id == ACCOUNT_ID
    # Exactly ONE statement: the read-only membership SELECT. No lock, no writes.
    assert len(engine.connection.calls) == 1
    sql, params = engine.connection.calls[0]
    assert sql.lstrip().startswith("SELECT")
    assert "pg_advisory_xact_lock" not in sql
    assert "INSERT" not in sql
    assert params["auth_provider"] == "supabase"
    assert params["auth_subject"] == "supabase-user-123"
    # Read-only connection, never a write transaction.
    assert engine.connect_calls == 1
    assert engine.begin_calls == 0


def test_none_token_display_name_still_takes_fast_path(monkeypatch):
    # The upsert COALESCEs display_name, so a token without one changes nothing.
    engine = _patch_engine(monkeypatch, [FakeResult(rows=[_fast_row()])])

    account_id = _resolve(display_name=None)

    assert account_id == ACCOUNT_ID
    assert engine.begin_calls == 0


def test_unknown_identity_falls_through_to_lock_and_upsert(monkeypatch):
    engine = _patch_engine(
        monkeypatch,
        [
            FakeResult(rows=[]),  # fast-path SELECT misses
            FakeResult(),  # advisory lock
            FakeResult(rows=[{"id": IDENTITY_ID}]),  # identity upsert RETURNING
            FakeResult(rows=[{"account_id": ACCOUNT_ID}]),  # membership SELECT
        ],
    )

    account_id = _resolve()

    assert account_id == ACCOUNT_ID
    assert engine.begin_calls == 1
    executed_sql = [sql for sql, _ in engine.connection.calls]
    assert "pg_advisory_xact_lock" in executed_sql[1]
    assert "INSERT INTO roomrate_user_identities" in executed_sql[2]
    assert "ON CONFLICT (auth_provider, auth_subject)" in executed_sql[2]


def test_changed_email_falls_through_to_upsert(monkeypatch):
    engine = _patch_engine(
        monkeypatch,
        [
            FakeResult(rows=[_fast_row(email="old@example.com")]),
            FakeResult(),  # advisory lock
            FakeResult(rows=[{"id": IDENTITY_ID}]),  # identity upsert RETURNING
            FakeResult(rows=[{"account_id": ACCOUNT_ID}]),  # membership SELECT
        ],
    )

    account_id = _resolve(email="new@example.com")

    assert account_id == ACCOUNT_ID
    # The stale email must be rewritten via the locked upsert path.
    assert engine.begin_calls == 1
    assert any("INSERT INTO roomrate_user_identities" in sql for sql, _ in engine.connection.calls)


def test_changed_display_name_falls_through_to_upsert(monkeypatch):
    engine = _patch_engine(
        monkeypatch,
        [
            FakeResult(rows=[_fast_row(display_name="Old Name")]),
            FakeResult(),  # advisory lock
            FakeResult(rows=[{"id": IDENTITY_ID}]),  # identity upsert RETURNING
            FakeResult(rows=[{"account_id": ACCOUNT_ID}]),  # membership SELECT
        ],
    )

    account_id = _resolve(display_name="New Name")

    assert account_id == ACCOUNT_ID
    assert engine.begin_calls == 1


def test_miss_without_membership_creates_account_and_membership(monkeypatch):
    engine = _patch_engine(
        monkeypatch,
        [
            FakeResult(rows=[]),  # fast-path SELECT misses
            FakeResult(),  # advisory lock
            FakeResult(rows=[{"id": IDENTITY_ID}]),  # identity upsert RETURNING
            FakeResult(rows=[]),  # membership SELECT: none yet
            FakeResult(),  # account INSERT
            FakeResult(),  # membership INSERT
        ],
    )

    account_id = _resolve()

    assert isinstance(account_id, uuid.UUID)
    executed_sql = [sql for sql, _ in engine.connection.calls]
    assert any("INSERT INTO roomrate_accounts" in sql for sql in executed_sql)
    assert any("INSERT INTO roomrate_memberships" in sql for sql in executed_sql)


# ---------------------------------------------------------------------------
# get_account_overview — what GET /me reports about onboarding state
# ---------------------------------------------------------------------------

OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-00000000077b")


def _owned_property_row(selected: str | None) -> dict:
    return {
        "id": OWNED_PROPERTY_ID,
        "display_name": "Villa Nefeli",
        "city": "Lindos",
        "raw_destination": "Lindos, Rhodes",
        "canonical_destination": "lindos",
        "selected_room_type_category": selected,
    }


def test_overview_reports_no_selection_when_the_user_never_chose_a_room(monkeypatch):
    """The pin: discovered rooms are NOT a selection.

    This is the state right after an owned_property_room_discovery job
    completes -- the catalog sync has inserted room types, but the user has
    not reached step 2 of the wizard. /me previously substituted the newest
    discovered category here and reported onboarding_complete, so setupGuard
    (Boolean(owned_property_id) && Boolean(selected_room_type_category))
    waved the user straight past steps 2-3 of the wizard.
    """
    engine = _patch_engine(monkeypatch, [FakeResult(rows=[_owned_property_row(None)])])

    overview = AccountsRepository().get_account_overview(ACCOUNT_ID)

    assert overview["selected_room_type_category"] is None
    assert overview["onboarding_complete"] is False
    # The property itself is still reported: the wizard needs it to resume
    # at step 2 rather than re-running step 1.
    assert overview["owned_property_id"] == OWNED_PROPERTY_ID
    assert overview["property_name"] == "Villa Nefeli"
    # ONE statement: the discovered-room-types lookup that fabricated the
    # selection is gone. A re-added fallback fails both assertions.
    assert len(engine.connection.calls) == 1
    sql, params = engine.connection.calls[0]
    assert "roomrate_owned_property_room_types" not in sql
    assert params["account_id"] == ACCOUNT_ID
    # Read-only: reporting onboarding state must never write.
    assert engine.connect_calls == 1
    assert engine.begin_calls == 0


def test_overview_reports_the_explicitly_selected_room_category(monkeypatch):
    engine = _patch_engine(
        monkeypatch, [FakeResult(rows=[_owned_property_row("family_suite")])]
    )

    overview = AccountsRepository().get_account_overview(ACCOUNT_ID)

    # The value written by PUT /owned-property/{id}/selected-room-type, passed
    # through verbatim -- this is the ONLY way /me reports a category.
    assert overview["selected_room_type_category"] == "family_suite"
    assert overview["onboarding_complete"] is True
    assert overview["destination"] == "Lindos"
    assert overview["raw_destination"] == "Lindos, Rhodes"
    assert overview["canonical_destination"] == "lindos"
    assert len(engine.connection.calls) == 1


def test_overview_of_an_account_without_a_property_is_all_empty(monkeypatch):
    engine = _patch_engine(monkeypatch, [FakeResult(rows=[])])

    overview = AccountsRepository().get_account_overview(ACCOUNT_ID)

    assert overview == {
        "owned_property_id": None,
        "property_name": None,
        "destination": None,
        "raw_destination": None,
        "canonical_destination": None,
        "selected_room_type_category": None,
        "onboarding_complete": False,
    }
    assert len(engine.connection.calls) == 1
