import json
import math

import numpy as np
import pytest

from backend import numerics, optics
from backend.engine import AtmosphereGrid
from backend.propagation3d import (propagate_beam_3d, pointing_coupling,
                                   scintillation_index_from_rytov, MAX_VOXELS)

LAM = 1550e-9
SRC, TGT = (0.0, 300.0, 300.0), (3000.0, 300.0, 300.0)


def uniform_grid(cn2=1e-16, gx=30, gy=6, gz=6, cs=100.0):
    g = AtmosphereGrid(gx, gy, gz, cs)
    g.grid[:] = 1.000293
    g.Cn2[:] = cn2
    g.temperature[:] = 15.0
    g.humidity[:] = 50.0
    return g


def cl(res, key):
    return np.array([c[key] for c in res["centerline"]])


def test_uniform_atmosphere_gives_straight_centerline_and_vacuum_beam_radius():
    res = propagate_beam_3d(uniform_grid(), SRC, TGT, w0=2e-3, wavelength=LAM, n_samples=60)
    assert np.allclose(cl(res, "y"), 300.0, atol=1e-9) and np.allclose(cl(res, "z"), 300.0, atol=1e-9)
    assert cl(res, "deflection").max() < 1e-9
    for c in res["centerline"][::10]:
        assert c["w"] == pytest.approx(optics.beam_radius(c["s"], 2e-3, LAM), rel=1e-5)


def test_extinction_matches_weather_alpha_and_numeric_path_integral():
    res = propagate_beam_3d(uniform_grid(), SRC, TGT, wavelength=LAM, weather="fog", n_samples=100)
    alpha = res["summary"]["weather_alpha_dB_km"]
    assert res["summary"]["total_atten_dB"] == pytest.approx(alpha * 3.0, rel=1e-4)
    check = numerics.path_integral(lambda p: alpha / 1000.0, [SRC, TGT])
    assert res["summary"]["total_atten_dB"] == pytest.approx(check, rel=1e-4)
    assert cl(res, "atten_dB")[0] == 0.0 and np.all(np.diff(cl(res, "atten_dB")) >= 0)


def test_turbulence_broadens_beam_and_never_narrows_it():
    weak = propagate_beam_3d(uniform_grid(1e-17), SRC, TGT, wavelength=LAM, n_samples=50)
    strong = propagate_beam_3d(uniform_grid(1e-13), SRC, TGT, wavelength=LAM, n_samples=50)
    for res in (weak, strong):
        assert np.all(cl(res, "w_lt") >= cl(res, "w") * (1 - 1e-12))
    assert strong["summary"]["final_w_lt"] / strong["summary"]["final_w"] > 1.05
    assert weak["summary"]["final_w_lt"] / weak["summary"]["final_w"] < 1.001
    assert strong["summary"]["wander_m"] > weak["summary"]["wander_m"]
    assert strong["summary"]["scint_index"] > weak["summary"]["scint_index"]


def test_rytov_variance_matches_homogeneous_closed_form():
    cn2 = 1e-15
    res = propagate_beam_3d(uniform_grid(cn2), SRC, TGT, wavelength=LAM, n_samples=200)
    k = 2 * math.pi / LAM
    assert res["summary"]["sigma2_R"] == pytest.approx(1.23 * cn2 * k ** (7 / 6) * 3000 ** (11 / 6), rel=0.02)


def test_vertical_index_gradient_deflects_centroid_toward_higher_n():
    g = uniform_grid()
    iz = np.repeat(np.arange(6), 30 * 6)           # index = ix + gX*(iy + gY*iz)
    g.grid[:] = 1.000293 + 1e-6 * iz                # dn/dz = 1e-8 per metre, increasing upward
    res = propagate_beam_3d(g, SRC, TGT, wavelength=LAM, n_samples=100)
    assert res["centerline"][-1]["z"] > 300.0
    analytic = 0.5 * (1e-8 / 1.000293) * 3000.0 ** 2
    assert res["summary"]["deflection_m"] == pytest.approx(analytic, rel=0.05)
    assert abs(res["centerline"][-1]["y"] - 300.0) < 1e-6
    g.grid[:] = 1.000293 + 1e-6 * (5 - iz)          # reversed: bends downward
    down = propagate_beam_3d(g, SRC, TGT, wavelength=LAM, n_samples=100)
    assert down["centerline"][-1]["z"] < 300.0


def test_hotspot_lies_in_deliberately_worsened_fog_block():
    block = {"center": [2000, 300, 300], "size": [400, 200, 200], "extra_alpha_dB_km": 60}
    res = propagate_beam_3d(uniform_grid(), SRC, TGT, wavelength=LAM, anomalies=[block])
    hs = res["degradation_cloud"]["hotspot"]
    assert 1800 <= hs["x"] <= 2200
    assert hs["dominant"] == "attenuation" and hs["reason_tr"] and hs["reason_en"]
    ws = res["summary"]["worst_segment"]
    assert ws["s_start"] < 2200 and ws["s_end"] > 1800
    top = max(res["degradation_cloud"]["voxels"], key=lambda v: v["score"])
    assert 1800 <= top["x"] <= 2200 and top["score"] == res["degradation_cloud"]["max_score"]


def test_hotspot_finds_turbulent_pocket_and_names_turbulence():
    block = {"center": [1000, 300, 300], "size": [300, 200, 200], "cn2_factor": 1e4}
    res = propagate_beam_3d(uniform_grid(), SRC, TGT, wavelength=LAM, anomalies=[block])
    hs = res["degradation_cloud"]["hotspot"]
    assert 850 <= hs["x"] <= 1150 and hs["dominant"] == "turbulence"


