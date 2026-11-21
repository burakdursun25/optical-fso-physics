import math

from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)


def test_catalog():
    r = client.get("/api/lens/catalog")
    assert r.status_code == 200
    b = r.json()
    assert {"materials", "shapes", "filters", "coatings"} <= set(b)
    assert "n_bk7" in b["materials"] and b["materials"]["n_bk7"]["n"]["1550"] > 1.4


def test_design_plano_convex_contract():
    r = client.post("/api/lens/design", json={"shape": "plano_convex", "material": "n_bk7",
                                              "focalLengthM": 0.1, "diameterM": 0.05, "wavelengthNm": 1550,
                                              "coating": "v_coat_1550", "filter": "bp_1550_10nm"})
    assert r.status_code == 200
    b = r.json()
    d = b["design"]
    assert math.isclose(b["abcd"]["efl_m"], 0.1, rel_tol=1e-6)
    assert d["R1_m"] > 0 and d["R2_m"] is None
    assert 0.0 < b["total_transmittance"] <= d["transmittance"]
    assert b["filter"]["transmittance"] > 0.5
    fan = b["ray_fan"]
    assert len(fan["rays"]) == 9 and all(len(ray["points"]) == 4 for ray in fan["rays"])
    assert abs(fan["bfd_m"] + b["design"]["center_thickness_m"] - fan["focus_z"]) < 1e-9
    # paraxial focus ~ EFL from the principal plane: BFD within a few % of f
    assert 0.8 * 0.1 < fan["bfd_m"] < 1.05 * 0.1
    # rays converge: exit heights have the opposite sign to the entry height
    top = [ray for ray in fan["rays"] if ray["y_in"] > 0][-1]
    assert top["points"][-1][1] < top["points"][0][1]
    assert b["link"]["gain_dB"] > 0 and len(d["profile"]) >= 41


def test_aspheric_has_no_spherical_aberration():
    b = client.post("/api/lens/design", json={"shape": "aspheric", "focalLengthM": 0.1,
                                              "diameterM": 0.03}).json()
    assert abs(b["ray_fan"]["lsa_m"]) < 1e-6


def test_diverging_lens_marked_unsuitable():
    b = client.post("/api/lens/design", json={"shape": "plano_concave"}).json()
    assert b["design"]["focal_length_m"] < 0 and b["link"]["receiver_suitable"] is False


def test_opaque_material_422_turkish():
    r = client.post("/api/lens/design", json={"material": "silicon", "wavelengthNm": 850})
    assert r.status_code == 422
    assert "opak" in r.json()["detail"]


def test_validation_errors():
    for body in ({"shape": "nope"}, {"material": "nope"}, {"filter": "nope"}, {"coating": "nope"},
                 {"shape": "grin"}, {"focalLengthM": -1}, {"diameterM": 0}):
        assert client.post("/api/lens/design", json=body).status_code == 422, body


def test_static_routes():
    for path in ("/viz3d.js", "/duplex.js", "/lens-designer.js", "/ui-v2.css"):
        assert client.get(path).status_code == 200, path


def test_duplex_with_propagation_has_spatial_regions():
    body = {"propagation": {"atmosphere": {"gridX": 20, "gridY": 4, "gridZ": 4, "thermalProfile": "uniform"},
                            "nSamples": 40, "nRays": 8}, "berOverride": 0.01}
    b = client.post("/api/duplex", json=body).json()
    for key in ("A→B", "B→A"):
        reg = b["directions"][key]["arq_result"]["retries_by_spatial_region"]
        assert len(reg) == 10 and all("sampled_retries" in r and "s_start" in r for r in reg)
