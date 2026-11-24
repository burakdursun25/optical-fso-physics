import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app import app, SimParams, TransmissionTestParams, coerce_numpy, run_simulation_backend

client = TestClient(app)


def test_sim_params_accept_ui_grid_and_bound_combined_resources():
    params = SimParams(gridX=600, gridY=10, gridZ=10, cellSize=100.0)
    assert params.gridX == 600

    with pytest.raises(ValidationError, match="cell limit"):
        SimParams(gridX=500, gridY=100, gridZ=100, cellSize=100.0)


def test_grid_height_is_bounded_before_atmosphere_construction():
    with pytest.raises(ValidationError, match="grid height"):
        TransmissionTestParams(gridZ=100, cellSize=5000.0)


def test_sim_params_reject_unknown_physics_options():
    with pytest.raises(ValidationError):
        SimParams(weather="hurricane")


def test_pointing_error_reaches_link_budget():
    params = SimParams(
        gridX=4,
        gridY=2,
        gridZ=2,
        cellSize=100.0,
        pointingErrorRad=1e-3,
        humidity=65.0,
    )

    atmosphere, _, link_budget, _, _, _ = run_simulation_backend(params)

    assert link_budget["geometric"]["pointingOffsetM"] > 0.0
    assert atmosphere.humidity.max() > 65.0
    assert atmosphere.humidity.min() < 65.0


def test_api_serialization_preserves_boolean_values():
    result = coerce_numpy({"success": True, "linkViable": False})

    assert result == {"success": True, "linkViable": False}
    assert type(result["success"]) is bool
    assert type(result["linkViable"]) is bool


def test_send_data_endpoint_returns_json_safe_zero_bit_results():
    response = client.post("/api/send-data", json={
        "simParams": {
            "gridX": 8,
            "gridY": 4,
            "gridZ": 4,
            "cellSize": 100.0,
            "weather": "storm",
        },
        "pattern": "all_zeros",
        "numBits": 2,
        "useArq": False,
    })

    assert response.status_code == 200
    result = response.json()
    assert result["correctBits"] == 0
    assert result["successRate"] == 0.0
    assert result["missedZeros"] == 2
    assert result["results"][0]["linkBudget"]["linkViable"] is False
    assert all(bit["linkBudget"] for bit in result["results"])


def test_simulation_endpoint_rejects_oversized_grid_before_work():
    response = client.post("/api/simulate", json={
        "gridX": 600,
        "gridY": 100,
        "gridZ": 100,
        "cellSize": 100.0,
    })

    assert response.status_code == 422
    assert "cell limit" in response.text


def test_api_does_not_enable_cross_origin_access_by_default():
    response = client.get("/api/health", headers={"Origin": "https://untrusted.example"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_unhandled_simulation_errors_do_not_leak_details(monkeypatch):
    def fail_simulation(_params):
        raise RuntimeError("private internal detail")

    monkeypatch.setattr("backend.app.run_simulation_backend", fail_simulation)
    response = client.post("/api/simulate", json={})

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert "private internal detail" not in response.text


def test_transmission_test_bounds_combined_environment_cells():
    with pytest.raises(ValidationError, match="cell limit"):
        TransmissionTestParams(gridX=200, gridY=40, gridZ=40, cellSize=10.0)


def test_sim_params_validate_optional_lens_dimensions():
    with pytest.raises(ValidationError):
        SimParams(lensAperture_m=-0.5)
    with pytest.raises(ValidationError):
        SimParams(lensFocalLength_m=float("inf"))


def test_send_data_legacy_large_packet_does_not_duplicate_trace():
    response = client.post("/api/send-data", json={
        "simParams": {"gridX": 60, "gridY": 4, "gridZ": 4, "cellSize": 100.0},
        "pattern": "alternating",
        "numBits": 1000,
        "useArq": False,
    })

    assert response.status_code == 200
    assert len(response.content) < 500_000
    rows = response.json()["results"]
    assert sum(1 for r in rows if r["trace"] is not None) == 1
    assert rows[-1]["trace"] is not None
    assert rows[-1]["linkBudget"] is not None


def test_truncated_arq_and_duplex_frames_fail_safely():
    from backend import arq, duplex

    assert arq.parse_ack([0] * 16)["ok"] is False
    assert duplex._parse([0] * 16)["ok"] is False
