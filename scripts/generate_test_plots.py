#!/usr/bin/env python3
"""Generate test/validation figures into reports/figures/ (headless Agg backend).

Usage:  python3 scripts/generate_test_plots.py [--junit reports/test-results.xml]

Importable: ``generate_all(junit_path=None) -> {name: Path}``; individual
plot_* functions return the written Path.  Requires matplotlib.
"""
import argparse
import math
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from backend import bitcheck, numerics, optics  # noqa: E402

FIG_DIR = ROOT / "reports" / "figures"
DEFAULT_JUNIT = ROOT / "reports" / "test-results.xml"

# one fixed colour per lens preset (colour-blind friendly) used in every figure
PRESET_COLORS = OrderedDict([
    ("none", "#555555"), ("collimating", "#0072B2"), ("focusing", "#E69F00"),
    ("keplerian_telescope", "#009E73"), ("galilean_telescope", "#CC79A7"),
    ("cassegrain", "#56B4E9"), ("diverging", "#D55E00"),
])
C_OK, C_ERR, C_SENT, C_RX = "#009E73", "#D62728", "#0072B2", "#7f7f7f"

MARGINAL_DISTANCE_M = 5000.0          # bare beam, BER of a few 1e-2
MARGINAL_PAYLOAD = "Hello FSO!"


def _style():
    plt.rcParams.update({
        "figure.dpi": 110, "savefig.dpi": 130, "axes.grid": True, "grid.alpha": 0.3,
        "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
        "legend.frameon": False,
    })


def _save(fig, name):
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    path = FIG_DIR / name
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def _pretty(key):
    return optics.FSO_LENS_PRESETS[key]["name"]


# ---------------------------------------------------------------- a, b
def plot_ber_vs_distance():
    d = np.logspace(2, 5, 150)
    fig, ax = plt.subplots(figsize=(10, 4.8))
    for key, col in PRESET_COLORS.items():
        ber = [max(bitcheck.link_ber(key, float(x)), 1e-300) for x in d]
        ax.semilogy(d / 1000, ber, color=col, lw=2, label=_pretty(key))
    ax.axhline(1e-9, color="k", ls="--", lw=1)
    ax.text(0.12, 1.6e-9, "BER = 1e-9", fontsize=8)
    ax.set_ylim(1e-12, 1.0)
    ax.set_xscale("log")
    ax.set_xlabel("Distance [km]")
    ax.set_ylabel("OOK bit error rate (theory)")
    ax.set_title("BER vs distance per lens preset\n(10 dBm Tx, -30 dBm rms noise, 1550 nm, 25 mm Rx radius)", fontsize=10)
    ax.legend(fontsize=8, loc="center left", bbox_to_anchor=(1.01, 0.5))
    return _save(fig, "ber_vs_distance.png")


def plot_link_gain_vs_distance():
    d = np.logspace(1, 4.5, 100)
    fig, ax = plt.subplots(figsize=(10, 4.8))
    for key, col in PRESET_COLORS.items():
        if key == "none":
            continue
        g = [optics.lens_link_gain_dB(key, float(x)) for x in d]
        ax.plot(d / 1000, g, color=col, lw=2, label=_pretty(key),
                ls="--" if key == "galilean_telescope" else "-")   # identical to Keplerian
    ax.axhline(0, color="#555555", lw=1, ls="--", label="Lens Yok (referans)")
    ax.set_xscale("log")
    ax.set_xlabel("Distance [km]")
    ax.set_ylabel("Link gain vs no lens [dB]")
    ax.set_title("Lens link gain vs distance (positive = better than bare beam;\nKeplerian and Galilean curves coincide)", fontsize=10)
    ax.legend(fontsize=8, loc="center left", bbox_to_anchor=(1.01, 0.5))
    return _save(fig, "lens_gain_vs_distance.png")


# ---------------------------------------------------------------- c
def plot_beam_radius():
    key, lam = "galilean_telescope", 1550e-9
    p = optics.build_preset(key)
    z = np.linspace(0, 4000, 200)
    w_with = [optics.beam_through_system(p.default_w0, lam, p.system, float(x), 1.0,
                                         p.source_offset_m)["w"] for x in z]
    z0 = p.source_offset_m + p.system.total_length
    w_bare = [optics.beam_radius(float(x) + z0, p.default_w0, lam) for x in z]
    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.plot(z, np.array(w_bare) * 1e3, color=PRESET_COLORS["none"], lw=2, label="Without expander")
    ax.plot(z, np.array(w_with) * 1e3, color=PRESET_COLORS[key], lw=2,
            label=f"With Galilean expander (M = {p.magnification:.1f}x)")
    ax.set_xlabel("Distance behind transmitter output z [m]")
    ax.set_ylabel("Beam radius w(z) (1/e$^2$) [mm]")
    ax.set_title(f"Gaussian beam radius, w0 = {p.default_w0 * 1e3:.1f} mm, 1550 nm")
    ax.legend()
    return _save(fig, "beam_radius.png")


