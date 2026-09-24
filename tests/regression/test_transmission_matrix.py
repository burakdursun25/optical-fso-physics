from backend.engine import ComprehensiveTransmissionTest, EnvironmentPresets, LensSystem


def matrix_results():
    return ComprehensiveTransmissionTest.run_all({
        "gridX": 8,
        "gridY": 4,
        "gridZ": 4,
        "cellSize": 750.0,
        "numBits": 16,
        "pattern": "alternating",
    })


def test_transmission_matrix_has_required_fields():
    required = {
        "environment", "envName", "lens", "lensName", "successRate",
        "correctBits", "totalBits", "lensGain_dB", "weather", "Cn2Level",
    }

    for result in matrix_results():
        assert required.issubset(result)
        assert 0.0 <= result["successRate"] <= 100.0


def test_transmission_matrix_has_expected_dimensions():
    results = matrix_results()

    assert len(results) == len(EnvironmentPresets.get_preset_keys()) * len(LensSystem.LENS_TYPES)


def test_clear_mountain_cassegrain_is_not_worse_than_no_lens():
    results = matrix_results()
    mountain = {result["lens"]: result["successRate"] for result in results if result["environment"] == "mountain"}

    assert mountain["cassegrain"] >= mountain["none"]


def test_industrial_conditions_do_not_beat_mountain_by_large_margin():
    results = matrix_results()
    by_env_lens = {(result["environment"], result["lens"]): result["successRate"] for result in results}

    for lens_key in LensSystem.LENS_TYPES:
        industrial = by_env_lens[("industrial", lens_key)]
        mountain = by_env_lens[("mountain", lens_key)]
        assert industrial <= mountain + 10.0
