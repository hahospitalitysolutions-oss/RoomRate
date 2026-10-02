import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

from api.config import settings

# Production pool defaults for the API engine, PER PROCESS: every worker
# (e.g. gunicorn -w 4) builds its own engine and pool, so PostgreSQL can see
# up to workers x (POOL_SIZE + MAX_OVERFLOW) connections from the API role
# alone (plus the writer engine and the LISTEN connection per process). Size
# max_connections — or put pgbouncer in front — accordingly.
POOL_SIZE = 10
MAX_OVERFLOW = 20
POOL_RECYCLE_SECONDS = 1800
API_STATEMENT_TIMEOUT_MS = 30_000

EngineRole = Literal["api", "writer"]

# One cached engine per role: "api" gets a server-side statement timeout,
# "writer" does not (scraper bulk writes may legitimately exceed 30s).
_engines: dict[EngineRole, Any] = {}
# Engine creation can race when worker threads call get_engine() concurrently;
# the lock guarantees exactly one engine (and pool) per role.
_engines_lock = threading.Lock()


def get_engine(role: EngineRole = "api") -> Any:
    """Create the SQLAlchemy engine lazily for the requested role.

    Args:
        role: "api" for request paths (short queries, protected by a
            server-side statement timeout); "writer" for the scraper/writer
            path whose long bulk transactions must not be killed mid-write.

    Raises:
        RuntimeError: If DATABASE_URL is missing or SQLAlchemy is not installed.
    """
    engine = _engines.get(role)
    if engine is not None:
        return engine
    with _engines_lock:
        # Re-check under the lock: another thread may have created it first.
        engine = _engines.get(role)
        if engine is not None:
            return engine
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is required for PostgreSQL access")
        try:
            import sqlalchemy
        except ImportError as exc:
            raise RuntimeError("SQLAlchemy is required. Install dependencies from requirements.txt") from exc
        connect_args = (
            {"options": f"-c statement_timeout={API_STATEMENT_TIMEOUT_MS}"}
            if role == "api"
            else {}
        )
        engine = sqlalchemy.create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=POOL_SIZE,
            max_overflow=MAX_OVERFLOW,
            pool_recycle=POOL_RECYCLE_SECONDS,
            connect_args=connect_args,
        )
        _engines[role] = engine
        return engine


@contextmanager
def db_connection() -> Iterator[Any]:
    """Yield a PostgreSQL connection for repository methods."""
    engine = get_engine()
    with engine.connect() as connection:
        yield connection
