"""Uploads: POST /imports → PUT /imports/{id}/mapping → POST /imports/{id}/commit; GET /imports; DELETE /bookings."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from api.dependencies import get_performance_import_service, get_performance_repository
from api.repositories.performance import PerformanceRepository
from api.routers.performance_routes.common import OwnedProperty, answer, owned_property
from api.schemas.performance import (
    ImportCommitIn,
    ImportCommitOut,
    ImportFormat,
    ImportMappingUpdateIn,
    ImportPreviewOut,
    ImportSummaryOut,
)
from api.services.performance.importing.service import MAX_UPLOAD_BYTES, PerformanceImportService

router = APIRouter()


@router.post("/imports", response_model=ImportPreviewOut)
async def upload_bookings(
    file: UploadFile = File(...),
    format: ImportFormat = Form(...),
    owned: OwnedProperty = Depends(owned_property),
    service: PerformanceImportService = Depends(get_performance_import_service),
) -> dict[str, Any]:
    """Step 1: the file is read and checked, kept for 24 hours; no booking is stored yet."""
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Το αρχείο είναι πάνω από 5 MB.")
    filename = file.filename or "kratiseis"
    # Reading 20,000 rows takes a moment: off the event loop.
    return await run_in_threadpool(
        answer, lambda: service.preview_upload(owned.account_id, owned.id, content, filename, format)
    )


@router.put("/imports/{import_id}/mapping", response_model=ImportPreviewOut)
def update_import_mapping(
    import_id: UUID,
    body: ImportMappingUpdateIn,
    owned: OwnedProperty = Depends(owned_property),
    service: PerformanceImportService = Depends(get_performance_import_service),
) -> dict[str, Any]:
    """Map (or ignore) the file's room labels; remembered for the next uploads."""
    items = [item.model_dump() for item in body.mapping]
    return answer(lambda: service.update_mapping(owned.account_id, owned.id, import_id, items))


@router.post("/imports/{import_id}/commit", response_model=ImportCommitOut)
def commit_import(
    import_id: UUID,
    body: ImportCommitIn,
    owned: OwnedProperty = Depends(owned_property),
    service: PerformanceImportService = Depends(get_performance_import_service),
) -> dict[str, Any]:
    """Step 2: the checked bookings are stored; a booking number stored before is replaced, never doubled."""
    return answer(lambda: service.commit(owned.account_id, owned.id, import_id, skip_errors=body.skip_errors))


@router.get("/imports", response_model=list[ImportSummaryOut])
def list_imports(
    owned: OwnedProperty = Depends(owned_property),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> list[dict[str, Any]]:
    """Committed uploads, newest first."""
    return repository.imports(owned.account_id, owned.id)


@router.delete("/bookings", status_code=204, response_class=Response)
def delete_all_bookings(
    owned: OwnedProperty = Depends(owned_property),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> Response:
    """Every booking of the property and its upload history; rooms, closures and settings stay."""
    repository.delete_bookings(owned.account_id, owned.id)
    return Response(status_code=204)
