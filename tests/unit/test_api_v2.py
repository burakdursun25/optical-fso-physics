import pytest
from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)
SMALL_ATM = {"gridX": 20, "gridY": 4, "gridZ": 4, "cellSize": 100.0, "thermalProfile": "uniform"}


def prop_body(**over):
    body = {"atmosphere": dict(SMALL_ATM), "nSamples": 60, "nRays": 8}
    body.update(over)
    return body


# ----------------------------------------------------------------- /api/propagation3d
def test_propagation3d_valid_request_contract():
    r = client.post("/api/propagation3d", json=prop_body())
    assert r.status_code == 200
    b = r.json()
    assert set(b) >= {"centerline", "rings", "degradation_cloud", "summary", "bounds", "meta"}
    assert len(b["centerline"]) == 61
    c0 = b["centerline"][0]
    assert set(c0) >= {"s", "x", "y", "z", "w", "w_lt", "wander", "power_frac", "atten_dB", "scint_index"}
    assert all(len(ring) == 8 and len(ring[0]) == 3 for ring in b["rings"])
    cloud = b["degradation_cloud"]
    assert set(cloud["hotspot"]) >= {"x", "y", "z", "score", "reason_tr"}
    assert set(cloud["voxels"][0]) >= {"x", "y", "z", "score", "atten_dB_km", "cn2", "grad_n", "scint"}
    assert b["bounds"] == {"x": 2000.0, "y": 400.0, "z": 400.0}
    assert b["meta"]["cells"] == 20 * 4 * 4
    assert client.post("/api/propagation3d", json=prop_body()).json() == b       # deterministic


def test_propagation3d_anomaly_and_lens_are_honoured():
    plain = client.post("/api/propagation3d", json=prop_body()).json()
    body = prop_body(lensPreset="keplerian_telescope", w0=0.002, anomalies=[
        {"center": [1000, 200, 200], "size": [300, 200, 200], "extra_alpha_dB_km": 80}])
    r = client.post("/api/propagation3d", json=body)
    assert r.status_code == 200
    b = r.json()
    assert b["summary"]["total_atten_dB"] > plain["summary"]["total_atten_dB"] + 10
    assert 850 <= b["degradation_cloud"]["hotspot"]["x"] <= 1150
    assert b["summary"]["lens_transmission"] < 1.0


@pytest.mark.parametrize("bad", [
    {"atmosphere": {"gridX": 300, "gridY": 30, "gridZ": 30}},        # 270k cells > limit
    {"atmosphere": {"gridX": 600, "gridY": 100, "gridZ": 100}},
    {"atmosphere": {"gridX": 0}}, {"atmosphere": {"weather": "tornado"}},
    {"nSamples": 5}, {"nSamples": 5000}, {"nRays": 100}, {"nRays": 1}, {"maxVoxels": 10 ** 6},
    {"source": [0, 0]}, {"source": [-5, 10, 10]}, {"target": [1e9, 0, 0]},
    {"source": [100, 100, 100], "target": [100, 100, 100]},
    {"lensPreset": "focusing"}, {"lensPreset": "magic"}, {"m2": 0.1},
    {"anomalies": [{"center": [0, 0, 0], "size": [0, 1, 1]}]},
    {"anomalies": [{"center": [0, 0, 0], "size": [1, 1, 1]}] * 9},
])
def test_propagation3d_rejects_out_of_bounds(bad):
    body = prop_body()
    body.update(bad)
    assert client.post("/api/propagation3d", json=body).status_code == 422


# ----------------------------------------------------------------- /api/arq
def test_arq_fixed_ber_contract_and_determinism():
    req = {"payload": "Merhaba FSO " * 10, "segmentBytes": 8, "maxRetries": 10, "ber": 0.003,
           "ackBer": 0.01, "seed": 4}
    r = client.post("/api/arq", json=req)
    assert r.status_code == 200
    b = r.json()
    assert set(b) >= {"segments", "retries_per_region", "retries_histogram", "total_transmissions",
                      "goodput_ratio", "efficiency", "delivered_payload", "delivered_ok",
                      "residual_errors", "spurious_retransmissions", "rounds"}
    assert b["delivered_ok"] and b["retransmissions"] > 0
    assert b["segments"][0]["region_tr"] == "Segment 0 (bayt 0–7)"
    assert client.post("/api/arq", json=req).json() == b


