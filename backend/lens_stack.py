"""Lens yığını (seri lens) modeli.

Bir yığın, optik eksen üzerinde sıralı N (<= 8) lens elemanıdır.  Her eleman
``materials.design_lens`` ile tasarlanır (R1, R2, kalınlık, profil, iletim); gerçek kalın-lens
ABCD matrisi (kırılma -> cam kalınlığı -> kırılma, n(lambda)) ve hava boşlukları bir
``optics.OpticalSystem`` içinde birleştirilir.

Geometri: ilk elemanın ön tepe noktası z = 0.  Eleman i'nin arka tepe noktası
z_i + t_i, bir sonraki elemanın ön tepe noktası z_i + t_i + boşluk_i.  Son elemanın
``spacingM`` değeri kullanılmaz (çıkış düzlemi son arka tepe noktasıdır).

Işın yelpazesi, ``backend.app`` içindeki /api/lens/design yardımcılarının (``_surface_hit``,
``_refract``) aynı algoritmasıyla (konik yüzey kesişimi + vektörel Snell), fakat TÜM
yüzeylerden geçecek biçimde hesaplanır.  Döngüsel içe aktarmayı önlemek için algoritma
burada yeniden yazılmıştır (app.py bu modülü içe aktaran router ile bağlıdır).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np

from backend import materials, optics

MAX_ELEMENTS = 8
MAX_GAP_M = 10.0
OVERLAP_TOL_M = 1e-9
AFOCAL_C = 1e-9          # |C| [1/m] altında sistem afokal sayılır (EFL > 1000 km)

__all__ = ["MAX_ELEMENTS", "StackError", "design_stack", "preset_list", "preset_elements",
           "normalize_element"]


class StackError(ValueError):
    """Kullanıcıya gösterilecek (Türkçe) doğrulama hatası."""


# --------------------------------------------------------------------------
# Presets
# --------------------------------------------------------------------------

_AR = "bbar_telecom_1200_1600"

PRESETS: Dict[str, dict] = {
    "kepler": {
        "name_tr": "Kepler genişletici (2 lens)",
        "description_tr": "İki pozitif lens; boşluk ≈ f1+f2 (afokal), ışın büyütmesi f2/f1 = 4×, ters çevirir, "
                          "iç odak noktası vardır.",
        "afocal": True,
        "elements": [
            {"shape": "plano_convex", "material": "n_bk7", "focalLengthM": 0.05, "diameterM": 0.025, "coating": _AR},
            {"shape": "plano_convex_flat_front", "material": "n_bk7", "focalLengthM": 0.20, "diameterM": 0.100,
             "coating": _AR},
        ],
    },
    "galileo": {
        "name_tr": "Galile genişletici",
        "description_tr": "Negatif + pozitif lens; boşluk ≈ f1+f2 = 150 mm (afokal), ışın büyütmesi 4×, görüntüyü "
                          "ters çevirmez, iç odak yoktur (kompakt).",
        "afocal": True,
        "elements": [
            {"shape": "plano_concave", "material": "n_bk7", "focalLengthM": 0.05, "diameterM": 0.015, "coating": _AR},
            {"shape": "plano_convex_flat_front", "material": "n_bk7", "focalLengthM": 0.20, "diameterM": 0.050,
             "coating": _AR},
        ],
    },
    "achromat_pair": {
        "name_tr": "Akromatik benzeri yakın çift (2 temaslı lens)",
        "description_tr": "Kron (N-BK7, çift dışbükey) + flint (N-SF6, düz-içbükey) temas halinde (boşluk 0). "
                          "Güçler Abbe sayılarıyla dengelenir: P_kron/V_kron + P_flint/V_flint ≈ 0.",
        "afocal": False,
        "elements": [
            {"shape": "biconvex", "material": "n_bk7", "focalLengthM": 0.10, "diameterM": 0.025, "coating": _AR,
             "spacingM": 0.0},
            {"shape": "plano_concave", "material": "n_sf6", "focalLengthM": 0.256, "diameterM": 0.025,
             "coating": _AR},
        ],
    },
    "cooke_triplet": {
        "name_tr": "Üçlü (Cooke benzeri)",
        "description_tr": "Pozitif kron – negatif flint – pozitif kron, aralarda hava boşluğu; Petzval toplamını "
                          "dengeleyen klasik üçlü düzen.",
        "afocal": False,
        "elements": [
            {"shape": "biconvex", "material": "n_bk7", "focalLengthM": 0.06, "diameterM": 0.025, "coating": _AR,
             "spacingM": 0.012},
            {"shape": "biconcave", "material": "n_sf6", "focalLengthM": 0.05, "diameterM": 0.020, "coating": _AR,
             "spacingM": 0.012},
            {"shape": "biconvex", "material": "n_bk7", "focalLengthM": 0.06, "diameterM": 0.025, "coating": _AR},
        ],
    },
}


def _elem_defaults(e: dict) -> dict:
    out = {"shape": "plano_convex", "material": "n_bk7", "focalLengthM": 0.1, "diameterM": 0.025,
           "centerThicknessM": None, "coating": "none", "filter": "none", "spacingM": 0.0}
    out.update({k: v for k, v in e.items() if v is not None or k == "centerThicknessM"})
    return out


def normalize_element(e: dict) -> dict:
    return _elem_defaults(dict(e))


def _thick_matrix_of(d: dict) -> np.ndarray:
    n = d["n_used"]
    R1 = math.inf if d["R1_m"] is None else d["R1_m"]
    R2 = math.inf if d["R2_m"] is None else d["R2_m"]
    return optics.thick_lens(n, R1, R2, d["center_thickness_m"])


def preset_elements(key: str, wavelength_m: float = 1550e-9) -> List[dict]:
    """Eleman listesi; afokal presetlerde boşluk, kalın lens ana düzlemleri hesaba katılarak
    gerçekten afokal olacak şekilde çözülür."""
    if key not in PRESETS:
        raise StackError(f"Bilinmeyen ön ayar: {key!r}")
    els = [_elem_defaults(e) for e in PRESETS[key]["elements"]]
    if PRESETS[key]["afocal"]:
        ds = [materials.design_lens(e["shape"], e["material"], e["focalLengthM"], e["diameterM"], wavelength_m,
                                    e["centerThicknessM"], e["coating"]) for e in els]
        m1, m2 = _thick_matrix_of(ds[0]), _thick_matrix_of(ds[1])
        f1, f2 = -1.0 / m1[1, 0], -1.0 / m2[1, 0]
        p_rear1 = (1.0 - m1[0, 0]) / m1[1, 0]       # arka ana düzlem, arka tepe noktasından
        p_front2 = (m2[1, 1] - 1.0) / m2[1, 0]      # ön ana düzlem, ön tepe noktasından
        els[0]["spacingM"] = float(f1 + f2 + p_rear1 - p_front2)
    return els


def preset_list(wavelength_m: float = 1550e-9) -> List[dict]:
    out = []
    for key, p in PRESETS.items():
        els = preset_elements(key, wavelength_m)
        entry = {"key": key, "name_tr": p["name_tr"], "description_tr": p["description_tr"],
                 "afocal": p["afocal"], "elements": els}
        try:
            r = design_stack(els, wavelength_m, n_points=41, n_rays=3)
            s = r["system"]
            entry["summary"] = {"efl_m": s["efl_m"], "bfl_m": s["bfl_m"], "afocal": s["afocal"],
                                "beam_magnification": s["beam_magnification"],
                                "total_transmittance": r["total_transmittance"],
                                "total_length_m": s["total_length_m"]}
        except ValueError as error:  # pragma: no cover - presetler 1550 nm'de geçerlidir
            entry["summary"] = {"error": str(error)}
        out.append(entry)
    return out


# --------------------------------------------------------------------------
# Exact (non-paraxial) ray tracing through all surfaces
# --------------------------------------------------------------------------


def _surface_hit(z0, y0, dz, dy, vz, R, k):
    """(z0,y0)+t(dz,dy) ışınının z = vz + sag(R,k;y) yüzeyiyle kesişimi.
    (z, y, nz, ny): +z'ye bakan birim normal; kesişmezse None."""
    def sag(y):
        return materials._sag(R, y, k)

    def dsag(y):
        if math.isinf(R):
            return 0.0
        c = 1.0 / R
        return c * y / math.sqrt(max(1e-18, 1.0 - (1.0 + k) * c * c * y * y))

    if dz <= 1e-12:
        return None
    z = vz
    for _ in range(60):
        y = y0 + (dy / dz) * (z - z0)
        g = z - vz - sag(y)
        gp = 1.0 - dsag(y) * (dy / dz)
        if abs(gp) < 1e-12:
            return None
        z_new = z - g / gp
        if abs(z_new - z) < 1e-14:
            z = z_new
            break
        z = z_new
    y = y0 + (dy / dz) * (z - z0)
    s = dsag(y)
    norm = math.hypot(1.0, s)
    return z, y, 1.0 / norm, -s / norm


