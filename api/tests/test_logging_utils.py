import json
import logging
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.logging_utils import JsonLogFormatter, RequestIdFilter, request_id_var
from api.main import app
from api.middleware import RequestIDMiddleware


def make_record(msg: str = "hello %s", args=("world",), **extras) -> logging.LogRecord:
    record = logging.LogRecord(
        name="api.services.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=42,
        msg=msg,
        args=args,
        exc_info=None,
    )
    for key, value in extras.items():
        setattr(record, key, value)
    return record


# ----------------------------------------------------------------------------
# JsonLogFormatter
# ----------------------------------------------------------------------------


def test_formatter_emits_valid_json_with_core_fields():
    payload = json.loads(JsonLogFormatter().format(make_record()))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "api.services.test"
    assert payload["message"] == "hello world"
    assert "timestamp" in payload


def test_formatter_includes_structured_extras():
    # The extras the codebase already attaches via extra= (scheduler/services).
    account_id = uuid.uuid4()
    record = make_record(
        account_id=str(account_id),
        job_id="job-1",
        notification_count=3,
        request_id="req-abc",
    )

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["account_id"] == str(account_id)
    assert payload["job_id"] == "job-1"
    assert payload["notification_count"] == 3
    assert payload["request_id"] == "req-abc"


def test_formatter_serializes_non_json_extras_via_str():
    # UUID objects (not pre-stringified) must not crash the formatter.
    raw_uuid = uuid.uuid4()
    payload = json.loads(JsonLogFormatter().format(make_record(config_id=raw_uuid)))

    assert payload["config_id"] == str(raw_uuid)


def test_formatter_includes_exception_text():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = make_record()
        record.exc_info = sys.exc_info()

    payload = json.loads(JsonLogFormatter().format(record))

    assert "ValueError: boom" in payload["exc_info"]


# ----------------------------------------------------------------------------
# RequestIdFilter
# ----------------------------------------------------------------------------


def test_filter_injects_request_id_from_contextvar():
    token = request_id_var.set("ctx-request-id")
    try:
        record = make_record()
        assert RequestIdFilter().filter(record) is True
        assert record.request_id == "ctx-request-id"
    finally:
        request_id_var.reset(token)


def test_filter_leaves_records_untouched_outside_requests():
    record = make_record()
    assert RequestIdFilter().filter(record) is True
    assert not hasattr(record, "request_id")


# ----------------------------------------------------------------------------
# RequestIDMiddleware
# ----------------------------------------------------------------------------


def test_response_carries_generated_request_id():
    client = TestClient(app)

    response = client.get("/health")

    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 32  # uuid4().hex
    int(request_id, 16)  # hex-parseable


def test_inbound_request_id_is_honored_and_echoed():
    client = TestClient(app)

    response = client.get("/health", headers={"X-Request-ID": "trace-me-123"})

    assert response.headers["X-Request-ID"] == "trace-me-123"


def test_request_id_is_available_in_request_context():
    context_app = FastAPI()

    @context_app.get("/whoami")
    async def whoami():
        return {"request_id": request_id_var.get()}

    context_app.add_middleware(RequestIDMiddleware)
    client = TestClient(context_app)

    response = client.get("/whoami", headers={"X-Request-ID": "ctx-42"})

    assert response.json() == {"request_id": "ctx-42"}
    # And the contextvar is reset once the request is over.
    assert request_id_var.get() is None


def test_oversized_inbound_request_id_is_truncated():
    client = TestClient(app)

    response = client.get("/health", headers={"X-Request-ID": "x" * 500})

    assert len(response.headers["X-Request-ID"]) == 128
