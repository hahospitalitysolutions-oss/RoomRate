"""Validated runtime configuration for Booking.com scraping."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ScraperConfig:
    account_id: uuid.UUID      = uuid.UUID("00000000-0000-0000-0000-000000000001")
    scrape_job_id: uuid.UUID | None = None
    destination: str          = "Φαληράκι"
    check_in: datetime        = field(default_factory=lambda: datetime(2026, 6, 15))
    check_out: datetime       = field(default_factory=lambda: datetime(2026, 6, 20))
    scout_max_items: int      = 10    # max ξενοδοχεία από Stage 1
    deep_crawl_max_items: int = 100   # max πακέτα ανά ξενοδοχείο
    rooms: int                = 1
    adults: int               = 2
    children: int             = 0
    currency: str             = "EUR"
    language: str             = "el"
    max_retries: int          = 3
    retry_delay_sec: int      = 5
    output_csv: str           = "roomrate_data.csv"
    error_log_csv: str        = "logs/error_log.csv"
    provider: str             = "booking_com"
    job_type: str             = "competitor_search"
    room_type_category: str | None = None
    room_name_query: str | None = None
    required_meal: str | None = None
    required_free_cancellation: str | None = None
    required_amenities: tuple[str, ...] = ()
    target_urls: tuple[str, ...] = ()
    dry_run: bool             = False
    write_normalized: bool    = True

    # --- Round 6: γειτονικές περιοχές, ακτίνα, όριο καταλυμάτων ---
    # Ο actor δεν δέχεται ακτίνα: κάθε γειτονική περιοχή γίνεται δικό της
    # scout (nearby_max_items το καθένα) και η ακτίνα εφαρμόζεται μετά,
    # με haversine από το κατάλυμα του ιδιοκτήτη (origin).
    nearby_destinations: tuple[str, ...] = ()
    nearby_max_items: int = 20
    origin_lat: float | None = None
    origin_lng: float | None = None
    radius_km: float | None = None          # None = χωρίς φίλτρο απόστασης
    deep_crawl_max_hotels: int | None = None  # None = χωρίς περικοπή

    # --- Αποδοτικότητα ---

    # Scout cache: αν τρέξεις ξανά εντός X ωρών για τον ίδιο προορισμό
    # & dates, παραλείπει το Stage 1 (0 Apify credits).
    # Βάλε scout_cache_hours=0 για να αναγκάσεις fresh scout.
    scout_cache_hours: int    = 24

    # Parallel Deep Crawl: πόσα batches τρέχουν ταυτόχρονα.
    # workers=1 → σειριακό (παλιά συμπεριφορά, πιο ασφαλές για Apify free plan)
    # workers=3 → 3x γρηγορότερο (χρειάζεται Apify plan με concurrency >= 3)
    deep_crawl_workers: int   = 3

    # Πόσα ξενοδοχεία ανά batch. Round 6: 8 (ήταν 4) — με 120 ξενοδοχεία και
    # 3 workers, 15 batches αντί για 30 Apify runs.
    deep_crawl_batch_size: int = 8

    # Production default: ένα ελλιπές market snapshot δεν πρέπει να
    # χρησιμοποιηθεί για pricing recommendations σαν να ήταν πλήρες.
    allow_partial_batches: bool = False

    def __post_init__(self) -> None:
        """Validate operational limits before any paid actor call is made.

        Raises:
            ValueError: If dates, occupancy, retry or concurrency limits are invalid.
        """
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        positive_fields = {
            "scout_max_items": self.scout_max_items,
            "deep_crawl_max_items": self.deep_crawl_max_items,
            "rooms": self.rooms,
            "adults": self.adults,
            "max_retries": self.max_retries,
            "retry_delay_sec": self.retry_delay_sec,
            "deep_crawl_workers": self.deep_crawl_workers,
            "deep_crawl_batch_size": self.deep_crawl_batch_size,
            "nearby_max_items": self.nearby_max_items,
        }
        invalid = [name for name, value in positive_fields.items() if value < 1]
        if invalid:
            raise ValueError(f"Expected positive values for: {', '.join(invalid)}")
        if self.children < 0:
            raise ValueError("children must be >= 0")
        if self.scout_cache_hours < 0:
            raise ValueError("scout_cache_hours must be >= 0")
        # Half an origin cannot centre a radius; reject it instead of silently
        # scouting without one.
        if (self.origin_lat is None) != (self.origin_lng is None):
            raise ValueError("origin_lat and origin_lng must be given together")
        if self.radius_km is not None and self.radius_km <= 0:
            raise ValueError("radius_km must be > 0")
        if self.deep_crawl_max_hotels is not None and self.deep_crawl_max_hotels < 1:
            raise ValueError("deep_crawl_max_hotels must be >= 1")

    @property
    def radius_active(self) -> bool:
        """True only with a radius AND a full origin; otherwise the scout keeps every hotel."""
        return self.radius_km is not None and self.origin_lat is not None and self.origin_lng is not None
