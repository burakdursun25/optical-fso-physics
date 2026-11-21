"""Lens materials, lens shapes, filters and AR coatings for the FSO simulator.

All dispersion data come from published sources (see ``source`` fields and
reports/lens_materials_research.md); nothing here is invented.  Wavelengths
passed to the public functions are in METRES; internal dispersion formulas use
micrometres as in the original references.

Sign convention for surface radii (paraxial, light travelling +z):
R > 0 -> centre of curvature on the +z side (surface convex toward the
source); R < 0 -> centre on the -z side; ``None``/inf -> flat.
Shape factor  q = (R2 + R1) / (R2 - R1):  biconvex symmetric q = 0,
plano-convex curved-side-first q = +1, flat-side-first q = -1.
Given f, n and q:  R1 = 2 f (n-1) / (q+1),  R2 = 2 f (n-1) / (q-1).

Filter and coating spectra are SIMPLE MODELS (super-Gaussian bandpass, smooth
edge filters, parabolic V-coat) calibrated on vendor datasheet headline
numbers; they are not measured curves.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import optics
from .numerics import central_diff

# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------
# formula kinds
#   "sellmeier_sq"  : n^2 = 1 + sum B_i l^2/(l^2 - C_i^2)   (C_i in um; refractiveindex.info "formula 1")
#   "sellmeier"     : n^2 = 1 + K + sum B_i l^2/(l^2 - C_i) (C_i in um^2; "formula 2", coefficients [K, B1, C1, ...])
#   "polynomial_n2" : n^2 = sum c_i l^p_i ("formula 3", coefficients [c0, c1, p1, c2, p2, ...])
#   "cauchy_n2"     : n^2 = a0 + a1 l^-2 + a2 l^-4 (fit to tabulated data)
#   "tabulated"     : linear interpolation of tabulated n(l) [[l_um, n], ...]

_SI_LI_TABLE = [[1.2, 3.5167], [1.22, 3.5133], [1.24, 3.5102], [1.26, 3.5072], [1.28, 3.5043],
                [1.3, 3.5016], [1.32, 3.499], [1.34, 3.4965], [1.36, 3.4941], [1.38, 3.4918],
                [1.4, 3.4896], [1.45, 3.4845], [1.5, 3.4799], [1.55, 3.4757], [1.6, 3.4719],
                [1.65, 3.4684], [1.7, 3.4653], [1.8, 3.4597], [1.9, 3.455], [2.0, 3.451],
                [2.25, 3.4431], [2.5, 3.4375], [2.75, 3.4334], [3.0, 3.4302], [4.0, 3.4229],
                [5.0, 3.4195], [6.0, 3.4177], [7.0, 3.4165], [8.0, 3.4158], [9.0, 3.4153],
                [10.0, 3.415], [11.0, 3.4147], [12.0, 3.4145], [13.0, 3.4144], [14.0, 3.4142]]

_RII = "https://refractiveindex.info"
_DB = "https://github.com/polyanskiy/refractiveindex.info-database/blob/main/database/data/"

LENS_MATERIALS: Dict[str, dict] = {
    "n_bk7": {
        "name": "N-BK7 (Schott)",
        "formula": "sellmeier",
        "coefficients": [0.0, 1.03961212, 0.00600069867, 0.231792344, 0.0200179144,
                         1.01046945, 103.560653],
        "range_um": (0.3, 2.5),
        "transmission_um": (0.35, 2.0),
        "density_g_cm3": 2.51,
        "cte_per_K": 7.1e-6,
        "thermal_schott": (1.86e-6, 1.31e-8, -1.37e-11, 4.34e-7, 6.27e-10, 0.17),
        "dn_dT_per_K": None,   # filled below from Schott thermal formula
        "alpha_1550_per_cm": 8.08e-3,   # from Schott k(1.53 um) = 9.84e-8
        "notes_tr": "Standart borosilikat taç cam; 1550 nm için en ucuz ve yaygın seçenek.",
        "cost": "düşük",
        "source": _DB + "specs/schott/optical/N-BK7.yml (SCHOTT Zemax catalog 2017-01-20b); "
                  "Thorlabs transmission 350 nm-2.0 um",
    },
    "fused_silica": {
        "name": "UV Ergimiş Silika (Corning 7980 / Malitson)",
        "formula": "sellmeier_sq",
        "coefficients": [0.0, 0.6961663, 0.0684043, 0.4079426, 0.1162414, 0.8974794, 9.896161],
        "range_um": (0.21, 6.7),
        "transmission_um": (0.185, 2.1),
        "density_g_cm3": 2.20,
        "cte_per_K": 0.52e-6,
        "dn_dT_per_K": None,
        "alpha_1550_per_cm": None,
        "notes_tr": "Çok düşük genleşme; termal kararlı, UV-NIR geçirgen. OH içeriği 1380 nm civarında soğurma yapar.",
        "cost": "orta",
        "source": "I. H. Malitson, JOSA 55, 1205 (1965) via " + _DB + "main/SiO2/nk/Malitson.yml; "
                  "Corning HPFS 7980 data (density 2.20, CTE 0.52e-6/K)",
    },
    "caf2": {
        "name": "Kalsiyum Florür (CaF2)",
        "formula": "sellmeier_sq",
        "coefficients": [0.0, 0.5675888, 0.050263605, 0.4710914, 0.1003909, 3.8484723, 34.649040],
        "range_um": (0.23, 9.7),
        "transmission_um": (0.13, 10.0),
        "density_g_cm3": 3.18,
        "cte_per_K": 18.85e-6,
        "dn_dT_per_K": -10.6e-6,
        "alpha_1550_per_cm": None,
        "notes_tr": "Düşük dağılım (Abbe ~95), geniş UV-MIR bandı; termal şoka ve çizilmeye duyarlı.",
        "cost": "yüksek",
        "source": "I. H. Malitson, Appl. Opt. 2, 1103 (1963) via " + _DB + "main/CaF2/nk/Malitson.yml; "
                  "https://www.crystran.com/optical-materials/calcium-fluoride-caf2",
    },
    "baf2": {
        "name": "Baryum Florür (BaF2)",
        "formula": "sellmeier_sq",
        "coefficients": [0.0, 0.643356, 0.057789, 0.506762, 0.10968, 3.8261, 46.3864],
        "range_um": (0.2652, 10.346),
        "transmission_um": (0.15, 12.0),
        "density_g_cm3": 4.89,
        "cte_per_K": 18.1e-6,
        "dn_dT_per_K": -15.2e-6,
        "alpha_1550_per_cm": None,
        "notes_tr": "Orta-IR (8-12 um) pencereleri; nem ve darbeye duyarlı, FSO 1550 nm için gereksiz pahalı.",
        "cost": "yüksek",
        "source": "I. H. Malitson, JOSA 54, 628 (1964) via " + _DB + "main/BaF2/nk/Malitson.yml; "
                  "https://www.crystran.com/optical-materials/barium-fluoride-baf2",
    },
    "mgf2": {
        "name": "Magnezyum Florür (MgF2, sıradan ışın)",
        "formula": "sellmeier_sq",
        "coefficients": [0.0, 0.48755108, 0.04338408, 0.39875031, 0.09461442, 2.3120353, 23.793604],
        "range_um": (0.2, 7.0),
        "transmission_um": (0.12, 7.0),
        "density_g_cm3": 3.18,
        "cte_per_K": 8.9e-6,
        "dn_dT_per_K": 1.7e-6,   # perpendicular to c-axis, quoted at 0.4 um
        "alpha_1550_per_cm": None,
        "notes_tr": "Çift kırıcı (Δn~0.011); düşük indeks, AR kaplama/pencere olarak. Sıradan ışın verisi.",
        "cost": "yüksek",
        "source": "M. J. Dodge, Appl. Opt. 23, 1980 (1984) via " + _DB + "main/MgF2/nk/Dodge-o.yml; "
                  "https://www.crystran.com/optical-materials/magnesium-fluoride-mgf2",
    },
    "sapphire": {
        "name": "Safir (Al2O3, sıradan ışın)",
        "formula": "sellmeier_sq",
        "coefficients": [0.0, 1.4313493, 0.0726631, 0.65054713, 0.1193242, 5.3414021, 18.028251],
        "range_um": (0.2, 5.0),
        "transmission_um": (0.17, 5.5),
        "density_g_cm3": 3.97,
        "cte_per_K": 5.3e-6,
        "dn_dT_per_K": 13.1e-6,   # at 0.546 um
        "alpha_1550_per_cm": None,
        "notes_tr": "Çok sert ve dayanıklı (Knoop ~2000); açık hava/pencere optikleri için. Çift kırıcı.",
        "cost": "yüksek",
        "source": "Malitson & Dodge (1972) via " + _DB + "main/Al2O3/nk/Malitson-o.yml; "
                  "https://www.crystran.com/optical-materials/sapphire-al2o3",
    },
    "n_sf11": {
        "name": "N-SF11 (Schott, yüksek indeksli flint)",
        "formula": "sellmeier",
        "coefficients": [0.0, 1.73759695, 0.013188707, 0.313747346, 0.0623068142,
                         1.89878101, 155.23629],
        "range_um": (0.37, 2.5),
        "transmission_um": (0.40, 2.5),
        "density_g_cm3": 3.224,
        "cte_per_K": 8.52e-6,
        "thermal_schott": (-3.56e-6, 9.2e-9, -2.1e-11, 9.65e-7, 1.44e-9, 0.294),
        "dn_dT_per_K": None,
        "alpha_1550_per_cm": 6.05e-3,   # k(1.53 um) = 7.36e-8
        "notes_tr": "Yüksek indeks (nd=1.785) -> daha az eğri yüzey; yüksek dağılım (Abbe 25.7), yüksek fiyat.",
        "cost": "orta",
        "source": _DB + "specs/schott/optical/N-SF11.yml (SCHOTT Zemax catalog 2017-01-20b)",
    },
    "n_sf6": {
        "name": "N-SF6 (Schott, yüksek indeksli flint)",
        "formula": "sellmeier",
        "coefficients": [0.0, 1.77931763, 0.0133714182, 0.338149866, 0.0617533621,
                         2.08734474, 174.01759],
        "range_um": (0.37, 2.5),
        "transmission_um": (0.40, 2.5),
        "density_g_cm3": 3.369,
        "cte_per_K": 9.03e-6,
        "thermal_schott": (-4.93e-6, 7.02e-9, -2.4e-11, 9.84e-7, 1.54e-9, 0.29),
        "dn_dT_per_K": None,
        "alpha_1550_per_cm": 8.9e-3,    # k(1.53 um) = 1.08e-7
        "notes_tr": "N-SF11'e benzer yüksek indeksli flint (nd=1.805); menisküs/kısa odaklı lensler için.",
        "cost": "orta",
        "source": _DB + "specs/schott/optical/N-SF6.yml (SCHOTT Zemax catalog 2017-01-20b)",
    },
    "s_lah64": {
        "name": "S-LAH64 (Ohara, lantan ağır flint)",
        "formula": "sellmeier",
        "coefficients": [0.0, 1.83021453, 0.0090482329, 0.29156359, 0.0330756689,
                         1.28544024, 89.3675501],
        "range_um": (0.32, 2.4),
        "transmission_um": (0.38, 2.4),
        "density_g_cm3": 4.30,
        "cte_per_K": 6.1e-6,
        "thermal_schott": (3.03e-6, 1.2e-8, -3.49e-11, 4.09e-7, 4.61e-10, 0.277),
        "dn_dT_per_K": None,
        "alpha_1550_per_cm": 7.0e-3,    # k(1.6 um) = 8.94e-8
        "notes_tr": "Yüksek indeks + orta dağılım (Abbe 47.4); akromat çiftlerinde yaygın.",
        "cost": "yüksek",
        "source": _DB + "specs/ohara/optical/S-LAH64.yml (OHARA Zemax catalog 2017-11-30)",
    },
    "znse": {
        "name": "Çinko Selenür (CVD ZnSe)",
        "formula": "sellmeier_sq",
        "coefficients": [0.0, 4.45813734, 0.200859853, 0.467216334, 0.391371166,
                         2.89566290, 47.1362108],
        "range_um": (0.54, 18.2),
        "transmission_um": (0.6, 21.0),
        "density_g_cm3": 5.27,
        "cte_per_K": 7.1e-6,
        "dn_dT_per_K": 61e-6,   # quoted at 10.6 um
        "alpha_1550_per_cm": 5e-3,   # 0.005 /cm at 1.3 um (Crystran)
        "notes_tr": "CO2 lazer / MIR optiği; 1550 nm'de geçirgen ama n=2.46 -> yüksek Fresnel kaybı (AR şart), pahalı.",
        "cost": "yüksek",
        "source": "Connolly et al., SPIE 181 (1979) / Tatian, Appl. Opt. 23, 4477 (1984) via "
                  + _DB + "main/ZnSe/nk/Connolly.yml; https://www.crystran.com/optical-materials/zinc-selenide-znse",
    },
    "germanium": {
        "name": "Germanyum (Ge) — 1.8 um altında OPAK",
        "formula": "sellmeier",
        "coefficients": [8.28156, 6.72880, 0.44105, 0.21307, 3870.1],
        "range_um": (2.5, 12.0),
        "transmission_um": (1.8, 23.0),
        "density_g_cm3": 5.33,
        "cte_per_K": 6.1e-6,
        "dn_dT_per_K": 396e-6,
        "alpha_1550_per_cm": None,
        "notes_tr": "UYARI: 1.8 um'den kısa dalga boylarında opak; 850/1310/1550 nm FSO için KULLANILAMAZ. Yalnızca 3-5/8-14 um termal görüntüleme.",
        "cost": "yüksek",
        "source": "Icenogle, Platt, Wolfe (1976) / Barnes & Piltch (1979) via "
                  + _DB + "main/Ge/nk/Icenogle.yml; https://www.crystran.com/optical-materials/germanium-ge",
    },
    "silicon": {
        "name": "Silisyum (Si) — 1.2 um altında OPAK",
        "formula": "tabulated",
        "coefficients": _SI_LI_TABLE,
        "range_um": (1.2, 14.0),
        "transmission_um": (1.2, 15.0),
        "density_g_cm3": 2.33,
        "cte_per_K": 2.6e-6,
        "dn_dT_per_K": 160e-6,
        "alpha_1550_per_cm": None,
        "notes_tr": "UYARI: ~1.1 um altında opak (850 nm için kullanılamaz). 1550 nm'de geçirgen, n=3.476 -> AR şart; 3-5 um pencereleri.",
        "cost": "orta",
        "source": "H. H. Li, J. Phys. Chem. Ref. Data 9, 561 (1980/1993 ed.) via "
                  + _DB + "main/Si/nk/Li-293K.yml; https://www.crystran.com/optical-materials/silicon-si",
    },
    "pmma": {
        "name": "PMMA (akrilik)",
        "formula": "polynomial_n2",
        "coefficients": [2.1778, 6.1209e-3, 2, -1.5004e-3, 4, 2.3678e-2, -2, -4.2137e-3, -4,
                         7.3417e-4, -6, -4.5042e-5, -8],
        "range_um": (0.42, 1.62),
        "transmission_um": (0.38, 1.6),
        "density_g_cm3": 1.18,
        "cte_per_K": None,
        "dn_dT_per_K": None,
        "alpha_1550_per_cm": 0.275,   # from k = 3.39e-6 at 1.55 um (Zhang 2020, Mitsubishi PMMA)
        "notes_tr": "Ucuz, hafif, kalıplanabilir (Fresnel/asferik); 1550 nm'de ~1.2 dB/cm soğurma, termal duyarlı. 850 nm için uygun.",
        "cost": "düşük",
        "source": "G. Beadie et al., Appl. Opt. 54, F139 (2015) via "
                  + _DB + "organic/(C5H8O2)n - poly(methyl methacrylate)/nk/Beadie.yml; "
                  "absorption: Zhang et al., Appl. Opt. 59, 2337 (2020)",
    },
    "polycarbonate": {
        "name": "Polikarbonat (PC)",
        "formula": "cauchy_n2",
        "coefficients": [2.42182174, 0.05309076, -0.00674124],
        "range_um": (0.7, 1.65),
        "transmission_um": (0.40, 1.6),
        "density_g_cm3": 1.20,
        "cte_per_K": None,
        "dn_dT_per_K": None,
        "alpha_1550_per_cm": 0.142,   # from k = 1.75e-6 at 1.55 um (Zhang 2020)
        "notes_tr": "Darbe dayanımlı polimer; n~1.56, yüksek dağılım. Katsayılar Zhang (2020) ölçümüne yapılan Cauchy uyumudur (hata <=1.5e-3).",
        "cost": "düşük",
        "source": "Fit (this module) to X. Zhang et al., Appl. Opt. 59, 2337 (2020) tabulated data, "
                  + _DB + "organic/(C16H14O3)n - polycarbonate/nk/Zhang.yml",
    },
}

# Materials flagged unusable at the common FSO wavelengths (absorption edge)
OPAQUE_BELOW_UM = {"germanium": 1.8, "silicon": 1.1}


def _check_material(material_key: str) -> dict:
    try:
        return LENS_MATERIALS[material_key]
    except KeyError:
        raise ValueError(f"unknown lens material: {material_key!r}") from None


def _n_um(mat: dict, lam_um: float) -> float:
    kind = mat["formula"]
    c = mat["coefficients"]
    l2 = lam_um * lam_um
    if kind == "sellmeier":
        # [K, B1, C1, B2, C2, ...]  C in um^2
        n2 = 1.0 + c[0] + sum(c[i] * l2 / (l2 - c[i + 1]) for i in range(1, len(c), 2))
    elif kind == "sellmeier_sq":
        n2 = 1.0 + c[0] + sum(c[i] * l2 / (l2 - c[i + 1] ** 2) for i in range(1, len(c), 2))
    elif kind == "polynomial_n2":
        n2 = c[0] + sum(c[i] * lam_um ** c[i + 1] for i in range(1, len(c), 2))
    elif kind == "cauchy_n2":
        n2 = c[0] + c[1] * lam_um ** -2 + c[2] * lam_um ** -4
    elif kind == "tabulated":
        xs = [r[0] for r in c]
        ys = [r[1] for r in c]
        return float(np.interp(lam_um, xs, ys))
    else:  # pragma: no cover
        raise ValueError(f"unsupported formula {kind}")
    return math.sqrt(n2)


def refractive_index(material_key: str, wavelength_m: float) -> float:
    """n(lambda) at ~20 degC.  ValueError for unknown material or lambda outside
    the dispersion formula's validated range ``range_um``."""
    mat = _check_material(material_key)
    lam = float(wavelength_m)
    if not math.isfinite(lam) or lam <= 0:
        raise ValueError("wavelength must be positive and finite")
    lam_um = lam * 1e6
    lo, hi = mat["range_um"]
    if not (lo <= lam_um <= hi):
        raise ValueError(f"{material_key}: wavelength {lam_um:.4g} um outside valid range [{lo}, {hi}] um")
    return _n_um(mat, lam_um)


