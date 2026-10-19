"""Parallel deep-crawl execution for discovered hotel URLs."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .actor import ActorRunError, _run_actor
from .checkpoint import JobCheckpoint, batch_name
from .config import ScraperConfig
from .logging_config import log
from .utils import _append_error_log, _chunked, _normalize_url

def _deep_crawl_batch(
    client: Any,
    urls: list[str],
    config: ScraperConfig,
    batch_label: str,
    max_items: int | None = None,
) -> list[dict]:
    """
    Τρέχει έναν Deep Crawl actor για ένα batch από URLs.
    Καλείται από threads — thread-safe γιατί κάθε call δημιουργεί
    νέο ApifyClient run ανεξάρτητα.
    """
    return _run_actor(
        client,
        actor_input={
            "startUrls": [{"url": u} for u in urls],
            "checkIn":   config.check_in.strftime("%Y-%m-%d"),
            "checkOut":  config.check_out.strftime("%Y-%m-%d"),
            "rooms":     config.rooms,
            "adults":    config.adults,
            "children":  config.children,
            "currency":  config.currency,
            "language":  config.language,
            "maxItems":  max_items or config.deep_crawl_max_items,
        },
        max_retries=config.max_retries,
        retry_delay=config.retry_delay_sec,
        label=batch_label,
    )


def fetch_deep_room_data(
    client: Any,
    hotel_list: list[dict],
    config: ScraperConfig,
    progress: Any | None = None,
    checkpoint: JobCheckpoint | None = None,
) -> list[dict]:
    """
    Stage 2 — Parallel Deep Crawl.

    Σπάει τα hotel URLs σε batches του `deep_crawl_batch_size`
    και τα τρέχει ταυτόχρονα με ThreadPoolExecutor.

    Παράδειγμα (10 hotels, batch_size=4, workers=3):
      batch 1: hotels [0..3]  ─┐
      batch 2: hotels [4..7]  ─┤ τρέχουν ταυτόχρονα
      batch 3: hotels [8..9]  ─┘

    `progress` (ProgressReporter ή None) λαμβάνει stage="deep_crawl" με
    done/total καταλύματα στην έναρξη και μετά από κάθε batch.
    """
    if not hotel_list:
        log.warning("[ΣΤΑΔΙΟ 2] Κενή λίστα — παραλείπω Deep Crawl.")
        return []

    urls = list(dict.fromkeys(_normalize_url(h["url"]) for h in hotel_list if h.get("url")))
    batches = _chunked(urls, config.deep_crawl_batch_size)
    total   = len(batches)
    max_items_per_batch = max(1, math.ceil(config.deep_crawl_max_items / max(total, 1)))

    log.info(
        "[ΣΤΑΔΙΟ 2] Deep Crawl → %d ξενοδοχεία | %d batches | %d workers | maxItems/batch=%d",
        len(urls), total, min(config.deep_crawl_workers, total), max_items_per_batch,
    )

    all_items: list[dict] = []
    batch_errors: list[dict[str, object]] = []
    checkpoint = checkpoint or JobCheckpoint(None)
    # Batches an earlier attempt of this job already paid for: reused, not re-run.
    hotels_done = 0
    pending: list[int] = []
    for i, batch in enumerate(batches):
        cached = checkpoint.load(batch_name(batch))
        if isinstance(cached, list):
            all_items.extend(cached)
            hotels_done += len(batch)
        else:
            pending.append(i)
    if hotels_done:
        log.info(
            "[ΣΤΑΔΙΟ 2] %d/%d batches από προηγούμενη προσπάθεια — τρέχουν μόνο τα %d που λείπουν.",
            total - len(pending), total, len(pending),
        )
    # Η πρόοδος αλλάζει στάδιο αμέσως: το πρώτο batch μπορεί να κρατήσει λεπτά.
    if progress is not None:
        progress.update("deep_crawl", done=hotels_done, total=len(urls))

    with ThreadPoolExecutor(max_workers=config.deep_crawl_workers) as pool:
        futures = {
            pool.submit(
                _deep_crawl_batch,
                client,
                batches[i],
                config,
                f"batch {i + 1}/{total}",
                max_items_per_batch,
            ): i
            for i in pending
        }

        failed_batches = 0
        for future in as_completed(futures):
            batch_idx = futures[future]
            try:
                items = future.result()
                all_items.extend(items)
                checkpoint.save(batch_name(batches[batch_idx]), items)
                log.info(
                    "[ΣΤΑΔΙΟ 2] batch %d/%d ολοκληρώθηκε → %d items (σύνολο: %d)",
                    batch_idx + 1, total, len(items), len(all_items),
                )
            except Exception as e:
                failed_batches += 1
                batch_errors.append(
                    {
                        "field": "deep_crawl_batch",
                        "issue": str(e),
                        "value": f"batch {batch_idx + 1}/{total}",
                    }
                )
                log.error(
                    "[ΣΤΑΔΙΟ 2] batch %d/%d απέτυχε: %s",
                    batch_idx + 1, total, e,
                )
            # Και ένα αποτυχημένο batch έχει τελειώσει: η πρόοδος φτάνει πάντα
            # στο total, και η απόφαση για ελλιπές snapshot παίρνεται παρακάτω.
            hotels_done += len(batches[batch_idx])
            if progress is not None:
                progress.update("deep_crawl", done=hotels_done, total=len(urls))

    # Μερικές αποτυχίες είναι ανεκτές (τα υπόλοιπα batches δίνουν δεδομένα),
    # αλλά ολική αποτυχία = αποτυχημένο scrape, όχι "κενή αγορά".
    _append_error_log(config, batch_errors)
    if failed_batches and (not config.allow_partial_batches or not all_items):
        raise ActorRunError(
            f"Απέτυχαν {failed_batches}/{total} Deep Crawl batches; "
            "το ελλιπές snapshot δεν αποθηκεύτηκε."
        )

    log.info("[ΣΤΑΔΙΟ 2] Deep Crawl ολοκληρώθηκε → %d raw items συνολικά.", len(all_items))
    return all_items