def test_cloud_shows_where_the_light_is_not_where_the_grid_is_bad():
    on = propagate_beam_3d(uniform_grid(), SRC, TGT, wavelength=LAM, max_voxels=4000, min_voxel_score=0.0,
                           anomalies=[{"center": [2000, 300, 300], "size": [400, 200, 200],
                                       "extra_alpha_dB_km": 60}])
    # an identical fog block far from the beam (y=550) must score far lower than on-beam fog
    off = propagate_beam_3d(uniform_grid(), SRC, TGT, wavelength=LAM, max_voxels=4000, min_voxel_score=0.0,
                            anomalies=[{"center": [2000, 550, 550], "size": [400, 100, 100],
                                        "extra_alpha_dB_km": 60}])
    on_best = max(v["score"] for v in on["degradation_cloud"]["voxels"] if 1800 <= v["x"] <= 2200)
    off_best = max(v["score"] for v in off["degradation_cloud"]["voxels"]
                   if 1800 <= v["x"] <= 2200 and v["y"] > 500)
    assert on_best > 5 * off_best


def test_voxel_budget_json_and_size_bounds():
    g = AtmosphereGrid(200, 15, 20, 50.0)            # 60k cells
    res = propagate_beam_3d(g, (0, 375, 500), (10000, 375, 500), wavelength=LAM, n_samples=400,
                            n_rays=24, max_voxels=MAX_VOXELS, min_voxel_score=0.0)
    cloud = res["degradation_cloud"]
    assert 0 < len(cloud["voxels"]) <= MAX_VOXELS and cloud["n_voxels"] == len(cloud["voxels"])
    assert all(0.0 <= v["score"] <= 1.0 for v in cloud["voxels"]) and 0 < cloud["max_score"] <= 1
    scores = [v["score"] for v in cloud["voxels"]]
    assert scores == sorted(scores, reverse=True)
    assert len(json.dumps(res)) < 2_000_000
    assert len(res["rings"]) <= 64 and all(len(r) == 24 for r in res["rings"])
    assert len(res["centerline"]) == 401
    small = propagate_beam_3d(g, (0, 375, 500), (10000, 375, 500), wavelength=LAM, max_voxels=50)
    assert len(small["degradation_cloud"]["voxels"]) <= 50
    assert set(res["summary"]) >= {"total_atten_dB", "final_w", "final_w_lt", "wander_m", "deflection_m",
                                   "power_at_rx_frac", "worst_segment"}
    assert set(res["bounds"]) == {"x", "y", "z"}


def test_rings_lie_on_the_long_term_envelope_around_the_centerline():
    res = propagate_beam_3d(uniform_grid(1e-14), SRC, TGT, wavelength=LAM, n_samples=40, n_rays=12, seed=3)
    for ring, i in zip(res["rings"], res["ring_sample_indices"]):
        c = res["centerline"][i]
        centre = np.array([c["x"], c["y"], c["z"]])
        d = np.linalg.norm(np.array(ring) - centre, axis=1)
        assert d == pytest.approx(c["w_lt"], rel=1e-3, abs=1e-6)
        assert np.allclose(np.array(ring)[:, 0], c["x"], atol=1e-3)      # ring is perpendicular to +x link


def test_lens_preset_changes_beam_and_reports_transmission():
    plain = propagate_beam_3d(uniform_grid(), SRC, TGT, w0=2e-3, wavelength=LAM)
    kep = propagate_beam_3d(uniform_grid(), SRC, TGT, w0=2e-3, wavelength=LAM, lens_preset="keplerian_telescope")
    assert kep["summary"]["final_w"] < 0.5 * plain["summary"]["final_w"]
    assert 0 < kep["summary"]["lens_transmission"] <= 1.0


def test_grid_not_mutated_and_seeded_determinism():
    g = uniform_grid()
    before = (g.grid.copy(), g.Cn2.copy())
    anomalies = [{"center": [1000, 300, 300], "size": [300, 300, 300], "cn2_factor": 100, "delta_T": 5}]
    a = propagate_beam_3d(g, SRC, TGT, wavelength=LAM, seed=5, anomalies=anomalies)
    b = propagate_beam_3d(g, SRC, TGT, wavelength=LAM, seed=5, anomalies=anomalies)
    assert a == b
    assert np.array_equal(before[0], g.grid) and np.array_equal(before[1], g.Cn2)


@pytest.mark.parametrize("kwargs", [
    {"target": (9999, 300, 300)}, {"source": (-1, 0, 0)}, {"n_rays": 100}, {"n_samples": 1},
    {"target": SRC}, {"max_voxels": 99999}, {"wavelength": -1.0},
])
def test_invalid_input_rejected(kwargs):
    args = dict(source=SRC, target=TGT, wavelength=LAM)
    args.update(kwargs)
    with pytest.raises(ValueError):
        propagate_beam_3d(uniform_grid(), **args)


def test_pointing_coupling_limits():
    assert pointing_coupling(0.01, 0.05, 0.0) == pytest.approx(1.0, abs=1e-6)
    assert pointing_coupling(0.1, 0.01, 0.0) == pytest.approx(1 - math.exp(-2 * 0.01 ** 2 / 0.1 ** 2))
    vals = [pointing_coupling(0.05, 0.05, d) for d in (0, 0.02, 0.05, 0.1, 0.3)]
    assert vals == sorted(vals, reverse=True) and vals[-1] < 1e-3


def test_scintillation_index_weak_and_saturated():
    assert scintillation_index_from_rytov(0.0) == 0.0
    assert scintillation_index_from_rytov(0.01) == pytest.approx(0.01, rel=0.05)
    assert 0.5 < scintillation_index_from_rytov(1e4) < 3.0
