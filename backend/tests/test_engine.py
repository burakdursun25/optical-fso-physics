import math
from backend.engine import (Vec3, AtmosphereGrid, RayTracer, DataTransmissionSimulator,
                             AtmosphericLoss, vectorialSnellLaw, verifySnellLaw,
                             LensSystem, EnvironmentPresets, ComprehensiveTransmissionTest)

# ── Original Physics Tests (T1-T11) ──────────────────────────

def test_normal_incidence():
    i = Vec3(0, 0, -1)
    n = Vec3(0, 0, 1)
    result = vectorialSnellLaw(i, n, 1.0, 1.5)
    assert result is not None
    assert abs(result["direction"].z - (-1.0)) < 1e-6

def test_refraction_air_to_glass_30():
    theta_i = 30.0 * math.pi / 180.0
    i = Vec3(math.sin(theta_i), 0, -math.cos(theta_i))
    n = Vec3(0, 0, 1)
    result = vectorialSnellLaw(i, n, 1.0, 1.5)
    assert result is not None
    expectedSin = (1.0 / 1.5) * math.sin(theta_i)
    actualSin = math.sqrt(1.0 - result["cosTheta_t"] * result["cosTheta_t"])
    assert abs(actualSin - expectedSin) < 1e-10

def test_total_internal_reflection():
    critAngle = math.asin(1.0 / 1.5)
    theta = critAngle + 5.0 * math.pi / 180.0
    i = Vec3(math.sin(theta), 0, -math.cos(theta))
    result = vectorialSnellLaw(i, Vec3(0, 0, 1), 1.5, 1.0)
    assert result is None

def test_atmosphere_boundary():
    n1, n2 = 1.000293, 1.000270
    theta_i = 5.0 * math.pi / 180.0
    i = Vec3(math.cos(theta_i), math.sin(theta_i), 0)
    result = vectorialSnellLaw(i, Vec3(-1, 0, 0), n1, n2)
    assert result is not None
    verify = verifySnellLaw(n1, n2, result["cosTheta_i"], result["cosTheta_t"])
    assert verify["valid"]

def test_reciprocity():
    n1, n2 = 1.0003, 1.0001
    theta_i = 10.0 * math.pi / 180.0
    i = Vec3(math.sin(theta_i), 0, -math.cos(theta_i))
    forward = vectorialSnellLaw(i, Vec3(0, 0, 1), n1, n2)
    assert forward is not None
    backward = vectorialSnellLaw(forward["direction"].scale(-1.0), Vec3(0, 0, -1), n2, n1)
    assert backward is not None
    recovered = backward["direction"].scale(-1.0)
    error = math.sqrt(math.pow(recovered.x - i.x, 2) + math.pow(recovered.y - i.y, 2) + math.pow(recovered.z - i.z, 2))
    assert error < 1e-10

def test_tangential_conservation():
    n1, n2 = 1.33, 1.0
    theta_i = 20.0 * math.pi / 180.0
    i = Vec3(math.sin(theta_i), 0, -math.cos(theta_i))
    n = Vec3(0, 0, 1)
    result = vectorialSnellLaw(i, n, n1, n2)
    assert result is not None
    i_tan = i.sub(n.scale(i.dot(n)))
    t_tan = result["direction"].sub(n.scale(result["direction"].dot(n)))
    assert abs(n1 * i_tan.length() - n2 * t_tan.length()) < 1e-10

def test_beer_lambert_loss():
    loss = AtmosphericLoss.absorptionLoss(6000.0, "clear", 1550.0)
    expectedLoss_dB = 0.2 * 6.0
    assert abs(loss["loss_dB"] - expectedLoss_dB) < 0.5

def test_geometric_loss():
    geo = AtmosphericLoss.geometricLoss(6000.0, 0.001, 0.1, 0.004)
    expectedBeamDiam = 0.004 + 2.0 * 0.001 * 6000.0
    assert abs(geo["beamDiamAtReceiver"] - expectedBeamDiam) < 0.01

