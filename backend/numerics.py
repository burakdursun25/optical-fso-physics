"""Numerical analysis toolkit for the FSO physics engine.

Sayisal analiz araclari: turev, integral, ODE cozucu, kok bulma, yakinsama
analizi ve fizik odakli yardimcilar. Only numpy is used (no scipy).

All routines take plain python callables and return floats / lists / dicts so
results are JSON-serializable where noted.
"""
from __future__ import annotations

import math
import sys
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np

EPS = sys.float_info.epsilon


# --------------------------------------------------------------------------
# validation helpers
# --------------------------------------------------------------------------
def _check_pos(value: float, name: str) -> None:
    if not (value > 0) or not math.isfinite(value):
        raise ValueError(f"{name} must be positive and finite, got {value!r}")


def _check_n(n: int, name: str = "n", minimum: int = 1) -> None:
    if not isinstance(n, (int, np.integer)) or isinstance(n, bool) or n < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}, got {n!r}")


# --------------------------------------------------------------------------
# A. Differentiation
# --------------------------------------------------------------------------
def optimal_step(x: float = 1.0, order: str = "central") -> float:
    """Roundoff/truncation-balanced step: eps^(1/3)*max(1,|x|) for central,
    sqrt(eps)*max(1,|x|) for one-sided differences."""
    scale = max(1.0, abs(x))
    if order == "central":
        return EPS ** (1.0 / 3.0) * scale
    if order in ("forward", "backward"):
        return math.sqrt(EPS) * scale
    raise ValueError(f"unknown order {order!r}")


def forward_diff(f: Callable[[float], float], x: float, h: float) -> float:
    """First-order forward difference (f(x+h)-f(x))/h, O(h)."""
    _check_pos(h, "h")
    return (f(x + h) - f(x)) / h


def backward_diff(f: Callable[[float], float], x: float, h: float) -> float:
    """First-order backward difference (f(x)-f(x-h))/h, O(h)."""
    _check_pos(h, "h")
    return (f(x) - f(x - h)) / h


def central_diff(f: Callable[[float], float], x: float, h: float) -> float:
    """Central difference (f(x+h)-f(x-h))/(2h), O(h^2)."""
    _check_pos(h, "h")
    return (f(x + h) - f(x - h)) / (2.0 * h)


def second_derivative(f: Callable[[float], float], x: float, h: float) -> float:
    """Central second derivative (f(x+h)-2f(x)+f(x-h))/h^2, O(h^2)."""
    _check_pos(h, "h")
    return (f(x + h) - 2.0 * f(x) + f(x - h)) / (h * h)


def richardson_derivative(f: Callable[[float], float], x: float, h: float = 0.1,
                          levels: int = 4) -> float:
    """Richardson-extrapolated central derivative (error O(h^(2*levels)))."""
    _check_pos(h, "h")
    _check_n(levels, "levels")
    d = [central_diff(f, x, h / 2 ** i) for i in range(levels)]
    for k in range(1, levels):
        factor = 4.0 ** k
        d = [(factor * d[i + 1] - d[i]) / (factor - 1.0) for i in range(len(d) - 1)]
    return d[0]


def gradient(f: Callable[[float, float, float], float], point: Sequence[float],
             h: float = 1e-5) -> List[float]:
    """Central-difference gradient of scalar field f(x, y, z) -> [fx, fy, fz]."""
    _check_pos(h, "h")
    if len(point) != 3:
        raise ValueError("point must have 3 components")
    p = [float(c) for c in point]
    g = []
    for i in range(3):
        hi = list(p)
        lo = list(p)
        hi[i] += h
        lo[i] -= h
        g.append((f(*hi) - f(*lo)) / (2.0 * h))
    return g


def laplacian(f: Callable[[float, float, float], float], point: Sequence[float],
              h: float = 1e-3) -> float:
    """Laplacian of f(x, y, z) via central second differences."""
    _check_pos(h, "h")
    if len(point) != 3:
        raise ValueError("point must have 3 components")
    p = [float(c) for c in point]
    f0 = f(*p)
    total = 0.0
    for i in range(3):
        hi = list(p)
        lo = list(p)
        hi[i] += h
        lo[i] -= h
        total += (f(*hi) - 2.0 * f0 + f(*lo)) / (h * h)
    return total


