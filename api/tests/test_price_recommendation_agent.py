import json
from datetime import date

import anthropic

from api.schemas.agents import PriceRecommendation, PriceStatistics, PriceStatsScope
from api.services.price_recommendation_agent import (
    PRICE_RECOMMENDATION_PROMPT_VERSION,
    PriceRecommendationAgent,
    apply_business_guardrails,
    deterministic_confidence,
)
from api.services.price_statistics_service import compute_price_statistics


def _stats() -> PriceStatistics:
    rows = [
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "p1", "hotel_name": "A", "min_price": 100.0},
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "p2", "hotel_name": "B", "min_price": 140.0},
    ]
    return compute_price_statistics(rows, own_price=120.0, check_in=date(2026, 7, 15), as_of=date(2026, 7, 1))


ADVISOR_CONTEXT = {
    "meta": {"destination": "Faliraki", "total_competitors": 2, "market_stats": {"price_median_eur": 120.0}},
    "pricing_signals": {"cheapest_competitor": "A", "most_expensive_competitor": "B"},
    "competitors": [
        {"hotel_name": "A", "price_min_eur": 100.0, "review_score": 8.8, "extra_noise": "x" * 500},
        {"hotel_name": "B", "price_min_eur": 140.0, "review_score": 9.0},
    ],
}


class _RaisingFactory:
    """A client_factory that fails the test if it is ever called."""

    def __init__(self):
        self.called = False

    def __call__(self):
        self.called = True
        raise AssertionError("client_factory must not be called when api_key is empty")


class _FakeParseResult:
    def __init__(self, parsed_output, stop_reason="end_turn"):
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.parse_kwargs = None

    def parse(self, **kwargs):
        self.parse_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._result


class _FakeClient:
    def __init__(self, result=None, error=None):
        self.messages = _FakeMessages(result=result, error=error)


def test_empty_api_key_uses_statistical_fallback_without_building_client():
    factory = _RaisingFactory()
    agent = PriceRecommendationAgent(api_key="", model="claude-opus-5-5", client_factory=factory)

    rec = agent.recommend(_stats(), ADVISOR_CONTEXT)

    assert factory.called is False
    assert rec.source == "statistical"
    assert isinstance(rec, PriceRecommendation)


def test_agent_passthrough_sets_source_agent():
    agent_rec = PriceRecommendation(
        recommended_price_eur=125.0,
        price_range_low_eur=110.0,
        price_range_high_eur=140.0,
        confidence="high",
        reasoning="Grounded in the provided market data.",
        key_factors=["market median 120"],
        source="agent",
    )
    fake_client = _FakeClient(result=_FakeParseResult(agent_rec, stop_reason="end_turn"))
    agent = PriceRecommendationAgent(
        api_key="sk-test", model="claude-opus-5-5", client_factory=lambda: fake_client
    )

    rec = agent.recommend(_stats(), ADVISOR_CONTEXT)

    assert rec.source == "agent"
    assert rec.recommended_price_eur == 125.0
    # Verify the call shape: exactly the allowed params are sent. On
    # claude-opus-5-5 thinking is always on — an explicit ``thinking`` value
    # (like ``temperature``/``top_p``/``top_k``) would 400, so none may appear.
    kwargs = fake_client.messages.parse_kwargs
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["output_format"] is PriceRecommendation
    # Effort rides in output_config — the installed anthropic 0.115.0 merges
    # ``output_format`` into it as ``format`` on the wire.
    assert kwargs["output_config"] == {"effort": "medium"}
    # max_tokens caps thinking + answer; 2048 truncated answers into the fallback.
    assert kwargs["max_tokens"] == 16_000
    assert set(kwargs) == {
        "model",
        "max_tokens",
        "system",
        "messages",
        "output_format",
        "output_config",
    }
    assert "temperature" not in kwargs
    assert "thinking" not in kwargs


def test_default_model_is_claude_opus_5_5():
    """Spec 2026-09-29 Β.1: the ANTHROPIC_MODEL default is claude-opus-5-5.

    Pinned on the FIELD default so an ambient .env / environment variable can
    never fake a pass (or a failure) here.
    """
    from api.config import Settings

    assert Settings.model_fields["anthropic_model"].default == "claude-opus-5-5"


def test_sent_kwargs_all_exist_on_the_installed_sdk_parse_signature():
    """Signature drift fails loudly here, never silently in production.

    Every kwarg the agent sends must be a parameter of the INSTALLED SDK's
    ``Messages.parse``. A future SDK upgrade renaming ``output_config`` /
    ``output_format`` then breaks CI on this assertion instead of 400-ing at
    runtime and being swallowed by the statistical fallback forever.
    """
    import inspect

    from anthropic.resources.messages import Messages

    fake_client = _FakeClient(result=_FakeParseResult(None, stop_reason="refusal"))
    agent = PriceRecommendationAgent(
        api_key="sk-test", model="claude-opus-5-5", client_factory=lambda: fake_client
    )
    agent.recommend(_stats(), ADVISOR_CONTEXT)

    sent = set(fake_client.messages.parse_kwargs)
    accepted = set(inspect.signature(Messages.parse).parameters) - {"self"}
    assert sent <= accepted, (
        f"kwargs unknown to the installed Messages.parse: {sorted(sent - accepted)}"
    )


