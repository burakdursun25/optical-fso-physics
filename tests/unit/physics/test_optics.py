import math

import numpy as np
import pytest

from backend import optics as op

LAM = 1550e-9


def _sys(*mats):
    return op.OpticalSystem([op.OpticalElement(f"e{i}", m) for i, m in enumerate(mats)])


# ---------------------------------------------------------------- ray matrices

def test_thin_lens_imaging_equation_via_abcd():
    f, s = 0.1, 0.3
    s_img = 1.0 / (1.0 / f - 1.0 / s)
    m = op.free_space(s_img) @ op.thin_lens(f) @ op.free_space(s)
    assert m[0, 1] == pytest.approx(0.0, abs=1e-12)          # B = 0 at image plane
    assert m[0, 0] == pytest.approx(-s_img / s)              # lateral magnification
    off = op.free_space(s_img * 1.1) @ op.thin_lens(f) @ op.free_space(s)
    assert abs(off[0, 1]) > 1e-3


def test_determinant_is_one_in_same_medium():
    system = _sys(op.free_space(0.2), op.thin_lens(0.1), op.free_space(0.05),
                  op.thick_lens(1.5, 0.1, -0.1, 0.01), op.curved_mirror(0.4))
    assert system.determinant == pytest.approx(1.0)
    assert system.determinant_ok()


def test_determinant_equals_index_ratio_across_interface():
    m = op.refraction_interface(1.0, 1.5, 0.1)
    system = op.OpticalSystem([op.OpticalElement("surf", m)], n_in=1.0, n_out=1.5)
    assert system.determinant == pytest.approx(1.0 / 1.5)
    assert system.determinant_ok()


def test_element_order_matters_M_equals_Mn_to_M1():
    a, b = op.free_space(0.3), op.thin_lens(0.1)
    system = _sys(a, b)
    assert np.allclose(system.matrix, b @ a)
    assert not np.allclose(system.matrix, a @ b)


def test_two_thin_lenses_in_contact_add_power():
    f1, f2 = 0.2, -0.5
    system = _sys(op.thin_lens(f1), op.thin_lens(f2))
    assert 1.0 / system.efl == pytest.approx(1.0 / f1 + 1.0 / f2)


def test_separated_thin_lenses_follow_gullstrand():
    f1, f2, d = 0.15, 0.3, 0.08
    system = _sys(op.thin_lens(f1), op.free_space(d), op.thin_lens(f2))
    assert 1.0 / system.efl == pytest.approx(1.0 / f1 + 1.0 / f2 - d / (f1 * f2))
    # BFL measured from the second lens
    assert system.bfl == pytest.approx(system.efl * (1.0 - d / f1))
    assert system.ffl == pytest.approx(system.efl * (1.0 - d / f2))


def test_thin_lens_cardinal_points():
    system = _sys(op.thin_lens(0.25))
    assert system.efl == pytest.approx(0.25)
    assert system.bfl == pytest.approx(0.25)
    assert system.ffl == pytest.approx(0.25)
    p, pp = system.principal_planes()
    assert p == pytest.approx(0.0, abs=1e-12) and pp == pytest.approx(0.0, abs=1e-12)


def test_ray_trace_reports_heights_and_vignetting():
    system = op.OpticalSystem([
        op.OpticalElement("L1", op.thin_lens(0.1), aperture_radius=0.01),
        op.OpticalElement("gap", op.free_space(0.05)),
        op.OpticalElement("L2", op.thin_lens(0.1), aperture_radius=0.004),
    ])
    states = system.trace_ray(0.008, 0.0)
    assert [s.name for s in states] == ["L1", "gap", "L2"]
    assert states[0].y_in == pytest.approx(0.008)
    assert states[2].y_in == pytest.approx(0.008 - 0.05 * 0.008 / 0.1)  # 0.004
    assert not states[0].clipped
    assert system.vignetted(0.012)           # L1 clips
    assert not system.vignetted(0.003)


# ---------------------------------------------------------------- lensmaker

