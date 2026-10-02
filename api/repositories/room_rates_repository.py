from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Literal
from uuid import UUID

from api.services.destination_aliases import destination_variants
from api.services.market_service import RoomRateFilters
from api.services.room_rates_normalizer import comparable_room_type_categories

RoomRateSource = Literal["legacy", "normalized"]


SOURCE_RELATIONS: dict[RoomRateSource, str] = {
    "legacy": "room_rates",
    "normalized": "roomrate_latest_room_rates",
}

# Static filter fragments shared by the normalized read paths below. They are
# composed into SQL at import/definition time only (never from request input),
# the same security posture as price_history_repository's CTE constants.
#
# Exclude the account's own (active) property from competitor rows by name.
OWNED_PROPERTY_EXCLUSION_FILTER = """
              AND (
                :owned_property_id IS NULL
                OR NOT EXISTS (
                    SELECT 1
                    FROM roomrate_owned_properties op
                    WHERE op.account_id = :account_id
                      AND op.id = :owned_property_id
                      AND op.is_active = true
                      AND lower(trim(op.display_name)) = lower(trim(rates.hotel_name))
                )
              )
            """

# Optionally limit rows to the user's tracked competitor set for this scope.
# ``{tracked_category_predicate}`` is filled with either
# TRACKED_ROW_CATEGORY_PREDICATE below (a constant) or an IN list of
# ``:room_type_category_<i>`` placeholders whose NAMES are generated from the
# pool's own indexes. No request value is ever interpolated into SQL — the
# categories themselves travel as bound parameters.
SELECTED_COMPETITORS_FILTER_TEMPLATE = """
              AND (
                :selected_competitors_only = false
                OR EXISTS (
                    SELECT 1
                    FROM roomrate_tracked_competitors tc
                    WHERE tc.account_id = :account_id
                      AND tc.owned_property_id = :owned_property_id
                      AND {tracked_category_predicate}
                      AND tc.competitor_property_id = rates.property_id
                      AND tc.is_active = true
                )
              )
            """

# Without a requested category there is no pool to scope by, so each tracked
# row is matched against the rate row's own category (historical behavior).
TRACKED_ROW_CATEGORY_PREDICATE = "tc.room_type_category = rates.room_type_category"

# Round 6 (§3.6): single rooms never compete with a 2+ person listing, so
# they stay out of the include_similar read (MarketService labels the rest
# «ίδια κατηγορία» or «Παρόμοιο») and out of any read without a requested
# category. A constant, never request input.
NOT_SINGLE_ROOM_PREDICATE = "rates.room_type_category IS DISTINCT FROM 'single'"

# Rate plans (spec 2026-09-29 §4): the package columns migration 20260929_0025
# added. Only these constant column NAMES are ever composed into SQL — the
# same posture as the category placeholders above.
RATE_PLAN_COLUMNS: tuple[str, ...] = (
    "discounted_price_per_night_eur",
    "discount_pct",
    "discount_label",
    "has_genius_discount",
    "cancellation_type",
    "payment_label",
    "rate_block_id",
)

# Match the requested occupancy exactly when provided.
OCCUPANCY_FILTER = """
              AND (:adults IS NULL OR rates.adults = :adults)
              AND (:children IS NULL OR rates.children = :children)
              AND (:rooms IS NULL OR rates.rooms = :rooms)
            """

POPULAR_FACILITY_ALIASES: dict[str, tuple[str, ...]] = {
    "free wifi": ("wifi", "wi-fi", "internet"),
    "free parking": ("parking", "στάθμευση", "πάρκινγκ"),
    "swimming pool": ("pool", "swimming", "πισίνα", "πισίνες"),
    "non-smoking rooms": ("non-smoking", "non smoking", "μη καπνιστών"),
    "restaurant": ("restaurant", "εστιατόριο"),
    "family rooms": ("family", "οικογενειακά"),
    "tea/coffee maker": ("tea", "coffee", "καφέ", "τσάι", "καφετιέρα"),
    "bar": ("bar", "μπαρ"),
    "breakfast": ("breakfast", "πρωινό"),
    "balcony": ("balcony", "μπαλκόνι"),
    "sea view": ("sea view", "θέα στη θάλασσα"),
    "air conditioning": ("air conditioning", "κλιματισμός"),
}


