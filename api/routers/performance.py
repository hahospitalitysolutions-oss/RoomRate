"""«Απόδοση»: how the owner's property did, from their own bookings (Excel uploads).

Contract: docs/performance/API.md and frontend/src/app/types/performance.ts.
Every call takes ``owned_property_id``, one of the caller's own properties;
any other is 404 «Owned property not found». The page answers from
frontend/public/mock/performance/ for each call not built here yet.
"""

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from api.dependencies import (
    AccountContext,
    get_account_context,
    get_onboarding_repository,
    get_performance_import_service,
    get_performance_kpi_service,
    get_performance_repository,
)
from api.repositories.onboarding_repository import OnboardingRepository
from api.repositories.performance_repository import PerformanceRepository
from api.schemas.performance import (
    AmountBasis,
    Compare,
    ImportCommitIn,
    ImportCommitOut,
    ImportFormat,
    ImportMappingUpdateIn,
    ImportPreviewOut,
    ImportSummaryOut,
    PerformanceChannelsOut,
    PerformanceCountriesOut,
    PerformanceMonthlyOut,
    PerformanceStatusOut,
    PerformanceSummaryOut,
    PropertyClosuresIn,
    PropertyClosuresOut,
    RevenueSettingsIn,
    RevenueSettingsOut,
    RoomInventoryIn,
    RoomInventoryOut,
)
from api.services.performance_import import ImportFileError
from api.services.performance_import_service import (
    MAX_UPLOAD_BYTES,
    ImportAlreadyCommitted,
    ImportNotFound,
    ImportRejected,
    PerformanceImportService,
)
from api.services.performance_kpis import Filters, KpiRejected, PerformanceKpiService
from api.services.performance_template import TEMPLATE_FILENAME, XLSX_MEDIA_TYPE, build_template

router = APIRouter()

UPLOAD_GONE = "Η προεπισκόπηση δεν βρέθηκε ή έληξε (24 ώρες). Ανεβάστε ξανά το αρχείο."
ALREADY_COMMITTED = "Οι κρατήσεις αυτού του αρχείου έχουν ήδη αποθηκευτεί."
FOREIGN_ROOM_TYPE = "Ο τύπος δωματίου δεν ανήκει στο κατάλυμα."


def _owned_property(
    account: AccountContext,
    owned_property_id: UUID,
    onboarding_repository: OnboardingRepository,
) -> dict[str, Any]:
    owned_property = onboarding_repository.get_owned_property(account.account_id, owned_property_id)
    if owned_property is None:
        raise HTTPException(status_code=404, detail="Owned property not found")
    return owned_property


def _upload_errors(call):
    """The import service's refusals as HTTP answers."""
    try:
        return call()
    except ImportFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ImportRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ImportNotFound as exc:
        raise HTTPException(status_code=404, detail=UPLOAD_GONE) from exc
    except ImportAlreadyCommitted as exc:
        raise HTTPException(status_code=409, detail=ALREADY_COMMITTED) from exc


@router.get("/status", response_model=PerformanceStatusOut)
def performance_status(
    owned_property_id: UUID,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    service: PerformanceImportService = Depends(get_performance_import_service),
) -> dict[str, Any]:
    """What the page has to work with: bookings, seasons, room types and their rooms, the last upload."""
    _owned_property(account, owned_property_id, onboarding_repository)
    return service.status(account.account_id, owned_property_id)


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


# ---------------------------------------------------------------------------
# KPIs (filters: stay dates, room type, gross/net, last year)
# ---------------------------------------------------------------------------


def _filters(
    date_from: date = Query(..., description="First stay date (a night belongs to the date it starts)"),
    date_to: date = Query(..., description="Last stay date, inclusive"),
    room_type_id: UUID | None = Query(None, description="An owned room type; omitted = the whole property"),
    amounts: AmountBasis = Query("gross"),
    compare: Compare = Query("last_year"),
) -> Filters:
    return Filters(date_from, date_to, room_type_id, amounts, compare)


