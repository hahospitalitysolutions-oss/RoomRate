from datetime import date
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from api.dependencies import AccountContext, get_account_context, get_scrape_job_service
from api.config import settings
from api.schemas.scrape_jobs import ScrapeJobCreate, ScrapeJobResponse
from api.services.scrape_job_service import ScrapeJobService

router = APIRouter()


@router.post("/", response_model=ScrapeJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_scrape_job(
    request: ScrapeJobCreate,
    background_tasks: BackgroundTasks,
    account: AccountContext = Depends(get_account_context),
    service: ScrapeJobService = Depends(get_scrape_job_service),
) -> ScrapeJobResponse:
    """Persist a Booking.com scrape job for the current account.

    Quota refusals (QuotaExceededError) propagate to the app-level handler in
    ``api.main`` and become 429 there.
    """
    # Νέα paid scrapes δεν έχουν επιχειρησιακή αξία για παρελθοντική διαμονή.
    if request.check_in < date.today():
        raise HTTPException(status_code=422, detail="check_in cannot be in the past")
    try:
        job = service.create_job(account_id=account.account_id, request=request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Το web process δεν εκτελεί scraping σε production. Στο local ``all``
    # mode το background kick κρατά γρήγορο το feedback, ενώ η μόνιμη ουρά
    # παραμένει το safety net σε περίπτωση restart.
    if settings.process_role == "all":
        background_tasks.add_task(service.run_job, account.account_id, job.id)
    return job


@router.get("/", response_model=list[ScrapeJobResponse])
async def list_scrape_jobs(
    account: AccountContext = Depends(get_account_context),
    service: ScrapeJobService = Depends(get_scrape_job_service),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ScrapeJobResponse]:
    """List recent scrape jobs for the current account."""
    return service.list_jobs(account_id=account.account_id, limit=limit)


@router.get("/{job_id}", response_model=ScrapeJobResponse)
async def get_scrape_job(
    job_id: UUID,
    account: AccountContext = Depends(get_account_context),
    service: ScrapeJobService = Depends(get_scrape_job_service),
) -> ScrapeJobResponse:
    """Return one scrape job for polling/status views."""
    job = service.get_job(account_id=account.account_id, job_id=job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Scrape job not found")
    return job
