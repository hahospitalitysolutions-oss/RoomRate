# Round 6 — Stream C (pricing) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the price recommendation trustworthy for the Thursday live demo: the account's own hotel never counts inside its own market statistics; the own reference price comes from the live Booking row of the latest run (fallback: onboarding sample); statistics fall back to "same + similar" categories when fewer than 5 same-category hotels exist; the response carries `position`, `stats_scope`, `own_price_source`; confidence counts distinct calendar days; the ±20% guardrail never collapses the band to a single price; every statistical-path string is Greek; the 15-minute cache is invalidated by a new scrape; price alerts ignore a "previous" run younger than 12 hours; and the pricing page prefills its form from the last competitor search (`?job=` or workflow storage) and renders the new fields in Greek.

**Architecture:** Backend changes are confined to the pricing vertical: `price_history_repository.py` (SQL CTE: own-hotel exclusion, per-hotel `min_price_same`/`min_price_similar`, latest-run lookup, own live price lookup, 12-hour alert gap), `price_statistics_service.py` (scope selection, position, confidence by days), `price_recommendation_agent.py` (Greek strings, guardrail, reference-price order, cache key), `price_alert_service.py` (passes `min_gap_hours=12`), `api/routers/agents.py` (wires the run id into the cache key and the new response fields) and the recommendation response schema. Frontend changes are confined to `pricing-page.component.ts` and `types/pricing.ts`; the page reads the last job through the existing `GET /api/v1/scrape-jobs/{id}`. Stream A/B files (`api/routers/market.py`, `types/market.ts`, `e2e/helpers.ts`, map/scraper files) are never modified.

**Tech Stack:** FastAPI + SQLAlchemy Core text SQL (PostgreSQL), pytest with fake repositories/services (no DB in unit tests), Angular 20 standalone components with signals, Playwright e2e with `**` route mocks.

---

## File structure

Worktree: `C:\vscode_code\wt-round6-c\room_project2` on branch `feature/round6-c` (git root is the PARENT directory `C:\vscode_code\wt-round6-c`; always `git add` explicit paths, never `git add -A`).

Modified files (Stream C ownership only):

| Path (relative to `room_project2/`) | Role in this plan |
|---|---|
| `api/repositories/price_history_repository.py` | own-hotel `NOT EXISTS` exclusion in the CTE; `min_price_same` / `min_price_similar` per (run, hotel); `fetch_latest_completed_run_id`; `fetch_own_live_price`; `fetch_latest_vs_previous(min_gap_hours)` |
| `api/services/price_statistics_service.py` | scope selection (`same` vs `same_plus_similar`), `position`, confidence by distinct days, Greek notes |
| `api/services/price_recommendation_agent.py` | own reference price order + `own_price_source`, guardrail without collapse, Greek notes/factors/reasoning, Greek LLM system prompt, cache key with run id |
| `api/services/price_alert_service.py` | passes `min_gap_hours=12` |
| `api/routers/agents.py` | resolves the latest run id before the cache lookup; surfaces the new response fields |
| `api/schemas/agents.py` (the module that defines `PriceStatistics`, `PriceRecommendation`, `PriceRecommendationResponse`) | `PricePosition`, `PriceStatsScope`, `sample_days`, `position`, `stats_scope`, `own_price_source` |
| `api/tests/test_price_history_repository.py` (extended) | SQL text assertions for exclusion / same-similar mins / run id / own live price / 12-hour gap |
| `api/tests/test_price_statistics_service.py` (extended) | scope rule, position, sample days, Greek notes |
| `api/tests/test_price_recommendation_agent.py` (extended) | confidence by days, Greek factors/reasoning, guardrail without collapse |
| `api/tests/test_price_alert_service.py` (extended) | 3-minute vs 13-hour gap |
| `api/tests/test_price_recommendation_routes.py` (extended) | own reference price order, cache key with run id, response contract |
| `frontend/src/app/types/pricing.ts` | `PricePosition`, `PriceStatsScope`, `OwnPriceSource` types |
| `frontend/src/app/pages/pricing-page.component.ts` | prefill from job, Greek labels for position / trend / source / scope, notes shown once |
| `frontend/e2e/pricing-round6.spec.ts` (new) | Playwright: prefill from `?job=` and from storage, position / scope / source texts |

Never touched by this stream: `api/routers/market.py`, `frontend/src/app/types/market.ts`, `frontend/e2e/helpers.ts` (imported read-only), any `map-*` or `scraper/*` file.

Command conventions used in every task:

- pytest: `cd C:\vscode_code\wt-round6-c\room_project2; $env:PYTHONUTF8=1; python -m pytest <path> -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-c"`
- Playwright: `cd C:\vscode_code\wt-round6-c\room_project2\frontend; $env:ROOMRATE_E2E_PORT="4300"; npx playwright test e2e/pricing-round6.spec.ts`
- commit: `cd C:\vscode_code\wt-round6-c\room_project2; git add <explicit paths>; git commit -m "<imperative message>" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

Design decisions fixed by this plan (read before starting):

1. **Own-hotel exclusion column.** `roomrate_properties.canonical_name` is `canonicalize_name(display_name)` (a canonical form written by `room_rates_normalizer.py:309`), while the exclusion that already works live (`room_rates_repository.py:23-35`) compares `op.display_name` with `p.display_name`. The CTE therefore accepts either spelling: `lower(trim(op.display_name)) IN (lower(trim(p.display_name)), lower(trim(p.canonical_name)))`.
2. **Row shape stays backward compatible.** `fetch_price_series` keeps returning `min_price` (= `min_price_same`) so the untouched `api/routers/market.py` and the alert comparison keep their meaning; the new columns `min_price_same` / `min_price_similar` are additive. A new keyword `include_similar: bool = False` decides whether similar-only hotels are returned at all — only the agents router passes `True`.
3. **Where the new response fields live.** `sample_days`, `position`, `stats_scope` sit inside `statistics` (next to `own_position_percentile`, computed by the pure statistics service); `own_price_source` sits at the top level of `PriceRecommendationResponse` (a router-level fact). Tasks 2 and 3 add the fields they need; Task 8 pins the whole JSON contract.
4. **Duplicated notes.** The statistical reasoning no longer concatenates `statistics.notes`; the notes render once in the «Στατιστικά αγοράς» panel (and once in the «not enough data» card when there is no recommendation).
5. **Prefill guard.** A completed competitor search whose `check_in` is already in the past is NOT replayed (the backend rejects a past check-in and a rolled-forward window would no longer be the search the note claims): today's defaults apply and no note is shown.
6. **Alert gap is enforced twice.** The repository picks the previous run with `finished_at <= latest − 12 h` in SQL, and the service re-checks `observed_at − previous_observed_at` on rows that carry both timestamps, so the 3-minute / 13-hour rule is unit-testable with fakes.

---

### Task 1: Repository — own-hotel exclusion, same/similar minimums, latest run id, own live price

**Files:**
- Modify: `api/repositories/price_history_repository.py`
- Test: `api/tests/test_price_history_repository.py`

- [ ] **Step 1.1: Append the failing tests to `api/tests/test_price_history_repository.py`**

Add these imports at the top of the file (keep the existing ones):

```python
from decimal import Decimal

from api.tests._fakes import EchoResult
```

Append at the end of the file:

```python
# ---------------------------------------------------------------------------
# Round 6 — own-hotel exclusion, same/similar minimums, run id, own live price
# ---------------------------------------------------------------------------


class _RowsResult(EchoResult):
    """Echo result that also hands back scripted rows."""

    def __init__(self, rows: list[dict]):
        self.rows = rows

    def all(self):
        return list(self.rows)


class _RowsConnection(FakeConnection):
    """Records SQL like EchoConnection and returns the scripted rows."""

    def __init__(self, rows: list[dict]):
        super().__init__()
        self.rows = rows

    def execute(self, sql, params):
        super().execute(sql, params)
        return _RowsResult(self.rows)


MARKET_KEY = {
    "account_id": ACCOUNT_ID,
    "canonical_destination": "faliraki",
    "check_in": date(2026, 7, 1),
    "check_out": date(2026, 7, 5),
    "adults": 2,
    "children": 0,
    "rooms": 1,
}


def test_prices_cte_excludes_the_accounts_own_properties():
    connection = FakeConnection()

    _repository(connection).fetch_price_series(**MARKET_KEY, room_type_category="double")

    sql = connection.executed_sql
    assert "NOT EXISTS" in sql
    assert "FROM roomrate_owned_properties op" in sql
    assert "op.account_id = :account_id" in sql
    assert "op.is_active = true" in sql
    # Either spelling of the property name identifies the owner's own row.
    assert "lower(trim(op.display_name)) IN (" in sql
    assert "lower(trim(p.display_name))" in sql
    assert "lower(trim(p.canonical_name))" in sql


def test_fetch_latest_vs_previous_excludes_the_accounts_own_properties_too():
    connection = FakeConnection()

    _repository(connection).fetch_latest_vs_previous(**MARKET_KEY, room_type_category="double")

    assert "FROM roomrate_owned_properties op" in connection.executed_sql


