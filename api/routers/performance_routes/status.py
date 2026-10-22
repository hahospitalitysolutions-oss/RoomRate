"""GET /status and GET /template.xlsx."""

from typing import Any

from fastapi import APIRouter, Depends, Response

from api.dependencies import get_performance_import_service, get_performance_repository
from api.repositories.performance import PerformanceRepository
from api.routers.performance_routes.common import OwnedProperty, owned_property
from api.schemas.performance import PerformanceStatusOut
from api.services.performance.importing.service import PerformanceImportService
from api.services.performance.template import TEMPLATE_FILENAME, XLSX_MEDIA_TYPE, build_template

router = APIRouter()


@router.get("/status", response_model=PerformanceStatusOut)
def performance_status(
    owned: OwnedProperty = Depends(owned_property),
    service: PerformanceImportService = Depends(get_performance_import_service),
) -> dict[str, Any]:
    """What the page has to work with: bookings, seasons, room types and their rooms, the last upload."""
    return service.status(owned.account_id, owned.id)


@router.get(
    "/template.xlsx",
    response_class=Response,
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}, "description": "The RoomRate bookings template"}},
)
def bookings_template(
    owned: OwnedProperty = Depends(owned_property),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> Response:
    """The Excel template, with the property's room types (and their rooms, when entered) filled in."""
    content = build_template(
        repository.room_types(owned.account_id, owned.id), property_name=owned.row.get("display_name")
    )
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{TEMPLATE_FILENAME}"'},
    )
