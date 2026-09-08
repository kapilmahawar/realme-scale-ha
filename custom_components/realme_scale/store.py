"""Per-entry persistent measurement store.

A tiny Home Assistant ``Store`` wrapper around the JSON record schema
defined in :mod:`records`.  It keeps a bounded history so the "unknown"
queue survives restarts and can be assigned to a user at any later time.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import storage

from .const import (
    DOMAIN,
    FIELD_MEASUREMENT_ID,
    STORE_MAX_ASSIGNED,
    STORE_MAX_UNKNOWN,
    STORE_VERSION,
)

__all__ = ["MeasurementStore"]


def _received_at(record: dict[str, Any]) -> str:
    return record.get("received_at", "")


def _prune(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the oldest records beyond the per-status caps."""
    assigned = [r for r in records if r.get("status") == "assigned"]
    unknown = [r for r in records if r.get("status") != "assigned"]
    for bucket, cap in ((assigned, STORE_MAX_ASSIGNED), (unknown, STORE_MAX_UNKNOWN)):
        if len(bucket) > cap:
            bucket.sort(key=_received_at, reverse=True)
            del bucket[cap:]
    combined = assigned + unknown
    combined.sort(key=_received_at)
    return combined


class MeasurementStore:
    """JSON-backed history of measurements for one scale config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        # Key is namespaced by entry so multiple scales never collide.
        self._store: storage.Store[list[dict[str, Any]]] = storage.Store(
            hass, STORE_VERSION, f"{DOMAIN}.measurements.{entry_id}"
        )
        self._records: list[dict[str, Any]] = []

    async def async_load(self) -> None:
        """Load persisted records (safe to call more than once)."""
        loaded = await self._store.async_load()
        self._records = _prune(list(loaded or []))

    async def async_save(self) -> None:
        """Persist the current (pruned) record list."""
        await self._store.async_save(self._records)

    # -- reads ------------------------------------------------------------

    def records(self) -> list[dict[str, Any]]:
        """All records, oldest first."""
        return list(self._records)

    def unknown_records(self) -> list[dict[str, Any]]:
        """Records not yet attributed to a user."""
        return [r for r in self._records if r.get("status") != "assigned"]

    def assigned_records(self, limit: int = 25) -> list[dict[str, Any]]:
        """Most recently received assigned records (newest first)."""
        assigned = [r for r in self._records if r.get("status") == "assigned"]
        assigned.sort(key=_received_at, reverse=True)
        return assigned[:limit]

    def record(self, measurement_id: str) -> dict[str, Any] | None:
        """One record by id, or ``None``."""
        for record in self._records:
            if record.get(FIELD_MEASUREMENT_ID) == measurement_id:
                return record
        return None

    def latest_assigned_for_user(self, user_id: str) -> dict[str, Any] | None:
        """Most recently received assigned record for a user, or ``None``."""
        for record in reversed(self._records):
            if record.get("user_id") == user_id and record.get("status") == "assigned":
                return record
        return None

    # -- writes -----------------------------------------------------------

    async def async_add(self, record: dict[str, Any]) -> None:
        """Append one record and persist (applying size caps)."""
        self._records.append(record)
        self._records = _prune(self._records)
        await self.async_save()

    async def async_release_user(self, user_id: str) -> None:
        """Unassign every record that belonged to a deleted user.

        History is preserved: records stay in the store but become
        unassigned so they can be attributed to another user later.
        """
        changed = False
        for record in self._records:
            if record.get("user_id") != user_id:
                continue
            record["status"] = "unknown"
            record.pop("user_id", None)
            record.pop("user_name", None)
            changed = True
        if changed:
            await self.async_save()