def test_thin_lensmaker_and_thick_matrix_consistency():
    n, R1, R2 = 1.5, 0.1, -0.1
    assert op.thin_lens_focal_length(n, R1, R2) == pytest.approx(0.1)
    assert op.thick_lens_focal_length(n, R1, R2, 0.0) == pytest.approx(0.1)


@pytest.mark.parametrize("n,R1,R2,d", [(1.5, 0.1, -0.1, 0.02), (1.7, 0.08, 0.3, 0.015),
                                       (1.5, 0.2, math.inf, 0.01)])
def test_thick_lens_matrix_efl_equals_thick_lensmaker(n, R1, R2, d):
    system = _sys(op.thick_lens(n, R1, R2, d))
    assert system.efl == pytest.approx(op.thick_lens_focal_length(n, R1, R2, d), rel=1e-12)
    assert system.determinant_ok()


def test_thick_lens_principal_planes_inside_glass_for_biconvex():
    d = 0.02
    system = _sys(op.thick_lens(1.5, 0.1, -0.1, d))
    p, pp = system.principal_planes()
    # symmetric lens: P behind first surface (inside), P' in front of last surface
    assert 0.0 < p < d
    assert -d < pp < 0.0
    assert p == pytest.approx(-pp)


def test_curved_mirror_focal_length_is_half_radius():
    assert _sys(op.curved_mirror(0.6)).efl == pytest.approx(0.3)


# ---------------------------------------------------------------- expanders

@pytest.mark.parametrize("builder,expected_m", [
    (lambda: op.keplerian_expander(0.05, 0.25), 5.0),
    (lambda: op.galilean_expander(-0.05, 0.25), 5.0),
    (lambda: op.cassegrain_expander(0.3, -0.075), 4.0),
])
def test_afocal_expanders_reduce_divergence_by_magnification(builder, expected_m):
    system = builder()
    assert system.is_afocal and math.isinf(system.efl)
    assert abs(system.matrix[1, 0]) < 1e-12
    assert abs(system.linear_magnification) == pytest.approx(expected_m)

    w0 = 2e-3
    res = op.beam_through_system(w0, LAM, system, 0.0)
    assert res["w0_out"] == pytest.approx(expected_m * w0, rel=1e-9)          # diameter x M
    assert res["divergence"] == pytest.approx(op.far_field_divergence(w0, LAM) / expected_m, rel=1e-9)


def test_keplerian_inverts_and_galilean_keeps_orientation():
    assert op.keplerian_expander().linear_magnification < 0
    assert op.galilean_expander().linear_magnification > 0


def test_cassegrain_telescope_efl_matches_two_mirror_formula():
    f1, f2, d = 0.4, -0.1, 0.32
    system = op.cassegrain_telescope(f1, f2, d)
    assert system.efl == pytest.approx(f1 * f2 / (f1 + f2 - d))
    assert system.efl > 0
    assert system.determinant_ok()


# ---------------------------------------------------------------- Gaussian beams

def test_gaussian_free_space_matches_analytic():
    w0 = 1e-3
    zr = op.rayleigh_range(w0, LAM)
    assert zr == pytest.approx(math.pi * w0 ** 2 / LAM)
    q0 = op.q_from_waist(w0, LAM)
    for z in (0.0, zr, 3 * zr, 1000.0):
        q = op.propagate_q(q0, op.free_space(z))
        w_exp = w0 * math.sqrt(1 + (z / zr) ** 2)
        assert op.q_beam_radius(q, LAM) == pytest.approx(w_exp, rel=1e-9)
        assert op.beam_radius(z, w0, LAM) == pytest.approx(w_exp, rel=1e-12)
        if z > 0:
            assert op.q_radius_of_curvature(q) == pytest.approx(z * (1 + (zr / z) ** 2))
    assert math.isinf(op.radius_of_curvature(0.0, w0, LAM))
    assert op.beam_radius(zr, w0, LAM) == pytest.approx(w0 * math.sqrt(2))


def test_m2_enlarges_divergence_and_shrinks_rayleigh_range():
    w0 = 1e-3
    assert op.far_field_divergence(w0, LAM, 2.0) == pytest.approx(2 * LAM / (math.pi * w0))
    assert op.rayleigh_range(w0, LAM, 2.0) == pytest.approx(op.rayleigh_range(w0, LAM) / 2)
    res = op.beam_through_system(w0, LAM, op.OpticalSystem([]), 500.0, m2=2.0)
    assert res["w"] == pytest.approx(op.beam_radius(500.0, w0, LAM, 2.0))