# --------------------------------------------------------------------------
# B. Integration
# --------------------------------------------------------------------------
def trapezoid(f: Callable[[float], float], a: float, b: float, n: int) -> float:
    """Composite trapezoid rule with n subintervals, O(h^2)."""
    _check_n(n)
    h = (b - a) / n
    s = 0.5 * (f(a) + f(b)) + sum(f(a + i * h) for i in range(1, n))
    return s * h


def simpson(f: Callable[[float], float], a: float, b: float, n: int) -> float:
    """Composite Simpson rule; n (subintervals) must be even, O(h^4)."""
    _check_n(n, minimum=2)
    if n % 2:
        raise ValueError(f"simpson requires an even n, got {n}")
    h = (b - a) / n
    s = f(a) + f(b)
    s += 4.0 * sum(f(a + i * h) for i in range(1, n, 2))
    s += 2.0 * sum(f(a + i * h) for i in range(2, n, 2))
    return s * h / 3.0


def romberg(f: Callable[[float], float], a: float, b: float, tol: float = 1e-10,
            max_levels: int = 12) -> Tuple[float, float]:
    """Romberg integration. Returns (value, estimated_error)."""
    _check_pos(tol, "tol")
    _check_n(max_levels, "max_levels", 2)
    h = b - a
    prev = [0.5 * h * (f(a) + f(b))]
    value, err = prev[0], float("inf")
    for k in range(1, max_levels):
        h /= 2.0
        mid = sum(f(a + (2 * i - 1) * h) for i in range(1, 2 ** (k - 1) + 1))
        row = [0.5 * prev[0] + h * mid]
        for j in range(1, k + 1):
            row.append(row[j - 1] + (row[j - 1] - prev[j - 1]) / (4.0 ** j - 1.0))
        value, err = row[-1], abs(row[-1] - prev[-1])
        prev = row
        if err < tol:
            break
    return value, err


def gauss_legendre(f: Callable[[float], float], a: float, b: float, n: int = 5) -> float:
    """n-point Gauss-Legendre quadrature, exact for degree <= 2n-1."""
    _check_n(n)
    x, w = np.polynomial.legendre.leggauss(n)
    c, m = 0.5 * (b - a), 0.5 * (b + a)
    return float(c * sum(wi * f(c * xi + m) for xi, wi in zip(x, w)))


def adaptive_simpson(f: Callable[[float], float], a: float, b: float,
                     tol: float = 1e-8, max_depth: int = 40) -> float:
    """Recursive adaptive Simpson with Richardson error correction."""
    _check_pos(tol, "tol")
    _check_n(max_depth, "max_depth")

    def simp(fa, fm, fb, lo, hi):
        return (hi - lo) / 6.0 * (fa + 4.0 * fm + fb)

    def rec(lo, hi, fa, fm, fb, whole, eps, depth):
        mid = 0.5 * (lo + hi)
        lm, rm = 0.5 * (lo + mid), 0.5 * (mid + hi)
        flm, frm = f(lm), f(rm)
        left = simp(fa, flm, fm, lo, mid)
        right = simp(fm, frm, fb, mid, hi)
        delta = left + right - whole
        if depth <= 0 or abs(delta) <= 15.0 * eps:
            return left + right + delta / 15.0
        return (rec(lo, mid, fa, flm, fm, left, eps / 2.0, depth - 1)
                + rec(mid, hi, fm, frm, fb, right, eps / 2.0, depth - 1))

    fa, fb, fm = f(a), f(b), f(0.5 * (a + b))
    return rec(a, b, fa, fm, fb, simp(fa, fm, fb, a, b), tol, max_depth)


def integrate_2d(f: Callable[[float, float], float], xa: float, xb: float,
                 ya: float, yb: float, n: int = 16) -> float:
    """Tensor-product Gauss-Legendre over the rectangle [xa,xb]x[ya,yb]."""
    _check_n(n)
    x, w = np.polynomial.legendre.leggauss(n)
    cx, mx = 0.5 * (xb - xa), 0.5 * (xb + xa)
    cy, my = 0.5 * (yb - ya), 0.5 * (yb + ya)
    total = 0.0
    for xi, wi in zip(x, w):
        for yj, wj in zip(x, w):
            total += wi * wj * f(cx * xi + mx, cy * yj + my)
    return float(total * cx * cy)


