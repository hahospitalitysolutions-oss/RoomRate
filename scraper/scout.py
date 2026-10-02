"""Booking.com scout discovery and PostgreSQL cache management."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.services.market_helpers import haversine_km

from .actor import _run_actor
from .config import ScraperConfig
from .logging_config import log
from .tables import _scout_cache
from .utils import _extract_meta, _normalize_url, _safe_coord

def _load_scout_cache(engine, config: ScraperConfig) -> list[dict]:
    """
    Επιστρέφει cached ξενοδοχεία αν υπάρχουν φρέσκα αποτελέσματα
    (< scout_cache_hours ώρες) για τον ίδιο προορισμό + dates.
    Επιστρέφει [] αν δεν υπάρχει cache, έχει λήξει ή είναι μικρότερη
    από το αίτημα (scout_max_items).
    """
    if config.scout_cache_hours == 0:
        return []

    # Πραγματικά date/datetime objects — οι στήλες είναι πλέον date/timestamptz.
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.scout_cache_hours)
    ci = config.check_in.date() if isinstance(config.check_in, datetime) else config.check_in
    co = config.check_out.date() if isinstance(config.check_out, datetime) else config.check_out

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT hotel_name, hotel_url, stars, review_score,
                           review_count, latitude, longitude,
                           property_type, city, address
                    FROM   scout_cache
                    WHERE  destination = :dest
                      AND  check_in    = :ci
                      AND  check_out   = :co
                      AND  adults      = :adults
                      AND  children    = :children
                      AND  rooms       = :rooms
                      AND  currency    = :currency
                      AND  language    = :language
                      AND  cached_at  >= :cutoff
                """),
                {
                    "dest": config.destination,
                    "ci": ci,
                    "co": co,
                    "adults": config.adults,
                    "children": config.children,
                    "rooms": config.rooms,
                    "currency": config.currency,
                    "language": config.language,
                    "cutoff": cutoff,
                },
            ).fetchall()

        if not rows:
            return []

        # Το κλειδί της cache δεν περιέχει το limit (το μοιράζονται αναζητήσεις
        # με διαφορετικό πλήθος), άρα μια παλιά λίστα 10 δεν πρέπει να απαντά
        # σε αίτημα για 40 (Round 6 §3.3): MISS και φρέσκο scout.
        if len(rows) < config.scout_max_items:
            log.info(
                "[ΣΤΑΔΙΟ 1] Η cache του scout είναι μικρότερη από το αίτημα: %d < %d",
                len(rows), config.scout_max_items,
            )
            return []

        # Rename ώστε το downstream code να βλέπει "name" / "url"
        hotels = []
        for r in rows:
            h = dict(r._mapping)
            h["name"] = h.pop("hotel_name")
            h["url"]  = h.pop("hotel_url")
            # Rows cached before the address column existed read back NULL;
            # the live scout yields "" there, so normalize to keep cached and
            # cold hotels byte-identical for downstream consumers.
            h["address"] = h.get("address") or ""
            hotels.append(h)

        log.info(
            "[ΣΤΑΔΙΟ 1] Cache HIT — %d ξενοδοχεία (< %dh παλιά). "
            "0 Apify credits χρησιμοποιήθηκαν.",
            len(hotels), config.scout_cache_hours,
        )
        return hotels

    except Exception as e:
        # Αν ο πίνακας δεν υπάρχει ακόμα ή άλλο DB error, συνέχισε κανονικά
        log.debug("Scout cache lookup failed (%s) — θα τρέξω τον actor.", e)
        return []