def _refract(dz, dy, nz, ny, n1, n2):
    cosi = dz * nz + dy * ny
    if cosi < 0:
        nz, ny, cosi = -nz, -ny, -cosi
    r = n1 / n2
    kk = 1.0 - r * r * (1.0 - cosi * cosi)
    if kk < 0:
        return None
    t = r * cosi - math.sqrt(kk)
    return r * dz - t * nz, r * dy - t * ny


def _trace_exact(y_in: float, theta: float, z_start: float, surfaces: List[dict], z_end: float) -> dict:
    """Işın z=0 düzleminde y_in yüksekliğinden, eksenle theta [rad] açıyla geçer.
    ``surfaces``: [{vz, R, k, n1, n2, h, el}] sırayla."""
    dz, dy = math.cos(theta), math.sin(theta)
    z, y = z_start, y_in + math.tan(theta) * z_start
    pts = [[z, y]]
    blocked = None
    for s in surfaces:
        try:
            hit = _surface_hit(z, y, dz, dy, s["vz"], s["R"], s["k"])
        except ValueError:
            hit = None
        if hit is None:
            blocked = s["el"]
            break
        zh, yh, nz, ny = hit
        pts.append([zh, yh])
        z, y = zh, yh
        if abs(yh) > s["h"] * (1.0 + 1e-9):
            blocked = s["el"]
            break
        nd = _refract(dz, dy, nz, ny, s["n1"], s["n2"])
        if nd is None:
            blocked = s["el"]
            break
        dz, dy = nd
        nrm = math.hypot(dz, dy)
        dz, dy = dz / nrm, dy / nrm
    if blocked is None and dz > 1e-12:
        t = (z_end - z) / dz
        pts.append([z_end, y + dy * t])
    return {"points": pts, "vignetted_at": blocked, "dz": dz, "dy": dy, "z_last": z, "y_last": y}


