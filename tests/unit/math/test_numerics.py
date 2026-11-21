import math

import numpy as np
import pytest

from backend import numerics as nm


# ---- differentiation ----
def test_derivative_schemes_on_sin_and_exp():
    x, h = 0.7, 1e-4
    assert nm.central_diff(math.sin, x, h) == pytest.approx(math.cos(x), abs=1e-8)
    assert nm.forward_diff(math.exp, x, h) == pytest.approx(math.exp(x), rel=1e-3)
    assert nm.backward_diff(math.exp, x, h) == pytest.approx(math.exp(x), rel=1e-3)
    assert nm.second_derivative(math.sin, x, 1e-3) == pytest.approx(-math.sin(x), abs=1e-6)
    assert nm.richardson_derivative(math.sin, x, 0.2) == pytest.approx(math.cos(x), abs=1e-10)
    h_opt = nm.optimal_step(x)
    assert nm.central_diff(math.exp, x, h_opt) == pytest.approx(math.exp(x), rel=1e-9)


def test_gradient_and_laplacian():
    f = lambda x, y, z: x * x + 3 * y * y + z
    assert nm.gradient(f, [1.0, 2.0, 3.0]) == pytest.approx([2.0, 12.0, 1.0], abs=1e-6)
    assert nm.laplacian(f, [1.0, 2.0, 3.0]) == pytest.approx(8.0, abs=1e-4)


# ---- integration ----
def test_simpson_exact_for_cubic_and_trapezoid_not():
    cubic = lambda x: x ** 3 - 2 * x + 1
    exact = 4.0 - 4.0 + 2.0  # on [0,2]
    assert nm.simpson(cubic, 0, 2, 2) == pytest.approx(exact, abs=1e-12)
    assert abs(nm.trapezoid(cubic, 0, 2, 2) - exact) > 0.1


def test_gauss_legendre_exact_to_degree_2n_minus_1():
    n = 4
    poly = lambda x: x ** 7 + 2 * x ** 3 - x + 5
    exact = 1 / 8 + 2 / 4 - 1 / 2 + 5
    assert nm.gauss_legendre(poly, 0, 1, n) == pytest.approx(exact, abs=1e-13)


def test_romberg_and_adaptive_sine():
    val, err = nm.romberg(math.sin, 0, math.pi, tol=1e-12)
    assert val == pytest.approx(2.0, abs=1e-11)
    assert err < 1e-10
    assert nm.adaptive_simpson(math.sin, 0, math.pi, tol=1e-10) == pytest.approx(2.0, abs=1e-9)


def test_2d_and_disk_integrals():
    R = 1.7
    assert nm.integrate_disk(lambda r, t: 1.0, R) == pytest.approx(math.pi * R * R, rel=1e-12)
    assert nm.integrate_2d(lambda x, y: x * y, 0, 2, 0, 3, n=4) == pytest.approx(9.0, abs=1e-12)


def test_path_integral_polyline():
    pts = [[0, 0, 0], [3, 4, 0], [3, 4, 12]]  # lengths 5 + 12
    assert nm.path_integral(lambda p: 1.0, pts) == pytest.approx(17.0)
    assert nm.optical_path_length(lambda p: 1.5, pts) == pytest.approx(25.5)


# ---- ODE ----
def test_rk4_and_euler_on_decay():
    f = lambda t, y: -y
    _, y = nm.rk4(f, [1.0], 0, 1, 20)
    assert y[-1, 0] == pytest.approx(math.exp(-1), abs=1e-6)
    _, ye = nm.euler(f, [1.0], 0, 1, 1000)
    assert ye[-1, 0] == pytest.approx(math.exp(-1), abs=1e-3)


def test_rk45_meets_tolerance():
    for tol in (1e-6, 1e-9):
        t, y = nm.rk45(lambda t, y: -y, [1.0], 0, 2, tol=tol)
        assert t[-1] == pytest.approx(2.0)
        assert abs(y[-1, 0] - math.exp(-2)) < 20 * tol


def test_ray_equation_constant_index_is_straight():
    rhs = nm.ray_equation_rhs(lambda r: 1.3, lambda r: [0.0, 0.0, 0.0])
    d = np.array([1.0, 2.0, 2.0]) / 3.0
    y0 = np.concatenate([[0, 0, 0], 1.3 * d])
    _, y = nm.rk4(rhs, y0, 0, 10, 50)
    assert y[-1, :3] == pytest.approx(10 * d, abs=1e-10)


