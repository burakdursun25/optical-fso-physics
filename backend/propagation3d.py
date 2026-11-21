"""3D Gaussian-beam propagation through an AtmosphereGrid.

The beam is marched from `source` to `target` (world metres; x along the grid
X axis, z = altitude).  At every sample the local cell values (n, Cn2, T,
humidity, weather extinction) are trilinearly interpolated between cell
centres and the following quantities are accumulated:

* cumulative extinction  A(s) = integral alpha(s') ds'   [dB]   (trapezoid)
* Gaussian beam radius w(s): optics.beam_radius (no lens) or
  optics.beam_through_system (transmit lens preset, includes clipping loss)
* plane-wave Rytov variance with a *non-uniform* Cn2 profile
      sigma_R^2(s) = 2.25 k^(7/6) * Int_0^s Cn2(s') (s - s')^(5/6) ds'
  (reduces to 1.23 Cn2 k^(7/6) s^(11/6) for homogeneous Cn2)
* long-term (turbulence broadened) beam radius, weak/moderate regime
      w_LT = w * sqrt(1 + 1.33 sigma_R^2 Lambda^(5/6)),  Lambda = 2 s / (k w^2)
* rms beam wander (Andrews & Phillips, collimated beam)
      <r_c^2> = 2.42 Cn2_avg s^3 w(0)^(-1/3)
* scintillation index sigma_I^2 (Andrews plane-wave saturating model)
* centroid deflection by refraction: the ray equation d/ds(n dr/ds) = grad n
  (numerics.ray_equation_rhs; bending ~ grad_perp(n)/n) is integrated with
  numerics.rk4 using grad n from numerics.gradient on the interpolated n field.
  Refraction bends the ray towards HIGHER n.  Differential refraction across
  the beam (millimetres) is negligible next to the 100 m cell scale, so the
  1/e^2 "marginal rays" share the centroid bending: each ring point is
  centroid + w_LT * (cos(phi) e1 + sin(phi) e2).

A degradation cloud of voxels (grid cells) shows where the light is most
degraded: score = beam_share * (0.35 f_att + 0.40 f_cn2 + 0.25 f_grad) with
saturating references (see `score_components`), beam_share = 1 within 3 w_LT
of the beam and decaying with a half-cell length scale outside.

Everything returned is plain Python (JSON serialisable).
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Sequence

import numpy as np

from backend import numerics, optics
from backend.engine import AtmosphericLoss

MAX_SAMPLES = 2000
MAX_RAYS = 64
MAX_VOXELS = 4000
MAX_RING_SAMPLES = 64          # decimated number of rings returned

# score references / weights
W_ATT, W_CN2, W_GRAD = 0.35, 0.40, 0.25
ALPHA_REF_DB_KM = 10.0
CN2_LOG_LO, CN2_LOG_HI = -17.0, -13.0
GRAD_REF = 5e-8                # |grad n| [1/m]

REASONS_TR = {
    "attenuation": "Yoğun zayıflama (sis/yağmur/kar) — ışık burada en çok sönümleniyor",
    "turbulence": "Güçlü türbülans (yüksek Cn²) — ışın dalgalanması ve genişlemesi",
    "gradient": "Kırılma indisi gradyanı — ışın burada sapıyor",
}
REASONS_EN = {
    "attenuation": "Strong extinction (fog/rain/snow): light is attenuated most here",
    "turbulence": "Strong turbulence (high Cn2): beam wander and spreading",
    "gradient": "Refractive-index gradient: the beam is bent here",
}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _r(x, sig: int = 6):
    """Round to `sig` significant digits and return a plain float."""
    x = float(x)
    if x == 0.0 or not math.isfinite(x):
        return x if math.isfinite(x) else 0.0
    return float(f"{x:.{sig}g}")


def scintillation_index_from_rytov(sigma2_R: float) -> float:
    """Plane-wave scintillation index sigma_I^2 for Rytov variance sigma2_R
    (Andrews & Phillips, valid from weak to saturated turbulence)."""
    s2 = max(float(sigma2_R), 0.0)
    if s2 == 0.0:
        return 0.0
    s = math.sqrt(s2)
    t = s ** (12.0 / 5.0)
    return math.exp(0.49 * s2 / (1.0 + 1.11 * t) ** (7.0 / 6.0)
                    + 0.51 * s2 / (1.0 + 0.69 * t) ** (5.0 / 6.0)) - 1.0


def weather_alpha_dB_km(weather: str, wavelength_m: float) -> float:
    """Extinction [dB/km] from the engine's weather model."""
    return float(AtmosphericLoss.absorptionLoss(1000.0, weather, wavelength_m * 1e9)["alpha_dB_km"])


