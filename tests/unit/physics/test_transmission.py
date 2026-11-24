import binascii
import math
import random
import zlib

import pytest

from backend.engine import (
    AtmosphericLoss,
    DataTransmissionSimulator,
    EnvironmentPresets,
    LensSystem,
    Vec3,
    crc16_ccitt,
    crc32,
    validate_crc,
    validate_crc32,
)


def test_legacy_crc16_still_available_and_unchanged():
    payload = b"OPTICAL-FSO-CRC"
    checksum = crc16_ccitt(payload)

    assert checksum <= 0xFFFF and crc16_ccitt(b"123456789") == 0x29B1
    assert validate_crc(payload, checksum)
    assert not validate_crc(payload, checksum ^ 0x1234)


def test_crc32_check_value_and_round_trip():
    assert crc32(b"123456789") == 0xCBF43926
    assert crc32(b"") == 0
    payload = b"OPTICAL-FSO-CRC"
    checksum = crc32(payload)
    assert validate_crc32(payload, checksum)
    assert not validate_crc32(payload, checksum ^ 0x12345678)


def test_crc32_matches_zlib_random_and_turkish_utf8():
    rng = random.Random(7)
    for n in (1, 2, 7, 64, 1000):
        data = bytes(rng.randrange(256) for _ in range(n))
        assert crc32(data) == zlib.crc32(data) == binascii.crc32(data)
    text = "Işık, şimşek ve ğüzel çöl — İstanbul'da optik bağlantı"
    assert crc32(text) == zlib.crc32(text.encode("utf-8"))


def test_crc32_detects_every_single_bit_flip():
    data = bytes(range(40))
    ref = crc32(data)
    for i in range(len(data) * 8):
        bad = bytearray(data)
        bad[i // 8] ^= 1 << (i % 8)
        assert crc32(bytes(bad)) != ref


def test_crc32_detects_all_bursts_up_to_32_bits():
    rng = random.Random(11)
    data = bytes(rng.randrange(256) for _ in range(48))
    ref = crc32(data)
    nbits = len(data) * 8
    for _ in range(3000):
        length = rng.randint(1, 32)
        start = rng.randrange(nbits - length + 1)
        pattern = (rng.getrandbits(length) | 1 | (1 << (length - 1))) if length > 1 else 1
        mask = pattern << (nbits - start - length)   # burst: first and last bit of the span flipped
        bad = (int.from_bytes(data, "big") ^ mask).to_bytes(len(data), "big")
        assert crc32(bad) != ref


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


def test_zero_bits_fail_when_link_is_unavailable():
    trace = {"success": False, "exitPosition": None, "avgCn2": 0.0}
    link_budget = {"P_rx_dBm": -200.0, "linkViable": False}
    simulator = DataTransmissionSimulator(
        None, Vec3(), Vec3(10.0, 0.0, 0.0), 1.0,
        trace=trace, linkBudget=link_budget,
    )

    result = simulator.sendPacket([0, 0])

    assert result["correctBits"] == 0
    assert result["bitErrorRate"] == 1.0
    assert result["successRate"] == 0.0
    assert result["missedZeros"] == 2
    assert all(bit["linkBudget"] == link_budget for bit in result["results"])
    assert all(bit["lateralDeviation"] is None for bit in result["results"])


def test_zero_bits_are_correct_when_link_is_available():
    trace = {
        "success": True,
        "exitPosition": Vec3(10.0, 0.0, 0.0),
        "avgCn2": 1e-15,
    }
    link_budget = {"P_rx_dBm": -20.0, "linkViable": True}
    simulator = DataTransmissionSimulator(
        None, Vec3(), Vec3(10.0, 0.0, 0.0), 1.0,
        trace=trace, linkBudget=link_budget,
    )

    result = simulator.sendPacket([0])

    assert result["correctBits"] == 1
    assert result["results"][0]["received"] == 0
    assert result["results"][0]["correct"] is True


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


def test_lens_system_enforces_diffraction_limit_etendue_and_custom_aperture():
    lam = 1550e-9
    for lens_key in LensSystem.LENS_TYPES:
        lens = LensSystem(lens_key)
        beam = lens.apply_to_beam({
            "divergenceRad": 1e-5,
            "beamDiamM": 0.004,
            "receiverDiamM": 0.1,
            "wavelengthNm": 1550.0,
        })
        theta_diff = lam / (math.pi * (beam["beamDiamM"] / 2.0))
        assert beam["divergenceRad"] >= theta_diff - 1e-15
        if LensSystem.LENS_TYPES[lens_key]["divergenceReduction"] > 0:
            assert beam["beamDiamM"] > 0.004

    custom = LensSystem("collimating", custom_aperture_m=0.04)
    custom_beam = custom.apply_to_beam({"divergenceRad": 1e-3, "beamDiamM": 0.004, "receiverDiamM": 0.1})
    assert custom_beam["receiverDiamM"] == pytest.approx(0.04)


def test_ascii_hello_respects_requested_length():
    for length in (8, 40, 50):
        bits = DataTransmissionSimulator.generateTestData("ascii_hello", length)
        assert len(bits) == length
        assert all(b in (0, 1) for b in bits)
