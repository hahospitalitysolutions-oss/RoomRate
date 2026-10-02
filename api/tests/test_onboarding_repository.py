from datetime import date
from uuid import UUID

from api.repositories.onboarding_repository import OnboardingRepository
from api.schemas.onboarding import OwnedPropertyOnboardingCreate

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
OWNED_PROPERTY_ID = UUID("00000000-0000-0000-0000-000000000456")


class FakeResult:
    """Scripted result: rows for mappings(), rowcount for DELETE/UPDATE."""

    def __init__(self, rows=None, rowcount: int = 0):
        self._rows = rows if rows is not None else []
        # rowcount is read by delete_owned_property; replace_owned_property
        # never inspects it, so passing one here is not itself an assertion of
        # what got deleted -- the SQL/param assertions below are.
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def one(self):
        return self._rows[0]


class FakeConnection:
    """Records every statement and returns the next scripted result."""

    def __init__(self, results=None):
        self.results = list(results) if results else []
        self.executed: list[tuple[str, dict]] = []

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params or {}))
        assert self.results, (
            f"no scripted result for statement #{len(self.executed)}: "
            f"{' '.join(str(sql).split())[:80]}"
        )
        return self.results.pop(0)


class FakeConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeEngine:
    """Records whether the caller opened a transaction or a bare connection.

    Both return the same connection, so without ``opened`` a method that
    dropped ``begin()`` for ``connect()`` -- losing atomicity entirely -- would
    still pass every assertion.
    """

    def __init__(self, connection):
        self.connection = connection
        self.opened: list[str] = []

    def connect(self):
        self.opened.append("connect")
        return FakeConnectionContext(self.connection)

    def begin(self):
        self.opened.append("begin")
        return FakeConnectionContext(self.connection)


def _patch_engine(monkeypatch, connection) -> FakeEngine:
    """Returns the engine so tests can assert how it was opened."""
    engine = FakeEngine(connection)
    monkeypatch.setattr(
        "api.repositories.onboarding_repository.get_engine",
        lambda *args, **kwargs: engine,
    )
    return engine


def _statement(connection, needle: str) -> str:
    """The one executed statement containing `needle`, whitespace-normalized."""
    matches = [" ".join(sql.split()) for sql, _ in connection.executed if needle in sql]
    assert len(matches) == 1, f"expected exactly one {needle!r} statement, got {len(matches)}"
    return matches[0]


def _replacement_request(raw_destination: str = "Faliraki") -> OwnedPropertyOnboardingCreate:
    return OwnedPropertyOnboardingCreate(
        display_name="Bellezza",
        booking_url="https://www.booking.com/hotel/gr/bellezza.html",
        city="Faliraki",
        raw_destination=raw_destination,
        check_in=date(2030, 7, 1),
        check_out=date(2030, 7, 5),
    )


def test_get_selected_room_type_returns_cheapest_active_row_in_selected_category(monkeypatch):
    row = {"room_type": "Double Room with Balcony", "room_type_category": "double"}
    connection = FakeConnection(results=[FakeResult(rows=[row])])
    monkeypatch.setattr(
        "api.repositories.onboarding_repository.get_engine",
        lambda *args, **kwargs: FakeEngine(connection),
    )

    result = OnboardingRepository().get_selected_room_type(ACCOUNT_ID, OWNED_PROPERTY_ID)

    assert result == row
    sql, params = connection.executed[0]
    assert "selected_room_type_category" in sql
    # The row id + samples ride along: the room-matching agent keys its match
    # rows on rt.id and feeds the samples into the reference-room payload.
    assert "rt.id" in sql
    assert "rt.owned_property_id" in sql
    assert "rt.sample_meals" in sql
    assert "rt.sample_free_cancellation" in sql
    assert "rt.sample_facilities" in sql
    # Cheapest row first within the user's chosen category, newest as
    # tie-break, so the baseline does not drift between same-category rows.
    assert "sample_price_per_night_eur ASC NULLS LAST" in sql
    assert "updated_at DESC" in sql
    assert "LIMIT 1" in sql
    assert params["account_id"] == ACCOUNT_ID
    assert params["owned_property_id"] == OWNED_PROPERTY_ID


def test_get_selected_room_type_returns_none_when_nothing_selected(monkeypatch):
    connection = FakeConnection(results=[FakeResult(rows=[])])
    monkeypatch.setattr(
        "api.repositories.onboarding_repository.get_engine",
        lambda *args, **kwargs: FakeEngine(connection),
    )

    assert OnboardingRepository().get_selected_room_type(ACCOUNT_ID, OWNED_PROPERTY_ID) is None


def test_get_owned_room_type_fetches_one_active_row_by_id(monkeypatch):
    room_type_id = UUID("00000000-0000-0000-0000-000000000abc")
    row = {
        "id": room_type_id,
        "owned_property_id": OWNED_PROPERTY_ID,
        "room_type": "Double Room",
        "room_type_category": "double",
        "sample_price_per_night_eur": 95.0,
    }
    connection = FakeConnection(results=[FakeResult(rows=[row])])
    _patch_engine(monkeypatch, connection)

    result = OnboardingRepository().get_owned_room_type(ACCOUNT_ID, room_type_id)

    assert result == row
    sql, params = connection.executed[0]
    assert "roomrate_owned_property_room_types" in sql
    assert "is_active = true" in sql
    assert params == {"account_id": ACCOUNT_ID, "owned_room_type_id": room_type_id}


