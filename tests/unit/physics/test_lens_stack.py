import json
import math

import pytest

from backend import lens_stack as ls
from backend import materials as mt
from backend import optics as op

LAM = 1550e-9


def el(shape="plano_convex", f=0.2, D=0.02, material="n_bk7", gap=0.0, **kw):
    return dict(shape=shape, material=material, focalLengthM=f, diameterM=D, spacingM=gap, **kw)


def _thick(shape, f, D, material="n_bk7"):
    d = mt.design_lens(shape, material, f, D, LAM)
    R1 = math.inf if d["R1_m"] is None else d["R1_m"]
    R2 = math.inf if d["R2_m"] is None else d["R2_m"]
    return d, op.thick_lens(d["n_used"], R1, R2, d["center_thickness_m"])


def test_single_element_matches_design_lens():
    r = ls.design_stack([el("biconvex", 0.1, 0.025)], LAM)
    d = mt.design_lens("biconvex", "n_bk7", 0.1, 0.025, LAM)
    assert r["system"]["efl_m"] == pytest.approx(d["efl_check_m"], rel=1e-6)
    assert r["total_transmittance"] == pytest.approx(d["transmittance"], rel=1e-7)
    assert r["system"]["stop_element"] == 0
    assert r["system"]["entrance_pupil_radius_m"] == pytest.approx(0.0125)
    assert r["system"]["f_number"] == pytest.approx(0.1 / 0.025, rel=1e-3)


def test_lenses_in_contact_add_powers():
    f1, f2 = 0.2, 0.3
    r = ls.design_stack([el("biconvex", f1, 0.02), el("plano_convex_flat_front", f2, 0.02)], LAM)
    assert r["system"]["efl_m"] == pytest.approx(1.0 / (1 / f1 + 1 / f2), rel=0.03)
    assert r["elements"][1]["z_front_m"] == pytest.approx(r["elements"][0]["z_back_m"])


def test_separated_lenses_follow_gullstrand():
    gap = 0.1
    r = ls.design_stack([el("biconvex", 0.2, 0.02, gap=gap), el("biconvex", 0.3, 0.02)], LAM)
    d1, m1 = _thick("biconvex", 0.2, 0.02)
    d2, m2 = _thick("biconvex", 0.3, 0.02)
    p1, p2 = -m1[1, 0], -m2[1, 0]
    rear1 = (1.0 - m1[0, 0]) / m1[1, 0]
    front2 = (m2[1, 1] - 1.0) / m2[1, 0]
    sep = gap + front2 - rear1                      # principal-plane separation
    power = p1 + p2 - sep * p1 * p2
    assert r["system"]["efl_m"] == pytest.approx(1.0 / power, rel=1e-6)
    # thin-lens Gullstrand within thick-lens tolerance
    thin = 1 / 0.2 + 1 / 0.3 - gap / (0.2 * 0.3)
    assert r["system"]["efl_m"] == pytest.approx(1.0 / thin, rel=0.03)


def test_kepler_is_afocal_with_magnification():
    els = ls.preset_elements("kepler", LAM)
    r = ls.design_stack(els, LAM)
    s = r["system"]
    assert s["afocal"] and abs(s["abcd"]["C"]) < 1e-9
    assert s["beam_magnification"] == pytest.approx(-0.20 / 0.05, rel=1e-6)
    assert s["angular_magnification"] == pytest.approx(-0.05 / 0.20, rel=1e-6)
    assert s["efl_m"] is None and s["f_number"] is None
    # thick-lens spacing is shorter than f1+f2 by the principal-plane offsets
    assert 0.2 < els[0]["spacingM"] < 0.25
    assert s["determinant_ok"]


def test_galileo_is_afocal_and_upright():
    r = ls.design_stack(ls.preset_elements("galileo", LAM), LAM)
    s = r["system"]
    assert s["afocal"] and s["beam_magnification"] == pytest.approx(4.0, rel=1e-6)
    assert s["stop_name"] == "Lens 2"                # the 50 mm lens limits a 4x beam


def test_transmittance_multiplies():
    els = [el("biconvex", 0.2, 0.02, coating="v_coat_1550", filter="bp_1550_10nm", gap=0.01),
           el("plano_convex", 0.3, 0.02, coating="none"),
           el("biconvex", 0.25, 0.02, coating="bbar_telecom_1200_1600", gap=0.0)]
    r = ls.design_stack(els, LAM)
    expect = 1.0
    for e in els:
        d = mt.design_lens(e["shape"], e["material"], e["focalLengthM"], e["diameterM"], LAM, None, e["coating"])
        f = e.get("filter")
        expect *= d["transmittance"] * (mt.filter_transmittance(f, LAM) if f else 1.0)
    assert r["total_transmittance"] == pytest.approx(expect, rel=1e-7)
    assert r["total_transmittance"] == pytest.approx(math.prod(e["transmittance"] for e in r["elements"]), rel=1e-6)
    assert r["total_transmittance"] < min(e["transmittance"] for e in r["elements"])