def test_client_is_built_once_and_reused_across_calls(monkeypatch):
    agent_rec = PriceRecommendation(
        recommended_price_eur=125.0,
        price_range_low_eur=110.0,
        price_range_high_eur=140.0,
        confidence="high",
        reasoning="Grounded in the provided market data.",
        key_factors=["market median 120"],
        source="agent",
    )
    fake_client = _FakeClient(result=_FakeParseResult(agent_rec, stop_reason="end_turn"))
    builds = {"count": 0}

    def fake_build(self):
        builds["count"] += 1
        return fake_client

    monkeypatch.setattr(PriceRecommendationAgent, "_build_client", fake_build)
    # No client_factory: the agent must build its own client — exactly once.
    agent = PriceRecommendationAgent(api_key="sk-test", model="claude-opus-5-5")

    first = agent.recommend(_stats(), ADVISOR_CONTEXT)
    second = agent.recommend(_stats(), ADVISOR_CONTEXT)

    assert first.source == "agent"
    assert second.source == "agent"
    assert builds["count"] == 1


def test_api_error_falls_back_without_raising():
    error = anthropic.APITimeoutError(request=None)
    fake_client = _FakeClient(error=error)
    agent = PriceRecommendationAgent(
        api_key="sk-test", model="claude-opus-5-5", client_factory=lambda: fake_client
    )

    rec = agent.recommend(_stats(), ADVISOR_CONTEXT)

    assert rec.source == "statistical"


def test_generic_exception_during_call_falls_back():
    fake_client = _FakeClient(error=RuntimeError("boom"))
    agent = PriceRecommendationAgent(
        api_key="sk-test", model="claude-opus-5-5", client_factory=lambda: fake_client
    )

    rec = agent.recommend(_stats(), ADVISOR_CONTEXT)

    assert rec.source == "statistical"


def test_refusal_or_none_parsed_output_falls_back():
    # parsed_output None (refusal) -> statistical fallback, no raise.
    fake_client = _FakeClient(result=_FakeParseResult(None, stop_reason="refusal"))
    agent = PriceRecommendationAgent(
        api_key="sk-test", model="claude-opus-5-5", client_factory=lambda: fake_client
    )

    rec = agent.recommend(_stats(), ADVISOR_CONTEXT)

    assert rec.source == "statistical"


def test_statistical_fallback_uses_stats_recommendation_and_band():
    stats = _stats()
    factory = _RaisingFactory()
    agent = PriceRecommendationAgent(api_key="", model="claude-opus-5-5", client_factory=factory)

    rec = agent.recommend(stats, ADVISOR_CONTEXT)

    assert rec.source == "statistical"
    assert rec.confidence == "low"
    assert rec.recommended_price_eur == stats.statistical_recommendation_eur
    assert rec.price_range_low_eur == stats.market_p25_eur
    assert rec.price_range_high_eur == stats.market_p75_eur


def test_no_data_returns_none_instead_of_zero_recommendation():
    """Insufficient data must yield None (recommendation_available=false), not €0."""
    empty_stats = compute_price_statistics([], own_price=None, check_in=date(2026, 7, 15))
    agent = PriceRecommendationAgent(api_key="", model="claude-opus-5-5")

    assert agent.recommend(empty_stats, ADVISOR_CONTEXT) is None


def test_no_data_returns_none_even_when_the_llm_hallucinates_a_price():
    """With zero grounding data an LLM number is noise — reject it outright."""
    empty_stats = compute_price_statistics([], own_price=None, check_in=date(2026, 7, 15))
    agent_rec = PriceRecommendation(
        recommended_price_eur=999.0,
        price_range_low_eur=900.0,
        price_range_high_eur=1100.0,
        confidence="high",
        reasoning="Invented.",
        key_factors=[],
        source="agent",
    )
    fake_client = _FakeClient(result=_FakeParseResult(agent_rec))
    agent = PriceRecommendationAgent(
        api_key="sk-test", model="claude-opus-5-5", client_factory=lambda: fake_client
    )

    assert agent.recommend(empty_stats, ADVISOR_CONTEXT) is None


# ---------------------------------------------------------------------------
# Post-validation of agent output
# ---------------------------------------------------------------------------