def is_transparent(material_key: str, wavelength_m: float) -> bool:
    """True when lambda is inside the material's transmission band."""
    mat = _check_material(material_key)
    lam_um = float(wavelength_m) * 1e6
    lo, hi = mat["transmission_um"]
    return bool(lo <= lam_um <= hi)


def dn_dlambda(material_key: str, wavelength_m: float) -> float:
    """dn/dlambda [1/m] by central differences (numerics.central_diff)."""
    mat = _check_material(material_key)
    lam = float(wavelength_m)
    lo, hi = (mat["range_um"][0] * 1e-6, mat["range_um"][1] * 1e-6)
    h = min(1e-3 * lam, lam - lo, hi - lam)
    if h <= 0:
        raise ValueError("wavelength at/outside the edge of the valid range")
    return central_diff(lambda x: refractive_index(material_key, x), lam, h)


def group_index(material_key: str, wavelength_m: float) -> float:
    """n_g = n - lambda dn/dlambda."""
    return refractive_index(material_key, wavelength_m) - wavelength_m * dn_dlambda(material_key, wavelength_m)


def abbe_number(material_key: str) -> Optional[float]:
    """Vd = (nd-1)/(nF-nC) from the dispersion formula, None if the formula does
    not cover 486.1-656.3 nm."""
    try:
        nd = refractive_index(material_key, 587.5618e-9)
        nf = refractive_index(material_key, 486.1327e-9)
        nc = refractive_index(material_key, 656.2725e-9)
    except ValueError:
        return None
    return (nd - 1.0) / (nf - nc)


