"""Pre-production checklist runner (docs/DOCUMENTATION.md §14).

Automates the integration checks that require live infrastructure, with
graceful SKIPs when a piece is not configured:

  1. PostgreSQL connectivity + Alembic revision vs migration head
  2. Read-only repository raw-SQL smoke (queries must EXECUTE, not match)
  3. Normalized-schema integrity + critical index presence
  4. LISTEN/NOTIFY round-trip on the roomrate_notifications channel
  5. Supabase JWT config (JWKS fetch, or local HS256 mint+verify)
  6. GET /ready database/process readiness (when --api-base-url / API_BASE_URL given)
  7. Scheduler configuration sanity from settings

Usage:
    python scripts/pre_production_checklist.py [--api-base-url https://api.example.com]

Prints a PASS/FAIL/SKIP table and exits 1 when any check FAILs, 0 otherwise.
The module imports cleanly with no database reachable: every check touches
live infrastructure only at RUNTIME and converts errors into FAIL/SKIP rows.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

LISTEN_NOTIFY_TIMEOUT_SECONDS = 5.0
HTTP_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one checklist item."""

    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""


# ----------------------------------------------------------------------------
# Pure aggregation/formatting (unit-tested; no infrastructure involved)
# ----------------------------------------------------------------------------


def summarize_exit_code(results: Sequence[CheckResult]) -> int:
    """Exit 1 when any check failed; SKIPs are informational only."""
    return 1 if any(result.status == FAIL for result in results) else 0


def format_results_table(results: Sequence[CheckResult]) -> str:
    """Render results as an aligned CHECK | STATUS | DETAIL text table."""
    name_header, status_header, detail_header = "CHECK", "STATUS", "DETAIL"
    name_width = max([len(name_header), *(len(r.name) for r in results)])
    status_width = max([len(status_header), *(len(r.status) for r in results)])
    lines = [
        f"{name_header:<{name_width}}  {status_header:<{status_width}}  {detail_header}",
        f"{'-' * name_width}  {'-' * status_width}  {'-' * len(detail_header)}",
    ]
    for result in results:
        lines.append(f"{result.name:<{name_width}}  {result.status:<{status_width}}  {result.detail}")
    return "\n".join(lines)


MAX_DETAIL_LENGTH = 300


def _one_line(text: str) -> str:
    """Collapse whitespace/newlines and cap length so table rows stay readable."""
    flattened = " ".join(str(text).split())
    if len(flattened) > MAX_DETAIL_LENGTH:
        return flattened[: MAX_DETAIL_LENGTH - 3] + "..."
    return flattened


def _failure(name: str, exc: Exception) -> CheckResult:
    return CheckResult(name, FAIL, _one_line(f"{type(exc).__name__}: {exc}"))


# ----------------------------------------------------------------------------
# Checks (live infrastructure touched at runtime only)
# ----------------------------------------------------------------------------


