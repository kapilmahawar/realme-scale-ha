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
"""Derived body metrics and interpretation tables (pure, no HA imports).

Values that need only the latest measurement and the user profile are
computed on the fly by the sensor platform (they are not persisted).  This
module mirrors the approach of integrations like ``bodymiscale`` but is a
faithful, documented port of standard equations:

- BMI + WHO BMI categories
- BMR: Schofield equation (FAO/WHO/UNU 1985), age-stratified
- Ideal weight: Devine formula
- Body fat mass (kg) and protein % (Wang, 19.5 % of lean mass)
- Body-fat category: Gallagher et al. (2000) sex/age bands
- Body-water level and visceral-fat rating (informational ranges)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Anthropometrics
# ---------------------------------------------------------------------------


def bmi(weight_kg: float | None, height_cm: float | None) -> float | None:
    """Body mass index in kg/m^2."""
    if weight_kg is None or not height_cm:
        return None
    h = height_cm / 100.0
    if h <= 0:
        return None
    return weight_kg / (h * h)


def bmi_category(value: float | None) -> str | None:
    """WHO BMI bands (Underweight..Obese class III)."""
    if value is None:
        return None
    if value < 18.5:
        return "Underweight"
    if value < 25.0:
        return "Normal"
    if value < 30.0:
        return "Overweight"
    if value < 35.0:
        return "Obese class I"
    if value < 40.0:
        return "Obese class II"
    return "Obese class III"


def body_fat_mass_kg(weight_kg: float | None, body_fat_pct: float | None) -> float | None:
    """Absolute fat mass in kg."""
    if weight_kg is None or body_fat_pct is None:
        return None
    return weight_kg * body_fat_pct / 100.0


def protein_pct(body_fat_pct: float | None) -> float | None:
    """Protein as % of total weight (Wang: ~19.5 % of fat-free mass)."""
    if body_fat_pct is None:
        return None
    lean_pct = 100.0 - body_fat_pct
    if lean_pct < 0:
        return None
    return 0.195 * lean_pct


def schofield_bmr(
    is_male: bool, age: int | None, weight_kg: float | None
) -> float | None:
    """Basal metabolic rate (kcal/day), Schofield / FAO-WHO-UNU 1985."""
    if age is None or weight_kg is None or age < 0:
        return None
    table = SCHOFIELD_MALE if is_male else SCHOFIELD_FEMALE
    for max_age, weight_coef, constant in table:
        if age < max_age:
            return weight_coef * weight_kg + constant
    # >= 60 group
    _, weight_coef, constant = table[-1]
    return weight_coef * weight_kg + constant


def devine_ideal_weight(is_male: bool, height_cm: float | None) -> float | None:
    """Ideal body weight in kg (Devine 1974)."""
    if height_cm is None:
        return None
    base = 50.0 if is_male else 45.5
    return base + 0.91 * (height_cm - 152.4)


# ---------------------------------------------------------------------------
# Interpretation tables
# ---------------------------------------------------------------------------

# BMR: (exclusive upper age bound, weight coefficient, constant), last = 60+
SCHOFIELD_MALE = (
    (3, 59.512, -30.4),
    (10, 22.706, 504.3),
    (18, 17.686, 658.2),
    (30, 15.057, 692.2),
    (60, 11.472, 873.1),
    (999, 11.711, 587.7),
)

SCHOFIELD_FEMALE = (
    (3, 58.317, -31.1),
    (10, 20.315, 485.9),
    (18, 13.384, 692.6),
    (30, 14.818, 486.6),
    (60, 8.126, 845.6),
    (999, 9.082, 658.5),
)

# Body-fat % bands (healthy / upper limits) by sex and age (Gallagher 2000).
# Rows: (min age, underfat limit, healthy upper, overfat upper).
_FAT_TABLE: dict[bool, tuple[tuple[int, float, float, float], ...]] = {
    True: (  # male
        (0, 8.0, 20.0, 25.0),   # 20-39
        (40, 11.0, 22.0, 28.0),  # 40-59
        (60, 13.0, 25.0, 30.0),  # 60-79
    ),
    False: (  # female
        (0, 21.0, 33.0, 39.0),
        (40, 23.0, 34.0, 40.0),
        (60, 24.0, 36.0, 42.0),
    ),
}


def fat_category(is_male: bool, age: int | None, fat_pct: float | None) -> str | None:
    """Underfat / Healthy / Overfat / Obese for the person's age band."""
    if age is None or fat_pct is None:
        return None
    table = _FAT_TABLE[is_male]
    row = table[-1]
    for candidate in table:
        if age < candidate[0] + 40:
            row = candidate
            break
    _, underfat, healthy_up, overfat_up = row
    if fat_pct < underfat:
        return "Underfat"
    if fat_pct <= healthy_up:
        return "Healthy"
    if fat_pct <= overfat_up:
        return "Overfat"
    return "Obese"


def body_type(is_male: bool, age: int | None, fat_pct: float | None,
              bmi_value: float | None) -> str | None:
    """Simple heuristic body type from BMI + fat band (see module docs)."""
    category = fat_category(is_male, age, fat_pct)
    if category is None or bmi_value is None:
        return None
    if category == "Obese" or bmi_value >= 30.0:
        return "Obese"
    if category == "Overfat" or bmi_value >= 25.0:
        return "Overweight"
    if category == "Underfat" or bmi_value < 18.5:
        return "Underweight"
    return "Normal"


def water_category(is_male: bool, water_pct: float | None) -> str | None:
    """Low / Normal / High against commonly cited hydration ranges."""
    if water_pct is None:
        return None
    low, high = (50.0, 65.0) if is_male else (45.0, 60.0)
    if water_pct < low:
        return "Low"
    if water_pct > high:
        return "High"
    return "Normal"


def visceral_fat_category(value: float | None) -> str | None:
    """Healthy / Elevated / High for the unitless visceral-fat index."""
    if value is None:
        return None
    if value <= 9.0:
        return "Healthy"
    if value <= 14.0:
        return "Elevated"
    return "High"