def dn_dT(material_key: str, wavelength_m: float = 1550e-9) -> Optional[float]:
    """Absolute dn/dT [1/K] near 20 degC.  Schott/Ohara glasses: from the
    published thermal-dispersion formula (D0, E0, lambda_TK) at 20 degC;
    crystals: the vendor figure stored in LENS_MATERIALS (quoted at a
    specific wavelength, see notes); None when unavailable."""
    mat = _check_material(material_key)
    th = mat.get("thermal_schott")
    if th is None:
        return mat["dn_dT_per_K"]
    d0, _d1, _d2, e0, _e1, ltk = th
    n = refractive_index(material_key, wavelength_m)
    lam_um = wavelength_m * 1e6
    return (n * n - 1.0) / (2.0 * n) * (d0 + e0 / (lam_um ** 2 - ltk ** 2))


for _k, _m in LENS_MATERIALS.items():
    if _m.get("thermal_schott") is not None:
        _m["dn_dT_per_K"] = dn_dT(_k, 1550e-9)
    _m["abbe"] = abbe_number(_k)


# ---------------------------------------------------------------------------
# Coatings
# ---------------------------------------------------------------------------

COATINGS: Dict[str, dict] = {
    "none": {
        "name_tr": "Kaplamasız (Fresnel)", "type": "none", "r_band": None, "band_nm": None,
        "source": "Fresnel equations",
    },
    "v_coat_1550": {
        "name_tr": "V-kaplama 1550 nm (R<%0.25 @ DWL)", "type": "v_coat", "r_band": 0.0025,
        "center_nm": 1550.0, "halfwidth_nm": 75.0, "band_nm": (1550.0, 1550.0),
        "source": "https://www.edmundoptics.eu/resources/application-notes/optics/anti-reflection-coatings/ (V-coat R<0.25% @ design wavelength)",
    },
    "v_coat_850": {
        "name_tr": "V-kaplama 850 nm (R<%0.25 @ DWL)", "type": "v_coat", "r_band": 0.0025,
        "center_nm": 850.0, "halfwidth_nm": 50.0, "band_nm": (850.0, 850.0),
        "source": "https://www.edmundoptics.eu/resources/application-notes/optics/anti-reflection-coatings/",
    },
    "mgf2_single": {
        "name_tr": "Tek katman MgF2 (λ/4 @ 550 nm, Ravg<=%1.75 @ 400-700 nm)", "type": "broadband",
        "r_band": 0.0175, "band_nm": (400.0, 700.0),
        "source": "https://www.edmundoptics.eu/resources/application-notes/optics/anti-reflection-coatings/",
    },
    "bbar_nir_600_1050": {
        "name_tr": "Geniş bant AR NIR (Ravg<=%0.5 @ 600-1050 nm)", "type": "broadband",
        "r_band": 0.005, "band_nm": (600.0, 1050.0),
        "source": "https://www.edmundoptics.eu/resources/application-notes/optics/anti-reflection-coatings/ (NIR I)",
    },
    "bbar_swir_900_1700": {
        "name_tr": "Geniş bant AR SWIR (Ravg<=%1.0 @ 900-1700 nm)", "type": "broadband",
        "r_band": 0.01, "band_nm": (900.0, 1700.0),
        "source": "https://www.edmundoptics.eu/resources/application-notes/optics/anti-reflection-coatings/ (SWIR)",
    },
    "bbar_telecom_1200_1600": {
        "name_tr": "Geniş bant AR Telekom-NIR (Ravg<=%0.25 @ 1200-1600 nm)", "type": "broadband",
        "r_band": 0.0025, "band_nm": (1200.0, 1600.0),
        "source": "Edmund Optics Telecom-NIR coating listing (search snippet; Ravg<=0.25% 1200-1600 nm)",
    },
}