# ---------------------------------------------------------------- d
def _fit(hs, errs, floor=1e-13):
    pts = [(h, e) for h, e in zip(hs, errs) if e > floor]
    if len(pts) < 2:
        return None
    return numerics.convergence_order([e for _, e in pts], [h for h, _ in pts])


def plot_convergence():
    f, a, b, exact = math.exp, 0.0, 1.0, math.e - 1.0
    fig, ax = plt.subplots(figsize=(8, 5.2))
    series = []
    ns = [2, 4, 8, 16, 32, 64, 128]
    for name, m, col in (("Trapezoid", numerics.trapezoid, "#0072B2"),
                         ("Simpson", numerics.simpson, "#009E73")):
        rows = numerics.convergence_study(m, f, exact, ns, a, b)
        series.append((name, [r["h"] for r in rows], [r["abs_error"] for r in rows], col, ""))
    rows = numerics.convergence_study(numerics.gauss_legendre, f, exact, [1, 2, 3, 4, 5, 6], a, b)
    series.append(("Gauss-Legendre", [r["h"] for r in rows], [r["abs_error"] for r in rows],
                   "#E69F00", " (spectral)"))
    hs, es = [], []
    for k in range(2, 8):
        v, _ = numerics.romberg(f, a, b, tol=1e-300, max_levels=k)
        hs.append((b - a) / 2 ** (k - 1))
        es.append(abs(v - exact))
    series.append(("Romberg", hs, es, "#CC79A7", " (spectral)"))
    ode_ns = [4, 8, 16, 32, 64, 128, 256]
    for name, solver, col in (("Euler (ODE)", numerics.euler, "#D55E00"),
                              ("RK4 (ODE)", numerics.rk4, "#56B4E9")):
        e = [abs(float(solver(lambda t, y: -y, 1.0, 0.0, 1.0, n)[1][-1][0]) - math.exp(-1)) for n in ode_ns]
        series.append((name, [1.0 / n for n in ode_ns], e, col, ""))
    for name, h, e, col, note in series:
        p = None if note else _fit(h, e)   # spectral methods: no algebraic order
        lab = (f"{name}, p = {p:.2f}" if p is not None
               else f"{name}, super-algebraic (no fixed p)")
        ax.loglog(h, np.maximum(e, 1e-17), "o-", color=col, lw=1.8, ms=4, label=lab)
    hh = np.array([1e-3, 0.5])
    for p_ref in (1, 2, 4):
        ax.loglog(hh, 0.3 * hh ** p_ref, color="#bbbbbb", lw=0.8, ls=":")
        ax.text(hh[0] * 1.1, 0.3 * hh[0] ** p_ref * 1.6, f"h$^{p_ref}$", color="#888888", fontsize=8)
    ax.set_ylim(1e-17, 1)
    ax.set_xlabel("Step size h")
    ax.set_ylabel("Absolute error")
    ax.set_title("Numerical convergence: integral of e$^x$ on [0,1]; ODE y' = -y")
    ax.legend(fontsize=8, loc="lower right")
    return _save(fig, "numerical_convergence.png")


# ---------------------------------------------------------------- e, f
def marginal_report():
    """Deterministic marginal-link example (first seed that gives 2-6 bit errors)."""
    for seed in range(1, 200):
        rep = bitcheck.run_link_check(MARGINAL_PAYLOAD, MARGINAL_DISTANCE_M, "none", seed=seed)
        if 2 <= rep["comparison"]["errors"] <= 6:
            return rep
    return rep


def plot_bit_timeline(rep=None):
    rep = rep or marginal_report()
    sent = [int(c) for c in rep["sent_bits"]]
    rx = [None if c == "?" else int(c) for c in rep["received_bits"]]
    err = set(rep["comparison"]["error_positions"])
    n = len(sent)
    x = np.arange(n + 1)
    fig, ax = plt.subplots(figsize=(13, 4.2))
    ax.step(x, sent + [sent[-1]], where="post", color=C_SENT, lw=1.8, label="Sent")
    ax.step(x, [(0 if b is None else b) - 1.6 for b in rx] + [(rx[-1] or 0) - 1.6], where="post",
            color=C_RX, lw=1.8, label="Received")
    for i in sorted(err):
        ax.axvspan(i, i + 1, color=C_ERR, alpha=0.25, lw=0)
        ax.plot([i + 0.5], [(rx[i] or 0) - 1.6], "o", color=C_ERR, ms=6)
    # frame field labels
    fields = [("preamble", 16), ("sync", 16), ("length", 16),
              ("payload", rep["payload_bytes"] * 8), ("CRC-16", 16)]
    pos = 0
    for name, width in fields:
        ax.axvline(pos, color="k", lw=0.6, alpha=0.5)
        ax.text(pos + width / 2, 1.35, name, ha="center", fontsize=8)
        pos += width
    ax.set_xticks(range(0, n + 1, 8))
    ax.set_yticks([-1.6, -0.6, 0, 1])
    ax.set_yticklabels(["Rx 0", "Rx 1", "Tx 0", "Tx 1"])
    ax.set_xlabel("Bit index (tick = byte boundary)")
    ax.set_ylabel("Logic level")
    ax.set_xlim(0, n)
    ax.set_ylim(-2.1, 1.7)
    crc = "OK" if rep["frame"]["crc_ok"] else "FAIL"
    c = rep["comparison"]
    ax.set_title(f"Bit I/O timeline, {rep['distance_m'] / 1000:.1f} km, no lens: "
                 f"{c['errors']}/{c['total']} bit errors (BER {c['ber']:.3f}), CRC {crc}",
                 color=C_OK if rep["frame"]["crc_ok"] else C_ERR)
    ax.legend(loc="lower right", ncol=2)
    ax.grid(axis="y", alpha=0)
    return _save(fig, "bit_io_timeline.png")


