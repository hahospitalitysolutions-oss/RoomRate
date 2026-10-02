"""Small value objects returned by the scraper pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


RESULT_SUMMARY_VERSION = 1


def empty_result_summary(*, rows_seen: int = 0) -> dict[str, object]:
    """Return the stable, versioned summary emitted by every successful run."""
    return {
        "version": RESULT_SUMMARY_VERSION,
        "rows_seen": max(0, rows_seen),
        "filter_counts": {},
        "rows_written": 0,
        # Round 6: always a list. The CLI adds nearby_scout_failed:<destination>
        # and the API appends its own (radius_skipped_no_coordinates).
        "warnings": [],
    }

@dataclass(frozen=True)
class PersistResult:
    rows_seen: int = 0
    csv_written: bool = False
    normalized_written: int = 0
    normalization_failed: int = 0
    result_summary: dict[str, object] = field(default_factory=empty_result_summary)


# ===========================================================
