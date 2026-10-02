from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date
from typing import Any
from uuid import UUID

from api.services.market_helpers import as_optional_float
from api.services.room_rates_normalizer import comparable_room_type_categories

# The EFFECTIVE nightly price of a package (owner decision 2026-09-30): what
# an anonymous guest actually pays — the discounted price (e.g. «-8% πληρωμή
# online») when Booking showed one, else the base price. The discount column
# is NULL on rows written before migration 0025 and on packages without a
# discount, so COALESCE falls back to base. Statistics, the chart, the advisor
# and the owner's live reference all read this; market_helpers'
# ``effective_price_per_night`` is its Python twin for the map read side.
EFFECTIVE_PRICE_SQL = "COALESCE(rp.discounted_price_per_night_eur, rp.price_per_night_eur)"
# The BASE price: only the price-change alerts keep comparing it.
BASE_PRICE_SQL = "rp.price_per_night_eur"
# Per (run, hotel): 1 when any package carried a discounted price, i.e. when
# the effective price could differ from the base one. Runs written before
# migration 0025 are all 0, so the trends never pair them with newer runs
# priced off discounts (a false «drop» of 5-15%). ``discount_pct`` alone does
# not change the effective price, so the discounted price is the signal.
_HAS_DISCOUNT_DATA_SQL = (
    "MAX(CASE WHEN rp.discounted_price_per_night_eur IS NOT NULL THEN 1 ELSE 0 END)"
)

# How many logical runs the alert comparison may look back through when a
# minimum gap is requested. Scheduled scrapes can run every few minutes, so the
# run that is 12 hours old is rarely rn = 2.
_ALERT_HISTORY_WINDOW_RUNS = 200

# Completed runs of one market key ranked into LOGICAL runs, newest first
# (rn = 1). One job can write one run per Booking city, all stamped with the
# same finished_at: dense_rank gives those fragments the same rank, so every
# read below treats them as one run. Runs without finished_at are excluded
# explicitly because PostgreSQL sorts NULLs first under DESC.
#
# canonical_destination is NOT NULL, so it is compared directly (no COALESCE)
# and the planner can use ix_roomrate_scrape_runs_history.
_RANKED_RUNS_SQL = """
            SELECT
                sr.id,
                sr.finished_at AS observed_at,
                dense_rank() OVER (ORDER BY sr.finished_at DESC) AS rn
            FROM roomrate_scrape_runs sr
            WHERE sr.account_id = :account_id
              AND sr.canonical_destination = :canonical_destination
              AND sr.check_in = :check_in
              AND sr.check_out = :check_out
              AND sr.adults = :adults
              AND sr.children = :children
              AND sr.rooms = :rooms
              AND sr.status = 'completed'
              AND sr.finished_at IS NOT NULL
"""

# The account's own (active) properties never count as competitors: not in
# the statistics, not in the /market/price-history series, not in the alerts,
# because every read goes through the CTE below. `display_name` mirrors the
# exclusion that already works live in room_rates_repository; `canonical_name`
# is the canonicalized spelling the writer stores next to it.
_OWN_PROPERTY_EXCLUSION = """
          AND NOT EXISTS (
              SELECT 1
              FROM roomrate_owned_properties op
              WHERE op.account_id = :account_id
                AND op.is_active = true
                AND lower(trim(op.display_name)) IN (
                    lower(trim(p.display_name)),
                    lower(trim(p.canonical_name))
                )
          )
"""

