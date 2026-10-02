"""LLM price-recommendation agent with a statistical fallback.

The agent wraps an Anthropic structured-output call. If no API key is
configured, or the LLM call fails, refuses, or returns nothing, it falls back
to a deterministic recommendation derived from the statistical baseline.
``recommend`` never raises.

Every LLM answer passes a deterministic post-validator before it may reach a
user: prices must be positive, the low/recommended/high ordering must hold,
and the recommended price must sit inside a market-derived plausibility band.
A failing answer is replaced by the statistical baseline. Confidence is NEVER
the model's self-assessment — it is computed from data quality alone, so the
same market history always yields the same confidence.

When the underlying data cannot anchor ANY number (no history, no market
distribution, no own reference price), ``recommend`` returns ``None`` and the
endpoint reports ``recommendation_available=false`` instead of a made-up €0.

Anthropic SDK call shape (authoritative — keep it minimal):
    client.messages.parse(
        model=..., max_tokens=2048, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": <json str>}],
        output_format=PriceRecommendation,
        output_config={"effort": "medium"},
    )
Only ``model``, ``max_tokens``, ``system``, ``messages``, ``output_format``,
``output_config`` are sent. ``thinking`` is NEVER passed: it is always on for
claude-opus-5-5 and any explicit value would 400 — as would
``temperature``/``top_p``/``top_k``. ``output_config`` carries only
``effort`` (spec 2026-09-29 Β.1: verified against the INSTALLED anthropic
0.115.0, whose ``messages.parse`` signature accepts ``output_config`` and
merges ``output_format`` into it as ``format`` on the wire). "medium" is
claude-opus-5-5's own default, pinned explicitly so a future model-side
recalibration cannot silently change this synchronous endpoint's latency.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from collections.abc import Callable
from typing import Any

from api.schemas.agents import PriceRecommendation, PriceStatistics

logger = logging.getLogger(__name__)

# Max competitors forwarded to the model. Bounds the token budget for what is a
# synchronous endpoint; the statistical signals already summarize the full set.
_MAX_COMPETITORS = 15
# v4: the advisor context is the full comparison basis (tracked flag per
# competitor, basis/shown counts) — a v3 answer read a tracked-only list.
# v5: the room-matching agent's comparable rooms are the basis when present
# (stats_scope.used == "agent"); a v4 answer read the category pool.
# v6: every price is the EFFECTIVE one (discounted when shown, else base); a
# v5 answer read base-price medians overstated by the online discounts.
# v7: trend_7d_pct / trend_30d_pct are same-store (hotels priced in both
# compared runs, runs of the same basis and price definition only); a v6
# answer read a median-vs-median sample change as a trend.
PRICE_RECOMMENDATION_PROMPT_VERSION = "2026-09-30.v7"

# Greek annotation of the statistical basis (spec 2026-09-29 Β.2): the payload
# spells out what stats_scope.cancellation_class means so the model reads the
# statistics on the right basis instead of guessing.
_CANCELLATION_CLASS_NOTES = {
    "matched": (
        "Στατιστική βάση: οι ελάχιστες τιμές ανά κατάλυμα μετρήθηκαν στην "
        "ίδια κλάση ακύρωσης με το πακέτο αναφοράς (cancellation_class=matched)."
    ),
    "all": (
        "Στατιστική βάση: οι ελάχιστες τιμές ανά κατάλυμα καλύπτουν όλες τις "
        "πολιτικές ακύρωσης (cancellation_class=all)."
    ),
}

SYSTEM_PROMPT = (
    "You are a hotel revenue-management analyst. Recommend a single nightly room "
    "price in EUR for the owner's selected room category, plus a low/high price "
    "range, a confidence level, concise reasoning, and the key factors that drove "
    "the recommendation. Ground every number ONLY in the statistics and market "
    "context provided in the user message — do not invent data or use outside "
    "knowledge. Prefer pricing near the market median, adjusted for the owner's "
    "current position, recent trend, and lead time. Keep the recommendation "
    "within a realistic band relative to the observed competitor prices. "
    # Β.2: like-for-like beats headline — a flexible-rate market read against a
    # non-refundable headline minimum would skew the recommendation.
    "Όταν οι τιμές στην ίδια κλάση ακύρωσης (cheapest_same_cancellation_eur) "
    "διαφέρουν από τις γενικές ελάχιστες τιμές, στάθμισε περισσότερο τις "
    "τιμές της ίδιας κλάσης ακύρωσης. "
    # v4: the list is a capped sample of the basis and tracked is a subset of
    # it — neither is the market size the statistics were computed on.
    "Η λίστα market_context.competitors δείχνει competitors_shown από τα "
    "competitors_in_basis καταλύματα της πλήρους βάσης σύγκρισης της αγοράς· "
    "όταν αναφέρεις πόσους ανταγωνιστές έχει η αγορά, ανάφερε το "
    "competitors_in_basis, ποτέ το πλήθος της λίστας ή των tracked. Όσοι "
    "έχουν tracked=true είναι οι ανταγωνιστές που παρακολουθεί ο ιδιοκτήτης "
    "και μπορείς να τους σχολιάσεις ξεχωριστά. "
    "Απάντησε αποκλειστικά στα ελληνικά: γράψε το reasoning και τα key_factors "
    "στα ελληνικά, με ποσά σε ευρώ (π.χ. «120 €»)."
)


class PriceRecommendationAgent:
    """Produce a price recommendation, preferring the LLM, falling back to stats."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        client_factory: Callable[[], Any] | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._client_factory = client_factory
        # Built lazily once and reused: a fresh anthropic.Anthropic per call
        # would pay a new connection pool + TLS handshake on every request.
        self._client: Any | None = None
        self._client_lock = threading.Lock()

    def recommend(
        self,
        stats: PriceStatistics,
        advisor_context: Any,
        own_cancellation_type: str | None = None,
    ) -> PriceRecommendation | None:
        """Return a validated recommendation, or None when data cannot support one.

        ``own_cancellation_type`` is the reference package's cancellation
        class (spec 2026-09-29 Β.2); it scopes the per-competitor
        cheapest-in-same-class price inside the prompt payload. None (typed-in
        sample price, unknown class) sends that figure as null.

        Never raises — LLM failures degrade to the statistical fallback.
        """
        # Nothing to ground a number in: an LLM answer here would be pure
        # hallucination, so refuse to recommend at all (skip the call, too).
        if not _has_anchor_data(stats):
            return None

        # No key configured: don't even construct a client. Pure statistical path.
        if not self.api_key:
            return self._statistical_fallback(stats)

        try:
            client = self._client_factory() if self._client_factory else self._get_client()
            user_payload = self._build_user_payload(stats, advisor_context, own_cancellation_type)
            # No `thinking`, no sampling params (module docstring): thinking is
            # always on for claude-opus-5-5 and explicit values would 400.
            response = client.messages.parse(
                model=self.model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_payload}],
                output_format=PriceRecommendation,
                output_config={"effort": "medium"},
            )
            recommendation = getattr(response, "parsed_output", None)
            stop_reason = getattr(response, "stop_reason", None)
            if recommendation is None or stop_reason == "refusal":
                logger.warning(
                    "Price agent returned no usable output (stop_reason=%s); using fallback",
                    stop_reason,
                )
                return self._statistical_fallback(stats)
            problem = validate_recommendation(recommendation, stats)
            if problem is not None:
                logger.warning(
                    "Price agent output failed post-validation (%s); using fallback", problem
                )
                return self._statistical_fallback(stats)
            recommendation.source = "agent"
            recommendation.confidence = deterministic_confidence(stats)
            return recommendation
        except Exception:  # noqa: BLE001 — endpoint must never raise out of the agent
            logger.warning("Price recommendation agent call failed; using fallback", exc_info=True)
            return self._statistical_fallback(stats)

    def _get_client(self) -> Any:
        """Return the cached SDK client, building it once per agent instance."""
        if self._client is None:
            # Construction is side-effect-free; the lock just avoids building
            # two pools when the first calls race from worker threads.
            with self._client_lock:
                if self._client is None:
                    self._client = self._build_client()
        return self._client

    def _build_client(self) -> Any:
        import anthropic

        return anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout_seconds)

    def _build_user_payload(
        self,
        stats: PriceStatistics,
        advisor_context: Any,
        own_cancellation_type: str | None = None,
    ) -> str:
        """Build a compact JSON user message: stats + a trimmed advisor context.

        ``cancellation_class_note`` spells the statistical basis out in Greek
        (spec Β.2) — null when the statistics carry no ``stats_scope``.
        """
        return json.dumps(
            {
                "statistics": stats.model_dump(),
                "cancellation_class_note": _cancellation_class_note(stats),
                "market_context": self._trim_advisor_context(
                    advisor_context, own_cancellation_type
                ),
            },
            default=str,
        )

    @staticmethod
    def _trim_advisor_context(
        advisor_context: Any, own_cancellation_type: str | None = None
    ) -> dict[str, Any]:
        """Keep only meta, pricing_signals and the top competitors to bound tokens.

        Per competitor a whitelist of scalar facts is forwarded — never the raw
        packages. The Β.2 additions (distance, category match, cheapest price
        in the reference package's cancellation class, Genius flag) are
        summarized here from the packages before those are dropped.

        The context is the full comparison basis (v4). The shown competitors
        are the closest comparables — same category first, then nearest, then
        cheapest — capped at ``_MAX_COMPETITORS``; the owner's tracked
        competitors always keep a seat (up to the cap) so the model can still
        single them out. ``competitors_in_basis`` / ``competitors_shown``
        state both sizes so the capped list is never read as the market.
        """
        context = (
            advisor_context.model_dump()
            if hasattr(advisor_context, "model_dump")
            else dict(advisor_context or {})
        )
        basis = [
            competitor
            for competitor in (context.get("competitors") or [])
            if isinstance(competitor, dict)
        ]
        shown = _select_shown_competitors(basis, _MAX_COMPETITORS)
        trimmed_competitors = [
            {
                "hotel_name": competitor.get("hotel_name"),
                "tracked": bool(competitor.get("tracked")),
                "price_min_eur": competitor.get("price_min_eur"),
                "review_score": competitor.get("review_score"),
                "rooms_left": competitor.get("rooms_left"),
                "distance_km": competitor.get("distance_km"),
                "category_match": competitor.get("category_match"),
                "cheapest_same_cancellation_eur": _cheapest_same_cancellation_price(
                    competitor.get("packages"), own_cancellation_type
                ),
                "has_genius_discount": _has_genius_rate(competitor.get("packages")),
            }
            for competitor in shown
        ]
        tracked_in_basis = sum(1 for competitor in basis if competitor.get("tracked"))
        return {
            "meta": context.get("meta"),
            "pricing_signals": context.get("pricing_signals"),
            # The class every cheapest_same_cancellation_eur refers to; null
            # when the reference package's class is unknown.
            "reference_cancellation_class": own_cancellation_type,
            "competitors_in_basis": len(basis),
            "competitors_shown": len(trimmed_competitors),
            "competitors_tracked": tracked_in_basis,
            "competitors_note": _competitors_note(
                len(basis), len(trimmed_competitors), tracked_in_basis
            ),
            "competitors": trimmed_competitors,
        }

    @staticmethod
    def _statistical_fallback(stats: PriceStatistics) -> PriceRecommendation | None:
        """Build a recommendation directly from the statistical baseline.

        Returns None when there is nothing to anchor on — the endpoint then
        reports ``recommendation_available=false`` instead of inventing €0.
        """
        recommended = _first_positive_anchor(stats)
        if recommended is None:
            return None

        recommended_price = round(float(recommended), 2)
        if stats.market_p25_eur is not None and stats.market_p75_eur is not None:
            low, high = stats.market_p25_eur, stats.market_p75_eur
        else:
            low = round(recommended_price * 0.9, 2)
            high = round(recommended_price * 1.1, 2)
        # The statistics notes are NOT repeated here: the page shows them once,
        # in the market-statistics panel (Round 6 §5.1).
        reasoning = (
            "Στατιστική σύσταση (χωρίς AI): η διάμεση τιμή της τρέχουσας αγοράς, "
            "προσαρμοσμένη στην τάση των 7 ημερών και περιορισμένη στο εύρος "
            "P25–P75 των ανταγωνιστών."
        )

        return PriceRecommendation(
            recommended_price_eur=recommended_price,
            price_range_low_eur=low,
            price_range_high_eur=high,
            confidence=deterministic_confidence(stats),
            reasoning=reasoning,
            key_factors=_fallback_key_factors(stats),
            source="statistical",
        )