# --------------------------------------------------------------------------
# Stack design
# --------------------------------------------------------------------------


def _check_element(i: int, e: dict, lam: float) -> None:
    tag = f"Eleman {i + 1}"
    if e["shape"] not in materials.LENS_SHAPES:
        raise StackError(f"{tag}: bilinmeyen lens şekli {e['shape']!r}.")
    if not materials.LENS_SHAPES[e["shape"]]["designable"]:
        raise StackError(f"{tag}: '{e['shape']}' şekli yalnızca açıklayıcıdır, tasarlanamaz.")
    if e["material"] not in materials.LENS_MATERIALS:
        raise StackError(f"{tag}: bilinmeyen malzeme {e['material']!r}.")
    if e["coating"] not in materials.COATINGS:
        raise StackError(f"{tag}: bilinmeyen kaplama {e['coating']!r}.")
    f = e.get("filter")
    if f not in (None, "", "none") and f not in materials.FILTERS:
        raise StackError(f"{tag}: bilinmeyen filtre {f!r}.")
    if not materials.is_transparent(e["material"], lam):
        lo, hi = materials.LENS_MATERIALS[e["material"]]["transmission_um"]
        raise StackError(f"{tag}: {e['material']} malzemesi {lam * 1e9:.0f} nm dalga boyunda opak "
                         f"(geçirgenlik bandı {lo:g}–{hi:g} µm). Başka bir malzeme seçin.")
    for key, lab in (("focalLengthM", "odak uzaklığı"), ("diameterM", "çap")):
        v = e[key]
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
            raise StackError(f"{tag}: {lab} pozitif ve sonlu olmalı.")
    gap = e["spacingM"]
    if not (isinstance(gap, (int, float)) and math.isfinite(gap)):
        raise StackError(f"{tag}: sonraki lense mesafe sonlu olmalı.")
    if gap < 0:
        raise StackError(f"{tag}: sonraki lense mesafe negatif olamaz ({gap * 1e3:.2f} mm).")
    if gap > MAX_GAP_M:
        raise StackError(f"{tag}: sonraki lense mesafe en fazla {MAX_GAP_M:g} m olabilir.")