def _save_scout_cache(engine, hotels: list[dict], config: ScraperConfig) -> None:
    """Αποθηκεύει τα αποτελέσματα Scout στη scout_cache."""
    if not hotels:
        return

    now    = datetime.now(timezone.utc)
    ci     = config.check_in.date() if isinstance(config.check_in, datetime) else config.check_in
    co     = config.check_out.date() if isinstance(config.check_out, datetime) else config.check_out

    rows = [
        {
            "destination":             config.destination,
            "check_in":                ci,
            "check_out":               co,
            "adults":                  config.adults,
            "children":                config.children,
            "rooms":                   config.rooms,
            "currency":                config.currency,
            "language":                config.language,
            "hotel_name":              h.get("name", ""),
            "hotel_url":               h.get("url", ""),
            "stars":                   h.get("stars", 0.0),
            "review_score":            h.get("review_score", 0.0),
            "review_count":            h.get("review_count", 0),
            "latitude":                h.get("latitude", 0.0),
            "longitude":               h.get("longitude", 0.0),
            "property_type":           h.get("property_type", "Hotel"),
            "city":                    h.get("city", ""),
            "address":                 h.get("address", ""),
            "cached_at":               now,
        }
        for h in hotels
    ]

    try:
        with engine.begin() as conn:
            # Καθαρίζει παλιές εγγραφές για τον ίδιο προορισμό + dates
            conn.execute(
                text("""
                    DELETE FROM scout_cache
                    WHERE destination = :dest
                      AND check_in    = :ci
                      AND check_out   = :co
                      AND adults      = :adults
                      AND children    = :children
                      AND rooms       = :rooms
                      AND currency    = :currency
                      AND language    = :language
                """),
                {
                    "dest": config.destination,
                    "ci": ci,
                    "co": co,
                    "adults": config.adults,
                    "children": config.children,
                    "rooms": config.rooms,
                    "currency": config.currency,
                    "language": config.language,
                },
            )
            statement = pg_insert(_scout_cache).values(rows)
            statement = statement.on_conflict_do_update(
                constraint="uq_scout_cache_query_hotel",
                set_={
                    "hotel_name": statement.excluded.hotel_name,
                    "stars": statement.excluded.stars,
                    "review_score": statement.excluded.review_score,
                    "review_count": statement.excluded.review_count,
                    "latitude": statement.excluded.latitude,
                    "longitude": statement.excluded.longitude,
                    "property_type": statement.excluded.property_type,
                    "city": statement.excluded.city,
                    "address": statement.excluded.address,
                    "cached_at": statement.excluded.cached_at,
                },
            )
            conn.execute(statement)
        log.info("[ΣΤΑΔΙΟ 1] %d ξενοδοχεία αποθηκεύτηκαν στο scout_cache.", len(rows))
    except Exception as e:
        log.warning("Αποτυχία αποθήκευσης scout cache: %s", e)


def fetch_hotel_list(
    client: Any,
    config: ScraperConfig,
    engine=None,
) -> list[dict]:
    """
    Stage 1 — Scout με cache.

    Ελέγχει πρώτα τη DB για φρέσκα αποτελέσματα.
    Αν βρει: επιστρέφει αμέσως (0 Apify credits).
    Αν όχι: τρέχει τον actor και αποθηκεύει το αποτέλεσμα για επόμενες φορές.
    """
    log.info("[ΣΤΑΔΙΟ 1] Scout → '%s'", config.destination)

    # Έλεγχος cache
    if engine is not None:
        cached = _load_scout_cache(engine, config)
        if cached:
            return cached

    log.info("[ΣΤΑΔΙΟ 1] Cache MISS — τρέχω Apify actor...")

    raw_items = _run_actor(
        client,
        actor_input={
            "search":   config.destination,
            "checkIn":  config.check_in.strftime("%Y-%m-%d"),
            "checkOut": config.check_out.strftime("%Y-%m-%d"),
            "maxItems": config.scout_max_items,
            "rooms":    config.rooms,
            "adults":   config.adults,
            "children": config.children,
            "currency": config.currency,
            "language": config.language,
        },
        max_retries=config.max_retries,
        retry_delay=config.retry_delay_sec,
        label="Scout",
    )

    hotels = []
    for item in raw_items:
        url  = item.get("url")
        name = item.get("name")
        if not url or not name:
            log.debug("Παραλείπω item χωρίς url/name: %s", item.get("id"))
            continue

        meta = _extract_meta(item)

        # Αποθηκεύουμε το normalized URL ώστε το join με το Stage 2 να δουλεύει
        # ακόμα και αν το Booking.com προσθέσει query params στα Deep Crawl URLs.
        hotels.append({
            "name": name,
            "url":  _normalize_url(url),   # ← κλειδί για το join
            **meta,
        })

        log.debug(
            "Scout item: %s | stars=%.1f score=%.1f lat=%.4f lng=%.4f",
            name, meta["stars"], meta["review_score"],
            meta["latitude"], meta["longitude"],
        )

    log.info("[ΣΤΑΔΙΟ 1] Scout βρήκε %d καταλύματα.", len(hotels))

    # Αποθήκευση στο cache μόνο όταν το cache είναι ενεργό.
    if engine is not None and hotels and config.scout_cache_hours > 0:
        _save_scout_cache(engine, hotels, config)

    return hotels


