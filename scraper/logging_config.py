"""Logging configuration shared by the RoomRate scraper package."""

from __future__ import annotations

import logging
import sys

log = logging.getLogger("roomrate.scraper")


def configure_logging() -> None:
    """Configure standalone scraper logging once."""
    if logging.getLogger().handlers:
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("scraper.log", encoding="utf-8"),
        ],
    )
