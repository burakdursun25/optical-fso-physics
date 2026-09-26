import math

from backend.engine import (
    AtmosphericLoss,
    DataTransmissionSimulator,
    EnvironmentPresets,
    LensSystem,
    Vec3,
    crc16_ccitt,
    validate_crc,
)


def test_crc16_matches_ccitt_false_round_trip():
    payload = b"OPTICAL-FSO-CRC"
    checksum = crc16_ccitt(payload)

    assert validate_crc(payload, checksum)
    assert not validate_crc(payload, checksum ^ 0x1234)


def test_lens_system_improves_collimation_and_aperture():
    lens = LensSystem("collimating")
    result = lens.apply_to_beam({"divergenceRad": 1e-3, "beamDiamM": 0.004, "receiverDiamM": 0.1})

    assert result["divergenceRad"] < 1e-3
    assert result["receiverDiamM"] > 0.1
    assert 0.0 < result["transmittance"] <= 1.0


def test_cassegrain_gain_exceeds_unassisted_link():
    none_gain = LensSystem("none").get_link_budget_gain_dB(6000.0)
    cassegrain_gain = LensSystem("cassegrain").get_link_budget_gain_dB(6000.0)

    assert cassegrain_gain > none_gain


def test_diverging_lens_increases_divergence():
    result = LensSystem("diverging").apply_to_beam({"divergenceRad": 1e-3, "beamDiamM": 0.004, "receiverDiamM": 0.1})

    assert result["divergenceRad"] > 1e-3


def test_collimating_gain_is_better_than_no_lens():
    none_gain = LensSystem("none").get_link_budget_gain_dB(6000.0)
    collimating_gain = LensSystem("collimating").get_link_budget_gain_dB(6000.0)

    assert collimating_gain > none_gain


def test_environment_presets_are_complete_and_ordered():
    required = {"thermalProfile", "weather", "Cn2Level", "baseTemp", "deltaT", "windSpeed", "humidity"}

    for key in EnvironmentPresets.get_preset_keys():
        assert required.issubset(EnvironmentPresets.get_preset(key))
    assert EnvironmentPresets.get_preset("desert")["baseTemp"] > EnvironmentPresets.get_preset("arctic")["baseTemp"]
    assert EnvironmentPresets.get_preset("industrial")["weather"] == "fog"


def test_packet_reports_success_for_repeated_one_bits():
    class SuccessfulTracer:
        def trace(self, origin, direction):
            return {
                "avgCn2": 1e-15,
                "exitPosition": Vec3(10.0, 0.0, 0.0),
                "path": [],
                "refractionEvents": [],
                "success": True,
                "totalDistance": 10.0,
                "totalInternalReflections": 0,
                "steps": 1,
                "exitDirection": direction,
            }

    tracer = SuccessfulTracer()
    simulator = DataTransmissionSimulator(tracer, Vec3(), Vec3(10.0, 0.0, 0.0), 1.0)

    result = simulator.sendPacket([1, 1, 1, 1])

    assert result["totalBits"] == 4
    assert result["correctBits"] == 4
    assert result["successRate"] == 100.0
    assert all(bit["sent"] == bit["received"] == 1 for bit in result["results"])


def test_packet_uses_supplied_trace_and_link_budget():
    trace = {
        "avgCn2": 1e-15,
        "exitPosition": Vec3(10.0, 0.0, 0.0),
        "path": [],
        "refractionEvents": [],
        "success": True,
        "totalDistance": 10.0,
        "totalInternalReflections": 0,
        "steps": 1,
        "exitDirection": Vec3(1.0, 0.0, 0.0),
    }
    link_budget = {"P_rx_dBm": -20.0, "linkViable": True}

    simulator = DataTransmissionSimulator(None, Vec3(), Vec3(10.0, 0.0, 0.0), 1.0, trace=trace, linkBudget=link_budget)
    result = simulator.sendPacket([1])

    assert result["correctBits"] == 1
    assert result["results"][0]["trace"]["success"] is True
    assert result["results"][0]["linkBudget"]["P_rx_dBm"] == -20.0
    assert result["results"][0]["linkBudget"]["linkViable"] is True


def test_comprehensive_transmission_covers_every_environment_and_lens():
    from backend.engine import ComprehensiveTransmissionTest

    results = ComprehensiveTransmissionTest.run_all({
        "gridX": 6,
        "gridY": 4,
        "gridZ": 4,
        "cellSize": 1000.0,
        "numBits": 4,
        "pattern": "alternating",
    })

    assert len(results) == len(EnvironmentPresets.get_preset_keys()) * len(LensSystem.LENS_TYPES)
    assert all(0.0 <= item["successRate"] <= 100.0 for item in results)
