from __future__ import annotations

import asyncio
import json
import logging
import select
import threading
from typing import Any
from uuid import UUID

from fastapi import WebSocket
from sqlalchemy import text

from api.config import settings
from api.db import get_engine

logger = logging.getLogger(__name__)

# PostgreSQL LISTEN/NOTIFY channel name. Every API process LISTENs on this and
# publishers pg_notify() into it, so an alert raised on one process reaches a
# WebSocket connected to any other process.
CHANNEL = "roomrate_notifications"

# PostgreSQL NOTIFY payloads must stay under 8000 bytes. Stay well under that;
# anything larger collapses to a compact {id} form and the client refetches the
# full notification over REST.
MAX_NOTIFY_PAYLOAD_BYTES = 7500

# How often the listener loop wakes to check the stop flag while idle.
LISTEN_SELECT_TIMEOUT_SECONDS = 1.0

# Backoff before rebuilding a broken LISTEN connection so a dead database
# never turns the listener thread into a hot error loop.
LISTEN_RECONNECT_DELAY_SECONDS = 2.0


class ConnectionManager:
    """Account-scoped WebSocket registry for cross-process notifications.

    Sockets are grouped by account so a notification only ever reaches the
    tenant it belongs to. All methods are async and guarded by a single lock so
    concurrent connect/disconnect/send from the event loop and the listener
    thread's scheduled coroutines stay consistent.
    """

    def __init__(self) -> None:
        self.active_connections: dict[UUID, list[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, account_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            self.active_connections.setdefault(account_id, []).append(websocket)

    async def disconnect(self, account_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self.active_connections.get(account_id)
            if not sockets:
                return
            if websocket in sockets:
                sockets.remove(websocket)
            # Drop the account key entirely once its last socket closes.
            if not sockets:
                self.active_connections.pop(account_id, None)

    async def send_to_account(self, account_id: UUID, text: str) -> None:
        """Send a text frame to every live socket for one account."""
        async with self._lock:
            sockets = list(self.active_connections.get(account_id, []))
        for websocket in sockets:
            try:
                await websocket.send_text(text)
            except Exception:
                # A dead socket must not block delivery to its siblings; it is
                # reaped on its own disconnect path.
                logger.debug("Notification send to a socket failed", exc_info=True)


# Module singleton shared by the WS router (connect/disconnect) and main.py's
# NotificationListener (send_to_account).
manager = ConnectionManager()


class NotificationBroadcaster:
    """Publish side of cross-process notifications (sync, worker-thread safe).

    ``publish`` opens a short-lived connection from the shared "api" engine and
    issues ``SELECT pg_notify(...)``. It is callable from APScheduler worker
    threads and from request handlers. All failures are logged and swallowed —
    REST remains the source of truth, so a missed live push is non-fatal.
    """

    def publish(self, account_id: UUID, payload: dict) -> None:
        """Fan a single notification out to all listeners via pg_notify."""
        try:
            message = self._build_message(account_id, payload)
            engine = get_engine(role="api")
            with engine.connect() as connection:
                connection.execute(
                    text("SELECT pg_notify(:channel, :payload)"),
                    {"channel": CHANNEL, "payload": message},
                )
                # A plain connect() opens an implicit transaction (DML autobegin)
                # and does not autocommit; commit explicitly so the NOTIFY is
                # actually delivered to LISTENers.
                connection.commit()
        except Exception:
            logger.warning("Notification publish (pg_notify) failed", exc_info=True)

    def publish_many(self, account_id: UUID, payloads: list[dict]) -> None:
        """Fan a batch of notifications out on ONE connection checkout/commit.

        Same contract as ``publish``: the per-payload oversize→compact fallback
        is preserved and all failures are logged, never raised.
        """
        if not payloads:
            return
        try:
            messages = [self._build_message(account_id, payload) for payload in payloads]
            engine = get_engine(role="api")
            with engine.connect() as connection:
                for message in messages:
                    connection.execute(
                        text("SELECT pg_notify(:channel, :payload)"),
                        {"channel": CHANNEL, "payload": message},
                    )
                # Single explicit commit delivers the whole batch (DML autobegin).
                connection.commit()
        except Exception:
            logger.warning("Notification publish_many (pg_notify) failed", exc_info=True)

    @staticmethod
    def _build_message(account_id: UUID, payload: dict) -> str:
        """JSON envelope for a NOTIFY; collapses to compact form when oversize."""
        envelope = {"account_id": str(account_id), "notification": payload}
        encoded = json.dumps(envelope)
        if len(encoded.encode("utf-8")) <= MAX_NOTIFY_PAYLOAD_BYTES:
            return encoded
        # Oversize: send only the id so the client can refetch via REST.
        compact = {
            "account_id": str(account_id),
            "notification": {"id": payload.get("id")},
        }
        return json.dumps(compact)


class NotificationListener:
    """Daemon thread that LISTENs for notifications and fans them to WS clients.

    Holds a dedicated psycopg2 connection in autocommit mode running
    ``LISTEN roomrate_notifications``. The loop selects on the connection's
    socket; on wakeup it polls and drains ``conn.notifies``, dispatching each
    onto the asyncio loop captured at ``start()`` so the (async) connection
    manager can send to the right account's sockets.
    """

    def __init__(self, manager: ConnectionManager):
        self.manager = manager
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._connection: Any = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the running loop and start the listen thread.

        A failure to open the dedicated LISTEN connection is logged, not
        raised: the app keeps serving and REST stays the source of truth.
        """
        self._loop = loop
        try:
            self._connection = self._open_listen_connection()
        except Exception:
            logger.warning(
                "Notification listener could not LISTEN (cross-process push disabled); "
                "REST remains source of truth",
                exc_info=True,
            )
            self._connection = None
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._listen_loop, name="roomrate-notification-listener", daemon=True
        )
        self._thread.start()
        logger.info("Notification listener started (LISTEN %s)", CHANNEL)

    def stop(self) -> None:
        """Signal the loop to stop, join the thread, then close the connection.

        Closing the psycopg2 connection while the listener thread is still
        reading it would touch one libpq connection from two threads — not
        thread-safe, and a likely segfault. So if the join TIMES OUT (the loop
        is wedged in select/poll), we log a warning and deliberately SKIP the
        close, leaking the connection. The thread is a daemon, so the process
        can still exit; a leaked connection is the safe tradeoff.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
            if thread.is_alive():
                logger.warning(
                    "Notification listener did not exit within the stop timeout; "
                    "leaking its connection rather than closing it from another "
                    "thread (libpq is not thread-safe on one connection)"
                )
                self._thread = None
                self._connection = None
                return
            self._thread = None
        connection = self._connection
        if connection is not None:
            try:
                connection.close()
            except Exception:
                logger.debug("Listener connection close failed", exc_info=True)
            self._connection = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _open_listen_connection() -> Any:
        """Open a dedicated autocommit psycopg2 connection and issue LISTEN."""
        import psycopg2

        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is required for the notification listener")
        # SQLAlchemy DSNs may carry a +psycopg2 driver tag; psycopg2.connect
        # wants a plain libpq DSN.
        dsn = settings.database_url.replace("postgresql+psycopg2://", "postgresql://")
        connection = psycopg2.connect(dsn)
        connection.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        with connection.cursor() as cursor:
            cursor.execute(f"LISTEN {CHANNEL};")
        return connection

    def _listen_loop(self) -> None:
        """Block on the socket, drain notifications, dispatch, until stopped.

        A dead LISTEN connection (PostgreSQL restart, failover, network drop)
        raises on every select/poll: without the backoff + reconnect below the
        loop would spin hot and cross-process WS push would stay dead until a
        full process restart even after the database recovered.
        """
        connection = self._connection
        while not self._stop_event.is_set():
            try:
                ready, _, _ = select.select(
                    [connection], [], [], LISTEN_SELECT_TIMEOUT_SECONDS
                )
                if not ready:
                    continue
                connection.poll()
                while connection.notifies:
                    notify = connection.notifies.pop(0)
                    self._dispatch_notify(notify)
            except Exception:
                logger.warning("Notification listener loop error", exc_info=True)
                # Wait on the stop event (not sleep) so stop() stays responsive,
                # then rebuild the LISTEN connection before resuming.
                if self._stop_event.wait(LISTEN_RECONNECT_DELAY_SECONDS):
                    break
                try:
                    connection.close()
                except Exception:
                    logger.debug("Closing broken listener connection failed", exc_info=True)
                try:
                    connection = self._open_listen_connection()
                    self._connection = connection
                    logger.info("Notification listener reconnected (LISTEN %s)", CHANNEL)
                except Exception:
                    logger.warning("Notification listener reconnect failed", exc_info=True)

    def _dispatch_notify(self, notify: Any) -> None:
        """Parse one NOTIFY payload and forward it to the owning account's WS."""
        try:
            envelope = json.loads(notify.payload)
            account_id_raw = envelope.get("account_id")
            if not account_id_raw:
                return
            account_id = UUID(str(account_id_raw))
        except (ValueError, TypeError, json.JSONDecodeError):
            logger.debug("Dropping malformed NOTIFY payload", exc_info=True)
            return
        # Forward the original JSON text so the client gets the full envelope.
        self._run_on_loop(self.manager.send_to_account(account_id, notify.payload))

    def _run_on_loop(self, coro: Any) -> None:
        """Schedule a coroutine on the captured asyncio loop from this thread."""
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)
