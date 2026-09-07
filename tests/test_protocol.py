"""Unit tests for the RMH2011 protocol core (no BLE / HA required)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.realme_scale.scale_controller import (
    ScaleUser,
    build_handshake,
    is_measurement_packet,
    mac_string_to_bytes,
    obfuscate,
    parse_measurement,
)

# MAC used in the project context: AA:BB:CC:DD:EE:FF
MAC = "AA:BB:CC:DD:EE:FF"
MAC_BYTES = bytes((0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF))


def test_mac_parsing() -> None:
    assert mac_string_to_bytes(MAC) == MAC_BYTES
    assert mac_string_to_bytes("aa:bb:cc:dd:ee:ff") == MAC_BYTES
    with pytest.raises(ValueError):
        mac_string_to_bytes("not-a-mac")


def test_obfuscate_roundtrip() -> None:
    payload = bytes(range(40))
    encoded = obfuscate(payload, MAC_BYTES)
    assert encoded != payload
    assert obfuscate(encoded, MAC_BYTES) == payload


def test_handshake_structure_and_roundtrip() -> None:
    user = ScaleUser(name="Alice", sex="male", age=35, height_cm=175.0,
                     activity_level="moderate", initial_weight=75.0)
    now = 1_800_000_000
    cmds = build_handshake(user, MAC_BYTES, now=now, tz_offset_min=330)

    assert len(cmds) == 6

    for cmd in cmds:
        # Wire format: 0x10 header, length byte, XOR-obfuscated payload.
        assert cmd[0] == 0x10
        assert cmd[1] == len(cmd) - 2
        # XOR is an involution: double-encryption restores the body.
        assert obfuscate(obfuscate(cmd[2:], MAC_BYTES), MAC_BYTES) == cmd[2:]


def test_handshake_deterministic_given_clock() -> None:
    user = ScaleUser(sex="female", age=28, height_cm=160.0)
    a = build_handshake(user, MAC_BYTES, now=123456, tz_offset_min=-240)
    b = build_handshake(user, MAC_BYTES, now=123456, tz_offset_min=-240)
    assert a == b


def _make_wire_packet(
    weight_raw: int, scale_time: int, impedance: int, mac: bytes = MAC_BYTES
) -> bytes:
    """Build a fake 0xA621 notification the way the scale would send it."""
    payload = bytearray(17)
    payload[8] = (weight_raw >> 8) & 0xFF
    payload[9] = weight_raw & 0xFF
    payload[10] = (scale_time >> 24) & 0xFF
    payload[11] = (scale_time >> 16) & 0xFF
    payload[12] = (scale_time >> 8) & 0xFF
    payload[13] = scale_time & 0xFF
    payload[14] = (impedance >> 8) & 0xFF
    payload[15] = impedance & 0xFF
    body = obfuscate(bytes(payload), mac)
    return bytes((0x10, 0x11)) + body


def test_packet_header_check() -> None:
    assert is_measurement_packet(_make_wire_packet(7500, 0, 0))
    assert not is_measurement_packet(b"\x10\x0f" + b"\x00" * 17)
    assert not is_measurement_packet(b"\x00\x11" + b"\x00" * 17)


def test_parse_weight_only() -> None:
    user = ScaleUser(sex="male", age=35, height_cm=175.0)
    packet = _make_wire_packet(weight_raw=7500, scale_time=0, impedance=0)
    m = parse_measurement(packet, MAC_BYTES, user)
    assert m is not None
    assert m.weight_kg == pytest.approx(75.0)
    assert m.impedance is None
    assert m.body_fat is None  # no impedance -> no BIA


def test_parse_with_impedance_computes_bia() -> None:
    user = ScaleUser(sex="male", age=35, height_cm=175.0,
                     activity_level="moderate")
    scale_time = 1_800_000_000
    packet = _make_wire_packet(7500, scale_time, 520, MAC_BYTES)
    m = parse_measurement(packet, MAC_BYTES, user)
    assert m is not None
    assert m.weight_kg == pytest.approx(75.0)
    assert m.impedance == 520
    assert m.measured_at == datetime.fromtimestamp(scale_time, tz=UTC)
    # A plausible male body-fat reading for this profile / impedance.
    assert m.body_fat is not None and 5.0 <= m.body_fat <= 45.0
    assert m.water is not None and 0.0 < m.water < 100.0
    assert m.muscle is not None
    assert m.bone_kg is not None and 0.0 < m.bone_kg < 20.0
    assert m.lean_body_mass_kg is not None
    assert m.visceral_fat is not None


def test_parse_rejects_out_of_range_weight() -> None:
    user = ScaleUser(sex="male", age=35, height_cm=175.0)
    # 10 kg / 100 = 0.1 kg -> below openScale's 0.5 kg sanity floor.
    assert parse_measurement(_make_wire_packet(10, 0, 0), MAC_BYTES, user) is None
    # 50000 / 100 = 500 kg -> above the 300 kg ceiling.
    assert parse_measurement(_make_wire_packet(50000, 0, 0), MAC_BYTES, user) is None


def test_xor_cipher_uses_mac_as_key() -> None:
    """The obfuscation must be keyed on the scale's MAC address."""
    packet = _make_wire_packet(7500, 42, 123, MAC_BYTES)
    # Decrypting with a *different* MAC must not yield the same weight.
    other_mac = bytes((0x01, 0x02, 0x03, 0x04, 0x05, 0x06))
    wrong = parse_measurement(packet, other_mac, ScaleUser())
    right = parse_measurement(packet, MAC_BYTES, ScaleUser())
    assert wrong is None or wrong.weight_kg != right.weight_kg
