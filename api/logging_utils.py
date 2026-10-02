"""Structured JSON logging and request-id correlation (stdlib only).

Opt-in via LOG_FORMAT=json: the root handlers get JsonLogFormatter so every
record becomes one JSON object per line, including the structured ``extra=``
fields the codebase already attaches (job_id, account_id, config_id,
notification_count, ...). RequestIdFilter stamps the current request's
correlation id onto every record in BOTH text and json modes.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone

# Correlation id for the current request; set by RequestIDMiddleware.
# Contextvars survive anyio's to_thread hop, so sync endpoints running in the
# Starlette threadpool still log the id of the request that spawned them.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord carries out of the box. Anything NOT listed here
# arrived via ``extra=`` and is emitted as a structured JSON field. Excluding
# the standard set (instead of whitelisting known extras) means new extras
# added anywhere in the codebase show up in JSON logs automatically.
_STANDARD_LOGRECORD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


class JsonLogFormatter(logging.Formatter):
    """Emit one JSON object per record: timestamp/level/logger/message + extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOGRECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # default=str: extras are often UUIDs/datetimes — a log call must
        # never crash on serialization.
        return json.dumps(payload, default=str)


class RequestIdFilter(logging.Filter):
    """Stamp the current request id (when inside a request) onto each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = request_id_var.get()
        if request_id is not None and not hasattr(record, "request_id"):
            record.request_id = request_id
        return True