def _agent_with_output(recommended, low, high, confidence="high"):
    agent_rec = PriceRecommendation(
        recommended_price_eur=recommended,
        price_range_low_eur=low,
        price_range_high_eur=high,
        confidence=confidence,
        reasoning="From the model.",
        key_factors=[],
        source="agent",
    )
    fake_client = _FakeClient(result=_FakeParseResult(agent_rec))
    return PriceRecommendationAgent(
        api_key="sk-test", model="claude-opus-5-5", client_factory=lambda: fake_client
    )


def test_agent_output_with_non_positive_price_falls_back_to_statistical():
    rec = _agent_with_output(-5.0, 100.0, 140.0).recommend(_stats(), ADVISOR_CONTEXT)

    assert rec is not None
    assert rec.source == "statistical"
    assert rec.recommended_price_eur > 0


def test_agent_output_with_inverted_range_falls_back_to_statistical():
    # low > recommended > high is meaningless for the UI range display.
    rec = _agent_with_output(120.0, 140.0, 100.0).recommend(_stats(), ADVISOR_CONTEXT)

    assert rec is not None
    assert rec.source == "statistical"
    assert rec.price_range_low_eur <= rec.recommended_price_eur <= rec.price_range_high_eur


def test_agent_output_far_outside_market_band_falls_back_to_statistical():
    # _stats(): p25=110, p75=130. 990 EUR is far outside any realistic band
    # for this market and must not reach the user.
    rec = _agent_with_output(990.0, 900.0, 1100.0).recommend(_stats(), ADVISOR_CONTEXT)

    assert rec is not None
    assert rec.source == "statistical"
    assert rec.recommended_price_eur == _stats().statistical_recommendation_eur


def test_valid_agent_output_keeps_its_prices():
    rec = _agent_with_output(125.0, 110.0, 140.0).recommend(_stats(), ADVISOR_CONTEXT)

    assert rec.source == "agent"
    assert rec.recommended_price_eur == 125.0
    assert rec.price_range_low_eur == 110.0
    assert rec.price_range_high_eur == 140.0


# ---------------------------------------------------------------------------
# Deterministic confidence (data quality, not model self-assessment)
# ---------------------------------------------------------------------------


def _rich_history_stats() -> PriceStatistics:
    """Six runs across six weeks: enough for trends and 'high' confidence.

    Five hotels in every run: a same-store trend needs at least
    MIN_MATCHED_HOTELS hotels priced in both compared runs.
    """
    days = ["2026-05-15", "2026-05-22", "2026-06-01", "2026-06-15", "2026-06-22", "2026-06-30"]
    base_prices = (("p1", 100.0), ("p2", 110.0), ("p3", 120.0), ("p4", 130.0), ("p5", 140.0))
    rows = []
    for run_offset, day in enumerate(days):
        rn = len(days) - run_offset  # rn=1 is the NEWEST run
        for prop, base_price in base_prices:
            price = base_price + run_offset
            rows.append(
                {
                    "rn": rn,
                    "observed_at": f"{day}T08:00:00+00:00",
                    "property_id": prop,
                    "hotel_name": prop.upper(),
                    "min_price": price,
                }
            )
    return compute_price_statistics(rows, own_price=120.0, check_in=date(2026, 7, 15), as_of=date(2026, 7, 1))


def test_confidence_ignores_llm_self_assessment():
    # The model says "high" but a single run of history only supports "low".
    rec = _agent_with_output(125.0, 110.0, 140.0, confidence="high").recommend(
        _stats(), ADVISOR_CONTEXT
    )

    assert rec.source == "agent"
    assert rec.confidence == "low"


def test_confidence_high_with_deep_history_and_trends():
    stats = _rich_history_stats()
    assert stats.sample_runs >= 5
    assert stats.trend_7d_pct is not None

    rec = _agent_with_output(125.0, 110.0, 140.0, confidence="low").recommend(
        stats, ADVISOR_CONTEXT
    )

    assert rec.confidence == "high"


def test_confidence_is_identical_for_statistical_source():
    """Same data ⇒ same confidence, regardless of which source produced it."""
    stats = _rich_history_stats()
    agent = PriceRecommendationAgent(api_key="", model="claude-opus-5-5")

    rec = agent.recommend(stats, ADVISOR_CONTEXT)

    assert rec.source == "statistical"
    assert rec.confidence == "high"


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
    # The sentence lives in the reasoning only; the card would show it twice.
    assert guarded.key_factors == ["Διάμεσος αγοράς 120 €"]
    assert guarded.reasoning == f"Η ζήτηση είναι ισχυρή. {note}."


def test_business_guardrail_clamps_a_drop_without_collapsing_the_range():
    # 60 / 50 / 80 all clamp to 96; reopened upwards only (96 is the band floor).
    guarded = apply_business_guardrails(_agent_recommendation(60, 50, 80), _stats(), 20)

    assert guarded.recommended_price_eur == 96
    assert guarded.price_range_low_eur == 96
    assert guarded.price_range_high_eur == 100.8
    assert f"Η αγορά είναι -50% {GUARDRAIL_NOTE}." in guarded.reasoning


