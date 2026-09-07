"""Unit tests for the YunmaiLib BIA port."""

from __future__ import annotations

import pytest

from custom_components.realme_scale.bia import YunmaiBia


def test_male_fat_expected_range() -> None:
    calc = YunmaiBia(sex=1, height_cm=175.0, activity_level="moderate")
    fat = calc.get_fat(age=35, weight=75.0, resistance=520)
    assert 5.0 <= fat <= 45.0


def test_female_fat_higher_than_male() -> None:
    male = YunmaiBia(sex=1, height_cm=175.0, activity_level="sedentary")
    female = YunmaiBia(sex=0, height_cm=175.0, activity_level="sedentary")
    fat_m = male.get_fat(age=30, weight=70.0, resistance=500)
    fat_f = female.get_fat(age=30, weight=70.0, resistance=500)
    assert fat_f > fat_m


def test_out_of_band_fat_returns_zero() -> None:
    calc = YunmaiBia(sex=1, height_cm=175.0, activity_level="sedentary")
    # Extremely high resistance -> implausible result -> guarded to 0.
    assert calc.get_fat(age=30, weight=75.0, resistance=5000) == 0.0


def test_water_and_muscle_are_percent_bounded() -> None:
    calc = YunmaiBia(sex=1, height_cm=175.0, activity_level="moderate")
    for fat in (10.0, 20.0, 30.0, 40.0):
        assert 0.0 < calc.get_water(fat) < 100.0
        assert 0.0 < calc.get_muscle(fat) < 100.0
        assert 0.0 < calc.get_skeletal_muscle(fat) < 100.0


def test_lean_body_mass() -> None:
    calc = YunmaiBia(sex=1, height_cm=175.0, activity_level="moderate")
    assert calc.get_lean_body_mass(weight=75.0, body_fat=20.0) == pytest.approx(60.0)


def test_bone_mass_male_plausible() -> None:
    calc = YunmaiBia(sex=1, height_cm=175.0, activity_level="moderate")
    bone = calc.get_bone_mass(muscle=50.0, weight=75.0)
    assert 2.0 <= bone <= 8.0


def test_visceral_fat_clamped() -> None:
    calc = YunmaiBia(sex=1, height_cm=175.0, activity_level="sedentary")
    low = calc.get_visceral_fat(body_fat=5.0, age=30)
    high = calc.get_visceral_fat(body_fat=50.0, age=80)
    assert 1.0 <= low <= 30.0
    assert 1.0 <= high <= 30.0


def test_fitness_branch_changes_muscle() -> None:
    athlete = YunmaiBia(sex=1, height_cm=175.0, activity_level="extreme")
    normal = YunmaiBia(sex=1, height_cm=175.0, activity_level="sedentary")
    assert athlete.get_muscle(20.0) > normal.get_muscle(20.0)