def _kpi_call(call):
    try:
        return call()
    except KpiRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/summary", response_model=PerformanceSummaryOut)
def performance_summary(
    owned_property_id: UUID,
    filters: Filters = Depends(_filters),
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    service: PerformanceKpiService = Depends(get_performance_kpi_service),
) -> dict[str, Any]:
    """Occupancy, ADR, RevPAR, revenue and booking habits of the period, against last year and the market."""
    owned_property = _owned_property(account, owned_property_id, onboarding_repository)
    return _kpi_call(lambda: service.summary(account.account_id, owned_property, filters))


@router.get("/monthly", response_model=PerformanceMonthlyOut)
def performance_monthly(
    owned_property_id: UUID,
    filters: Filters = Depends(_filters),
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    service: PerformanceKpiService = Depends(get_performance_kpi_service),
) -> dict[str, Any]:
    """The same figures month by month, with the unsold and closed nights."""
    owned_property = _owned_property(account, owned_property_id, onboarding_repository)
    return _kpi_call(lambda: service.monthly(account.account_id, owned_property, filters))


@router.get("/channels", response_model=PerformanceChannelsOut)
def performance_channels(
    owned_property_id: UUID,
    filters: Filters = Depends(_filters),
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    service: PerformanceKpiService = Depends(get_performance_kpi_service),
) -> dict[str, Any]:
    """Revenue, nights and commission per channel, and per channel per month."""
    owned_property = _owned_property(account, owned_property_id, onboarding_repository)
    return _kpi_call(lambda: service.channels(account.account_id, owned_property, filters))


@router.get("/countries", response_model=PerformanceCountriesOut)
def performance_countries(
    owned_property_id: UUID,
    filters: Filters = Depends(_filters),
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    service: PerformanceKpiService = Depends(get_performance_kpi_service),
) -> dict[str, Any]:
    """The ten booker countries with most bookings, then «Λοιπές»."""
    owned_property = _owned_property(account, owned_property_id, onboarding_repository)
    return _kpi_call(lambda: service.countries(account.account_id, owned_property, filters))


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------


@router.post("/imports", response_model=ImportPreviewOut)
async def upload_bookings(
    owned_property_id: UUID,
    file: UploadFile = File(...),
    format: ImportFormat = Form(...),
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    service: PerformanceImportService = Depends(get_performance_import_service),
) -> dict[str, Any]:
    """Step 1: the file is read and checked, kept for 24 hours; no booking is stored yet."""
    await run_in_threadpool(_owned_property, account, owned_property_id, onboarding_repository)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Το αρχείο είναι πάνω από 5 MB.")
    filename = file.filename or "kratiseis"
    return await run_in_threadpool(
        _upload_errors,
        lambda: service.preview_upload(account.account_id, owned_property_id, content, filename, format),
    )


@router.put("/imports/{import_id}/mapping", response_model=ImportPreviewOut)
def update_import_mapping(
    import_id: UUID,
    owned_property_id: UUID,
    body: ImportMappingUpdateIn,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    service: PerformanceImportService = Depends(get_performance_import_service),
) -> dict[str, Any]:
    """Map (or ignore) the file's room labels; remembered for the next uploads."""
    _owned_property(account, owned_property_id, onboarding_repository)
    items = [item.model_dump() for item in body.mapping]
    return _upload_errors(lambda: service.update_mapping(account.account_id, owned_property_id, import_id, items))


@router.post("/imports/{import_id}/commit", response_model=ImportCommitOut)
def commit_import(
    import_id: UUID,
    owned_property_id: UUID,
    body: ImportCommitIn,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    service: PerformanceImportService = Depends(get_performance_import_service),
) -> dict[str, Any]:
    """Step 2: the checked bookings are stored; a booking number stored before is replaced, never doubled."""
    _owned_property(account, owned_property_id, onboarding_repository)
    return _upload_errors(
        lambda: service.commit(account.account_id, owned_property_id, import_id, skip_errors=body.skip_errors)
    )


