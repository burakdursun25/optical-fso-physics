import json
import math

import pytest

from backend import arq
from backend.arq import ArqChannel, run_arq
from backend.engine import AtmosphereGrid
from backend.propagation3d import propagate_beam_3d

TEXT = "Merhaba FSO! Bu bir seçici tekrar (selective repeat) ARQ testidir. " * 3   # ~ 200 bytes


def test_zero_ber_means_zero_retries_and_perfect_delivery():
    r = run_arq(TEXT, segment_bytes=8, ber=0.0)
    n = r["n_segments"]
    assert r["total_transmissions"] == n and r["retransmissions"] == 0
    assert all(s["retries"] == 0 and s["final_ok"] and s["attempts"] == 1 for s in r["segments"])
    assert r["delivered_ok"] and r["delivered_payload"] == TEXT
    assert r["residual_errors"] == {"failed_segments": 0, "lost_bytes": 0, "undetected_segments": 0,
                                    "bit_errors": 0}
    assert r["efficiency"] == 1.0
    assert r["goodput_ratio"] == pytest.approx(len(TEXT.encode()) / (len(TEXT.encode()) + 5 * n))
    assert r["retries_histogram"] == {"0": n} and set(r["retries_per_region"].values()) == {0}


def test_moderate_ber_needs_retries_but_delivers_with_enough_budget():
    r = run_arq(TEXT, segment_bytes=8, max_retries=20, ber=3e-3, seed=2)
    assert r["retransmissions"] > 0 and sum(r["retries_per_region"].values()) == r["retransmissions"]
    assert r["delivered_ok"] and r["delivered_payload"] == TEXT
    assert r["efficiency"] < 1.0
    failed = sum(1 for s in r["segments"] for c in s["crc_history"] if not c)
    assert failed >= r["retransmissions"] - r["spurious_retransmissions"]
    for s in r["segments"]:
        assert len(s["crc_history"]) == len(s["ber_history"]) == len(s["error_bits_history"]) == s["attempts"]
        assert s["crc_history"][-1] or not s["final_ok"] or any(s["crc_history"])


def test_no_retries_with_high_ber_reports_residual_errors_honestly():
    r = run_arq(TEXT, segment_bytes=8, max_retries=0, ber=0.05, seed=1)
    assert r["total_transmissions"] == r["n_segments"] and r["retransmissions"] == 0
    assert not r["delivered_ok"]
    res = r["residual_errors"]
    assert res["failed_segments"] > 0 and res["lost_bytes"] > 0 and res["bit_errors"] > 0
    assert res["failed_segments"] == sum(1 for s in r["segments"] if not s["final_ok"])


def test_only_bad_segments_are_resent():
    r = run_arq(TEXT, segment_bytes=8, max_retries=6, ber=4e-3, seed=7)
    rounds = r["rounds"]
    assert len(rounds) > 1
    first = dict(zip(rounds[0]["sent"], rounds[0]["crc_ok"]))
    assert not all(first.values()) and any(first.values())
    assert rounds[1]["sent"] == sorted(i for i, ok in first.items() if not ok)
    for prev, nxt in zip(rounds, rounds[1:]):
        bad = {i for i, ok in zip(prev["sent"], prev["crc_ok"]) if not ok}
        assert set(nxt["sent"]) == bad          # perfect reverse channel: exactly the NACKed ones


def test_seeded_determinism_and_seed_sensitivity():
    kw = dict(segment_bytes=8, max_retries=8, ber=3e-3, ack_ber=0.01)
    a, b, c = run_arq(TEXT, seed=4, **kw), run_arq(TEXT, seed=4, **kw), run_arq(TEXT, seed=5, **kw)
    assert a == b
    assert a["retries_per_region"] != c["retries_per_region"]


def test_lost_acks_cause_counted_spurious_retransmissions():
    big = "x" * 800
    clean = run_arq(big, segment_bytes=8, ber=0.0, ack_ber=0.0)
    assert clean["lost_acks"] == 0 and clean["spurious_retransmissions"] == 0
    r = run_arq(big, segment_bytes=8, max_retries=10, ber=0.0, ack_ber=0.05, seed=1)
    assert r["lost_acks"] > 0 and r["spurious_retransmissions"] > 0
    assert r["delivered_ok"]
    # forward channel is perfect, so EVERY retransmission is spurious (data already held)
    assert r["spurious_retransmissions"] == r["retransmissions"] > 0
    assert sum(s["spurious_retransmissions"] for s in r["segments"]) == r["spurious_retransmissions"]