def _interp_profile(d: dict, ys: np.ndarray, col: int) -> np.ndarray:
    prof = np.asarray(d["profile"], dtype=float)
    order = np.argsort(prof[:, 0])
    return np.interp(ys, prof[order, 0], prof[order, col])


def _compact(x, sig: int = 8):
    if isinstance(x, dict):
        return {k: _compact(v, sig) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_compact(v, sig) for v in x]
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        x = float(x)
        return float(f"{x:.{sig}g}") if math.isfinite(x) else None
    return x


def design_stack(elements: List[dict], wavelength_m: float = 1550e-9, *, n_points: int = 61,
                 n_rays: int = 9, field_angle_deg: float = 0.0, link_distance_m: float = 1000.0,
                 beam_w0_m: float = 0.002, bare_rx_radius_m: float = 0.0125,
                 source_offset_m: float = 0.0, m2: float = 1.0) -> Dict[str, Any]:
    """Yığını tasarla ve sistem büyüklüklerini, ışın yelpazesini ve link kazancını döndür (JSON uyumlu)."""
    if not elements:
        raise StackError("Yığında en az bir lens olmalı.")
    if len(elements) > MAX_ELEMENTS:
        raise StackError(f"Yığında en fazla {MAX_ELEMENTS} lens olabilir (gönderilen: {len(elements)}).")
    lam = float(wavelength_m)
    if not (math.isfinite(lam) and lam > 0):
        raise StackError("Dalga boyu pozitif olmalı.")
    els = [normalize_element(e) for e in elements]
    designs: List[dict] = []
    filt_t: List[float] = []
    for i, e in enumerate(els):
        _check_element(i, e, lam)
        try:
            d = materials.design_lens(e["shape"], e["material"], e["focalLengthM"], e["diameterM"], lam,
                                      e["centerThicknessM"], e["coating"], None, max(41, int(n_points)))
        except ValueError as error:
            raise StackError(f"Eleman {i + 1}: lens tasarlanamadı: {error}") from error
        designs.append(d)
        f = e.get("filter")
        filt_t.append(materials.filter_transmittance(f, lam) if f not in (None, "", "none") else 1.0)

    # ---- yerleşim ----
    z_front, z_back = [], []
    z = 0.0
    for i, d in enumerate(designs):
        z_front.append(z)
        z_back.append(z + d["center_thickness_m"])
        gap = els[i]["spacingM"] if i < len(els) - 1 else 0.0
        z = z_back[-1] + gap
    z_out = z_back[-1]

    # ---- çakışma denetimi (yüz sagitası dahil) ----
    for i in range(len(designs) - 1):
        a, b = designs[i], designs[i + 1]
        r = min(a["diameter_m"], b["diameter_m"]) / 2.0
        ys = np.linspace(0.0, r, 81)
        zb_prev = z_front[i] + _interp_profile(a, ys, 2)
        zf_next = z_front[i + 1] + _interp_profile(b, ys, 1)
        clearance = float(np.min(zf_next - zb_prev))
        if clearance < -OVERLAP_TOL_M:
            raise StackError(
                f"Eleman {i + 1} ve {i + 2} çakışıyor: yüz sagitası hesaba katıldığında lensler "
                f"{-clearance * 1e3:.2f} mm iç içe giriyor. 'Sonraki lense mesafe' değerini artırın.")

    # ---- ABCD sistemi ----
    sys_els: List[optics.OpticalElement] = []
    for i, (e, d) in enumerate(zip(els, designs)):
        t_el = d["transmittance"] * filt_t[i]
        sys_els.append(optics.OpticalElement(f"Lens {i + 1}", _thick_matrix_of(d), d["diameter_m"] / 2.0,
                                             d["center_thickness_m"], min(1.0, max(0.0, t_el))))
        gap = e["spacingM"] if i < len(els) - 1 else 0.0
        if gap > 0:
            sys_els.append(optics.OpticalElement(f"Boşluk {i + 1}", optics.free_space(gap), length=gap))
    system = optics.OpticalSystem(sys_els)
    M = system.matrix
    A, B, C, D = (float(M[0, 0]), float(M[0, 1]), float(M[1, 0]), float(M[1, 1]))
    afocal = abs(C) < AFOCAL_C
    efl = None if afocal else -1.0 / C
    bfl = None if afocal else -A / C
    ffl = None if afocal else -D / C
    planes = None if afocal else ((D - 1.0) / C, (1.0 - A) / C)
    total_len = z_out
    p_front_z = None if planes is None else planes[0]
    p_rear_z = None if planes is None else z_out + planes[1]

    # ---- limitleyen açıklık (giriş gözbebeği): paraksiyel marjinal ışın ----
    states = system.trace_ray(1.0, 0.0)
    ratios = []
    lens_state = [s for s in states if s.name.startswith("Lens")]
    for i, (s, d) in enumerate(zip(lens_state, designs)):
        yh = max(abs(s.y_in), abs(s.y_out))
        ratios.append(math.inf if yh < 1e-15 else (d["diameter_m"] / 2.0) / yh)
    stop_idx = int(np.argmin(ratios))
    ep_radius = float(ratios[stop_idx])
    f_number = None if afocal else abs(efl) / (2.0 * ep_radius)

    # ---- vinyetleme (paraksiyel, alan açısı) ----
    def pass_fraction(theta: float) -> float:
        ok, n = 0, 41
        for yy in np.linspace(-ep_radius, ep_radius, n):
            st = system.trace_ray(float(yy), theta)
            bad = False
            for s, e in zip((q for q in st if q.name.startswith("Lens")), designs):
                if max(abs(s.y_in), abs(s.y_out)) > e["diameter_m"] / 2.0 * (1.0 + 1e-9):
                    bad = True
                    break
            ok += 0 if bad else 1
        return ok / n

    theta_f = math.radians(float(field_angle_deg))
    frac_field = pass_fraction(theta_f)
    fov_ok = 0.0
    for ang in np.arange(0.0, 10.01, 0.25):
        if pass_fraction(math.radians(float(ang))) >= 0.999:
            fov_ok = float(ang)
        else:
            break
    vign = {"on_axis_clear": pass_fraction(0.0) >= 0.999, "field_angle_deg": float(field_angle_deg),
            "pass_fraction_at_field": frac_field, "unvignetted_half_field_deg": fov_ok,
            "note_tr": "Paraksiyel tarama: giriş gözbebeği genişliğince ışınların tüm lens açıklıklarından "
                       "geçen oranı (ön/arka tepe yüksekliği kullanılır)."}

    # ---- iletim ----
    t_each = [d["transmittance"] * f for d, f in zip(designs, filt_t)]
    t_total = float(np.prod(t_each))

    # ---- Gauss ışını ----
    gb = optics.beam_through_system(beam_w0_m, lam, system, link_distance_m, m2, source_offset_m)
    w0 = float(beam_w0_m)
    w_base = optics.beam_radius(source_offset_m + total_len + link_distance_m, w0, lam, m2)
    frac_base_tx = max(optics.received_power_fraction(w_base, bare_rx_radius_m), 1e-300)
    frac_tx = max(optics.received_power_fraction(gb["w"], bare_rx_radius_m) * gb["total_transmission"], 1e-300)
    gauss = {k: gb[k] for k in ("w", "R", "divergence", "w0_out", "z_waist_out", "zR_out",
                                "clipping_transmission", "transmittance", "total_transmission")}
    gauss["element_radii"] = [[n, w] for n, w in gb["element_radii"]]
    gauss["q_re"], gauss["q_im"] = gb["q"].real, gb["q"].imag

    # ---- alıcı olarak link kazancı (giriş gözbebeği x toplam iletim) vs çıplak alıcı ----
    w_rx = optics.beam_radius(link_distance_m, w0, lam, m2)
    frac_bare = max(optics.received_power_fraction(w_rx, bare_rx_radius_m), 1e-300)
    frac_stack = max(optics.received_power_fraction(w_rx, ep_radius) * t_total, 1e-300)
    receiver_suitable = afocal or (efl is not None and efl > 0)
    link = {
        "gain_dB": 10.0 * math.log10(frac_stack / frac_bare),
        "beam_radius_at_rx_m": w_rx, "frac_bare": frac_bare, "frac_stack": frac_stack,
        "bare_rx_radius_m": float(bare_rx_radius_m), "stack_rx_radius_m": ep_radius,
        "link_distance_m": float(link_distance_m), "receiver_suitable": bool(receiver_suitable),
        "transmit": {"gain_dB": 10.0 * math.log10(frac_tx / frac_base_tx), "w_with_m": gb["w"],
                     "w_without_m": w_base, "divergence_with_rad": gb["divergence"],
                     "divergence_without_rad": optics.far_field_divergence(w0, lam, m2)},
        "note_tr": ("Net ıraksatıcı (negatif) yığın alıcı olarak kullanılamaz; kazanç yalnızca açıklık etkisini gösterir."
                    if not receiver_suitable else
                    "Alıcı kazancı = (giriş gözbebeği açıklığı × toplam iletim) ile çıplak alıcının topladığı güç oranı; "
                    "verici kazancı = yığının ışını yeniden şekillendirmesiyle çıplak alıcıda toplanan güç oranı."),
    }

    # ---- tam Snell ışın yelpazesi ----
    surfaces: List[dict] = []
    for i, d in enumerate(designs):
        n = d["n_used"]
        R1 = math.inf if d["R1_m"] is None else d["R1_m"]
        R2 = math.inf if d["R2_m"] is None else d["R2_m"]
        h = d["diameter_m"] / 2.0
        surfaces.append({"vz": z_front[i], "R": R1, "k": 0.0, "n1": 1.0, "n2": n, "h": h, "el": i})
        surfaces.append({"vz": z_back[i], "R": R2, "k": d["conic_k2"], "n1": n, "n2": 1.0, "h": h, "el": i})
    d_max = max(d["diameter_m"] for d in designs)
    z_start = -(0.5 * designs[0]["diameter_m"] + 0.05 * total_len)
    base_len = max(total_len, d_max)
    if not afocal and bfl is not None and bfl > 0:
        z_end = z_out + max(1.3 * bfl, 0.1 * abs(efl))
        z_end = min(z_end, z_out + 20.0 * base_len)
    else:
        z_end = z_out + max(0.8 * total_len, 1.5 * d_max)
    paraxial = _trace_exact(ep_radius * 1e-3, 0.0, z_start, surfaces, z_end)
    focus_z = None
    if abs(paraxial["dy"]) > 1e-12 and paraxial["vignetted_at"] is None:
        focus_z = paraxial["z_last"] - paraxial["y_last"] * paraxial["dz"] / paraxial["dy"]
        if abs(focus_z - z_out) > 1e3 * max(base_len, 1e-3):
            focus_z = None
    n_rays = max(3, min(21, int(n_rays)))
    rays = []
    for i in range(n_rays):
        frac = -0.9 + 1.8 * i / (n_rays - 1)
        tr = _trace_exact(frac * ep_radius, 0.0, z_start, surfaces, z_end)
        rays.append({"y_in": frac * ep_radius, "points": tr["points"], "vignetted_at": tr["vignetted_at"]})
    rays_field: List[dict] = []
    field_image = None
    if abs(field_angle_deg) > 1e-12:
        for i in range(n_rays):
            frac = -0.9 + 1.8 * i / (n_rays - 1)
            tr = _trace_exact(frac * ep_radius, theta_f, z_start, surfaces, z_end)
            rays_field.append({"y_in": frac * ep_radius, "points": tr["points"],
                               "vignetted_at": tr["vignetted_at"]})
        chief = _trace_exact(0.0, theta_f, z_start, surfaces, z_end)
        if focus_z is not None and chief["vignetted_at"] is None and chief["dz"] > 1e-12:
            field_image = chief["y_last"] + chief["dy"] / chief["dz"] * (focus_z - chief["z_last"])
    lsa = None
    if focus_z is not None and n_rays >= 3:
        marg = _trace_exact(0.9 * ep_radius, 0.0, z_start, surfaces, z_end)
        if marg["vignetted_at"] is None and abs(marg["dy"]) > 1e-12:
            lsa = (marg["z_last"] - marg["y_last"] * marg["dz"] / marg["dy"]) - focus_z

    # ---- çizim için eleman profilleri (mutlak z) ----
    el_out = []
    for i, (e, d) in enumerate(zip(els, designs)):
        prof = [[y, zf + z_front[i], zb + z_front[i]] for y, zf, zb in d["profile"]]
        el_out.append({
            "index": i, "name": f"Lens {i + 1}", "shape": d["shape"], "material": d["material"], "n_used": d["n_used"],
            "R1_m": d["R1_m"], "R2_m": d["R2_m"], "conic_k2": d["conic_k2"],
            "focal_length_m": d["focal_length_m"], "efl_check_m": d["efl_check_m"],
            "diameter_m": d["diameter_m"], "center_thickness_m": d["center_thickness_m"],
            "edge_thickness_m": d["edge_thickness_m"], "z_front_m": z_front[i], "z_back_m": z_back[i],
            "gap_after_m": e["spacingM"] if i < len(els) - 1 else None,
            "coating": d["coating"], "filter": e.get("filter") or "none",
            "transmittance_lens": d["transmittance"], "transmittance_filter": filt_t[i],
            "transmittance": t_each[i], "stop_ratio_m": None if math.isinf(ratios[i]) else ratios[i],
            "is_stop": i == stop_idx, "profile": prof,
        })

    result = {
        "wavelength_m": lam,
        "n_elements": len(els),
        "elements": el_out,
        "system": {
            "abcd": {"A": A, "B": B, "C": C, "D": D}, "afocal": bool(afocal),
            "efl_m": efl, "bfl_m": bfl, "ffl_m": ffl,
            "principal_planes_m": None if planes is None else {"front_from_input": planes[0],
                                                              "rear_from_output": planes[1],
                                                              "front_z": p_front_z, "rear_z": p_rear_z},
            "total_length_m": total_len, "z_out_m": z_out,
            "f_number": f_number,
            "beam_magnification": A if afocal else None,
            "angular_magnification": D if afocal else None,
            "entrance_pupil_radius_m": ep_radius,
            "stop_element": stop_idx, "stop_name": f"Lens {stop_idx + 1}",
            "determinant_ok": bool(system.determinant_ok(1e-9)),
        },
        "total_transmittance": t_total,
        "vignetting": vign,
        "gaussian": gauss,
        "link": link,
        "ray_fan": {
            "rays": rays, "rays_field": rays_field, "z_start": z_start, "z_end": z_end, "focus_z": focus_z,
            "bfl_exact_m": None if focus_z is None else focus_z - z_out, "lsa_m": lsa,
            "field_angle_deg": float(field_angle_deg), "field_image_height_m": field_image,
            "note_tr": "Işınlar giriş gözbebeğinde eksene paralel; tüm yüzeylerde tam (vektörel) Snell yasası uygulandı. "
                       "Açıklık dışına çıkan ışın o yüzeyde kesilir (vignetted_at).",
        },
        "meta": {"wavelengthNm": lam * 1e9, "maxElements": MAX_ELEMENTS},
    }
    return _compact(result)