class RoomRatesRepository:
    """PostgreSQL repository for legacy or normalized room-rate rows."""

    def __init__(
        self,
        connection_factory: Callable[[], AbstractContextManager[Any]],
        source: RoomRateSource = "legacy",
        account_id: UUID | str | None = None,
    ):
        self.connection_factory = connection_factory
        self.source = source
        self.account_id = str(account_id) if account_id else None

    @property
    def source_relation(self) -> str:
        """Return the hardcoded relation for the configured source."""
        return SOURCE_RELATIONS[self.source]

    @staticmethod
    def _build_amenities_filter(amenities: tuple[str, ...]) -> tuple[str, dict[str, str]]:
        """Build a parameterized SQL filter requiring every requested amenity."""
        if not amenities:
            return "", {}

        clauses: list[str] = []
        params: dict[str, str] = {}
        for index, amenity in enumerate(amenities):
            terms = POPULAR_FACILITY_ALIASES.get(amenity.strip().casefold(), (amenity,))
            term_clauses: list[str] = []
            for term_index, term in enumerate(terms):
                param_name = f"required_amenity_{index}_{term_index}"
                term_clauses.append(f"COALESCE(rates.facilities, '') ILIKE :{param_name}")
                params[param_name] = f"%{term}%"
            clauses.append("(" + " OR ".join(term_clauses) + ")")

        return "AND " + "\n              AND ".join(clauses), params

    def _base_params(self, filters: RoomRateFilters) -> dict[str, Any]:
        """Shared query parameters (str/isoformat casts in one place)."""
        return {
            "check_in": filters.check_in.isoformat() if filters.check_in else None,
            "check_out": filters.check_out.isoformat() if filters.check_out else None,
            "account_id": self.account_id,
            "adults": filters.adults,
            "children": filters.children,
            "rooms": filters.rooms,
            "owned_property_id": str(filters.owned_property_id) if filters.owned_property_id else None,
        }

    @staticmethod
    def _build_room_type_category_filter(
        room_type_category: str | None,
        include_similar: bool = False,
    ) -> tuple[str, str, dict[str, str]]:
        """Filter rate rows by category: the comparable pool, or everything but singles.

        Equality would drop the twin-bucketed «1 Διπλό ή 2 Μονά Κρεβάτια»
        rooms from a `double` search (and vice versa) even though they are the
        same sellable room; see ``comparable_room_type_categories``. With
        ``include_similar`` (Round 6) the rows are every stored category except
        single rooms, but the requested pool always stays in (a single-room
        owner still compares singles). Without a requested category single
        rooms stay out in BOTH modes, so ``include_similar=false`` can never
        return more rows than ``true``.

        Returns:
            The rate-row predicate, the bare ``:name`` placeholder list (the
            tracked-competitor scope reuses the same pool in both modes) and
            the bound parameters. Without a requested category the pool parts
            are empty. Only placeholder NAMES are ever composed into SQL; the
            categories themselves stay parameters.
        """
        params = {
            f"room_type_category_{index}": category
            for index, category in enumerate(comparable_room_type_categories(room_type_category))
        }
        if not params:
            return f"AND {NOT_SINGLE_ROOM_PREDICATE}", "", {}
        placeholders = ", ".join(f":{name}" for name in params)
        pool_predicate = f"rates.room_type_category IN ({placeholders})"
        if include_similar:
            return f"AND ({NOT_SINGLE_ROOM_PREDICATE} OR {pool_predicate})", placeholders, params
        return f"AND {pool_predicate}", placeholders, params

    @staticmethod
    def _build_selected_competitors_filter(category_placeholders: str) -> str:
        """Scope rows to the tracked competitor set for the requested pool.

        Tracked rows are keyed by the category the user searched under, so with
        a pooled read (a `double` search also returning twin rows) comparing
        ``tc.room_type_category`` to the ROW's category would hide precisely
        the cross-bucket competitors the pool exists to surface.
        """
        predicate = (
            f"tc.room_type_category IN ({category_placeholders})"
            if category_placeholders
            else TRACKED_ROW_CATEGORY_PREDICATE
        )
        # replace(), not format(): a literal { or } added to the template later
        # (a JSON operator, an array literal) would make str.format raise.
        return SELECTED_COMPETITORS_FILTER_TEMPLATE.replace(
            "{tracked_category_predicate}", predicate
        )

    @staticmethod
    def _build_destination_filter(destination: str | None) -> tuple[str, dict[str, str]]:
        """Build a destination filter that accepts known English/Greek aliases."""
        variants = destination_variants(destination)
        if not variants:
            return "1 = 1", {}

        clauses: list[str] = []
        params: dict[str, str] = {}
        for index, variant in enumerate(variants):
            param_name = f"destination_{index}"
            clauses.append(f":{param_name}")
            params[param_name] = variant

        return f"rates.city IN ({', '.join(clauses)})", params

    def fetch_room_rates(self, filters: RoomRateFilters) -> list[dict]:
        """Fetch room rate records with optional dashboard filters.

        Args:
            filters: Destination, date and limit filters.

        Returns:
            List of room rate rows as dictionaries.

        Raises:
            RuntimeError: If SQLAlchemy is not installed.
        """
        try:
            from sqlalchemy import text
        except ImportError as exc:
            raise RuntimeError("SQLAlchemy is required. Install dependencies from requirements.txt") from exc

        if self.source == "normalized" and filters.scrape_job_id:
            return self._fetch_normalized_scrape_job_room_rates(filters, text)

        account_select = "rates.account_id," if self.source == "normalized" else "NULL AS account_id,"
        identity_select = (
            "rates.property_id, rates.room_package_id, rates.room_type_category, rates.room_attributes,"
            if self.source == "normalized"
            else "NULL AS property_id, NULL AS room_package_id, NULL AS room_type_category, NULL AS room_attributes,"
        )
        # Round 6: roomrate_latest_room_rates predates properties.booking_url,
        # so the popup link is read from the property row by primary key.
        booking_url_select = (
            "(SELECT bp.booking_url FROM roomrate_properties bp WHERE bp.id = rates.property_id) AS booking_url,"
            if self.source == "normalized"
            else "NULL AS booking_url,"
        )
        # Rate plans: the view predates these columns too, so they come from
        # the package row by primary key (one LEFT JOIN, no view migration).
        rate_plan_select = (
            " ".join(f"plan.{column}," for column in RATE_PLAN_COLUMNS)
            if self.source == "normalized"
            else " ".join(f"NULL AS {column}," for column in RATE_PLAN_COLUMNS)
        )
        rate_plan_join = (
            "LEFT JOIN roomrate_room_packages plan ON plan.id = rates.room_package_id"
            if self.source == "normalized"
            else ""
        )
        # Cheapest EFFECTIVE price first (owner decision 2026-09-30), so a
        # LIMIT keeps the packages the service will price; legacy rows have
        # no discount column, so they keep the base order.
        price_order = (
            "COALESCE(plan.discounted_price_per_night_eur, rates.price_per_night_eur)"
            if self.source == "normalized"
            else "rates.price_per_night_eur"
        )
        account_filter = (
            "AND (:account_id IS NULL OR rates.account_id = :account_id)" if self.source == "normalized" else ""
        )
        category_filter, category_placeholders, category_params = self._build_room_type_category_filter(
            filters.room_type_category, filters.include_similar
        )
        room_type_filter = category_filter if self.source == "normalized" else ""
        owned_property_exclusion_filter = (
            OWNED_PROPERTY_EXCLUSION_FILTER if self.source == "normalized" else ""
        )
        selected_competitors_filter = (
            self._build_selected_competitors_filter(category_placeholders)
            if self.source == "normalized"
            else ""
        )
        occupancy_select = (
            "rates.adults, rates.children, rates.rooms,"
            if self.source == "normalized"
            else "NULL AS adults, NULL AS children, NULL AS rooms,"
        )
        occupancy_filter = OCCUPANCY_FILTER if self.source == "normalized" else ""
        destination_filter, destination_params = self._build_destination_filter(filters.destination)
        amenities_filter, amenity_params = self._build_amenities_filter(filters.amenities)
        sql = text(
            f"""
            SELECT
                {account_select}
                {occupancy_select}
                {identity_select}
                {booking_url_select}
                {rate_plan_select}
                rates.record_id,
                rates.scraped_at,
                rates.check_in,
                rates.check_out,
                rates.hotel_name,
                rates.city,
                rates.address,
                rates.property_type,
                rates.latitude,
                rates.longitude,
                rates.stars,
                rates.review_score,
                rates.review_count,
                rates.price_per_night_eur,
                rates.nights,
                rates.guests,
                rates.room_type,
                rates.meals,
                rates.free_cancellation,
                rates.price_total_eur,
                rates.facilities,
                rates.rooms_left
            FROM {self.source_relation} rates
            {rate_plan_join}
            WHERE {destination_filter}
              AND (:check_in IS NULL OR rates.check_in = :check_in)
              AND (:check_out IS NULL OR rates.check_out = :check_out)
              {account_filter}
              {occupancy_filter}
              {room_type_filter}
              {owned_property_exclusion_filter}
              {selected_competitors_filter}
              {amenities_filter}
            ORDER BY rates.scraped_at DESC, {price_order} ASC
            LIMIT :limit
            """
        )
        params = {
            **self._base_params(filters),
            "selected_competitors_only": filters.selected_competitors_only,
            "limit": filters.limit,
        }
        params.update(destination_params)
        params.update(amenity_params)
        if self.source == "normalized":
            params.update(category_params)
        with self.connection_factory() as connection:
            result = connection.execute(sql, params)
            return [dict(row) for row in result.mappings().all()]

    def _fetch_normalized_scrape_job_room_rates(self, filters: RoomRateFilters, text: Any) -> list[dict]:
        """Fetch rows from one completed scrape job instead of the latest market snapshot.

        Round 6: no ``city IN (aliases)`` filter here. The job defines the set,
        and hotels found by the nearby-area scouts are stored under their own
        city names (Ιξιά, Καλλιθέα), which a Faliraki city filter would hide.
        """
        amenities_filter, amenity_params = self._build_amenities_filter(filters.amenities)
        category_filter, category_placeholders, category_params = self._build_room_type_category_filter(
            filters.room_type_category, filters.include_similar
        )
        selected_competitors_filter = self._build_selected_competitors_filter(category_placeholders)
        # Rate plans (spec 2026-09-29 §4): this read selects from the package
        # table directly, so the plan columns come straight from ``rp``.
        rate_plan_cte_select = " ".join(f"rp.{column}," for column in RATE_PLAN_COLUMNS)
        rate_plan_outer_select = " ".join(f"rates.{column}," for column in RATE_PLAN_COLUMNS)
        sql = text(
            f"""
            WITH rates AS (
                SELECT
                    sr.account_id,
                    p.id AS property_id,
                    rp.id AS room_package_id,
                    rp.source_record_id AS record_id,
                    ro.observed_at AS scraped_at,
                    sr.check_in,
                    sr.check_out,
                    p.display_name AS hotel_name,
                    p.city,
                    p.address,
                    p.property_type,
                    p.latitude,
                    p.longitude,
                    p.stars,
                    p.booking_url,
                    ro.review_score,
                    ro.review_count,
                    rp.price_per_night_eur,
                    (sr.check_out - sr.check_in) AS nights,
                    (sr.adults + sr.children) AS guests,
                    sr.adults,
                    sr.children,
                    sr.rooms,
                    rp.room_type,
                    rp.room_type_category,
                    rp.room_attributes,
                    rp.meals,
                    rp.free_cancellation,
                    rp.price_total_eur,
                    COALESCE(p.amenities_cached, '') AS facilities,
                    {rate_plan_cte_select}
                    rp.rooms_left
                FROM roomrate_room_packages rp
                JOIN roomrate_rate_observations ro ON ro.id = rp.rate_observation_id
                JOIN roomrate_scrape_runs sr ON sr.id = ro.scrape_run_id
                JOIN roomrate_properties p ON p.id = ro.property_id
                WHERE sr.scrape_job_id = :scrape_job_id
                  AND sr.status = 'completed'
            )
            SELECT
                rates.account_id,
                rates.adults,
                rates.children,
                rates.rooms,
                rates.property_id,
                rates.room_package_id,
                rates.room_type_category,
                rates.room_attributes,
                rates.booking_url,
                rates.record_id,
                rates.scraped_at,
                rates.check_in,
                rates.check_out,
                rates.hotel_name,
                rates.city,
                rates.address,
                rates.property_type,
                rates.latitude,
                rates.longitude,
                rates.stars,
                rates.review_score,
                rates.review_count,
                rates.price_per_night_eur,
                rates.nights,
                rates.guests,
                rates.room_type,
                rates.meals,
                rates.free_cancellation,
                rates.price_total_eur,
                rates.facilities,
                {rate_plan_outer_select}
                rates.rooms_left
            FROM rates
            WHERE (:account_id IS NULL OR rates.account_id = :account_id)
              AND (:check_in IS NULL OR rates.check_in = :check_in)
              AND (:check_out IS NULL OR rates.check_out = :check_out)
              {OCCUPANCY_FILTER}
              {category_filter}
              {OWNED_PROPERTY_EXCLUSION_FILTER}
              {selected_competitors_filter}
              {amenities_filter}
            ORDER BY rates.scraped_at DESC,
                     COALESCE(rates.discounted_price_per_night_eur, rates.price_per_night_eur) ASC
            LIMIT :limit
            """
        )
        params = {
            **self._base_params(filters),
            "scrape_job_id": str(filters.scrape_job_id),
            "selected_competitors_only": filters.selected_competitors_only,
            "limit": filters.limit,
        }
        params.update(amenity_params)
        params.update(category_params)
        with self.connection_factory() as connection:
            result = connection.execute(sql, params)
            return [dict(row) for row in result.mappings().all()]

    def fetch_own_property_rates(
        self,
        account_id: UUID | str,
        scrape_job_id: UUID | str | None,
        display_name: str | None,
    ) -> list[dict]:
        """Return the owner's own rows in one scrape job, cheapest (effective) first.

        Exactly the rows ``OWNED_PROPERTY_EXCLUSION_FILTER`` removes from the
        competitor reads (the same trimmed, case-insensitive name equality),
        with no category filter: the service picks the cheapest same-category
        package and falls back to any category (Round 6 «Εσείς» marker).

        Returns [] without querying when there is no job, no name, or no
        normalized data to read.
        """
        name = (display_name or "").strip()
        if self.source != "normalized" or not scrape_job_id or not name:
            return []
        from sqlalchemy import text

        sql = text(
            """
            SELECT
                p.id AS property_id,
                p.display_name AS hotel_name,
                p.latitude,
                p.longitude,
                p.booking_url,
                rp.id AS room_package_id,
                rp.room_type,
                rp.room_type_category,
                rp.price_per_night_eur,
                rp.discounted_price_per_night_eur,
                rp.price_total_eur
            FROM roomrate_room_packages rp
            JOIN roomrate_rate_observations ro ON ro.id = rp.rate_observation_id
            JOIN roomrate_scrape_runs sr ON sr.id = ro.scrape_run_id
            JOIN roomrate_properties p ON p.id = ro.property_id
            WHERE sr.scrape_job_id = :scrape_job_id
              AND sr.account_id = :account_id
              AND sr.status = 'completed'
              AND lower(trim(p.display_name)) = lower(trim(:display_name))
            ORDER BY COALESCE(rp.discounted_price_per_night_eur, rp.price_per_night_eur) ASC
            """
        )
        params = {
            "account_id": str(account_id),
            "scrape_job_id": str(scrape_job_id),
            "display_name": name,
        }
        with self.connection_factory() as connection:
            result = connection.execute(sql, params)
            return [dict(row) for row in result.mappings().all()]

    def fetch_available_amenities(self, filters: RoomRateFilters) -> list[str]:
        """Fetch dynamic amenity options for the current market filter scope."""
        try:
            from sqlalchemy import text
        except ImportError as exc:
            raise RuntimeError("SQLAlchemy is required. Install dependencies from requirements.txt") from exc

        account_filter = (
            "AND (:account_id IS NULL OR rates.account_id = :account_id)" if self.source == "normalized" else ""
        )
        category_filter, _, category_params = self._build_room_type_category_filter(
            filters.room_type_category, filters.include_similar
        )
        room_type_filter = category_filter if self.source == "normalized" else ""
        owned_property_exclusion_filter = (
            OWNED_PROPERTY_EXCLUSION_FILTER if self.source == "normalized" else ""
        )
        occupancy_filter = OCCUPANCY_FILTER if self.source == "normalized" else ""
        destination_filter, destination_params = self._build_destination_filter(filters.destination)
        sql = text(
            f"""
            SELECT amenity
            FROM (
                SELECT DISTINCT
                    -- Facilities are pipe-joined everywhere (view string_agg,
                    -- amenities_cached refresh, scraper _normalize_facilities);
                    -- translate also folds legacy ','/';' separators to '|'.
                    trim(lower(unnest(string_to_array(translate(COALESCE(rates.facilities, ''), ',;', '||'), '|')))) AS amenity
                FROM {self.source_relation} rates
                WHERE {destination_filter}
                  AND (:check_in IS NULL OR rates.check_in = :check_in)
                  AND (:check_out IS NULL OR rates.check_out = :check_out)
                  {account_filter}
                  {occupancy_filter}
                  {room_type_filter}
                  {owned_property_exclusion_filter}
            ) amenity_options
            WHERE amenity <> ''
            ORDER BY amenity ASC
            LIMIT :limit
            """
        )
        params = {
            **self._base_params(filters),
            "limit": min(filters.limit, 200),
        }
        params.update(destination_params)
        if self.source == "normalized":
            params.update(category_params)
        with self.connection_factory() as connection:
            result = connection.execute(sql, params)
            return [str(row["amenity"]) for row in result.mappings().all()]