def test_fading_gives_later_attempts_a_different_channel_state():
    ch = ArqChannel(p_rx_dBm=-23.0, noise_rms_dBm=-30.0, sigma_I2=1.0, fading_rho=0.0)
    r = run_arq(TEXT, segment_bytes=8, max_retries=30, channel=ch, seed=3)
    assert r["delivered_ok"] and r["retransmissions"] > 0
    multi = [s for s in r["segments"] if s["attempts"] > 1]
    assert multi and any(len(set(s["ber_history"])) > 1 for s in multi)
    assert multi[0]["crc_history"][-1] is True or not multi[0]["final_ok"]


def test_stronger_link_needs_fewer_retransmissions():
    weak = run_arq(TEXT, max_retries=30, channel=ArqChannel(p_rx_dBm=-24.0, sigma_I2=0.3), seed=6)
    strong = run_arq(TEXT, max_retries=30, channel=ArqChannel(p_rx_dBm=-16.0, sigma_I2=0.3), seed=6)
    assert strong["retransmissions"] < weak["retransmissions"]


def test_segment_regions_and_byte_ranges():
    r = run_arq("A" * 30, segment_bytes=8, ber=0.0)
    assert r["n_segments"] == 4
    assert r["segments"][3]["region_tr"] == "Segment 3 (bayt 24–29)"
    assert [s["bytes_range"] for s in r["segments"]] == [[0, 7], [8, 15], [16, 23], [24, 29]]
    assert r["segments"][1]["region_tr"] == "Segment 1 (bayt 8–15)"


def test_non_utf8_payload_is_hex_encoded_and_binary_safe():
    data = bytes(range(200, 230))
    r = run_arq(data, segment_bytes=10, ber=0.0)
    assert r["delivered_encoding"] == "hex" and r["delivered_payload"] == data.hex() and r["delivered_ok"]


@pytest.mark.parametrize("kw", [
    {"payload": ""}, {"segment_bytes": 0}, {"segment_bytes": 300}, {"max_retries": -1}, {"max_retries": 201},
    {"ack_ber": 0.9}, {"ber": 1.5}, {"payload": "x" * 5000}, {"payload": "x" * 2000, "segment_bytes": 2},
])
def test_invalid_arguments_rejected(kw):
    args = {"payload": "hello", "segment_bytes": 4, "max_retries": 2, "ber": 0.0}
    args.update(kw)
    with pytest.raises(ValueError):
        run_arq(**args)
    with pytest.raises(ValueError):
        run_arq("hello", ber=None)


def test_framing_roundtrip_and_crc_detects_single_bit_flip():
    bits = arq.frame_segment(7, b"abc")
    p = arq.parse_segment(bits)
    assert p == {"ok": True, "seq": 7, "data": b"abc"}
    for i in range(len(bits)):
        flipped = list(bits)
        flipped[i] ^= 1
        assert not arq.parse_segment(flipped)["ok"]
    ack = arq.frame_ack(9, arq.ACK)
    assert arq.parse_ack(ack)["ok"] and not arq.parse_ack([ack[0] ^ 1] + ack[1:])["ok"]


def _fog_propagation():
    g = AtmosphereGrid(30, 6, 6, 100.0)
    g.grid[:] = 1.000293
    g.Cn2[:] = 1e-16
    block = {"center": [2000, 300, 300], "size": [400, 200, 200], "extra_alpha_dB_km": 60}
    return propagate_beam_3d(g, (0, 300, 300), (3000, 300, 300), wavelength=1550e-9, anomalies=[block])


def test_spatial_region_view_points_at_the_degraded_part_of_the_link():
    prop = _fog_propagation()
    r = run_arq(TEXT * 3, segment_bytes=8, max_retries=30, ber=0.01, seed=2, propagation=prop)
    reg = r["retries_by_spatial_region"]
    assert len(reg) == 10 and sum(x["share"] for x in reg) == pytest.approx(1.0, abs=1e-4)
    failed = sum(1 for s in r["segments"] for c in s["crc_history"] if not c)
    assert sum(x["sampled_retries"] for x in reg) == failed
    assert sum(x["expected_retries"] for x in reg) == pytest.approx(failed, abs=0.2)
    worst = max(reg, key=lambda x: x["share"])
    assert worst["s_start"] <= 2200 and worst["s_end"] >= 1800      # bin overlapping the fog block
    assert worst["share"] > 1.3 * min(x["share"] for x in reg)
    assert "retries_by_spatial_region" not in run_arq(TEXT, ber=0.0)
    json.dumps(r)


def test_channel_builders_follow_the_link_physics():
    near = arq.channel_from_link("none", 500.0)
    far = arq.channel_from_link("none", 5000.0)
    assert far.p_rx_dBm < near.p_rx_dBm and far.sigma_I2 > near.sigma_I2
    assert far.mean_ber() >= near.mean_ber()
    ch = arq.channel_from_propagation(_fog_propagation(), tx_power_dBm=10.0)
    assert ch.p_rx_dBm < 10.0 and ch.sigma_I2 > 0
