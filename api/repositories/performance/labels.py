"""The room labels of the owner's files and the owned room type each one means (or ignored)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import text

from api.repositories.performance.base import RepositoryBase, fence


class LabelQueries(RepositoryBase):
    def label_mappings(self, account_id: uuid.UUID, owned_property_id: uuid.UUID) -> dict[str, dict[str, Any]]:
        """label → ``{"room_type_id", "ignored"}`` remembered from earlier uploads."""
        with self._engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT label, room_type_id, ignored
                    FROM roomrate_performance_label_mappings
                    WHERE account_id = :account_id AND owned_property_id = :owned_property_id
                    """
                ),
                fence(account_id, owned_property_id),
            ).mappings().all()
        return {row["label"]: {"room_type_id": row["room_type_id"], "ignored": row["ignored"]} for row in rows}

    def save_label_mappings(
        self,
        account_id: uuid.UUID,
        owned_property_id: uuid.UUID,
        mappings: Iterable[dict[str, Any]],
    ) -> None:
        """Remember file room labels: ``{"label", "room_type_id"}`` maps one, ``{"label", "ignored": True}`` skips it.

        A room type not of this property is not stored.
        """
        by_label = {
            mapping["label"]: mapping for mapping in mappings
            if mapping.get("ignored") or mapping.get("room_type_id") is not None
        }
        mappings = list(by_label.values())
        if not mappings:
            return
        with self._engine("writer").begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO roomrate_performance_label_mappings (
                        owned_property_id, label, account_id, room_type_id, ignored
                    )
                    SELECT :owned_property_id, m.label, :account_id,
                           CASE WHEN m.ignored THEN NULL ELSE rt.id END, m.ignored
                    FROM unnest(
                        CAST(:labels AS text[]),
                        CAST(:room_type_ids AS uuid[]),
                        CAST(:ignored AS boolean[])
                    ) AS m(label, room_type_id, ignored)
                    LEFT JOIN roomrate_owned_property_room_types rt
                      ON rt.id = m.room_type_id
                     AND rt.account_id = :account_id
                     AND rt.owned_property_id = :owned_property_id
                    WHERE m.ignored OR rt.id IS NOT NULL
                    ON CONFLICT (owned_property_id, label) DO UPDATE SET
                        room_type_id = EXCLUDED.room_type_id,
                        ignored = EXCLUDED.ignored,
                        updated_at = now()
                    WHERE roomrate_performance_label_mappings.account_id = :account_id
                    """
                ),
                {
                    **fence(account_id, owned_property_id),
                    "labels": [mapping["label"] for mapping in mappings],
                    "room_type_ids": [
                        None if mapping.get("ignored") else str(mapping["room_type_id"]) for mapping in mappings
                    ],
                    "ignored": [bool(mapping.get("ignored")) for mapping in mappings],
                },
            )

    def delete_label_mappings(self, account_id: uuid.UUID, owned_property_id: uuid.UUID, labels: Sequence[str]) -> None:
        if not labels:
            return
        with self._engine("writer").begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM roomrate_performance_label_mappings
                    WHERE account_id = :account_id AND owned_property_id = :owned_property_id
                      AND label = ANY(CAST(:labels AS text[]))
                    """
                ),
                {**fence(account_id, owned_property_id), "labels": list(labels)},
            )

