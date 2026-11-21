import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
JUNIT_PATH = REPORTS / "test-results.xml"
MARKDOWN_PATH = REPORTS / "test_report.md"
FIGURES = [
    ("ber_vs_distance.png", "BER vs distance per lens preset"),
    ("lens_gain_vs_distance.png", "Lens link gain vs distance"),
    ("beam_radius.png", "Gaussian beam radius with/without Galilean expander"),
    ("numerical_convergence.png", "Numerical convergence (log-log, fitted orders)"),
    ("bit_io_timeline.png", "Bit I/O timeline at a marginal distance"),
    ("error_position_map.png", "Error position histogram / per-byte error map"),
    ("test_results_summary.png", "Test results per module"),
]


def make_plots():
    """Generate figures; never raises. Returns (set of written names, note)."""
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import generate_test_plots
        return set(generate_test_plots.generate_all(JUNIT_PATH)), None
    except Exception as exc:  # matplotlib missing, plotting bug, ...
        return set(), f"Figures skipped: {type(exc).__name__}: {exc}"


def bitcheck_table():
    try:
        sys.path.insert(0, str(ROOT))
        from backend import bitcheck
        rows = bitcheck.summary_rows()
    except Exception as exc:
        return [f"_Bit-check summary unavailable: {type(exc).__name__}: {exc}_"]
    lines = ["| Payload | Lens | Distance [m] | P_rx [dBm] | BER (theory) | Bit errors | BER (measured) | CRC ok |",
             "|---|---|---:|---:|---:|---:|---:|:---:|"]
    for r in rows:
        lines.append(
            f"| {r['payload']} | {r['preset']} | {r['distance_m']:.0f} | {r['p_rx_dBm']:.1f} | "
            f"{r['ber_theory']:.2e} | {r['errors']}/{r['total']} | {r['ber']:.4f} | "
            f"{'yes' if r['crc_ok'] else 'NO'} |")
    return lines


def main():
    REPORTS.mkdir(exist_ok=True)
    command = [sys.executable, "-m", "pytest", "-q", f"--junitxml={JUNIT_PATH}"]
    completed = subprocess.run(command, cwd=ROOT, text=True)

    root = ET.parse(JUNIT_PATH).getroot()
    suite = root.find("testsuite") if root.tag == "testsuites" else root
    if suite is None:
        raise RuntimeError("JUnit report does not contain a testsuite element")
    tests = int(suite.attrib.get("tests", 0))
    failures = int(suite.attrib.get("failures", 0))
    errors = int(suite.attrib.get("errors", 0))
    skipped = int(suite.attrib.get("skipped", 0))
    passed = tests - failures - errors - skipped
    duration = suite.attrib.get("time", "0")
    status = "PASSED" if completed.returncode == 0 else "FAILED"

    written, plot_note = make_plots()
    figure_lines = ["## Figures", ""]
    if plot_note:
        figure_lines += [f"_{plot_note}_", ""]
    for name, caption in FIGURES:
        if name in written:
            figure_lines += [f"### {caption}", "", f"![{caption}](figures/{name})", ""]

    MARKDOWN_PATH.write_text(
        "\n".join(
            [
                "# Automated Physics Test Report",
                "",
                f"- **Status:** {status}",
                f"- **Generated:** {datetime.now(timezone.utc).isoformat()}",
                f"- **Total:** {tests}",
                f"- **Passed:** {passed}",
                f"- **Failed:** {failures}",
                f"- **Errors:** {errors}",
                f"- **Skipped:** {skipped}",
                f"- **Duration:** {duration}s",
                "",
                "## Scope",
                "",
                "The headless suite covers physics primitives, API validation/serialization, and COMSOL export data.",
                "Regression tests cover transmission matrices and deterministic ray traces; browser UI and licensed COMSOL solver execution are separate checks.",
                "",
                "## Bit-level I/O check",
                "",
                "Frame = preamble + sync + length + payload + CRC-16/CCITT, sent through a seeded OOK channel (seed 1, 10 dBm Tx, -30 dBm rms noise).",
                "",
                *bitcheck_table(),
                "",
                *figure_lines,
                "## Artifacts",
                "",
                "- `reports/test-results.xml`: JUnit XML for CI systems",
                "- `reports/figures/*.png`: plots from `scripts/generate_test_plots.py`",
                "- `tests/fixtures/gradient_trace.json`: deterministic ray-trace baseline",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())