"""Environment loading and external client factories."""

from __future__ import annotations

import os
from typing import Any

from dotenv import find_dotenv, load_dotenv
from sqlalchemy import create_engine

from pathlib import Path

from .logging_config import log


# Το .env παραμένει στη ρίζα της εφαρμογής, όχι μέσα στο package.
_script_dir_env = Path(__file__).resolve().parents[1] / ".env"
if _script_dir_env.exists():
    _dotenv_path = str(_script_dir_env)
else:
    _dotenv_path = find_dotenv(usecwd=True) or ""


def _load_scraper_dotenv(dotenv_path: str) -> bool:
    """Load scraper .env files that may include a UTF-8 BOM from Windows editors."""
    if not dotenv_path:
        return False
    return load_dotenv(dotenv_path, override=True, encoding="utf-8-sig")


_load_scraper_dotenv(_dotenv_path)


def log_dotenv_resolution() -> None:
    """Report where (or whether) the .env was found.

    Called from the CLI AFTER ``configure_logging()``. Emitting this at import
    time — as the module split originally did — wrote it to a root logger that
    still had no handlers, so the breadcrumb never reached stdout or
    scraper.log. That mattered precisely when it was needed: a job failing
    with "Δεν βρέθηκε APIFY_TOKEN" had no record of which .env was consulted.
    """
    if _dotenv_path:
        log.info(".env φορτώθηκε από: %s", _dotenv_path)
    else:
        log.warning(
            ".env δεν βρέθηκε. Ψάχνω σε: %s\n"
            "Βεβαιώσου ότι το .env υπάρχει στον ίδιο φάκελο με το script "
            "ή σε κάποιον γονικό φάκελο.",
            os.getcwd(),
        )


def build_client() -> Any:
    token = os.getenv("APIFY_TOKEN")
    if not token:
        raise EnvironmentError(
            "Δεν βρέθηκε APIFY_TOKEN.\n"
            f"  .env path  : {_dotenv_path or 'ΔΕΝ ΒΡΕΘΗΚΕ'}\n"
            f"  cwd        : {os.getcwd()}\n"
            "Βεβαιώσου ότι το .env περιέχει: APIFY_TOKEN=apify_xxxx..."
        )
    try:
        from apify_client import ApifyClient
    except ImportError as exc:
        raise RuntimeError("Apify client is required for live scraping. Reinstall pinned requirements.") from exc
    return ApifyClient(token)


def build_engine():
    """Lazy — καλείται μόνο κατά την αποθήκευση."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise EnvironmentError(
            "Δεν βρέθηκε DATABASE_URL.\n"
            f"  .env path  : {_dotenv_path or 'ΔΕΝ ΒΡΕΘΗΚΕ'}\n"
            f"  cwd        : {os.getcwd()}\n"
            "Παράδειγμα: DATABASE_URL=postgresql://user:pass@localhost:5432/roomrate"
        )
    return create_engine(db_url, pool_pre_ping=True)
