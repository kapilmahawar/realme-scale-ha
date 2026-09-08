"""Static UI/UX metadata regression tests (no HA runtime required).

Guards the presentation-only polish: canonical branding, name-only user
devices, stable identifiers preserved, diagnostics categorized, and
destructive remove-user wording matching the v0.7 deletion behavior.
"""

from __future__ import annotations

import json
import pathlib

from custom_components.realme_scale.const import MANUFACTURER, MODEL

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "realme_scale"
SENSOR = COMPONENT / "sensor.py"
BINARY = COMPONENT / "binary_sensor.py"
SELECT = COMPONENT / "select.py"
STRINGS = COMPONENT / "strings.json"
EN = COMPONENT / "translations" / "en.json"


def test_model_metadata_is_rmh2011() -> None:
    assert MANUFACTURER == "realme"
    assert MODEL == "RMH2011"


def test_scale_device_name_is_canonical_without_mac() -> None:
    sensor = SENSOR.read_text(encoding="utf-8")
    # Scale friendly name is the canonical brand, not the MAC title.
    assert "name=DEFAULT_NAME" in sensor
    assert "entry.title" not in sensor.split("def _user_device_info")[0].split(
        "def _scale_device_info"
    )[1]


def test_user_device_name_is_just_the_user_name() -> None:
    sensor = SENSOR.read_text(encoding="utf-8")
    assert 'name=user.name or "User"' in sensor
    assert "Realme Smart Scale} - {user.name" not in sensor


def test_stable_device_identifiers_preserved() -> None:
    sensor = SENSOR.read_text(encoding="utf-8")
    assert "def _user_device_identifier(address" in sensor
    assert 'f"{address}_{user_id}"' in sensor
    assert 'return (DOMAIN, f"{self.address}_{user_id}")' in (
        COMPONENT / "coordinator.py"
    ).read_text(encoding="utf-8")


def test_scale_level_entities_use_canonical_brand() -> None:
    for path in (BINARY, SELECT):
        text = path.read_text(encoding="utf-8")
        assert "name=DEFAULT_NAME" in text
        assert "entry.title" not in text


def test_last_measured_is_diagnostic() -> None:
    sensor = SENSOR.read_text(encoding="utf-8")
    segment = sensor[sensor.index('key="last_measured"') :]
    segment = segment[: segment.index(")")]
    assert "EntityCategory.DIAGNOSTIC" in segment


def test_remove_user_wording_matches_destructive_delete() -> None:
    for language_file in (STRINGS, EN):
        data = json.loads(language_file.read_text(encoding="utf-8"))
        confirm = data["options"]["step"]["remove_user_confirm"]
        description = confirm["description"]
        assert "permanently delete" in description
        assert "stored measurements" in description
        assert "sensors" in description and "device" in description
        assert "cannot be undone" in description
        # No stale "kept as unassigned" wording remains in the options copy.
        assert "kept but become unassigned" not in description


def test_select_hides_internal_user_ids() -> None:
    select = SELECT.read_text(encoding="utf-8")
    assert "def _display_name" in select
    assert "duplicate_names" in select
    # The plain (non-duplicate) option never embeds the stable user id.
    assert 'return f"{name} ({user.user_id[-6:]})"' in select
    assert 'return name' in select


def test_sensor_availability_does_not_depend_on_ble_connection() -> None:
    """Measurement sensors stay available with last-known data when the
    scale disconnects; only the Connected binary sensor reports link state.
    """
    sensor = SENSOR.read_text(encoding="utf-8")
    assert "coordinator.connected" not in sensor, (
        "measurement sensors must not gate availability on BLE link state"
    )
    assert "Available whenever we have a (last known) measurement value." in sensor
    binary = BINARY.read_text(encoding="utf-8")
    assert "coordinator.connected" in binary