def test_edlen_equation():
    atm = AtmosphereGrid(2, 2, 2, 1.0)
    n_15C = atm.refractiveIndexSimple(15.0, 101325.0)
    n_35C = atm.refractiveIndexSimple(35.0, 101325.0)
    assert n_15C > n_35C
    assert abs(n_15C - 1.000293) < 1e-5

def test_scintillation():
    scint = AtmosphericLoss.scintillationIndex(6000.0, 1e-15, 1550e-9)
    assert scint["sigma2_R"] > 0
    assert not math.isnan(scint["sigma2_R"])

# ── Lens System Tests (T12-T13) ───────────────────────────────

def test_collimating_lens_reduces_divergence():
    """T12: Collimating lens must reduce beam divergence."""
    lens = LensSystem("collimating")
    result = lens.apply_to_beam({"divergenceRad": 1e-3, "beamDiamM": 0.004, "receiverDiamM": 0.1})
    assert result["divergenceRad"] < 1e-3, "Collimating lens should reduce divergence"
    assert result["receiverDiamM"] > 0.1, "Collimating lens should boost effective aperture"
    assert 0.0 < result["transmittance"] <= 1.0, "Transmittance must be in (0, 1]"

def test_diverging_lens_increases_divergence():
    """Diverging lens must increase beam divergence."""
    lens = LensSystem("diverging")
    result = lens.apply_to_beam({"divergenceRad": 1e-3, "beamDiamM": 0.004, "receiverDiamM": 0.1})
    assert result["divergenceRad"] > 1e-3, "Diverging lens should increase divergence"

def test_cassegrain_highest_gain():
    """T13: Cassegrain must give the highest link budget gain."""
    distance = 6000.0
    gains = {}
    for lt in LensSystem.LENS_TYPES:
        lens = LensSystem(lt)
        gains[lt] = lens.get_link_budget_gain_dB(distance)
    assert gains["cassegrain"] > gains["none"], "Cassegrain should outperform no lens"
    assert gains["cassegrain"] >= gains["galilean_telescope"], "Cassegrain should be best telescope"
    assert gains["cassegrain"] >= gains["collimating"], "Cassegrain should exceed collimating"

def test_lens_gain_monotonic_with_aperture():
    """Lenses with higher apertureBoost should generally give better gain at 6 km."""
    lens_none = LensSystem("none")
    lens_col = LensSystem("collimating")
    gain_none = lens_none.get_link_budget_gain_dB(6000.0)
    gain_col = lens_col.get_link_budget_gain_dB(6000.0)
    assert gain_col > gain_none, "Collimating lens should improve link vs no lens"

def test_lens_transmittance_power_penalty():
    """Diverging lens with transmittance < 1 should be penalized."""
    lens = LensSystem("diverging")
    tx = lens.spec["transmittance"]
    assert tx < 1.0
    penalty_dB = -10.0 * math.log10(tx)
    assert penalty_dB > 0.0

# ── Environment Preset Tests (T14) ────────────────────────────

def test_all_presets_have_required_fields():
    """T14: Every environment preset must have all required fields."""
    required = ["thermalProfile", "weather", "Cn2Level", "baseTemp", "deltaT", "windSpeed", "humidity"]
    for pk in EnvironmentPresets.get_preset_keys():
        preset = EnvironmentPresets.get_preset(pk)
        for field in required:
            assert field in preset, f"Preset '{pk}' missing field '{field}'"

def test_preset_temperature_ranges():
    """Preset temperatures must be physically plausible (-50 to 60 °C)."""
    for pk in EnvironmentPresets.get_preset_keys():
        preset = EnvironmentPresets.get_preset(pk)
        assert -50.0 <= preset["baseTemp"] <= 60.0, f"Preset '{pk}' baseTemp out of range"

def test_desert_hotter_than_arctic():
    """Desert preset must be warmer than Arctic preset."""
    desert = EnvironmentPresets.get_preset("desert")
    arctic = EnvironmentPresets.get_preset("arctic")
    assert desert["baseTemp"] > arctic["baseTemp"]