# Shared CTE: rank completed scrape runs for one market into logical runs
# (newest first), then fold each logical run down to the cheapest offer per
# property — across all of its fragments. Snapshots accumulate one row per
# (run, property) so price history falls out of existing tables.
#
# Per (run, property) two minimums come back: ``min_price_same`` over the
# packages in the baseline category's comparable pool and ``min_price_similar``
# over every other category except ``single`` (Round 6 §5.2). ``min_price``
# keeps its historical meaning (= the same-category minimum) for callers that
# predate the split. Without a baseline category every package is "same".
# Every minimum is over EFFECTIVE_PRICE_SQL, except the alerts' BASE_PRICE_SQL.
#
# Like-for-like (spec 2026-09-29 §4): with an own cancellation class, each
# minimum is computed FIRST over the packages in that class — same
# ``cancellation_type``, with NULL/unknown always participating — and falls
# back to the hotel's overall minimum when the hotel has no row in the class
# set. ``cancellation_matched_same`` / ``cancellation_matched_similar`` say
# whether the hotel's SAME-pool (respectively similar, non-single) packages
# include a row whose class EQUALS the own one — NULL participation alone does
# not count. One flag per basis class, so _stats_scope can decide AFTER the
# widening decision which rows may label the comparison "matched": a match in
# a similar-only hotel that widening never used must not flip the label. The
# min and matched expressions come from _build_cancellation_exprs; without a
# class they are byte-identical to the pre-existing behaviour.
#
# ``hotel_name`` is the name as Booking shows it («Evita Resort»), not the
# lowercased ``canonical_name`` matching key. Nothing downstream keys on it
# (series group by run, alerts dedupe on property_id), and it is grouped next
# to the property id so one property never splits into two series.
_PRICES_CTE_TEMPLATE = """
    WITH {runs_ctes}
    prices AS (
        SELECT
            rr.rn,
            rr.observed_at,
            ro.property_id,
            p.display_name AS hotel_name,
            {min_price_same_expr} AS min_price_same,
            {min_price_similar_expr} AS min_price_similar,
            {min_price_same_expr} AS min_price,
            {cancellation_matched_same_expr} AS cancellation_matched_same,
            {cancellation_matched_similar_expr} AS cancellation_matched_similar,
            {agent_basis_expr} AS agent_basis,
            {has_discount_data_expr} AS has_discount_data
        FROM ranked_runs rr
        JOIN roomrate_rate_observations ro ON ro.scrape_run_id = rr.id
        JOIN roomrate_room_packages rp ON rp.rate_observation_id = ro.id
        JOIN roomrate_properties p ON p.id = ro.property_id{agent_match_join}
        WHERE rp.price_per_night_eur IS NOT NULL
          {property_filter}
          {own_property_exclusion}
        GROUP BY rr.rn, rr.observed_at, ro.property_id, p.display_name
    )
"""

# Category basis (today's): the logical runs, nothing else.
_CATEGORY_RUNS_CTES = """ranked_runs AS (
        SELECT runs.id, runs.observed_at, runs.rn
        FROM ({ranked_runs}) runs
        WHERE runs.rn <= :limit_runs
    ),"""

# Agent basis (owner decision 2026-09-30): the comparison set is chosen by the
# room-matching agent alone. A logical run whose scrape job has agent rows for
# the requested owned room type prices every hotel over its agent-comparable
# rooms only; every other run keeps the category pool above. The flag is per
# LOGICAL run (MAX over its fragments), so one run never mixes both bases.
_AGENT_RANKED_RUNS_SQL = _RANKED_RUNS_SQL.replace(
    "sr.finished_at AS observed_at,",
    "sr.finished_at AS observed_at, sr.scrape_job_id,",
)
_AGENT_RUNS_CTES = """ranked_fragments AS (
        SELECT
            runs.id,
            runs.observed_at,
            runs.rn,
            runs.scrape_job_id,
            CASE WHEN EXISTS (
                SELECT 1
                FROM roomrate_room_matches rm_any
                WHERE rm_any.account_id = :account_id
                  AND rm_any.scrape_job_id = runs.scrape_job_id
                  AND rm_any.owned_room_type_id = :owned_room_type_id
            ) THEN 1 ELSE 0 END AS has_agent_matches
        FROM ({ranked_runs}) runs
        WHERE runs.rn <= :limit_runs
    ),
    ranked_runs AS (
        SELECT
            rf.id,
            rf.observed_at,
            rf.rn,
            rf.scrape_job_id,
            MAX(rf.has_agent_matches) OVER (PARTITION BY rf.rn) AS agent_basis
        FROM ranked_fragments rf
    ),"""

