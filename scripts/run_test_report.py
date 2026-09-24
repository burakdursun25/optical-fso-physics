import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
JUNIT_PATH = REPORTS / "test-results.xml"
MARKDOWN_PATH = REPORTS / "test_report.md"


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
                "The suite is headless and validates the Python physics engine without launching the UI.",
                "Unit tests cover mathematical and physical primitives; regression tests compare deterministic ray-trace output with a checked-in JSON fixture.",
                "",
                "## Artifacts",
                "",
                "- `reports/test-results.xml`: JUnit XML for CI systems",
                "- `tests/fixtures/gradient_trace.json`: deterministic ray-trace baseline",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())