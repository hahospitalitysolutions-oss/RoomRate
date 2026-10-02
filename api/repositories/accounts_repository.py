from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from api.db import get_engine


def _account_slug(auth_subject: str, email: str | None) -> str:
    base = (email or auth_subject).split("@")[0]
    base = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()
    if not base:
        base = "account"
    return f"{base}-{auth_subject[:8].lower()}"


class AccountsRepository:
    """Persistence for auth identities, memberships, and tenant accounts."""

    def __init__(self, engine_factory: Callable[[], Engine] | None = None):
        # Optional engine seam (same design as the injected connection factory
        # in RoomRatesRepository/PriceHistoryRepository). None falls back to
        # the module-level get_engine inside _engine().
        self._engine_factory = engine_factory

    def _engine(self) -> Engine:
        """Injected factory when present, else the module-level get_engine.

        get_engine is deliberately resolved as a module global at call time
        (not bound at import/def time) so tests that monkeypatch it keep
        working.
        """
        return self._engine_factory() if self._engine_factory else get_engine()

    def get_or_create_account_for_identity(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        email: str | None,
        display_name: str | None,
    ) -> uuid.UUID:
        """Resolve a verified external identity into a RoomRate account id.

        Runs on EVERY authenticated request, so the common case (identity and
        membership already exist and the token fields match what is stored) is
        answered with one read-only SELECT — no advisory lock and no upsert
        churn. Only a miss or changed token fields take the write path below.
        """
        with self._engine().connect() as connection:
            existing = connection.execute(
                text(
                    """
                    SELECT i.email, i.display_name, m.account_id
                    FROM roomrate_user_identities i
                    JOIN roomrate_memberships m ON m.user_id = i.id
                    JOIN roomrate_accounts a ON a.id = m.account_id
                    WHERE i.auth_provider = :auth_provider
                      AND i.auth_subject = :auth_subject
                      AND i.is_active = true
                      AND a.status = 'active'
                    ORDER BY m.created_at ASC
                    LIMIT 1
                    """
                ),
                {"auth_provider": auth_provider, "auth_subject": auth_subject},
            ).mappings().first()
        if (
            existing is not None
            and existing["email"] == email
            # The upsert only overwrites display_name when the token carries
            # one (COALESCE), so a None token display_name never forces a write.
            and (display_name is None or existing["display_name"] == display_name)
        ):
            return existing["account_id"]

        with self._engine().begin() as connection:
            # Κλειδώνει το πρώτο login του ίδιου auth user ώστε να μη δημιουργηθούν διπλά accounts.
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": f"{auth_provider}:{auth_subject}"},
            )
            identity = connection.execute(
                text(
                    """
                    INSERT INTO roomrate_user_identities (
                        id,
                        auth_provider,
                        auth_subject,
                        email,
                        display_name,
                        is_active
                    )
                    VALUES (
                        :id,
                        :auth_provider,
                        :auth_subject,
                        :email,
                        :display_name,
                        true
                    )
                    ON CONFLICT (auth_provider, auth_subject)
                    DO UPDATE SET
                        email = EXCLUDED.email,
                        display_name = COALESCE(EXCLUDED.display_name, roomrate_user_identities.display_name),
                        is_active = true,
                        updated_at = now()
                    RETURNING id
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "auth_provider": auth_provider,
                    "auth_subject": auth_subject,
                    "email": email,
                    "display_name": display_name,
                },
            ).mappings().one()

            membership = connection.execute(
                text(
                    """
                    SELECT m.account_id
                    FROM roomrate_memberships m
                    JOIN roomrate_accounts a ON a.id = m.account_id
                    WHERE m.user_id = :user_id
                      AND a.status = 'active'
                    ORDER BY m.created_at ASC
                    LIMIT 1
                    """
                ),
                {"user_id": identity["id"]},
            ).mappings().first()
            if membership:
                return membership["account_id"]

            account_id = uuid.uuid4()
            account_name = display_name or email or "RoomRate Account"
            connection.execute(
                text(
                    """
                    INSERT INTO roomrate_accounts (id, slug, display_name, status)
                    VALUES (:id, :slug, :display_name, 'active')
                    """
                ),
                {
                    "id": account_id,
                    "slug": _account_slug(auth_subject, email),
                    "display_name": account_name,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO roomrate_memberships (id, account_id, user_id, role)
                    VALUES (:id, :account_id, :user_id, 'owner')
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "account_id": account_id,
                    "user_id": identity["id"],
                },
            )
            return account_id

    def get_account_overview(self, account_id: uuid.UUID) -> dict[str, Any]:
        """Return the account's current onboarding status for the frontend.

        ``selected_room_type_category`` is the RAW column: null until the
        user picks a room in step 2 of the wizard through PUT
        /owned-property/{id}/selected-room-type. This used to fall back to
        the newest discovered room type when the column was null, which made
        /me report a selection nobody made -- and setupGuard, which reads
        exactly these two fields, then skipped the user past steps 2-3.
        Discovering rooms is not choosing one.
        """
        with self._engine().connect() as connection:
            owned_property = connection.execute(
                text(
                    """
                    SELECT
                        id,
                        display_name,
                        city,
                        raw_destination,
                        canonical_destination,
                        selected_room_type_category
                    FROM roomrate_owned_properties
                    WHERE account_id = :account_id
                      AND is_active = true
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT 1
                    """
                ),
                {"account_id": account_id},
            ).mappings().first()

        return {
            "owned_property_id": owned_property["id"] if owned_property else None,
            "property_name": owned_property["display_name"] if owned_property else None,
            "destination": owned_property["city"] if owned_property else None,
            "raw_destination": owned_property["raw_destination"] if owned_property else None,
            "canonical_destination": owned_property["canonical_destination"] if owned_property else None,
            "selected_room_type_category": (
                owned_property["selected_room_type_category"] if owned_property else None
            ),
            "onboarding_complete": bool(
                owned_property and owned_property["selected_room_type_category"]
            ),
        }