def check_database_and_migrations() -> CheckResult:
    """DB connectivity plus alembic_version == migration head (ScriptDirectory)."""
    name = "database & migrations"
    from api.config import settings

    if not settings.database_url:
        return CheckResult(name, SKIP, "DATABASE_URL not configured")
    try:
        import sqlalchemy
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        alembic_cfg = Config(str(REPO_ROOT / "alembic.ini"))
        # Absolute script_location: alembic resolves relative paths against
        # the process cwd, which need not be the repo root.
        alembic_cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
        head = ScriptDirectory.from_config(alembic_cfg).get_current_head()

        engine = sqlalchemy.create_engine(settings.database_url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                current = connection.execute(
                    sqlalchemy.text("SELECT version_num FROM alembic_version")
                ).scalar()
        finally:
            engine.dispose()
    except Exception as exc:
        return _failure(name, exc)
    if current == head:
        return CheckResult(name, PASS, f"connected; schema at head {head}")
    return CheckResult(
        name, FAIL, f"alembic current={current!r} != head={head!r} (run: alembic upgrade head)"
    )


def check_repository_sql_smoke() -> CheckResult:
    """Key repository raw-SQL queries execute read-only without error.

    Dummy parameters on purpose: the assertion is that the SQL parses/plans
    against the live schema, not that it returns rows.
    """
    name = "repository SQL smoke"
    from api.config import settings

    if not settings.database_url:
        return CheckResult(name, SKIP, "DATABASE_URL not configured")
    from datetime import date

    from api.db import db_connection
    from api.repositories.alerts_repository import AlertsRepository
    from api.repositories.price_history_repository import PriceHistoryRepository
    from api.repositories.scrape_jobs_repository import ScrapeJobRepository

    queries = (
        (
            "fetch_price_series",
            lambda: PriceHistoryRepository(db_connection).fetch_price_series(
                account_id=uuid.uuid4(),
                canonical_destination="__pre_prod_checklist__",
                check_in=date(2026, 1, 1),
                check_out=date(2026, 1, 2),
                adults=2,
                children=0,
                rooms=1,
            ),
        ),
        ("list_queued_jobs", lambda: ScrapeJobRepository().list_queued_jobs(limit=1)),
        ("count_unread", lambda: AlertsRepository().count_unread(uuid.uuid4())),
    )
    for query_name, run_query in queries:
        try:
            run_query()
        except Exception as exc:
            return CheckResult(name, FAIL, _one_line(f"{query_name}: {type(exc).__name__}: {exc}"))
    return CheckResult(name, PASS, "fetch_price_series, list_queued_jobs, count_unread executed")


def check_normalized_schema_integrity() -> CheckResult:
    """Detect broken relations, duplicate natural keys and missing hot-path indexes."""
    name = "normalized schema integrity"
    from api.config import settings

    if not settings.database_url:
        return CheckResult(name, SKIP, "DATABASE_URL not configured")
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(settings.database_url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                issues = connection.execute(
                    text(
                        """
                        WITH integrity_issues AS (
                            SELECT 'duplicate properties' AS issue, count(*) AS issue_count
                            FROM (
                                SELECT provider, source_property_key
                                FROM roomrate_properties
                                GROUP BY provider, source_property_key
                                HAVING count(*) > 1
                            ) duplicates
                            UNION ALL
                            SELECT 'duplicate packages', count(*)
                            FROM (
                                SELECT rate_observation_id, source_record_id
                                FROM roomrate_room_packages
                                GROUP BY rate_observation_id, source_record_id
                                HAVING count(*) > 1
                            ) duplicates
                            UNION ALL
                            SELECT 'duplicate amenities', count(*)
                            FROM (
                                SELECT normalized_name
                                FROM roomrate_amenities
                                GROUP BY normalized_name
                                HAVING count(*) > 1
                            ) duplicates
                            UNION ALL
                            SELECT 'orphan observations', count(*)
                            FROM roomrate_rate_observations observation
                            LEFT JOIN roomrate_properties property ON property.id = observation.property_id
                            LEFT JOIN roomrate_scrape_runs run ON run.id = observation.scrape_run_id
                            WHERE property.id IS NULL OR run.id IS NULL
                            UNION ALL
                            SELECT 'orphan packages', count(*)
                            FROM roomrate_room_packages package
                            LEFT JOIN roomrate_rate_observations observation
                                ON observation.id = package.rate_observation_id
                            WHERE observation.id IS NULL
                            UNION ALL
                            SELECT 'invalid observation price ranges', count(*)
                            FROM roomrate_rate_observations
                            WHERE price_min_eur <= 0 OR price_max_eur < price_min_eur
                        )
                        SELECT issue, issue_count
                        FROM integrity_issues
                        WHERE issue_count > 0
                        """
                    )
                ).mappings().all()
                required_indexes = (
                    "ix_roomrate_scrape_jobs_account_requested",
                    "ix_roomrate_scrape_jobs_queued_requested",
                    "ix_roomrate_rate_observations_property_observed",
                    "ix_roomrate_room_packages_category_price",
                    "ix_roomrate_notifications_account_created",
                    "ix_roomrate_notifications_account_unread_created",
                )
                present_indexes = set(
                    connection.execute(
                        text(
                            """
                            SELECT indexname
                            FROM pg_indexes
                            WHERE schemaname = current_schema()
                              AND indexname = ANY(CAST(:names AS text[]))
                            """
                        ),
                        {"names": list(required_indexes)},
                    ).scalars()
                )
        finally:
            engine.dispose()
    except Exception as exc:
        return _failure(name, exc)

    if issues:
        detail = "; ".join(f"{row['issue']}={row['issue_count']}" for row in issues)
        return CheckResult(name, FAIL, detail)
    missing_indexes = sorted(set(required_indexes) - present_indexes)
    if missing_indexes:
        return CheckResult(name, FAIL, f"missing indexes: {', '.join(missing_indexes)}")
    return CheckResult(name, PASS, "natural keys, relations, price ranges and hot-path indexes verified")


def check_listen_notify() -> CheckResult:
    """pg_notify sent on one connection is received by a LISTENing second one."""
    name = "LISTEN/NOTIFY round-trip"
    from api.config import settings

    if not settings.database_url:
        return CheckResult(name, SKIP, "DATABASE_URL not configured")
    try:
        import select

        import psycopg2
        import psycopg2.extensions

        from api.services.notification_broadcaster import CHANNEL

        dsn = settings.database_url.replace("postgresql+psycopg2://", "postgresql://")
        marker = f"pre-prod-checklist-{uuid.uuid4().hex}"
        listen_conn = psycopg2.connect(dsn)
        notify_conn = psycopg2.connect(dsn)
        try:
            listen_conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            notify_conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            with listen_conn.cursor() as cursor:
                cursor.execute(f"LISTEN {CHANNEL};")
            with notify_conn.cursor() as cursor:
                cursor.execute("SELECT pg_notify(%s, %s)", (CHANNEL, marker))
            deadline = time.monotonic() + LISTEN_NOTIFY_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                remaining = max(deadline - time.monotonic(), 0.0)
                readable, _, _ = select.select([listen_conn], [], [], remaining)
                if not readable:
                    continue
                listen_conn.poll()
                while listen_conn.notifies:
                    notification = listen_conn.notifies.pop(0)
                    if notification.payload == marker:
                        return CheckResult(name, PASS, f"pg_notify round-trip on '{CHANNEL}'")
            return CheckResult(
                name, FAIL, f"notification not received within {LISTEN_NOTIFY_TIMEOUT_SECONDS:.0f}s"
            )
        finally:
            listen_conn.close()
            notify_conn.close()
    except Exception as exc:
        return _failure(name, exc)


def check_jwt_config() -> CheckResult:
    """JWKS endpoint serves keys, or the HS256 secret mints+verifies locally."""
    name = "JWKS / JWT config"
    from api.config import settings

    from api.services.auth_service import _neon_jwks_url, _resolved_jwks_url, neon_auth_enabled

    # Same derivation rules as api.services.auth_service: Neon Auth when
    # NEON_AUTH_URL is set, else Supabase.
    jwks_url = _neon_jwks_url() if neon_auth_enabled() else _resolved_jwks_url()
    try:
        if jwks_url:
            import httpx

            response = httpx.get(jwks_url, timeout=HTTP_TIMEOUT_SECONDS)
            response.raise_for_status()
            keys = response.json().get("keys") or []
            if keys:
                return CheckResult(name, PASS, f"JWKS serves {len(keys)} key(s): {jwks_url}")
            return CheckResult(name, FAIL, f"JWKS 'keys' array empty at {jwks_url}")
        if settings.supabase_jwt_secret:
            import jwt

            claims = {
                "sub": "pre-prod-checklist",
                "aud": settings.supabase_jwt_audience,
                "exp": int(time.time()) + 300,
            }
            token = jwt.encode(claims, settings.supabase_jwt_secret, algorithm="HS256")
            jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience=settings.supabase_jwt_audience,
            )
            return CheckResult(name, PASS, "HS256 token minted and verified with SUPABASE_JWT_SECRET")
        return CheckResult(
            name, SKIP, "no NEON_AUTH_URL / SUPABASE_JWKS_URL / SUPABASE_URL / SUPABASE_JWT_SECRET configured"
        )
    except Exception as exc:
        return _failure(name, exc)


def check_health_endpoint(api_base_url: str) -> CheckResult:
    """Poll GET /ready and require database/process readiness."""
    name = "/ready API readiness"
    if not api_base_url:
        return CheckResult(name, SKIP, "no --api-base-url (or API_BASE_URL env) provided")
    try:
        import httpx

        response = httpx.get(api_base_url.rstrip("/") + "/ready", timeout=HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        checks = payload.get("checks") or {}
        database = checks.get("database") or {}
        scheduler = checks.get("scheduler") or {}
        detail = (
            f"status={payload.get('status')} role={payload.get('process_role')} "
            f"database.ready={database.get('ready')} scheduler.ready={scheduler.get('ready')}"
        )
        status = PASS if payload.get("status") == "ready" else FAIL
        return CheckResult(name, status, detail)
    except Exception as exc:
        return _failure(name, exc)


def check_scheduler_config() -> CheckResult:
    """Scheduler-related settings are internally sensible (no live infra)."""
    name = "scheduler config sanity"
    from api.config import settings

    problems: list[str] = []
    if settings.scheduler_tick_minutes < 1:
        problems.append(f"ROOMRATE_SCHEDULER_TICK_MINUTES={settings.scheduler_tick_minutes} (< 1)")
    if settings.executor_batch_size < 1:
        problems.append(f"ROOMRATE_EXECUTOR_BATCH_SIZE={settings.executor_batch_size} (< 1)")
    if settings.stale_job_minutes < 1:
        problems.append(f"ROOMRATE_STALE_JOB_MINUTES={settings.stale_job_minutes} (< 1)")
    if settings.scrape_csv_retention_days < 0:
        problems.append(f"SCRAPE_CSV_RETENTION_DAYS={settings.scrape_csv_retention_days} (< 0)")
    if not 1 <= settings.scrape_job_max_attempts <= 10:
        problems.append(f"SCRAPE_JOB_MAX_ATTEMPTS={settings.scrape_job_max_attempts} (outside 1..10)")
    if settings.scrape_job_retry_base_seconds < 1:
        problems.append(
            f"SCRAPE_JOB_RETRY_BASE_SECONDS={settings.scrape_job_retry_base_seconds} (< 1)"
        )
    if settings.scrape_job_retry_max_seconds < settings.scrape_job_retry_base_seconds:
        problems.append("SCRAPE_JOB_RETRY_MAX_SECONDS is lower than the base delay")
    if problems:
        return CheckResult(name, FAIL, "; ".join(problems))
    detail = (
        f"enabled={settings.scheduler_enabled} tick={settings.scheduler_tick_minutes}m "
        f"batch={settings.executor_batch_size} stale={settings.stale_job_minutes}m "
        f"csv_retention={settings.scrape_csv_retention_days}d role={settings.process_role} "
        f"attempts={settings.scrape_job_max_attempts} retry="
        f"{settings.scrape_job_retry_base_seconds}-{settings.scrape_job_retry_max_seconds}s"
    )
    if not settings.scheduler_enabled:
        detail += " (disabled: enable on the worker(s) meant to run background jobs)"
    return CheckResult(name, PASS, detail)


def run_all_checks(api_base_url: str = "") -> list[CheckResult]:
    """Run every checklist item, converting all errors into result rows."""
    return [
        check_database_and_migrations(),
        check_repository_sql_smoke(),
        check_normalized_schema_integrity(),
        check_listen_notify(),
        check_jwt_config(),
        check_health_endpoint(api_base_url),
        check_scheduler_config(),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the docs §14 pre-production checks against live infrastructure."
    )
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("API_BASE_URL", ""),
        help="Base URL of a running API (e.g. https://api.example.com) for the /ready "
        "check; defaults to the API_BASE_URL environment variable.",
    )
    args = parser.parse_args(argv)

    results = run_all_checks(api_base_url=args.api_base_url)
    print(format_results_table(results))
    exit_code = summarize_exit_code(results)
    failed = sum(1 for result in results if result.status == FAIL)
    skipped = sum(1 for result in results if result.status == SKIP)
    print(f"\n{'FAIL' if exit_code else 'OK'}: {failed} failed, {skipped} skipped, {len(results)} total")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
