"""RMH2011 (Realme Smart Scale) protocol core.

Faithful Python port of the byte-level logic in openScale
``RealmeSmartScaleHandler.kt`` (GPL-3.0):

- 6-step dynamic handshake written to 0xA624 right after connection.
- Commands are XOR-obfuscated with the scale MAC (``mac[i % 6]`` cipher).
- Continuous ``0x00 0x01 0xD9`` keep-alive on 0xA622 every 1 second.
- Live measurements stream to 0xA621; each packet is fully XOR encrypted
  using the same repeating MAC cipher.

This module is pure Python (no Home Assistant imports) so the protocol can
be exercised in unit tests.  BLE I/O lives in ``coordinator.py``.

Note on local body composition: the scale transmits weight + impedance only.
Body fat / muscle / water / bone / LBM / visceral fat are computed locally by
porting openScale's ``YunmaiLib`` (see ``bia.py``).  Metric assembly mirrors
the RealmeSmartScaleHandler exactly, including the way it feeds the stored
MUSCLE value back into the bone-mass formula.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .bia import YunmaiBia
from .const import (
    MAX_WEIGHT_KG,
    MEASUREMENT_HEADER,
    MEASUREMENT_LENGTH,
    MEASUREMENT_MIN_SIZE,
    MIN_WEIGHT_KG,
    SEX_FEMALE,
    SEX_MALE,
)

# --------------------------------------------------------------------------
# Wire helpers
# --------------------------------------------------------------------------


def mac_string_to_bytes(mac: str) -> bytes:
    """Convert a MAC like ``AA:BB:CC:DD:EE:FF`` to its 6 raw bytes."""
    clean = mac.replace(":", "").replace("-", "")
    if len(clean) != 12:
        raise ValueError(f"Invalid MAC address: {mac!r}")
    return bytes.fromhex(clean)


def obfuscate(payload: bytes, mac: bytes) -> bytes:
    """XOR payload against the repeating 6-byte MAC cipher."""
    return bytes(b ^ mac[i % 6] for i, b in enumerate(payload))


def _wrap_and_obfuscate(payload: bytes, mac: bytes) -> bytes:
    """Prepend ``0x10 <length>`` header then XOR the payload (TX format)."""
    out = bytearray(payload)
    return bytes((MEASUREMENT_HEADER, len(payload))) + obfuscate(bytes(out), mac)


def _deobfuscate(data: bytes, mac: bytes) -> bytes:
    """Strip the 2-byte header and XOR-decrypt the rest (RX format)."""
    return obfuscate(data[2:], mac)


# --------------------------------------------------------------------------
# User profile
# --------------------------------------------------------------------------


@dataclass
class ScaleUser:
    """A user profile, mirroring openScale's ScaleUser fields."""

    name: str = ""
    sex: str = SEX_MALE            # "male" | "female"
    age: int = 30
    height_cm: float = 175.0
    activity_level: str = "moderate"
    initial_weight: float = 0.0    # kg; <= 0 means "new user" (0xFFFF sentinel)

    def is_male(self) -> bool:
        return self.sex != SEX_FEMALE

    def sex_int(self) -> int:
        """1 = male, 0 = female (YunmaiLib convention)."""
        return 1 if self.is_male() else 0


def _kotlin_round(value: float) -> int:
    """Kotlin ``roundToInt()``: Math.round -> floor(x + 0.5) for x >= 0."""
    return int(math.floor(value + 0.5))


# --------------------------------------------------------------------------
# Handshake
# --------------------------------------------------------------------------


