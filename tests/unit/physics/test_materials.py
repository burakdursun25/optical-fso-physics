import json
import math

import pytest

from backend import materials as mt
from backend import optics as op

UM = 1e-6

# (material, wavelength_um, published n, tolerance, source of the published value)
PUBLISHED = [
    ("n_bk7", 1.55, 1.5007, 5e-4, "Schott N-BK7 datasheet ~1.5007 @1550"),
    ("fused_silica", 1.55, 1.4440, 5e-4, "Corning/Malitson 1.4440 @1550"),
    ("caf2", 1.65, 1.4256, 5e-4, "Crystran table"),
    ("baf2", 1.5295, 1.4661, 5e-4, "Crystran table"),
    ("mgf2", 1.512, 1.370, 1.5e-3, "Crystran table (3 digits)"),
    ("sapphire", 1.55, 1.7462, 5e-4, "Crystran table"),
    ("znse", 1.40, 2.4609, 5e-4, "Crystran table"),
    ("silicon", 1.55, 3.4757, 5e-4, "Li (1993) table"),
    ("germanium", 3.0, 4.0442, 5e-4, "Li (1993) table"),
    ("n_sf11", 0.5875618, 1.78472, 5e-4, "Schott nd"),
    ("n_sf6", 0.5875618, 1.80518, 5e-4, "Schott nd"),
    ("s_lah64", 0.5875618, 1.788001, 5e-4, "Ohara nd"),
    ("pmma", 0.5875618, 1.4917, 2e-3, "typical nd of PMMA"),
    ("pmma", 1.55, 1.4771, 5e-3, "Zhang 2020 (Mitsubishi grade, different sample)"),
    ("polycarbonate", 1.55, 1.5633, 2e-3, "Zhang 2020 table"),
]


@pytest.mark.parametrize("key,lam_um,n_pub,tol,_src", PUBLISHED)
def test_index_matches_published(key, lam_um, n_pub, tol, _src):
    assert mt.refractive_index(key, lam_um * UM) == pytest.approx(n_pub, abs=tol)


def test_bk7_and_silica_at_1550():
    assert mt.refractive_index("n_bk7", 1550e-9) == pytest.approx(1.5007, abs=5e-4)
    assert mt.refractive_index("fused_silica", 1550e-9) == pytest.approx(1.4440, abs=5e-4)


def test_abbe_numbers_match_datasheets():
    assert mt.LENS_MATERIALS["n_bk7"]["abbe"] == pytest.approx(64.17, abs=0.1)
    assert mt.LENS_MATERIALS["n_sf11"]["abbe"] == pytest.approx(25.68, abs=0.1)
    assert mt.LENS_MATERIALS["fused_silica"]["abbe"] == pytest.approx(67.8, abs=0.3)
    assert mt.LENS_MATERIALS["caf2"]["abbe"] > 90


def test_errors():
    with pytest.raises(ValueError):
        mt.refractive_index("n_bk7", 3.0e-6)          # beyond Sellmeier range
    with pytest.raises(ValueError):
        mt.refractive_index("n_bk7", 0.2e-6)
    with pytest.raises(ValueError):
        mt.refractive_index("unobtainium", 1550e-9)
    with pytest.raises(ValueError):
        mt.refractive_index("n_bk7", -1.0)
    with pytest.raises(ValueError):
        mt.is_transparent("unobtainium", 1550e-9)
    with pytest.raises(ValueError):
        mt.refractive_index("germanium", 1550e-9)     # outside data and opaque


def test_opaque_materials_flagged():
    assert not mt.is_transparent("germanium", 1550e-9)
    assert not mt.is_transparent("germanium", 850e-9)
    assert not mt.is_transparent("silicon", 850e-9)
    assert mt.is_transparent("silicon", 1550e-9)
    assert mt.is_transparent("n_bk7", 1550e-9)
    assert mt.is_transparent("n_bk7", 850e-9)
    assert not mt.is_transparent("n_bk7", 3.0e-6)
    cat = mt.catalog()["materials"]
    assert cat["germanium"]["usable_fso_1550"] is False
    assert cat["n_bk7"]["usable_fso_1550"] is True
    with pytest.raises(ValueError):
        mt.design_lens("plano_convex", "germanium", 0.1, 0.025, 1550e-9)


def test_dispersion_helpers():
    # normal dispersion: dn/dlambda < 0, group index > phase index
    dn = mt.dn_dlambda("n_bk7", 1550e-9)
    assert dn < 0
    assert mt.group_index("n_bk7", 1550e-9) > mt.refractive_index("n_bk7", 1550e-9)
    # fused silica n_g(1550) ~ 1.4626 (Sellmeier-derived)
    assert mt.group_index("fused_silica", 1550e-9) == pytest.approx(1.4626, abs=1e-3)


