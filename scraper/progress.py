"""Best-effort live progress for scrape jobs (Round 6 §3.5).

The API reads the scraper's stdout only when the process exits, so the
scraper itself merges a ``progress`` object into ``result_summary`` while the
job is running. ``complete_job`` later replaces the whole summary, so the key
never outlives the run. Progress must never fail a scrape: every error is
logged and swallowed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from .logging_config import log

# CAST(:progress AS jsonb), not ``:progress::jsonb``: SQLAlchemy's text()
# would parse the latter as a bind named ``progres``. The status fence keeps a
# late snapshot from resurrecting progress on a job that already finished.
PROGRESS_UPDATE_SQL = text(
    """
    UPDATE roomrate_scrape_jobs
    SET result_summary = COALESCE(result_summary, '{}'::jsonb)
                         || jsonb_build_object('progress', CAST(:progress AS jsonb)),
        updated_at = now()
    WHERE id = :job_id
      AND status = 'running'
    """
)


class ProgressReporter:
    """Writes ``result_summary.progress`` for one running job.

    Counts accumulate across calls (``destinations_total``, ``hotels_found``,
    ``hotels_in_radius``), so every snapshot carries the whole picture;
    ``done``/``total`` describe the current stage and reset when it changes
    (scouts during ``scout``, hotels during ``deep_crawl``). Callers report
    from the orchestrating thread only (the ``as_completed`` loops), so the
    snapshot state needs no lock.
    """

    def __init__(self, engine: Any, scrape_job_id: uuid.UUID | None):
        self.engine = engine
        self.scrape_job_id = scrape_job_id
        self._state: dict[str, Any] = {}

    @property
    def enabled(self) -> bool:
        """False in dry runs (no engine) or interactive runs (no job id)."""
        return self.engine is not None and self.scrape_job_id is not None

    def update(
        self,
        stage: str,
        done: int | None = None,
        total: int | None = None,
        **counts: int | None,
    ) -> bool:
        """Merge one progress snapshot into the job row; True when written."""
        if stage != self._state.get("stage"):
            self._state.pop("done", None)
            self._state.pop("total", None)
        self._state["stage"] = stage
        for key, value in (("done", done), ("total", total), *counts.items()):
            if value is not None:
                self._state[key] = value
        self._state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not self.enabled:
            return False
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    PROGRESS_UPDATE_SQL,
                    {"progress": json.dumps(self._state, ensure_ascii=False), "job_id": self.scrape_job_id},
                )
            return True
        except Exception as exc:
            log.warning("Η ενημέρωση προόδου απέτυχε (η εργασία συνεχίζει): %s", exc)
            return False