def test_business_guardrail_clamps_each_bound_independently():
    # 150 / 130 / 160: only the recommendation and the high bound exceed 144;
    # the low bound (130) is inside the band and must survive untouched.
    guarded = apply_business_guardrails(_agent_recommendation(150, 130, 160), _stats(), 20)

    assert (guarded.recommended_price_eur, guarded.price_range_low_eur, guarded.price_range_high_eur) == (144, 130, 144)
    assert f"Η αγορά είναι +25% {GUARDRAIL_NOTE}." in guarded.reasoning


def test_capped_card_states_the_guardrail_exactly_once():
    # Own 92 € in a 100 / 140 market (median 120): the ±20% cap applies.
    rows = [
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "p1", "hotel_name": "A", "min_price": 100.0},
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "p2", "hotel_name": "B", "min_price": 140.0},
    ]
    stats = compute_price_statistics(rows, own_price=92.0, check_in=date(2026, 7, 15), as_of=date(2026, 7, 1))
    recommendation = PriceRecommendationAgent(api_key="", model="claude-opus-5-5").recommend(stats, ADVISOR_CONTEXT)

    guarded = apply_business_guardrails(recommendation, stats, 20)

    assert guarded.recommended_price_eur == 110.4  # the cap really applied
    card_texts = [guarded.reasoning, *guarded.key_factors, *stats.notes]
    assert sum(text.count("(όριο ασφαλείας)") for text in card_texts) == 1
    assert "(όριο ασφαλείας)" in guarded.reasoning


def test_guardrail_note_only_when_the_recommended_price_itself_moves():
    # Own 120 -> band [96, 144]. 125 stays; only the low bound (90) is clipped.
    original = _agent_recommendation(125, 90, 140)

    guarded = apply_business_guardrails(original, _stats(), 20)

    assert (guarded.recommended_price_eur, guarded.price_range_low_eur, guarded.price_range_high_eur) == (125, 96, 140)
    assert guarded.reasoning == original.reasoning


def test_guardrail_never_reports_a_zero_percent_market_gap():
    # The owner already charges the recommendation; only the range is clipped.
    at_own_price = apply_business_guardrails(_agent_recommendation(120, 80, 150), _stats(), 20)
    # A cap so tight that the clamped 120.4 -> 120.36 move rounds to 0 %.
    tiny_cap = apply_business_guardrails(_agent_recommendation(120.4, 115, 125), _stats(), 0.3)

    assert tiny_cap.recommended_price_eur == 120.36  # the recommendation was clamped
    for guarded in (at_own_price, tiny_cap):
        assert "+0%" not in guarded.reasoning
        assert "-0%" not in guarded.reasoning


def test_collapsed_range_reopens_even_without_an_own_price():
    # One hotel at 100 €: p25 = p75 = 100, so the fallback range is «100 € – 100 €».
    rows = [{"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "p1", "hotel_name": "A", "min_price": 100.0}]
    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15), as_of=date(2026, 7, 1))
    recommendation = PriceRecommendationAgent(api_key="", model="claude-opus-5-5").recommend(stats, ADVISOR_CONTEXT)

    guarded = apply_business_guardrails(recommendation, stats, 20)

    assert (guarded.recommended_price_eur, guarded.price_range_low_eur, guarded.price_range_high_eur) == (100, 95, 105)
    assert guarded.reasoning == recommendation.reasoning


def test_near_collapsed_range_reopens_without_a_clamp():
    # Own 120 -> band [96, 144]; 120 / 119 / 121 sits inside it but is only 2 € wide.
    original = _agent_recommendation(120, 119, 121)

    guarded = apply_business_guardrails(original, _stats(), 20)

    assert (guarded.recommended_price_eur, guarded.price_range_low_eur, guarded.price_range_high_eur) == (120, 114, 126)
    assert guarded.reasoning == original.reasoning


def test_reopened_range_stays_inside_the_business_band():
    # 140 / 139 / 141 reopens to 133 - 147, but the band ceiling is 144.
    guarded = apply_business_guardrails(_agent_recommendation(140, 139, 141), _stats(), 20)

    assert (guarded.recommended_price_eur, guarded.price_range_low_eur, guarded.price_range_high_eur) == (140, 133, 144)


def test_guardrail_output_always_brackets_the_recommendation():
    # A wide range that does not contain its own recommendation (125 - 140 around 120).
    guarded = apply_business_guardrails(_agent_recommendation(120, 125, 140), _stats(), 20)

    assert guarded.price_range_low_eur <= guarded.recommended_price_eur <= guarded.price_range_high_eur


def test_business_guardrail_preserves_safe_recommendation():
    recommendation = PriceRecommendation(
        recommended_price_eur=125,
        price_range_low_eur=110,
        price_range_high_eur=135,
        confidence="medium",
        reasoning="Grounded recommendation.",
        key_factors=[],
        source="agent",
    )

    guarded = apply_business_guardrails(recommendation, _stats(), 20)

    assert guarded is recommendation