@router.get("/imports", response_model=list[ImportSummaryOut])
def list_imports(
    owned_property_id: UUID,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> list[dict[str, Any]]:
    """Committed uploads, newest first."""
    _owned_property(account, owned_property_id, onboarding_repository)
    return repository.imports(account.account_id, owned_property_id)


@router.delete("/bookings", status_code=204, response_class=Response)
def delete_all_bookings(
    owned_property_id: UUID,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> Response:
    """Every booking of the property and its upload history; rooms, closures and settings stay."""
    _owned_property(account, owned_property_id, onboarding_repository)
    repository.delete_bookings(account.account_id, owned_property_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Rooms, closed periods, net-amount settings
# ---------------------------------------------------------------------------


def _check_room_types(repository: PerformanceRepository, account: AccountContext, owned_property_id: UUID, ids) -> None:
    owned = {room["id"] for room in repository.room_types(account.account_id, owned_property_id)}
    if any(room_type_id is not None and room_type_id not in owned for room_type_id in ids):
        raise HTTPException(status_code=400, detail=FOREIGN_ROOM_TYPE)


@router.get("/inventory", response_model=RoomInventoryOut)
def room_inventory(
    owned_property_id: UUID,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> dict[str, Any]:
    """Rooms per room type; ``rooms`` null where the owner has not entered them."""
    _owned_property(account, owned_property_id, onboarding_repository)
    return {"room_types": repository.inventory(account.account_id, owned_property_id)}


@router.put("/inventory", response_model=RoomInventoryOut)
def save_room_inventory(
    owned_property_id: UUID,
    body: RoomInventoryIn,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> dict[str, Any]:
    """Replace the rooms per room type (one entry per room type, or per period when the count changed)."""
    _owned_property(account, owned_property_id, onboarding_repository)
    _check_room_types(repository, account, owned_property_id, [row.room_type_id for row in body.room_types])
    repository.save_inventory(account.account_id, owned_property_id, [row.model_dump() for row in body.room_types])
    return {"room_types": repository.inventory(account.account_id, owned_property_id)}


@router.get("/closures", response_model=PropertyClosuresOut)
def property_closures(
    owned_property_id: UUID,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> dict[str, Any]:
    _owned_property(account, owned_property_id, onboarding_repository)
    return {"closures": repository.closures(account.account_id, owned_property_id)}


@router.put("/closures", response_model=PropertyClosuresOut)
def save_property_closures(
    owned_property_id: UUID,
    body: PropertyClosuresIn,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> dict[str, Any]:
    """Replace the closed periods: never counted as unsold."""
    _owned_property(account, owned_property_id, onboarding_repository)
    _check_room_types(repository, account, owned_property_id, [row.room_type_id for row in body.closures])
    repository.save_closures(account.account_id, owned_property_id, [row.model_dump() for row in body.closures])
    return {"closures": repository.closures(account.account_id, owned_property_id)}


@router.get("/revenue-settings", response_model=RevenueSettingsOut)
def revenue_settings(
    owned_property_id: UUID,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> dict[str, Any]:
    """VAT and resilience fee behind the net amounts (13% and no fee until saved)."""
    _owned_property(account, owned_property_id, onboarding_repository)
    return repository.revenue_settings(account.account_id, owned_property_id)


@router.put("/revenue-settings", response_model=RevenueSettingsOut)
def save_revenue_settings(
    owned_property_id: UUID,
    body: RevenueSettingsIn,
    account: AccountContext = Depends(get_account_context),
    onboarding_repository: OnboardingRepository = Depends(get_onboarding_repository),
    repository: PerformanceRepository = Depends(get_performance_repository),
) -> dict[str, Any]:
    _owned_property(account, owned_property_id, onboarding_repository)
    repository.save_revenue_settings(account.account_id, owned_property_id, **body.model_dump())
    return repository.revenue_settings(account.account_id, owned_property_id)
