"""Unit tests for derived metrics & interpretation tables."""

from __future__ import annotations

import pytest

from custom_components.realme_scale.metrics import (
    bmi,
    bmi_category,
    body_fat_mass_kg,
    body_type,
    devine_ideal_weight,
    fat_category,
    protein_pct,
    schofield_bmr,
    visceral_fat_category,
    water_category,
)


def test_bmi_and_category() -> None:
    # 70 kg @ 1.75 m = 22.86
    assert bmi(70.0, 175.0) == pytest.approx(22.857, abs=0.01)
    assert bmi_category(bmi(70.0, 175.0)) == "Normal"
    assert bmi_category(17.0) == "Underweight"
    assert bmi_category(26.0) == "Overweight"
    assert bmi_category(32.0) == "Obese class I"
    assert bmi_category(37.0) == "Obese class II"
    assert bmi_category(42.0) == "Obese class III"


def test_bmi_guards() -> None:
    assert bmi(None, 175.0) is None
    assert bmi(70.0, None) is None


def test_fat_mass_and_protein() -> None:
    assert body_fat_mass_kg(80.0, 25.0) == pytest.approx(20.0)
    assert protein_pct(20.0) == pytest.approx(15.6)  # 19.5% of 80% lean
    assert protein_pct(None) is None


def test_schofield_bmr_sane_ranges() -> None:
    male = schofield_bmr(True, 35, 75.0)
    female = schofield_bmr(False, 35, 65.0)
    assert male is not None and 1400 < male < 2100
    assert female is not None and 1200 < female < 1700
    assert schofield_bmr(True, None, 75.0) is None


def test_devine_ideal_weight() -> None:
    # male 180 cm: 50 + 0.91*(180-152.4) = 75.116
    assert devine_ideal_weight(True, 180.0) == pytest.approx(75.116, abs=1e-3)
    assert devine_ideal_weight(False, 180.0) == pytest.approx(70.616, abs=1e-3)


def test_fat_category_typical() -> None:
    # Male 35y: 25% fat -> Overfat band (healthy upper 20, overfat upper 25+)
    assert fat_category(True, 35, 25.0) in ("Overfat", "Obese")
    assert fat_category(True, 35, 12.0) == "Healthy"
    assert fat_category(False, 45, 20.0) == "Underfat"  # female 40-59 lower band 23


def test_water_and_visceral() -> None:
    assert water_category(True, 58.0) == "Normal"
    assert water_category(True, 40.0) == "Low"
    assert water_category(False, 70.0) == "High"
    assert visceral_fat_category(5.0) == "Healthy"
    assert visceral_fat_category(12.0) == "Elevated"
    assert visceral_fat_category(20.0) == "High"


def test_body_type_heuristic() -> None:
    assert body_type(True, 35, 30.0, bmi(80.0, 175.0)) in ("Obese", "Overweight")
    assert body_type(True, 35, 12.0, bmi(65.0, 175.0)) == "Normal"