def pointing_coupling(w: float, a: float, d: float) -> float:
    """Gaussian beam power (radius w) captured by a circular aperture (radius a)
    with centroid offset d (Farid & Hranilovic 2007):
        A0 = 1 - exp(-2 a^2/w^2),  w_eq^2 = w^2 sqrt(pi) erf(v) / (2 v exp(-v^2)),
        coupling = A0 exp(-2 d^2 / w_eq^2),  v = sqrt(pi) a / (sqrt(2) w)."""
    a0 = 1.0 - math.exp(-2.0 * a * a / (w * w))
    if d <= 0.0:
        return a0
    v = math.sqrt(math.pi) * a / (math.sqrt(2.0) * w)
    # log of w_eq^2 (avoids overflow of exp(v^2))
    log_weq2 = (math.log(w * w) + 0.5 * math.log(math.pi) + math.log(max(math.erf(v), 1e-300))
                - math.log(2.0 * v) + v * v)
    expo = -2.0 * d * d * math.exp(-log_weq2) if log_weq2 < 700 else 0.0
    return a0 * math.exp(max(expo, -700.0))


class _Fields:
    """3D arrays (z, y, x) of the grid plus optional anomalies, with trilinear
    interpolation between cell centres (clamped at the boundary)."""

    def __init__(self, grid, weather: str, wavelength_m: float, anomalies=None):
        self.gx, self.gy, self.gz = int(grid.gridX), int(grid.gridY), int(grid.gridZ)
        self.cs = float(grid.cellSize)
        shape = (self.gz, self.gy, self.gx)
        self.n = np.array(grid.grid, dtype=np.float64).reshape(shape)
        self.cn2 = np.array(grid.Cn2, dtype=np.float64).reshape(shape)
        self.temp = np.array(grid.temperature, dtype=np.float64).reshape(shape)
        self.hum = np.array(grid.humidity, dtype=np.float64).reshape(shape)
        a0 = weather_alpha_dB_km(weather, wavelength_m)
        self.alpha = np.full(shape, a0, dtype=np.float64)
        self.alpha_weather = a0
        for an in (anomalies or []):
            self._apply(an)

    def _apply(self, an: Dict) -> None:
        cx, cy, cz = (float(v) for v in an["center"])
        sx, sy, sz = (float(v) for v in an["size"])
        xs = (np.arange(self.gx) + 0.5) * self.cs
        ys = (np.arange(self.gy) + 0.5) * self.cs
        zs = (np.arange(self.gz) + 0.5) * self.cs
        mx = np.abs(xs - cx) <= sx / 2.0
        my = np.abs(ys - cy) <= sy / 2.0
        mz = np.abs(zs - cz) <= sz / 2.0
        mask = mz[:, None, None] & my[None, :, None] & mx[None, None, :]
        self.cn2[mask] *= float(an.get("cn2_factor", 1.0))
        self.alpha[mask] += float(an.get("extra_alpha_dB_km", 0.0))
        dT = float(an.get("delta_T", 0.0))
        if dT:
            # dn/dT = -(n-1)/T  (ideal-gas density dependence)
            T_K = self.temp[mask] + 273.15
            self.n[mask] += -(self.n[mask] - 1.0) / T_K * dT
            self.temp[mask] += dT

    def interp(self, arr: np.ndarray, x: float, y: float, z: float) -> float:
        cs = self.cs
        out = []
        idx = []
        for c, g in ((x, self.gx), (y, self.gy), (z, self.gz)):
            f = min(max(c / cs - 0.5, 0.0), float(g - 1))
            i0 = min(int(f), max(g - 2, 0))
            i1 = min(i0 + 1, g - 1)
            idx.append((i0, i1, f - i0 if g > 1 else 0.0))
        (x0, x1, tx), (y0, y1, ty), (z0, z1, tz) = idx
        c00 = arr[z0, y0, x0] * (1 - tx) + arr[z0, y0, x1] * tx
        c10 = arr[z0, y1, x0] * (1 - tx) + arr[z0, y1, x1] * tx
        c01 = arr[z1, y0, x0] * (1 - tx) + arr[z1, y0, x1] * tx
        c11 = arr[z1, y1, x0] * (1 - tx) + arr[z1, y1, x1] * tx
        c0 = c00 * (1 - ty) + c10 * ty
        c1 = c01 * (1 - ty) + c11 * ty
        return float(c0 * (1 - tz) + c1 * tz)


