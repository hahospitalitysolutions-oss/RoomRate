"""Per-job checkpoints: a retried job reuses what its earlier attempts paid for.

A scrape job that fails is retried as a whole (SCRAPE_JOB_MAX_ATTEMPTS). One
deep-crawl batch failing used to re-run the scout and EVERY batch on each
attempt, up to three times the Apify credits. The checkpoint keeps the scout's
hotel list and each successful batch's raw items next to the job's CSV, so the
next attempt runs only what is missing. It is removed once the job has a
result; the scrape-CSV retention sweep removes what a finally failed job left.

Writes are atomic (temp file + rename): a job killed at its timeout never
leaves half a file, and an unreadable file is ignored and re-scraped.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from .config import ScraperConfig
from .logging_config import log

CHECKPOINT_PREFIX = "checkpoint_"


class JobCheckpoint:
    """Stored stage results of one scrape job; a no-op when ``directory`` is None."""

    def __init__(self, directory: Path | None):
        self.directory = directory

    @classmethod
    def for_config(cls, config: ScraperConfig) -> JobCheckpoint:
        """Enabled only for a persisted job: dry runs and ad-hoc runs keep nothing."""
        if config.dry_run or config.scrape_job_id is None:
            return cls(None)
        return cls(Path(config.output_csv).parent / f"{CHECKPOINT_PREFIX}{config.scrape_job_id}")

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def load(self, name: str) -> Any | None:
        if self.directory is None:
            return None
        path = self.directory / f"{name}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            log.warning("Checkpoint '%s' δεν διαβάζεται (%s) — ξανά από την αρχή.", path.name, exc)
            return None

    def save(self, name: str, data: Any) -> None:
        if self.directory is None:
            return
        path = self.directory / f"{name}.json"
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
            os.replace(temporary, path)
        except (OSError, TypeError, ValueError) as exc:
            # A checkpoint only saves credits on a retry; never fail the scrape for it.
            log.warning("Checkpoint '%s' δεν αποθηκεύτηκε: %s", name, exc)
            temporary.unlink(missing_ok=True)

    def clear(self) -> None:
        if self.directory is not None:
            shutil.rmtree(self.directory, ignore_errors=True)


def batch_name(urls: list[str]) -> str:
    """Stable name of a deep-crawl batch: its hotel URLs, order-independent."""
    digest = hashlib.sha256("\n".join(sorted(urls)).encode("utf-8")).hexdigest()[:20]
    return f"batch_{digest}"