def test_get_owned_room_type_returns_none_for_a_foreign_or_missing_row(monkeypatch):
    connection = FakeConnection(results=[FakeResult(rows=[])])
    _patch_engine(monkeypatch, connection)

    assert OnboardingRepository().get_owned_room_type(ACCOUNT_ID, UUID(int=1)) is None


def test_replace_owned_property_clears_rooms_and_baseline(monkeypatch):
    """Swapping the property must not leave the old hotel's rooms behind."""
    # One scripted result per statement, in execution order: FakeConnection pops
    # them as they run, so a short list starves the final RETURNING.
    connection = FakeConnection(
        results=[
            FakeResult(rows=[{"canonical_destination": "faliraki"}]),  # SELECT ... FOR UPDATE
            FakeResult(rowcount=2),                                    # DELETE room types
            FakeResult(rows=[{"id": OWNED_PROPERTY_ID}]),              # UPDATE ... RETURNING id
        ]
    )
    _patch_engine(monkeypatch, connection)

    result = OnboardingRepository().replace_owned_property(
        ACCOUNT_ID, OWNED_PROPERTY_ID, _replacement_request(raw_destination="Faliraki")
    )

    assert result == {"id": OWNED_PROPERTY_ID, "market_changed": False}

    # Old room types always go: they belong to the previous hotel. Pinning the
    # full predicate (not just the table name) is what actually proves this is
    # scoped to one account's one property, not every property in the account.
    rooms_delete = _statement(connection, "DELETE FROM roomrate_owned_property_room_types")
    assert "WHERE account_id = :account_id AND owned_property_id = :owned_property_id" in rooms_delete

    # Same market -> tracked competitors are still valid and must survive.
    joined = " ".join(" ".join(sql.split()) for sql, _ in connection.executed)
    assert "DELETE FROM roomrate_tracked_competitors" not in joined

    update = _statement(connection, "UPDATE roomrate_owned_properties")
    assert "WHERE account_id = :account_id AND id = :owned_property_id AND is_active = true" in update
    # The baseline room pointed at a deleted room type.
    assert "selected_room_type_category = NULL" in update
    # Pin the whole SET list in order. A bind param dict check alone does not
    # catch a column quietly dropped from SET: SQLAlchemy ignores an unused
    # bind, so the params dict is unchanged even though e.g. `address` (or
    # latitude/longitude) would silently keep the previous hotel's value.
    assert (
        "SET display_name = :display_name, booking_url = :booking_url, "
        "address = :address, city = :city, raw_destination = :raw_destination, "
        "canonical_destination = :canonical_destination, country = :country, "
        "property_type = :property_type, latitude = :latitude, longitude = :longitude, "
        "location_source = 'booking_search', location_confidence = 0.90, "
        "selected_room_type_category = NULL, updated_at = now() WHERE"
    ) in update

    # The SET-clause text pins which columns are assigned; this pins what
    # they are bound to -- pointing at a different Booking page is the entire
    # feature, so the NEW property's fields must actually reach the params,
    # not just the old ones being silently kept.
    update_params = connection.executed[-1][1]
    assert update_params == {
        "account_id": ACCOUNT_ID,
        "owned_property_id": OWNED_PROPERTY_ID,
        "display_name": "Bellezza",
        "booking_url": "https://www.booking.com/hotel/gr/bellezza.html",
        "address": None,
        "city": "Faliraki",
        "raw_destination": "Faliraki",
        "canonical_destination": "faliraki",
        "country": None,
        "property_type": None,
        "latitude": None,
        "longitude": None,
    }

    # Every statement received the account id as a bind param -- the
    # WHERE-clause fencing itself is pinned above (and, for the lock read, in
    # test_replace_owned_property_locks_the_row_for_the_whole_transaction).
    assert all(params["account_id"] == ACCOUNT_ID for _, params in connection.executed)


def test_replace_owned_property_drops_tracked_when_the_market_changes(monkeypatch):
    """A different destination makes the tracked competitor set meaningless."""
    connection = FakeConnection(
        results=[
            FakeResult(rows=[{"canonical_destination": "faliraki"}]),  # SELECT ... FOR UPDATE
            FakeResult(rowcount=2),                                    # DELETE room types
            FakeResult(rowcount=1),                                    # DELETE tracked competitors
            FakeResult(rows=[{"id": OWNED_PROPERTY_ID}]),              # UPDATE ... RETURNING id
        ]
    )
    _patch_engine(monkeypatch, connection)

    result = OnboardingRepository().replace_owned_property(
        ACCOUNT_ID, OWNED_PROPERTY_ID, _replacement_request(raw_destination="Lindos")
    )

    assert result == {"id": OWNED_PROPERTY_ID, "market_changed": True}
    competitors_delete = _statement(connection, "DELETE FROM roomrate_tracked_competitors")
    assert "WHERE account_id = :account_id AND owned_property_id = :owned_property_id" in competitors_delete


