"""Unit tests for measurement record serialization."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.realme_scale.records import (
    display_label,
    measurement_from_record,
    new_measurement_id,
    record_from_measurement,
)
from custom_components.realme_scale.scale_controller import ScaleMeasurement


def _sample_measurement() -> ScaleMeasurement:
    return ScaleMeasurement(
        weight_kg=75.2,
        measured_at=datetime(2026, 2, 10, 8, 15, tzinfo=UTC),
        impedance=520,
        user_id="ualice123",
        user_name="Alice",
        body_fat=22.0,
        muscle=52.0,
        water=56.6,
        bone_kg=3.2,
        lean_body_mass_kg=58.6,
        visceral_fat=9.0,
    )


def test_record_roundtrip() -> None:
    record = record_from_measurement(
        _sample_measurement(),
        measurement_id="abc123",
        status="assigned",
        received_at=datetime(2026, 2, 10, 8, 16, tzinfo=UTC),
    )
    assert record["measurement_id"] == "abc123"
    assert record["status"] == "assigned"
    assert record["user_id"] == "ualice123"
    assert record["weight_kg"] == 75.2

    restored = measurement_from_record(record)
    assert restored.weight_kg == 75.2
    assert restored.impedance == 520
    assert restored.user_id == "ualice123"
    assert restored.user_name == "Alice"
    assert restored.measured_at == datetime(2026, 2, 10, 8, 15, tzinfo=UTC)
    for attr in ("body_fat", "muscle", "water", "bone_kg",
                 "lean_body_mass_kg", "visceral_fat"):
        assert getattr(restored, attr) == getattr(_sample_measurement(), attr)


def test_record_missing_optional_fields() -> None:
    """A weight-only record has no impedance / BIA and no owner."""
    measurement = ScaleMeasurement(
        weight_kg=75.2,
        measured_at=datetime(2026, 2, 10, 8, 15, tzinfo=UTC),
    )
    record = record_from_measurement(
        measurement, measurement_id="x1", status="unknown"
    )
    assert record["status"] == "unknown"
    assert "user_id" not in record
    assert "impedance" not in record

    restored = measurement_from_record(record)
    assert restored.impedance is None
    assert restored.body_fat is None
    assert restored.user_id is None


def test_ids_are_unique_and_short() -> None:
    assert len(new_measurement_id()) == 12
    assert new_measurement_id() != new_measurement_id()


def test_display_label() -> None:
    record = record_from_measurement(
        _sample_measurement(), measurement_id="abc", status="unknown"
    )
    label = display_label(record)
    assert "75.2" in label
