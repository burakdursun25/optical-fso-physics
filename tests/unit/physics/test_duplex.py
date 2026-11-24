import json
import math

import pytest

from backend.duplex import AB, BA, Terminal, link_budget, run_duplex


def sym(**kw):
    return Terminal("A", wavelength_nm=1550.0, **kw), Terminal("B", wavelength_nm=1550.0, **kw)


def test_symmetric_configuration_gives_symmetric_links_and_similar_arq():
    a, b = sym()
    r = run_duplex(a, b, "a" * 160, "b" * 160, distance_m=2000, ber_override=2e-3, max_retries=15, seed=1)
    d = r["directions"]
    assert r["summary"]["asymmetry_dB"] == pytest.approx(0.0, abs=1e-9)
    assert d[AB]["link_budget"]["p_rx_dBm"] == pytest.approx(d[BA]["link_budget"]["p_rx_dBm"])
    assert d[AB]["ber"] == pytest.approx(d[BA]["ber"])
    assert r["summary"]["both_delivered"]
    ta, tb = (d[k]["arq_result"]["total_transmissions"] for k in (AB, BA))
    assert abs(ta - tb) <= 0.35 * max(ta, tb)
    assert ta > d[AB]["arq_result"]["n_segments"]        # retries really happened


def test_wavelength_asymmetry_sign_1310_vs_1550_in_clear_air():
    r = run_duplex(distance_m=3000, seed=0)          # A 1550 nm, B 1310 nm, identical hardware
    d = r["directions"]
    # shorter wavelength -> narrower diffraction beam -> more power captured: B->A is stronger
    assert d[BA]["link_budget"]["p_rx_dBm"] > d[AB]["link_budget"]["p_rx_dBm"]
    assert r["summary"]["asymmetry_dB"] < 0
    assert r["summary"]["asymmetry_dB"] == pytest.approx(
        d[AB]["link_budget"]["p_rx_dBm"] - d[BA]["link_budget"]["p_rx_dBm"])
    assert d[AB]["link_budget"]["wavelength_nm"] == 1550 and d[BA]["link_budget"]["wavelength_nm"] == 1310
    # but 1310 scintillates more (sigma_R^2 ~ lambda^-7/6)
    assert d[BA]["link_budget"]["sigma_I2"] > d[AB]["link_budget"]["sigma_I2"]


def test_haze_hits_shorter_wavelength_harder_in_the_engine_model():
    r = run_duplex(distance_m=1000, weather="haze")
    d = r["directions"]
    assert d[BA]["link_budget"]["atmos_alpha_dB_km"] > d[AB]["link_budget"]["atmos_alpha_dB_km"]


def test_lower_crosstalk_isolation_worsens_ber_and_costs_retransmissions():
    good_a, good_b = Terminal("A", 10, 1550, filter_isolation_dB=40), Terminal("B", 10, 1310, filter_isolation_dB=40)
    bad_a, bad_b = Terminal("A", 10, 1550, filter_isolation_dB=5), Terminal("B", 10, 1310, filter_isolation_dB=5)
    good = run_duplex(good_a, good_b, "a" * 120, "b" * 120, distance_m=2000, seed=3, max_retries=20)
    bad = run_duplex(bad_a, bad_b, "a" * 120, "b" * 120, distance_m=2000, seed=3, max_retries=20)
    for k in (AB, BA):
        assert bad["directions"][k]["ber"] > 100 * good["directions"][k]["ber"]
        assert bad["directions"][k]["link_budget"]["sir_dB"] < good["directions"][k]["link_budget"]["sir_dB"]
        assert (bad["directions"][k]["arq_result"]["total_transmissions"]
                > good["directions"][k]["arq_result"]["total_transmissions"])


