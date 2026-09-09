# Copyright (C) 2026 Kapil Mahawar
#
# This file is part of the realme-scale-ha Home Assistant integration.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""Automatic user identification (pure logic, no HA imports).

Separates *identity* from *latest measurement*:

- Each user has an **identity center** (expected weight; falls back to
  initial weight / validated history in the coordinator) and a tolerance.
  Identity lives in ``[center - tol, center + tol]``.
- A wrong assignment outside that range can never shift the center, so a
  wrongly assigned ~52 kg record cannot poison a ~73 kg user.
- Impedance is an optional *secondary* discriminator when weight alone
  leaves several plausible users.

Hard rules:

- impossible (out-of-range) candidates are rejected,
- exactly one plausible candidate -> assigned (weight method; impedance is
  only used to resolve ties),
- zero candidates -> unassigned,
- several comparable candidates -> ambiguous / unassigned, never a guess,
- a single configured user keeps working without heuristics,
- missing impedance never crashes (weight-only matching).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Assignment methods (also used for record/event payloads).
METHOD_NONE = "none"
METHOD_WEIGHT = "automatic_weight"
METHOD_WEIGHT_IMPEDANCE = "automatic_weight_impedance"


@dataclass(frozen=True)
class UserIdentity:
    """One user's resolved identity for a comparison.

    ``center``/``weight_tolerance_kg`` are already resolved by the caller
    (per-user value or global default) and are always present here.
    ``baseline_impedance_ohm`` is the newest identity-valid measurement's
    impedance, used only when both sides measured it.
    """

    user_id: str
    center: float
    weight_tolerance_kg: float
    baseline_impedance_ohm: int | None = None
    impedance_tolerance_ohm: float = 0.0


@dataclass(frozen=True)
class AssignmentResult:
    """Outcome of one identification attempt."""

    user_id: str | None
    method: str = METHOD_NONE
    confidence: float | None = None
    candidates: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_assigned(self) -> bool:
        return self.user_id is not None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _confidence(
    weight_kg: float,
    user: UserIdentity,
    impedance_ohm: int | None,
    weight_resolved: bool,
) -> float:
    """0..1 plausibility for the winning user (informational)."""
    weight_ratio = 0.0
    if user.weight_tolerance_kg > 0:
        weight_ratio = abs(user.center - weight_kg) / user.weight_tolerance_kg
    confidence = 1.0 - 0.5 * weight_ratio
    if (
        weight_resolved
        and impedance_ohm is not None
        and user.baseline_impedance_ohm is not None
        and user.impedance_tolerance_ohm > 0
    ):
        impedance_ratio = (
            abs(impedance_ohm - user.baseline_impedance_ohm)
            / user.impedance_tolerance_ohm
        )
        confidence -= 0.25 * impedance_ratio
    return _clamp(confidence)


def _sort_key(user: UserIdentity, weight_kg: float) -> tuple[float, str]:
    weight_delta = abs(user.center - weight_kg)
    normalized = (
        weight_delta / user.weight_tolerance_kg
        if user.weight_tolerance_kg > 0
        else weight_delta
    )
    return (normalized, user.user_id)


def identify(
    users: list[UserIdentity],
    weight_kg: float,
    impedance_ohm: int | None,
) -> AssignmentResult:
    """Identify the person behind a measurement; never guesses."""
    if not users:
        return AssignmentResult(user_id=None)

    # A single-user scale needs no heuristics.
    if len(users) == 1:
        user = users[0]
        has_impedance = (
            impedance_ohm is not None
            and user.baseline_impedance_ohm is not None
            and user.impedance_tolerance_ohm > 0
        )
        return AssignmentResult(
            user_id=user.user_id,
            method=METHOD_WEIGHT_IMPEDANCE if has_impedance else METHOD_WEIGHT,
            confidence=_confidence(weight_kg, user, impedance_ohm, True),
        )

    candidates = [
        user
        for user in users
        if user.weight_tolerance_kg > 0
        and abs(user.center - weight_kg) <= user.weight_tolerance_kg
    ]

    if len(candidates) == 1:
        user = candidates[0]
        return AssignmentResult(
            user_id=user.user_id,
            method=METHOD_WEIGHT,
            confidence=_confidence(weight_kg, user, impedance_ohm, True),
        )

    if not candidates:
        return AssignmentResult(user_id=None)

    # Several users are inside their weight ranges.  Try impedance as the
    # secondary discriminator when it is available for both sides.
    if impedance_ohm is not None:
        impedance_matched = [
            user
            for user in candidates
            if user.baseline_impedance_ohm is not None
            and user.impedance_tolerance_ohm > 0
            and abs(impedance_ohm - user.baseline_impedance_ohm)
            <= user.impedance_tolerance_ohm
        ]
        if len(impedance_matched) == 1:
            user = impedance_matched[0]
            return AssignmentResult(
                user_id=user.user_id,
                method=METHOD_WEIGHT_IMPEDANCE,
                confidence=_confidence(weight_kg, user, impedance_ohm, True),
            )
        if len(impedance_matched) > 1:
            candidates = impedance_matched

    ordered = sorted(candidates, key=lambda user: _sort_key(user, weight_kg))
    return AssignmentResult(
        user_id=None,
        method=METHOD_NONE,
        candidates=tuple(user.user_id for user in ordered),
    )
