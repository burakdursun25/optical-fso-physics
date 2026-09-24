import math

import pytest

from backend.engine import AtmosphereGrid, AtmosphericLoss


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
