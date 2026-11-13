"""What every «Απόδοση» route shares: the caller's own property, and the refusals as Greek HTTP answers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import UUID

from fastapi import Depends, HTTPException, Query

from api.dependencies import AccountContext, get_account_context, get_onboarding_repository
from api.repositories.onboarding_repository import OnboardingRepository
from api.services.performance.importing import ImportFileError
from api.services.performance.importing.service import ImportAlreadyCommitted, ImportNotFound, ImportRejected
from api.services.performance.kpis import KpiRejected

UPLOAD_GONE = "Η προεπισκόπηση δεν βρέθηκε ή έληξε (24 ώρες). Ανεβάστε ξανά το αρχείο."
ALREADY_COMMITTED = "Οι κρατήσεις αυτού του αρχείου έχουν ήδη αποθηκευτεί."
FOREIGN_ROOM_TYPE = "Ο τύπος δωματίου δεν ανήκει στο κατάλυμα."

T = TypeVar("T")


@dataclass(frozen=True)
class OwnedProperty:
    account_id: UUID
    id: UUID
    # The owned property row (display_name, canonical_destination, selected_room_type_category, …).
    row: dict[str, Any]


def owned_property(
    owned_property_id: UUID = Query(..., description="One of the caller's own properties"),
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
) -> OwnedProperty:
    """The caller's own property; any other is 404 «Owned property not found»."""
    row = onboarding_repository.get_owned_property(account.account_id, owned_property_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Owned property not found")
    return OwnedProperty(account.account_id, owned_property_id, row)


def answer(call: Callable[[], T]) -> T:
    """The services' refusals as HTTP answers: 400 (file, mapping, filters), 404 (upload gone), 409 (stored)."""
    try:
        return call()
    except (ImportFileError, ImportRejected, KpiRejected) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ImportNotFound as exc:
        raise HTTPException(status_code=404, detail=UPLOAD_GONE) from exc
    except ImportAlreadyCommitted as exc:
        raise HTTPException(status_code=409, detail=ALREADY_COMMITTED) from exc
