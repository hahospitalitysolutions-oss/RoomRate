"""Manual PostgreSQL backup via pg_dump (docs/DEPLOYMENT.md § Backups).

Supabase's own managed backups are the PRIMARY mechanism; this script is the
portable second copy — the one you can restore into any Postgres, keep off the
provider, and hand to an auditor.

    python scripts/backup_db.py                     # -> output/backups/*.dump
    python scripts/backup_db.py --output-dir /srv/backups
    python scripts/backup_db.py --dry-run           # show what would run

Custom format (-Fc) is used so `pg_restore` can restore selectively and in
parallel; --no-owner/--no-privileges keep the dump restorable into a database
whose roles differ from production's (a Supabase dump restored locally).

Credentials are never printed and never appear in the process list: the
connection is passed to pg_dump through PG* environment variables rather than
as a URL argument, so `ps` on a shared host cannot leak the password.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.config import settings

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "backups"


def build_connection_env(database_url: str) -> tuple[dict[str, str], str, str]:
    """Translate a SQLAlchemy/libpq URL into pg_dump PG* environment variables.

    Args:
        database_url: The DATABASE_URL value, with or without a SQLAlchemy
            driver suffix (``postgresql+psycopg2://``).

    Returns:
        A (environment, database_name, safe_target) triple. ``safe_target`` is a
        host:port/database label that is safe to log — it carries no user and
        no password.

    Raises:
        ValueError: If the URL is not a PostgreSQL URL or names no database.
    """
    parts = urlsplit(database_url)
    # SQLAlchemy spells the driver into the scheme; libpq tools do not know it.
    scheme = parts.scheme.split("+", 1)[0].lower()
    if scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"DATABASE_URL must be a PostgreSQL URL, got scheme {scheme!r}")

    database_name = unquote(parts.path.lstrip("/"))
    if not database_name:
        raise ValueError("DATABASE_URL does not name a database")

    connection_env = dict(os.environ)
    connection_env["PGDATABASE"] = database_name
    if parts.hostname:
        connection_env["PGHOST"] = parts.hostname
    if parts.port:
        connection_env["PGPORT"] = str(parts.port)
    if parts.username:
        connection_env["PGUSER"] = unquote(parts.username)
    if parts.password:
        # The ONLY place the password exists in this process, and it is handed
        # to the child through its environment — never through argv.
        connection_env["PGPASSWORD"] = unquote(parts.password)

    # Supabase connection strings usually carry ?sslmode=require; dropping it
    # would turn a working URL into a rejected connection.
    query = parse_qs(parts.query)
    for query_key, pg_key in (("sslmode", "PGSSLMODE"), ("sslrootcert", "PGSSLROOTCERT")):
        if query.get(query_key):
            connection_env[pg_key] = query[query_key][0]

    host_label = parts.hostname or "localhost"
    port_label = parts.port or 5432
    return connection_env, database_name, f"{host_label}:{port_label}/{database_name}"


def build_dump_path(output_dir: Path, database_name: str) -> Path:
    """Return a timestamped dump path so repeated runs never overwrite."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return output_dir / f"roomrate_{database_name}_{stamp}.dump"


def run_backup(output_dir: Path, dry_run: bool) -> int:
    """Dump the configured database and report the process exit code.

    Returns:
        0 on success, 2 on a missing prerequisite or bad configuration, and
        pg_dump's own exit code when the dump itself fails.
    """
    if not settings.database_url:
        print("DATABASE_URL is not set; nothing to back up.", file=sys.stderr)
        return 2

    # Prerequisite check BEFORE anything else: pg_dump ships with the Postgres
    # client tools (Debian/Ubuntu: postgresql-client, macOS: libpq, Windows:
    # the EnterpriseDB installer) and is not part of this repo's dependencies.
    pg_dump_path = shutil.which("pg_dump")
    if pg_dump_path is None:
        print(
            "pg_dump was not found on PATH.\n"
            "  Debian/Ubuntu: apt-get install postgresql-client\n"
            "  macOS:         brew install libpq && brew link --force libpq\n"
            "  Windows:       install PostgreSQL and add its bin/ to PATH\n"
            "Use a client whose major version is >= the server's.",
            file=sys.stderr,
        )
        return 2

    try:
        connection_env, database_name, safe_target = build_connection_env(settings.database_url)
    except ValueError as exc:
        print(f"Invalid DATABASE_URL: {exc}", file=sys.stderr)
        return 2

    dump_path = build_dump_path(output_dir, database_name)
    command = [
        pg_dump_path,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        # Fail fast in cron instead of blocking on an interactive prompt.
        "--no-password",
        f"--file={dump_path}",
    ]

    # Safe to print: the connection lives in the environment, so the command
    # line holds no user, host or password.
    print(f"Backing up {safe_target}")
    print(f"  {' '.join(command)}")
    if dry_run:
        print("Dry run: pg_dump was not executed.")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(command, env=connection_env, check=False)
    if completed.returncode != 0:
        # pg_dump already wrote its diagnostics to stderr. Remove the partial
        # file so a failed run cannot be mistaken for a restorable backup.
        dump_path.unlink(missing_ok=True)
        print(f"pg_dump failed with exit code {completed.returncode}", file=sys.stderr)
        return completed.returncode

    size_mb = dump_path.stat().st_size / (1024 * 1024)
    print(f"Wrote {dump_path} ({size_mb:.1f} MB)")
    print("Restore with: pg_restore --no-owner --no-privileges --clean --if-exists "
          f'-d "$DATABASE_URL" "{dump_path}"')
    return 0


def main() -> int:
    """Parse arguments and run the backup.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Dump the RoomRate PostgreSQL database with pg_dump (custom format).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for the dump file (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the pg_dump command without executing it.",
    )
    arguments = parser.parse_args()
    return run_backup(arguments.output_dir, arguments.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
