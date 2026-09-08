"""Unit tests for the identity-range identification engine."""

from __future__ import annotations

from custom_components.realme_scale.assignment import (
    METHOD_NONE,
    METHOD_WEIGHT,
    METHOD_WEIGHT_IMPEDANCE,
    AssignmentResult,
    UserIdentity,
    identify,
)

ALICE = UserIdentity("alice", center=73.0, weight_tolerance_kg=8.0)
BOB = UserIdentity("bob", center=52.0, weight_tolerance_kg=6.0)


def _result(*args, **kwargs) -> AssignmentResult:
    return identify(*args, **kwargs)


def test_no_candidates() -> None:
    result = _result([], 70.0, None)
    assert result.user_id is None
    assert result.method == METHOD_NONE


def test_single_user_easy() -> None:
    result = _result([ALICE], 90.0, None)
    assert result.user_id == "alice"  # single-user scale needs no heuristics


def test_weight_identifies_unique_user() -> None:
    assert _result([ALICE, BOB], 52.0, None).user_id == "bob"
    assert _result([ALICE, BOB], 52.0, None).method == METHOD_WEIGHT
    assert _result([ALICE, BOB], 73.0, None).user_id == "alice"
    assert _result([ALICE, BOB], 73.4, None).user_id == "alice"
    assert _result([ALICE, BOB], 52.2, None).user_id == "bob"


def test_no_valid_candidate_unassigned() -> None:
    # 60 kg is outside both ranges (Alice 65..81, Bob 46..58).
    result = _result([ALICE, BOB], 60.0, None)
    assert result.user_id is None
    assert result.method == METHOD_NONE
    assert result.candidates == ()


def test_ambiguous_close_users_unassigned() -> None:
    close_alice = UserIdentity("alice", center=70.0, weight_tolerance_kg=3.0)
    close_bob = UserIdentity("bob", center=69.0, weight_tolerance_kg=3.0)
    result = _result([close_alice, close_bob], 69.5, None)
    assert result.user_id is None
    assert set(result.candidates) == {"alice", "bob"}


def test_impedance_resolves_tie() -> None:
    close_alice = UserIdentity(
        "alice", 70.0, 3.0, baseline_impedance_ohm=480, impedance_tolerance_ohm=60
    )
    close_bob = UserIdentity(
        "bob", 69.0, 3.0, baseline_impedance_ohm=620, impedance_tolerance_ohm=60
    )
    # 69.5 kg is in both ranges; 595 Ohms is only plausible for Bob.
    result = _result([close_alice, close_bob], 69.5, 595)
    assert result.user_id == "bob"
    assert result.method == METHOD_WEIGHT_IMPEDANCE


def test_impedance_unavailable_weight_only() -> None:
    close_alice = UserIdentity(
        "alice", 70.0, 3.0, baseline_impedance_ohm=480, impedance_tolerance_ohm=60
    )
    # Unique weight match even though impedance is missing.
    result = _result([close_alice, BOB], 69.4, None)
    assert result.user_id == "alice"
    assert result.method == METHOD_WEIGHT


def test_missing_impedance_never_crashes() -> None:
    close = [
        UserIdentity("a", 70.0, 3.0, baseline_impedance_ohm=None),
        UserIdentity("b", 75.0, 3.0, baseline_impedance_ohm=500),
    ]
    assert _result(close, 74.8, None).user_id == "b"


def test_anti_poison_wrong_assignment_does_not_reidentify() -> None:
    """Regression: a wrong ~52 kg assignment to Alice must not poison him.

    Alice's identity range comes from his expected weight, so an
    out-of-range record can never shift his identity center.  The next
    ~52 kg reading must still identify Bob.
    """
    assert _result([ALICE, BOB], 52.0, None).user_id == "bob"
    # 52 kg is inside Bob's range, outside Alice's - always Bob.
    for weight in (52.0, 52.1, 52.2, 51.9):
        assert _result([ALICE, BOB], weight, None).user_id == "bob"
    # And Alice's own range is unaffected.
    assert _result([ALICE, BOB], 73.0, None).user_id == "alice"
    assert _result([ALICE, BOB], 60.0, None).user_id is None
