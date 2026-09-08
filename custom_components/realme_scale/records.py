"""Persistent measurement record helpers (pure logic, no HA imports).

The integration stores every parsed measurement so that unassigned ones can
be attributed to a user later.  A *record* is a plain JSON-safe dict; this
module converts between records and :class:`ScaleMeasurement` objects and
knows the record schema so the store wrapper stays trivial.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .const import (
    FIELD_MEASUREMENT_ID,
    STATUS_ASSIGNED,
)
from .scale_controller import ScaleMeasurement

# Metric columns carried in a record (all optional, recomputed on assignment
# under the assigned user's profile).
BIA_FIELDS: tuple[str, ...] = (
    "body_fat",
    "muscle",
    "water",
    "bone_kg",
    "lean_body_mass_kg",
    "visceral_fat",
)

_FIELD_MAP: dict[str, str] = {  # record key -> ScaleMeasurement attribute
    "weight_kg": "weight_kg",
    "impedance": "impedance",
    "user_id": "user_id",
    "user_name": "user_name",
    "assignment_method": "assignment_method",
    "confidence": "confidence",
}


def new_measurement_id() -> str:
    """A short, collision-safe id for one stored measurement."""
    return uuid4().hex[:12]


def _iso(dt: datetime | str | None) -> str:
    if isinstance(dt, str):
        return dt  # already serialized (stored record being rewritten)
    if dt is None:
        dt = datetime.now(tz=UTC)
    return dt.astimezone(UTC).isoformat()


def record_from_measurement(
    measurement: ScaleMeasurement,
    *,
    measurement_id: str | None = None,
    status: str = STATUS_ASSIGNED,
    received_at: datetime | None = None,
) -> dict[str, Any]:
    """Serialize a ScaleMeasurement into a JSON-safe record dict."""
    record: dict[str, Any] = {
        FIELD_MEASUREMENT_ID: measurement_id or new_measurement_id(),
        "status": status,
        "measured_at": _iso(measurement.measured_at),
        "received_at": _iso(received_at or datetime.now(tz=UTC)),
    }
    for record_key, attr in _FIELD_MAP.items():
        value = getattr(measurement, attr)
        if value is not None:
            record[record_key] = value
    for field in BIA_FIELDS:
        value = getattr(measurement, field)
        if value is not None:
            record[field] = value
    return record


def measurement_from_record(record: dict[str, Any]) -> ScaleMeasurement:
    """Rebuild a ScaleMeasurement from a stored record."""
    measured_at_raw = record.get("measured_at")
    try:
        measured_at = (
            datetime.fromisoformat(measured_at_raw)
            if measured_at_raw
            else datetime.now(tz=UTC)
        )
    except (TypeError, ValueError):
        measured_at = datetime.now(tz=UTC)

    measurement = ScaleMeasurement(
        weight_kg=float(record["weight_kg"]),
        measured_at=measured_at,
        impedance=(
            int(record["impedance"]) if record.get("impedance") is not None else None
        ),
        user_id=record.get("user_id"),
        user_name=record.get("user_name"),
        assignment_method=record.get("assignment_method"),
        confidence=(
            float(record["confidence"]) if record.get("confidence") is not None else None
        ),
    )
    for field in BIA_FIELDS:
        value = record.get(field)
        if value is not None:
            setattr(measurement, field, float(value))
    return measurement


def display_label(record: dict[str, Any]) -> str:
    """Human label for one record, e.g. ``12:04 - 75.2 kg (10 Feb)``."""
    try:
        when = datetime.fromisoformat(record["measured_at"])
    except (KeyError, TypeError, ValueError):
        when = None
    weight = float(record.get("weight_kg", 0.0))
    time_part = when.strftime("%H:%M %d %b") if when else "?"
    return f"{time_part} - {weight:.1f} kg"