def test_waist_imaging_by_thin_lens_matches_standard_formula():
    w0, f, s = 0.5e-3, 0.2, 0.5
    zr = op.rayleigh_range(w0, LAM)
    assert s > f
    system = op.OpticalSystem([op.OpticalElement("L", op.thin_lens(f))])
    res = op.beam_through_system(w0, LAM, system, 0.0, z_before=s)
    # standard result: s' = f + f^2 (s-f) / ((s-f)^2 + zR^2), w0' = w0 / sqrt((1-s/f)^2 + (zR/f)^2)
    s_img = f + f * f * (s - f) / ((s - f) ** 2 + zr ** 2)
    w_img = w0 / math.sqrt((1 - s / f) ** 2 + (zr / f) ** 2)
    assert res["z_waist_out"] == pytest.approx(s_img, rel=1e-9)
    assert res["w0_out"] == pytest.approx(w_img, rel=1e-9)


def test_collimator_with_source_at_focus():
    w0 = 5.2e-6
    f = 0.05
    system = op.collimator(f)
    res = op.beam_through_system(w0, LAM, system, 0.0, z_before=f)
    assert res["w0_out"] == pytest.approx(LAM * f / (math.pi * w0), rel=1e-9)
    assert res["divergence"] == pytest.approx(w0 / f, rel=1e-9)


def test_aperture_clipping_is_reported():
    big = op.OpticalSystem([op.OpticalElement("L", op.thin_lens(1.0), aperture_radius=1.0)])
    small = op.OpticalSystem([op.OpticalElement("L", op.thin_lens(1.0), aperture_radius=1e-3)])
    assert op.beam_through_system(2e-3, LAM, big, 0.0)["clipping_transmission"] == pytest.approx(1.0)
    expected = 1 - math.exp(-2 * (1e-3 / 2e-3) ** 2)
    assert op.beam_through_system(2e-3, LAM, small, 0.0)["clipping_transmission"] == pytest.approx(expected)


# ---------------------------------------------------------------- link helpers

def test_received_power_fraction_limits():
    assert op.received_power_fraction(0.01, 1e6) == pytest.approx(1.0)
    assert op.received_power_fraction(0.01, math.inf) == 1.0
    w = 0.05
    assert op.received_power_fraction(w, w / math.sqrt(2)) == pytest.approx(1 - math.exp(-1))
    assert op.received_power_fraction(w, 1e-9) == pytest.approx(0.0, abs=1e-12)


def test_geometric_loss_grows_with_distance():
    near = op.geometric_loss_dB(2e-3, LAM, 500.0, 0.05)
    far = op.geometric_loss_dB(2e-3, LAM, 5000.0, 0.05)
    assert 0.0 <= near < far


def test_fresnel_loss_per_surface_for_glass():
    assert op.fresnel_reflectance(1.0, 1.5) == pytest.approx(0.04)
    assert op.surface_transmittance(1.0, 1.5) == pytest.approx(0.96)
    assert op.lens_transmittance(1.5, 2) == pytest.approx(0.96 ** 2)
    assert op.lens_transmittance(1.5, 4, ar_reflectance=0.005) == pytest.approx(0.995 ** 4)


@pytest.mark.parametrize("name", ["collimating", "keplerian_telescope",
                                  "galilean_telescope", "cassegrain"])
@pytest.mark.parametrize("distance", [1000.0, 5000.0])
def test_beam_expanders_give_positive_link_gain(name, distance):
    assert op.lens_link_gain_dB(name, distance) > 0.0


def test_diverging_lens_hurts_link_and_none_is_neutral():
    assert op.lens_link_gain_dB("diverging", 1000.0) < 0.0
    assert op.lens_link_gain_dB("none", 1000.0) == pytest.approx(0.0, abs=1e-12)


