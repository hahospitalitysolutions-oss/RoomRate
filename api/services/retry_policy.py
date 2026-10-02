"""Reusable retry policies for durable background work."""

from __future__ import annotations

import random
from collections.abc import Callable


def calculate_exponential_backoff_seconds(
    base_seconds: int,
    attempt: int,
    max_seconds: int,
    randomizer: Callable[[float, float], float] = random.uniform,
) -> float:
    """Calculate capped exponential backoff with full jitter.

    Args:
        base_seconds: Initial retry window in seconds.
        attempt: One-based failed attempt number.
        max_seconds: Maximum retry window in seconds.
        randomizer: Injectable random function used for deterministic tests.

    Returns:
        A randomized delay between zero and the capped exponential window.

    Raises:
        ValueError: If the retry policy parameters are invalid.
    """
    if base_seconds < 1 or attempt < 1 or max_seconds < base_seconds:
        raise ValueError("Invalid retry backoff parameters")
    retry_window = min(max_seconds, base_seconds * (2 ** (attempt - 1)))
    return randomizer(0.0, float(retry_window))