# ---------------------------------------------------------------------------
# Own price alone is NOT market knowledge
# ---------------------------------------------------------------------------


def test_own_price_without_market_data_is_not_a_recommendation():
    """Zero market history must NOT yield "your own price" dressed as advice.

    Observed in a live walkthrough: an owner with a 92 EUR sample price and no
    scraped history for the requested dates saw a headline "Recommended
    nightly price 92 EUR" while every market statistic rendered as an em dash
    and sample_runs was 0. The number carried no market knowledge at all — it
    was the input echoed back — so the endpoint must report the recommendation
    as unavailable instead.
    """
    stats = compute_price_statistics([], own_price=92.0, check_in=date(2026, 8, 26))
    agent = PriceRecommendationAgent(api_key="", model="claude-opus-5-5")

    assert stats.sample_runs == 0
    assert stats.market_median_eur is None
    assert agent.recommend(stats, ADVISOR_CONTEXT) is None


def test_own_price_without_market_data_does_not_reach_the_llm():
    """Without market grounding the LLM must not be called (or trusted)."""
    stats = compute_price_statistics([], own_price=92.0, check_in=date(2026, 8, 26))
    factory = _RaisingFactory()
    agent = PriceRecommendationAgent(
        api_key="sk-test", model="claude-opus-5-5", client_factory=factory
    )

    assert agent.recommend(stats, ADVISOR_CONTEXT) is None
    assert factory.called is False


def test_market_data_without_own_price_still_recommends():
    """The market distribution alone is enough grounding — own price optional."""
    rows = [
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "p1", "hotel_name": "A", "min_price": 100.0},
        {"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "p2", "hotel_name": "B", "min_price": 140.0},
    ]
    stats = compute_price_statistics(rows, own_price=None, check_in=date(2026, 7, 15), as_of=date(2026, 7, 1))
    agent = PriceRecommendationAgent(api_key="", model="claude-opus-5-5")

    recommendation = agent.recommend(stats, ADVISOR_CONTEXT)

    assert recommendation is not None
    assert recommendation.recommended_price_eur > 0


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


# ---------------------------------------------------------------------------
# Round 6 — Greek statistical path, Greek LLM answers
# ---------------------------------------------------------------------------


def test_statistical_fallback_speaks_greek_and_does_not_repeat_the_notes():
    stats = _stats()  # 100 / 140, own 120, 1 run, lead time 14 days

    rec = PriceRecommendationAgent(api_key="", model="claude-opus-5-5").recommend(stats, ADVISOR_CONTEXT)

    assert rec.reasoning.startswith("Στατιστική σύσταση (χωρίς AI)")
    # The notes render once, in the statistics panel — never inside the reasoning.
    assert all(note not in rec.reasoning for note in stats.notes)
    assert rec.key_factors == [
        "Διάμεσος αγοράς 120 €",
        "Τιμή αναφοράς σας 120 €",
        "Φθηνότερα από εσάς: 1 από 2 καταλύματα",
        "Χρόνος έως την άφιξη 14 ημέρες",
        "Βάση: 1 αναζήτηση",
    ]


def test_position_factor_uses_the_singular_for_one_hotel():
    rows = [{"rn": 1, "observed_at": "2026-06-30T08:00:00+00:00", "property_id": "p1", "hotel_name": "A", "min_price": 100.0}]
    stats = compute_price_statistics(rows, own_price=120.0, check_in=date(2026, 7, 15), as_of=date(2026, 7, 1))

    rec = PriceRecommendationAgent(api_key="", model="claude-opus-5-5").recommend(stats, ADVISOR_CONTEXT)

    assert "Φθηνότερα από εσάς: 1 από 1 κατάλυμα" in rec.key_factors
    assert not any("Φθηνότεροι" in factor for factor in rec.key_factors)


def test_llm_system_prompt_demands_greek_answers():
    fake_client = _FakeClient(result=_FakeParseResult(None, stop_reason="refusal"))
    agent = PriceRecommendationAgent(
        api_key="sk-test", model="claude-opus-5-5", client_factory=lambda: fake_client
    )

    agent.recommend(_stats(), ADVISOR_CONTEXT)

    assert "Απάντησε αποκλειστικά στα ελληνικά" in fake_client.messages.parse_kwargs["system"]
    # A changed prompt is a new prompt version in the audit trail; v7 = the
    # same-store trends, so a cached v6 (median-vs-median trend) answer cannot serve.
    assert PRICE_RECOMMENDATION_PROMPT_VERSION == "2026-09-30.v7"