def test_fetch_price_series_returns_same_and_similar_minimums():
    connection = FakeConnection()

    _repository(connection).fetch_price_series(**MARKET_KEY, room_type_category="double")

    sql = connection.executed_sql
    assert "AS min_price_same" in sql
    assert "AS min_price_similar" in sql
    # The legacy column keeps its meaning (same-category minimum) for market.py and alerts.
    assert "AS min_price" in sql
    assert "rp.room_type_category IN (:room_type_category_0, :room_type_category_1)" in sql
    assert "IS DISTINCT FROM 'single'" in sql
    # Without opting in, similar-only hotels are not returned (default behaviour).
    assert "WHERE min_price_same IS NOT NULL" in sql
    assert connection.executed_params["room_type_category_0"] == "double"
    assert connection.executed_params["room_type_category_1"] == "twin"


def test_fetch_price_series_include_similar_keeps_similar_only_hotels():
    connection = FakeConnection()

    _repository(connection).fetch_price_series(
        **MARKET_KEY, room_type_category="double", include_similar=True
    )

    assert "(min_price_same IS NOT NULL OR min_price_similar IS NOT NULL)" in connection.executed_sql


def test_fetch_price_series_without_category_treats_every_package_as_same():
    connection = FakeConnection()

    _repository(connection).fetch_price_series(**MARKET_KEY)

    sql = connection.executed_sql
    assert "CASE WHEN 1 = 1 THEN rp.price_per_night_eur END" in sql
    assert not any(key.startswith("room_type_category_") for key in connection.executed_params)


def test_fetch_latest_vs_previous_default_keeps_the_immediately_previous_run():
    connection = FakeConnection()

    _repository(connection).fetch_latest_vs_previous(**MARKET_KEY)

    sql = connection.executed_sql
    assert "prev.rn = 2" in sql
    assert "min_gap_hours" not in connection.executed_params
    assert connection.executed_params["limit_runs"] == 2


def test_fetch_latest_vs_previous_with_min_gap_picks_a_run_at_least_that_old():
    connection = FakeConnection()

    _repository(connection).fetch_latest_vs_previous(**MARKET_KEY, min_gap_hours=12)

    sql = connection.executed_sql
    assert "prev.rn = 2" not in sql
    assert "older.rn > 1" in sql
    assert "(:min_gap_hours * interval '1 hour')" in sql
    assert "AS previous_observed_at" in sql
    assert connection.executed_params["min_gap_hours"] == 12.0
    # Back-to-back scrapes must not push the 12-hour-old run out of the window.
    assert connection.executed_params["limit_runs"] > 2


def test_fetch_latest_completed_run_id_returns_none_without_runs():
    connection = FakeConnection()

    run_id = _repository(connection).fetch_latest_completed_run_id(**MARKET_KEY)

    assert run_id is None
    sql = connection.executed_sql
    assert "FROM roomrate_scrape_runs sr" in sql
    assert "sr.status = 'completed'" in sql
    assert "ORDER BY sr.finished_at DESC, sr.id DESC" in sql
    assert "LIMIT 1" in sql
    params = connection.executed_params
    assert params["account_id"] == str(ACCOUNT_ID)
    assert params["canonical_destination"] == "faliraki"
    assert params["check_in"] == "2026-07-01"
    assert params["rooms"] == 1


def test_fetch_latest_completed_run_id_returns_the_id_as_a_string():
    run_id = UUID("00000000-0000-0000-0000-00000000f00d")
    connection = _RowsConnection([{"id": run_id}])

    assert _repository(connection).fetch_latest_completed_run_id(**MARKET_KEY) == str(run_id)


def test_fetch_own_live_price_matches_the_display_name_in_the_latest_run():
    connection = FakeConnection()

    price = _repository(connection).fetch_own_live_price(
        **MARKET_KEY, room_type_category="double", display_name="  Rea Hotel "
    )

    assert price is None  # the echo connection returns no rows
    sql = connection.executed_sql
    assert "WITH latest_run AS" in sql
    assert "sr.status = 'completed'" in sql
    assert "lower(trim(:display_name)) IN (" in sql
    assert "lower(trim(p.display_name))" in sql
    assert "lower(trim(p.canonical_name))" in sql
    assert "AS min_price_same" in sql
    assert "AS min_price_any" in sql
    assert "rp.room_type_category IN (:room_type_category_0, :room_type_category_1)" in sql
    params = connection.executed_params
    assert params["display_name"] == "Rea Hotel"
    assert params["room_type_category_0"] == "double"
    assert params["canonical_destination"] == "faliraki"


def test_fetch_own_live_price_prefers_the_comparable_pool_over_any_package():
    connection = _RowsConnection(
        [{"min_price_same": Decimal("92.00"), "min_price_any": Decimal("70.00")}]
    )

    price = _repository(connection).fetch_own_live_price(
        **MARKET_KEY, room_type_category="double", display_name="Rea Hotel"
    )

    assert price == 92.0


def test_fetch_own_live_price_falls_back_to_the_cheapest_package_of_any_category():
    connection = _RowsConnection([{"min_price_same": None, "min_price_any": Decimal("70.00")}])

    price = _repository(connection).fetch_own_live_price(
        **MARKET_KEY, room_type_category="double", display_name="Rea Hotel"
    )

    assert price == 70.0


def test_fetch_own_live_price_returns_none_when_the_hotel_is_absent_from_the_run():
    # An aggregate over zero rows yields one all-NULL row in PostgreSQL.
    connection = _RowsConnection([{"min_price_same": None, "min_price_any": None}])

    assert (
        _repository(connection).fetch_own_live_price(
            **MARKET_KEY, room_type_category="double", display_name="Rea Hotel"
        )
        is None
    )


def test_fetch_own_live_price_skips_the_query_without_a_display_name():
    connection = FakeConnection()

    assert (
        _repository(connection).fetch_own_live_price(
            **MARKET_KEY, room_type_category="double", display_name="   "
        )
        is None
    )
    assert connection.executed_sql == ""
```

- [ ] **Step 1.2: Run the repository tests and confirm the new ones fail**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_price_history_repository.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-c"
```

Expected: the 6 pre-existing tests pass; the new tests fail with `AssertionError` (no `NOT EXISTS`, no `min_price_same`) or `TypeError: ... unexpected keyword argument 'include_similar'` / `'min_gap_hours'` and `AttributeError: 'PriceHistoryRepository' object has no attribute 'fetch_latest_completed_run_id'` / `'fetch_own_live_price'`.

- [ ] **Step 1.3: Replace `api/repositories/price_history_repository.py` with the implementation**

Full file content:

```python
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date
from typing import Any
from uuid import UUID

from api.services.market_helpers import as_optional_float
from api.services.room_rates_normalizer import comparable_room_type_categories

# How many completed runs the alert comparison may look back through when a
# minimum gap is requested. Scheduled scrapes can run every few minutes, so the
# run that is 12 hours old is rarely rn = 2.
_ALERT_HISTORY_WINDOW_RUNS = 200

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

# Shared CTE: rank completed scrape runs for one market (newest first), then
# fold each run down to the cheapest offer per property. Snapshots accumulate
# one row per (run, property) so price history falls out of existing tables.
#
# Per (run, property) two minimums come back: ``min_price_same`` over the
# packages in the baseline category's comparable pool and ``min_price_similar``
# over every other category except ``single`` (Round 6 §5.2). ``min_price``
# keeps its historical meaning (= the same-category minimum) for callers that
# predate the split. Without a baseline category every package is "same".
#
# canonical_destination is NOT NULL and the writer always stamps finished_at
# on completed runs, so both are compared/ordered directly (no COALESCE) and
# the planner can use ix_roomrate_scrape_runs_history.
_PRICES_CTE_TEMPLATE = """
    WITH ranked_runs AS (
        SELECT runs.id, runs.observed_at, runs.rn
        FROM (
            SELECT
                sr.id,
                sr.finished_at AS observed_at,
                row_number() OVER (
                    ORDER BY sr.finished_at DESC, sr.id DESC
                ) AS rn
            FROM roomrate_scrape_runs sr
            WHERE sr.account_id = :account_id
              AND sr.canonical_destination = :canonical_destination
              AND sr.check_in = :check_in
              AND sr.check_out = :check_out
              AND sr.adults = :adults
              AND sr.children = :children
              AND sr.rooms = :rooms
              AND sr.status = 'completed'
        ) runs
        WHERE runs.rn <= :limit_runs
    ),
    prices AS (
        SELECT
            rr.rn,
            rr.observed_at,
            ro.property_id,
            p.canonical_name AS hotel_name,
            MIN(CASE WHEN {same_filter} THEN rp.price_per_night_eur END) AS min_price_same,
            MIN(
                CASE
                    WHEN NOT ({same_filter})
                     AND rp.room_type_category IS DISTINCT FROM 'single'
                    THEN rp.price_per_night_eur
                END
            ) AS min_price_similar,
            MIN(CASE WHEN {same_filter} THEN rp.price_per_night_eur END) AS min_price
        FROM ranked_runs rr
        JOIN roomrate_rate_observations ro ON ro.scrape_run_id = rr.id
        JOIN roomrate_room_packages rp ON rp.rate_observation_id = ro.id
        JOIN roomrate_properties p ON p.id = ro.property_id
        WHERE rp.price_per_night_eur IS NOT NULL
          {property_filter}
          {own_property_exclusion}
        GROUP BY rr.rn, rr.observed_at, ro.property_id, p.canonical_name
    )
"""

# The latest completed run of one market key (used on its own and as a CTE).
_LATEST_RUN_SQL = """
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
    ) -> list[dict]:
        """Fetch the cheapest nightly price per property for recent runs.

        Returns one row per (run, property), newest run first (rn = 1), with
        ``min_price_same``, ``min_price_similar`` and the legacy ``min_price``
        (= ``min_price_same``). Hotels that only offer similar categories are
        returned only when ``include_similar`` is True.
        """
        same_filter, category_params = self._build_category_filter(room_type_category)
        sql = (
            self._prices_cte(property_ids, same_filter)
            + f"""
            SELECT rn, observed_at, property_id, hotel_name,
                   min_price, min_price_same, min_price_similar
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
    ) -> list[dict]:
        """Compare each property's cheapest same-category price with a previous run.

        Without ``min_gap_hours`` the previous run is the immediately preceding
        one. With it, the previous run is the most recent one that finished at
        least that many hours before the latest run; when no such run exists
        ``previous_price`` is NULL and the caller raises no alert (Round 6:
        a rerun three minutes later is the same market moment, not a change).
        """
        same_filter, category_params = self._build_category_filter(room_type_category)
        if min_gap_hours is None:
            previous_run_predicate = "prev.rn = 2"
            limit_runs = 2  # only the latest and previous runs matter here
        else:
            previous_run_predicate = """prev.rn = (
                    SELECT MIN(older.rn)
                    FROM ranked_runs older
                    WHERE older.rn > 1
                      AND older.observed_at <= (
                          (SELECT latest.observed_at FROM ranked_runs latest WHERE latest.rn = 1)
                          - (:min_gap_hours * interval '1 hour')
                      )
                )"""
            limit_runs = _ALERT_HISTORY_WINDOW_RUNS
        sql = (
            self._prices_cte(property_ids, same_filter)
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
               AND {previous_run_predicate}
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
            _LATEST_RUN_SQL,
            self._market_key_params(
                account_id, canonical_destination, check_in, check_out, adults, children, rooms
            ),
        )
        if not rows:
            return None
        run_id = rows[0].get("id")
        return str(run_id) if run_id is not None else None

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
    ) -> float | None:
        """Cheapest nightly price of the owner's OWN row in the latest completed run.

        Prefers the cheapest package inside the baseline category's comparable
        pool, else the cheapest package of any category. None when the market
        key has no completed run, or Booking did not return the property.
        """
        name = (display_name or "").strip()
        if not name:
            return None
        same_filter, category_params = self._build_category_filter(room_type_category)
        sql = f"""
            WITH latest_run AS (
            {_LATEST_RUN_SQL}
            )
            SELECT
                MIN(CASE WHEN {same_filter} THEN rp.price_per_night_eur END) AS min_price_same,
                MIN(rp.price_per_night_eur) AS min_price_any
            FROM latest_run lr
            JOIN roomrate_rate_observations ro ON ro.scrape_run_id = lr.id
            JOIN roomrate_room_packages rp ON rp.rate_observation_id = ro.id
            JOIN roomrate_properties p ON p.id = ro.property_id
            WHERE rp.price_per_night_eur IS NOT NULL
              AND lower(trim(:display_name)) IN (
                  lower(trim(p.display_name)),
                  lower(trim(p.canonical_name))
              )
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
        same_price = as_optional_float(rows[0].get("min_price_same"))
        if same_price is not None:
            return same_price
        return as_optional_float(rows[0].get("min_price_any"))

    @staticmethod
    def _prices_cte(
        property_ids: list[UUID | str] | None,
        same_filter: str,
    ) -> str:
        """Build the CTE from trusted predicate fragments only."""
        property_filter = (
            "AND ro.property_id = ANY(CAST(:property_ids AS uuid[]))"
            if property_ids
            else ""
        )
        return _PRICES_CTE_TEMPLATE.format(
            same_filter=same_filter,
            property_filter=property_filter,
            own_property_exclusion=_OWN_PROPERTY_EXCLUSION,
        )

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
```

- [ ] **Step 1.4: Run the repository tests — all pass**

Same pytest command as Step 1.2. Expected: `20 passed` (6 old + 14 new).

- [ ] **Step 1.5: Run the neighbours that share the repository (alerts, routes) to prove nothing else moved**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_price_alert_service.py api/tests/test_price_recommendation_routes.py api/tests/test_price_statistics_service.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-c"
```

Expected: all pass (the new keyword arguments have defaults; the fakes never see them).

- [ ] **Step 1.6: Commit**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; git add api/repositories/price_history_repository.py api/tests/test_price_history_repository.py; git commit -m "Exclude the owner's hotel from price history and split same/similar minimums" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 2: Router — own reference price: live Booking row, then onboarding sample (`own_price_source`)

**Files:**
- Modify: `api/schemas/agents.py`
- Modify: `api/routers/agents.py`
- Test: `api/tests/test_price_recommendation_routes.py`

- [ ] **Step 2.1: Extend the shared fake and append the failing tests**

Replace the whole `FakePriceHistoryRepository` class in `api/tests/test_price_recommendation_routes.py` with:

```python
class FakePriceHistoryRepository:
    def __init__(self, rows, own_live_price=None):
        self.rows = rows
        self.own_live_price = own_live_price
        self.last_kwargs = None
        self.own_price_kwargs = None

    def fetch_price_series(self, **kwargs):
        self.last_kwargs = kwargs
        return self.rows

    def fetch_own_live_price(self, **kwargs):
        self.own_price_kwargs = kwargs
        return self.own_live_price
```

Append at the end of the file:

```python
# ---------------------------------------------------------------------------
# Round 6 — own reference price: live Booking row, then onboarding sample
# ---------------------------------------------------------------------------


def _round6_client(history_repo, sample_price=120.0, agent=None, audit_repo=None):
    onboarding_repo = FakeOnboardingRepository(
        owned_property={
            "id": OWNED_PROPERTY_ID,
            "display_name": "My Hotel",
            "canonical_destination": "faliraki",
            "selected_room_type_category": "double",
        },
        selected_room_type={
            "room_type": "Double Room",
            "room_type_category": "double",
            "sample_price_per_night_eur": sample_price,
        },
    )
    _override_account()
    app.dependency_overrides[get_onboarding_repository] = lambda: onboarding_repo
    app.dependency_overrides[get_price_history_repository] = lambda: history_repo
    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()
    app.dependency_overrides[get_price_recommendation_agent] = lambda: agent or FakeAgent()
    if audit_repo is not None:
        app.dependency_overrides[get_price_recommendation_audit_repository] = lambda: audit_repo
    return TestClient(app)


def test_own_reference_price_prefers_the_live_booking_row():
    history_repo = FakePriceHistoryRepository(_history_rows(), own_live_price=92.0)
    client = _round6_client(history_repo, sample_price=120.0)

    body = _pricing_request(client).json()

    assert body["own_price_source"] == "booking_live"
    assert body["statistics"]["own_reference_price_eur"] == 92.0
    kwargs = history_repo.own_price_kwargs
    assert kwargs["display_name"] == "My Hotel"
    assert kwargs["room_type_category"] == "double"
    assert kwargs["canonical_destination"] == "faliraki"
    assert kwargs["check_in"].isoformat() == "2030-07-15"
    app.dependency_overrides.clear()


def test_own_reference_price_falls_back_to_the_onboarding_sample():
    history_repo = FakePriceHistoryRepository(_history_rows(), own_live_price=None)
    client = _round6_client(history_repo, sample_price=120.0)

    body = _pricing_request(client).json()

    assert body["own_price_source"] == "onboarding_sample"
    assert body["statistics"]["own_reference_price_eur"] == 120.0
    app.dependency_overrides.clear()


def test_own_reference_price_is_null_without_any_source():
    history_repo = FakePriceHistoryRepository(_history_rows(), own_live_price=None)
    client = _round6_client(history_repo, sample_price=None)

    body = _pricing_request(client).json()

    assert body["own_price_source"] is None
    assert body["statistics"]["own_reference_price_eur"] is None
    app.dependency_overrides.clear()
```

- [ ] **Step 2.2: Run the route tests — the 3 new ones fail**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_price_recommendation_routes.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-c"
```

Expected: 14 pass, 3 fail with `KeyError: 'own_price_source'` (the response has no such field yet; the router still reads only the sample price).

- [ ] **Step 2.3: Add `OwnPriceSource` to `api/schemas/agents.py`**

Insert after the imports:

```python
# Where the owner's reference price came from (Round 6 §5.2): the property's
# own live Booking row in the latest completed run, or the onboarding sample.
OwnPriceSource = Literal["booking_live", "onboarding_sample"]
```

In `PriceRecommendationResponse`, after `prompt_version: str | None = None` add:

```python
    own_price_source: OwnPriceSource | None = None
```

- [ ] **Step 2.4: Wire the order into `api/routers/agents.py`**

Import: change `from api.schemas.agents import (` to include `OwnPriceSource`:

```python
from api.schemas.agents import (
    OwnPriceSource,
    PriceRecommendationRequest,
    PriceRecommendationResponse,
)
```

Add after `build_recommendation_request_hash`:

```python
def resolve_own_reference_price(
    price_history_repository: PriceHistoryRepository,
    *,
    account_id,
    canonical_destination: str,
    request: PriceRecommendationRequest,
    room_type_category: str,
    display_name: str | None,
    sample_price: float | None,
) -> tuple[float | None, OwnPriceSource | None]:
    """Own reference price, in order: live Booking row of the latest run, onboarding sample, none.

    The live row is what the market actually sees for these dates; the
    onboarding sample is a typed-in number that goes stale. Neither is ever
    counted as a competitor (the repository CTE excludes the own hotel).
    """
    live_price = price_history_repository.fetch_own_live_price(
        account_id=account_id,
        canonical_destination=canonical_destination,
        check_in=request.check_in,
        check_out=request.check_out,
        adults=request.adults,
        children=request.children,
        rooms=request.rooms,
        room_type_category=room_type_category,
        display_name=display_name,
    )
    if live_price is not None:
        return round(live_price, 2), "booking_live"
    if sample_price is not None:
        return round(sample_price, 2), "onboarding_sample"
    return None, None
```