def test_arq_link_model_and_propagation_spatial_view():
    r = client.post("/api/arq", json={"distanceM": 500, "seed": 1})
    assert r.status_code == 200 and r.json()["delivered_ok"] and r.json()["channel"]["p_rx_dBm"] is not None
    body = {"ber": 0.01, "maxRetries": 10, "seed": 2, "spatialBins": 8,
            "propagation": prop_body(anomalies=[{"center": [1500, 200, 200], "size": [300, 200, 200],
                                                 "extra_alpha_dB_km": 80}])}
    r = client.post("/api/arq", json=body)
    assert r.status_code == 200
    b = r.json()
    assert len(b["retries_by_spatial_region"]) == 8
    assert b["propagation_summary"]["hotspot"]["x"] > 1300


def test_arq_honest_failure_reporting():
    r = client.post("/api/arq", json={"ber": 0.05, "maxRetries": 0, "seed": 1})
    b = r.json()
    assert r.status_code == 200 and not b["delivered_ok"] and b["residual_errors"]["failed_segments"] > 0


@pytest.mark.parametrize("bad", [
    {"payload": ""}, {"payload": "x" * 3000}, {"segmentBytes": 0}, {"segmentBytes": 256},
    {"payload": "x" * 2000, "segmentBytes": 2},            # 1000 segments
    {"maxRetries": 201}, {"ackBer": 0.7}, {"ber": -0.1}, {"distanceM": 0}, {"seed": -1},
    {"fadingRho": 1.0}, {"spatialBins": 1}, {"lensPreset": "nope"}, {"cn2": 1.0},
    {"propagation": {"atmosphere": {"gridX": 600, "gridY": 100, "gridZ": 100}}},
])
def test_arq_rejects_invalid_input(bad):
    assert client.post("/api/arq", json=bad).status_code == 422


# ----------------------------------------------------------------- /api/duplex
def test_duplex_contract():
    r = client.post("/api/duplex", json={"distanceM": 2000, "seed": 1})
    assert r.status_code == 200
    b = r.json()
    assert set(b) >= {"directions", "timeline", "summary"}
    assert set(b["directions"]) == {"A→B", "B→A"}
    d = b["directions"]["A→B"]
    assert set(d) >= {"link_budget", "ber", "arq_result", "throughput_bps", "goodput_bps"}
    assert b["summary"]["both_delivered"] and b["summary"]["asymmetry_dB"] < 0
    assert len(b["timeline"]) == b["summary"]["total_slots"]
    assert set(b["timeline"][0]) == {"slot", "a_tx", "b_tx", "a_rx_ok", "b_rx_ok"}
    assert b["directions"]["B→A"]["link_budget"]["wavelength_nm"] == 1310


def test_duplex_crosstalk_isolation_parameter_matters():
    def run(iso):
        t = {"filterIsolationDb": iso}
        return client.post("/api/duplex", json={"terminalA": t, "terminalB": {**t, "wavelengthNm": 1310},
                                                "distanceM": 2000, "seed": 3}).json()
    assert run(5)["directions"]["A→B"]["ber"] > 100 * run(40)["directions"]["A→B"]["ber"]


def test_duplex_with_3d_propagation_uses_per_wavelength_paths():
    body = {"propagation": prop_body(atmosphere={**SMALL_ATM, "weather": "haze"}), "seed": 1}
    r = client.post("/api/duplex", json=body)
    assert r.status_code == 200
    b = r.json()
    assert b["directions"]["A→B"]["link_budget"]["atmos_loss_dB"] > 0
    assert set(b["propagation_summary"]) == {"A→B", "B→A"}


@pytest.mark.parametrize("bad", [
    {"distanceM": -5}, {"segmentBytes": 0}, {"maxRetries": 201}, {"payloadA": "x" * 600},
    {"payloadA": "x" * 512, "segmentBytes": 4}, {"bitrateBps": 1}, {"reciprocityRho": 2},
    {"terminalA": {"wavelengthNm": 5}}, {"terminalB": {"txLens": "focusing"}},
    {"terminalA": {"rxLens": "keplerian_telescope"}}, {"terminalA": {"filterIsolationDb": -1}},
    {"weather": "meteor"}, {"berOverride": 2},
])
def test_duplex_rejects_invalid_input(bad):
    assert client.post("/api/duplex", json=bad).status_code == 422


def test_max_parameter_responses_stay_below_2mb():
    r = client.post("/api/arq", json={"payload": "é" * 1024, "segmentBytes": 8, "maxRetries": 10,
                                      "ber": 0.004, "ackBer": 0.02, "seed": 2})
    assert r.status_code == 200 and len(r.content) < 2_000_000
    r = client.post("/api/duplex", json={"payloadA": "é" * 256, "payloadB": "ü" * 256, "segmentBytes": 8,
                                         "maxRetries": 10, "berOverride": 0.004, "seed": 2})
    assert r.status_code == 200 and len(r.content) < 2_000_000


# ---- duplex/ARQ: propagation from a longer link must not change the link budget ----
LONG_ATM = {"gridX": 60, "gridY": 6, "gridZ": 6, "cellSize": 100, "weather": "clear"}   # 6 km