def test_llm_system_prompt_weighs_same_cancellation_class_prices():
    """Spec 2026-09-29 Β.2/Β.3: one sentence tells the model to weigh
    same-cancellation-class prices over headline prices when they differ."""
    fake_client = _FakeClient(result=_FakeParseResult(None, stop_reason="refusal"))
    agent = PriceRecommendationAgent(
        api_key="sk-test", model="claude-opus-5-5", client_factory=lambda: fake_client
    )

    agent.recommend(_stats(), ADVISOR_CONTEXT)

    system = fake_client.messages.parse_kwargs["system"]
    assert (
        "Όταν οι τιμές στην ίδια κλάση ακύρωσης (cheapest_same_cancellation_eur) "
        "διαφέρουν από τις γενικές ελάχιστες τιμές, στάθμισε περισσότερο τις "
        "τιμές της ίδιας κλάσης ακύρωσης." in system
    )
    # The Greek-answer directive survives the addition.
    assert "Απάντησε αποκλειστικά στα ελληνικά" in system


# ---------------------------------------------------------------------------
# Spec 2026-09-29 Β.2 — richer advisor context in the prompt payload
# ---------------------------------------------------------------------------


RICH_ADVISOR_CONTEXT = {
    "meta": {"destination": "Faliraki", "total_competitors": 2},
    "pricing_signals": {"cheapest_competitor": "A", "most_expensive_competitor": "B"},
    "competitors": [
        {
            "hotel_name": "A",
            "price_min_eur": 100.0,
            "review_score": 8.8,
            "rooms_left": 3,
            "distance_km": 0.7,
            "category_match": "similar",
            "extra_noise": "x" * 500,
            "packages": [
                {
                    "room_type": "Double Room",
                    "price_per_night_eur": 118.0,
                    "rate_plan": {
                        "cancellation_type": "non_refundable",
                        "has_genius_discount": False,
                    },
                },
                {
                    "room_type": "Double Room flex",
                    "price_per_night_eur": 132.0,
                    "rate_plan": {
                        "cancellation_type": "free_cancellation",
                        "has_genius_discount": True,
                    },
                },
                {
                    # Cheapest package overall — but without a rate plan it has
                    # no cancellation class and must NOT win the class figure.
                    "room_type": "Saver",
                    "price_per_night_eur": 100.0,
                    "rate_plan": None,
                },
            ],
        },
        {
            "hotel_name": "B",
            "price_min_eur": 140.0,
            "review_score": 9.0,
            "rooms_left": 1,
            "distance_km": None,
            "category_match": "same",
            "packages": [
                {
                    "room_type": "Suite",
                    "price_per_night_eur": 140.0,
                    "rate_plan": {
                        "cancellation_type": "non_refundable",
                        "has_genius_discount": False,
                    },
                },
            ],
        },
    ],
}


def _capture_payload(advisor_context, stats=None, own_cancellation_type=None) -> dict:
    """Run recommend() against a refusing fake client, return the sent payload."""
    fake_client = _FakeClient(result=_FakeParseResult(None, stop_reason="refusal"))
    agent = PriceRecommendationAgent(
        api_key="sk-test", model="claude-opus-5-5", client_factory=lambda: fake_client
    )
    agent.recommend(
        stats if stats is not None else _stats(),
        advisor_context,
        own_cancellation_type=own_cancellation_type,
    )
    return json.loads(fake_client.messages.parse_kwargs["messages"][0]["content"])


def test_payload_carries_distance_category_cancellation_and_genius_per_competitor():
    payload = _capture_payload(
        RICH_ADVISOR_CONTEXT, own_cancellation_type="free_cancellation"
    )

    # v4 order: same-category B before similar A (ordering has its own test).
    competitors = payload["market_context"]["competitors"]
    assert [c["hotel_name"] for c in competitors] == ["B", "A"]
    by_name = {c["hotel_name"]: c for c in competitors}
    first, second = by_name["A"], by_name["B"]
    assert first["hotel_name"] == "A"
    assert first["distance_km"] == 0.7
    assert first["category_match"] == "similar"
    # Cheapest price in the SAME class as the reference package
    # (free_cancellation): 132 € — not the 100 € saver, not the 118 €
    # non-refundable rate.
    assert first["cheapest_same_cancellation_eur"] == 132.0
    assert first["has_genius_discount"] is True
    assert second["hotel_name"] == "B"
    assert second["distance_km"] is None
    assert second["cheapest_same_cancellation_eur"] is None
    assert second["has_genius_discount"] is False
    # The class the per-competitor figures refer to is spelled out once.
    assert payload["market_context"]["reference_cancellation_class"] == "free_cancellation"


def test_payload_without_reference_class_reports_no_same_class_price():
    payload = _capture_payload(RICH_ADVISOR_CONTEXT)

    competitors = payload["market_context"]["competitors"]
    assert all(c["cheapest_same_cancellation_eur"] is None for c in competitors)
    assert payload["market_context"]["reference_cancellation_class"] is None