Replace the category/own-price block (from `# Resolve the room category + own reference price.` through `own_price = round(own_price, 2)`) with:

```python
    # Resolve the room category + onboarding sample price. A body-supplied
    # category wins; otherwise we use the account's selected baseline room type.
    selected_room = onboarding_repository.get_selected_room_type(
        account.account_id, request.owned_property_id
    )
    room_type_category = request.room_type_category
    sample_price: float | None = None
    if selected_room is not None:
        room_type_category = room_type_category or selected_room.get("room_type_category")
        # as_optional_float preserves a genuine 0.0 sample price and maps only a
        # missing/unparseable value to "no sample price".
        sample_price = as_optional_float(selected_room.get("sample_price_per_night_eur"))
```

Replace `started_at = perf_counter()` + the blank line before `history_rows = ...` with (the live lookup sits AFTER the cache/quota gates so a cache hit costs no query):

```python
    started_at = perf_counter()

    own_price, own_price_source = resolve_own_reference_price(
        price_history_repository,
        account_id=account.account_id,
        canonical_destination=canonical_destination,
        request=request,
        room_type_category=room_type_category,
        display_name=owned_property.get("display_name"),
        sample_price=sample_price,
    )
```

Add `own_price_source=own_price_source,` to the `PriceRecommendationResponse(...)` construction (after `recommendation_available=...`).

- [ ] **Step 2.5: Run the route tests — all pass**

Same command as Step 2.2. Expected: `17 passed` (the cached-response path still validates: `own_price_source` is inside the stored `response_payload`).

- [ ] **Step 2.6: Commit**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; git add api/schemas/agents.py api/routers/agents.py api/tests/test_price_recommendation_routes.py; git commit -m "Take the owner's reference price from the live Booking row before the onboarding sample" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 3: Statistics — `stats_scope` rule, `position`, confidence by distinct days

**Files:**
- Modify: `api/schemas/agents.py`
- Modify: `api/services/price_statistics_service.py`
- Modify: `api/services/price_recommendation_agent.py` (`deterministic_confidence` only)
- Modify: `api/routers/agents.py` (`include_similar=True`)
- Test: `api/tests/test_price_statistics_service.py`, `api/tests/test_price_recommendation_agent.py`, `api/tests/test_price_recommendation_routes.py`

- [ ] **Step 3.1: Append the failing tests**

`api/tests/test_price_statistics_service.py` — change the schema import to `from api.schemas.agents import PricePosition, PriceStatistics, PriceStatsScope` and append:

```python
# ---------------------------------------------------------------------------
# Round 6 — scope (same vs same + similar), position, distinct days
# ---------------------------------------------------------------------------

LATEST = "2026-06-30T08:00:00+00:00"


def _row(rn, observed_at, property_id, same, similar=None):
    """A Round 6 repository row: two minimums per (run, hotel), legacy min_price = same."""
    return {
        "rn": rn,
        "observed_at": observed_at,
        "property_id": property_id,
        "hotel_name": property_id.upper(),
        "min_price": same,
        "min_price_same": same,
        "min_price_similar": similar,
    }


def test_five_same_category_hotels_keep_the_same_scope_and_ignore_similar_ones():
    rows = [_row(1, LATEST, f"p{index}", 100.0 + 10 * index) for index in range(5)]
    rows += [_row(1, LATEST, "s1", None, 40.0), _row(1, LATEST, "s2", None, 300.0)]

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.stats_scope == PriceStatsScope(same_category=5, similar=2, used="same")
    assert stats.market_median_eur == 120.0  # 100..140; the similar 40 / 300 never counted
    assert not any("παρόμοια" in note for note in stats.notes)


def test_below_five_same_hotels_similar_ones_join_with_one_minimum_per_hotel():
    rows = [
        _row(1, LATEST, "p1", 100.0, 80.0),  # has both: counts once, at its cheapest (80)
        _row(1, LATEST, "p2", 140.0),
        _row(1, LATEST, "s1", None, 90.0),
        _row(1, LATEST, "s2", None, 200.0),
    ]

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.stats_scope == PriceStatsScope(same_category=2, similar=2, used="same_plus_similar")
    assert stats.market_median_eur == 115.0  # 80 / 90 / 140 / 200
    assert any("παρόμοια" in note for note in stats.notes)


def test_position_counts_hotels_strictly_cheaper_than_the_own_price():
    stats = compute_price_statistics(_history_rows(), own_price=120.0, check_in=date(2026, 7, 15))

    assert stats.position == PricePosition(cheaper_than_you=1, total=3)  # 100 < 120; 120 is not cheaper
    # Legacy rows (min_price only) are a same-category scope of every hotel.
    assert stats.stats_scope == PriceStatsScope(same_category=3, similar=0, used="same")
    assert compute_price_statistics(_history_rows(), own_price=None, check_in=date(2026, 7, 15)).position is None


def test_sample_days_counts_distinct_calendar_days_not_runs():
    rows = [
        _row(1, "2026-06-30T08:03:00+00:00", "p1", 100.0),
        _row(2, "2026-06-30T08:00:00+00:00", "p1", 100.0),  # rerun 3 minutes earlier, same day
        _row(3, "2026-06-23T08:00:00+00:00", "p1", 90.0),
    ]

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert stats.sample_runs == 3
    assert stats.sample_days == 2
```

`api/tests/test_price_recommendation_agent.py` — add `deterministic_confidence` to the `price_recommendation_agent` import and append:

```python
# ---------------------------------------------------------------------------
# Round 6 — confidence counts calendar days, not runs
# ---------------------------------------------------------------------------


def _runs_on_days(days: list[str]) -> PriceStatistics:
    rows = []
    for run_offset, stamp in enumerate(days):
        rn = len(days) - run_offset  # rn=1 is the NEWEST run
        for prop, price in (("p1", 100.0), ("p2", 140.0)):
            rows.append({"rn": rn, "observed_at": stamp, "property_id": prop, "hotel_name": prop, "min_price": price})
    return compute_price_statistics(rows, own_price=120.0, check_in=date(2026, 7, 15), as_of=date(2026, 7, 1))


def test_six_runs_in_one_afternoon_are_one_day_and_stay_low():
    stats = _runs_on_days([f"2026-06-30T{hour:02d}:00:00+00:00" for hour in range(8, 14)])

    assert stats.sample_runs == 6
    assert stats.sample_days == 1
    assert deterministic_confidence(stats) == "low"


def test_two_distinct_days_without_a_trend_are_medium():
    stats = _runs_on_days(["2026-06-29T08:00:00+00:00", "2026-06-30T08:00:00+00:00"])

    assert stats.sample_days == 2
    assert stats.trend_7d_pct is None
    assert deterministic_confidence(stats) == "medium"
```

`api/tests/test_price_recommendation_routes.py` — append:

```python
def test_price_recommendation_reads_similar_hotels_too():
    history_repo = FakePriceHistoryRepository(_history_rows())
    client = _round6_client(history_repo)

    assert _pricing_request(client).status_code == 200
    # Similar-only hotels ride along; the statistics decide whether to use them.
    assert history_repo.last_kwargs["include_similar"] is True
    app.dependency_overrides.clear()
```

- [ ] **Step 3.2: Run the three files — the 7 new tests fail**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_price_statistics_service.py api/tests/test_price_recommendation_agent.py api/tests/test_price_recommendation_routes.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-c"
```

Expected: `ImportError: cannot import name 'PricePosition'` for the statistics file (collection error), `AttributeError: 'PriceStatistics' object has no attribute 'sample_days'` in the agent file, `KeyError: 'include_similar'` in the routes file.

- [ ] **Step 3.3: Add `PricePosition` / `PriceStatsScope` / the new fields to `api/schemas/agents.py`**

Insert before `class PriceStatistics`:

```python
class PricePosition(BaseModel):
    """How many hotels of the current market undercut the owner's reference price."""

    cheaper_than_you: int
    total: int


class PriceStatsScope(BaseModel):
    """Which hotels of the latest run back the statistics (Round 6 §5.2).

    ``same_category`` hotels offer the baseline category's comparable pool;
    ``similar`` hotels only offer other (non-single) categories. They are
    pulled in — one minimum per hotel — only when fewer than 5 same-category
    hotels exist, and ``used`` says whether that happened.
    """

    same_category: int
    similar: int
    used: Literal["same", "same_plus_similar"]
```

In `PriceStatistics`, after `sample_runs: int` add `sample_days: int = 0`, and after `own_position_percentile: float | None = None` add:

```python
    position: PricePosition | None = None
    stats_scope: PriceStatsScope | None = None
