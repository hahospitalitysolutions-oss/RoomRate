import pytest

from api.services.retry_policy import calculate_exponential_backoff_seconds


def test_backoff_grows_exponentially() -> None:
    delay = calculate_exponential_backoff_seconds(30, 3, 900, randomizer=lambda _a, b: b)
    assert delay == 120


def test_backoff_is_capped() -> None:
    delay = calculate_exponential_backoff_seconds(30, 10, 900, randomizer=lambda _a, b: b)
    assert delay == 900


def test_backoff_supports_full_jitter_lower_bound() -> None:
    delay = calculate_exponential_backoff_seconds(30, 2, 900, randomizer=lambda a, _b: a)
    assert delay == 0


@pytest.mark.parametrize(
    ("base_seconds", "attempt", "max_seconds"),
    [(0, 1, 60), (5, 0, 60), (5, 1, 4)],
)
def test_backoff_rejects_invalid_parameters(base_seconds, attempt, max_seconds) -> None:
    with pytest.raises(ValueError, match="Invalid retry backoff parameters"):
        calculate_exponential_backoff_seconds(base_seconds, attempt, max_seconds)
