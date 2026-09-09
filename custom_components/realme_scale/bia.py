# Copyright (C) 2026 Kapil Mahawar
#
# This file is a Python port derived from openScale
# (https://github.com/oliexdev/openScale): YunmaiLib.kt,
# Copyright (C) 2025 olie.xdev <olie.xdeveloper@googlemail.com>,
# licensed under the GNU General Public License v3.  The port and the
# integration code around it are original realme-scale-ha contributions.
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
"""Local body-composition ("BIA") calculation engine.

Faithful Python port of openScale ``YunmaiLib.kt`` (GPL-3.0):
https://github.com/oliexdev/openScale

The RMH2011 scale only transmits weight + impedance + timestamp. All other
body-composition figures (body fat, muscle, water, bone, LBM, visceral fat)
are derived locally from impedance and the user profile, exactly like the
openScale Android handler does with this library.

All functions are pure and unit-testable.
"""

from __future__ import annotations

import math

# Activity levels that select the "fitness body type" branch (HEAVY/EXTREME)
# in the openScale ActivityLevel enum.
FITNESS_ACTIVITY_LEVELS = frozenset({"heavy", "extreme"})


class YunmaiBia:
    """Local BIA calculator.

    sex: 1 = male, 0 = female (as consumed by openScale YunmaiLib).
    height_cm: body height in centimetres.
    activity_level: one of openScale's ActivityLevel names
        ("sedentary", "mild", "moderate", "heavy", "extreme").
    """

    def __init__(self, sex: int, height_cm: float, activity_level: str) -> None:
        self._sex = sex
        self._height = float(height_cm)
        self._fitness_body_type = activity_level in FITNESS_ACTIVITY_LEVELS

    # --- public API (units: percent / kg, values already scaled) ---

    def get_water(self, body_fat: float) -> float:
        """Body water in percent."""
        return ((100.0 - body_fat) * 0.726 * 100.0 + 0.5) / 100.0

    def get_fat(self, age: int, weight: float, resistance: int) -> float:
        """Body fat in percent (0.0 when out of the plausible 5..75 band)."""
        r = (resistance - 100.0) / 100.0
        h = self._height / 100.0

        if r >= 1:
            r = math.sqrt(r)

        fat = (weight * 1.5 / h / h) + (age * 0.08)
        if self._sex == 1:
            fat -= 10.8

        fat = (fat - 7.4) + r

        if fat < 5.0 or fat > 75.0:
            return 0.0
        return fat

    def get_muscle(self, body_fat: float) -> float:
        """Muscle in percent of body weight (openScale convention)."""
        muscle = (100.0 - body_fat) * 0.67
        if self._fitness_body_type:
            muscle = (100.0 - body_fat) * 0.7
        return ((muscle * 100.0) + 0.5) / 100.0

    def get_skeletal_muscle(self, body_fat: float) -> float:
        """Skeletal muscle in percent of body weight."""
        muscle = (100.0 - body_fat) * 0.53
        if self._fitness_body_type:
            muscle = (100.0 - body_fat) * 0.6
        return ((muscle * 100.0) + 0.5) / 100.0

    def get_bone_mass(self, muscle: float, weight: float) -> float:
        """Bone mass in kg."""
        h = self._height - 170.0
        if self._sex == 1:
            bone_mass = (
                (weight * (muscle / 100.0) * 4.0) / 7.0 * 0.22 * 0.6
            ) + (h / 100.0)
        else:
            bone_mass = (
                (weight * (muscle / 100.0) * 4.0) / 7.0 * 0.34 * 0.45
            ) + (h / 100.0)
        return ((bone_mass * 10.0) + 0.5) / 10.0

    def get_lean_body_mass(self, weight: float, body_fat: float) -> float:
        """Fat-free mass in kg."""
        return weight * (100.0 - body_fat) / 100.0

    def get_visceral_fat(self, body_fat: float, age: int) -> float:
        """Visceral fat level (unitless index, clamped)."""
        f = body_fat
        a = age if 18 <= age <= 120 else 18

        if not self._fitness_body_type:
            if self._sex == 1:
                if a < 40:
                    f -= 21.0
                elif a < 60:
                    f -= 22.0
                else:
                    f -= 24.0
            else:
                if a < 40:
                    f -= 34.0
                elif a < 60:
                    f -= 35.0
                else:
                    f -= 36.0

            d = 1.4 if self._sex == 1 else 1.8
            if f > 0.0:
                d = 1.1

            vf = (f / d) + 9.5
            if vf < 1.0:
                return 1.0
            if vf > 30.0:
                return 30.0
            return vf

        # fitness body type
        if body_fat > 15.0:
            vf = (body_fat - 15.0) / 1.1 + 12.0
        else:
            vf = -1 * (15.0 - body_fat) / 1.4 + 12.0
        if vf < 1.0:
            return 1.0
        if vf > 9.0:
            return 9.0
        return vf