```

- [ ] **Step 3.4: Replace `api/services/price_statistics_service.py`**

Full file content (English notes stay for now — Task 4 translates every pre-existing string; the one NEW note is written in Greek directly):

```python
"""Deterministic statistical baseline for the hybrid price-prediction agent.

This module is pure: it does no I/O and never calls an LLM. It turns the raw
price-history rows produced by ``PriceHistoryRepository.fetch_price_series``
into a :class:`PriceStatistics` snapshot that the LLM agent uses as grounding
and that the statistical fallback uses directly when no LLM is configured.

The function is defensive against empty/short history — every derived figure is
``None`` when the input doesn't support it, and ``notes`` explains why.

Round 6: rows carry two minimums per (run, hotel) — ``min_price_same`` (the
baseline category's comparable pool) and ``min_price_similar`` (every other
category except single). The statistics use same-category hotels; when the
latest run has fewer than ``MIN_SAME_CATEGORY_HOTELS`` of them and similar
hotels exist, every hotel counts once with its cheapest price of either kind.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from api.schemas.agents import PricePosition, PriceStatistics, PriceStatsScope

# Below this many same-category hotels a median is noise; similar hotels are
# pulled in (Round 6 §5.2).
MIN_SAME_CATEGORY_HOTELS = 5


def compute_price_statistics(
    history_rows: list[dict],
    own_price: float | None,
    check_in: date,
    as_of: date | None = None,
) -> PriceStatistics:
    """Compute a statistical price baseline from per-(run, property) history.

    Args:
        history_rows: Rows from ``fetch_price_series`` — one per (run, property)
            with at least ``rn`` (1 = newest run), ``observed_at`` and either
            ``min_price_same`` / ``min_price_similar`` or the legacy ``min_price``.
        own_price: The owned property's reference nightly price, or None.
        check_in: The market's check-in date (drives the lead-time figure).
        as_of: "Today" for the lead-time computation. Defaults to ``date.today()``
            but is injectable so tests stay deterministic.

    Returns:
        A populated :class:`PriceStatistics`. Optional figures are None when the
        history is too sparse, with explanatory caveats appended to ``notes``.
    """
    reference_date = as_of or date.today()
    lead_time_days = (check_in - reference_date).days
    notes: list[str] = []

    if not history_rows:
        notes.append("No price history available for this market key.")
        if own_price is None:
            notes.append("No own reference price provided.")
        return PriceStatistics(
            sample_runs=0,
            own_reference_price_eur=own_price,
            lead_time_days=lead_time_days,
            notes=notes,
        )

    # Build the DataFrame once; coerce the load-bearing numeric/time columns so
    # downstream math is null-safe regardless of how the rows were serialized.
    history_df = pd.DataFrame(history_rows)
    history_df["rn"] = pd.to_numeric(history_df.get("rn"), errors="coerce")
    # The repo emits ISO-8601 ``finished_at`` strings; pass an explicit format so
    # pandas doesn't infer per-row (silences the UserWarning and is faster).
    history_df["observed_at"] = pd.to_datetime(
        history_df.get("observed_at"), errors="coerce", utc=True, format="ISO8601"
    )
    # Rows that predate the same/similar split only carry ``min_price``.
    same_source = (
        history_df["min_price_same"]
        if "min_price_same" in history_df
        else history_df.get("min_price")
    )
    history_df["min_price_same"] = pd.to_numeric(same_source, errors="coerce")
    history_df["min_price_similar"] = pd.to_numeric(
        history_df.get("min_price_similar"), errors="coerce"
    )
    any_priced_df = history_df[
        history_df["rn"].notna()
        & (history_df["min_price_same"].notna() | history_df["min_price_similar"].notna())
    ]

    if any_priced_df.empty:
        notes.append("Price history rows contained no usable prices.")
        if own_price is None:
            notes.append("No own reference price provided.")
        return PriceStatistics(
            sample_runs=0,
            own_reference_price_eur=own_price,
            lead_time_days=lead_time_days,
            notes=notes,
        )

    # The most-recent run (smallest rn) is the current market snapshot. It
    # alone decides the scope, which then applies to every run so trends
    # compare like with like.
    latest_run_index = any_priced_df["rn"].min()
    stats_scope = _stats_scope(any_priced_df[any_priced_df["rn"] == latest_run_index])
    if stats_scope.used == "same_plus_similar":
        effective_price = any_priced_df[["min_price_same", "min_price_similar"]].min(axis=1)
        notes.append(
            f"Λιγότερα από {MIN_SAME_CATEGORY_HOTELS} καταλύματα ίδιας κατηγορίας "
            f"({stats_scope.same_category})· η βάση περιλαμβάνει και "
            f"{stats_scope.similar} παρόμοια."
        )
    else:
        effective_price = any_priced_df["min_price_same"]
    priced_df = any_priced_df.assign(min_price=effective_price).dropna(subset=["min_price"])

    sample_runs = int(priced_df["rn"].nunique())
    sample_days = _distinct_days(priced_df)
    current_prices = priced_df.loc[priced_df["rn"] == latest_run_index, "min_price"]

    market_median = round(float(current_prices.median()), 2)
    market_p25 = round(float(current_prices.quantile(0.25)), 2)
    market_p75 = round(float(current_prices.quantile(0.75)), 2)

    # Percentile rank of the owned price within the current market distribution.
    own_position_percentile = _own_position_percentile(current_prices, own_price)
    position = _position(current_prices, own_price)
    if own_price is None:
        notes.append("No own reference price provided; own position percentile unavailable.")

    # Trends compare the current median against the run closest to N days before
    # the latest observed_at. None when history doesn't reach back that far.
    latest_observed_at = priced_df.loc[priced_df["rn"] == latest_run_index, "observed_at"].max()
    run_medians = _median_per_run(priced_df)

    trend_7d_pct = _trend_pct(run_medians, latest_observed_at, market_median, days=7, notes=notes)
    trend_30d_pct = _trend_pct(run_medians, latest_observed_at, market_median, days=30, notes=notes)

    # Statistical recommendation: nudge the current median by the 7d trend, then
    # clamp into the current interquartile band so it stays market-realistic.
    statistical_recommendation = _statistical_recommendation(
        market_median, market_p25, market_p75, trend_7d_pct
    )

    if sample_runs == 1:
        notes.append("Only 1 run of history available; trends and momentum are unavailable.")

    return PriceStatistics(
        sample_runs=sample_runs,
        sample_days=sample_days,
        own_reference_price_eur=own_price,
        market_median_eur=market_median,
        market_p25_eur=market_p25,
        market_p75_eur=market_p75,
        own_position_percentile=own_position_percentile,
        position=position,
        stats_scope=stats_scope,
        trend_7d_pct=trend_7d_pct,
        trend_30d_pct=trend_30d_pct,
        lead_time_days=lead_time_days,
        statistical_recommendation_eur=statistical_recommendation,
        notes=notes,
    )


def _stats_scope(latest_df: pd.DataFrame) -> PriceStatsScope:
    """Scope of the latest run: same-category hotels, widened below the minimum.

    ``similar`` counts the hotels that ONLY offer similar categories (the ones
    widening adds); a hotel with both kinds is a same-category hotel.
    """
    same_count = int(latest_df["min_price_same"].notna().sum())
    similar_count = int(
        (latest_df["min_price_same"].isna() & latest_df["min_price_similar"].notna()).sum()
    )
    widen = same_count < MIN_SAME_CATEGORY_HOTELS and similar_count > 0
    return PriceStatsScope(
        same_category=same_count,
        similar=similar_count,
        used="same_plus_similar" if widen else "same",
    )


def _position(current_prices: pd.Series, own_price: float | None) -> PricePosition | None:
    """«Φθηνότεροι από εσάς»: hotels strictly below the own price, out of all of them."""
    if own_price is None or current_prices.empty:
        return None
    return PricePosition(
        cheaper_than_you=int((current_prices < own_price).sum()),
        total=int(len(current_prices)),
    )


def _distinct_days(priced_df: pd.DataFrame) -> int:
    """Distinct UTC calendar days with a run; a run without a timestamp still counts as one."""
    observed = priced_df["observed_at"].dropna()
    days = int(observed.dt.date.nunique()) if not observed.empty else 0
    return max(days, 1)


def _own_position_percentile(current_prices: pd.Series, own_price: float | None) -> float | None:
    """Percentile rank (0-100) of ``own_price`` within the current market.

    Uses the share of competitors priced at or below the owned price. Returns
    None when there's no own price or no current market to compare against.
    """
    if own_price is None or current_prices.empty:
        return None
    at_or_below = int((current_prices <= own_price).sum())
    percentile = (at_or_below / len(current_prices)) * 100
    return round(float(percentile), 1)


def _median_per_run(priced_df: pd.DataFrame) -> pd.DataFrame:
    """Collapse to one row per run: its observed_at and per-property median price."""
    grouped = priced_df.groupby("rn", as_index=False).agg(
        observed_at=("observed_at", "max"),
        median_price=("min_price", "median"),
    )
    return grouped.sort_values("observed_at")


def _trend_pct(
    run_medians: pd.DataFrame,
    latest_observed_at,
    current_median: float,
    days: int,
    notes: list[str],
) -> float | None:
    """Percent change of the current median vs the run closest to ``days`` ago.

    Picks the historical run whose observed_at is nearest to
    ``latest_observed_at - days`` (and strictly older than the latest run).
    Returns None — with a caveat — when history doesn't span that far.
    """
    if pd.isna(latest_observed_at) or run_medians.empty:
        notes.append(f"History does not span {days} days; {days}-day trend unavailable.")
        return None

    # Το groupby/max μπορεί να επιστρέψει numpy.datetime64. Η άμεση αφαίρεση
    # Pandas Timedelta από numpy scalar χρησιμοποιεί deprecated generic unit.
    latest_timestamp = pd.Timestamp(latest_observed_at)
    target_time = latest_timestamp - pd.Timedelta(days=int(days))
    # Only consider runs at or before the target window (older history).
    candidates = run_medians[run_medians["observed_at"] <= target_time]
    if candidates.empty:
        notes.append(f"History does not span {days} days; {days}-day trend unavailable.")
        return None

    # Closest run to the target window edge. ``candidates`` is sorted oldest-
    # first (via _median_per_run), and idxmin returns the FIRST minimum, so on a
    # tie the older (earlier) run is chosen deterministically.
    time_distance = (candidates["observed_at"] - target_time).abs()
    baseline_row = candidates.loc[time_distance.idxmin()]
    baseline_median = float(baseline_row["median_price"])
    if baseline_median == 0:
        return None

    change_pct = (current_median - baseline_median) / baseline_median * 100
    return round(float(change_pct), 2)


def _statistical_recommendation(
    market_median: float,
    market_p25: float,
    market_p75: float,
    trend_7d_pct: float | None,
) -> float:
    """Trend-adjusted market median, clamped into the [p25, p75] band."""
    adjusted = market_median * (1 + (trend_7d_pct or 0.0) / 100)
    clamped = min(max(adjusted, market_p25), market_p75)
    return round(float(clamped), 2)
```

- [ ] **Step 3.5: Confidence by days in `api/services/price_recommendation_agent.py`**

Replace `deterministic_confidence` with:

```python
def deterministic_confidence(stats: PriceStatistics) -> str:
    """Confidence from data quality alone — never the model's self-assessment.

    Counts distinct calendar DAYS with a run, not runs: five scrapes in one
    afternoon are one market moment (Round 6 §5.2).

    high:   5+ days with a market distribution and a 7-day trend
    medium: 2+ days with a market distribution
    low:    anything thinner
    """
    has_market = stats.market_median_eur is not None
    if stats.sample_days >= 5 and has_market and stats.trend_7d_pct is not None:
        return "high"
    if stats.sample_days >= 2 and has_market:
        return "medium"
    return "low"
```

- [ ] **Step 3.6: Ask the repository for similar hotels in `api/routers/agents.py`**

In the `fetch_price_series(...)` call add, after `room_type_category=room_type_category,`:

```python
        # Similar-only hotels ride along; the statistics decide whether to use them.
        include_similar=True,
```

- [ ] **Step 3.7: Run the three files — all pass**

Same command as Step 3.2. Expected: `13 passed` (statistics), `24 passed` (agent — `_rich_history_stats` spans six distinct days so `high` still holds), `18 passed` (routes).

- [ ] **Step 3.8: Commit**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; git add api/schemas/agents.py api/services/price_statistics_service.py api/services/price_recommendation_agent.py api/routers/agents.py api/tests/test_price_statistics_service.py api/tests/test_price_recommendation_agent.py api/tests/test_price_recommendation_routes.py; git commit -m "Widen thin same-category markets with similar hotels and report position and scope" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 4: Greek — every statistical note, key factor and reasoning; Greek LLM system prompt

**Files:**
- Modify: `api/services/price_statistics_service.py`
- Modify: `api/services/price_recommendation_agent.py`
- Test: `api/tests/test_price_statistics_service.py`, `api/tests/test_price_recommendation_agent.py`

The guardrail sentence (`Business guardrail: max ...`) is NOT touched here — Task 5 replaces it together with the clamp logic and its test.

- [ ] **Step 4.1: Update the tests that pin English and append the Greek ones**

`api/tests/test_price_statistics_service.py` — three one-line replacements:

| Test | Old assertion | New assertion |
|---|---|---|
| `test_single_run_history_has_no_trend_and_a_caveat` | `assert any("7 day" in note.lower() or "history does not span" in note.lower() for note in stats.notes)` | `assert any("η τάση 7 ημερών δεν είναι διαθέσιμη" in note for note in stats.notes)` |
| `test_empty_history_all_none_and_notes` | `assert any("no" in note.lower() for note in stats.notes)` | `assert any(note.startswith("Δεν υπάρχει") for note in stats.notes)` |
| `test_no_own_price_yields_none_percentile_and_note` | `assert any("own" in note.lower() for note in stats.notes)` | `assert any("τιμή αναφοράς για το κατάλυμά σας" in note for note in stats.notes)` |

Append to the same file:

```python
def test_trend_notes_are_greek_for_both_windows():
    rows = [_row(1, LATEST, "p1", 100.0), _row(1, LATEST, "p2", 140.0)]

    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15))

    assert "Το ιστορικό δεν καλύπτει 7 ημέρες· η τάση 7 ημερών δεν είναι διαθέσιμη." in stats.notes
    assert "Το ιστορικό δεν καλύπτει 30 ημέρες· η τάση 30 ημερών δεν είναι διαθέσιμη." in stats.notes


def test_every_note_is_greek_on_every_path():
    empty = compute_price_statistics([], own_price=None, check_in=date(2026, 7, 15))
    unpriced = compute_price_statistics([_row(1, LATEST, "p1", None)], own_price=None, check_in=date(2026, 7, 15))
    single = compute_price_statistics([_row(1, LATEST, "p1", 100.0)], own_price=None, check_in=date(2026, 7, 15))

    for stats in (empty, unpriced, single):
        assert stats.notes
        # Every Greek sentence carries non-ASCII letters; an English leftover would not.
        assert not any(note.isascii() for note in stats.notes)
```

`api/tests/test_price_recommendation_agent.py` — add `PRICE_RECOMMENDATION_PROMPT_VERSION` to the `price_recommendation_agent` import and append:

```python
# ---------------------------------------------------------------------------
# Round 6 — Greek statistical path, Greek LLM answers
# ---------------------------------------------------------------------------


def test_statistical_fallback_speaks_greek_and_does_not_repeat_the_notes():
    stats = _stats()  # 100 / 140, own 120, 1 run, lead time 14 days

    rec = PriceRecommendationAgent(api_key="", model="claude-sonnet-5").recommend(stats, ADVISOR_CONTEXT)

    assert rec.reasoning.startswith("Στατιστική σύσταση (χωρίς AI)")
    # The notes render once, in the statistics panel — never inside the reasoning.
    assert all(note not in rec.reasoning for note in stats.notes)
    assert rec.key_factors == [
        "Διάμεσος αγοράς 120 €",
        "Τιμή αναφοράς σας 120 €",
        "Φθηνότεροι από εσάς: 1 από 2",
        "Χρόνος έως την άφιξη 14 ημέρες",
        "Βάση: 1 αναζήτηση",
    ]


def test_llm_system_prompt_demands_greek_answers():
    fake_client = _FakeClient(result=_FakeParseResult(None, stop_reason="refusal"))
    agent = PriceRecommendationAgent(
        api_key="sk-test", model="claude-sonnet-5", client_factory=lambda: fake_client
    )

    agent.recommend(_stats(), ADVISOR_CONTEXT)

    assert "Απάντησε αποκλειστικά στα ελληνικά" in fake_client.messages.parse_kwargs["system"]
    # A changed prompt is a new prompt version in the audit trail.
    assert PRICE_RECOMMENDATION_PROMPT_VERSION == "2026-09-15.v2"
```

- [ ] **Step 4.2: Run both files — the 4 new tests and the 3 updated ones fail**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_price_statistics_service.py api/tests/test_price_recommendation_agent.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-c"
```

Expected: 7 failures, all `AssertionError` on English text / the old prompt version.

- [ ] **Step 4.3: Translate the notes in `api/services/price_statistics_service.py`**

Exact replacements (the trend sentence and the own-price sentence occur twice each — replace every occurrence):

| Old | New |
|---|---|
| `"No price history available for this market key."` | `"Δεν υπάρχει ιστορικό τιμών για αυτές τις ημερομηνίες και παραμέτρους."` |
| `"No own reference price provided."` | `"Δεν υπάρχει τιμή αναφοράς για το κατάλυμά σας."` |
| `"Price history rows contained no usable prices."` | `"Το ιστορικό τιμών δεν περιέχει αξιοποιήσιμες τιμές."` |
| `"No own reference price provided; own position percentile unavailable."` | `"Δεν υπάρχει τιμή αναφοράς για το κατάλυμά σας· η θέση σας στην αγορά δεν είναι διαθέσιμη."` |
| `f"History does not span {days} days; {days}-day trend unavailable."` | `f"Το ιστορικό δεν καλύπτει {days} ημέρες· η τάση {days} ημερών δεν είναι διαθέσιμη."` |
| `"Only 1 run of history available; trends and momentum are unavailable."` | `"Υπάρχει μόνο 1 αναζήτηση στο ιστορικό· οι τάσεις δεν είναι διαθέσιμες."` |

- [ ] **Step 4.4: Greek prompt, reasoning and key factors in `api/services/price_recommendation_agent.py`**

Replace `PRICE_RECOMMENDATION_PROMPT_VERSION = "2026-07-26.v1"` with `PRICE_RECOMMENDATION_PROMPT_VERSION = "2026-09-15.v2"`.

Replace the last line of `SYSTEM_PROMPT` (`"within a realistic band relative to the observed competitor prices."`) with:

```python
    "within a realistic band relative to the observed competitor prices. "
    "Απάντησε αποκλειστικά στα ελληνικά: γράψε το reasoning και τα key_factors "
    "στα ελληνικά, με ποσά σε ευρώ (π.χ. «120 €»)."
```

In `_statistical_fallback`, replace the `reasoning = (...)` statement with:

```python
        # The statistics notes are NOT repeated here: the page shows them once,
        # in the market-statistics panel (Round 6 §5.1).
        reasoning = (
            "Στατιστική σύσταση (χωρίς AI): η διάμεση τιμή της τρέχουσας αγοράς, "
            "προσαρμοσμένη στην τάση των 7 ημερών και περιορισμένη στο εύρος "
            "P25–P75 των ανταγωνιστών."
        )
```

Replace `_fallback_key_factors` (to the end of the file) with:

```python
def _fallback_key_factors(stats: PriceStatistics) -> list[str]:
    """Greek, human-readable key factors from the non-None statistical signals."""
    factors: list[str] = []
    if stats.market_median_eur is not None:
        factors.append(f"Διάμεσος αγοράς {_eur(stats.market_median_eur)}")
    if stats.own_reference_price_eur is not None:
        factors.append(f"Τιμή αναφοράς σας {_eur(stats.own_reference_price_eur)}")
    if stats.position is not None:
        factors.append(
            f"Φθηνότεροι από εσάς: {stats.position.cheaper_than_you} από {stats.position.total}"
        )
    if stats.trend_7d_pct is not None:
        factors.append(f"Τάση αγοράς 7 ημερών {_pct(stats.trend_7d_pct)}")
    if stats.trend_30d_pct is not None:
        factors.append(f"Τάση αγοράς 30 ημερών {_pct(stats.trend_30d_pct)}")
    factors.append(f"Χρόνος έως την άφιξη {_days(stats.lead_time_days)}")
    factors.append(f"Βάση: {_runs(stats.sample_runs)}")
    return factors


def _eur(value: float) -> str:
    """«120 €» / «92,5 €»: Greek decimal comma, no trailing zeros."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text.replace('.', ',')} €"