def plot_error_map(rep=None, trials=400):
    rep = rep or marginal_report()
    nbytes = len(rep["comparison"]["byte_errors"])
    agg = np.zeros(nbytes)
    for seed in range(trials):
        r = bitcheck.run_link_check(MARGINAL_PAYLOAD, MARGINAL_DISTANCE_M, "none", seed=seed)
        agg += np.array(r["comparison"]["byte_errors"])
    ber = rep["link"]["ber_theory"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.2), gridspec_kw={"width_ratios": [1, 1.3]})
    be = rep["comparison"]["byte_errors"]
    a1.bar(range(nbytes), be, color=[C_ERR if v else C_OK for v in be])
    a1.set_xlabel("Byte index in frame")
    a1.set_ylabel("Bit errors in byte")
    a1.set_ylim(0, max(2, max(be) + 1))
    a1.set_title("Per-byte error map (single frame)")
    a2.bar(range(nbytes), agg / trials, color="#0072B2", label=f"Simulated mean ({trials} seeds)")
    a2.axhline(8 * ber, color=C_ERR, ls="--", label=f"Expected 8 x BER = {8 * ber:.2f}")
    a2.set_xlabel("Byte index in frame")
    a2.set_ylabel("Mean bit errors per byte")
    a2.set_title("Error position histogram (uniform for random BSC errors)")
    a2.legend(fontsize=8)
    return _save(fig, "error_position_map.png")


# ---------------------------------------------------------------- g
def read_junit(junit_path):
    """{module: {"passed": n, "failed": n, "skipped": n}} from a JUnit XML file."""
    root = ET.parse(junit_path).getroot()
    mods = OrderedDict()
    for tc in root.iter("testcase"):
        mod = tc.attrib.get("classname", "?").split(".")
        mod = ".".join(mod[-1:]) if mod[-1].startswith("test_") else ".".join(mod[-2:])
        m = mods.setdefault(mod, {"passed": 0, "failed": 0, "skipped": 0})
        if tc.find("failure") is not None or tc.find("error") is not None:
            m["failed"] += 1
        elif tc.find("skipped") is not None:
            m["skipped"] += 1
        else:
            m["passed"] += 1
    return OrderedDict(sorted(mods.items()))


def plot_test_summary(junit_path=DEFAULT_JUNIT):
    junit_path = Path(junit_path)
    if not junit_path.exists():
        raise FileNotFoundError(f"{junit_path} not found; run pytest with --junitxml first")
    mods = read_junit(junit_path)
    names = list(mods)
    fig, ax = plt.subplots(figsize=(9, 0.42 * len(names) + 1.8))
    y = np.arange(len(names))
    left = np.zeros(len(names))
    for key, col, lab in (("passed", C_OK, "Passed"), ("failed", C_ERR, "Failed"),
                          ("skipped", "#E69F00", "Skipped")):
        vals = np.array([mods[n][key] for n in names], dtype=float)
        ax.barh(y, vals, left=left, color=col, label=lab)
        for yi, (v, l) in enumerate(zip(vals, left)):
            if v:
                ax.text(l + v / 2, yi, f"{int(v)}", ha="center", va="center", color="white", fontsize=8)
        left += vals
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Number of tests")
    total = int(left.sum())
    failed = sum(m["failed"] for m in mods.values())
    ax.set_title(f"Test results by module: {total - failed}/{total} passed")
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(axis="y", alpha=0)
    return _save(fig, "test_results_summary.png")


# ---------------------------------------------------------------- driver
def generate_all(junit_path=None):
    """Create every figure; returns {filename: Path}.  A failing figure raises."""
    _style()
    rep = marginal_report()
    out = OrderedDict()
    for fn in (plot_ber_vs_distance, plot_link_gain_vs_distance, plot_beam_radius,
               plot_convergence, lambda: plot_bit_timeline(rep), lambda: plot_error_map(rep)):
        p = fn()
        out[p.name] = p
    junit_path = Path(junit_path) if junit_path else DEFAULT_JUNIT
    if junit_path.exists():
        p = plot_test_summary(junit_path)
        out[p.name] = p
    else:
        print(f"[plots] {junit_path} missing; skipping test summary figure")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--junit", default=str(DEFAULT_JUNIT), help="JUnit XML for the test summary bar chart")
    args = ap.parse_args(argv)
    for name, path in generate_all(args.junit).items():
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