def coating_reflectance(coating_key: str, wavelength_m: float, n_substrate: float,
                        n_surround: float = 1.0) -> Optional[float]:
    """Single-surface reflectance with the coating (approximate model).

    none -> None (caller uses bare Fresnel).  Broadband: R_band inside the band,
    rising linearly to the bare Fresnel value over 10 % of the band-centre
    wavelength outside.  V-coat: parabolic rise from R_band at the design
    wavelength, saturating at the bare Fresnel value.
    """
    try:
        c = COATINGS[coating_key]
    except KeyError:
        raise ValueError(f"unknown coating: {coating_key!r}") from None
    if c["type"] == "none":
        return None
    r_f = optics.fresnel_reflectance(n_surround, n_substrate)
    lam = wavelength_m * 1e9
    r0 = min(c["r_band"], r_f)
    if c["type"] == "v_coat":
        t = min(1.0, ((lam - c["center_nm"]) / c["halfwidth_nm"]) ** 2)
        return r0 + (r_f - r0) * t
    lo, hi = c["band_nm"]
    if lo <= lam <= hi:
        return r0
    dist = lo - lam if lam < lo else lam - hi
    width = 0.1 * 0.5 * (lo + hi)
    return r0 + (r_f - r0) * min(1.0, dist / width)


# ---------------------------------------------------------------------------
# Filters (approximate spectral models)
# ---------------------------------------------------------------------------

