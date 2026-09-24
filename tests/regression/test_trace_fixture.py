import json
from pathlib import Path

import pytest

from backend.engine import AtmosphereGrid, RayTracer, Vec3

FIXTURE = Path(__file__).parents[1] / "fixtures" / "gradient_trace.json"


def test_gradient_trace_matches_deterministic_fixture():
    expected = json.loads(FIXTURE.read_text())
    config = expected["input"]
    atmosphere = AtmosphereGrid(*config["grid"], config["cellSize"])
    atmosphere.applyThermalProfile(
        config["profile"],
        {
            "baseTemp": 25,
            "deltaT": 15,
            "humidity": 50,
            "windSpeed": 5,
            "Cn2Level": "moderate",
            "wavelengthUm": 1.55,
            "useEdlen": True,
            "seed": config["seed"],
        },
    )
    source = Vec3(50.0, 200.0, 200.0)
    target = Vec3(750.0, 200.0, 200.0)
    trace = RayTracer(atmosphere).trace(source, target.sub(source).normalize())
    expected_trace = expected["trace"]

    assert trace["success"] is expected_trace["success"]
    assert trace["steps"] == expected_trace["steps"]
    assert len(trace["path"]) == expected_trace["pathPoints"]
    assert len(trace["refractionEvents"]) == expected_trace["refractionEvents"]
    assert trace["totalDistance"] == pytest.approx(expected_trace["totalDistance"], abs=1e-9)
    assert trace["avgCn2"] == pytest.approx(expected_trace["avgCn2"], rel=1e-12)
    assert trace["exitPosition"].x == pytest.approx(expected_trace["exit"]["x"], abs=1e-9)