def integrate_disk(f: Callable[[float, float], float], R: float,
                   nr: int = 32, ntheta: int = 32) -> float:
    """Integral of f(r, theta) over a disk of radius R (includes Jacobian r).

    Gauss-Legendre in r, uniform (spectrally accurate periodic) rule in theta.
    """
    _check_pos(R, "R")
    _check_n(nr, "nr")
    _check_n(ntheta, "ntheta")
    x, w = np.polynomial.legendre.leggauss(nr)
    r = 0.5 * R * (x + 1.0)
    wr = 0.5 * R * w
    thetas = (np.arange(ntheta) + 0.5) * (2.0 * math.pi / ntheta)
    dth = 2.0 * math.pi / ntheta
    total = 0.0
    for ri, wi in zip(r, wr):
        total += wi * ri * sum(f(float(ri), float(t)) for t in thetas) * dth
    return float(total)


def path_integral(f: Callable[[Sequence[float]], float],
                  points: Sequence[Sequence[float]], n_per_segment: int = 8) -> float:
    """Integral of scalar field f(point) along a polyline.

    Each segment uses Gauss-Legendre with n_per_segment nodes (default 8).
    """
    _check_n(n_per_segment, "n_per_segment")
    if len(points) < 2:
        raise ValueError("path needs at least 2 points")
    x, w = np.polynomial.legendre.leggauss(n_per_segment)
    t = 0.5 * (x + 1.0)
    wt = 0.5 * w
    pts = np.asarray(points, dtype=float)
    total = 0.0
    for p, q in zip(pts[:-1], pts[1:]):
        length = float(np.linalg.norm(q - p))
        if length == 0.0:
            continue
        total += length * sum(wi * f(p + ti * (q - p)) for ti, wi in zip(t, wt))
    return float(total)


# --------------------------------------------------------------------------
# C. ODE solvers  (y' = f(t, y), vector state)
# --------------------------------------------------------------------------
def _as_state(y0) -> np.ndarray:
    return np.atleast_1d(np.asarray(y0, dtype=float))


def _fixed_grid(t0: float, t1: float, n: int):
    _check_n(n)
    return np.linspace(t0, t1, n + 1)


def euler(f, y0, t0: float, t1: float, n: int) -> Tuple[np.ndarray, np.ndarray]:
    """Explicit Euler, n steps. Returns (t array, y array [n+1, dim]). O(h)."""
    t = _fixed_grid(t0, t1, n)
    h = (t1 - t0) / n
    y = np.empty((n + 1, _as_state(y0).size))
    y[0] = _as_state(y0)
    for i in range(n):
        y[i + 1] = y[i] + h * np.asarray(f(t[i], y[i]), dtype=float)
    return t, y


def rk4(f, y0, t0: float, t1: float, n: int) -> Tuple[np.ndarray, np.ndarray]:
    """Classical Runge-Kutta 4, n steps. O(h^4)."""
    t = _fixed_grid(t0, t1, n)
    h = (t1 - t0) / n
    y = np.empty((n + 1, _as_state(y0).size))
    y[0] = _as_state(y0)
    for i in range(n):
        ti, yi = t[i], y[i]
        k1 = np.asarray(f(ti, yi), dtype=float)
        k2 = np.asarray(f(ti + h / 2, yi + h / 2 * k1), dtype=float)
        k3 = np.asarray(f(ti + h / 2, yi + h / 2 * k2), dtype=float)
        k4 = np.asarray(f(ti + h, yi + h * k3), dtype=float)
        y[i + 1] = yi + h / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
    return t, y


_DP_C = (0, 1 / 5, 3 / 10, 4 / 5, 8 / 9, 1.0, 1.0)
_DP_A = (
    (),
    (1 / 5,),
    (3 / 40, 9 / 40),
    (44 / 45, -56 / 15, 32 / 9),
    (19372 / 6561, -25360 / 2187, 64448 / 6561, -212 / 729),
    (9017 / 3168, -355 / 33, 46732 / 5247, 49 / 176, -5103 / 18656),
    (35 / 384, 0, 500 / 1113, 125 / 192, -2187 / 6784, 11 / 84),
)
_DP_B5 = (35 / 384, 0, 500 / 1113, 125 / 192, -2187 / 6784, 11 / 84, 0)
_DP_B4 = (5179 / 57600, 0, 7571 / 16695, 393 / 640, -92097 / 339200, 187 / 2100, 1 / 40)


