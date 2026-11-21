import json
import math
import random

import pytest

from backend import bitcheck as bc
from backend.engine import crc16_ccitt


def test_bits_bytes_roundtrip_msb_first():
    assert bc.bytes_to_bits(b"\x80\x01") == [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]
    data = bytes(range(256))
    assert bc.bits_to_bytes(bc.bytes_to_bits(data)) == data
    assert bc.bits_to_bytes(bc.bytes_to_bits("héllo")) == "héllo".encode("utf-8")
    with pytest.raises(ValueError):
        bc.bits_to_bytes([1, 0, 1])


def test_frame_build_parse_roundtrip():
    frame = bc.build_frame(b"FSO link")
    assert len(frame) == (bc.HEADER_BYTES + 8 + 2) * 8
    assert bc.bits_to_bytes(frame[:16]) == bc.PREAMBLE
    parsed = bc.parse_frame(frame)
    assert parsed["crc_ok"] and parsed["sync_ok"]
    assert parsed["payload"] == b"FSO link"
    assert parsed["errors"] == []
    assert bc.parse_frame(bc.build_frame(b""))["payload"] == b""


def test_crc_detects_every_single_bit_flip_in_payload_region():
    frame = bc.build_frame(b"hello")
    start, end = (bc.HEADER_BYTES) * 8, len(frame)
    for i in range(start, end):
        bad = list(frame)
        bad[i] ^= 1
        parsed = bc.parse_frame(bad)
        assert not parsed["crc_ok"], f"flip at {i} undetected"
        assert "CRC mismatch" in parsed["errors"]


def test_parse_reports_truncation_and_sync_errors():
    frame = bc.build_frame(b"abc")
    assert not bc.parse_frame(frame[:-9])["crc_ok"]
    assert not bc.parse_frame(frame[:10])["crc_ok"]
    bad = list(frame)
    bad[20] ^= 1  # inside sync word
    p = bc.parse_frame(bad)
    assert not p["sync_ok"] and p["sync_bit_errors"] == 1 and not p["crc_ok"]


def test_frame_crc_uses_engine_crc16():
    frame = bc.build_frame(b"xyz")
    crc = bc.bits_to_bytes(frame[-16:])
    assert int.from_bytes(crc, "big") == crc16_ccitt(b"\x00\x03xyz")


def test_compare_bits_hand_made_case():
    sent = [0, 1, 1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 0]
    recv = [0, 1, 0, 1, 1, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 1]
    # errors at 2 (1->0), 3 (0->1), 4 (0->1), 15 (0->1)
    r = bc.compare_bits(sent, recv)
    assert r["total"] == 16 and r["errors"] == 4
    assert r["ber"] == 0.25
    assert r["error_positions"] == [2, 3, 4, 15]
    assert r["flips_0_to_1"] == 3 and r["flips_1_to_0"] == 1
    assert r["longest_burst"] == 3 and r["burst_start"] == 2
    assert r["byte_errors"] == [3, 1] and r["bytes_with_errors"] == 2
    json.dumps(r)


def test_compare_identical_and_empty():
    r = bc.compare_bits([1, 0, 1], [1, 0, 1])
    assert r["errors"] == 0 and r["longest_burst"] == 0 and r["burst_start"] is None
    assert bc.compare_bits([], [])["ber"] == 0.0


def test_erasures_and_short_receive_count_as_errors():
    r = bc.compare_bits([0, 0, 0, 0], [0, None, 0])
    assert r["errors"] == 2 and r["erasures"] == 2
    assert r["error_positions"] == [1, 3]
    assert r["flips_0_to_1"] == 0
    # all-zero sent, all-erased received must NOT look correct
    assert bc.compare_bits([0] * 8, [None] * 8)["ber"] == 1.0


def test_erased_frame_never_passes_crc():
    frame = bc.build_frame(b"")
    assert not bc.parse_frame([None] * len(frame))["crc_ok"]
    assert not bc.parse_frame([0] * len(frame))["crc_ok"]


def test_ook_ber_formula():
    assert bc.ook_ber(0.0) == pytest.approx(0.5)
    assert bc.ook_ber(144.0) == pytest.approx(0.5 * math.erfc(6 / math.sqrt(2)), rel=1e-12)  # Q(6)
    assert bc.ook_ber(144.0) < 1e-8          # Q(6) ~ 1e-9
    assert bc.ook_ber(16.0) > bc.ook_ber(36.0)


def test_channel_ber_zero_is_exact_and_half_flips_half():
    bits = [random.Random(1).randint(0, 1) for _ in range(4000)]
    assert bc.apply_channel(bits, 0.0, seed=3) == bits
    out = bc.apply_channel(bits, 0.5, seed=3)
    flips = sum(a != b for a, b in zip(bits, out))
    assert 0.45 * len(bits) < flips < 0.55 * len(bits)
    assert bc.apply_channel(bits, 1.0, seed=3) == [b ^ 1 for b in bits]
    with pytest.raises(ValueError):
        bc.apply_channel(bits, 1.5)


def test_channel_deterministic_with_seed():
    bits = [0, 1] * 500
    assert bc.apply_channel(bits, 0.1, 7) == bc.apply_channel(bits, 0.1, 7)
    assert bc.apply_channel(bits, 0.1, 7) != bc.apply_channel(bits, 0.1, 8)


def test_ber_non_decreasing_with_distance():
    for preset in ("none", "collimating", "keplerian_telescope"):
        bers = [bc.link_ber(preset, d) for d in (100, 1000, 2000, 3000, 4000, 6000, 10000, 20000)]
        assert all(b2 >= b1 for b1, b2 in zip(bers, bers[1:])), (preset, bers)
        assert bers[0] < 1e-9 and bers[-1] > 0.1


def test_run_link_check_clean_link_is_exact():
    rep = bc.run_link_check("Merhaba FSO", 100.0, "none", seed=1)
    assert rep["comparison"]["errors"] == 0
    assert rep["frame"]["crc_ok"] and rep["payload_intact"]
    assert rep["received_bits"] == rep["sent_bits"]
    json.dumps(rep)


def test_run_link_check_marginal_link_is_deterministic_and_flags_errors():
    kw = dict(payload="A" * 64, distance_m=6000.0, preset="none", seed=5)
    a, b = bc.run_link_check(**kw), bc.run_link_check(**kw)
    assert a == b
    assert a["comparison"]["errors"] > 0
    assert not a["frame"]["crc_ok"] and not a["payload_intact"]
    assert a["comparison"]["errors"] == len(a["comparison"]["error_positions"])


def test_dropped_channel_counts_all_bits_lost():
    rep = bc.run_link_check("\x00\x00", 100.0, "none", seed=0, sensitivity_dBm=100.0)
    assert rep["link"]["channel_dropped"]
    c = rep["comparison"]
    assert c["errors"] == c["total"] == c["erasures"]
    assert not rep["frame"]["crc_ok"] and set(rep["received_bits"]) == {"?"}


def test_lens_improves_link_over_bare_beam():
    assert bc.link_ber("keplerian_telescope", 6000) < bc.link_ber("none", 6000)