def build_handshake(user: ScaleUser, mac: bytes, now: int | None = None,
                    tz_offset_min: int | None = None) -> list[bytes]:
    """Generate the 6 handshake commands for characteristic 0xA624.

    Mirrors ``RealmeSmartScaleHandler.buildHandshake`` / ``wrapAndObfuscate``.
    ``now`` is a unix epoch timestamp and ``tz_offset_min`` the local UTC
    offset in minutes, both only used for the scale time-set command.
    """
    if now is None:
        now = int(time.time())

    # Kotlin: TimeZone.getDefault().getOffset(ms) / 60000, then .toByte().
    # The byte pattern of (minutes & 0xFF) equals Kotlin's signed wrap.
    if tz_offset_min is None:
        tz_offset_min = int(datetime.now().astimezone().utcoffset().total_seconds() // 60)
    tz_byte = tz_offset_min & 0xFF

    ts = now
    sex_byte = 0x00 if user.is_male() else 0x80
    h_cm = _kotlin_round(user.height_cm)

    # openScale: new users (initialWeight <= 0) get the 0xFFFF sentinel.
    if user.initial_weight <= 0.0:
        weight_to_send = 0xFFFF
    else:
        weight_to_send = min(max(_kotlin_round(user.initial_weight * 100.0), 0), 0xFFFF)

    def u16be(v: int) -> bytes:
        return bytes(((v >> 8) & 0xFF, v & 0xFF))

    # Raw, un-obfuscated payloads (from the Kotlin handler).
    p1 = bytes((0x00, 0x08, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02))  # Register
    p2 = bytes((0x00, 0x0A, 0x18)) + bytes((ts >> 24, (ts >> 16) & 0xFF,
                                            (ts >> 8) & 0xFF, ts & 0xFF)) + bytes((tz_byte,))  # Set time
    p3 = bytes((0x48, 0x01, 0x00, 0x01))  # Start measure
    p4 = (bytes((0x10, 0x01, 0x01, sex_byte, user.age & 0xFF))
          + u16be(h_cm)
          + bytes((0x00, 0x00))
          + u16be(weight_to_send))  # User info
    p5 = bytes((0x10, 0x04, 0x00))  # Formula
    p6 = bytes((0x10, 0x07, 0x01))  # Unit: KG

    return [_wrap_and_obfuscate(p, mac) for p in (p1, p2, p3, p4, p5, p6)]


# --------------------------------------------------------------------------
# Measurement parsing
# --------------------------------------------------------------------------


@dataclass
class ScaleMeasurement:
    """A parsed live measurement (values as reported / locally derived)."""

    weight_kg: float
    measured_at: datetime            # timestamp from the scale when present
    impedance: int | None = None     # Ohms; None when the scale sent none

    # Locally derived body-composition metrics (percent / kg / index).
    # Only filled in when impedance > 0 produced a plausible fat estimate.
    body_fat: float | None = None        # %
    muscle: float | None = None          # % (see note in module docstring)
    water: float | None = None           # %
    bone_kg: float | None = None
    lean_body_mass_kg: float | None = None
    visceral_fat: float | None = None    # unitless index
    raw: bytes = field(default=b"", repr=False)


def is_measurement_packet(data: bytes) -> bool:
    """True when a 0xA621 notification looks like a live measurement."""
    return (
        len(data) >= MEASUREMENT_MIN_SIZE
        and data[0] == MEASUREMENT_HEADER
        and data[1] == MEASUREMENT_LENGTH
    )


def parse_measurement(data: bytes, mac: bytes, user: ScaleUser) -> ScaleMeasurement | None:
    """Parse and decrypt one 0xA621 measurement notification.

    Returns ``None`` for an out-of-range weight (same sanity gate as
    openScale: 0.5 < weight <= 300 kg).
    """
    if not is_measurement_packet(data):
        return None

    payload = _deobfuscate(data, mac)

    # Offsets are relative to the decrypted payload (Kotlin parseMeasurement).
    if len(payload) < 16:
        return None

    weight_raw = (payload[8] << 8) | payload[9]
    weight_kg = weight_raw / 100.0
    scale_time = (
        (payload[10] << 24) | (payload[11] << 16) | (payload[12] << 8) | payload[13]
    )
    impedance = (payload[14] << 8) | payload[15]

    if not (MIN_WEIGHT_KG < weight_kg <= MAX_WEIGHT_KG):
        return None

    measured_at = (
        datetime.fromtimestamp(scale_time, tz=UTC)
        if scale_time > 0
        else datetime.now(tz=UTC)
    )

    measurement = ScaleMeasurement(
        weight_kg=weight_kg,
        measured_at=measured_at,
        impedance=impedance if impedance > 0 else None,
        raw=data,
    )

    if impedance > 0:
        calc = YunmaiBia(user.sex_int(), user.height_cm, user.activity_level)
        fat_pct = calc.get_fat(user.age, weight_kg, impedance)
        if fat_pct > 0.0:
            muscle_pct = calc.get_muscle(fat_pct) / weight_kg * 100.0
            measurement.body_fat = fat_pct
            measurement.muscle = muscle_pct
            measurement.water = calc.get_water(fat_pct)
            # Kotlin feeds the *stored* MUSCLE value into the bone formula.
            measurement.bone_kg = calc.get_bone_mass(muscle_pct, weight_kg)
            measurement.lean_body_mass_kg = calc.get_lean_body_mass(weight_kg, fat_pct)
            measurement.visceral_fat = calc.get_visceral_fat(fat_pct, user.age)

    return measurement