def rk45(f, y0, t0: float, t1: float, tol: float = 1e-8, h0: float = None,
         max_steps: int = 100000, atol: float = None) -> Tuple[np.ndarray, np.ndarray]:
    """Adaptive Dormand-Prince 5(4) with step-size control.

    tol is the relative tolerance (atol defaults to tol). Returns
    (t array, y array [steps+1, dim]). Requires t1 > t0.
    """
    _check_pos(tol, "tol")
    if not t1 > t0:
        raise ValueError("t1 must be greater than t0")
    atol = tol if atol is None else atol
    _check_pos(atol, "atol")
    t, y = float(t0), _as_state(y0)
    h = (t1 - t0) / 100.0 if h0 is None else h0
    _check_pos(h, "h0")
    ts, ys = [t], [y.copy()]
    for _ in range(max_steps):
        if t >= t1:
            return np.array(ts), np.array(ys)
        h = min(h, t1 - t)
        k = []
        for s in range(7):
            yi = y + h * sum(a * kj for a, kj in zip(_DP_A[s], k)) if s else y
            k.append(np.asarray(f(t + _DP_C[s] * h, yi), dtype=float))
        y5 = y + h * sum(b * kj for b, kj in zip(_DP_B5, k))
        y4 = y + h * sum(b * kj for b, kj in zip(_DP_B4, k))
        scale = atol + tol * np.maximum(np.abs(y), np.abs(y5))
        err = float(np.sqrt(np.mean(((y5 - y4) / scale) ** 2)))
        if err <= 1.0:
            t += h
            y = y5
            ts.append(t)
            ys.append(y.copy())
        factor = 0.9 * (1.0 / max(err, 1e-10)) ** 0.2
        h *= min(5.0, max(0.2, factor))
        if h < 1e-15 * max(1.0, abs(t)):
            raise RuntimeError("rk45 step size underflow")
    raise RuntimeError("rk45 exceeded max_steps")


def ray_equation_rhs(n_func: Callable[[np.ndarray], float],
                     grad_n_func: Callable[[np.ndarray], Sequence[float]]):
    """RHS for the ray equation d/ds(n dr/ds) = grad n.

    State y = [r (3), T (3)] with T = n dr/ds. Then dr/ds = T/n(r) and
    dT/ds = grad n(r). Independent variable is arclength s. |T| = n is
    conserved by the exact flow (eikonal equation).
    """
    def rhs(s, y):
        y = np.asarray(y, dtype=float)
        r, T = y[:3], y[3:]
        return np.concatenate([T / n_func(r), np.asarray(grad_n_func(r), dtype=float)])
    return rhs


# --------------------------------------------------------------------------
# D. Root finding
# --------------------------------------------------------------------------
def _bracket(f, a, b):
    fa, fb = f(a), f(b)
    if fa * fb > 0:
        raise ValueError(f"no sign change on [{a}, {b}]: f(a)={fa}, f(b)={fb}")
    return fa, fb


def bisection(f, a: float, b: float, tol: float = 1e-12, max_iter: int = 200) -> float:
    """Bisection on a bracketing interval."""
    _check_pos(tol, "tol")
    fa, fb = _bracket(f, a, b)
    if fa == 0:
        return float(a)
    if fb == 0:
        return float(b)
    for _ in range(max_iter):
        m = 0.5 * (a + b)
        fm = f(m)
        if fm == 0 or 0.5 * (b - a) < tol:
            return float(m)
        if fa * fm < 0:
            b = m
        else:
            a, fa = m, fm
    return float(0.5 * (a + b))


def newton(f, x0: float, df=None, tol: float = 1e-12, max_iter: int = 100) -> float:
    """Newton's method; uses a central-difference derivative if df is None."""
    _check_pos(tol, "tol")
    x = float(x0)
    for _ in range(max_iter):
        fx = f(x)
        d = df(x) if df is not None else central_diff(f, x, optimal_step(x))
        if d == 0:
            raise ValueError("zero derivative encountered in newton")
        dx = fx / d
        x -= dx
        if abs(dx) < tol * max(1.0, abs(x)):
            return float(x)
    raise RuntimeError("newton did not converge")