_EDM = "https://www.edmundoptics.com/p/"
FILTERS: Dict[str, dict] = {
    "bp_1550_10nm": {
        "name_tr": "Dar bant geçiren 1550 nm / FWHM 10 nm (OD4)", "type": "bandpass",
        "center_nm": 1550.0, "fwhm_nm": 10.0, "peak_T": 0.85, "od": 4.0, "order": 2,
        "verified": True,
        "source": _EDM + "1550nm-cwl-50mm-dia-hard-coated-od-4-10nm-bandpass-filter/28581/ (CWL 1550±2, FWHM 10±2, OD>=4, Tmin>=85%)",
    },
    "bp_1550_2nm": {
        "name_tr": "Ultra dar bant 1550.6 nm / FWHM 2 nm (çok kavite, OD4)", "type": "bandpass",
        "center_nm": 1550.6, "fwhm_nm": 2.0, "peak_T": 0.90, "od": 4.0, "order": 3,
        "verified": True,
        "source": _EDM + "1550nm-cwl-25mm-dia-od-4-ultra-narrow-filter/41345/ (Tabs>90%, FWHM 2 nm)",
    },
    "bp_850_10nm": {
        "name_tr": "Dar bant geçiren 850 nm / FWHM 10 nm (OD4)", "type": "bandpass",
        "center_nm": 850.0, "fwhm_nm": 10.0, "peak_T": 0.85, "od": 4.0, "order": 2,
        "verified": True,
        "source": _EDM + "850nm-cwl-25mm-dia-hard-coated-od-4-10nm-bandpass-filter-/28577/ (CWL 850±2, FWHM 10±2, OD>=4, Tmin>=85%)",
    },
    "lp_1400": {
        "name_tr": "Uzun geçiren (cut-on 1400 nm) — güneş arka plan bastırma", "type": "longpass",
        "edge_nm": 1400.0, "peak_T": 0.90, "od": 4.0, "verified": False,
        "source": "Generic model (typical vendor longpass: >90% pass, OD4 stop); not a specific datasheet",
    },
    "sp_1000": {
        "name_tr": "Kısa geçiren (cut-off 1000 nm)", "type": "shortpass",
        "edge_nm": 1000.0, "peak_T": 0.90, "od": 4.0, "verified": False,
        "source": "Generic model (typical vendor shortpass); not a specific datasheet",
    },
    "nd_od1": {
        "name_tr": "Nötr yoğunluk OD 1.0 (T=%10)", "type": "nd", "od": 1.0, "verified": True,
        "source": "T = 10^-OD (https://www.rp-photonics.com/optical_density.html)",
    },
    "nd_od2": {
        "name_tr": "Nötr yoğunluk OD 2.0 (T=%1)", "type": "nd", "od": 2.0, "verified": True,
        "source": "T = 10^-OD (https://www.rp-photonics.com/optical_density.html)",
    },
    "nd_od3": {
        "name_tr": "Nötr yoğunluk OD 3.0 (T=%0.1)", "type": "nd", "od": 3.0, "verified": True,
        "source": "T = 10^-OD (https://www.rp-photonics.com/optical_density.html)",
    },
}


def filter_transmittance(filter_key: str, wavelength_m: float) -> float:
    """Approximate power transmittance of a filter at ``wavelength_m``.

    bandpass: peak * exp(-ln2 (2|dl|/FWHM)^(2*order)) (super-Gaussian, = peak/2
    at +-FWHM/2) floored at 10^-OD; longpass/shortpass: logistic edge of
    width 0.3 % of the edge wavelength; nd: constant 10^-OD.  A model, not a
    measured spectrum.
    """
    try:
        f = FILTERS[filter_key]
    except KeyError:
        raise ValueError(f"unknown filter: {filter_key!r}") from None
    lam = float(wavelength_m) * 1e9
    if not math.isfinite(lam) or lam <= 0:
        raise ValueError("wavelength must be positive and finite")
    floor = 10.0 ** (-f["od"])
    kind = f["type"]
    if kind == "nd":
        return floor
    peak = f["peak_T"]
    if kind == "bandpass":
        x = 2.0 * abs(lam - f["center_nm"]) / f["fwhm_nm"]
        t = peak * math.exp(-math.log(2.0) * x ** (2 * f["order"])) if x < 60 else 0.0
    else:
        w = 0.003 * f["edge_nm"]
        z = (lam - f["edge_nm"]) / w
        z = max(-60.0, min(60.0, z))
        s = 1.0 / (1.0 + math.exp(-z))
        t = peak * (s if kind == "longpass" else 1.0 - s)
    return max(t, floor)


# ---------------------------------------------------------------------------
# Lens shapes and design
# ---------------------------------------------------------------------------

