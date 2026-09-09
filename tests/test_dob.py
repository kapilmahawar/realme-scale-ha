"""Unit tests for date-of-birth based user profiles (v0.8.3).

Covers the pure age math, input validation, legacy-age migration, profile
serialization and the handshake age byte - everything testable without a
Home Assistant runtime.
"""

from __future__ import annotations

from datetime import date

from custom_components.realme_scale.scale_controller import (
    ScaleUser,
    age_on_date,
    build_handshake,
    build_user_options,
    dob_error,
    obfuscate,
    parse_dob,
    parse_user_options,
    scale_user_from_profile,
    scale_user_to_profile,
)

MAC = "AA:BB:CC:DD:EE:FF"
MAC_BYTES = bytes((0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF))


# ---------------------------------------------------------------------------
# Birthday-aware age calculation (spec cases 1-5)
# ---------------------------------------------------------------------------


def test_age_birthday_today() -> None:
    assert age_on_date(date(1990, 5, 14), date(2026, 5, 14)) == 36


def test_age_day_before_birthday() -> None:
    assert age_on_date(date(1990, 5, 14), date(2026, 5, 13)) == 35


def test_age_day_after_birthday() -> None:
    assert age_on_date(date(1990, 5, 14), date(2026, 5, 15)) == 36


def test_age_year_boundary_december() -> None:
    dob = date(2000, 12, 31)
    assert age_on_date(dob, date(2026, 12, 30)) == 25
    assert age_on_date(dob, date(2026, 12, 31)) == 26


def test_age_january_birthday() -> None:
    assert age_on_date(date(2000, 1, 1), date(2026, 1, 1)) == 26
    assert age_on_date(date(2000, 1, 1), date(2025, 12, 31)) == 25


def test_age_leap_day() -> None:
    dob = date(2000, 2, 29)
    assert age_on_date(dob, date(2026, 2, 28)) == 25  # birthday not reached
    assert age_on_date(dob, date(2026, 3, 1)) == 26    # birthday passed


def test_scale_user_current_age_derives_from_dob() -> None:
    user = ScaleUser(name="Alice", date_of_birth="1990-05-14")
    assert user.current_age(date(2026, 5, 13)) == 35
    assert user.current_age(date(2026, 5, 14)) == 36
    assert user.current_age(date(2027, 5, 14)) == 37


def test_scale_user_legacy_age_when_no_dob() -> None:
    """Pre-DOB profiles keep working off the stored age (no migration)."""
    user = ScaleUser(name="Alice", age=35)
    assert user.date_of_birth == ""
    assert user.current_age(date(2026, 5, 14)) == 35


def test_scale_user_future_dob_falls_back_to_legacy() -> None:
    """Corrupt / future stored DOB must never crash or go negative."""
    user = ScaleUser(name="Alice", age=40, date_of_birth="2030-01-01")
    assert user.current_age(date(2026, 1, 1)) == 40


def test_scale_user_invalid_dob_falls_back_to_legacy() -> None:
    user = ScaleUser(name="Alice", age=33, date_of_birth="not-a-date")
    assert user.current_age(date(2026, 1, 1)) == 33


# ---------------------------------------------------------------------------
# DOB parsing + validation (spec cases 6-7)
# ---------------------------------------------------------------------------


def test_parse_dob() -> None:
    assert parse_dob("1990-05-14") == date(1990, 5, 14)
    assert parse_dob("") is None
    assert parse_dob("2026-13-45") is None
    assert parse_dob("14/05/1990") is None
    assert parse_dob(None) is None


def test_dob_error_empty_is_required() -> None:
    assert dob_error("", on=date(2026, 5, 14)) == "dob_required"
    assert dob_error(None, on=date(2026, 5, 14)) == "dob_required"


def test_dob_error_future_rejected() -> None:
    assert dob_error("2026-05-15", on=date(2026, 5, 14)) == "dob_future"
    assert dob_error("2030-01-01", on=date(2026, 5, 14)) == "dob_future"