def _first_positive_anchor(stats: PriceStatistics) -> float | None:
    """First usable MARKET price anchor: statistical baseline, then median.

    The owner's own reference price is deliberately NOT an anchor. Echoing the
    input back as advice ("we recommend the price you already charge") reads
    like an analysis the data cannot support — with no scraped history every
    market statistic renders empty while a confident headline number implies
    the market was studied. Own price still shapes the recommendation once
    market data exists (via the percentile position and the guardrails).

    A genuine-but-useless 0.0 never anchors; the next candidate is tried.
    """
    for candidate in (
        stats.statistical_recommendation_eur,
        stats.market_median_eur,
    ):
        if candidate is not None and candidate > 0:
            return float(candidate)
    return None


def _has_anchor_data(stats: PriceStatistics) -> bool:
    """Whether real market knowledge exists to ground a recommendation."""
    return _first_positive_anchor(stats) is not None


def _cancellation_class_note(stats: PriceStatistics) -> str | None:
    """Greek one-liner naming the statistical basis, or None without a scope."""
    if stats.stats_scope is None:
        return None
    return _CANCELLATION_CLASS_NOTES.get(stats.stats_scope.cancellation_class)


def _finite_or_none(value: Any) -> float | None:
    """A finite number as float; None for missing, non-numeric or bool values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _competitor_order_key(competitor: dict) -> tuple:
    """Closest comparables first: same category, then nearest, then cheapest.

    Unknown distances and prices sort after known ones instead of reading as 0.
    """
    distance = _finite_or_none(competitor.get("distance_km"))
    price = _finite_or_none(competitor.get("price_min_eur"))
    return (
        competitor.get("category_match") != "same",
        distance is None,
        distance or 0.0,
        price is None,
        price or 0.0,
    )


def _select_shown_competitors(basis: list[dict], cap: int) -> list[dict]:
    """The at-most-``cap`` competitors the prompt shows, in comparable order.

    Tracked competitors are chosen first (the owner's watched set is small
    and the model must be able to name them); the remaining seats go to the
    closest untracked comparables. Without tracked competitors outside the
    top ``cap`` this is exactly the first ``cap`` of the ordered basis.
    """
    ordered = sorted(basis, key=_competitor_order_key)
    tracked = [competitor for competitor in ordered if competitor.get("tracked")][:cap]
    untracked = [competitor for competitor in ordered if not competitor.get("tracked")]
    chosen = {id(competitor) for competitor in tracked + untracked[: cap - len(tracked)]}
    return [competitor for competitor in ordered if id(competitor) in chosen]


def _competitors_note(in_basis: int, shown: int, tracked: int) -> str:
    """Greek one-liner stating the comparison-basis size the model must report."""
    basis_text = "1 ανταγωνιστής" if in_basis == 1 else f"{in_basis} ανταγωνιστές"
    if tracked == 0:
        tracked_text = "κανένας δεν παρακολουθείται από τον ιδιοκτήτη"
    elif tracked == 1:
        tracked_text = "1 παρακολουθείται από τον ιδιοκτήτη (tracked=true)"
    else:
        tracked_text = f"{tracked} παρακολουθούνται από τον ιδιοκτήτη (tracked=true)"
    return (
        f"Βάση σύγκρισης της αγοράς: {basis_text}. "
        f"Η λίστα δείχνει {shown} από αυτούς· {tracked_text}."
    )


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _cheapest_same_cancellation_price(
    packages: Any, cancellation_type: str | None
) -> float | None:
    """Cheapest package price inside the reference cancellation class.

    None when the reference class is unknown, the competitor carries no
    packages, or no package's rate plan sits in that class — an honest null
    beats letting the headline minimum masquerade as a like-for-like price.
    """
    if not cancellation_type or not isinstance(packages, list):
        return None
    prices: list[float] = []
    for package in packages:
        if not isinstance(package, dict):
            continue
        rate_plan = package.get("rate_plan")
        if not isinstance(rate_plan, dict):
            continue
        if rate_plan.get("cancellation_type") != cancellation_type:
            continue
        # The EFFECTIVE price, like every other figure the model reads: the
        # plan's discounted price when shown, else the package's base price.
        price = rate_plan.get("discounted_price_per_night_eur")
        if not _is_finite_number(price):
            price = package.get("price_per_night_eur")
        if _is_finite_number(price):
            prices.append(float(price))
    return min(prices) if prices else None


def _has_genius_rate(packages: Any) -> bool:
    """Genius flag summary: does ANY package carry a Genius-discount plan."""
    if not isinstance(packages, list):
        return False
    return any(
        isinstance(package, dict)
        and isinstance(package.get("rate_plan"), dict)
        and bool(package["rate_plan"].get("has_genius_discount"))
        for package in packages
    )


# Plausibility margins around the market's interquartile band. Wide enough for
# legitimate strategy (undercutting p25, premium above p75), tight enough that
# an ungrounded LLM number cannot reach a user.
_BAND_LOW_FACTOR = 0.5
_BAND_HIGH_FACTOR = 1.5


def _allowed_market_band(stats: PriceStatistics) -> tuple[float, float] | None:
    """Market-derived (min, max) for a plausible recommendation, or None."""
    if stats.market_p25_eur is not None and stats.market_p75_eur is not None:
        return (stats.market_p25_eur * _BAND_LOW_FACTOR, stats.market_p75_eur * _BAND_HIGH_FACTOR)
    anchor = _first_positive_anchor(stats)
    if anchor is not None:
        return (anchor * _BAND_LOW_FACTOR, anchor * _BAND_HIGH_FACTOR)
    return None


def validate_recommendation(
    recommendation: PriceRecommendation, stats: PriceStatistics
) -> str | None:
    """Deterministic post-validation of an LLM answer; None means valid.

    Returns a short human-readable problem description (for the log line)
    when the answer must not reach a user.
    """
    prices = (
        recommendation.recommended_price_eur,
        recommendation.price_range_low_eur,
        recommendation.price_range_high_eur,
    )
    if any(price is None or not math.isfinite(price) or price <= 0 for price in prices):
        return f"non-positive or non-finite price in {prices}"
    if not (
        recommendation.price_range_low_eur
        <= recommendation.recommended_price_eur
        <= recommendation.price_range_high_eur
    ):
        return (
            "range out of order: "
            f"low={recommendation.price_range_low_eur} "
            f"recommended={recommendation.recommended_price_eur} "
            f"high={recommendation.price_range_high_eur}"
        )
    band = _allowed_market_band(stats)
    if band is not None:
        band_low, band_high = band
        if not band_low <= recommendation.recommended_price_eur <= band_high:
            return (
                f"recommended price {recommendation.recommended_price_eur} outside the "
                f"market plausibility band [{band_low:.2f}, {band_high:.2f}]"
            )
    return None


def apply_business_guardrails(
    recommendation: PriceRecommendation | None,
    stats: PriceStatistics,
    max_change_pct: float,
) -> PriceRecommendation | None:
    """Clamp a recommendation to the configured move from the own reference.

    The statistical/LLM plausibility check protects against market outliers.
    This second business guardrail protects the operator from an abrupt price
    move relative to the property's current reference price.

    ``recommended``, ``low`` and ``high`` are clamped INDEPENDENTLY into
    ``[own × (1 − max %), own × (1 + max %)]``. The old clamp folded both
    bounds onto the recommended price, so a 92 € hotel in a 160 € market read
    «160 € – 160 €». Without an own price nothing is clamped.

    Clamp or not, a range narrower than 5 % of the recommendation reads as one
    number, so it is reopened to ±5 % around the recommendation, inside the
    business band when there is one (Round 6 §5.2). The result always keeps
    ``low <= recommended <= high``.
    """
    if recommendation is None:
        return None
    own_price = stats.own_reference_price_eur
    guarded = own_price is not None and own_price > 0 and max_change_pct > 0

    # Επιχειρηματικό όριο: καμία αυτόματη σύσταση δεν αλλάζει απότομα την τιμή βάσης.
    band_low = own_price * max(0.0, 1.0 - max_change_pct / 100.0) if guarded else -math.inf
    band_high = own_price * (1.0 + max_change_pct / 100.0) if guarded else math.inf

    def clamp(value: float) -> float:
        return min(max(value, band_low), band_high)

    recommended = clamp(recommendation.recommended_price_eur)
    low = min(clamp(recommendation.price_range_low_eur), recommended)
    high = max(clamp(recommendation.price_range_high_eur), recommended)
    if high - low < recommended * 0.05:
        # Both stay on their side of the recommendation: band_low <= recommended
        # <= band_high after the clamp.
        low = max(band_low, recommended * 0.95)
        high = min(band_high, recommended * 1.05)
    if (recommended, low, high) == (
        recommendation.recommended_price_eur,
        recommendation.price_range_low_eur,
        recommendation.price_range_high_eur,
    ):
        return recommendation

    # The note explains a moved RECOMMENDATION only: clipping just the range
    # changes no headline number. NN is the unclamped recommendation's
    # distance from the own price — the number the guardrail actually moved
    # (on the statistical path the recommendation IS the market figure).
    # The sentence goes into the reasoning only: the card renders reasoning
    # and key factors together, so adding it to both showed it twice.
    reasoning = recommendation.reasoning
    if recommended != recommendation.recommended_price_eur:
        market_vs_own_pct = round(
            (recommendation.recommended_price_eur - own_price) / own_price * 100.0
        )
        # A gap that rounds to «+0%» explains nothing, so nothing is said.
        if market_vs_own_pct != 0:
            note = (
                f"Η αγορά είναι {market_vs_own_pct:+d}% σε σχέση με την τιμή σας· "
                f"η σύσταση περιορίζεται στο ±{max_change_pct:.0f}% (όριο ασφαλείας)"
            )
            reasoning = f"{reasoning} {note}."
    return recommendation.model_copy(
        update={
            "recommended_price_eur": round(recommended, 2),
            "price_range_low_eur": round(low, 2),
            "price_range_high_eur": round(high, 2),
            "reasoning": reasoning,
        }
    )


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


def _fallback_key_factors(stats: PriceStatistics) -> list[str]:
    """Greek, human-readable key factors from the non-None statistical signals."""
    factors: list[str] = []
    if stats.market_median_eur is not None:
        factors.append(f"Διάμεσος αγοράς {_eur(stats.market_median_eur)}")
    if stats.own_reference_price_eur is not None:
        factors.append(f"Τιμή αναφοράς σας {_eur(stats.own_reference_price_eur)}")
    if stats.position is not None:
        # «καταλύματα» is neuter, so the adjective agrees as «Φθηνότερα».
        total = stats.position.total
        hotels = "1 κατάλυμα" if total == 1 else f"{total} καταλύματα"
        factors.append(f"Φθηνότερα από εσάς: {stats.position.cheaper_than_you} από {hotels}")
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
