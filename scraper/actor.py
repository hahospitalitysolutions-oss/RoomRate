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
) -> list[dict]:
    """
    Τρέχει τον voyager/booking-scraper actor με retry logic.
    `label` χρησιμοποιείται μόνο για logging (π.χ. "batch 2/3").
    """
    prefix = f"[{label}] " if label else ""
    for attempt in range(1, max_retries + 1):
        try:
            run   = client.actor("voyager/booking-scraper").call(run_input=actor_input)
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
