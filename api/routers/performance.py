"""«Απόδοση»: how the owner's property did, from their own bookings (Excel uploads).

Contract: docs/performance/API.md and frontend/src/app/types/performance.ts.
Every call takes ``owned_property_id``, one of the caller's own properties;
any other is 404 «Owned property not found». The page answers from
frontend/public/mock/performance/ for each call not built here yet.

The routes live in api/routers/performance_routes/, by area.
"""

from fastapi import APIRouter

from api.routers.performance_routes import figures, setup, status, uploads

router = APIRouter()
router.include_router(status.router)
router.include_router(uploads.router)
router.include_router(setup.router)
router.include_router(figures.router)
