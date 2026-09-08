"""Unit tests for stored user-options parsing / serialization."""

from __future__ import annotations

import pytest

from custom_components.realme_scale.const import (
    DEFAULT_AUTO_ASSIGN_KG,
    DEFAULT_IMPEDANCE_TOL_OHM,
    LEGACY_USER_ID,
)
from custom_components.realme_scale.scale_controller import (
    ScaleUser,
    build_user_options,
    parse_user_options,
    scale_user_from_profile,
    scale_user_to_profile,
)


def test_legacy_flat_options_migrate_to_single_user() -> None:
    """v0.1 entries stored the profile flat in options."""
    options = {
        "user_name": "Alice",
        "sex": "male",
        "age": 35,
        "height": 175.0,
        "activity_level": "moderate",
        "initial_weight": 75.0,
    }
    users, active_user_id, tolerance, impedance_tol = parse_user_options(options)
    assert len(users) == 1
    assert users[0].user_id == LEGACY_USER_ID
    assert users[0].name == "Alice"
    assert active_user_id == LEGACY_USER_ID
    assert tolerance == DEFAULT_AUTO_ASSIGN_KG
    assert impedance_tol == DEFAULT_IMPEDANCE_TOL_OHM


def test_legacy_missing_keys_get_defaults() -> None:
    users, active_user_id, _, _ = parse_user_options({})
    assert len(users) == 1
    assert users[0].name == ""
    assert users[0].age == 30
    assert active_user_id == users[0].user_id


def test_canonical_roundtrip() -> None:
    users = [
        ScaleUser(
            user_id="ualice123", name="Alice", sex="female", age=32,
            height_cm=168.0, activity_level="heavy", initial_weight=62.0,
        ),
        ScaleUser(
            user_id="ubob4567", name="Bob", sex="male", age=40,
            height_cm=182.0, activity_level="moderate", initial_weight=0.0,
        ),
    ]
    options = build_user_options(users, "ubob4567", 2.5, 45.0)
    parsed_users, active, tolerance, impedance_tol = parse_user_options(options)

    assert [u.user_id for u in parsed_users] == ["ualice123", "ubob4567"]
    assert parsed_users[0].sex == "female"
    assert parsed_users[0].activity_level == "heavy"
    assert parsed_users[1].initial_weight == 0.0
    assert active == "ubob4567"
    assert tolerance == 2.5
    assert impedance_tol == 45.0


def test_active_user_falls_back_to_first() -> None:
    users = [ScaleUser(user_id="u1", name="One")]
    options = build_user_options(users, "ghost", 1.0)
    _, active, _, _ = parse_user_options(options)
    assert active == "u1"


def test_bad_activity_level_sanitized() -> None:
    raw = scale_user_to_profile(ScaleUser(user_id="u1", name="X"))
    raw["activity_level"] = "sprinting"  # not an openScale level
    parsed = scale_user_from_profile(raw)
    # parse_user_options applies the sanitizer too.
    _, _, _, _ = parse_user_options({"users": [raw], "active_user_id": "u1",
                                     "auto_assign_kg": 1.0})
    assert parsed.activity_level == "moderate"


def test_garbage_user_entries_skipped_leaves_zero_users() -> None:
    """An explicit (even if all-invalid) users list means zero users."""
    options = {
        "users": [42, None, {}],
        "active_user_id": "",
        "auto_assign_kg": 3.0,
    }
    users, active, tolerance, impedance_tol = parse_user_options(options)
    assert users == []
    assert active is None
    assert tolerance == 3.0
    assert impedance_tol == DEFAULT_IMPEDANCE_TOL_OHM


def test_zero_users_roundtrip() -> None:
    """Deleting the last user keeps the scale configured with 0 users."""
    options = build_user_options([], None, 3.0)
    users, active, tolerance, _ = parse_user_options(options)
    assert users == []
    assert active is None
    assert tolerance == 3.0


def test_legacy_flat_profile_still_migrates() -> None:
    """Absence of a 'users' key (v0.1 entries) still yields one user."""
    users, active, _, _ = parse_user_options({"user_name": "Alice"})
    assert len(users) == 1
    assert users[0].user_id == LEGACY_USER_ID
    assert active == LEGACY_USER_ID


def test_profile_serialization_matches_keys() -> None:
    user = ScaleUser(user_id="u1", name="Alice", person_entity_id="person.alice",
                     sex="female", age=32, height_cm=168.0,
                     activity_level="moderate", initial_weight=62.0)
    profile = scale_user_to_profile(user)
    assert scale_user_from_profile(profile) == user
    assert profile["person_entity_id"] == "person.alice"
    assert pytest.approx(profile["height"]) == 168.0