def test_dob_error_invalid_rejected() -> None:
    assert dob_error("not-a-date", on=date(2026, 5, 14)) == "dob_invalid"
    assert dob_error("1990-13-45", on=date(2026, 5, 14)) == "dob_invalid"
    assert dob_error("0090-05-14", on=date(2026, 5, 14)) == "dob_invalid"


def test_dob_error_valid_returns_none() -> None:
    assert dob_error("1990-05-14", on=date(2026, 5, 14)) is None
    assert dob_error("2000-02-29", on=date(2026, 5, 14)) is None


# ---------------------------------------------------------------------------
# Serialization: DOB profiles store the DOB, not a derived age (spec 1)
# ---------------------------------------------------------------------------


def test_dob_profile_serialization_drops_derived_age() -> None:
    """date_of_birth is the stored source of truth; age is never stored."""
    user = ScaleUser(
        user_id="u-alice", name="Alice", sex="female", date_of_birth="1990-05-14",
        height_cm=168.0, activity_level="moderate",
    )
    profile = scale_user_to_profile(user)
    assert profile["date_of_birth"] == "1990-05-14"
    assert "age" not in profile
    restored = scale_user_from_profile(profile)
    assert restored.user_id == "u-alice"
    assert restored.name == "Alice"
    assert restored.date_of_birth == "1990-05-14"
    assert restored.current_age(date(2026, 5, 14)) == 36


def test_legacy_profile_serialization_keeps_age_only() -> None:
    """An age-only (pre-DOB) profile round-trips unchanged; no DOB invented."""
    user = ScaleUser(
        user_id="u-bob", name="Bob", sex="male", age=35, height_cm=182.0,
    )
    profile = scale_user_to_profile(user)
    assert profile["age"] == 35
    assert "date_of_birth" not in profile
    restored = scale_user_from_profile(profile)
    assert restored.user_id == "u-bob"
    assert restored.date_of_birth == ""
    assert restored.age == 35
    assert restored.current_age(date(2026, 5, 14)) == 35


def test_invalid_stored_dob_normalized_to_empty() -> None:
    profile = {
        "user_id": "u1", "user_name": "X", "sex": "male",
        "date_of_birth": "garbage", "age": 42,
    }
    restored = scale_user_from_profile(profile)
    assert restored.date_of_birth == ""
    assert restored.age == 42


# ---------------------------------------------------------------------------
# Migration: legacy v0.8.2 profiles keep loading (spec 8)
# ---------------------------------------------------------------------------


def test_legacy_options_profile_loads_without_fabricated_dob() -> None:
    options = {
        "users": [
            {
                "user_id": "u-alice", "user_name": "Alice", "sex": "female",
                "age": 35, "height": 168.0, "activity_level": "moderate",
            }
        ],
        "active_user_id": "u-alice",
        "auto_assign_kg": 3.0,
    }
    users, active, _, _ = parse_user_options(options)
    assert len(users) == 1
    alice = users[0]
    assert active == "u-alice"
    assert alice.user_id == "u-alice"
    assert alice.name == "Alice"
    assert alice.date_of_birth == ""          # nothing fabricated
    assert alice.current_age(date(2026, 5, 14)) == 35  # legacy age intact


def test_legacy_flat_options_with_age_still_migrate() -> None:
    options = {"user_name": "Alice", "sex": "male", "age": 44, "height": 180.0}
    users, _, _, _ = parse_user_options(options)
    assert len(users) == 1
    assert users[0].date_of_birth == ""
    assert users[0].current_age(date(2026, 5, 14)) == 44


# ---------------------------------------------------------------------------
# After DOB entered: handshake receives derived age, ids stable (spec 9)
# ---------------------------------------------------------------------------


def _handshake_age_byte(user: ScaleUser, on: date) -> int:
    cmds = build_handshake(user, MAC_BYTES, now=1_800_000_000,
                           tz_offset_min=330, today=on)
    # cmd[3] is p4 (user info); strip the 2-byte header and deobfuscate.
    p4 = obfuscate(cmds[3][2:], MAC_BYTES)
    return p4[4]  # sex_byte, then the age byte


