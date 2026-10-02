"""Short-lived one-time tickets for WebSocket authentication.

Browser clients must not put their Supabase JWT in the WebSocket URL: query
strings leak into server/proxy access logs and browser history, and a leaked
access token stays valid until it expires. Instead the client mints a ticket
over authenticated REST (``POST /api/v1/notifications/ws-ticket``) and opens
``/ws/alerts?ticket=...`` with it.

Security properties:

* Only the SHA-256 hex of the ticket is stored, so a database read can never
  recover a redeemable ticket.
* Redemption is one atomic UPDATE fenced on ``used_at IS NULL AND expires_at >
  now()`` — a ticket works exactly once, and replaying a leaked URL is useless.
* Tickets expire after ``WS_TICKET_TTL_SECONDS`` (they only need to survive
  the milliseconds between mint and handshake).
* State lives in PostgreSQL, so the handshake may land on a different API
  process than the one that minted the ticket (load balancing).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

from sqlalchemy import text

from api.db import get_engine

# Tickets bridge one REST round trip and one WS handshake; 60s absorbs slow
# networks without leaving a meaningful replay window.
WS_TICKET_TTL_SECONDS = 60

# issue() opportunistically deletes rows expired for at least this long, so the
# table self-cleans without a scheduler job while recent rows stay inspectable.
_CLEANUP_GRACE_SECONDS = 3600


class WsTicketService:
    """Mint and redeem one-time WebSocket auth tickets (PostgreSQL-backed)."""

    def issue(self, account_id: uuid.UUID) -> str:
        """Return a new opaque ticket for ``account_id`` (valid once, 60s)."""
        ticket = secrets.token_urlsafe(32)
        with get_engine(role="api").begin() as connection:
            # Piggyback cleanup on the mint write: long-expired rows are dead
            # weight, and this keeps the table bounded without a cron job.
            connection.execute(
                text(
                    """
                    DELETE FROM roomrate_ws_tickets
                    WHERE expires_at < now() - make_interval(secs => :grace_seconds)
                    """
                ),
                {"grace_seconds": _CLEANUP_GRACE_SECONDS},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO roomrate_ws_tickets (id, account_id, ticket_hash, expires_at)
                    VALUES (
                        :id, :account_id, :ticket_hash,
                        now() + make_interval(secs => :ttl_seconds)
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "account_id": account_id,
                    "ticket_hash": _hash_ticket(ticket),
                    "ttl_seconds": WS_TICKET_TTL_SECONDS,
                },
            )
        return ticket

    def redeem(self, ticket: str) -> uuid.UUID | None:
        """Consume a ticket atomically; returns its account or None.

        None covers every rejection: unknown, expired, and already-used
        tickets are indistinguishable to the caller (and to an attacker).
        """
        if not ticket or not ticket.strip():
            return None
        with get_engine(role="api").begin() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        UPDATE roomrate_ws_tickets
                        SET used_at = now()
                        WHERE ticket_hash = :ticket_hash
                          AND used_at IS NULL
                          AND expires_at > now()
                        RETURNING account_id
                        """
                    ),
                    {"ticket_hash": _hash_ticket(ticket)},
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        account_id = row["account_id"]
        return account_id if isinstance(account_id, uuid.UUID) else uuid.UUID(str(account_id))


def _hash_ticket(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()