def _pct(value: float) -> str:
    return f"{value:+.1f}%".replace(".", ",")


def _days(days: int) -> str:
    return "1 ημέρα" if abs(days) == 1 else f"{days} ημέρες"


def _runs(runs: int) -> str:
    return "1 αναζήτηση" if runs == 1 else f"{runs} αναζητήσεις"
```

- [ ] **Step 4.5: Run both files — all pass**

Same command as Step 4.2. Expected: `15 passed` (statistics), `26 passed` (agent).

- [ ] **Step 4.6: Commit**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; git add api/services/price_statistics_service.py api/services/price_recommendation_agent.py api/tests/test_price_statistics_service.py api/tests/test_price_recommendation_agent.py; git commit -m "Speak Greek in every statistical note, key factor and reasoning" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 5: Guardrail — clamp each bound independently, never collapse the range, Greek note

**Files:**
- Modify: `api/services/price_recommendation_agent.py` (`apply_business_guardrails`)
- Test: `api/tests/test_price_recommendation_agent.py`

- [ ] **Step 5.1: Rewrite the two clamp tests and add the partial-clamp one**

In `api/tests/test_price_recommendation_agent.py` replace `test_business_guardrail_clamps_excessive_price_rise` and `test_business_guardrail_clamps_excessive_price_drop` with:

```python
GUARDRAIL_NOTE = "σε σχέση με την τιμή σας· η σύσταση περιορίζεται στο ±20% (όριο ασφαλείας)"


