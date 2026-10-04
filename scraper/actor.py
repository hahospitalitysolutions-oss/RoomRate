"""Reliable Apify actor execution with retry backoff."""

from __future__ import annotations

import random
import time
from typing import Any

try:
    from apify_client.errors import ApifyApiError
except ImportError:
    try:
        from apify_client._errors import ApifyApiError
    except ImportError:
        ApifyApiError = Exception

from .logging_config import log

class ActorRunError(RuntimeError):
    """Ο Apify actor απέτυχε σε όλα τα retries (≠ επέστρεψε 0 αποτελέσματα)."""


ACTOR_ID = "voyager/booking-scraper"
# Apify stops a run after this long. A scout or an 8-hotel batch takes 1-4
# minutes; three attempts of 8 minutes stay inside the 30-minute job
# timeout, so one stuck run can no longer take the whole scrape with it.
ACTOR_RUN_TIMEOUT_SECS = 480
# How much longer the client waits for Apify to report the timed-out run.
_WAIT_MARGIN_SECS = 60
# Only SUCCEEDED means the dataset is complete. FAILED, TIMED-OUT and ABORTED
# runs come back from call() WITHOUT an exception, with a partial or empty
# dataset; reading that as the result dropped hotels from the market silently.
_SUCCEEDED = "SUCCEEDED"
_STILL_GOING = frozenset({"READY", "RUNNING"})


class _RunNotSucceeded(RuntimeError):
    """One attempt's run ended in another status than SUCCEEDED."""


def _abort_quietly(client: Any, run_id: str, prefix: str) -> None:
    """Stop a run that outlived the wait, so it stops costing credits."""
    try:
        client.run(run_id).abort()
    except Exception as exc:  # the attempt already failed; nothing more to lose
        log.warning("%sAbort του run %s απέτυχε: %s", prefix, run_id, exc)


def _retry_delay_seconds(base_delay: int, attempt: int, max_delay: int = 60) -> float:
    """Return capped exponential backoff with full jitter.

    Args:
        base_delay: Initial retry delay in seconds.
        attempt: One-based failed attempt number.
        max_delay: Upper bound for the exponential window.

    Returns:
        A randomized delay in seconds, bounded by ``max_delay``.

    Raises:
        ValueError: If any argument is outside its valid range.
    """
    if base_delay < 1 or attempt < 1 or max_delay < base_delay:
        raise ValueError("Invalid retry backoff parameters")
    return random.uniform(0.0, min(max_delay, base_delay * (2 ** (attempt - 1))))


def _run_actor(
    client: Any,
    actor_input: dict,
    max_retries: int,
    retry_delay: int,
    label: str = "",
    timeout_secs: int = ACTOR_RUN_TIMEOUT_SECS,
) -> list[dict]:
    """
    Τρέχει τον voyager/booking-scraper actor με retry logic.
    `label` χρησιμοποιείται μόνο για logging (π.χ. "batch 2/3").
    Μόνο ένα run με status SUCCEEDED δίνει αποτελέσματα· κάθε άλλο είναι
    αποτυχημένη προσπάθεια (retry), όχι «0 ξενοδοχεία».
    """
    prefix = f"[{label}] " if label else ""
    for attempt in range(1, max_retries + 1):
        try:
            run = client.actor(ACTOR_ID).call(
                run_input=actor_input,
                timeout_secs=timeout_secs,
                wait_secs=timeout_secs + _WAIT_MARGIN_SECS,
            )
            status = (run or {}).get("status")
            if status != _SUCCEEDED:
                if run and status in _STILL_GOING:
                    _abort_quietly(client, run["id"], prefix)
                raise _RunNotSucceeded(f"run {(run or {}).get('id')} ended with status {status}")
            items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
            log.info("%sActor → %d items (attempt %d/%d)", prefix, len(items), attempt, max_retries)
            return items
        except ApifyApiError as e:
            log.warning("%sApifyApiError attempt %d/%d: %s", prefix, attempt, max_retries, e)
        except Exception as e:
            log.warning("%sUnexpected error attempt %d/%d: %s", prefix, attempt, max_retries, e)
        if attempt < max_retries:
            delay = _retry_delay_seconds(retry_delay, attempt)
            log.info("%sRetry σε %.1fs (exponential backoff + jitter)...", prefix, delay)
            time.sleep(delay)

    # Το failure πρέπει να διακρίνεται από το "actor έτρεξε και βρήκε 0 items":
    # αν επέστρεφε [], το API θα μαρκάριζε ένα ολικά αποτυχημένο scrape ως
    # completed job (exit code 0) χωρίς δεδομένα.
    raise ActorRunError(f"{prefix}Actor απέτυχε μετά από {max_retries} attempts.")
