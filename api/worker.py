"""Standalone Python worker for RoomRate scheduled and queued scrape jobs."""

from __future__ import annotations

import asyncio
import logging
import signal

from api.config import settings, validate_production_settings
from api.logging_utils import JsonLogFormatter
from api.scheduler import SchedulerStatus, create_scheduler

logger = logging.getLogger(__name__)


async def run_worker() -> None:
    """Run the APScheduler worker until the process receives a stop signal.

    Returns:
        None.

    Raises:
        RuntimeError: If the process role or production configuration is invalid.
    """
    validate_production_settings(settings)
    if settings.process_role not in {"worker", "all"}:
        raise RuntimeError(
            "ROOMRATE_PROCESS_ROLE must be 'worker' (production) or 'all' (local development)"
        )
    if not settings.scheduler_enabled:
        raise RuntimeError("ROOMRATE_SCHEDULER_ENABLED must be true for the worker")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        """Ask the worker loop to stop after the current scheduler callback."""
        loop.call_soon_threadsafe(stop_event.set)

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, request_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(signal_name, lambda *_args: request_stop())

    scheduler = create_scheduler(status=SchedulerStatus())
    scheduler.start()
    logger.info("RoomRate Python worker started")
    try:
        await stop_event.wait()
    finally:
        scheduler.shutdown(wait=False)
        logger.info("RoomRate Python worker stopped")


def main() -> None:
    """Start the standalone worker process.

    Returns:
        None.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    if settings.log_format == "json":
        for handler in logging.getLogger().handlers:
            handler.setFormatter(JsonLogFormatter())
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
