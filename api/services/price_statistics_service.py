"""Deterministic statistical baseline for the hybrid price-prediction agent.

This module is pure: it does no I/O and never calls an LLM. It turns the raw
price-history rows produced by ``PriceHistoryRepository.fetch_price_series``
into a :class:`PriceStatistics` snapshot that the LLM agent uses as grounding
and that the statistical fallback uses directly when no LLM is configured.

The function is defensive against empty/short history — every derived figure is
``None`` when the input doesn't support it, and ``notes`` explains why.

Owner decision 2026-09-30: rows fetched with an ``owned_room_type_id`` carry
``agent_basis``; when the latest run's job has room-matching agent rows, the
agent-comparable rooms ARE the basis (``stats_scope.used == "agent"``) and the
widening below never applies.

Round 6: rows carry two minimums per (run, hotel) — ``min_price_same`` (the
baseline category's comparable pool) and ``min_price_similar`` (every other
category except single). The statistics use same-category hotels; when the
latest run has fewer than ``MIN_SAME_CATEGORY_HOTELS`` of them and similar
hotels exist, every hotel counts once: at its same-category price when it has
one, otherwise at its similar-category price.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from api.schemas.agents import PricePosition, PriceStatistics, PriceStatsScope

# Below this many same-category hotels a median is noise; similar hotels are
# pulled in (Round 6 §5.2).
MIN_SAME_CATEGORY_HOTELS = 5

# Below this many hotels priced in BOTH compared runs a few repricings would
# read as a market move, so the same-store trend is not computed.
MIN_MATCHED_HOTELS = 5

# Calendar days are the owner's days: a scrape at 01:30 in Athens belongs to
# that morning, not to the previous UTC date.
OWNER_TIMEZONE = "Europe/Athens"


def compute_price_statistics(
    history_rows: list[dict],
    own_price: float | None,
    check_in: date,
    as_of: date | None = None,
    own_cancellation_type: str | None = None,
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
        own_cancellation_type: The own reference package's cancellation class,
            when the rows were fetched like-for-like (spec 2026-09-29 §4).
            Combined with the rows' ``cancellation_matched_same`` /
            ``cancellation_matched_similar`` flags it decides
            ``stats_scope.cancellation_class`` ("matched" | "all").

    Returns:
        A populated :class:`PriceStatistics`. Optional figures are None when the
        history is too sparse, with explanatory caveats appended to ``notes``.
    """
    reference_date = as_of or date.today()
    lead_time_days = (check_in - reference_date).days
    notes: list[str] = []

    # Empty history: nothing to compute. Still return a valid, fully-None-ish
    # snapshot so the caller (and the LLM fallback) can degrade gracefully.
    if not history_rows:
        notes.append("Δεν υπάρχει ιστορικό τιμών για αυτές τις ημερομηνίες και παραμέτρους.")
        if own_price is None:
            notes.append("Δεν υπάρχει τιμή αναφοράς για το κατάλυμά σας.")
        return PriceStatistics(
            sample_runs=0,
            own_reference_price_eur=own_price,
            lead_time_days=lead_time_days,
            notes=notes,
        )

    any_priced_df = _priced_history(history_rows)

    if _latest_agent_run_without_basis(history_rows, any_priced_df):
        # The matching agent judged no room of the newest search comparable,
        # so the map shows no comparable hotel for it either. An older search
        # is not today's market: nothing current to price on.
        notes.append(
            "Στην τελευταία αναζήτηση η εκτίμηση AI δεν βρήκε δωμάτιο συγκρίσιμο "
            "με το δικό σας· δεν υπάρχει τρέχουσα βάση σύγκρισης."
        )
        if own_price is None:
            notes.append("Δεν υπάρχει τιμή αναφοράς για το κατάλυμά σας.")
        return PriceStatistics(
            sample_runs=0,
            own_reference_price_eur=own_price,
            stats_scope=PriceStatsScope(same_category=0, similar=0, used="agent", comparable=0),
            lead_time_days=lead_time_days,
            notes=notes,
        )

    if any_priced_df.empty:
        notes.append("Το ιστορικό τιμών δεν περιέχει αξιοποιήσιμες τιμές.")
        if own_price is None:
            notes.append("Δεν υπάρχει τιμή αναφοράς για το κατάλυμά σας.")
        return PriceStatistics(
            sample_runs=0,
            own_reference_price_eur=own_price,
            lead_time_days=lead_time_days,
            notes=notes,
        )

    latest_run_index, stats_scope, priced_df = _select_basis(any_priced_df, own_cancellation_type)

    # ``rn`` is the repository's LOGICAL run (dense_rank by finished_at), so the
    # per-city fragments of one job count once here.
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
        notes.append(
            "Δεν υπάρχει τιμή αναφοράς για το κατάλυμά σας· "
            "η θέση σας στην αγορά δεν είναι διαθέσιμη."
        )

    # Trends are same-store: the latest run against the run of the same
    # pricing method closest to N days before it, over the hotels priced in
    # BOTH (live check 2026-09-30: a 10-hotel run against a 91-hotel one read
    # as «+40%» with flat prices). None when history doesn't reach back that
    # far, every older run was priced another way, or too few hotels match.
    latest_observed_at = priced_df.loc[priced_df["rn"] == latest_run_index, "observed_at"].max()
    run_timeline = _run_timeline(priced_df, any_priced_df)
    hotel_prices = _price_per_run_and_hotel(priced_df)

    trend_7d_pct = _trend_pct(
        run_timeline, hotel_prices, latest_run_index, latest_observed_at, days=7, notes=notes
    )
    trend_30d_pct = _trend_pct(
        run_timeline, hotel_prices, latest_run_index, latest_observed_at, days=30, notes=notes
    )

    # Statistical recommendation: nudge the current median by the 7d trend, then
    # clamp into the current interquartile band so it stays market-realistic.
    statistical_recommendation = _statistical_recommendation(
        market_median, market_p25, market_p75, trend_7d_pct
    )

    if sample_runs == 1:
        notes.append("Υπάρχει μόνο 1 αναζήτηση στο ιστορικό· οι τάσεις δεν είναι διαθέσιμες.")

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