def secant(f, x0: float, x1: float, tol: float = 1e-12, max_iter: int = 100) -> float:
    """Secant method."""
    _check_pos(tol, "tol")
    f0, f1 = f(x0), f(x1)
    for _ in range(max_iter):
        if f1 == f0:
            raise ValueError("flat secant (f(x1) == f(x0))")
        x2 = x1 - f1 * (x1 - x0) / (f1 - f0)
        if abs(x2 - x1) < tol * max(1.0, abs(x2)):
            return float(x2)
        x0, f0, x1, f1 = x1, f1, x2, f(x2)
    raise RuntimeError("secant did not converge")


def brent(f, a: float, b: float, tol: float = 1e-12, max_iter: int = 200) -> float:
    """Safeguarded bracketing root finder (Illinois regula falsi with a
    bisection fallback every few steps); always converges on a bracket."""
    _check_pos(tol, "tol")
    fa, fb = _bracket(f, a, b)
    if fa == 0:
        return float(a)
    if fb == 0:
        return float(b)
    side = 0
    for i in range(max_iter):
        c = (a * fb - b * fa) / (fb - fa)
        if i % 4 == 3 or not (min(a, b) < c < max(a, b)):
            c = 0.5 * (a + b)
        fc = f(c)
        if fc == 0 or abs(b - a) < tol:
            return float(c)
        if fc * fb > 0:
            b, fb = c, fc
            if side == -1:
                fa *= 0.5
            side = -1
        else:
            a, fa = c, fc
            if side == 1:
                fb *= 0.5
            side = 1
        if abs(b - a) < tol:
            return float(c)
    return float(c)


# --------------------------------------------------------------------------
# E. Error / convergence analysis
# --------------------------------------------------------------------------
def convergence_order(errors: Sequence[float], hs: Sequence[float]) -> float:
    """Observed order p from a least-squares fit of log(err) = p*log(h) + c."""
    if len(errors) != len(hs) or len(errors) < 2:
        raise ValueError("need at least two matching (error, h) pairs")
    if any(e <= 0 for e in errors) or any(h <= 0 for h in hs):
        raise ValueError("errors and step sizes must be positive")
    p, _ = np.polyfit(np.log(hs), np.log(errors), 1)
    return float(p)


def convergence_study(method: Callable[[Callable, float, float, int], float],
                      f: Callable, exact: float, ns: Sequence[int],
                      a: float = 0.0, b: float = 1.0) -> List[Dict[str, float]]:
    """Run method(f, a, b, n) for each n and compare with the exact value.

    Returns JSON-serializable rows {n, h, value, abs_error}.
    """
    if len(ns) == 0:
        raise ValueError("ns must not be empty")
    rows = []
    for n in ns:
        _check_n(n)
        value = float(method(f, a, b, n))
        rows.append({"n": int(n), "h": (b - a) / n, "value": value,
                     "abs_error": abs(value - exact)})
    return rows


# --------------------------------------------------------------------------
# F. Physics-facing helpers
# --------------------------------------------------------------------------
def optical_path_length(n_func: Callable[[np.ndarray], float],
                        points: Sequence[Sequence[float]], n_per_segment: int = 8) -> float:
    """Optical path length  OPL = integral of n ds along a polyline [m]."""
    return path_integral(n_func, points, n_per_segment)


def total_attenuation_dB(alpha_func_per_m: Callable[[np.ndarray], float],
                         points: Sequence[Sequence[float]], n_per_segment: int = 8) -> float:
    """Attenuation in dB: 10*log10(e) * integral of alpha ds (alpha in 1/m)."""
    tau = path_integral(alpha_func_per_m, points, n_per_segment)
    return 10.0 * math.log10(math.e) * tau


def gaussian_power_in_aperture(w: float, R: float, numeric: bool = True) -> Dict[str, float]:
    """Fraction of a unit-power Gaussian beam (1/e^2 radius w) inside radius R.

    Returns {"analytic": 1-exp(-2R^2/w^2), "numeric": ..., "abs_error": ...}.
    numeric=False skips the quadrature (numeric/abs_error are None).
    """
    _check_pos(w, "w")
    _check_pos(R, "R")
    analytic = 1.0 - math.exp(-2.0 * R * R / (w * w))
    if not numeric:
        return {"analytic": analytic, "numeric": None, "abs_error": None}

    def intensity(r, theta):
        return 2.0 / (math.pi * w * w) * math.exp(-2.0 * r * r / (w * w))

    num = integrate_disk(intensity, R, nr=64, ntheta=4)
    return {"analytic": analytic, "numeric": num, "abs_error": abs(num - analytic)}