def test_industrial_worst_weather():
    """Industrial preset should have fog (worst visibility)."""
    industrial = EnvironmentPresets.get_preset("industrial")
    assert industrial["weather"] == "fog"

def test_preset_grid_builds_without_error():
    """Each preset should build a valid atmosphere grid."""
    for pk in EnvironmentPresets.get_preset_keys():
        preset = EnvironmentPresets.get_preset(pk)
        atm = AtmosphereGrid(8, 4, 4, 200.0)
        atm.applyThermalProfile(preset["thermalProfile"], {
            "baseTemp": preset["baseTemp"],
            "deltaT": preset["deltaT"],
            "humidity": preset["humidity"],
            "windSpeed": preset["windSpeed"],
            "Cn2Level": preset["Cn2Level"],
            "wavelengthUm": 1.55,
            "useEdlen": True
        })
        assert atm.grid is not None

# ── Comprehensive Transmission Test (T15) ────────────────────

def test_transmission_rates_in_valid_range():
    """T15: All transmission success rates must be between 0% and 100%."""
    results = ComprehensiveTransmissionTest.run_all({
        "gridX": 8, "gridY": 4, "gridZ": 4, "cellSize": 750.0,
        "numBits": 8, "pattern": "alternating"
    })
    assert len(results) > 0
    for r in results:
        assert 0.0 <= r["successRate"] <= 100.0, \
            f"successRate {r['successRate']} out of range for {r['environment']}/{r['lens']}"

def test_transmission_correct_number_of_combinations():
    """Result matrix must cover all env × lens combinations."""
    results = ComprehensiveTransmissionTest.run_all({
        "gridX": 8, "gridY": 4, "gridZ": 4, "cellSize": 750.0,
        "numBits": 4, "pattern": "alternating"
    })
    expected = len(EnvironmentPresets.get_preset_keys()) * len(LensSystem.LENS_TYPES)
    assert len(results) == expected, f"Expected {expected} combinations, got {len(results)}"

def test_transmission_cassegrain_beats_no_lens_clear_sky():
    """In clear sky (mountain), Cassegrain should outperform no lens."""
    results = ComprehensiveTransmissionTest.run_all({
        "gridX": 8, "gridY": 4, "gridZ": 4, "cellSize": 750.0,
        "numBits": 16, "pattern": "all_ones"
    })
    mountain_results = {r["lens"]: r["successRate"] for r in results if r["environment"] == "mountain"}
    if "cassegrain" in mountain_results and "none" in mountain_results:
        assert mountain_results["cassegrain"] >= mountain_results["none"], \
            "Cassegrain should be >= no lens in clear mountain conditions"

def test_transmission_industrial_below_mountain():
    """Industrial (fog) should have lower or equal success rate than mountain (clear) for same lens."""
    results = ComprehensiveTransmissionTest.run_all({
        "gridX": 8, "gridY": 4, "gridZ": 4, "cellSize": 750.0,
        "numBits": 16, "pattern": "alternating"
    })
    by_env_lens = {(r["environment"], r["lens"]): r["successRate"] for r in results}
    for lens_key in LensSystem.LENS_TYPES:
        ind_rate = by_env_lens.get(("industrial", lens_key), 100.0)
        mtn_rate = by_env_lens.get(("mountain", lens_key), 0.0)
        assert ind_rate <= mtn_rate + 10.0, \
            f"Industrial ({ind_rate}%) should not exceed mountain ({mtn_rate}%) by >10% for lens={lens_key}"

def test_transmission_result_has_required_fields():
    """Each result dict must have all required fields."""
    results = ComprehensiveTransmissionTest.run_all({
        "gridX": 6, "gridY": 4, "gridZ": 4, "cellSize": 1000.0,
        "numBits": 4, "pattern": "alternating"
    })
    required = ["environment", "envName", "lens", "lensName", "successRate",
                "correctBits", "totalBits", "lensGain_dB", "weather", "Cn2Level"]
    for r in results:
        for field in required:
            assert field in r, f"Result missing field '{field}'"
