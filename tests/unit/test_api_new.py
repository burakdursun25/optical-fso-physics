import pytest
from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)


def test_bitcheck_clean_link_roundtrip():
    r = client.post("/api/bitcheck", json={"payload": "hello", "distanceM": 100, "seed": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["comparison"]["errors"] == 0
    assert body["frame"]["crc_ok"] is True and body["payload_intact"] is True
    assert body["sent_bits"] == body["received_bits"]


def test_bitcheck_marginal_link_deterministic_with_seed():
    req = {"payload": "A" * 64, "distanceM": 6000, "seed": 5}
    a, b = client.post("/api/bitcheck", json=req).json(), client.post("/api/bitcheck", json=req).json()
    assert a == b
    assert a["comparison"]["errors"] > 0 and a["frame"]["crc_ok"] is False


def test_bitcheck_dropped_channel_and_include_bits_flag():
    r = client.post("/api/bitcheck", json={"payload": "x", "distanceM": 10,
                                           "sensitivityDbm": 50, "includeBits": False})
    body = r.json()
    assert body["link"]["channel_dropped"] is True
    assert body["comparison"]["errors"] == body["comparison"]["total"]
    assert "sent_bits" not in body


@pytest.mark.parametrize("bad", [
    {"distanceM": -1}, {"distanceM": 1e9}, {"lensPreset": "magic"}, {"seed": -3},
    {"payload": "é" * 400}, {"noiseRmsDbm": 5},
])
def test_bitcheck_rejects_invalid_input(bad):
    assert client.post("/api/bitcheck", json=bad).status_code == 422


def test_optics_presets_listing():
    r = client.get("/api/optics/presets")
    assert r.status_code == 200
    keys = {p["key"] for p in r.json()["presets"]}
    assert {"none", "collimating", "keplerian_telescope", "galilean_telescope"} <= keys
    kep = next(p for p in r.json()["presets"] if p["key"] == "keplerian_telescope")
    assert kep["magnification"] == pytest.approx(5.0)


def test_optics_link_gain_rows_and_validation():
    r = client.post("/api/optics/link-gain", json={"lensPreset": "keplerian_telescope",
                                                   "distancesM": [100, 5000]})
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 2 and rows[1]["gain_dB"] > 0
    for bad in ({"distancesM": []}, {"distancesM": [-5]}, {"rxRadiusM": 0}, {"lensPreset": "x"},
                {"wavelengthM": 1e-12}):
        assert client.post("/api/optics/link-gain", json=bad).status_code == 422


@pytest.mark.parametrize("method,order", [("trapezoid", 2), ("simpson", 4), ("euler", 1), ("rk4", 4)])
def test_convergence_orders(method, order):
    r = client.post("/api/numerics/convergence", json={"method": method, "ns": [8, 16, 32, 64]})
    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 4
    assert body["order"] == pytest.approx(order, abs=0.3)


def test_convergence_rejects_bad_input():
    for bad in ({"method": "os.system"}, {"method": "simpson", "ns": [3, 5]}, {"ns": [4]},
                {"ns": [0, 4]}, {"ns": [4, 4]}, {"problem": "eval"}, {"method": "gauss_legendre", "ns": [2, 100]}):
        assert client.post("/api/numerics/convergence", json=bad).status_code == 422


def test_trace_events_carry_incidence_cosines():
    """Regression: refraction events lacked cosTheta_i/t, so the UI table showed NaN degrees."""
    import math
    ev = [e for e in client.post("/api/simulate", json={}).json()["trace"]["refractionEvents"]
          if e["type"] == "refraction"]
    assert ev and all(0.0 <= e["cosTheta_i"] <= 1.0 and 0.0 <= e["cosTheta_t"] <= 1.0 for e in ev)
    from backend.engine import Vec3, vectorialSnellLaw
    for tilt in (0.0, 1e-12, 1e-9):
        res = vectorialSnellLaw(Vec3(tilt, 0.0, 1.0), Vec3(0.0, 0.0, 1.0), 1.0, 1.0003)
        assert 0.0 <= res["cosTheta_i"] <= 1.0 and 0.0 <= res["cosTheta_t"] <= 1.0
        assert math.degrees(math.acos(res["cosTheta_i"])) < 1e-3