def test_dn_dT_values():
    assert mt.dn_dT("caf2") == pytest.approx(-10.6e-6)
    assert mt.dn_dT("n_sf11") < 0 < mt.dn_dT("n_bk7")
    assert mt.dn_dT("fused_silica") is None


# ---------------------------------------------------------------- design_lens

def test_plano_convex_bk7_f100():
    d = mt.design_lens("plano_convex", "n_bk7", 0.1, 0.025, 1550e-9)
    n = mt.refractive_index("n_bk7", 1550e-9)
    assert d["R1_m"] == pytest.approx(0.1 * (n - 1), rel=1e-6)       # ~50.07 mm
    assert 0.049 < d["R1_m"] < 0.0525
    assert d["R2_m"] is None                                          # flat back
    assert d["edge_thickness_m"] > 0
    assert d["center_thickness_m"] > d["edge_thickness_m"]
    assert d["efl_check_m"] == pytest.approx(0.1, rel=1e-6)
    assert d["f_number"] == pytest.approx(4.0)
    assert d["NA"] == pytest.approx(math.sin(math.atan(0.125)))
    assert len(d["profile"]) >= 41
    assert d["profile"][0][0] == pytest.approx(-0.0125)
    assert d["profile"][-1][0] == pytest.approx(0.0125)
    # lens thicker at centre: vertex of the curved front at z=0, back flat
    mid = d["profile"][len(d["profile"]) // 2]
    assert mid[1] == pytest.approx(0.0, abs=1e-12)
    assert mid[2] == pytest.approx(d["center_thickness_m"])


def test_biconvex_symmetric_and_thick_lens_efl():
    d = mt.design_lens("biconvex", "n_bk7", 0.05, 0.025, 1550e-9)
    assert d["R1_m"] == pytest.approx(-d["R2_m"])
    n = d["n_used"]
    assert op.thick_lens_focal_length(n, d["R1_m"], d["R2_m"], d["center_thickness_m"]) == \
        pytest.approx(0.05, rel=1e-6)
    # thick correction: |R| slightly different from thin-lens 2f(n-1)
    assert d["R1_m"] != pytest.approx(2 * 0.05 * (n - 1), rel=1e-9)


@pytest.mark.parametrize("shape", ["plano_convex_flat_front", "positive_meniscus", "aspheric",
                                   "cylindrical", "biconvex"])
def test_positive_shapes_hit_target_efl(shape):
    d = mt.design_lens(shape, "fused_silica", 0.15, 0.03, 1550e-9)
    assert d["efl_check_m"] == pytest.approx(0.15, rel=1e-5)
    assert d["edge_thickness_m"] > 0


@pytest.mark.parametrize("shape", ["plano_concave", "biconcave", "negative_meniscus"])
def test_negative_shapes(shape):
    d = mt.design_lens(shape, "n_bk7", 0.1, 0.025, 1550e-9)
    assert d["focal_length_m"] == pytest.approx(-0.1)
    assert d["efl_check_m"] == pytest.approx(-0.1, rel=1e-5)
    assert d["edge_thickness_m"] > d["center_thickness_m"] > 0


def test_meniscus_radii_same_sign():
    d = mt.design_lens("positive_meniscus", "n_bk7", 0.1, 0.025, 1550e-9)
    assert d["R1_m"] > 0 and d["R2_m"] > 0 and d["R2_m"] > d["R1_m"]


def test_aspheric_conic():
    d = mt.design_lens("aspheric", "n_bk7", 0.1, 0.025, 1550e-9)
    assert d["conic_k2"] == pytest.approx(-d["n_used"] ** 2)


def test_ball_lens():
    d = mt.design_lens("ball", "fused_silica", 0.005, 0.01, 1550e-9)
    n, R = d["n_used"], d["R1_m"]
    assert 0.005 == pytest.approx(n * R / (2 * (n - 1)), rel=1e-9)
    assert d["center_thickness_m"] == pytest.approx(2 * R)


def test_fresnel_profile_positive_thickness():
    d = mt.design_lens("fresnel", "pmma", 0.1, 0.05, 850e-9)
    assert all(zb - zf > 0 for _y, zf, zb in d["profile"])


def test_design_errors():
    with pytest.raises(ValueError):
        mt.design_lens("achromatic_doublet", "n_bk7", 0.1, 0.025, 1550e-9)
    with pytest.raises(ValueError):
        mt.design_lens("nope", "n_bk7", 0.1, 0.025, 1550e-9)
    with pytest.raises(ValueError):
        mt.design_lens("plano_convex", "n_bk7", -0.1, 0.025, 1550e-9)
    with pytest.raises(ValueError):                       # D/2 > R: impossible surface
        mt.design_lens("plano_convex", "n_bk7", 0.01, 0.05, 1550e-9)
    with pytest.raises(ValueError):                       # 1550 nm outside PMMA formula? no: 3 um is
        mt.design_lens("plano_convex", "n_bk7", 0.1, 0.025, 3.0e-6)


def test_transmittance_with_coating():
    bare = mt.design_lens("plano_convex", "n_bk7", 0.1, 0.025, 1550e-9)
    ar = mt.design_lens("plano_convex", "n_bk7", 0.1, 0.025, 1550e-9, coating="v_coat_1550")
    n = bare["n_used"]
    assert bare["transmittance"] == pytest.approx(
        op.lens_transmittance(n, 2) * math.exp(-8.08e-3 * bare["center_thickness_m"] * 100), rel=1e-6)
    assert ar["transmittance"] > bare["transmittance"]
    assert ar["surface_reflectance"] == pytest.approx(0.0025)
    assert ar["transmittance"] == pytest.approx(0.9975 ** 2, abs=3e-3)


def test_coating_reflectance_model():
    n = 1.5007
    assert mt.coating_reflectance("none", 1550e-9, n) is None
    assert mt.coating_reflectance("v_coat_1550", 1550e-9, n) == pytest.approx(0.0025)
    off = mt.coating_reflectance("v_coat_1550", 1300e-9, n)
    assert off > 0.0025 and off <= op.fresnel_reflectance(1.0, n) + 1e-12
    assert mt.coating_reflectance("bbar_swir_900_1700", 1200e-9, n) == pytest.approx(0.01)
    assert mt.coating_reflectance("bbar_swir_900_1700", 400e-9, n) == pytest.approx(
        op.fresnel_reflectance(1.0, n))
    with pytest.raises(ValueError):
        mt.coating_reflectance("zzz", 1550e-9, n)


# ---------------------------------------------------------------- filters

def test_bandpass_filter():
    f = mt.FILTERS["bp_1550_10nm"]
    peak = mt.filter_transmittance("bp_1550_10nm", 1550e-9)
    assert peak == pytest.approx(f["peak_T"])
    half = mt.filter_transmittance("bp_1550_10nm", (1550 + 5) * 1e-9)
    assert half == pytest.approx(peak / 2, rel=1e-6)                  # FWHM definition
    for sign in (+1, -1):
        far = mt.filter_transmittance("bp_1550_10nm", (1550 + sign * 3 * 10) * 1e-9)
        assert far < 1e-3 * peak + 1e-4
        assert far >= 10 ** (-f["od"])
    assert mt.filter_transmittance("bp_850_10nm", 1550e-9) == pytest.approx(1e-4)


def test_edge_and_nd_filters():
    assert mt.filter_transmittance("lp_1400", 1550e-9) == pytest.approx(0.90, abs=1e-3)
    assert mt.filter_transmittance("lp_1400", 1000e-9) == pytest.approx(1e-4)
    assert mt.filter_transmittance("sp_1000", 850e-9) == pytest.approx(0.90, abs=1e-3)
    assert mt.filter_transmittance("sp_1000", 1550e-9) == pytest.approx(1e-4)
    assert mt.filter_transmittance("nd_od2", 1550e-9) == pytest.approx(0.01)
    assert mt.filter_transmittance("nd_od1", 850e-9) == pytest.approx(0.1)
    with pytest.raises(ValueError):
        mt.filter_transmittance("nope", 1550e-9)


def test_telescopes():
    k = mt.telescope_parameters("keplerian", 0.05, 0.25)
    assert k["spacing_m"] == pytest.approx(0.30) and k["magnification"] == pytest.approx(-5.0)
    g = mt.telescope_parameters("galilean", -0.05, 0.25)
    assert g["spacing_m"] == pytest.approx(0.20) and g["magnification"] == pytest.approx(5.0)
    with pytest.raises(ValueError):
        mt.telescope_parameters("galilean", 0.05, 0.25)
    assert {"cassegrain", "ritchey_chretien"} <= set(mt.TELESCOPES)


def test_catalog_json_serializable():
    cat = mt.catalog()
    s = json.dumps(cat)
    back = json.loads(s)
    assert {"materials", "shapes", "telescopes", "filters", "coatings"} <= set(back)
    assert back["materials"]["n_bk7"]["n"]["1550"] == pytest.approx(1.5007, abs=5e-4)
    assert back["materials"]["germanium"]["n"]["1550"] is None
    assert len(back["materials"]) >= 12
    for m in back["materials"].values():
        assert m["source"] and m["cost"] in ("düşük", "orta", "yüksek")