def test_limits_and_validation():
    with pytest.raises(ls.StackError, match="en fazla 8"):
        ls.design_stack([el(gap=0.05)] * 9, LAM)
    with pytest.raises(ls.StackError, match="en az bir"):
        ls.design_stack([], LAM)
    with pytest.raises(ls.StackError, match="opak"):
        ls.design_stack([el(material="germanium")], LAM)
    with pytest.raises(ls.StackError, match="negatif"):
        ls.design_stack([el(gap=-0.01), el()], LAM)
    # flat-back lens touching a biconcave one: the concave front rim sags into lens 1
    with pytest.raises(ls.StackError, match="çakış"):
        ls.design_stack([el("plano_convex", 0.1, 0.025, gap=0.0), el("biconcave", 0.1, 0.025)], LAM)
    # ... and fine with enough air gap
    ls.design_stack([el("plano_convex", 0.1, 0.025, gap=0.02), el("biconcave", 0.1, 0.025)], LAM)
    with pytest.raises(ls.StackError, match="Bilinmeyen ön ayar"):
        ls.preset_elements("yok")


def test_ray_fan_converges_near_reported_bfl():
    els = ls.preset_elements("achromat_pair", LAM)
    r = ls.design_stack(els, LAM, n_rays=11)
    fan, s = r["ray_fan"], r["system"]
    z_out = s["z_out_m"]
    assert fan["focus_z"] == pytest.approx(z_out + s["bfl_m"], rel=1e-4)    # exact paraxial = ABCD
    assert fan["bfl_exact_m"] == pytest.approx(s["bfl_m"], rel=1e-4)
    ep = s["entrance_pupil_radius_m"]
    checked = 0
    for ray in fan["rays"]:
        if abs(ray["y_in"]) > 0.55 * ep or abs(ray["y_in"]) < 1e-9:
            continue
        (z1, y1), (z2, y2) = ray["points"][-2], ray["points"][-1]
        zc = z1 - y1 * (z2 - z1) / (y2 - y1)
        assert abs(zc - fan["focus_z"]) < 0.08 * s["bfl_m"]
        assert y1 * ray["y_in"] > 0 or abs(zc - z1) > 0       # converging
        checked += 1
    assert checked >= 4
    # one polyline point per surface + start + end
    assert all(len(ray["points"]) == 2 * len(els) + 2 for ray in fan["rays"])


def test_kepler_rays_exit_parallel_and_expanded():
    r = ls.design_stack(ls.preset_elements("kepler", LAM), LAM, n_rays=5)
    for ray in r["ray_fan"]["rays"]:
        (z1, y1), (z2, y2) = ray["points"][-2], ray["points"][-1]
        slope = (y2 - y1) / (z2 - z1)
        assert abs(slope) < 0.05                    # marginal rays: spherical-aberration residue
        if abs(ray["y_in"]) <= 0.5 * 0.0125:
            assert abs(slope) < 6e-3                # paraxial region: nearly collimated
        if abs(ray["y_in"]) <= 0.5 * 0.0125:
            assert abs(y1) == pytest.approx(4.0 * abs(ray["y_in"]), rel=0.05)
        if abs(ray["y_in"]) > 1e-9:
            assert y1 * ray["y_in"] < 0             # inverted


def test_field_angle_image_height():
    r = ls.design_stack(ls.preset_elements("achromat_pair", LAM), LAM, field_angle_deg=2.0)
    f = r["system"]["efl_m"]
    assert r["ray_fan"]["field_image_height_m"] == pytest.approx(f * math.tan(math.radians(2.0)), rel=0.03)
    assert len(r["ray_fan"]["rays_field"]) == 9


def test_vignetting_reduces_with_field():
    r0 = ls.design_stack(ls.preset_elements("cooke_triplet", LAM), LAM, field_angle_deg=0.0)
    r8 = ls.design_stack(ls.preset_elements("cooke_triplet", LAM), LAM, field_angle_deg=8.0)
    assert r0["vignetting"]["pass_fraction_at_field"] > 0.99
    assert r8["vignetting"]["pass_fraction_at_field"] < r0["vignetting"]["pass_fraction_at_field"]


def test_link_gain_and_gaussian():
    r = ls.design_stack(ls.preset_elements("kepler", LAM), LAM, link_distance_m=2000.0, beam_w0_m=0.002)
    assert r["link"]["transmit"]["gain_dB"] > 5.0           # 4x expander narrows the divergence
    g = r["gaussian"]
    assert g["divergence"] == pytest.approx(r["link"]["transmit"]["divergence_without_rad"] / 4.0, rel=0.05)
    assert 0 < g["clipping_transmission"] <= 1
    # receiving: aperture (EP) x T vs bare receiver
    big = ls.design_stack([el("plano_convex", 0.3, 0.1)], LAM, bare_rx_radius_m=0.0125, beam_w0_m=0.05,
                          link_distance_m=100.0)
    assert big["link"]["gain_dB"] > 0


def test_presets_listing():
    pl = ls.preset_list(LAM)
    assert [p["key"] for p in pl] == ["kepler", "galileo", "achromat_pair", "cooke_triplet"]
    names = [p["name_tr"] for p in pl]
    assert "Kepler genişletici (2 lens)" in names and "Üçlü (Cooke benzeri)" in names
    by = {p["key"]: p for p in pl}
    assert by["kepler"]["summary"]["afocal"] and by["achromat_pair"]["summary"]["efl_m"] == pytest.approx(0.1626, rel=0.01)
    assert len(by["cooke_triplet"]["elements"]) == 3


def test_json_size_bounded():
    els = [el("biconvex", 0.15, 0.02, gap=0.02) for _ in range(8)]
    r = ls.design_stack(els, LAM)
    assert len(json.dumps(r)) < 150_000
    r2 = ls.design_stack(els, LAM, n_points=201, n_rays=21, field_angle_deg=3.0)
    assert len(json.dumps(r2)) < 450_000