def test_replace_owned_property_returns_none_for_another_account(monkeypatch):
    """A property that is not ours must be indistinguishable from missing."""
    connection = FakeConnection(results=[FakeResult(rows=[])])
    _patch_engine(monkeypatch, connection)

    result = OnboardingRepository().replace_owned_property(
        ACCOUNT_ID, OWNED_PROPERTY_ID, _replacement_request()
    )

    assert result is None
    # Nothing was mutated after the ownership check failed.
    # (Not a plain "UPDATE" substring check: the locking read itself is a
    # SELECT ... FOR UPDATE, so that would trip on its own SQL text. Normalized
    # like the other tests, so reformatted SQL cannot pass this vacuously.)
    joined = " ".join(" ".join(sql.split()) for sql, _ in connection.executed)
    assert "DELETE" not in joined
    assert "UPDATE roomrate_owned_properties" not in joined


def test_replace_owned_property_locks_the_row_for_the_whole_transaction(monkeypatch):
    """Without FOR UPDATE two concurrent replaces could both read the old market.

    Both halves matter and neither works alone: a row lock outside a
    transaction is released immediately, and a transaction without the lock
    still lets a concurrent replace read a stale ``canonical_destination`` and
    keep competitors it should have dropped.
    """
    connection = FakeConnection(
        results=[
            FakeResult(rows=[{"canonical_destination": "faliraki"}]),  # SELECT ... FOR UPDATE
            FakeResult(rowcount=2),                                    # DELETE room types
            FakeResult(rows=[{"id": OWNED_PROPERTY_ID}]),              # UPDATE ... RETURNING id
        ]
    )
    engine = _patch_engine(monkeypatch, connection)

    OnboardingRepository().replace_owned_property(
        ACCOUNT_ID, OWNED_PROPERTY_ID, _replacement_request()
    )

    lock_statement = _statement(connection, "FOR UPDATE")
    assert "SELECT canonical_destination" in lock_statement
    # is_active = true here is the load-bearing copy of this clause: it is
    # what makes replacing a deactivated property 404 instead of resurrecting
    # it. The UPDATE's copy of the same predicate is a defensive re-check, not
    # the guarantee itself -- the row is already locked by this point.
    assert (
        "WHERE account_id = :account_id AND id = :owned_property_id AND is_active = true"
        in lock_statement
    )
    # begin(), not connect(): the partial states in between are all invalid.
    assert engine.opened == ["begin"]


def test_delete_owned_property_is_account_fenced(monkeypatch):
    connection = FakeConnection(results=[FakeResult(rowcount=1)])
    engine = _patch_engine(monkeypatch, connection)

    deleted = OnboardingRepository().delete_owned_property(ACCOUNT_ID, OWNED_PROPERTY_ID)

    assert deleted is True
    sql, params = connection.executed[0]
    normalized = " ".join(sql.split())
    assert "DELETE FROM roomrate_owned_properties" in normalized
    # The whole predicate, not fragments: SQLAlchemy ignores unused binds, so a
    # dropped fence would leave the params assertion below green (Task 2 lesson).
    assert "WHERE account_id = :account_id AND id = :owned_property_id" in normalized
    # No is_active fence, pinned deliberately: a deactivated property must stay
    # deletable, and a "consistency pass" adding the fence would break that.
    assert "is_active" not in normalized
    assert params == {"account_id": ACCOUNT_ID, "owned_property_id": OWNED_PROPERTY_ID}
    # begin(), not connect(): connect() rolls back on exit, but rowcount is read
    # before the rollback -- the delete would report True while the row survives.
    assert engine.opened == ["begin"]


def test_delete_owned_property_returns_false_when_nothing_matched(monkeypatch):
    """Another account's property is reported as missing, never as forbidden."""
    _patch_engine(monkeypatch, FakeConnection(results=[FakeResult(rowcount=0)]))

    assert OnboardingRepository().delete_owned_property(ACCOUNT_ID, OWNED_PROPERTY_ID) is False


def test_get_owned_property_returns_coordinates_and_booking_url(monkeypatch):
    """Round 6: the radius centre («Εσείς» marker) and the popup link come
    from here; the read stays a bare connection (no transaction)."""
    row = {
        "id": OWNED_PROPERTY_ID,
        "display_name": "Rea Hotel",
        "canonical_destination": "faliraki",
        "selected_room_type_category": "double",
        "booking_url": "https://www.booking.com/hotel/gr/rea.html",
        "latitude": 36.34,
        "longitude": 28.2,
    }
    connection = FakeConnection(results=[FakeResult(rows=[row])])
    engine = _patch_engine(monkeypatch, connection)

    result = OnboardingRepository().get_owned_property(ACCOUNT_ID, OWNED_PROPERTY_ID)

    assert result == row
    assert engine.opened == ["connect"]
    sql = _statement(connection, "FROM roomrate_owned_properties")
    for column in ("booking_url", "latitude", "longitude", "selected_room_type_category"):
        assert column in sql.split("FROM roomrate_owned_properties")[0]
    assert "is_active = true" in sql