def _grad3(arr: np.ndarray, cs: float) -> np.ndarray:
    """|grad| of a (z,y,x) array with spacing cs (0 along singleton axes)."""
    comps = []
    for ax in range(3):
        if arr.shape[ax] < 2:
            comps.append(np.zeros_like(arr))
        else:
            comps.append(np.gradient(arr, cs, axis=ax))
    return np.sqrt(comps[0] ** 2 + comps[1] ** 2 + comps[2] ** 2)


def score_components(alpha_dB_km, cn2, grad_n):
    """Saturating [0,1] maps of the three degradation drivers (numpy or float)."""
    f_att = 1.0 - np.exp(-np.asarray(alpha_dB_km, dtype=float) / ALPHA_REF_DB_KM)
    f_cn2 = np.clip((np.log10(np.maximum(np.asarray(cn2, dtype=float), 1e-30)) - CN2_LOG_LO)
                    / (CN2_LOG_HI - CN2_LOG_LO), 0.0, 1.0)
    f_grad = 1.0 - np.exp(-np.asarray(grad_n, dtype=float) / GRAD_REF)
    return f_att, f_cn2, f_grad


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n == 0:
        raise ValueError("source and target coincide")
    return v / n


# --------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------
def propagate_beam_3d(grid, source: Sequence[float], target: Sequence[float],
                      w0: Optional[float] = None, wavelength: float = 1550e-9,
                      lens_preset: Optional[str] = None, n_samples: int = 200,
                      n_rays: int = 16, seed: int = 0, weather: str = "clear",
                      rx_radius: float = 0.05, m2: float = 1.0,
                      anomalies: Optional[List[Dict]] = None,
                      max_voxels: int = 2000, min_voxel_score: float = 0.02) -> Dict:
    """Propagate a Gaussian beam from source to target through `grid`.

    anomalies: optional list of boxes {center:[x,y,z], size:[dx,dy,dz],
    cn2_factor, extra_alpha_dB_km, delta_T} applied to private copies of the
    grid arrays (the grid itself is never modified).  Extra extinction models
    local fog patches, delta_T a hot/cold air bubble (changes n), cn2_factor a
    turbulent pocket.  Returns a JSON-serialisable dict (see module docstring and
    reports/api_contract_v2.md)."""
    if not 2 <= int(n_samples) <= MAX_SAMPLES:
        raise ValueError(f"n_samples must be within [2, {MAX_SAMPLES}]")
    if not 3 <= int(n_rays) <= MAX_RAYS:
        raise ValueError(f"n_rays must be within [3, {MAX_RAYS}]")
    if not 1 <= int(max_voxels) <= MAX_VOXELS:
        raise ValueError(f"max_voxels must be within [1, {MAX_VOXELS}]")
    if wavelength <= 0 or rx_radius <= 0:
        raise ValueError("wavelength and rx_radius must be positive")
    n_samples, n_rays = int(n_samples), int(n_rays)

    F = _Fields(grid, weather, wavelength, anomalies)
    cs = F.cs
    bounds = (F.gx * cs, F.gy * cs, F.gz * cs)
    src = np.asarray(source, dtype=float)
    tgt = np.asarray(target, dtype=float)
    for p in (src, tgt):
        if p.shape != (3,) or np.any(p < 0) or np.any(p > np.asarray(bounds) + 1e-9):
            raise ValueError("source/target must be inside the grid bounds")
    dirv = _unit(tgt - src)
    L = float(np.linalg.norm(tgt - src))
    k = 2.0 * math.pi / wavelength

    # ---- optics --------------------------------------------------------
    preset = optics.build_preset(lens_preset) if lens_preset else None
    if w0 is None:
        w0 = preset.default_w0 if preset else 2.0e-3
    use_lens = preset is not None and preset.role == "transmit"
    s_arr = np.linspace(0.0, L, n_samples + 1)
    if use_lens:
        w_geo = np.array([optics.beam_through_system(w0, wavelength, preset.system, float(s), m2,
                                                    preset.source_offset_m)["w"] for s in s_arr])
        lens_T = optics.beam_through_system(w0, wavelength, preset.system, 0.0, m2,
                                            preset.source_offset_m)["total_transmission"]
    else:
        w_geo = np.array([optics.beam_radius(float(s), w0, wavelength, m2) for s in s_arr])
        lens_T = 1.0

    # ---- ray equation (centroid) --------------------------------------
    h = 0.5 * cs

    def n_func(r):
        return F.interp(F.n, r[0], r[1], r[2])

    def grad_n(r):
        return numerics.gradient(lambda x, y, z: F.interp(F.n, x, y, z), r, h)

    rhs = numerics.ray_equation_rhs(n_func, grad_n)
    y0 = np.concatenate([src, n_func(src) * dirv])
    _, y = numerics.rk4(rhs, y0, 0.0, L, n_samples)
    path = y[:, :3]
    straight = src[None, :] + s_arr[:, None] * dirv[None, :]
    dev = path - straight
    dev_perp = dev - (dev * dirv[None, :]).sum(axis=1)[:, None] * dirv[None, :]
    defl = np.linalg.norm(dev_perp, axis=1)

    # ---- local samples along the ray ----------------------------------
    n_loc = np.array([n_func(p) for p in path])
    cn2_loc = np.array([F.interp(F.cn2, *p) for p in path])
    T_loc = np.array([F.interp(F.temp, *p) for p in path])
    hum_loc = np.array([F.interp(F.hum, *p) for p in path])
    alpha_loc = np.array([F.interp(F.alpha, *p) for p in path])
    gn_loc = np.array([np.linalg.norm(grad_n(p)) for p in path])

    ds = s_arr[1] - s_arr[0]
    cum_atten = np.concatenate([[0.0], np.cumsum(0.5 * (alpha_loc[1:] + alpha_loc[:-1]) * ds / 1000.0)])

    # Rytov variance with profile, wander, long-term radius
    diff = s_arr[:, None] - s_arr[None, :]
    W = np.where(diff > 0, np.power(np.maximum(diff, 0.0), 5.0 / 6.0), 0.0)
    W[:, 0] *= 0.5
    sigma2 = 2.25 * k ** (7.0 / 6.0) * ds * (W * cn2_loc[None, :]).sum(axis=1)
    cn2_cum_avg = np.concatenate([[cn2_loc[0]], np.cumsum(0.5 * (cn2_loc[1:] + cn2_loc[:-1]) * ds)
                                  / np.maximum(s_arr[1:], 1e-30)])
    lam_fresnel = 2.0 * s_arr / (k * w_geo ** 2)
    w_lt = w_geo * np.sqrt(1.0 + 1.33 * sigma2 * np.power(lam_fresnel, 5.0 / 6.0))
    wander = np.sqrt(2.42 * cn2_cum_avg * s_arr ** 3 * w_geo[0] ** (-1.0 / 3.0))
    scint = np.array([scintillation_index_from_rytov(v) for v in sigma2])

    f_att, f_cn2, f_grad = score_components(alpha_loc, cn2_loc, gn_loc)
    local_score = W_ATT * f_att + W_CN2 * f_cn2 + W_GRAD * f_grad

    offset = np.sqrt(defl ** 2 + wander ** 2)
    ap = np.array([pointing_coupling(float(w_lt[i]), rx_radius, float(offset[i]))
                   for i in range(len(s_arr))])
    power_frac = np.power(10.0, -cum_atten / 10.0) * lens_T * ap

    # ---- rings ----------------------------------------------------------
    helper = np.array([0.0, 0.0, 1.0]) if abs(dirv[2]) < 0.95 else np.array([1.0, 0.0, 0.0])
    e1 = _unit(np.cross(dirv, helper))
    e2 = np.cross(dirv, e1)
    rng = random.Random(seed)
    phi0 = rng.random() * 2.0 * math.pi
    phis = phi0 + 2.0 * math.pi * np.arange(n_rays) / n_rays
    ring_idx = np.unique(np.linspace(0, n_samples, min(n_samples + 1, MAX_RING_SAMPLES)).round().astype(int))
    rings = []
    for i in ring_idx:
        pts = (path[i][None, :] + w_lt[i] * (np.cos(phis)[:, None] * e1[None, :]
                                             + np.sin(phis)[:, None] * e2[None, :]))
        rings.append([[_r(c, 10) for c in pt] for pt in pts])

    centerline = [{
        "s": _r(s_arr[i]), "x": _r(path[i, 0], 10), "y": _r(path[i, 1], 10), "z": _r(path[i, 2], 10),
        "w": _r(w_geo[i]), "w_lt": _r(w_lt[i]), "wander": _r(wander[i]),
        "power_frac": _r(power_frac[i]), "atten_dB": _r(cum_atten[i]),
        "scint_index": _r(scint[i]), "sigma2_R": _r(sigma2[i]),
        "deflection": _r(defl[i]), "n": _r(n_loc[i], 10), "cn2": _r(cn2_loc[i]),
        "temp_C": _r(T_loc[i]), "humidity": _r(hum_loc[i]), "alpha_dB_km": _r(alpha_loc[i]),
        "grad_n": _r(gn_loc[i]), "score": _r(local_score[i]),
    } for i in range(n_samples + 1)]

    # ---- degradation cloud ---------------------------------------------
    zz, yy, xx = np.meshgrid((np.arange(F.gz) + 0.5) * cs, (np.arange(F.gy) + 0.5) * cs,
                             (np.arange(F.gx) + 0.5) * cs, indexing="ij")
    centers = np.stack([xx.ravel(), yy.ravel(), zz.ravel()], axis=1)
    sc = np.clip(((centers - src) * dirv[None, :]).sum(axis=1), 0.0, L)
    ci = np.clip(np.rint(sc / L * n_samples).astype(int), 0, n_samples)
    dvec = np.abs(centers - path[ci]) - cs / 2.0
    d_box = np.linalg.norm(np.maximum(dvec, 0.0), axis=1)
    near = 3.0 * w_lt[ci]
    beam_share = np.where(d_box <= near, 1.0, np.exp(-(d_box - near) / (0.5 * cs)))
    gcell = _grad3(F.n, cs).ravel()
    acell, ccell = F.alpha.ravel(), F.cn2.ravel()
    fa, fc, fg = score_components(acell, ccell, gcell)
    score = beam_share * (W_ATT * fa + W_CN2 * fc + W_GRAD * fg)
    max_score = float(score.max())
    order = np.argsort(-score, kind="stable")
    sel = order[:max_voxels]
    sel = sel[score[sel] >= min_voxel_score]
    if sel.size == 0:
        sel = order[:1]
    sigma2_cell = 1.23 * ccell * k ** (7.0 / 6.0) * cs ** (11.0 / 6.0)
    voxels = [{"x": _r(centers[j, 0], 7), "y": _r(centers[j, 1], 7), "z": _r(centers[j, 2], 7),
               "score": _r(score[j]), "atten_dB_km": _r(acell[j]), "cn2": _r(ccell[j]),
               "grad_n": _r(gcell[j]), "scint": _r(sigma2_cell[j])} for j in sel]

    # ---- hotspot / worst segment ----------------------------------------
    def middle_of_max(vals: np.ndarray) -> int:
        idx = np.flatnonzero(vals >= vals.max() - 1e-9 * max(1.0, abs(vals.max())))
        runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
        run = runs[0]
        return int(run[len(run) // 2])

    hi = middle_of_max(local_score)
    contrib = np.stack([W_ATT * f_att, W_CN2 * f_cn2, W_GRAD * f_grad])
    excess = contrib[:, hi] - np.median(contrib, axis=1)
    dominant = ("attenuation", "turbulence", "gradient")[int(np.argmax(excess))]
    hotspot = {"x": _r(path[hi, 0], 7), "y": _r(path[hi, 1], 7), "z": _r(path[hi, 2], 7),
               "s": _r(s_arr[hi]), "score": _r(local_score[hi]), "dominant": dominant,
               "reason_tr": REASONS_TR[dominant], "reason_en": REASONS_EN[dominant]}

    win = max(1, int(round(0.1 * n_samples)))
    csum = np.concatenate([[0.0], np.cumsum(local_score)])
    means = (csum[win:] - csum[:-win]) / win          # window starting at i
    wi = middle_of_max(means)
    worst = {"s_start": _r(s_arr[wi]), "s_end": _r(s_arr[min(wi + win, n_samples)]),
             "score": _r(means[wi])}

    summary = {
        "length_m": _r(L), "total_atten_dB": _r(cum_atten[-1]),
        "final_w": _r(w_geo[-1]), "final_w_lt": _r(w_lt[-1]),
        "wander_m": _r(wander[-1]), "deflection_m": _r(defl[-1]),
        "power_at_rx_frac": _r(power_frac[-1]),
        "power_at_rx_dB": _r(-10.0 * math.log10(max(float(power_frac[-1]), 1e-300))),
        "sigma2_R": _r(sigma2[-1]), "scint_index": _r(scint[-1]),
        "lens_transmission": _r(lens_T), "weather_alpha_dB_km": _r(F.alpha_weather),
        "worst_segment": worst,
    }
    return {
        "centerline": centerline, "rings": rings,
        "degradation_cloud": {"voxels": voxels, "max_score": _r(max_score),
                              "hotspot": hotspot, "cell_size": _r(cs),
                              "n_voxels": len(voxels)},
        "summary": summary,
        "bounds": {"x": _r(bounds[0]), "y": _r(bounds[1]), "z": _r(bounds[2])},
        "ring_sample_indices": [int(i) for i in ring_idx],
    }