LENS_SHAPES: Dict[str, dict] = {
    "plano_convex": {
        "name_tr": "Düz-dışbükey (eğri yüz kaynağa dönük)", "shape_factor": 1.0, "sign": 1,
        "designable": True, "convention_tr": "R1 = f(n-1), R2 = ∞ (q=+1)",
        "description_tr": "Bir yüzü düz, diğeri küresel dışbükey. Eğri yüz paralel ışık kaynağına bakınca küresel sapma minimum.",
        "use_case_tr": "FSO'da kolimasyon ve odaklama (sonsuz eşlenik, ≤5:1); alıcı lensi ve verici kolimatörü.",
    },
    "plano_convex_flat_front": {
        "name_tr": "Düz-dışbükey (düz yüz kaynağa dönük)", "shape_factor": -1.0, "sign": 1,
        "designable": True, "convention_tr": "R1 = ∞, R2 = -f(n-1) (q=-1)",
        "description_tr": "Ters yönlendirilmiş düz-dışbükey; küresel sapma daha yüksek (karşılaştırma için).",
        "use_case_tr": "Yanlış yönlendirme etkisini göstermek; mekanik montaj nedeniyle düz yüzün dışa baktığı durumlar.",
    },
    "biconvex": {
        "name_tr": "Çift dışbükey (simetrik)", "shape_factor": 0.0, "sign": 1,
        "designable": True, "convention_tr": "R1 = -R2 = 2f(n-1) (q=0)",
        "description_tr": "İki yüz eşit eğrilikli dışbükey; 1:1 yakın eşlenik görüntülemede en az sapma.",
        "use_case_tr": "Röle/görüntüleme (eşlenik oranı 5:1 - 1:5), kısa mesafeli kuplaj.",
    },
    "plano_concave": {
        "name_tr": "Düz-içbükey", "shape_factor": -1.0, "sign": -1,
        "designable": True, "convention_tr": "R1 = ∞, R2 = -f(n-1) > 0 (f<0, q=-1)",
        "description_tr": "Bir yüzü düz, diğeri içbükey; ıraksatıcı lens.",
        "use_case_tr": "Galileo ışın genişletici (negatif eleman), ışın açma, odak uzunluğu uzatma.",
    },
    "biconcave": {
        "name_tr": "Çift içbükey (simetrik)", "shape_factor": 0.0, "sign": -1,
        "designable": True, "convention_tr": "R1 = -R2 = 2f(n-1) < 0 (f<0, q=0)",
        "description_tr": "İki yüz içbükey; ıraksatıcı lens.",
        "use_case_tr": "Işın genişletme, ıraksama açısı ayarı, Galileo teleskopu.",
    },
    "positive_meniscus": {
        "name_tr": "Pozitif menisküs", "shape_factor": 2.0, "sign": 1,
        "designable": True, "convention_tr": "R1 = 2f(n-1)/3, R2 = 2f(n-1), her ikisi >0 (q=+2 varsayılan)",
        "description_tr": "Dışbükey-içbükey, net yakınsatıcı; küresel sapmayı azaltır.",
        "use_case_tr": "Küçük f-sayısı (<2.5) uygulamaları, yüksek NA toplama, odak uzunluğu kısaltma.",
    },
    "negative_meniscus": {
        "name_tr": "Negatif menisküs", "shape_factor": -2.0, "sign": -1,
        "designable": True, "convention_tr": "R1 = -2f(n-1) > 0, R2 = -2f(n-1)/3 > 0 (f<0, q=-2)",
        "description_tr": "Dışbükey-içbükey, net ıraksatıcı; kenarı kalın.",
        "use_case_tr": "Yüksek açılı genişleticiler, sapma düzeltme elemanı.",
    },
    "aspheric": {
        "name_tr": "Asferik (hiperbolik düz-dışbükey, k=-n²)", "shape_factor": -1.0, "sign": 1,
        "designable": True, "conic": "-n^2",
        "convention_tr": "Düz yüz kaynağa dönük; eğri yüz R2 = -f(n-1), konik k = -n² (paralel ışık için küresel sapmasız)",
        "description_tr": "Konik yüzey; paralel ışık için monokromatik küresel sapma sıfır (paraksiyel limitte).",
        "use_case_tr": "Lazer kolimasyonu/odaklama, fiber kuplajı, çoklu küresel elemanın yerine tek eleman.",
    },
    "ball": {
        "name_tr": "Top lens (küre)", "shape_factor": 0.0, "sign": 1,
        "designable": True, "convention_tr": "R1 = -R2 = R, d = 2R; f = nR/(2(n-1)); çap D = 4(n-1)f/n",
        "description_tr": "Tam küre; EFL küre merkezinden ölçülür, çap odak uzunluğunu belirler.",
        "use_case_tr": "Fiber kuplajı, küçük dedektör lensi, retroreflektörler (n=2 küre).",
    },
    "fresnel": {
        "name_tr": "Fresnel lens (testere dişli, düz-dışbükey eşdeğeri)", "shape_factor": -1.0, "sign": 1,
        "designable": True, "convention_tr": "Düz ön yüz, arka yüzde halka bölgeler; eşdeğer R2 = -f(n-1)",
        "description_tr": "Eğri yüzün bölgeler halinde katlanmış hali; ince ve hafif ama saçılma/kenar kaybı var.",
        "use_case_tr": "Düşük maliyetli geniş açıklıklı alıcılar (PMMA), kısa mesafe/IR sensör.",
    },
    "cylindrical": {
        "name_tr": "Silindirik (tek eksenli düz-dışbükey)", "shape_factor": 1.0, "sign": 1,
        "designable": True, "convention_tr": "Kesit düz-dışbükey ile aynı (yalnız y-ekseninde güç)",
        "description_tr": "Işığı tek eksende odaklar; çizgi odak oluşturur.",
        "use_case_tr": "Diyot lazer ışın şekillendirme, eliptik demeti dairesele çevirme.",
    },
    "achromatic_doublet": {
        "name_tr": "Akromatik doublet (yapıştırılmış çift)", "shape_factor": None, "sign": 1,
        "designable": False, "convention_tr": "İki malzeme (taç+flint), 3 yarıçap; tek-malzeme R1,R2 modeline uymaz",
        "description_tr": "Taç + flint çifti; renk ve küresel sapmayı azaltır.",
        "use_case_tr": "Çok dalga boylu FSO (850 + 1550 nm), geniş alan, f/2 altında difraksiyon sınırlı odak.",
    },
    "grin": {
        "name_tr": "GRIN (gradyan indeksli çubuk)", "shape_factor": None, "sign": 1,
        "designable": False, "convention_tr": "Düz yüzler; odaklama n(r)=n0(1-g²r²/2) ile, R1,R2 yok",
        "description_tr": "İndeks radyal değişir; düz yüzeyli odaklayıcı çubuk (pitch).",
        "use_case_tr": "Fiber ucu kolimatörü, mikro-optik; büyük FSO açıklıkları için uygun değil.",
    },
}

