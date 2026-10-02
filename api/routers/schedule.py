from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import AccountContext, get_account_context, get_schedule_service
from api.schemas.schedule import ScheduleConfigResponse, ScheduleConfigUpdate
from api.services.schedule_service import ScheduleService

router = APIRouter()


@router.get("", response_model=ScheduleConfigResponse)
async def get_schedule_config(
    account: AccountContext = Depends(get_account_context),
    service: ScheduleService = Depends(get_schedule_service),
) -> ScheduleConfigResponse:
    """Return the account's scrape schedule, or defaults when not configured."""
    return service.get_config(account.account_id)


@router.put("", response_model=ScheduleConfigResponse)
async def update_schedule_config(
    request: ScheduleConfigUpdate,
    account: AccountContext = Depends(get_account_context),
    service: ScheduleService = Depends(get_schedule_service),
) -> ScheduleConfigResponse:
    """Upsert the account's scrape schedule from a partial update.

    Enabling a disabled schedule resets the failure circuit breaker (see
    ScheduleService.update_config for the rationale).
    """
    return service.update_config(account.account_id, request)