def basis_price_rows(history_rows: list[dict]) -> list[dict]:
    """The history rows that FORM the statistical basis, each at its basis price.

    The price-history chart reads this so chart and statistics can never
    disagree (owner report 2026-09-30: chart median 59 EUR over same-category
    hotels, recommendation 82 EUR over the widened set). Same scope decision
    as ``compute_price_statistics``: the agent's comparable rooms when the
    latest run has agent matches, else same-category hotels widened with
    similar-only ones below ``MIN_SAME_CATEGORY_HOTELS``. The original rows
    come back (input order, original ``observed_at``) with ``min_price`` set
    to the basis price; rows outside the basis are dropped.
    """
    if not history_rows:
        return []
    any_priced_df = _priced_history(history_rows)
    if any_priced_df.empty:
        return []
    _, _, priced_df = _select_basis(any_priced_df, None)
    return [
        {**history_rows[int(position)], "min_price": float(price)}
        for position, price in priced_df["min_price"].items()
    ]


def _priced_history(history_rows: list[dict]) -> pd.DataFrame:
    """Rows with a run index and at least one price, numeric/time columns coerced.

    The frame keeps the default RangeIndex, so each label is the row's
    position in ``history_rows`` (``basis_price_rows`` maps back through it).
    """
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
    return history_df[
        history_df["rn"].notna()
        & (history_df["min_price_same"].notna() | history_df["min_price_similar"].notna())
    ]


def _latest_agent_run_without_basis(
    history_rows: list[dict], any_priced_df: pd.DataFrame
) -> bool:
    """Whether the newest search is on the agent basis with no basis price at all.

    ``rn`` is the repository's dense rank over EVERY completed run of the
    market key, so rn = 1 is the newest search whether or not any of its
    hotels has a basis price; on the agent basis the repository returns its
    hotels either way. Without this check the smallest PRICED rank (an
    older search) would silently become the current market.
    """
    history_df = pd.DataFrame(history_rows)
    if "rn" not in history_df or "agent_basis" not in history_df:
        return False
    latest_df = history_df[pd.to_numeric(history_df["rn"], errors="coerce") == 1]
    if latest_df.empty or not _flag_column(latest_df, "agent_basis").any():
        return False
    return not bool((any_priced_df["rn"] == 1).any())


def _select_basis(
    any_priced_df: pd.DataFrame, own_cancellation_type: str | None
) -> tuple[float, PriceStatsScope, pd.DataFrame]:
    """Latest run index, its scope, and every run's rows at their basis price.

    The most-recent run (smallest rn) is the current market snapshot. It
    alone decides the scope, which then applies to every run so trends
    compare like with like.
    """
    latest_run_index = any_priced_df["rn"].min()
    stats_scope = _stats_scope(
        any_priced_df[any_priced_df["rn"] == latest_run_index], own_cancellation_type
    )
    # No note for the widened basis: ``stats_scope`` carries it and the page
    # renders the basis line once.
    if stats_scope.used == "same_plus_similar":
        # A hotel with a same-category offer keeps THAT price; its cheaper studio
        # would understate what it charges for the owner's room type.
        effective_price = any_priced_df["min_price_same"].combine_first(
            any_priced_df["min_price_similar"]
        )
    else:
        # "same", and "agent": the repository already priced agent runs over
        # the agent-comparable rooms in min_price_same (similar is NULL there).
        effective_price = any_priced_df["min_price_same"]
    priced_df = any_priced_df.assign(min_price=effective_price).dropna(subset=["min_price"])
    return latest_run_index, stats_scope, priced_df


