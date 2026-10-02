from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from api.services.schedule_timing import DEFAULT_HOUR_LOCAL, DEFAULT_TIMEZONE


class ScheduleConfigResponse(BaseModel):
    """Per-account scrape schedule configuration.

    Field defaults mirror the DB column defaults so the API can answer
    GET /schedule with sensible values before any row exists.
    """

    account_id: UUID
    enabled: bool = False
    frequency_hours: int = 24
    # The hour the owner picked, on their own clock (``timezone``); the
    # schedule runs at it all year, across daylight-saving changes.
    hour_local: int = DEFAULT_HOUR_LOCAL
    timezone: str = DEFAULT_TIMEZONE
    # Deprecated: ``hour_local`` as a UTC hour TODAY, for clients built before
    # ``hour_local`` existed. Moves by one at every daylight-saving change.
    hour_utc: int = 5
    lead_days: int = 30
    nights: int = 3
    adults: int = 2
    children: int = 0
    rooms: int = 1
    consecutive_failures: int = 0
    last_run_at: datetime | None = None


class ScheduleConfigUpdate(BaseModel):
    """Partial schedule update; ranges mirror the DB CHECK constraints."""

    enabled: bool | None = None
    frequency_hours: int | None = Field(default=None, ge=1)
    hour_local: int | None = Field(default=None, ge=0, le=23)
    # Deprecated: a client built before ``hour_local`` sends the UTC hour it
    # converted with today's offset; it is read back the same way. Ignored
    # whenever ``hour_local`` is sent too.
    hour_utc: int | None = Field(default=None, ge=0, le=23)
    lead_days: int | None = Field(default=None, ge=0)
    nights: int | None = Field(default=None, ge=1)
    adults: int | None = Field(default=None, ge=1)
    children: int | None = Field(default=None, ge=0)
    rooms: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_nulls(cls, data: object) -> object:
        """Reject explicit JSON nulls for fields backed by NOT NULL columns.

        Every field here is Optional only to express "not sent" (partial
        update). An explicit ``{"frequency_hours": null}`` would survive
        ``model_dump(exclude_unset=True)`` and hit the DB's NOT NULL
        constraint as a 500; fail fast with a 422 instead.
        """
        if isinstance(data, dict):
            null_fields = sorted(
                key for key, value in data.items() if value is None and key in cls.model_fields
            )
            if null_fields:
                raise ValueError(
                    f"Fields cannot be null (omit them to keep current values): {', '.join(null_fields)}"
                )
        return data
