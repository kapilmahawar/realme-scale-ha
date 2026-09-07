"""Measurement attribution (pure logic, no HA imports).

The RMH2011 scale never identifies who is standing on it, so attribution is
a heuristic on the signals the scale *does* send: weight and, when present,
impedance.  Policy (per measurement):

- With a single configured user every measurement belongs to them.
- A measurement is a candidate for a user when their last reading is within
  the weight tolerance **and** (when both readings carry impedance) within
  the impedance tolerance.
- Exactly one candidate  -> attributed to that user.
- Zero candidates        -> unassigned ("unknown"), ask the household.
- Several candidates     -> genuinely ambiguous (e.g. two people at a
  similar weight); the measurement stays unassigned and the candidates are
  returned in order of closeness so a confirmation prompt can offer them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class UserReadingRef:
    """One candidate: a user's most recent (weight, impedance) reading.

    ``weight_kg``/``impedance_ohm`` may fall back to profile data (initial
    weight); ``None`` means "no usable baseline".
    """

    user_id: str
    weight_kg: float | None = None
    impedance_ohm: int | None = None


@dataclass(frozen=True)
class Attribution:
    """Result of attributing one measurement to the user registry."""

    user_id: str | None
    """Owner id, or ``None`` when the reading stays unassigned."""

    candidates: tuple[str, ...] = field(default_factory=tuple)
    """Plausible owners, nearest first (empty when none or unambiguous)."""

    @property
    def is_assigned(self) -> bool:
        return self.user_id is not None


def _eligible(
    ref: UserReadingRef,
    weight_kg: float,
    impedance_ohm: int | None,
    weight_tolerance_kg: float,
    impedance_tolerance_ohm: float,
) -> bool:
    """Whether a user's baseline is consistent with this reading."""
    if ref.weight_kg is None:
        return False
    if abs(ref.weight_kg - weight_kg) > weight_tolerance_kg:
        return False
    # Impedance only discriminates when *both* sides measured it.  It is
    # noisy enough that a mismatch means "not sure", never "wrong person".
    impedance_ok = (
        ref.impedance_ohm is None
        or impedance_ohm is None
        or impedance_tolerance_ohm <= 0
        or abs(ref.impedance_ohm - impedance_ohm) <= impedance_tolerance_ohm
    )
    return impedance_ok


def attribute_measurement(
    candidates: list[UserReadingRef],
    weight_kg: float,
    impedance_ohm: int | None,
    weight_tolerance_kg: float,
    impedance_tolerance_ohm: float,
) -> Attribution:
    """Attribute one measurement; see module docstring for the rules."""
    if not candidates:
        return Attribution(user_id=None)

    # A one-user scale needs no heuristics: it is always that user.
    if len(candidates) == 1:
        return Attribution(user_id=candidates[0].user_id)

    if weight_tolerance_kg <= 0:
        # Auto-assignment disabled entirely.
        return Attribution(user_id=None)

    eligible = [
        ref
        for ref in candidates
        if _eligible(
            ref,
            weight_kg,
            impedance_ohm,
            weight_tolerance_kg,
            impedance_tolerance_ohm,
        )
    ]

    if len(eligible) == 1:
        return Attribution(user_id=eligible[0].user_id)

    if not eligible:
        return Attribution(user_id=None)

    # Multiple plausible owners -> ambiguous.  Rank by normalised closeness
    # in (weight, impedance) space so a prompt can offer the best guesses.
    def _score(ref: UserReadingRef) -> float:
        dw = (abs(ref.weight_kg - weight_kg) / weight_tolerance_kg) ** 2  # type: ignore[operator]
        dz = 0.0
        if (
            ref.impedance_ohm is not None
            and impedance_ohm is not None
            and impedance_tolerance_ohm > 0
        ):
            dz = (abs(ref.impedance_ohm - impedance_ohm) / impedance_tolerance_ohm) ** 2
        return dw + dz

    eligible.sort(key=_score)
    return Attribution(
        user_id=None,
        candidates=tuple(ref.user_id for ref in eligible),
    )
