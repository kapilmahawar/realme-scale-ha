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
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timezone
from typing import Any, Final

from .bia import YunmaiBia
from .const import (
    ACTIVITY_LEVELS,
    CONF_ACTIVE_USER_ID,
    CONF_ACTIVITY_LEVEL,
    CONF_AGE,
    CONF_AUTO_ASSIGN_KG,
    CONF_DATE_OF_BIRTH,
    CONF_EXPECTED_WEIGHT,
    CONF_HEIGHT,
    CONF_IMPEDANCE_TOL_OHM,
    CONF_INITIAL_WEIGHT,
    CONF_PERSON_ENTITY,
    CONF_SEX,
    CONF_USER_ID,
    CONF_USER_NAME,
    CONF_USERS,
    CONF_WEIGHT_TOLERANCE,
    DEFAULT_AUTO_ASSIGN_KG,
    DEFAULT_IMPEDANCE_TOL_OHM,
    LEGACY_USER_ID,
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
    """A user profile, mirroring openScale's ScaleUser fields.

    ``user_id`` is a stable string that identifies the user within one
    config entry (used for attribution, device ids and storage keys).
    """

    user_id: str = ""
    name: str = ""
    # Optional link to a Home Assistant person entity (person.*), so
    # dashboards can be built around the people HA already knows.
    person_entity_id: str = ""
    sex: str = SEX_MALE            # "male" | "female"
    # Legacy age in years. Kept for profiles created before date-of-birth
    # support; when ``date_of_birth`` is set the current age is derived from
    # it instead and this field is only a migration fallback.
    age: int = 30
    # ISO date "YYYY-MM-DD". Empty string means "not configured yet" (the
    # profile keeps using the legacy age until the user provides a DOB).
    date_of_birth: str = ""
    height_cm: float = 175.0
    activity_level: str = "moderate"
    initial_weight: float = 0.0    # kg; <= 0 means "new user" (0xFFFF sentinel)
    # Identity fields for the automatic identification engine.
    # 0 = not configured (falls back to initial weight / global defaults).
    expected_weight_kg: float = 0.0
    weight_tolerance_kg: float = 0.0
    impedance_tolerance_ohm: float = 0.0

    def is_male(self) -> bool:
        return self.sex != SEX_FEMALE

    def sex_int(self) -> int:
        """1 = male, 0 = female (YunmaiLib convention)."""
        return 1 if self.is_male() else 0

    def current_age(self, on: date | None = None) -> int:
        """The user's age in whole years on ``on`` (default: today).

        Derived from ``date_of_birth`` when it is configured; otherwise the
        legacy stored ``age`` is used (pre-DOB profiles keep working without
        any migration step). An unparseable / future DOB falls back to the
        legacy age so corrupt data can never crash profile use.
        """
        dob = parse_dob(self.date_of_birth)
        if dob is not None:
            today = on or _local_today()
            if dob <= today:
                return age_on_date(dob, today)
            return self.age  # future DOB (invalid input) -> legacy fallback
        return self.age


def _local_today() -> date:
    """Today in the host's local time zone (pure default; HA callers pass
    their own time-zone-aware date explicitly)."""
    return datetime.now(timezone.utc).astimezone().date()


def parse_dob(value: Any) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` DOB string; ``None`` when invalid."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def age_on_date(dob: date, on: date) -> int:
    """Whole years completed by ``on`` (birthday-aware).

    Never ``on.year - dob.year`` alone: that over-counts before the birthday
    in the current year.
    """
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


# Minimum plausible birth year: rejects obvious typos (e.g. "0090") without
# being restrictive for real users (covers anyone born after 1900).
MIN_DOB_YEAR: Final = 1900


def dob_error(value: Any, on: date | None = None) -> str | None:
    """Validate a raw DOB input; return an error key or ``None``.

    Error keys (translated by the flows):
    - ``dob_required``  -> empty input
    - ``dob_invalid``   -> not a real YYYY-MM-DD date / implausibly old
    - ``dob_future``    -> in the future
    """
    if value is None or str(value).strip() == "":
        return "dob_required"
    dob = parse_dob(value)
    if dob is None or dob.year < MIN_DOB_YEAR:
        return "dob_invalid"
    if dob > (on or _local_today()):
        return "dob_future"
    return None


# --------------------------------------------------------------------------
# User profile <-> stored options helpers
# --------------------------------------------------------------------------


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any, default: str) -> str:
    try:
        return str(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _valid_activity(value: str) -> str:
    return value if value in ACTIVITY_LEVELS else "moderate"


def scale_user_from_profile(values: Mapping[str, Any]) -> ScaleUser:
    """Build a ScaleUser from a stored profile dict (any key subset ok)."""
    dob_raw = _safe_str(values.get(CONF_DATE_OF_BIRTH), "")
    dob = parse_dob(dob_raw)
    return ScaleUser(
        user_id=_safe_str(values.get(CONF_USER_ID), ""),
        name=_safe_str(values.get(CONF_USER_NAME), ""),
        person_entity_id=_safe_str(values.get(CONF_PERSON_ENTITY), ""),
        sex=_safe_str(values.get(CONF_SEX), SEX_MALE),
        age=_safe_int(values.get(CONF_AGE), 30),
        date_of_birth=dob.isoformat() if dob is not None else "",
        height_cm=_safe_float(values.get(CONF_HEIGHT), 175.0),
        activity_level=_valid_activity(
            _safe_str(values.get(CONF_ACTIVITY_LEVEL), "moderate")
        ),
        initial_weight=_safe_float(values.get(CONF_INITIAL_WEIGHT), 0.0),
        expected_weight_kg=_safe_float(values.get(CONF_EXPECTED_WEIGHT), 0.0),
        weight_tolerance_kg=_safe_float(values.get(CONF_WEIGHT_TOLERANCE), 0.0),
        impedance_tolerance_ohm=_safe_float(
            values.get(CONF_IMPEDANCE_TOL_OHM), 0.0
        ),
    )


def scale_user_to_profile(user: ScaleUser) -> dict[str, Any]:
    """Serialize a ScaleUser into the canonical stored profile dict.

    ``date_of_birth`` is the source of truth when configured (age is derived
    from it and therefore not stored).  Profiles without a DOB keep the
    legacy ``age`` key so pre-DOB installs load unchanged.
    """
    profile = {
        CONF_USER_ID: user.user_id,
        CONF_USER_NAME: user.name,
        CONF_PERSON_ENTITY: user.person_entity_id,
        CONF_SEX: user.sex,
        CONF_HEIGHT: user.height_cm,
        CONF_ACTIVITY_LEVEL: user.activity_level,
        CONF_INITIAL_WEIGHT: user.initial_weight,
        CONF_EXPECTED_WEIGHT: user.expected_weight_kg,
        CONF_WEIGHT_TOLERANCE: user.weight_tolerance_kg,
        CONF_IMPEDANCE_TOL_OHM: user.impedance_tolerance_ohm,
    }
    if user.date_of_birth:
        profile[CONF_DATE_OF_BIRTH] = user.date_of_birth
    else:
        profile[CONF_AGE] = user.age
    return profile


def parse_user_options(
    options: Mapping[str, Any],
) -> tuple[list[ScaleUser], str | None, float, float]:
    """Decode entry.options into (users, active id, weight tol, impedance tol).

    Migrates a legacy single-profile entry (flat CONF_* keys, as shipped in
    v0.1.x) into a one-user list so old installs keep working unchanged.

    An explicitly empty ``users`` list is kept empty: the scale may exist
    with zero configured users (measurements then stay unassigned).
    """
    users_raw = options.get(CONF_USERS)
    if isinstance(users_raw, list):
        # Canonical storage.  Invalid dict entries are skipped; an empty /
        # all-invalid list legitimately means "no users configured".
        users: list[ScaleUser] = []
        for raw in users_raw:
            if not isinstance(raw, dict):
                continue
            user = scale_user_from_profile(raw)
            if not user.user_id:
                continue
            users.append(user)
    else:
        # No "users" key at all -> legacy flat profile; migrate it into a
        # single user with the stable legacy id.
        profile = {key: options.get(key) for key in (
            CONF_USER_NAME, CONF_SEX, CONF_AGE, CONF_HEIGHT,
            CONF_ACTIVITY_LEVEL, CONF_INITIAL_WEIGHT,
        )}
        profile[CONF_USER_ID] = LEGACY_USER_ID
        users = [scale_user_from_profile(profile)]

    active_user_id = _safe_str(options.get(CONF_ACTIVE_USER_ID), "")
    known_ids = {user.user_id for user in users}
    if active_user_id not in known_ids:
        active_user_id = users[0].user_id if users else None
    tolerance = _safe_float(options.get(CONF_AUTO_ASSIGN_KG), DEFAULT_AUTO_ASSIGN_KG)
    impedance_tol = _safe_float(
        options.get(CONF_IMPEDANCE_TOL_OHM), DEFAULT_IMPEDANCE_TOL_OHM
    )
    return users, active_user_id, tolerance, impedance_tol


def build_user_options(
    users: list[ScaleUser],
    active_user_id: str | None,
    tolerance_kg: float,
    impedance_tol_ohm: float | None = None,
) -> dict[str, Any]:
    """Serialize (users, active user, tolerances) into entry.options."""
    return {
        CONF_USERS: [scale_user_to_profile(user) for user in users],
        CONF_ACTIVE_USER_ID: active_user_id or (users[0].user_id if users else ""),
        CONF_AUTO_ASSIGN_KG: float(tolerance_kg),
        CONF_IMPEDANCE_TOL_OHM: float(
            impedance_tol_ohm
            if impedance_tol_ohm is not None
            else DEFAULT_IMPEDANCE_TOL_OHM
        ),
    }


def _kotlin_round(value: float) -> int:
    """Kotlin ``roundToInt()``: Math.round -> floor(x + 0.5) for x >= 0."""
    return math.floor(value + 0.5)


# --------------------------------------------------------------------------
# Handshake
# --------------------------------------------------------------------------


def build_handshake(user: ScaleUser, mac: bytes, now: int | None = None,
                    tz_offset_min: int | None = None,
                    today: date | None = None) -> list[bytes]:
    """Generate the 6 handshake commands for characteristic 0xA624.

    Mirrors ``RealmeSmartScaleHandler.buildHandshake`` / ``wrapAndObfuscate``.
    ``now`` is a unix epoch timestamp and ``tz_offset_min`` the local UTC
    offset in minutes, both only used for the scale time-set command.
    ``today`` selects the date used to derive the age from ``date_of_birth``
    (defaults to the current local date).
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
    age = user.current_age(today)

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
    p4 = (bytes((0x10, 0x01, 0x01, sex_byte, age & 0xFF))
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
class DecodedMeasurement:
    """Raw, user-agnostic values decoded from a scale packet.

    Attribution to a user happens *after* decoding (the scale never tells
    us who is standing on it), so the coordinator can pick the right
    profile before any body-composition math runs.
    """

    weight_kg: float
    scale_time_epoch: int          # 0 when the scale sent no timestamp
    measured_at: datetime
    impedance: int | None = None   # Ohms; None when the scale sent none
    raw: bytes = field(default=b"", repr=False)


@dataclass
class ScaleMeasurement:
    """A parsed live measurement (values as reported / locally derived)."""

    weight_kg: float
    measured_at: datetime            # timestamp from the scale when present
    impedance: int | None = None     # Ohms; None when the scale sent none

    # Attribution (set by the coordinator, not by packet parsing).
    user_id: str | None = None
    user_name: str | None = None
    # Plausible owners when the reading was ambiguous (nearest first).
    # Only meaningful while the measurement is unassigned.
    candidate_user_ids: tuple[str, ...] | None = None
    # How ownership was decided (assignment module constants, or "manual").
    assignment_method: str | None = None
    confidence: float | None = None

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


def decode_measurement(data: bytes, mac: bytes) -> DecodedMeasurement | None:
    """Decrypt and decode one 0xA621 notification (no user involved).

    Returns ``None`` for a non-measurement packet or an out-of-range weight
    (same sanity gate as openScale: 0.5 < weight <= 300 kg).
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

    return DecodedMeasurement(
        weight_kg=weight_kg,
        scale_time_epoch=scale_time,
        measured_at=measured_at,
        impedance=impedance if impedance > 0 else None,
        raw=data,
    )


def compute_body_composition(
    user: ScaleUser, weight_kg: float, impedance: int | None,
    today: date | None = None,
) -> tuple[float, float, float, float, float, float] | None:
    """Derive body-composition figures from weight + impedance + profile.

    Returns ``(body_fat, muscle, water, bone_kg, lean_body_mass_kg,
    visceral_fat)`` or ``None`` when impedance is missing / implausible.
    Mirrors the Kotlin Realme handler, including feeding the stored MUSCLE
    value back into the bone-mass formula.
    """
    if not impedance or impedance <= 0:
        return None

    age = user.current_age(today)
    calc = YunmaiBia(user.sex_int(), user.height_cm, user.activity_level)
    fat_pct = calc.get_fat(age, weight_kg, impedance)
    if fat_pct <= 0.0:
        return None

    muscle_pct = calc.get_muscle(fat_pct) / weight_kg * 100.0
    return (
        fat_pct,
        muscle_pct,
        calc.get_water(fat_pct),
        calc.get_bone_mass(muscle_pct, weight_kg),
        calc.get_lean_body_mass(weight_kg, fat_pct),
        calc.get_visceral_fat(fat_pct, age),
    )


def parse_measurement(data: bytes, mac: bytes, user: ScaleUser) -> ScaleMeasurement | None:
    """Parse and decrypt one 0xA621 notification, attributed to ``user``.

    Kept for API compatibility with earlier versions / tests: decode the
    packet, then immediately compute body composition under ``user``.
    Returns ``None`` for a packet the sanity gates reject.
    """
    decoded = decode_measurement(data, mac)
    if decoded is None:
        return None

    measurement = ScaleMeasurement(
        weight_kg=decoded.weight_kg,
        measured_at=decoded.measured_at,
        impedance=decoded.impedance,
        raw=decoded.raw,
    )
    metrics = compute_body_composition(user, decoded.weight_kg, decoded.impedance)
    if metrics is not None:
        (
            measurement.body_fat,
            measurement.muscle,
            measurement.water,
            measurement.bone_kg,
            measurement.lean_body_mass_kg,
            measurement.visceral_fat,
        ) = metrics
    return measurement
