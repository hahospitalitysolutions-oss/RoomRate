"""The plan of scripts/link_neon_auth_users.py: which identity gets which link."""

import importlib.util
import sys
from pathlib import Path
from uuid import UUID

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "link_neon_auth_users.py"
_spec = importlib.util.spec_from_file_location("link_neon_auth_users", SCRIPT)
link = importlib.util.module_from_spec(_spec)
# Registered first: @dataclass resolves annotations through sys.modules.
sys.modules[_spec.name] = link
_spec.loader.exec_module(link)

OLD_ACCOUNT = UUID("00000000-0000-0000-0000-00000000000a")
NEW_ACCOUNT = UUID("00000000-0000-0000-0000-00000000000b")
SUPABASE_ID = UUID("00000000-0000-0000-0000-0000000000a1")
NEON_IDENTITY_ID = UUID("00000000-0000-0000-0000-0000000000b1")


def _supabase(email="Owner@Example.com"):
    return [{"id": SUPABASE_ID, "email": email, "account_id": OLD_ACCOUNT}]


def test_a_user_who_has_not_logged_in_yet_is_relinked_in_place():
    actions = link.plan_links(_supabase(), [{"id": "neon-1", "email": "owner@example.com"}], [])

    assert [(a.kind, a.email, a.neon_user_id, a.target_account_id) for a in actions] == [
        ("relink", "owner@example.com", "neon-1", OLD_ACCOUNT),
    ]


def test_an_empty_account_from_a_first_neon_login_is_replaced_by_the_existing_one():
    neon_identities = [{"id": NEON_IDENTITY_ID, "auth_subject": "neon-1", "account_id": NEW_ACCOUNT,
                        "account_has_property": False}]

    actions = link.plan_links(_supabase(), [{"id": "neon-1", "email": "owner@example.com"}], neon_identities)

    assert [(a.kind, a.neon_identity_id, a.target_account_id) for a in actions] == [
        ("move", NEON_IDENTITY_ID, OLD_ACCOUNT),
    ]


def test_a_new_account_that_already_has_a_property_is_left_for_a_person():
    neon_identities = [{"id": NEON_IDENTITY_ID, "auth_subject": "neon-1", "account_id": NEW_ACCOUNT,
                        "account_has_property": True}]

    actions = link.plan_links(_supabase(), [{"id": "neon-1", "email": "owner@example.com"}], neon_identities)

    assert [a.kind for a in actions] == ["skip"]


def test_no_match_and_ambiguous_matches_change_nothing():
    unmatched = link.plan_links(_supabase(), [{"id": "neon-9", "email": "other@example.com"}], [])
    ambiguous = link.plan_links(
        _supabase(),
        [{"id": "neon-1", "email": "owner@example.com"}, {"id": "neon-2", "email": "OWNER@example.com"}],
        [],
    )

    assert [a.kind for a in unmatched] == ["skip"]
    assert [a.kind for a in ambiguous] == ["skip"]


def test_an_already_linked_user_needs_nothing():
    neon_identities = [{"id": NEON_IDENTITY_ID, "auth_subject": "neon-1", "account_id": OLD_ACCOUNT,
                        "account_has_property": True}]

    assert link.plan_links(_supabase(), [{"id": "neon-1", "email": "owner@example.com"}], neon_identities) == []