def test_same_class_price_is_the_effective_price_with_a_base_fallback():
    """Owner decision 2026-09-30: the discounted price when shown, else base."""
    context = {
        **RICH_ADVISOR_CONTEXT,
        "competitors": [
            {
                "hotel_name": "Discounted",
                "price_min_eur": 110.4,
                "packages": [
                    {
                        "room_type": "Double Room",
                        "price_per_night_eur": 120.0,
                        "rate_plan": {
                            "cancellation_type": "non_refundable",
                            "discounted_price_per_night_eur": 110.4,
                        },
                    },
                ],
            },
            {
                "hotel_name": "NoDiscount",
                "price_min_eur": 115.0,
                "packages": [
                    {
                        "room_type": "Double Room",
                        "price_per_night_eur": 115.0,
                        "rate_plan": {
                            "cancellation_type": "non_refundable",
                            "discounted_price_per_night_eur": None,
                        },
                    },
                ],
            },
        ],
    }

    payload = _capture_payload(context, own_cancellation_type="non_refundable")

    by_name = {c["hotel_name"]: c for c in payload["market_context"]["competitors"]}
    assert by_name["Discounted"]["cheapest_same_cancellation_eur"] == 110.4
    assert by_name["NoDiscount"]["cheapest_same_cancellation_eur"] == 115.0


def test_trimmed_context_stays_bounded_and_never_forwards_packages():
    many = {
        "meta": {"destination": "Faliraki"},
        "pricing_signals": {"cheapest_competitor": "H0"},
        "competitors": [
            {
                "hotel_name": f"H{index}",
                "price_min_eur": 100.0 + index,
                "review_score": 8.0,
                "rooms_left": 1,
                "distance_km": 1.0,
                "category_match": "same",
                "extra_noise": "x" * 500,
                "packages": [
                    {
                        "room_type": "Double",
                        "price_per_night_eur": 100.0 + index,
                        "rate_plan": {
                            "cancellation_type": "free_cancellation",
                            "has_genius_discount": True,
                        },
                    }
                ],
            }
            for index in range(40)
        ],
    }

    payload = _capture_payload(many, own_cancellation_type="free_cancellation")

    competitors = payload["market_context"]["competitors"]
    # The token guard holds: top 15 competitors, whitelisted keys only —
    # raw packages and unknown keys never reach the prompt.
    assert len(competitors) == 15
    assert all("packages" not in competitor for competitor in competitors)
    assert all("extra_noise" not in competitor for competitor in competitors)
    assert competitors[0]["cheapest_same_cancellation_eur"] == 100.0


def test_payload_surfaces_the_cancellation_class_basis_in_greek():
    matched_stats = _stats().model_copy(
        update={
            "stats_scope": PriceStatsScope(
                same_category=2, similar=0, used="same", cancellation_class="matched"
            )
        }
    )

    matched_payload = _capture_payload(
        ADVISOR_CONTEXT, stats=matched_stats, own_cancellation_type="non_refundable"
    )
    all_payload = _capture_payload(ADVISOR_CONTEXT)  # _stats() scope: "all"

    assert "ίδια κλάση ακύρωσης" in matched_payload["cancellation_class_note"]
    assert "matched" in matched_payload["cancellation_class_note"]
    assert "όλες τις πολιτικές ακύρωσης" in all_payload["cancellation_class_note"]
    assert "all" in all_payload["cancellation_class_note"]


def test_payload_without_stats_scope_sends_no_cancellation_class_note():
    scopeless = _stats().model_copy(update={"stats_scope": None})

    payload = _capture_payload(ADVISOR_CONTEXT, stats=scopeless)

    assert payload["cancellation_class_note"] is None


# ---------------------------------------------------------------------------
# v4 — the advisor context is the full comparison basis, not the tracked set
# ---------------------------------------------------------------------------


def _basis_competitor(name, category_match, distance_km, price, tracked=None) -> dict:
    competitor = {
        "hotel_name": name,
        "price_min_eur": price,
        "review_score": 8.5,
        "rooms_left": 4,
        "distance_km": distance_km,
        "category_match": category_match,
        "packages": [],
    }
    if tracked is not None:
        competitor["tracked"] = tracked
    return competitor