TELESCOPES: Dict[str, dict] = {
    "keplerian": {
        "name_tr": "Kepler (refraktör, + +)", "elements": ["pozitif f1", "pozitif f2"],
        "spacing_rule_tr": "L = f1 + f2; M = -f2/f1 (ters görüntü)",
        "use_case_tr": "Iç odak noktası var: uzamsal filtre/alan diyaframı konabilir; FSO alıcı ve ışın genişletici.",
        "source": "https://www.opticsforhire.com/blog/design-considerations-for-free-space-optics/",
    },
    "galilean": {
        "name_tr": "Galileo (refraktör, - +)", "elements": ["negatif f1", "pozitif f2"],
        "spacing_rule_tr": "L = f2 - |f1|; M = f2/|f1| (dik görüntü)",
        "use_case_tr": "Kompakt, iç odak yok (yüksek güç lazerlerde havada iyonlaşma yok); vericide ışın genişletme.",
        "source": "https://www.opticsforhire.com/blog/design-considerations-for-free-space-optics/",
    },
    "cassegrain": {
        "name_tr": "Cassegrain (yansıtıcı: dışbükey ikincil + içbükey birincil)", "elements": ["parabolik birincil", "hiperbolik ikincil"],
        "spacing_rule_tr": "Katlanmış yol; bileşik EFL = f1·m, m = ikincil büyütme",
        "use_case_tr": "Büyük açıklıklı (>10 cm) FSO terminalleri, kısa mekanik boy, renksiz (aynalar).",
        "source": "FSO patents, e.g. https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/6016212",
    },
    "ritchey_chretien": {
        "name_tr": "Ritchey–Chrétien (çift hiperbolik Cassegrain)", "elements": ["hiperbolik birincil", "hiperbolik ikincil"],
        "spacing_rule_tr": "Cassegrain geometrisi; koma ve küresel sapma birlikte giderilir",
        "use_case_tr": "Yüksek performanslı uzay/yer terminalleri; 200 mm RC ile 1550 nm 2.5 Gb/s 1.1 km bağlantı (SPIE 5550).",
        "source": "https://spiedigitallibrary.org/conference-proceedings-of-spie/5550/0000/25-Gb-s-free-space-optics-link-over-11-km/10.1117/12.554886.short",
    },
}


def telescope_parameters(kind: str, f1_m: float, f2_m: float) -> dict:
    """Afocal refractor spacing and angular magnification (keplerian/galilean)."""
    if kind == "keplerian":
        if f1_m <= 0 or f2_m <= 0:
            raise ValueError("Keplerian needs f1>0 and f2>0")
        return {"spacing_m": f1_m + f2_m, "magnification": -f2_m / f1_m}
    if kind == "galilean":
        if f1_m >= 0 or f2_m <= 0:
            raise ValueError("Galilean needs f1<0 and f2>0")
        return {"spacing_m": f1_m + f2_m, "magnification": f2_m / abs(f1_m)}
    raise ValueError("telescope_parameters supports keplerian and galilean only")


def _sag(R: float, y: float, k: float = 0.0) -> float:
    """Conic sag of a surface with vertex radius R (inf -> 0)."""
    if math.isinf(R):
        return 0.0
    c = 1.0 / R
    arg = 1.0 - (1.0 + k) * c * c * y * y
    if arg < 0:
        raise ValueError("surface radius too small for the requested diameter (|R| < D/2)")
    return c * y * y / (1.0 + math.sqrt(arg))


def _solve_curvature_scale(n: float, c1: float, c2: float, d: float, f_signed: float) -> float:
    """u such that thick-lens power with curvatures (u c1, u c2) = 1/f."""
    a = c1 - c2
    b = (n - 1.0) * d * c1 * c2 / n
    target = 1.0 / ((n - 1.0) * f_signed)
    if abs(b) < 1e-30:
        return target / a
    disc = a * a + 4.0 * b * target
    if disc < 0:
        raise ValueError("no real thick-lens solution for this thickness/focal length")
    roots = [(-a + s * math.sqrt(disc)) / (2.0 * b) for s in (1.0, -1.0)]
    roots = [r for r in roots if r > 0]
    if not roots:
        raise ValueError("no positive curvature scale solves the thick-lens equation")
    return min(roots, key=lambda r: abs(math.log(r)))


