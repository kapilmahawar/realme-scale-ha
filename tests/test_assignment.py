"""Unit tests for the (weight + impedance) attribution policy."""

from __future__ import annotations

from custom_components.realme_scale.assignment import (
    Attribution,
    UserReadingRef,
    attribute_measurement,
)

ALICE = UserReadingRef("alice", weight_kg=60.0, impedance_ohm=500)
BOB = UserReadingRef("bob", weight_kg=75.0, impedance_ohm=520)
# Same weight as Alice, but very different impedance (different make-up).
CAROL = UserReadingRef("carol", weight_kg=60.0, impedance_ohm=650)


def _attr(refs, weight, impedance, w_tol=3.0, z_tol=60.0) -> Attribution:
    return attribute_measurement(refs, weight, impedance, w_tol, z_tol)


def test_no_candidates() -> None:
    result = _attr([], 75.0, None)
    assert result.user_id is None
    assert not result.candidates
    assert not result.is_assigned


def test_single_user_always_wins() -> None:
    """A one-user scale needs no heuristics, whatever the tolerances."""
    result = _attr([ALICE], 120.0, None, w_tol=0.0, z_tol=0.0)
    assert result.user_id == "alice"
    assert result.is_assigned


def test_unique_weight_match_assigns() -> None:
    assert _attr([ALICE, BOB], 74.8, 518).user_id == "bob"


def test_impedance_discriminates_same_weight_users() -> None:
    """Alice and Carol weigh the same; Carol's impedance is far out of the
    tolerance band around this reading, so only Alice matches."""
    assert _attr([ALICE, CAROL], 60.3, 512).user_id == "alice"


def test_same_weight_similar_impedance_is_ambiguous() -> None:
    """Two users both within tolerance on both axes -> do not guess."""
    dan = UserReadingRef("dan", weight_kg=60.0, impedance_ohm=560)
    result = _attr([ALICE, dan], 60.3, 530)
    assert result.user_id is None
    assert set(result.candidates) == {"alice", "dan"}


def test_weight_outside_tolerance_is_pending() -> None:
    result = _attr([ALICE, BOB], 90.0, 520)
    assert result.user_id is None
    assert not result.candidates


def test_candidates_ordered_nearest_first() -> None:
    erin = UserReadingRef("erin", weight_kg=75.8, impedance_ohm=540)
    result = _attr([BOB, erin, ALICE], 75.2, 530)
    # BOB/erin are plausible (Alice's weight is far away).
    assert result.user_id is None
    assert result.candidates == ("bob", "erin")


def test_zero_weight_tolerance_disables_auto_assign() -> None:
    result = _attr([ALICE, BOB], 60.2, 505, w_tol=0.0)
    assert result.user_id is None
    assert not result.is_assigned


def test_weight_only_reading_uses_weight_gate() -> None:
    """No impedance in the reading -> match purely on weight."""
    assert _attr([ALICE, BOB], 75.2, None).user_id == "bob"
    # Two users weight-close, no impedance available to tell them apart.
    dan = UserReadingRef("dan", weight_kg=60.0, impedance_ohm=None)
    ambiguous = _attr([ALICE, dan], 60.4, None)
    assert ambiguous.user_id is None
    assert len(ambiguous.candidates) == 2


def test_candidate_without_baseline_never_matches() -> None:
    newcomer = UserReadingRef("dave", weight_kg=None, impedance_ohm=None)
    result = _attr([newcomer, BOB], 70.0, 500)
    assert result.user_id is None
