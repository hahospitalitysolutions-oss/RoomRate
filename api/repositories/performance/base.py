"""What every «Απόδοση» repository part shares: the engine and the account + property fence."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from typing import Any

from sqlalchemy.engine import Engine

from api.db import get_engine


def fence(account_id: uuid.UUID, owned_property_id: uuid.UUID) -> dict[str, Any]:
    """The bound parameters every query is fenced by."""
    return {"account_id": account_id, "owned_property_id": owned_property_id}


def uuid_texts(values: Iterable[Any]) -> list[str | None]:
    """UUIDs as text for an array parameter: psycopg2 adapts no uuid.UUID in an array without register_uuid()."""
    return [None if value is None else str(value) for value in values]


class RepositoryBase:
    def __init__(self, engine_factory: Callable[[str], Engine] | None = None):
        self._engine_factory = engine_factory

    def _engine(self, role: str = "api") -> Engine:
        return self._engine_factory(role) if self._engine_factory else get_engine(role=role)