def test_timeline_has_both_terminals_transmitting_in_every_slot_with_piggybacked_feedback():
    a, b = sym()
    r = run_duplex(a, b, "a" * 80, "b" * 24, distance_m=2000, ber_override=3e-3, max_retries=15, seed=2)
    tl = r["timeline"]
    assert len(tl) == r["summary"]["total_slots"] > 3
    for i, e in enumerate(tl):
        assert e["slot"] == i
        assert e["a_tx"]["type"] in ("DATA", "RETX", "ACK") and e["b_tx"]["type"] in ("DATA", "RETX", "ACK")
        assert isinstance(e["a_rx_ok"], bool) and isinstance(e["b_rx_ok"], bool)
    assert tl[0]["a_tx"]["type"] == tl[0]["b_tx"]["type"] == "DATA"
    assert any(e["a_tx"]["type"] in ("DATA", "RETX") and e["a_tx"]["fb"] in ("ACK", "NACK") for e in tl)
    # B has fewer segments, so it falls back to feedback-only frames while A is still sending
    assert any(e["b_tx"]["type"] == "ACK" and e["a_tx"]["type"] in ("DATA", "RETX") for e in tl)
    assert tl[0]["a_tx"]["fb"] == "NONE"


def test_clean_link_delivers_both_payloads_exactly_and_reports_rates():
    r = run_duplex(distance_m=2000, bitrate_bps=1e9, seed=0)
    s, d = r["summary"], r["directions"]
    assert s["both_delivered"] and not s["timed_out"]
    assert d[AB]["arq_result"]["delivered_payload"] == "Terminal A -> B: merhaba FSO!"
    assert d[BA]["arq_result"]["delivered_payload"] == "Terminal B -> A: selam!"
    assert s["total_time_s"] == pytest.approx(s["total_slots"] * (8 + 9) * 8 / 1e9)
    for k in (AB, BA):
        assert 0 < d[k]["goodput_bps"] <= d[k]["throughput_bps"] <= 1e9
        assert d[k]["arq_result"]["piggybacked_feedback"] is True
    json.dumps(r)


def test_hopeless_link_reports_failure_honestly():
    r = run_duplex(distance_m=2000, ber_override=0.3, max_retries=0, seed=1)
    assert not r["summary"]["both_delivered"]
    for k in (AB, BA):
        assert r["directions"][k]["arq_result"]["residual_errors"]["failed_segments"] > 0
    assert r["summary"]["total_slots"] >= 4


def test_seeded_determinism():
    kw = dict(distance_m=2000, ber_override=3e-3, max_retries=10)
    assert run_duplex(seed=5, **kw) == run_duplex(seed=5, **kw)
    assert run_duplex(seed=5, **kw)["timeline"] != run_duplex(seed=6, **kw)["timeline"]


def test_lost_piggybacked_feedback_triggers_spurious_retransmissions():
    a, b = sym()
    r = run_duplex(a, b, "a" * 400, "b" * 400, distance_m=2000, ber_override=4e-3, max_retries=30, seed=9)
    assert sum(d["arq_result"]["spurious_retransmissions"] for d in r["directions"].values()) > 0
    assert r["summary"]["both_delivered"]


def test_lens_roles_are_validated_and_receive_lens_adds_capture_area():
    with pytest.raises(ValueError):
        link_budget(Terminal(tx_lens="focusing"), Terminal(), 1000)
    with pytest.raises(ValueError):
        link_budget(Terminal(), Terminal(rx_lens="galilean_telescope"), 1000)
    plain = link_budget(Terminal(), Terminal(), 4000)["budget"]
    rx = link_budget(Terminal(), Terminal(rx_lens="focusing"), 4000)["budget"]
    assert rx["optics_loss_dB"] < plain["optics_loss_dB"]       # 5 cm aperture vs 2.5 cm


@pytest.mark.parametrize("kw", [{"reciprocity_rho": 1.5}, {"bitrate_bps": 0}, {"max_retries": 201},
                                {"distance_m": 0}, {"fading_rho": 1.0}])
def test_invalid_parameters_rejected(kw):
    with pytest.raises(ValueError):
        run_duplex(**kw)


def test_duplex_margin_is_negative_when_ber_exceeds_noise_floor():
    res = link_budget(Terminal("A", tx_power_dBm=-10.0), Terminal("B", sensitivity_dBm=-40.0, noise_rms_dBm=-30.0), 1500.0)
    b = res["budget"]

    assert b["ber_faded_mean"] > 0.01
    assert b["viable"] is False
    assert b["margin_dB"] < 0.0
