import pytest

from backend.comsol import COMSOLBridge
from backend.engine import AtmosphereGrid


def test_grid_csv_exports_pressure_from_the_simulated_atmosphere():
    atmosphere = AtmosphereGrid(1, 1, 1, 12000.0)

    rows = [
        line for line in COMSOLBridge().exportGridCSV(atmosphere).splitlines()
        if line and not line.startswith("%")
    ]
    values = [float(value.strip()) for value in rows[0].split(",")]

    assert len(rows) == 1
    assert len(values) == 6
    assert values[5] == pytest.approx(float(atmosphere.pressure[0]), abs=0.1)