def test_receiver_lens_gain_scales_with_collecting_area():
    gain = op.lens_link_gain_dB("focusing", 1000.0, rx_radius=0.025)
    w = op.beam_radius(1000.0, 2e-3, LAM)
    t = op.lens_transmittance(1.5007, 2, ar_reflectance=0.005)
    expected = 10 * math.log10(op.received_power_fraction(w, 0.05) * t
                               / op.received_power_fraction(w, 0.025))
    assert gain == pytest.approx(expected, rel=1e-9)
    assert 5.0 < gain < 6.1                                  # ~ area ratio 4 -> 6 dB


def test_link_gain_includes_element_transmittance():
    ideal = op.build_preset("keplerian_telescope", ar_reflectance=0.0)
    bare = op.build_preset("keplerian_telescope", ar_reflectance=None)
    g_ideal = op.lens_link_gain_dB(ideal, 2000.0)
    g_bare = op.lens_link_gain_dB(bare, 2000.0)
    assert g_ideal - g_bare == pytest.approx(-10 * math.log10(0.96 ** 4), abs=0.05)


def test_presets_dict_and_overrides():
    assert {"none", "collimating", "focusing", "galilean_telescope",
            "keplerian_telescope", "cassegrain"} <= set(op.FSO_LENS_PRESETS)
    p = op.build_preset("galilean_telescope", f2=0.5)
    assert p.magnification == pytest.approx(10.0)
    assert p.system.determinant_ok()
    assert op.build_preset("cassegrain").magnification == pytest.approx(10.0)


# ---------------------------------------------------------------- aberration

def test_spherical_aberration_scaling_and_best_form():
    n, f = 1.5, 0.1
    lsa = op.thin_lens_longitudinal_spherical_aberration
    a1 = lsa(f, n, 0.005, shape_factor=0.0)
    a2 = lsa(f, n, 0.010, shape_factor=0.0)
    assert a1 < 0 and a2 == pytest.approx(4 * a1)            # ~ h^2
    q_best = 2 * (n * n - 1) / (n + 2)
    assert abs(lsa(f, n, 0.005, shape_factor=q_best)) < abs(a1)
    # equi-convex n=1.5: LSA = -(h^2/f) * 0.6667 ... via radii
    assert lsa(0, n, 0.002, R1=0.1, R2=-0.1) == pytest.approx(-6.667e-5, rel=1e-3)


def test_airy_radius():
    assert op.airy_radius(LAM, 0.5, 0.1) == pytest.approx(1.22 * LAM * 5.0)


# ---------------------------------------------------------------- validation

@pytest.mark.parametrize("call", [
    lambda: op.thin_lens(0.0),
    lambda: op.free_space(-1.0),
    lambda: op.thick_lens(1.5, 0.0, -0.1, 0.01),
    lambda: op.thick_lens(0.0, 0.1, -0.1, 0.01),
    lambda: op.curved_mirror(0.0),
    lambda: op.refraction_interface(1.0, -1.5, 0.1),
    lambda: op.rayleigh_range(1e-3, 0.0),
    lambda: op.rayleigh_range(-1e-3, LAM),
    lambda: op.far_field_divergence(1e-3, -LAM),
    lambda: op.beam_through_system(1e-3, LAM, op.OpticalSystem([]), -1.0),
    lambda: op.received_power_fraction(0.0, 0.1),
    lambda: op.received_power_fraction(0.1, -0.1),
    lambda: op.geometric_loss_dB(1e-3, LAM, -5.0, 0.05),
    lambda: op.lens_link_gain_dB("collimating", -10.0),
    lambda: op.lens_link_gain_dB("collimating", 100.0, wavelength=0.0),
    lambda: op.build_preset("warp_drive"),
    lambda: op.keplerian_expander(-0.05, 0.25),
    lambda: op.airy_radius(LAM, 0.1, 0.0),
    lambda: op.OpticalElement("x", np.eye(2), aperture_radius=-1.0),
    lambda: op.surface_transmittance(1.0, 1.5, ar_reflectance=1.5),
    lambda: op.thin_lens_focal_length(1.5, 0.0, 0.1),
])
def test_invalid_inputs_raise_value_error(call):
    with pytest.raises(ValueError):
        call()
