"""Public API for the modular RoomRate Booking.com scraper."""

from .clients import build_client, build_engine
from .cli import build_config_from_args, main
from .config import ScraperConfig
from .models import PersistResult
from .persistence import persist_results
from .provider import ActorRunError, fetch_deep_room_data, fetch_hotel_list, fetch_hotel_lists
from .transform import process_and_flatten_data

__all__ = [
    "ActorRunError",
    "PersistResult",
    "ScraperConfig",
    "build_client",
    "build_config_from_args",
    "build_engine",
    "fetch_deep_room_data",
    "fetch_hotel_list",
    "fetch_hotel_lists",
    "main",
    "persist_results",
    "process_and_flatten_data",
]