def test_ray_bends_toward_higher_index():
    n0, g = 1.0, 0.01
    rhs = nm.ray_equation_rhs(lambda r: n0 + g * r[1],
                              lambda r: [0.0, g, 0.0])
    y0 = [0, 0, 0, n0, 0, 0]  # launched along +x
    _, y = nm.rk45(rhs, y0, 0, 20, tol=1e-9)
    assert y[-1, 1] > 0.0  # curved toward +y (higher n)
    assert y[-1, 1] == pytest.approx(g * 20 ** 2 / 2 / n0, rel=0.05)
    assert np.linalg.norm(y[-1, 3:]) == pytest.approx(n0 + g * y[-1, 1], rel=1e-6)


# ---- roots ----
def test_root_finders_sqrt2():
    f = lambda x: x * x - 2
    for root in (nm.bisection(f, 0, 2), nm.newton(f, 1.0, df=lambda x: 2 * x),
                 nm.newton(f, 1.0), nm.secant(f, 1.0, 2.0), nm.brent(f, 0, 2)):
        assert root == pytest.approx(math.sqrt(2), abs=1e-10)


# ---- convergence ----
def test_observed_convergence_orders():
    ns = [8, 16, 32, 64]
    exact = 2.0
    trap = nm.convergence_study(lambda f, a, b, n: nm.trapezoid(f, a, b, n), math.sin, exact, ns, 0, math.pi)
    simp = nm.convergence_study(nm.simpson, math.sin, exact, ns, 0, math.pi)
    p_trap = nm.convergence_order([r["abs_error"] for r in trap], [r["h"] for r in trap])
    p_simp = nm.convergence_order([r["abs_error"] for r in simp], [r["h"] for r in simp])
    assert p_trap == pytest.approx(2.0, abs=0.1)
    assert p_simp == pytest.approx(4.0, abs=0.2)
    assert set(trap[0]) == {"n", "h", "value", "abs_error"}


def test_rk4_observed_order_is_four():
    def method(f, a, b, n):
        return nm.rk4(lambda t, y: -y, [1.0], a, b, n)[1][-1, 0]
    rows = nm.convergence_study(method, None, math.exp(-1), [4, 8, 16, 32], 0, 1)
    p = nm.convergence_order([r["abs_error"] for r in rows], [r["h"] for r in rows])
    assert p == pytest.approx(4.0, abs=0.2)


# ---- physics helpers ----
def test_attenuation_dB():
    pts = [[0, 0, 0], [1000, 0, 0]]
    # alpha = 0.0001 /m over 1 km -> tau = 0.1 -> 0.4343 dB
    assert nm.total_attenuation_dB(lambda p: 1e-4, pts) == pytest.approx(0.1 * 10 * math.log10(math.e))


def test_gaussian_power_in_aperture_matches_analytic():
    for w, R in ((0.05, 0.03), (0.05, 0.1), (1.0, 0.5)):
        res = nm.gaussian_power_in_aperture(w, R)
        assert res["analytic"] == pytest.approx(1 - math.exp(-2 * R * R / (w * w)))
        assert abs(res["numeric"] - res["analytic"]) < 1e-6
    assert nm.gaussian_power_in_aperture(1.0, 1.0, numeric=False)["numeric"] is None


# ---- validation ----
@pytest.mark.parametrize("call", [
    lambda: nm.simpson(math.sin, 0, 1, 3),
    lambda: nm.simpson(math.sin, 0, 1, 0),
    lambda: nm.trapezoid(math.sin, 0, 1, -2),
    lambda: nm.central_diff(math.sin, 0, 0.0),
    lambda: nm.forward_diff(math.sin, 0, -1e-3),
    lambda: nm.romberg(math.sin, 0, 1, tol=0),
    lambda: nm.adaptive_simpson(math.sin, 0, 1, tol=-1),
    lambda: nm.gauss_legendre(math.sin, 0, 1, 0),
    lambda: nm.bisection(lambda x: x * x + 1, -1, 1),
    lambda: nm.brent(lambda x: x * x + 1, -1, 1),
    lambda: nm.rk4(lambda t, y: y, [1.0], 0, 1, 0),
    lambda: nm.rk45(lambda t, y: y, [1.0], 0, 1, tol=0),
    lambda: nm.integrate_disk(lambda r, t: 1, -1.0),
    lambda: nm.convergence_order([1.0], [0.1]),
    lambda: nm.gaussian_power_in_aperture(0.0, 1.0),
])
def test_invalid_inputs_raise_value_error(call):
    with pytest.raises(ValueError):
        call()