def _agent_recommendation(recommended, low, high, reasoning="Η ζήτηση είναι ισχυρή."):
    return PriceRecommendation(
        recommended_price_eur=recommended,
        price_range_low_eur=low,
        price_range_high_eur=high,
        confidence="medium",
        reasoning=reasoning,
        key_factors=["Διάμεσος αγοράς 120 €"],
        source="agent",
    )


def test_business_guardrail_clamps_a_rise_without_collapsing_the_range():
    # Own 120 -> band [96, 144]. 180 / 160 / 200 all clamp to 144; the band is
    # reopened to -5 % around the recommendation instead of «144 € – 144 €».
    guarded = apply_business_guardrails(_agent_recommendation(180, 160, 200), _stats(), 20)

    assert guarded.recommended_price_eur == 144
    assert guarded.price_range_low_eur == 136.8
    assert guarded.price_range_high_eur == 144
    note = f"Η αγορά είναι +50% {GUARDRAIL_NOTE}"
    assert guarded.key_factors == ["Διάμεσος αγοράς 120 €", note]
    assert guarded.reasoning == f"Η ζήτηση είναι ισχυρή. {note}."


def test_business_guardrail_clamps_a_drop_without_collapsing_the_range():
    # 60 / 50 / 80 all clamp to 96; reopened upwards only (96 is the band floor).
    guarded = apply_business_guardrails(_agent_recommendation(60, 50, 80), _stats(), 20)

    assert guarded.recommended_price_eur == 96
    assert guarded.price_range_low_eur == 96
    assert guarded.price_range_high_eur == 100.8
    assert guarded.key_factors[-1].startswith("Η αγορά είναι -50% ")


def test_business_guardrail_clamps_each_bound_independently():
    # 150 / 130 / 160: only the recommendation and the high bound exceed 144;
    # the low bound (130) is inside the band and must survive untouched.
    guarded = apply_business_guardrails(_agent_recommendation(150, 130, 160), _stats(), 20)

    assert (guarded.recommended_price_eur, guarded.price_range_low_eur, guarded.price_range_high_eur) == (144, 130, 144)
    assert guarded.key_factors[-1].startswith("Η αγορά είναι +25% ")
```

- [ ] **Step 5.2: Run the agent tests — the 3 guardrail tests fail**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_price_recommendation_agent.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-c"
```

Expected: 3 failures — `price_range_low_eur == 136.8` gets 144, the drop gets 96 as high, the partial clamp folds `low` onto 130 → 144 (`AssertionError`).

- [ ] **Step 5.3: Replace `apply_business_guardrails` in `api/services/price_recommendation_agent.py`**

```python
def apply_business_guardrails(
    recommendation: PriceRecommendation | None,
    stats: PriceStatistics,
    max_change_pct: float,
) -> PriceRecommendation | None:
    """Clamp a recommendation to the configured move from the own reference.

    ``recommended``, ``low`` and ``high`` are clamped INDEPENDENTLY into
    ``[own × (1 − max %), own × (1 + max %)]``. The old clamp folded both
    bounds onto the recommended price, so a 92 € hotel in a 160 € market read
    «160 € – 160 €». If the band still collapses it is reopened to ±5 % around
    the recommendation, inside the business band (Round 6 §5.2). Without an
    own price nothing is applied.
    """
    own_price = stats.own_reference_price_eur
    if recommendation is None or own_price is None or own_price <= 0 or max_change_pct <= 0:
        return recommendation

    # Επιχειρηματικό όριο: καμία αυτόματη σύσταση δεν αλλάζει απότομα την τιμή βάσης.
    band_low = own_price * max(0.0, 1.0 - max_change_pct / 100.0)
    band_high = own_price * (1.0 + max_change_pct / 100.0)

    def clamp(value: float) -> float:
        return min(max(value, band_low), band_high)

    recommended = clamp(recommendation.recommended_price_eur)
    low = clamp(recommendation.price_range_low_eur)
    high = clamp(recommendation.price_range_high_eur)
    if (recommended, low, high) == (
        recommendation.recommended_price_eur,
        recommendation.price_range_low_eur,
        recommendation.price_range_high_eur,
    ):
        return recommendation
    if low == high:
        low = max(band_low, recommended * 0.95)
        high = min(band_high, recommended * 1.05)

    # NN is the unclamped recommendation's distance from the own price — the
    # number the guardrail actually moved (on the statistical path the
    # recommendation IS the market figure).
    market_vs_own_pct = (recommendation.recommended_price_eur - own_price) / own_price * 100.0
    note = (
        f"Η αγορά είναι {market_vs_own_pct:+.0f}% σε σχέση με την τιμή σας· "
        f"η σύσταση περιορίζεται στο ±{max_change_pct:.0f}% (όριο ασφαλείας)"
    )
    return recommendation.model_copy(
        update={
            "recommended_price_eur": round(recommended, 2),
            "price_range_low_eur": round(low, 2),
            "price_range_high_eur": round(high, 2),
            "reasoning": f"{recommendation.reasoning} {note}.",
            "key_factors": [*recommendation.key_factors, note],
        }
    )
```

- [ ] **Step 5.4: Run the agent tests — all pass**

Same command as Step 5.2. Expected: `27 passed` (`test_business_guardrail_preserves_safe_recommendation` still gets the identical object back).

- [ ] **Step 5.5: Commit**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; git add api/services/price_recommendation_agent.py api/tests/test_price_recommendation_agent.py; git commit -m "Keep the 20% guardrail from collapsing the price range and explain it in Greek" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 6: Cache key — include the latest completed run id of the market key

**Files:**
- Modify: `api/routers/agents.py`
- Test: `api/tests/test_price_recommendation_routes.py`

- [ ] **Step 6.1: Extend the fake and append the failing tests**

In `FakePriceHistoryRepository` (routes test file) change the constructor to `def __init__(self, rows, own_live_price=None, latest_run_id=None):`, add `self.latest_run_id = latest_run_id` and `self.run_id_calls = []`, and add the method:

```python
    def fetch_latest_completed_run_id(self, **kwargs):
        self.run_id_calls.append(kwargs)
        return self.latest_run_id
```

Append at the end of the file:

```python
# ---------------------------------------------------------------------------
# Round 6 — a new scrape is a new cache key
# ---------------------------------------------------------------------------


def _cached_audit():
    return {
        "id": UUID("00000000-0000-0000-0000-000000000999"),
        "response_payload": {
            "statistics": {"sample_runs": 1, "lead_time_days": 30, "notes": []},
            "recommendation": None,
            "recommendation_available": False,
        },
        "model_version": "statistical-v1",
        "prompt_version": "v1",
        "created_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
    }


def test_cache_key_changes_with_the_latest_completed_run():
    audit_repo = FakeAuditRepository()
    hashes = []
    for run_id in ("run-1", "run-2"):
        history_repo = FakePriceHistoryRepository(_history_rows(), latest_run_id=run_id)
        client = _round6_client(history_repo, audit_repo=audit_repo)

        assert _pricing_request(client).status_code == 200

        hashes.append(audit_repo.insert_calls[-1]["request_hash"])
        assert audit_repo.insert_calls[-1]["request_payload"]["latest_run_id"] == run_id
        assert history_repo.run_id_calls[0]["canonical_destination"] == "faliraki"
        assert history_repo.run_id_calls[0]["check_in"].isoformat() == "2030-07-15"
    assert hashes[0] != hashes[1]
    app.dependency_overrides.clear()


def test_cache_key_is_stable_while_no_new_run_completes():
    audit_repo = FakeAuditRepository()
    for _ in range(2):
        history_repo = FakePriceHistoryRepository(_history_rows(), latest_run_id="run-1")
        _pricing_request(_round6_client(history_repo, audit_repo=audit_repo))

    first, second = (call["request_hash"] for call in audit_repo.insert_calls)
    assert first == second
    app.dependency_overrides.clear()


def test_latest_run_is_resolved_before_the_cache_lookup():
    history_repo = FakePriceHistoryRepository(_history_rows(), latest_run_id="run-1")
    client = _round6_client(history_repo, audit_repo=FakeAuditRepository(cached=_cached_audit()))

    assert _pricing_request(client).json()["cached"] is True
    # The run id is part of the key, so it is read on every request ...
    assert len(history_repo.run_id_calls) == 1
    # ... while a cache hit still reads no price series and no own live price.
    assert history_repo.last_kwargs is None
    assert history_repo.own_price_kwargs is None
    app.dependency_overrides.clear()
```

- [ ] **Step 6.2: Run the route tests — the 3 new ones fail**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_price_recommendation_routes.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-c"
```

Expected: `KeyError: 'latest_run_id'`, `assert hashes[0] != hashes[1]` fails (identical hashes), `len(history_repo.run_id_calls) == 1` fails with 0.

- [ ] **Step 6.3: Resolve the run id before the cache lookup in `api/routers/agents.py`**

Replace the `audit_request_payload = {...}` statement with:

```python
    # A new scrape must yield a new recommendation inside the cache window, so
    # the latest completed run of this market key is part of the request identity
    # (None when the market has never been scraped — still a stable key).
    latest_run_id = price_history_repository.fetch_latest_completed_run_id(
        account_id=account.account_id,
        canonical_destination=canonical_destination,
        check_in=request.check_in,
        check_out=request.check_out,
        adults=request.adults,
        children=request.children,
        rooms=request.rooms,
    )
    audit_request_payload = {
        **request.model_dump(mode="json"),
        "room_type_category": room_type_category,
        "canonical_destination": canonical_destination,
        "latest_run_id": latest_run_id,
    }
```

- [ ] **Step 6.4: Run the route tests — all pass**

Same command as Step 6.2. Expected: `21 passed` (the pre-existing cache and quota tests still see `last_kwargs is None`: the run-id lookup is recorded separately).

- [ ] **Step 6.5: Commit**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; git add api/routers/agents.py api/tests/test_price_recommendation_routes.py; git commit -m "Fold the latest completed run into the recommendation cache key" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 7: Alerts — compare against a run at least 12 hours old

**Files:**
- Modify: `api/services/price_alert_service.py`
- Test: `api/tests/test_price_alert_service.py`

- [ ] **Step 7.1: Append the failing tests**

Change the datetime import of `api/tests/test_price_alert_service.py` to `from datetime import date, datetime, timedelta, timezone` and append:

```python
# ---------------------------------------------------------------------------
# Round 6 — a rerun minutes later is the same market moment, not a change
# ---------------------------------------------------------------------------


def _gap_row(property_id, current, previous, gap: timedelta):
    observed_at = datetime(2026, 6, 14, 12, tzinfo=timezone.utc)
    return {
        **_history_row(property_id, current, previous),
        "observed_at": observed_at,
        "previous_observed_at": observed_at - gap,
    }


def test_service_asks_for_a_previous_run_at_least_twelve_hours_old():
    history = FakeHistoryRepository(rows=[])
    service = _service(FakeAlertsRepository(), history, FakeTrackingRepository(_tracked(PROP_A)), FakeNotifier())

    service.evaluate_completed_job(ACCOUNT_ID, FakeJob())

    assert history.calls[0]["min_gap_hours"] == 12


def test_a_rerun_three_minutes_later_raises_no_alert():
    # 100 -> 213 would be «+113%» — exactly the demo bug — but 3 minutes is no gap.
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(rows=[_gap_row(PROP_A, Decimal("213"), Decimal("100"), timedelta(minutes=3))])
    notifier = FakeNotifier()
    service = _service(alerts, history, FakeTrackingRepository(_tracked(PROP_A)), notifier)

    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 0
    assert alerts.inserted_rows == []
    assert notifier.published == []


def test_a_run_thirteen_hours_later_raises_the_alert():
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(rows=[_gap_row(PROP_A, Decimal("213"), Decimal("100"), timedelta(hours=13))])
    service = _service(alerts, history, FakeTrackingRepository(_tracked(PROP_A)), FakeNotifier())

    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 1
    assert alerts.inserted_rows[0][0]["payload"]["change_pct"] == 113.0


def test_rows_without_a_previous_timestamp_trust_the_repository():
    # The SQL already picked a run that is old enough; a row that carries no
    # previous_observed_at (older fakes, NULL column) is not rejected here.
    alerts = FakeAlertsRepository()
    history = FakeHistoryRepository(rows=[_history_row(PROP_A, Decimal("80"), Decimal("100"))])
    service = _service(alerts, history, FakeTrackingRepository(_tracked(PROP_A)), FakeNotifier())

    assert service.evaluate_completed_job(ACCOUNT_ID, FakeJob()) == 1
```

- [ ] **Step 7.2: Run the alert tests — 2 of the new ones fail**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; $env:PYTHONUTF8=1; python -m pytest api/tests/test_price_alert_service.py -q -p no:cacheprovider --basetemp="C:\Users\vagel\AppData\Local\Temp\claude\C--vscode-code-room-project2\c750232c-6e24-4fa0-9c72-fe95624ecc60\scratchpad\pytest-c"
```

Expected: `KeyError: 'min_gap_hours'` and the 3-minute test alerting (`assert 1 == 0`); the 13-hour and no-timestamp tests already pass.

- [ ] **Step 7.3: Implement in `api/services/price_alert_service.py`**

Change the datetime import to `from datetime import date, datetime, timedelta`.

After `SCHEDULE_DISABLED_TYPE = "schedule_disabled"` add:

```python
# The "previous" run of an alert comparison must be at least this old.
# Scheduled scrapes rerun every few minutes; comparing two of them reported a
# «+113%» move that was really Booking's intra-hour noise (Round 6 §5.2).
ALERT_MIN_GAP_HOURS = 12.0
```

In `PriceHistoryRepositoryProtocol.fetch_latest_vs_previous` add the parameter `min_gap_hours: float | None = None,` after `property_ids`.

In `evaluate_completed_job`, add `min_gap_hours=ALERT_MIN_GAP_HOURS,` to the `fetch_latest_vs_previous(...)` call (after `property_ids=tracked_property_ids,`), and replace the `# 3) Need both prices ...` guard with:

```python
            # 3) Need both prices, a positive baseline and a real time gap.
            if current_price is None or previous_price is None or previous_price <= 0:
                continue
            if not self._gap_is_wide_enough(history_row):
                continue
```

Add to the internals section (before `_default_rule`):

```python
    @staticmethod
    def _gap_is_wide_enough(history_row: dict) -> bool:
        """Second line of defence for the 12-hour rule (the SQL is the first).

        Rows that carry both timestamps are re-checked here so the rule is
        unit-testable with fakes; a row without ``previous_observed_at`` is
        trusted — the repository already picked a run that is old enough.
        """
        observed_at = history_row.get("observed_at")
        previous_observed_at = history_row.get("previous_observed_at")
        if not isinstance(observed_at, datetime) or not isinstance(previous_observed_at, datetime):
            return True
        return observed_at - previous_observed_at >= timedelta(hours=ALERT_MIN_GAP_HOURS)
```

- [ ] **Step 7.4: Run the alert tests — all pass**

Same command as Step 7.2. Expected: `27 passed`.

- [ ] **Step 7.5: Commit**

```powershell
cd C:\vscode_code\wt-round6-c\room_project2; git add api/services/price_alert_service.py api/tests/test_price_alert_service.py; git commit -m "Compare price alerts against a run at least twelve hours old" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

<!-- next-task -->