def _stats_scope(latest_df: pd.DataFrame, own_cancellation_type: str | None) -> PriceStatsScope:
    """Scope of the latest run: same-category hotels, widened below the minimum.

    ``similar`` counts the hotels that ONLY offer similar categories (the ones
    widening adds); a hotel with both kinds is a same-category hotel.

    ``cancellation_class`` is "matched" only when the own class is known AND a
    row of that exact class sits among the rows that FORM the statistical
    basis — decided here, AFTER the widening decision: under "same" only the
    same-pool flags count; under "same_plus_similar" a hotel with a
    same-category price still counts through its same-pool flag (that price
    is its basis) while similar-only hotels count through their similar flag.
    A match in a similar-only hotel that widening never used, rows that
    predate the flags, and markets where nothing matched all report "all".
    """
    has_same = latest_df["min_price_same"].notna()
    same_count = int(has_same.sum())
    if _flag_column(latest_df, "agent_basis").any():
        # Agent basis (owner decision 2026-09-30): the matching agent chose
        # the set, so there is no widening; the basis is the hotels with an
        # agent-comparable price and their same-pool flags.
        agent_matched = bool(own_cancellation_type) and bool(
            (has_same & _flag_column(latest_df, "cancellation_matched_same")).any()
        )
        return PriceStatsScope(
            same_category=same_count,
            similar=0,
            used="agent",
            cancellation_class="matched" if agent_matched else "all",
            comparable=same_count,
        )
    similar_count = int((~has_same & latest_df["min_price_similar"].notna()).sum())
    widen = same_count < MIN_SAME_CATEGORY_HOTELS and similar_count > 0
    cancellation_matched = False
    if own_cancellation_type:
        basis_matched = has_same & _flag_column(latest_df, "cancellation_matched_same")
        if widen:
            basis_matched = basis_matched | (
                ~has_same & _flag_column(latest_df, "cancellation_matched_similar")
            )
        cancellation_matched = bool(basis_matched.any())
    return PriceStatsScope(
        same_category=same_count,
        similar=similar_count,
        used="same_plus_similar" if widen else "same",
        cancellation_class="matched" if cancellation_matched else "all",
    )


def _flag_column(frame: pd.DataFrame, name: str) -> pd.Series:
    """The named flag as booleans; all-False when the column is absent.

    SQLite hands the repository flags back as 0/1, PostgreSQL as bool, and
    legacy rows lack them entirely or carry None: the map folds every
    spelling without pandas' deprecated object-dtype fillna downcasting.
    """
    if name in frame:
        return frame[name].map(lambda value: bool(value) if pd.notna(value) else False).astype(bool)
    return pd.Series(False, index=frame.index)


def _position(current_prices: pd.Series, own_price: float | None) -> PricePosition | None:
    """«Φθηνότερα από εσάς»: hotels strictly below the own price, out of all of them."""
    if own_price is None or current_prices.empty:
        return None
    return PricePosition(
        cheaper_than_you=int((current_prices < own_price).sum()),
        total=int(len(current_prices)),
    )


def _distinct_days(priced_df: pd.DataFrame) -> int:
    """Distinct Athens calendar days with a run; a run without a timestamp still counts as one."""
    observed = priced_df["observed_at"].dropna()
    days = int(observed.dt.tz_convert(OWNER_TIMEZONE).dt.date.nunique()) if not observed.empty else 0
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


def _run_timeline(priced_df: pd.DataFrame, any_priced_df: pd.DataFrame) -> pd.DataFrame:
    """One row per run, oldest first: ``rn``, ``observed_at`` and its pricing method.

    The method is two per-run flags over every priced row: ``agent_basis``
    (agent-comparable rooms vs the category pool, set per logical run by the
    repository) and ``has_discount_data`` (any hotel priced off a discounted
    package; runs before migration 0025 stored none, so theirs are base
    prices). Rows that predate a flag read False. ``observed_at`` comes from
    the rows with a basis price, so a run without one (NaT) is never a
    trend baseline.
    """
    method = (
        any_priced_df.assign(
            agent_basis=_flag_column(any_priced_df, "agent_basis"),
            has_discount_data=_flag_column(any_priced_df, "has_discount_data"),
        )
        .groupby("rn")[["agent_basis", "has_discount_data"]]
        .max()
    )
    observed_at = priced_df.groupby("rn")["observed_at"].max()
    return method.assign(observed_at=observed_at).reset_index().sort_values("observed_at")