def test_handshake_uses_dob_derived_age() -> None:
    user = ScaleUser(name="Alice", sex="male", date_of_birth="1990-05-14",
                     height_cm=175.0, initial_weight=75.0)
    assert _handshake_age_byte(user, date(2026, 5, 13)) == 35
    assert _handshake_age_byte(user, date(2026, 5, 14)) == 36


def test_handshake_legacy_age_unchanged() -> None:
    user = ScaleUser(name="Alice", sex="male", age=35, height_cm=175.0)
    assert _handshake_age_byte(user, date(2026, 5, 14)) == 35


def test_stable_user_id_unchanged_after_dob_entered() -> None:
    """Adding a DOB must not create/delete users or change user ids."""
    legacy = ScaleUser(user_id="u-alice", name="Alice", sex="female", age=35)

    # The user later edits the profile and enters a DOB (same user_id).
    upgraded = ScaleUser(
        user_id=legacy.user_id, name=legacy.name, sex=legacy.sex,
        date_of_birth="1990-05-14", height_cm=legacy.height_cm,
        activity_level=legacy.activity_level,
    )
    options = build_user_options([upgraded], "u-alice", 3.0)
    users, active, _, _ = parse_user_options(options)
    assert [u.user_id for u in users] == ["u-alice"]
    assert active == "u-alice"
    assert users[0].current_age(date(2026, 5, 14)) == 36


def test_dob_survives_options_roundtrip_restart() -> None:
    """Persist / reload cycles (HA restart) keep the DOB (spec 12)."""
    users = [
        ScaleUser(user_id="u-a", name="Alice", date_of_birth="1990-05-14"),
        ScaleUser(user_id="u-b", name="Bob", age=40),  # legacy stays legacy
    ]
    options = build_user_options(users, "u-b", 2.0)
    for _ in range(3):  # restart / reload multiple times
        parsed, active, _, _ = parse_user_options(dict(options))
        options = build_user_options(parsed, active, 2.0)
    final, _, _, _ = parse_user_options(options)
    alice = next(u for u in final if u.user_id == "u-a")
    bob = next(u for u in final if u.user_id == "u-b")
    assert alice.date_of_birth == "1990-05-14"
    assert bob.date_of_birth == ""
    assert bob.age == 40


# ---------------------------------------------------------------------------
# Multi-user independence (spec 11) + deletion unchanged (spec 10)
# ---------------------------------------------------------------------------


def test_each_user_dob_is_independent() -> None:
    users = [
        ScaleUser(user_id="u-a", name="Alice", date_of_birth="1980-06-01"),
        ScaleUser(user_id="u-b", name="Bob", date_of_birth="2010-06-01"),
    ]
    on = date(2026, 6, 1)
    assert users[0].current_age(on) == 46
    assert users[1].current_age(on) == 16
    profile_b = scale_user_to_profile(users[1])
    assert profile_b["date_of_birth"] == "2010-06-01"


def test_removing_a_dob_user_leaves_others() -> None:
    """User deletion operates on user_id and is DOB-agnostic."""
    users = [
        ScaleUser(user_id="u-a", name="Alice", date_of_birth="1980-06-01"),
        ScaleUser(user_id="u-b", name="Bob", date_of_birth="2010-06-01"),
    ]
    remaining = [u for u in users if u.user_id != "u-a"]
    options = build_user_options(remaining, "u-b", 3.0)
    parsed, active, _, _ = parse_user_options(options)
    assert [u.user_id for u in parsed] == ["u-b"]
    assert active == "u-b"
    assert parsed[0].date_of_birth == "2010-06-01"


def test_measurement_records_never_store_dob() -> None:
    """The record schema is unchanged; DOB lives only in the profile."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    records_src = (root / "custom_components" / "realme_scale" / "records.py").read_text(
        encoding="utf-8"
    )
    assert "date_of_birth" not in records_src
    # The record column maps never gain an age/DOB column key.
    assert '"age"' not in records_src
    assert '"date_of_birth"' not in records_src
