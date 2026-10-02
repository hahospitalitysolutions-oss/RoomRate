"""The owner's reference price: the one rule the recommendation and the chart share.

Spec 2026-09-29 §4: the per-hotel minimums are like-for-like in the
cancellation class of the owner's reference PACKAGE. The recommendation
(api/routers/agents.py) and the price-history chart (api/routers/market.py)
must resolve that package identically, or the chart's latest median and the
recommendation's market median drift apart for the same inputs — so both call
``resolve_own_reference_price`` instead of repeating the lookup.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from api.schemas.agents import OwnPriceSource


def resolve_own_reference_price(
    price_history_repository: Any,
    *,
    account_id: Any,
    canonical_destination: str,
    check_in: date,
    check_out: date,
    adults: int,
    children: int,
    rooms: int,
    room_type_category: str | None,
    display_name: str | None,
    sample_price: float | None = None,
) -> tuple[float | None, OwnPriceSource | None, str | None]:
    """Own reference price, in order: live Booking row of the latest run, onboarding sample, none.

    The live row is what the market actually sees for these dates; the
    onboarding sample is a typed-in number that goes stale. Neither is ever
    counted as a competitor (the repository CTE excludes the own hotel).

    The third element is the reference PACKAGE's cancellation class (spec
    2026-09-29 §4), which scopes the like-for-like history minimums. A
    typed-in sample has no package, so its class is None.
    """
    live_reference = price_history_repository.fetch_own_live_price(
        account_id=account_id,
        canonical_destination=canonical_destination,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        children=children,
        rooms=rooms,
        room_type_category=room_type_category,
        display_name=display_name,
    )
    if live_reference is not None:
        return (
            round(live_reference["price"], 2),
            "booking_live",
            live_reference.get("cancellation_type"),
        )
    if sample_price is not None:
        return round(sample_price, 2), "onboarding_sample", None
    return None, None, None
