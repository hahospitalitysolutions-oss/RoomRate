"""Gross and net amounts: the one definition both the upload preview and the KPIs use.

Gross is what the guest paid (VAT included, and the resilience fee when the
owner says the price includes it). Net is what the property keeps:

    net = (gross − resilience fee, when in the price) / (1 + VAT) − commission

``settings`` are the owner's /revenue-settings: ``vat_pct``,
``resilience_fee_eur`` (per room and night), ``fee_included_in_price``.
"""

from __future__ import annotations

from typing import Any


def net_amount(gross_eur: float, commission_eur: float, *, rooms: int, nights: int, settings: dict[str, Any]) -> float:
    """What the property keeps of ``gross_eur`` for ``rooms`` × ``nights``; not rounded."""
    fee = settings["resilience_fee_eur"] * rooms * nights if settings["fee_included_in_price"] else 0.0
    return (gross_eur - fee) / (1 + settings["vat_pct"] / 100) - commission_eur


def night_amount(night: dict[str, Any], amounts: str, settings: dict[str, Any] | None) -> float:
    """One stored night (its share of a booking) as gross or net."""
    gross = float(night["gross_eur"])
    if amounts != "net":
        return gross
    return net_amount(gross, float(night["commission_eur"]), rooms=night["rooms"], nights=1, settings=settings)
