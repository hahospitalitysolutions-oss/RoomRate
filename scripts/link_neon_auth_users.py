"""Link existing RoomRate accounts to their Neon Auth users, by email.

Login moved from Supabase to Neon Auth. Neon Auth keeps its users in the same
database (schema ``neon_auth``), but their ids are new, so a user's first
Neon login would otherwise create a fresh, empty RoomRate account. For every
active Supabase identity whose email matches a Neon Auth user, this script:

* ``relink``: no Neon identity yet (the user has not logged in since the
  switch) -> the Supabase identity becomes the Neon one; the next login finds
  the existing account.
* ``move``: the user already logged in and got an auto-created account with no
  property yet -> that login's membership moves to the existing account; the
  Supabase identity is deactivated. The empty account is left as is.
* ``skip``: the auto-created account already has a property (the user started
  over there), or the email matches several Neon users -> nothing changes;
  sort it out by hand.

Passwords cannot be carried over: create each user first (Neon -> Auth ->
Users -> Create user, same email) or let them sign up again.

Prints emails and actions only. A dry run by default; ``--apply`` writes, in
one transaction.

Usage:
    python scripts/link_neon_auth_users.py
    python scripts/link_neon_auth_users.py --apply
    python scripts/link_neon_auth_users.py --email owner@example.com --apply
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Before importing api.*: api.config builds its settings at import time.
# utf-8-sig because the owner's .env is saved by a Windows editor with a BOM.
load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")

from api.db import get_engine  # noqa: E402
from api.services.auth_service import NEON_AUTH_PROVIDER  # noqa: E402

SUPABASE_PROVIDER = "supabase"


@dataclass(frozen=True)
class LinkAction:
    kind: str  # "relink" | "move" | "skip"
    email: str
    reason: str
    supabase_identity_id: Any = None
    neon_user_id: str | None = None
    neon_identity_id: Any = None
    target_account_id: Any = None


def plan_links(
    supabase_identities: list[dict[str, Any]],
    neon_users: list[dict[str, Any]],
    neon_identities: list[dict[str, Any]],
) -> list[LinkAction]:
    """Decide, per Supabase identity with an email, what linking it takes.

    ``supabase_identities``: id, email, account_id (its account).
    ``neon_users``: id, email (neon_auth."user").
    ``neon_identities``: id, auth_subject, account_id, account_has_property.
    """
    users_by_email: dict[str, list[dict[str, Any]]] = {}
    for user in neon_users:
        if user.get("email"):
            users_by_email.setdefault(user["email"].strip().lower(), []).append(user)
    identity_by_subject = {identity["auth_subject"]: identity for identity in neon_identities}

    actions: list[LinkAction] = []
    for identity in supabase_identities:
        email = (identity.get("email") or "").strip().lower()
        if not email:
            continue
        matches = users_by_email.get(email, [])
        if not matches:
            actions.append(LinkAction("skip", email, "no Neon Auth user with this email yet"))
            continue
        if len(matches) > 1:
            actions.append(LinkAction("skip", email, "several Neon Auth users share this email"))
            continue
        neon_user_id = str(matches[0]["id"])
        neon_identity = identity_by_subject.get(neon_user_id)
        if neon_identity is None:
            actions.append(LinkAction(
                "relink", email, "the next Neon login opens the existing account",
                supabase_identity_id=identity["id"], neon_user_id=neon_user_id,
                target_account_id=identity["account_id"],
            ))
        elif neon_identity["account_id"] == identity["account_id"]:
            continue  # already linked
        elif neon_identity["account_has_property"]:
            actions.append(LinkAction(
                "skip", email,
                "already logged in with Neon and set up a property on a new account; link by hand",
            ))
        else:
            actions.append(LinkAction(
                "move", email, "the empty account from the first Neon login is replaced by the existing one",
                supabase_identity_id=identity["id"], neon_user_id=neon_user_id,
                neon_identity_id=neon_identity["id"], target_account_id=identity["account_id"],
            ))
    return actions


def _load(connection, email_filter: str | None) -> tuple[list[dict], list[dict], list[dict]]:
    supabase_identities = connection.execute(
        text(
            """
            SELECT DISTINCT ON (i.id) i.id, i.email, m.account_id
            FROM roomrate_user_identities i
            JOIN roomrate_memberships m ON m.user_id = i.id
            WHERE i.auth_provider = :provider AND i.is_active = true AND i.email IS NOT NULL
            ORDER BY i.id, m.created_at ASC
            """
        ),
        {"provider": SUPABASE_PROVIDER},
    ).mappings().all()
    neon_users = connection.execute(text('SELECT id, email FROM neon_auth."user"')).mappings().all()
    neon_identities = connection.execute(
        text(
            """
            SELECT DISTINCT ON (i.id) i.id, i.auth_subject, m.account_id,
                   EXISTS (
                       SELECT 1 FROM roomrate_owned_properties p WHERE p.account_id = m.account_id
                   ) AS account_has_property
            FROM roomrate_user_identities i
            JOIN roomrate_memberships m ON m.user_id = i.id
            WHERE i.auth_provider = :provider
            ORDER BY i.id, m.created_at ASC
            """
        ),
        {"provider": NEON_AUTH_PROVIDER},
    ).mappings().all()
    rows = [dict(row) for row in supabase_identities]
    if email_filter:
        rows = [row for row in rows if (row["email"] or "").strip().lower() == email_filter.strip().lower()]
    return rows, [dict(row) for row in neon_users], [dict(row) for row in neon_identities]


def _apply(connection, action: LinkAction) -> None:
    if action.kind == "relink":
        connection.execute(
            text(
                """
                UPDATE roomrate_user_identities
                SET auth_provider = :provider, auth_subject = :subject, updated_at = now()
                WHERE id = :id
                """
            ),
            {"provider": NEON_AUTH_PROVIDER, "subject": action.neon_user_id, "id": action.supabase_identity_id},
        )
    elif action.kind == "move":
        connection.execute(
            text("DELETE FROM roomrate_memberships WHERE user_id = :user_id"),
            {"user_id": action.neon_identity_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO roomrate_memberships (id, account_id, user_id, role)
                VALUES (gen_random_uuid(), :account_id, :user_id, 'owner')
                """
            ),
            {"account_id": action.target_account_id, "user_id": action.neon_identity_id},
        )
        connection.execute(
            text("UPDATE roomrate_user_identities SET is_active = false, updated_at = now() WHERE id = :id"),
            {"id": action.supabase_identity_id},
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    parser.add_argument("--email", help="only this user")
    args = parser.parse_args()

    with get_engine().begin() as connection:
        schema = connection.execute(
            text("SELECT to_regclass('neon_auth.\"user\"') IS NOT NULL")
        ).scalar()
        if not schema:
            print('neon_auth."user" not found: enable Neon Auth on this database first.')
            return 1
        actions = plan_links(*_load(connection, args.email))
        for action in actions:
            print(f"{action.kind:6} {action.email}: {action.reason}")
        if not actions:
            print("Nothing to link.")
        if args.apply:
            for action in actions:
                _apply(connection, action)
            print(f"Applied {sum(action.kind != 'skip' for action in actions)} change(s).")
        else:
            print("Dry run: nothing written. Re-run with --apply to link.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
