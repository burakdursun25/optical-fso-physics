import json
import math

from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)


def el(shape="plano_convex", f=0.2, D=0.02, gap=0.0, **kw):
    return {"shape": shape, "material": "n_bk7", "focalLengthM": f, "diameterM": D, "spacingM": gap, **kw}


def test_presets_endpoint():
    r = client.get("/api/lens/stack/presets")
    assert r.status_code == 200
    b = r.json()
    assert b["maxElements"] == 8
    keys = [p["key"] for p in b["presets"]]
    assert keys == ["kepler", "galileo", "achromat_pair", "cooke_triplet"]
    for p in b["presets"]:
        assert p["elements"] and p["summary"]["total_transmittance"] > 0.5
        # a preset's elements can be posted back unchanged
        rr = client.post("/api/lens/stack", json={"elements": p["elements"]})
        assert rr.status_code == 200, rr.text
        assert math.isclose(rr.json()["total_transmittance"], p["summary"]["total_transmittance"], rel_tol=1e-4)


def test_stack_by_preset_name():
    r = client.post("/api/lens/stack", json={"preset": "kepler"})
    assert r.status_code == 200
    assert r.json()["system"]["afocal"] is True
    assert client.post("/api/lens/stack", json={"preset": "nope"}).status_code == 422
    assert client.post("/api/lens/stack", json={}).status_code == 422


def test_single_element_equals_design_endpoint():
    body = {"shape": "biconvex", "material": "n_bk7", "focalLengthM": 0.1, "diameterM": 0.05,
            "coating": "v_coat_1550", "filter": "bp_1550_10nm"}
    d = client.post("/api/lens/design", json={**body, "wavelengthNm": 1550}).json()
    s = client.post("/api/lens/stack", json={"elements": [{**body, "spacingM": 0.0}], "wavelengthNm": 1550})
    assert s.status_code == 200
    s = s.json()
    assert math.isclose(s["system"]["efl_m"], d["abcd"]["efl_m"], rel_tol=1e-6)
    assert math.isclose(s["system"]["bfl_m"], d["abcd"]["bfd_m"], rel_tol=1e-6)
    assert math.isclose(s["total_transmittance"], d["total_transmittance"], rel_tol=1e-6)
    assert math.isclose(s["ray_fan"]["focus_z"], d["ray_fan"]["focus_z"], rel_tol=2e-3)


def test_validation_errors_are_turkish_422():
    r = client.post("/api/lens/stack", json={"elements": [el(gap=0.01)] * 9})
    assert r.status_code == 422 and "en fazla 8" in r.json()["detail"]
    r = client.post("/api/lens/stack", json={"elements": []})
    assert r.status_code == 422
    r = client.post("/api/lens/stack", json={"elements": [{**el(), "material": "germanium"}]})
    assert r.status_code == 422 and "opak" in r.json()["detail"]
    r = client.post("/api/lens/stack", json={"elements": [el(gap=-0.01), el()]})
    assert r.status_code == 422 and "negatif" in r.json()["detail"]
    r = client.post("/api/lens/stack", json={"elements": [el("plano_convex", 0.1, 0.025), el("biconcave", 0.1, 0.025)]})
    assert r.status_code == 422 and "çakış" in r.json()["detail"]
    r = client.post("/api/lens/stack", json={"elements": [{**el(), "shape": "kare"}]})
    assert r.status_code == 422 and "Eleman 1" in r.json()["detail"]


def test_ray_fan_contract_and_size():
    els = [el("biconvex", 0.15, 0.02, gap=0.02) for _ in range(8)]
    r = client.post("/api/lens/stack", json={"elements": els, "nRays": 9, "fieldAngleDeg": 2.0})
    assert r.status_code == 200
    b = r.json()
    assert len(b["elements"]) == 8 and b["n_elements"] == 8
    z = [(e["z_front_m"], e["z_back_m"]) for e in b["elements"]]
    assert all(z[i][1] <= z[i + 1][0] for i in range(7))
    assert all(e["profile"][0][1] >= e["z_front_m"] - 1e-6 for e in b["elements"])
    fan = b["ray_fan"]
    assert len(fan["rays"]) == 9 and len(fan["rays_field"]) == 9
    assert len(r.content) < 150_000
    json.loads(r.text)  # strictly valid JSON (no NaN/inf)


def test_cooke_triplet_readouts():
    b = client.post("/api/lens/stack", json={"preset": "cooke_triplet", "linkDistanceM": 500}).json()
    s = b["system"]
    assert s["efl_m"] > 0 and s["bfl_m"] > 0 and s["f_number"] > 1
    assert s["stop_name"].startswith("Lens") and 0 < b["total_transmittance"] < 1
    assert "gain_dB" in b["link"] and "transmit" in b["link"]
    assert b["vignetting"]["on_axis_clear"] is True