def test_payload_orders_same_category_then_nearest_then_cheapest_with_tracked_flags():
    basis = {
        "meta": {"destination": "Faliraki", "total_competitors": 6},
        "pricing_signals": {},
        # Deliberately scrambled input order (the service hands it price-sorted).
        "competitors": [
            _basis_competitor("similar-near", "similar", 0.1, 50.0, tracked=False),
            _basis_competitor("same-far-cheap", "same", 3.0, 80.0, tracked=True),
            _basis_competitor("same-near-dear", "same", 1.0, 190.0, tracked=False),
            _basis_competitor("same-near-cheap", "same", 1.0, 120.0, tracked=False),
            _basis_competitor("same-unknown-distance", "same", None, 60.0, tracked=False),
            # No "tracked" key at all (a pre-v4 shaped context): reads untracked.
            _basis_competitor("similar-unknown", "similar", None, 40.0),
        ],
    }

    market = _capture_payload(basis)["market_context"]

    assert [c["hotel_name"] for c in market["competitors"]] == [
        "same-near-cheap",        # same, 1.0 km, 120 € — equal distance: cheaper first
        "same-near-dear",         # same, 1.0 km, 190 €
        "same-far-cheap",         # same, 3.0 km — distance beats its lower price
        "same-unknown-distance",  # same, unknown distance sorts after known ones
        "similar-near",           # similar after every same-category hotel
        "similar-unknown",
    ]
    tracked = {c["hotel_name"]: c["tracked"] for c in market["competitors"]}
    assert tracked == {
        "same-near-cheap": False,
        "same-near-dear": False,
        "same-far-cheap": True,
        "same-unknown-distance": False,
        "similar-near": False,
        "similar-unknown": False,
    }
    assert (market["competitors_in_basis"], market["competitors_shown"]) == (6, 6)
    assert market["competitors_tracked"] == 1


def test_payload_caps_the_list_but_states_the_basis_and_keeps_tracked_seats():
    untracked = [
        _basis_competitor(f"U{index}", "same", 1.0 + index * 0.1, 100.0 + index, tracked=False)
        for index in range(30)
    ]
    # Last in comparable order (similar, 25 km, dearest) — would fall off a
    # plain top-15 cut, but the owner watches it.
    watched_far = _basis_competitor("Watched Far", "similar", 25.0, 400.0, tracked=True)
    watched_near = _basis_competitor("Watched Near", "same", 0.2, 150.0, tracked=True)
    basis = {
        "meta": {"destination": "Faliraki", "total_competitors": 32},
        "pricing_signals": {},
        "competitors": [watched_far, *untracked, watched_near],
    }

    market = _capture_payload(basis)["market_context"]

    names = [c["hotel_name"] for c in market["competitors"]]
    # The token guard holds at 15; the two tracked keep their seats, the 13
    # closest untracked fill the rest, and the list stays in comparable order.
    assert len(names) == 15
    assert names == ["Watched Near", *[f"U{index}" for index in range(13)], "Watched Far"]
    assert market["competitors_in_basis"] == 32
    assert market["competitors_shown"] == 15
    assert market["competitors_tracked"] == 2
    assert market["competitors_note"] == (
        "Βάση σύγκρισης της αγοράς: 32 ανταγωνιστές. "
        "Η λίστα δείχνει 15 από αυτούς· 2 παρακολουθούνται από τον ιδιοκτήτη (tracked=true)."
    )


def test_payload_without_tracked_competitors_is_the_plain_top_of_the_order():
    basis = {
        "meta": {},
        "pricing_signals": {},
        "competitors": [
            _basis_competitor(f"H{index}", "same", 5.0 - index * 0.1, 100.0, tracked=False)
            for index in range(20)
        ],
    }

    market = _capture_payload(basis)["market_context"]

    # Nearest first: H19 (3.1 km) down to H5 (4.5 km).
    assert [c["hotel_name"] for c in market["competitors"]] == [
        f"H{index}" for index in range(19, 4, -1)
    ]
    assert (market["competitors_in_basis"], market["competitors_shown"]) == (20, 15)
    assert market["competitors_tracked"] == 0
    assert "κανένας δεν παρακολουθείται" in market["competitors_note"]


def test_llm_system_prompt_reports_the_basis_count_not_the_shown_or_tracked_count():
    fake_client = _FakeClient(result=_FakeParseResult(None, stop_reason="refusal"))
    agent = PriceRecommendationAgent(
        api_key="sk-test", model="claude-opus-5-5", client_factory=lambda: fake_client
    )

    agent.recommend(_stats(), ADVISOR_CONTEXT)

    system = fake_client.messages.parse_kwargs["system"]
    assert (
        "όταν αναφέρεις πόσους ανταγωνιστές έχει η αγορά, ανάφερε το "
        "competitors_in_basis, ποτέ το πλήθος της λίστας ή των tracked." in system
    )
    assert "tracked=true" in system
    # The earlier directives survive the addition.
    assert "cheapest_same_cancellation_eur" in system
    assert "Απάντησε αποκλειστικά στα ελληνικά" in system


def test_the_sdk_client_waits_long_enough_for_a_thinking_answer(monkeypatch):
    """Thinking is always on: 30 s with 2 retries timed out into the fallback."""
    from api.config import Settings

    captured = {}
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: captured.update(kwargs) or object())

    PriceRecommendationAgent(api_key="test-key", model="claude-opus-5-5")._build_client()

    assert Settings.model_fields["anthropic_timeout_seconds"].default == 75.0
    assert captured == {"api_key": "test-key", "timeout": 75.0, "max_retries": 1}