# The unique scope (job, owned room, property, room_type) makes this join
# row-preserving: at most one agent row per package, matched on the exact
# stored room name. Only agent-basis runs join at all.
_AGENT_MATCH_JOIN = """
        LEFT JOIN roomrate_room_matches rm
          ON rr.agent_basis = 1
         AND rm.account_id = :account_id
         AND rm.scrape_job_id = rr.scrape_job_id
         AND rm.owned_room_type_id = :owned_room_type_id
         AND rm.property_id = ro.property_id
         AND rm.room_type = rp.room_type"""

# EFFECTIVE comparable (shared contract): the agent's verdict when set, else
# score >= 50 (rows written before migration 0027). A package the agent did
# not score has no row, so the whole expression is NULL: not comparable.
_AGENT_COMPARABLE = "COALESCE(rm.comparable, rm.score >= 50)"

# One deterministic id for the latest logical run: the largest id among the
# fragments that share the latest finished_at (the recommendation cache key).
_LATEST_RUN_ID_SQL = """
            SELECT sr.id
            FROM roomrate_scrape_runs sr
            WHERE sr.account_id = :account_id
              AND sr.canonical_destination = :canonical_destination
              AND sr.check_in = :check_in
              AND sr.check_out = :check_out
              AND sr.adults = :adults
              AND sr.children = :children
              AND sr.rooms = :rooms
              AND sr.status = 'completed'
              AND sr.finished_at IS NOT NULL
            ORDER BY sr.finished_at DESC, sr.id DESC
            LIMIT 1
"""


