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


def test_comsol_exports_reflect_dynamic_distance_divergence_and_cn2():
    from backend.engine import Vec3

    atmosphere = AtmosphereGrid(20, 4, 4, 100.0)  # 2 km
    atmosphere.applyThermalProfile("gradient", {"Cn2Level": "strong"})
    src, tgt = Vec3(0.0, 200.0, 200.0), Vec3(2000.0, 200.0, 200.0)
    params = {"beamDivRad": 0.0025, "Cn2": float(atmosphere.Cn2.mean())}
    bridge = COMSOLBridge()

    java_script = bridge.generateModelScript(atmosphere, src, tgt, params)
    params_txt = bridge.generateParametersFile(atmosphere, src, tgt, params)

    assert "2km.mph" in java_script
    assert "6km.mph" not in java_script
    assert "0.0025 [rad]" in java_script
    assert "alpha_div 0.0025[rad]" in params_txt
    assert f"Cn2 {params['Cn2']}[m^(-2/3)]" in params_txt