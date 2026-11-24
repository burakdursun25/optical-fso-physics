import math

import pytest

from backend.engine import AtmosphereGrid, AtmosphericLoss, RayTracer, Vec3


def test_beer_lambert_clear_air_loss_at_six_kilometres():
    loss = AtmosphericLoss.absorptionLoss(6000.0, "clear", 1550.0)

    assert loss["loss_dB"] == pytest.approx(1.2, abs=0.5)
    assert 0.0 < loss["transmission"] < 1.0


def test_geometric_loss_includes_pointing_error():
    aligned = AtmosphericLoss.geometricLoss(6000.0, 0.001, 0.1, 0.004, 0.0)
    misaligned = AtmosphericLoss.geometricLoss(6000.0, 0.001, 0.1, 0.004, 1e-4)

    assert misaligned["pointingOffsetM"] > 0.0
    assert misaligned["pointingCoupling"] < 1.0
    assert misaligned["loss_dB"] > aligned["loss_dB"]


def test_edlen_index_decreases_as_temperature_increases():
    atmosphere = AtmosphereGrid(2, 2, 2, 1.0)

    cool = atmosphere.refractiveIndexSimple(15.0, 101325.0)
    hot = atmosphere.refractiveIndexSimple(35.0, 101325.0)

    assert cool > hot
    assert cool == pytest.approx(1.000293, abs=1e-5)


def test_scintillation_index_is_finite_and_positive():
    result = AtmosphericLoss.scintillationIndex(6000.0, 1e-15, 1550e-9)

    assert result["sigma2_R"] > 0.0
    assert math.isfinite(result["sigma2_R"])
    assert result["regime"] in {"weak", "moderate", "strong"}


def test_each_environment_preset_builds_an_atmosphere_grid():
    from backend.engine import EnvironmentPresets

    for key in EnvironmentPresets.get_preset_keys():
        preset = EnvironmentPresets.get_preset(key)
        atmosphere = AtmosphereGrid(8, 4, 4, 200.0)
        atmosphere.applyThermalProfile(preset["thermalProfile"], {
            "baseTemp": preset["baseTemp"],
            "deltaT": preset["deltaT"],
            "humidity": preset["humidity"],
            "windSpeed": preset["windSpeed"],
            "Cn2Level": preset["Cn2Level"],
            "wavelengthUm": 1.55,
            "useEdlen": True,
        })
        assert len(atmosphere.grid) == 8 * 4 * 4


def test_path_average_cn2_includes_every_traversed_segment():
    atmosphere = AtmosphereGrid(4, 2, 2, 100.0)
    atmosphere.Cn2.fill(2e-15)

    trace = RayTracer(atmosphere).trace(Vec3(0.0, 100.0, 100.0), Vec3(1.0, 0.0, 0.0))

    assert trace["avgCn2"] == pytest.approx(2e-15, rel=1e-12)


def test_randomized_humidity_stays_in_physical_range():
    atmosphere = AtmosphereGrid(4, 2, 2, 100.0)

    for humidity in (0.0, 100.0):
        atmosphere.applyThermalProfile("uniform", {"humidity": humidity, "seed": 42})
        assert atmosphere.humidity.min() >= 0.0
        assert atmosphere.humidity.max() <= 100.0


def test_kim_kruse_fog_and_haze_attenuation_in_db_per_km():
    fog = AtmosphericLoss.absorptionLoss(1000.0, "fog", 1550.0)
    haze = AtmosphericLoss.absorptionLoss(1000.0, "haze", 1550.0)
    custom = AtmosphericLoss.absorptionLoss(1000.0, "fog", 1550.0, visibility_km=0.2)

    assert fog["alpha_dB_km"] == pytest.approx(33.96, abs=0.5)
    assert haze["alpha_dB_km"] == pytest.approx(1.54, abs=0.2)
    assert custom["alpha_dB_km"] > fog["alpha_dB_km"]


def test_edlen_humidity_correction_uses_pascal_units():
    atmosphere = AtmosphereGrid(2, 2, 2, 100.0)
    n_dry = atmosphere.refractiveIndexEdlen(15.0, 101325.0, humidity=0.0, wavelengthUm=1.55)
    n_wet = atmosphere.refractiveIndexEdlen(15.0, 101325.0, humidity=100.0, wavelengthUm=1.55)
    delta_n = n_wet - n_dry

    assert -1e-6 <= delta_n <= -5e-7
    assert delta_n == pytest.approx(-7.3e-7, abs=2e-7)


def test_scintillation_penalty_is_continuous_across_sigma2_five():
    below = AtmosphericLoss.linkBudget({"distance_m": 6000.0, "Cn2": 9.2e-15})
    above = AtmosphericLoss.linkBudget({"distance_m": 6000.0, "Cn2": 9.6e-15})

    assert below["scintillation"]["sigma2_R"] < 5.0 < above["scintillation"]["sigma2_R"]
    assert abs(above["scintPenalty_dB"] - below["scintPenalty_dB"]) < 0.2


def test_cn2_levels_are_distinguishable_at_link_altitude():
    vals = {}
    for level in ("weak", "moderate", "strong"):
        g = AtmosphereGrid(10, 10, 10, 100.0)
        g.applyThermalProfile("gradient", {"Cn2Level": level})
        vals[level] = float(g.getCn2(5, 5, 5))

    assert vals["weak"] < vals["moderate"] < vals["strong"]
    assert vals["moderate"] / vals["weak"] >= 3.0
