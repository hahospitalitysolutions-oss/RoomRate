"""«Απόδοση»: how the owner's property did, from their own bookings (Excel uploads).

Contract: docs/performance/API.md and frontend/src/app/types/performance.ts.
Every call takes ``owned_property_id``, one of the caller's own properties;
any other is 404 «Owned property not found». The page answers from
frontend/public/mock/performance/ for each call not built here yet.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from api.dependencies import (
    AccountContext,
    get_account_context,
    get_onboarding_repository,
    get_performance_repository,
)
from api.repositories.onboarding_repository import OnboardingRepository
from api.repositories.performance_repository import PerformanceRepository
from api.services.performance_template import TEMPLATE_FILENAME, XLSX_MEDIA_TYPE, build_template

router = APIRouter()


def _owned_property(
    account: AccountContext,
    owned_property_id: UUID,
    onboarding_repository: OnboardingRepository,
) -> dict[str, Any]:
    owned_property = onboarding_repository.get_owned_property(account.account_id, owned_property_id)
    if owned_property is None:
        raise HTTPException(status_code=404, detail="Owned property not found")
    return owned_property


@router.get(
    "/template.xlsx",
    response_class=Response,
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}, "description": "The RoomRate bookings template"}},
)
def bookings_template(
    owned_property_id: UUID,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> Response:
    """The Excel template, with the property's room types (and their rooms, when entered) filled in."""
    owned_property = _owned_property(account, owned_property_id, onboarding_repository)
    room_types = repository.room_types(account.account_id, owned_property_id)
    content = build_template(room_types, property_name=owned_property.get("display_name"))
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{TEMPLATE_FILENAME}"'},
    )