def design_lens(shape: str, material: str, focal_length_m: float, diameter_m: float,
                wavelength_m: float, center_thickness_m: Optional[float] = None,
                coating: str = "none", shape_factor: Optional[float] = None,
                n_points: int = 81, min_edge_thickness_m: Optional[float] = None) -> dict:
    """Design a single lens (R1, R2, thickness, profile, transmittance).

    ``focal_length_m`` is the magnitude; negative shapes (plano_concave,
    biconcave, negative_meniscus) return f < 0.  The thick-lens EFL (computed
    with optics.thick_lens_focal_length) equals the target at ``wavelength_m``.
    """
    if shape not in LENS_SHAPES:
        raise ValueError(f"unknown lens shape: {shape!r}")
    sh = LENS_SHAPES[shape]
    if not sh["designable"]:
        raise ValueError(f"shape {shape!r} is descriptive only (needs multi-material/GRIN design)")
    f_mag = float(focal_length_m)
    D = float(diameter_m)
    if not (math.isfinite(f_mag) and f_mag > 0):
        raise ValueError("focal_length_m must be positive")
    if not (math.isfinite(D) and D > 0):
        raise ValueError("diameter_m must be positive")
    n = refractive_index(material, wavelength_m)
    if not is_transparent(material, wavelength_m):
        raise ValueError(f"{material} is not transparent at {wavelength_m * 1e9:.0f} nm")
    f_signed = sh["sign"] * f_mag
    h = D / 2.0
    n_points = max(41, int(n_points))
    min_edge = (min_edge_thickness_m if min_edge_thickness_m is not None
                else max(1e-3, D / 25.0))
    if min_edge <= 0:
        raise ValueError("min_edge_thickness_m must be positive")
    k2 = 0.0
    fresnel_step = None

    if shape == "ball":
        R = 2.0 * (n - 1.0) * f_mag / n
        R1, R2, d = R, -R, 2.0 * R
        if h > R:
            h = R   # a ball cannot be wider than itself
        ys = np.linspace(-h, h, n_points)
        profile = [(float(y), float(R - math.sqrt(R * R - y * y)),
                    float(d - (R - math.sqrt(R * R - y * y)))) for y in ys]
        edge = profile[-1][2] - profile[-1][1]
        center = d
        D = 2.0 * R
    else:
        q = sh["shape_factor"] if shape_factor is None else float(shape_factor)
        base = 2.0 * (n - 1.0) * f_signed
        c1 = (q + 1.0) / base
        c2 = (q - 1.0) / base
        if shape == "aspheric":
            k2 = -n * n
        # iterate center thickness <-> curvature (thick-lens EFL = target)
        u = 1.0
        d = center_thickness_m if center_thickness_m is not None else min_edge
        if d <= 0:
            raise ValueError("center_thickness_m must be positive")
        for _ in range(60):
            u = _solve_curvature_scale(n, c1, c2, d, f_signed)
            r1 = (1.0 / (u * c1)) if c1 != 0 else math.inf
            r2 = (1.0 / (u * c2)) if c2 != 0 else math.inf
            if center_thickness_m is not None:
                break
            d_new = max(min_edge, min_edge + _sag(r1, h) - _sag(r2, h, k2))
            if shape == "fresnel":
                break
            if abs(d_new - d) < 1e-12:
                d = d_new
                break
            d = d_new
        R1, R2 = r1, r2
        if shape == "fresnel":
            # flat front, grooved back with equivalent curvature; fixed thin substrate
            fresnel_step = max(2e-4, D / 100.0)
            d = center_thickness_m if center_thickness_m is not None else fresnel_step + min_edge
        ys = np.linspace(-h, h, max(n_points, 401 if shape == "fresnel" else n_points))
        profile = []
        for y in ys:
            y = float(y)
            zf = _sag(R1, y)
            if shape == "fresnel":
                # R2<0: sag is negative (bulges toward -z); sawtooth on |sag|
                s_abs = abs(_sag(R2, y))
                zb = d - (s_abs - math.floor(s_abs / fresnel_step) * fresnel_step)
            else:
                zb = d + _sag(R2, y, k2)
            profile.append((y, zf, zb))
        center = profile[len(profile) // 2][2] - profile[len(profile) // 2][1]
        edge = profile[-1][2] - profile[-1][1]
        if edge <= 0:
            raise ValueError("edge thickness <= 0: increase center_thickness_m or reduce diameter")
        if shape != "fresnel" and edge < min_edge * (1 - 1e-9) and center_thickness_m is None:
            raise ValueError("edge thickness below minimum")

    # EFL check with the library thick-lens formula (not for fresnel/ball specials)
    if shape == "fresnel":
        efl = 1.0 / ((n - 1.0) * (0.0 - 1.0 / R2))   # equivalent thin PCX
    else:
        efl = optics.thick_lens_focal_length(n, R1, R2, center)
    r_ar = coating_reflectance(coating, wavelength_m, n)
    t_surf = optics.lens_transmittance(n, 2, 1.0, r_ar)
    alpha = LENS_MATERIALS[material].get("alpha_1550_per_cm") or 0.0
    t_abs = math.exp(-alpha * center * 100.0)
    f_out = f_signed if shape != "ball" else f_mag
    return {
        "shape": shape,
        "material": material,
        "wavelength_m": float(wavelength_m),
        "n_used": float(n),
        "shape_factor": None if shape in ("ball", "fresnel") else float(q),
        "R1_m": None if math.isinf(R1) else float(R1),
        "R2_m": None if math.isinf(R2) else float(R2),
        "conic_k2": float(k2),
        "center_thickness_m": float(center),
        "edge_thickness_m": float(edge),
        "diameter_m": float(D),
        "focal_length_m": float(f_out),
        "efl_check_m": float(efl),
        "f_number": float(abs(f_out) / D),
        "NA": float(math.sin(math.atan(D / (2.0 * abs(f_out))))),
        "coating": coating,
        "surface_reflectance": None if r_ar is None else float(r_ar),
        "transmittance": float(t_surf * t_abs),
        "fresnel_step_m": fresnel_step,
        "profile": [[float(a), float(b), float(c)] for a, b, c in profile],
    }


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def _jsonable(x):
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def catalog() -> dict:
    """JSON-serializable catalog of materials, shapes, telescopes, filters, coatings."""
    mats = {}
    for key, m in LENS_MATERIALS.items():
        entry = {k: v for k, v in m.items() if k not in ("thermal_schott",)}
        if m["formula"] == "tabulated":
            entry["coefficients"] = "tabulated (see source)"
        n_at = {}
        for nm in (850, 1310, 1550):
            try:
                n_at[str(nm)] = refractive_index(key, nm * 1e-9)
            except ValueError:
                n_at[str(nm)] = None
        entry["n"] = n_at
        entry["transparent"] = {str(nm): is_transparent(key, nm * 1e-9) for nm in (850, 1310, 1550)}
        entry["usable_fso_1550"] = bool(entry["transparent"]["1550"] and n_at["1550"] is not None)
        mats[key] = entry
    return _jsonable({
        "materials": mats,
        "shapes": LENS_SHAPES,
        "telescopes": TELESCOPES,
        "filters": FILTERS,
        "coatings": COATINGS,
    })
