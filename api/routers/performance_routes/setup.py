"""The property's set-up: GET/PUT /inventory, /closures, /revenue-settings."""

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_performance_repository
from api.repositories.performance import PerformanceRepository
from api.routers.performance_routes.common import FOREIGN_ROOM_TYPE, OwnedProperty, owned_property
from api.schemas.performance import (
    PropertyClosuresIn,
    PropertyClosuresOut,
    RevenueSettingsIn,
    RevenueSettingsOut,
    RoomInventoryIn,
    RoomInventoryOut,
)

router = APIRouter()


def _own_room_types(repository: PerformanceRepository, owned: OwnedProperty, ids: Iterable[UUID | None]) -> None:
    owned_ids = {room["id"] for room in repository.room_types(owned.account_id, owned.id)}
    if any(room_type_id is not None and room_type_id not in owned_ids for room_type_id in ids):
        raise HTTPException(status_code=400, detail=FOREIGN_ROOM_TYPE)


@router.get("/inventory", response_model=RoomInventoryOut)
def room_inventory(
    owned: OwnedProperty = Depends(owned_property),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> dict[str, Any]:
    """Rooms per room type; ``rooms`` null where the owner has not entered them."""
    return {"room_types": repository.inventory(owned.account_id, owned.id)}


@router.put("/inventory", response_model=RoomInventoryOut)
def save_room_inventory(
    body: RoomInventoryIn,
    owned: OwnedProperty = Depends(owned_property),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> dict[str, Any]:
    """Replace the rooms per room type (one entry per room type, or per period when the count changed)."""
    _own_room_types(repository, owned, [row.room_type_id for row in body.room_types])
    repository.save_inventory(owned.account_id, owned.id, [row.model_dump() for row in body.room_types])
    return {"room_types": repository.inventory(owned.account_id, owned.id)}


@router.get("/closures", response_model=PropertyClosuresOut)
def property_closures(
    owned: OwnedProperty = Depends(owned_property),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> dict[str, Any]:
    return {"closures": repository.closures(owned.account_id, owned.id)}


@router.put("/closures", response_model=PropertyClosuresOut)
def save_property_closures(
    body: PropertyClosuresIn,
    owned: OwnedProperty = Depends(owned_property),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> dict[str, Any]:
    """Replace the closed periods: never counted as unsold."""
    _own_room_types(repository, owned, [row.room_type_id for row in body.closures])
    repository.save_closures(owned.account_id, owned.id, [row.model_dump() for row in body.closures])
    return {"closures": repository.closures(owned.account_id, owned.id)}


@router.get("/revenue-settings", response_model=RevenueSettingsOut)
def revenue_settings(
    owned: OwnedProperty = Depends(owned_property),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> dict[str, Any]:
    """VAT and resilience fee behind the net amounts (13% and no fee until saved)."""
    return repository.revenue_settings(owned.account_id, owned.id)


@router.put("/revenue-settings", response_model=RevenueSettingsOut)
def save_revenue_settings(
    body: RevenueSettingsIn,
    owned: OwnedProperty = Depends(owned_property),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> dict[str, Any]:
    repository.save_revenue_settings(owned.account_id, owned.id, **body.model_dump())
    return repository.revenue_settings(owned.account_id, owned.id)