def _report(progress: Any | None, stage: str, **counts: int | None) -> None:
    if progress is not None:
        progress.update(stage, **counts)


def _hotel_key(hotel: dict) -> str:
    """Identity of a scouted hotel across destinations: its normalized URL ("" = none)."""
    return _normalize_url(str(hotel.get("url") or ""))


def _distance_from_origin(hotel: dict, config: ScraperConfig) -> float | None:
    """km from the owner's property; None when the hotel has no coordinates (0/0)."""
    lat = _safe_coord(hotel.get("latitude"))
    lng = _safe_coord(hotel.get("longitude"))
    # _extract_meta and the cache store a missing location as 0.0, never NULL.
    if lat == 0.0 or lng == 0.0:
        return None
    return haversine_km(config.origin_lat, config.origin_lng, lat, lng)


def fetch_hotel_lists(
    client: Any,
    config: ScraperConfig,
    engine=None,
    progress: Any | None = None,
    warnings: list[str] | None = None,
) -> list[dict]:
    """Stage 1 for the main destination plus its nearby areas (Round 6 §3.3).

    Runs one ``fetch_hotel_list`` per destination in parallel (cache and
    retries as today), merges by normalized URL with the main destination
    winning, applies the radius from the owner's coordinates, sorts nearest
    first and trims to ``deep_crawl_max_hotels``. A failed nearby scout is a
    warning (``nearby_scout_failed:<destination>`` appended to ``warnings``);
    a failed main scout raises exactly as before. Distances are NOT stored —
    the read API recomputes them from the coordinates.
    """
    destinations = [config.destination, *config.nearby_destinations]
    scout_configs = [
        replace(
            config,
            destination=destination,
            scout_max_items=config.scout_max_items if index == 0 else config.nearby_max_items,
        )
        for index, destination in enumerate(destinations)
    ]
    _report(progress, "scout", done=0, total=len(destinations), destinations_total=len(destinations), hotels_found=0)

    results: dict[int, list[dict]] = {}
    # Μοναδικά καταλύματα όσων scouts έχουν τελειώσει: το ίδιο ξενοδοχείο σε δύο
    # περιοχές μετρά μία φορά, άρα το hotels_found δεν πέφτει μετά την ένωση.
    found_keys: set[str] = set()
    with ThreadPoolExecutor(max_workers=config.deep_crawl_workers) as pool:
        futures = {
            pool.submit(fetch_hotel_list, client, scout_config, engine): index
            for index, scout_config in enumerate(scout_configs)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                if index == 0:
                    # Χωρίς τον κύριο προορισμό δεν υπάρχει αγορά: η αποτυχία
                    # του (ActorRunError μετά τα retries) αποτυγχάνει το scrape
                    # όπως σήμερα (exit 2 στο cli). Τα scouts σε αναμονή
                    # ακυρώνονται — αλλιώς το with θα τα έτρεχε (και θα τα
                    # πλήρωνε) πριν φτάσει το σφάλμα· όσα ήδη τρέχουν τελειώνουν.
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
                log.warning(
                    "[ΣΤΑΔΙΟ 1] Το scout για τη γειτονική περιοχή '%s' απέτυχε (%s) — συνεχίζω χωρίς αυτήν.",
                    destinations[index], exc,
                )
                if warnings is not None:
                    warnings.append(f"nearby_scout_failed:{destinations[index]}")
                results[index] = []
            found_keys.update(key for key in map(_hotel_key, results[index]) if key)
            _report(
                progress, "scout",
                done=len(results), total=len(destinations),
                hotels_found=len(found_keys),
            )

    # Ένωση: κλειδί το normalized URL, ο κύριος προορισμός (index 0) κερδίζει.
    merged: dict[str, dict] = {}
    for index in range(len(destinations)):
        for hotel in results.get(index, []):
            key = _hotel_key(hotel)
            if key and key not in merged:
                merged[key] = {**hotel, "url": key}
    hotels = list(merged.values())
    log.info("[ΣΤΑΔΙΟ 1] Ένωση %d scouts → %d μοναδικά καταλύματα.", len(destinations), len(hotels))

    if config.origin_lat is not None and config.origin_lng is not None:
        ranked: list[tuple[float | None, dict]] = []
        dropped_outside = dropped_no_coords = 0
        for hotel in hotels:
            distance = _distance_from_origin(hotel, config)
            if config.radius_km is not None:
                if distance is None:
                    dropped_no_coords += 1
                    log.info("[ΣΤΑΔΙΟ 1] '%s' χωρίς συντεταγμένες — εκτός ακτίνας.", hotel.get("name"))
                    continue
                if distance > config.radius_km:
                    dropped_outside += 1
                    continue
            ranked.append((distance, hotel))
        # Πλησιέστερα πρώτα, None στο τέλος· stable sort κρατά τη σειρά scout στις ισοπαλίες.
        ranked.sort(key=lambda pair: (pair[0] is None, pair[0] or 0.0))
        hotels = [hotel for _, hotel in ranked]
        if config.radius_km is not None:
            log.info(
                "[ΣΤΑΔΙΟ 1] Ακτίνα %.1f km: %d εντός, %d εκτός, %d χωρίς συντεταγμένες.",
                config.radius_km, len(hotels), dropped_outside, dropped_no_coords,
            )
    hotels_in_radius = len(hotels)
    # Βρέθηκαν καταλύματα αλλά η ακτίνα τα έκοψε όλα (π.χ. ανεστραμμένες ή
    # λάθος συντεταγμένες του ιδιοκτήτη): το κενό αποτέλεσμα πρέπει να εξηγείται
    # στη σύνοψη, όχι να μοιάζει με άδεια αγορά.
    if config.radius_active and merged and not hotels:
        log.warning(
            "[ΣΤΑΔΙΟ 1] Κανένα από τα %d καταλύματα δεν είναι εντός %.1f km — ελέγξτε τις συντεταγμένες του καταλύματος.",
            len(merged), config.radius_km,
        )
        if warnings is not None:
            warnings.append("radius_excluded_all")

    if config.deep_crawl_max_hotels is not None and len(hotels) > config.deep_crawl_max_hotels:
        log.info(
            "[ΣΤΑΔΙΟ 1] Περικοπή %d → %d καταλύματα (deep_crawl_max_hotels).",
            len(hotels), config.deep_crawl_max_hotels,
        )
        hotels = hotels[: config.deep_crawl_max_hotels]

    _report(
        progress, "scout",
        done=len(destinations), total=len(destinations),
        hotels_found=len(merged), hotels_in_radius=hotels_in_radius,
    )
    return hotels