def _price_per_run_and_hotel(priced_df: pd.DataFrame) -> pd.DataFrame:
    """Each run's basis price per hotel: columns ``rn``, ``property_id``, ``min_price``.

    These are the very prices the market statistics use (the basis decided by
    the latest run, effective, like-for-like class). The repository returns
    one row per (run, hotel) already; the min only guards against a hotel
    repeated across one logical run's city fragments. Rows without a
    ``property_id`` cannot be matched across runs and drop out.
    """
    if "property_id" not in priced_df:
        return pd.DataFrame(columns=["rn", "property_id", "min_price"])
    keyed = priced_df.dropna(subset=["property_id"])
    # UUID objects (PostgreSQL) and strings (fixtures, JSON) must match alike.
    keyed = keyed.assign(property_id=keyed["property_id"].astype(str))
    return keyed.groupby(["rn", "property_id"], as_index=False)["min_price"].min()


def _trend_pct(
    run_timeline: pd.DataFrame,
    hotel_prices: pd.DataFrame,
    latest_run_index: float,
    latest_observed_at,
    days: int,
    notes: list[str],
) -> float | None:
    """Same-store percent change vs the run closest to ``days`` ago.

    Picks the historical run of the SAME pricing method (basis and price
    definition, see ``_run_timeline``) whose observed_at is nearest to
    ``latest_observed_at - days`` (at or before that edge), then compares
    ONLY the hotels priced in both runs: the median of their per-hotel
    changes ``(current / baseline - 1) * 100``. Comparing the two runs'
    medians would read a change of sample (other hotels, more hotels) as a
    market move. Returns None — with a caveat — when history doesn't span
    that far, every older run was priced another way, or fewer than
    ``MIN_MATCHED_HOTELS`` hotels appear in both runs.
    """
    if pd.isna(latest_observed_at) or run_timeline.empty:
        notes.append(f"Το ιστορικό δεν καλύπτει {days} ημέρες· η τάση {days} ημερών δεν είναι διαθέσιμη.")
        return None

    # Το groupby/max μπορεί να επιστρέψει numpy.datetime64. Η άμεση αφαίρεση
    # Pandas Timedelta από numpy scalar χρησιμοποιεί deprecated generic unit.
    latest_timestamp = pd.Timestamp(latest_observed_at)
    target_time = latest_timestamp - pd.Timedelta(days=int(days))
    # Only consider runs at or before the target window (older history).
    candidates = run_timeline[run_timeline["observed_at"] <= target_time]
    if candidates.empty:
        notes.append(f"Το ιστορικό δεν καλύπτει {days} ημέρες· η τάση {days} ημερών δεν είναι διαθέσιμη.")
        return None

    # A pair is only valid between runs priced the same way: agent-comparable
    # rooms against the category pool, or discounted prices against the
    # base-only prices before migration 0025, would read a change of
    # definition as a market move. The latest run is always in the timeline.
    current_run = run_timeline[run_timeline["rn"] == latest_run_index].iloc[0]
    eligible = candidates[
        (candidates["agent_basis"] == current_run["agent_basis"])
        & (candidates["has_discount_data"] == current_run["has_discount_data"])
    ]
    if eligible.empty:
        notes.append(
            "Οι προηγούμενες αναζητήσεις έγιναν με διαφορετική μέθοδο — "
            f"η τάση {days} ημερών δεν υπολογίζεται."
        )
        return None

    # Closest eligible run to the target window edge. The timeline is sorted
    # oldest-first (via _run_timeline), and idxmin returns the FIRST minimum,
    # so on a tie the older (earlier) run is chosen deterministically.
    time_distance = (eligible["observed_at"] - target_time).abs()
    baseline_run_index = eligible.loc[time_distance.idxmin(), "rn"]

    current = hotel_prices[hotel_prices["rn"] == latest_run_index].set_index("property_id")["min_price"]
    baseline = hotel_prices[hotel_prices["rn"] == baseline_run_index].set_index("property_id")["min_price"]
    # Inner join on the hotel: the same hotels at both moments. A hotel with a
    # zero baseline has no percentage change and cannot count.
    matched = pd.concat({"current": current, "baseline": baseline}, axis=1, join="inner")
    matched = matched[matched["baseline"] > 0]
    if len(matched) < MIN_MATCHED_HOTELS:
        notes.append(
            f"Λίγα κοινά καταλύματα μεταξύ των αναζητήσεων ({len(matched)}) — "
            f"η τάση {days} ημερών δεν υπολογίζεται."
        )
        return None

    per_hotel_change_pct = (matched["current"] / matched["baseline"] - 1) * 100
    return round(float(per_hotel_change_pct.median()), 2)


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