def test_duplex_longer_propagation_is_rescaled_to_duplex_distance():
    base = {"distanceM": 2000, "seed": 1}
    plain = client.post("/api/duplex", json=base).json()
    with3d = client.post("/api/duplex", json={**base, "propagation": prop_body(atmosphere=LONG_ATM)}).json()
    resc = with3d["propagation_rescale"]
    assert resc["distance_used_m"] == 2000 and resc["propagation_length_m"] == 6000 and resc["rescaled"]
    for k in ("A→B", "B→A"):
        d3, d0 = with3d["directions"][k], plain["directions"][k]
        assert d3["link_budget"]["distance_m"] == 2000
        # BER level comes from the 2 km budget, not from the 6 km propagation (47.8 dB)
        assert d3["link_budget"]["atmos_loss_dB"] < 0.5 * with3d["propagation_summary"][k]["summary"]["total_atten_dB"] + 1
        assert abs(d3["link_budget"]["p_rx_dBm"] - d0["link_budget"]["p_rx_dBm"]) < 3.0
    assert with3d["summary"]["both_delivered"]
    # spatial regions are mapped onto the duplex length
    reg = with3d["directions"]["A→B"]["arq_result"]["retries_by_spatial_region"]
    assert abs(reg[-1]["s_end"] - 2000) < 1e-6


def test_duplex_reported_ber_matches_channel_used():
    r = client.post("/api/duplex", json={"distanceM": 2000, "seed": 2, "terminalA": {"txPowerDbm": -5},
                                          "terminalB": {"txPowerDbm": -5, "wavelengthNm": 1310},
                                          "payloadA": "x" * 400, "payloadB": "y" * 400,
                                          "segmentBytes": 8, "maxRetries": 10}).json()
    for d in r["directions"].values():
        a = d["arq_result"]
        assert d["ber"] == a["forward_effective_ber"]
        assert d["ber_mean_power"] == a["forward_mean_ber"]
        assert d["ber_observed_mean"] > 0
        # observed per-attempt mean BER agrees with the reported (fading-averaged) channel BER
        assert 0.25 < d["ber_observed_mean"] / d["ber"] < 4.0


def test_duplex_rescale_unit():
    from backend import duplex
    s = {"length_m": 6000.0, "total_atten_dB": 12.0, "sigma2_R": 4.0}
    r = duplex.rescale_propagation(s, 2000.0)
    assert abs(r["atmos_loss_dB"] - 4.0) < 1e-12 and abs(r["scale"] - 1 / 3) < 1e-12
    assert r["sigma_I2"] < duplex.rescale_propagation(s, 6000.0)["sigma_I2"]


def test_arq_3d_reports_distance_used_and_effective_ber():
    r = client.post("/api/arq", json={"distanceM": 500, "seed": 1,
                                       "propagation": prop_body(atmosphere=LONG_ATM)}).json()
    assert r["channel"]["distance_used_m"] == 6000     # channel is the propagation's own link
    assert r["channel"]["effective_ber"] >= r["channel"]["mean_ber"] * 0.999


# ----------------------------------------------------------------- distanceM
def test_propagation3d_distance_m_sets_length():
    r = client.post("/api/propagation3d", json=prop_body(distanceM=1500))
    assert r.status_code == 200
    b = r.json()
    assert b["bounds"]["x"] == pytest.approx(1500.0)
    assert b["centerline"][-1]["x"] == pytest.approx(1500.0, abs=1.0)
    assert b["meta"]["target"][0] == pytest.approx(1500.0)
    g = b["meta"]["grid"]
    assert g["gridX"] * g["cellSize"] == pytest.approx(1500.0)


def test_propagation3d_distance_m_20km_within_limit_and_fast():
    import time
    t0 = time.time()
    r = client.post("/api/propagation3d", json={"atmosphere": {"gridY": 100, "gridZ": 100, "cellSize": 100},
                                               "distanceM": 20000, "nSamples": 160})
    assert r.status_code == 200
    b = r.json()
    assert b["meta"]["cells"] <= 40000
    assert b["bounds"]["x"] == pytest.approx(20000.0)
    assert time.time() - t0 < 3.0


@pytest.mark.parametrize("d", [50, 99.9, 20001, -5])
def test_propagation3d_distance_m_out_of_range_422(d):
    assert client.post("/api/propagation3d", json=prop_body(distanceM=d)).status_code == 422


def test_propagation3d_distance_m_absent_unchanged():
    b = client.post("/api/propagation3d", json=prop_body()).json()
    assert b["bounds"]["x"] == 2000.0 and b["meta"]["cells"] == 320
