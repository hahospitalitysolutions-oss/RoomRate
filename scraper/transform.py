"""Transform raw Apify hotel payloads into normalized tabular records."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from api.services.room_rates_normalizer import normalize_room_type_category

from .config import ScraperConfig
from .logging_config import log
from .utils import (
    _clean_discount_label,
    _clean_text,
    _detect_meal,
    _detect_payment,
    _extract_meta,
    _fix_encoding,
    _make_record_id,
    _normalize_facilities,
    _normalize_url,
    _parse_discount_pct,
    _safe_float,
    _safe_int,
)

def process_and_flatten_data(
    raw_data: list[dict],
    hotel_meta: list[dict],
    config: ScraperConfig,
) -> pd.DataFrame:
    """
    Flat ens raw Apify items → DataFrame έτοιμο για αποθήκευση.
    Κάθε row = hotel × room_type × option (μοναδικό πακέτο).
    Metadata join γίνεται βάσει URL (όχι ονόματος).
    """
    log.info("[ΣΤΑΔΙΟ 3] Flattening → %d raw items", len(raw_data))

    # Κτίζουμε το index με normalized URLs ώστε να ταιριάζει ακόμα
    # και αν το Deep Crawl επιστρέφει URLs με query params.
    meta_by_url = {_normalize_url(h["url"]): h for h in hotel_meta}
    ci_str      = config.check_in.strftime("%Y-%m-%d")
    co_str      = config.check_out.strftime("%Y-%m-%d")
    nights      = (config.check_out - config.check_in).days
    now_str     = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    records        = []
    seen_ids       = set()
    skipped_rooms  = 0
    duplicate_count = 0
    skipped_zero_price = 0

    for property_item in raw_data:
        hotel_name = property_item.get("name", "Άγνωστο Κατάλυμα")
        hotel_name = _clean_text(hotel_name) or "Unknown Property"
        hotel_url  = _normalize_url(property_item.get("url", ""))
        base_price = _safe_float(property_item.get("price"))
        meta       = meta_by_url.get(hotel_url, {})

        if not meta:
            # Fallback: δοκίμασε να εξάγεις metadata κατευθείαν από το Deep Crawl item
            # (μερικές εκδόσεις του actor επιστρέφουν τα πεδία και στο Stage 2)
            fallback = _extract_meta(property_item)
            if any(v for v in [fallback["latitude"], fallback["stars"], fallback["review_score"]]):
                meta = fallback
                log.debug("'%s': metadata από Deep Crawl fallback (lat=%.4f).", hotel_name, fallback["latitude"])
            else:
                log.warning(
                    "'%s': δεν βρέθηκε metadata (url_key='%s'). "
                    "Τα coordinates/stars θα είναι 0.",
                    hotel_name, hotel_url,
                )

        rooms_list = property_item.get("rooms", [])
        if not isinstance(rooms_list, list):
            continue

        for room in rooms_list:
            room_name  = room.get("roomType", "Standard Δωμάτιο")
            room_name = _clean_text(room_name) or "Standard Room"
            # Σταθερό ανά room — υπολογίζεται μία φορά, όχι ανά option.
            room_type_category = normalize_room_type_category(room_name)
            rooms_left = _safe_int(room.get("roomsLeft"), default=0)

            # facilities: λίστα παροχών δωματίου → pipe-separated string
            raw_fac = room.get("facilities", []) or []
            facilities = _normalize_facilities(raw_fac)

            options = room.get("options", [])

            if not options or not isinstance(options, list):
                skipped_rooms += 1
                log.debug("'%s' / '%s': χωρίς options, παραλείπεται.", hotel_name, room_name)
                continue

            for option_index, opt in enumerate(options):
                # Χωρίς id (ή id=null) κάθε option παίρνει δικό του index —
                # αλλιώς όλα κατέληγαν στο ίδιο record_id και το dedup
                # πετούσε σιωπηλά τα υπόλοιπα rate variants του δωματίου.
                package_id = opt.get("id") or f"opt{option_index}"
                # Το URL αποτρέπει collision όταν δύο διαφορετικά listings
                # έχουν το ίδιο fallback/display name και provider option id.
                record_id  = _make_record_id(
                    hotel_url or hotel_name,
                    room_name,
                    str(package_id),
                    config,
                )

                if record_id in seen_ids:
                    duplicate_count += 1
                    continue
                seen_ids.add(record_id)

                guests      = config.adults + config.children
                # "price": null (συχνό σε sold-out πακέτα) πρέπει να πέφτει στο
                # base_price — το dict.get default ισχύει μόνο όταν ΛΕΙΠΕΙ το
                # key, οπότε null θα γινόταν 0.0 και θα μόλυνε κάθε MIN(price).
                raw_price    = opt.get("price")
                room_price   = _safe_float(raw_price if raw_price is not None else base_price)
                if room_price <= 0:
                    skipped_zero_price += 1
                    continue
                cancellation = "Yes" if opt.get("freeCancellation") else "No"

                # Max guests του δωματίου από το Apify `persons` πεδίο:
                # option-level τιμή προηγείται, room-level ως fallback.
                # 0 = άγνωστο — το room_matching._parse_capacity το αγνοεί,
                # οπότε δεν "μολύνει" τα extracted room_attributes.
                max_persons = _safe_int(opt.get("persons", room.get("persons")), default=0)

                choices = opt.get("yourChoices", []) or []
                meals   = _detect_meal(choices)
                if not meals:
                    meals = (
                        "Πρωινό (Από Ξενοδοχείο)"
                        if property_item.get("breakfast")
                        else "Δεν περιλαμβάνεται"
                    )

                price_per_night = round(room_price / nights, 2) if nights > 0 else room_price

                # Rate plan (spec 2026-09-29 §2). Η ΚΥΡΙΑ στήλη
                # price_per_night_eur μένει ως έχει (συνέχεια ιστορικού)· η
                # σύγκριση/εμφάνιση χρησιμοποιεί τη discounted στήλη, που
                # χωρίς έκπτωση ισούται με την κύρια τιμή.
                raw_discount = opt.get("discount")
                if not isinstance(raw_discount, dict):
                    raw_discount = None  # ο actor στέλνει null χωρίς έκπτωση
                discounted_total = (
                    _safe_float(raw_discount.get("discountedPrice")) if raw_discount else 0.0
                )
                if discounted_total <= 0:
                    discounted_total = room_price
                discounted_per_night = (
                    round(discounted_total / nights, 2) if nights > 0 else discounted_total
                )
                # Έκπτωση μόνο με ΠΡΑΓΜΑΤΙΚΟ χάσμα τιμής: ένα discount object
                # με discountedPrice που λείπει/είναι μηδέν κατέληξε μόλις ίσο
                # με την κύρια τιμή, οπότε pct/label μένουν NULL — ποτέ chip
                # «-8%» χωρίς πραγματική διαφορά (review 2026-09-29).
                has_price_gap = discounted_total < room_price
                discount_text = (
                    raw_discount.get("text") if raw_discount and has_price_gap else None
                )
                # «- 8%» → 8.0· αλλιώς υπολογισμένο από τις δύο τιμές· αλλιώς NULL.
                discount_pct = _parse_discount_pct(discount_text)
                if discount_pct is None and has_price_gap:
                    discount_pct = round((1 - discounted_total / room_price) * 100, 1)

                records.append({
                    # [ID]
                    "record_id":           record_id,
                    "scraped_at":          now_str,
                    "check_in":            ci_str,
                    "check_out":           co_str,
                    # [DISPLAY]
                    "hotel_name":          hotel_name,
                    # Σταθερό property identity για per-hotel matching. Το
                    # εμφανιζόμενο όνομα δεν αρκεί (fallback/duplicates).
                    "hotel_url":           hotel_url,
                    "city":                meta.get("city", ""),
                    # Κενό (όχι "Unknown") όταν λείπει: το κενό γίνεται NULL στο
                    # normalization και το COALESCE του writer διατηρεί την
                    # υπάρχουσα διεύθυνση — τα cache-hit runs (scout_cache δεν
                    # έχει address) δεν πατάνε πια πραγματικές διευθύνσεις.
                    "address":             _fix_encoding(meta.get("address") or ""),
                    "property_type":       meta.get("property_type", "Hotel"),
                    "latitude":            meta.get("latitude", 0.0),
                    "longitude":           meta.get("longitude", 0.0),
                    "stars":               meta.get("stars", 0.0),
                    "review_score":        meta.get("review_score", 0.0),
                    "review_count":        meta.get("review_count", 0),
                    "price_per_night_eur": price_per_night,
                    # [FEATURE + TARGET]
                    "nights":              nights,
                    "guests":              guests,
                    # Πραγματικά occupancy πεδία: χωρίς αυτά ο normalizer
                    # μάντευε adults=guests/children=0/rooms=1 και τα price
                    # history/alerts queries (που φιλτράρουν στα ακριβή job
                    # values) γύριζαν για πάντα κενά για children>0 ή rooms>1.
                    "adults":              config.adults,
                    "children":            config.children,
                    "rooms":               config.rooms,
                    "max_persons":         max_persons,
                    "room_type":           room_name,
                    "room_type_category":  room_type_category,
                    "meals":               meals,
                    "free_cancellation":   cancellation,
                    # [RATE PLAN — spec 2026-09-29 §2]
                    "discounted_price_per_night_eur": discounted_per_night,
                    "discount_pct":        discount_pct,
                    "discount_label":      _clean_discount_label(discount_text),
                    "has_genius_discount": bool(opt.get("hasGeniusDiscount")),
                    # Το ωμό cancellationType (το Yes/No free_cancellation υπάρχει ήδη).
                    "cancellation_type":   _clean_text(opt.get("cancellationType")) or None,
                    "payment_label":       _detect_payment(choices),
                    # Ταυτότητα πλάνου: μπαίνει ΗΔΗ στο record_id, απλώς
                    # αποθηκεύεται και ως στήλη.
                    "rate_block_id":       str(package_id),
                    "price_total_eur":     room_price,
                    # [AGENT DATA]
                    "facilities":          facilities,
                    "rooms_left":          rooms_left,
                })

    log.info(
        "[ΣΤΑΔΙΟ 3] %d records | %d skipped (no options) | %d duplicates | %d zero-price αφαιρέθηκαν",
        len(records), skipped_rooms, duplicate_count, skipped_zero_price,
    )
    df = pd.DataFrame(records)

    if not df.empty:
        filled_coords  = (df["latitude"]  != 0).sum()
        filled_stars   = (df["stars"]     != 0).sum()
        filled_reviews = (df["review_score"] != 0).sum()
        log.info(
            "[ΣΤΑΔΙΟ 3] Metadata quality → lat/lng: %d/%d records | "
            "stars: %d/%d | review_score: %d/%d",
            filled_coords,  len(df),
            filled_stars,   len(df),
            filled_reviews, len(df),
        )
        if filled_coords == 0:
            log.warning(
                "[ΣΤΑΔΙΟ 3] ΠΡΟΣΟΧΗ: 0 records έχουν coordinates. "
                "Έλεγξε αν το Scout actor επιστρέφει latitude/longitude "
                "— ίσως χρειάζεσαι getDetails=true στο actor input."
            )

    return df


# ===========================================================
# ΑΠΟΘΗΚΕΥΣΗ
# ===========================================================