class PriceHistoryRepository:
    """PostgreSQL repository for per-market price history built from scrape runs."""

    def __init__(self, connection_factory: Callable[[], AbstractContextManager[Any]]):
        self.connection_factory = connection_factory

    def fetch_price_series(
        self,
        account_id: UUID | str,
        canonical_destination: str,
        check_in: date,
        check_out: date,
        adults: int,
        children: int,
        rooms: int,
        room_type_category: str | None = None,
        property_ids: list[UUID | str] | None = None,
        limit_runs: int = 60,
        include_similar: bool = False,
        cancellation_type: str | None = None,
        owned_room_type_id: UUID | str | None = None,
    ) -> list[dict]:
        """Fetch the cheapest EFFECTIVE nightly price per property for recent runs.

        Returns one row per (run, property), newest run first (rn = 1), with
        ``min_price_same``, ``min_price_similar`` and the legacy ``min_price``
        (= ``min_price_same``). Hotels that only offer similar categories are
        returned only when ``include_similar`` is True.

        With ``cancellation_type`` (the own reference package's class, spec
        2026-09-29 §4) each minimum is computed like-for-like: FIRST over the
        hotel's packages in that class (NULL/unknown always participates),
        else its overall minimum as before. ``cancellation_matched_same`` /
        ``cancellation_matched_similar`` then flag hotels whose same-pool
        (respectively similar) packages include a row whose class equals the
        own one; without a class the minimums are exactly the pre-existing
        ones.

        With ``owned_room_type_id`` a run whose scrape job has room-matching
        agent rows for that owned room switches to the agent basis: each
        hotel's ``min_price_same`` is its cheapest package among its
        agent-comparable rooms, ``min_price_similar`` is NULL (no widening)
        and ``agent_basis`` is 1. Runs without agent rows keep the category
        pool (``agent_basis`` 0).

        ``has_discount_data`` is 1 when any of the hotel's packages in that
        run carried a discounted price (never before migration 0025): with
        ``agent_basis`` it tells the statistics which runs were priced the
        same way, so a trend only pairs those.
        """
        same_filter, category_params = self._build_category_filter(room_type_category)
        prices_cte, cancellation_params = self._prices_cte(
            property_ids, same_filter, cancellation_type, owned_room_type_id
        )
        sql = (
            prices_cte
            + f"""
            SELECT rn, observed_at, property_id, hotel_name,
                   min_price, min_price_same, min_price_similar,
                   cancellation_matched_same, cancellation_matched_similar,
                   agent_basis, has_discount_data
            FROM prices
            WHERE {self._scope_filter(include_similar)}
            ORDER BY rn ASC, property_id ASC
            """
        )
        params = self._build_params(
            account_id=account_id,
            canonical_destination=canonical_destination,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            children=children,
            rooms=rooms,
            category_params=category_params,
            property_ids=property_ids,
            limit_runs=limit_runs,
        )
        params.update(cancellation_params)
        params.update(self._agent_params(owned_room_type_id))
        return self._execute(sql, params)

    def fetch_latest_vs_previous(
        self,
        account_id: UUID | str,
        canonical_destination: str,
        check_in: date,
        check_out: date,
        adults: int,
        children: int,
        rooms: int,
        room_type_category: str | None = None,
        property_ids: list[UUID | str] | None = None,
        min_gap_hours: float | None = None,
        owned_room_type_id: UUID | str | None = None,
    ) -> list[dict]:
        """Compare each property's cheapest same-category price with a previous run.

        Without ``min_gap_hours`` the previous run is the immediately preceding
        one. With it, the previous run is the most recent one that finished at
        least that many hours before the latest run; when no such run exists
        ``previous_price`` is NULL and the caller raises no alert (Round 6:
        a rerun three minutes later is the same market moment, not a change).

        ``owned_room_type_id`` enables the agent basis exactly as in
        ``fetch_price_series``. A previous run on a DIFFERENT basis never
        pairs up (``previous_price`` NULL): a switch from the category pool to
        the agent's comparable rooms is not a market price change.
        """
        same_filter, category_params = self._build_category_filter(room_type_category)
        if min_gap_hours is None:
            previous_run_predicate = "prev.rn = 2"
            limit_runs = 2  # only the latest and previous runs matter here
        else:
            # MAX, not a bare scalar subquery: every fragment of the latest
            # logical run has rn = 1, and more than one row would raise.
            previous_run_predicate = """prev.rn = (
                    SELECT MIN(older.rn)
                    FROM ranked_runs older
                    WHERE older.rn > 1
                      AND older.observed_at <= (
                          (SELECT MAX(latest.observed_at) FROM ranked_runs latest WHERE latest.rn = 1)
                          - (:min_gap_hours * interval '1 hour')
                      )
                )"""
            limit_runs = _ALERT_HISTORY_WINDOW_RUNS
        # No cancellation class here: alert evaluation has no own reference
        # package, so its minimums keep today's meaning.
        same_basis_predicate = (
            "\n               AND prev.agent_basis = cur.agent_basis"
            if owned_room_type_id
            else ""
        )
        # BASE prices: older runs lack discount data, so effective-vs-base would raise false «Πτώση τιμής».
        prices_cte, _ = self._prices_cte(
            property_ids,
            same_filter,
            owned_room_type_id=owned_room_type_id,
            price_expr=BASE_PRICE_SQL,
        )
        sql = (
            prices_cte
            + f"""
            SELECT
                cur.property_id,
                cur.hotel_name,
                cur.min_price AS current_price,
                prev.min_price AS previous_price,
                cur.observed_at,
                prev.observed_at AS previous_observed_at
            FROM prices cur
            LEFT JOIN prices prev
                ON prev.property_id = cur.property_id
               AND {previous_run_predicate}{same_basis_predicate}
            WHERE cur.rn = 1
              AND cur.min_price_same IS NOT NULL
            ORDER BY cur.property_id ASC
            """
        )
        params = self._build_params(
            account_id=account_id,
            canonical_destination=canonical_destination,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            children=children,
            rooms=rooms,
            category_params=category_params,
            property_ids=property_ids,
            limit_runs=limit_runs,
        )
        if min_gap_hours is not None:
            params["min_gap_hours"] = float(min_gap_hours)
        params.update(self._agent_params(owned_room_type_id))
        return self._execute(sql, params)

    def fetch_latest_completed_run_id(
        self,
        account_id: UUID | str,
        canonical_destination: str,
        check_in: date,
        check_out: date,
        adults: int,
        children: int,
        rooms: int,
    ) -> str | None:
        """Return the id of the newest completed run for one market key, or None.

        The agents router folds it into the recommendation cache key so a new
        scrape yields a new recommendation inside the cache window.
        """
        rows = self._execute(
            _LATEST_RUN_ID_SQL,
            self._market_key_params(
                account_id, canonical_destination, check_in, check_out, adults, children, rooms
            ),
        )
        if not rows:
            return None
        run_id = rows[0].get("id")
        return str(run_id) if run_id is not None else None

    def fetch_agent_comparable_rooms(
        self,
        account_id: UUID | str,
        canonical_destination: str,
        check_in: date,
        check_out: date,
        adults: int,
        children: int,
        rooms: int,
        owned_room_type_id: UUID | str,
    ) -> set[tuple[str, str]]:
        """Agent-comparable (property_id, casefolded room_type) pairs of the latest run.

        The price advisor filters its competitor rows through this set so the
        model reads the same comparison basis as the statistics. An empty set
        means the latest run's job has no comparable agent row: callers keep
        the category basis.
        """
        sql = f"""
            WITH latest_jobs AS (
                SELECT DISTINCT runs.scrape_job_id
                FROM ({_AGENT_RANKED_RUNS_SQL}) runs
                WHERE runs.rn = 1
            )
            SELECT rm.property_id, rm.room_type
            FROM roomrate_room_matches rm
            JOIN latest_jobs lj ON lj.scrape_job_id = rm.scrape_job_id
            WHERE rm.account_id = :account_id
              AND rm.owned_room_type_id = :owned_room_type_id
              AND {_AGENT_COMPARABLE}
        """
        params = {
            **self._market_key_params(
                account_id, canonical_destination, check_in, check_out, adults, children, rooms
            ),
            **self._agent_params(owned_room_type_id),
        }
        return {
            (str(row["property_id"]), str(row["room_type"] or "").strip().casefold())
            for row in self._execute(sql, params)
        }

    def fetch_own_live_price(
        self,
        account_id: UUID | str,
        canonical_destination: str,
        check_in: date,
        check_out: date,
        adults: int,
        children: int,
        rooms: int,
        room_type_category: str | None,
        display_name: str | None,
    ) -> dict | None:
        """The owner's OWN reference package in the latest completed run.

        Looks across every fragment of the latest logical run (the own hotel
        sits in whichever Booking city returned it). The reference is the
        cheapest package inside the baseline category's comparable pool, else
        the cheapest package of any category — a ROW, not an aggregate, so
        its ``cancellation_type`` can scope the like-for-like minimums (spec
        2026-09-29 §4). The price is the EFFECTIVE one, like-for-like with the
        competitors' minimums. Returns ``{"price": float, "cancellation_type":
        str | None}``, or None when the market key has no completed run or
        Booking did not return the property.
        """
        name = (display_name or "").strip()
        if not name:
            return None
        same_filter, category_params = self._build_category_filter(room_type_category)
        sql = f"""
            WITH latest_runs AS (
                SELECT runs.id
                FROM ({_RANKED_RUNS_SQL}) runs
                WHERE runs.rn = 1
            )
            SELECT
                {EFFECTIVE_PRICE_SQL} AS price,
                rp.cancellation_type,
                CASE WHEN {same_filter} THEN 0 ELSE 1 END AS pool_rank
            FROM latest_runs lr
            JOIN roomrate_rate_observations ro ON ro.scrape_run_id = lr.id
            JOIN roomrate_room_packages rp ON rp.rate_observation_id = ro.id
            JOIN roomrate_properties p ON p.id = ro.property_id
            WHERE rp.price_per_night_eur IS NOT NULL
              AND lower(trim(:display_name)) IN (
                  lower(trim(p.display_name)),
                  lower(trim(p.canonical_name))
              )
            ORDER BY pool_rank ASC, {EFFECTIVE_PRICE_SQL} ASC, rp.id ASC
            LIMIT 1
        """
        params = {
            **self._market_key_params(
                account_id, canonical_destination, check_in, check_out, adults, children, rooms
            ),
            **category_params,
            "display_name": name,
        }
        rows = self._execute(sql, params)
        if not rows:
            return None
        price = as_optional_float(rows[0].get("price"))
        if price is None:
            return None
        cancellation_type = rows[0].get("cancellation_type")
        return {
            "price": price,
            "cancellation_type": str(cancellation_type) if cancellation_type else None,
        }

    @staticmethod
    def _build_cancellation_exprs(
        same_filter: str,
        cancellation_type: str | None,
        similar_condition: str | None = None,
        price_expr: str = EFFECTIVE_PRICE_SQL,
    ) -> tuple[str, str, str, str, dict[str, str]]:
        """The two MIN expressions and the per-basis matched flags for the CTE.

        ``price_expr`` is the per-package price the minimums fold: the
        EFFECTIVE price by default, the BASE price for the alerts only.

        The matched flags are split per basis class (review fix): the same
        flag looks at same-pool rows only and the similar flag at the
        non-single rows outside the pool, so _stats_scope can count exactly
        the rows that form the statistical basis after its widening decision.
        Composed from trusted fragments only; the class itself always travels
        as the ``:own_cancellation_class`` bound parameter. Portable SQL: the
        SQLite repository tests run these expressions verbatim, so no
        BOOL_OR/FILTER — MAX over a 0/1 CASE plays that role.
        """
        if similar_condition is None:
            similar_condition = (
                f"NOT ({same_filter}) AND rp.room_type_category IS DISTINCT FROM 'single'"
            )
        if not cancellation_type:
            return (
                f"MIN(CASE WHEN {same_filter} THEN {price_expr} END)",
                f"MIN(CASE WHEN {similar_condition} THEN {price_expr} END)",
                "FALSE",
                "FALSE",
                {},
            )
        participates = (
            "(rp.cancellation_type IS NULL OR rp.cancellation_type = :own_cancellation_class)"
        )
        same_expr = (
            f"COALESCE(MIN(CASE WHEN {same_filter} AND {participates} THEN {price_expr} END), "
            f"MIN(CASE WHEN {same_filter} THEN {price_expr} END))"
        )
        similar_expr = (
            f"COALESCE(MIN(CASE WHEN {similar_condition} AND {participates} THEN {price_expr} END), "
            f"MIN(CASE WHEN {similar_condition} THEN {price_expr} END))"
        )
        matched_same_expr = (
            f"(MAX(CASE WHEN {same_filter}"
            " AND rp.cancellation_type = :own_cancellation_class THEN 1 ELSE 0 END) = 1)"
        )
        matched_similar_expr = (
            f"(MAX(CASE WHEN {similar_condition}"
            " AND rp.cancellation_type = :own_cancellation_class THEN 1 ELSE 0 END) = 1)"
        )
        return (
            same_expr,
            similar_expr,
            matched_same_expr,
            matched_similar_expr,
            {"own_cancellation_class": cancellation_type},
        )

    @classmethod
    def _prices_cte(
        cls,
        property_ids: list[UUID | str] | None,
        same_filter: str,
        cancellation_type: str | None = None,
        owned_room_type_id: UUID | str | None = None,
        price_expr: str = EFFECTIVE_PRICE_SQL,
    ) -> tuple[str, dict[str, str]]:
        """Build the CTE from trusted predicate fragments only.

        Without ``owned_room_type_id`` the SQL is the category basis exactly
        as before. With it, the same/similar predicates become per-run: an
        agent-basis run's "same" pool is its agent-comparable rooms and it
        has no similar pool; a category run keeps ``same_filter``.
        ``price_expr`` is one of the two module constants, never input.
        """
        property_filter = (
            "AND ro.property_id = ANY(CAST(:property_ids AS uuid[]))"
            if property_ids
            else ""
        )
        similar_condition = None
        if owned_room_type_id:
            runs_ctes = _AGENT_RUNS_CTES.format(ranked_runs=_AGENT_RANKED_RUNS_SQL)
            agent_match_join = _AGENT_MATCH_JOIN
            agent_basis_expr = "MAX(rr.agent_basis)"
            similar_condition = (
                f"rr.agent_basis = 0 AND NOT ({same_filter})"
                " AND rp.room_type_category IS DISTINCT FROM 'single'"
            )
            same_filter = (
                f"((rr.agent_basis = 1 AND {_AGENT_COMPARABLE})"
                f" OR (rr.agent_basis = 0 AND {same_filter}))"
            )
        else:
            runs_ctes = _CATEGORY_RUNS_CTES.format(ranked_runs=_RANKED_RUNS_SQL)
            agent_match_join = ""
            agent_basis_expr = "0"
        (
            same_expr,
            similar_expr,
            matched_same_expr,
            matched_similar_expr,
            cancellation_params,
        ) = cls._build_cancellation_exprs(
            same_filter, cancellation_type, similar_condition, price_expr
        )
        # The discount flag describes effective prices; the base-price alerts
        # never read discount columns.
        has_discount_data_expr = (
            _HAS_DISCOUNT_DATA_SQL if price_expr == EFFECTIVE_PRICE_SQL else "0"
        )
        sql = _PRICES_CTE_TEMPLATE.format(
            runs_ctes=runs_ctes,
            agent_match_join=agent_match_join,
            agent_basis_expr=agent_basis_expr,
            has_discount_data_expr=has_discount_data_expr,
            min_price_same_expr=same_expr,
            min_price_similar_expr=similar_expr,
            cancellation_matched_same_expr=matched_same_expr,
            cancellation_matched_similar_expr=matched_similar_expr,
            property_filter=property_filter,
            own_property_exclusion=_OWN_PROPERTY_EXCLUSION,
        )
        return sql, cancellation_params

    @staticmethod
    def _agent_params(owned_room_type_id: UUID | str | None) -> dict[str, str]:
        """The owned room type as a bound parameter, only in agent mode."""
        if not owned_room_type_id:
            return {}
        return {"owned_room_type_id": str(owned_room_type_id)}

    @staticmethod
    def _scope_filter(include_similar: bool) -> str:
        """Outer predicate: same-category hotels only, or also similar-only ones."""
        if include_similar:
            return "(min_price_same IS NOT NULL OR min_price_similar IS NOT NULL)"
        return "min_price_same IS NOT NULL"

    @staticmethod
    def _build_category_filter(
        room_type_category: str | None,
    ) -> tuple[str, dict[str, str]]:
        """Bind the complete comparable pool without interpolating values."""
        params = {
            f"room_type_category_{index}": category
            for index, category in enumerate(
                comparable_room_type_categories(room_type_category)
            )
        }
        if not params:
            return "1 = 1", {}
        placeholders = ", ".join(f":{name}" for name in params)
        return f"rp.room_type_category IN ({placeholders})", params

    @staticmethod
    def _market_key_params(
        account_id: UUID | str,
        canonical_destination: str,
        check_in: date,
        check_out: date,
        adults: int,
        children: int,
        rooms: int,
    ) -> dict[str, Any]:
        return {
            "account_id": str(account_id),
            "canonical_destination": canonical_destination,
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "adults": adults,
            "children": children,
            "rooms": rooms,
        }

    @classmethod
    def _build_params(
        cls,
        account_id: UUID | str,
        canonical_destination: str,
        check_in: date,
        check_out: date,
        adults: int,
        children: int,
        rooms: int,
        category_params: dict[str, str],
        property_ids: list[UUID | str] | None,
        limit_runs: int,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            **cls._market_key_params(
                account_id, canonical_destination, check_in, check_out, adults, children, rooms
            ),
            "limit_runs": limit_runs,
            **category_params,
        }
        if property_ids:
            params["property_ids"] = [str(property_id) for property_id in property_ids]
        return params

    def _execute(self, sql: str, params: dict[str, Any]) -> list[dict]:
        try:
            from sqlalchemy import text
        except ImportError as exc:
            raise RuntimeError("SQLAlchemy is required. Install dependencies from requirements.txt") from exc

        with self.connection_factory() as connection:
            result = connection.execute(text(sql), params)
            return [dict(row) for row in result.mappings().all()]
