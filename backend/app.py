from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import math

from backend.engine import (Vec3, AtmosphereGrid, RayTracer, DataTransmissionSimulator,
                             AtmosphericLoss, vectorialSnellLaw, verifySnellLaw,
                             LensSystem, EnvironmentPresets, ComprehensiveTransmissionTest)
from backend.comsol import COMSOLBridge

app = FastAPI(title="FSO Physics Engine Backend")

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic Request Models ──────────────────────────────────
class SimParams(BaseModel):
    gridX: int = 60
    gridY: int = 10
    gridZ: int = 10
    cellSize: float = 100.0
    thermalProfile: str = "gradient"
    baseTemp: float = 25.0
    deltaT: float = 15.0
    weather: str = "clear"
    Cn2Level: str = "moderate"
    windSpeed: float = 5.0
    wavelengthNm: int = 1550
    laserPower_mW: float = 10.0
    receiverDiamM: float = 0.10
    beamDivRad: float = 0.001
    # Lens system
    lensType: str = "none"
    lensFocalLength_m: Optional[float] = None
    lensAperture_m: Optional[float] = None
    # Environment preset (if set, overrides thermal/weather/Cn2 params)
    environmentPreset: Optional[str] = None

class SendDataParams(BaseModel):
    simParams: SimParams
    pattern: str = "random"
    numBits: int = 32

class TransmissionTestParams(BaseModel):
    gridX: int = 30
    gridY: int = 6
    gridZ: int = 6
    cellSize: float = 200.0
    wavelengthNm: float = 1550.0
    numBits: int = 32
    pattern: str = "alternating"
    laserPower_mW: float = 10.0
    receiverDiamM: float = 0.10
    beamDivRad: float = 0.001
    receiverSensitivity_dBm: float = -40.0


# ── Serialization Helpers ─────────────────────────────────────
def coerce_numpy(obj):
    import numpy as np
    if isinstance(obj, dict):
        return {k: coerce_numpy(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [coerce_numpy(x) for x in obj]
    elif isinstance(obj, tuple):
        return tuple(coerce_numpy(x) for x in obj)
    elif isinstance(obj, (np.floating, float)):
        return float(obj)
    elif isinstance(obj, (np.integer, int)):
        return int(obj)
    elif isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    else:
        return obj

def serialize_vec3(v: Vec3) -> Dict[str, float]:
    return {"x": v.x, "y": v.y, "z": v.z}

def serialize_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    serialized_path = []
    for p in trace.get("path", []):
        serialized_path.append({
            "position": serialize_vec3(p["position"]),
            "gridCell": p["gridCell"],
            "n": p["n"],
            "direction": serialize_vec3(p["direction"]),
            "distance": p["distance"]
        })

    serialized_events = []
    for e in trace.get("refractionEvents", []):
        evt_dict = {
            "position": serialize_vec3(e["position"]),
            "type": e["type"],
            "n1": e["n1"],
            "n2": e["n2"],
            "cell": e["cell"]
        }
        if "incidentDir" in e:
            evt_dict["incidentDir"] = serialize_vec3(e["incidentDir"])
        if "refractedDir" in e:
            evt_dict["refractedDir"] = serialize_vec3(e["refractedDir"])
        if "verification" in e:
            v = e["verification"]
            evt_dict["verification"] = {
                "n1_sin_theta_i": v["n1_sin_theta_i"],
                "n2_sin_theta_t": v["n2_sin_theta_t"],
                "error": v["error"],
                "valid": v["valid"]
            }
        serialized_events.append(evt_dict)

    return {
        "success": trace["success"],
        "path": serialized_path,
        "refractionEvents": serialized_events,
        "totalDistance": trace["totalDistance"],
        "totalInternalReflections": trace["totalInternalReflections"],
        "steps": trace["steps"],
        "avgCn2": trace["avgCn2"],
        "exitPosition": serialize_vec3(trace["exitPosition"]) if trace["exitPosition"] else None,
        "exitDirection": serialize_vec3(trace["exitDirection"]) if trace["exitDirection"] else None
    }

# ── Helper to build grid & transmitter ──────────────────────
def run_simulation_backend(p: SimParams):
    wavelengthUm = p.wavelengthNm / 1000.0
    laserPower_W = p.laserPower_mW / 1000.0

    # Apply environment preset if set (overrides individual params)
    thermal_profile = p.thermalProfile
    weather = p.weather
    cn2_level = p.Cn2Level
    base_temp = p.baseTemp
    delta_t = p.deltaT
    wind_speed = p.windSpeed

    if p.environmentPreset:
        preset = EnvironmentPresets.get_preset(p.environmentPreset)
        thermal_profile = preset["thermalProfile"]
        weather = preset["weather"]
        cn2_level = preset["Cn2Level"]
        base_temp = preset["baseTemp"]
        delta_t = preset["deltaT"]
        wind_speed = preset["windSpeed"]

    atmosphere = AtmosphereGrid(p.gridX, p.gridY, p.gridZ, p.cellSize)
    atmosphere.applyThermalProfile(thermal_profile, {
        "baseTemp": base_temp,
        "deltaT": delta_t,
        "humidity": 50.0,
        "windSpeed": wind_speed,
        "Cn2Level": cn2_level,
        "wavelengthUm": wavelengthUm,
        "useEdlen": True
    })

    rayTracer = RayTracer(atmosphere)

    sourcePos = Vec3(0.5 * p.cellSize, p.gridY * p.cellSize * 0.5, p.gridZ * p.cellSize * 0.5)
    targetPos = Vec3((p.gridX - 0.5) * p.cellSize, p.gridY * p.cellSize * 0.5, p.gridZ * p.cellSize * 0.5)

    # Apply lens system
    lens = LensSystem(p.lensType, p.lensFocalLength_m, p.lensAperture_m)
    lensed_beam = lens.apply_to_beam({
        "divergenceRad": p.beamDivRad,
        "beamDiamM": 0.004,
        "receiverDiamM": p.receiverDiamM
    })
    recvR = lensed_beam["receiverDiamM"] / 2.0

    dir_vec = targetPos.sub(sourcePos).normalize()
    trace = rayTracer.trace(sourcePos, dir_vec)

    distance = sourcePos.sub(targetPos).length()
    link_budget = AtmosphericLoss.linkBudget({
        "distance_m": distance,
        "laserPower_W": laserPower_W,
        "weather": weather,
        "wavelengthNm": p.wavelengthNm,
        "beamDivRad": lensed_beam["divergenceRad"],
        "receiverDiamM": lensed_beam["receiverDiamM"],
        "beamDiamM": 0.004,
        "Cn2": trace["avgCn2"] if trace["avgCn2"] > 0 else 1e-15
    })
    # Apply lens transmittance power penalty
    link_budget["P_rx_dBm"] += 10.0 * math.log10(max(1e-300, lensed_beam["transmittance"]))
    link_budget["P_rx_W"] = math.pow(10.0, link_budget["P_rx_dBm"] / 10.0) / 1000.0
    link_budget["linkMargin_dB"] = link_budget["P_rx_dBm"] - (-40.0)
    link_budget["linkViable"] = link_budget["linkMargin_dB"] > 0
    link_budget["lensType"] = p.lensType
    link_budget["lensGain_dB"] = round(lens.get_link_budget_gain_dB(distance, p.beamDivRad, p.receiverDiamM, 0.004), 2)

    return atmosphere, trace, link_budget, sourcePos, targetPos, recvR

# ── Endpoints ────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "backend": "python"}

@app.post("/api/simulate")
def simulate(p: SimParams):
    try:
        atmosphere, trace, link_budget, sourcePos, targetPos, recvR = run_simulation_backend(p)
        res = {
            "trace": serialize_trace(trace),
            "linkBudget": link_budget,
            "sourcePos": serialize_vec3(sourcePos),
            "targetPos": serialize_vec3(targetPos),
            "receiverRadius": recvR
        }
        return coerce_numpy(res)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/send-data")
def send_data(params: SendDataParams):
    try:
        p = params.simParams
        wavelengthUm = p.wavelengthNm / 1000.0
        laserPower_W = p.laserPower_mW / 1000.0

        atmosphere = AtmosphereGrid(p.gridX, p.gridY, p.gridZ, p.cellSize)
        atmosphere.applyThermalProfile(p.thermalProfile, {
            "baseTemp": p.baseTemp,
            "deltaT": p.deltaT,
            "humidity": 50.0,
            "windSpeed": p.windSpeed,
            "Cn2Level": p.Cn2Level,
            "wavelengthUm": wavelengthUm,
            "useEdlen": True
        })

        rayTracer = RayTracer(atmosphere)
        sourcePos = Vec3(0.5 * p.cellSize, p.gridY * p.cellSize * 0.5, p.gridZ * p.cellSize * 0.5)
        targetPos = Vec3((p.gridX - 0.5) * p.cellSize, p.gridY * p.cellSize * 0.5, p.gridZ * p.cellSize * 0.5)
        recvR = p.receiverDiamM / 2.0

        simulator = DataTransmissionSimulator(rayTracer, sourcePos, targetPos, recvR)
        data = DataTransmissionSimulator.generateTestData(params.pattern, params.numBits)

        results = simulator.sendPacket(data)

        # Serialize Vec3 elements inside the result trace
        serialized_results = []
        for r in results["results"]:
            res_dict = {
                "sent": r["sent"],
                "received": r["received"],
                "correct": r["correct"],
                "lateralDeviation": r["lateralDeviation"],
                "signalPower": r["signalPower"],
                "linkBudget": r["linkBudget"]
            }
            if r.get("trace"):
                res_dict["trace"] = serialize_trace(r["trace"])
            else:
                res_dict["trace"] = None
            serialized_results.append(res_dict)

        res = {
            "data": results["data"],
            "results": serialized_results,
            "totalBits": results["totalBits"],
            "correctBits": results["correctBits"],
            "bitErrorRate": results["bitErrorRate"],
            "totalOnes": results["totalOnes"],
            "totalZeros": results["totalZeros"],
            "missedOnes": results["missedOnes"],
            "successRate": results["successRate"]
        }
        return coerce_numpy(res)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/test")
def run_tests():
    # Runs the 11 unit tests matching JS unit tests
    test_results = []
    
    # Test 1: Normal incidence
    try:
        i = Vec3(0, 0, -1)
        n = Vec3(0, 0, 1)
        result = vectorialSnellLaw(i, n, 1.0, 1.5)
        pass_t1 = result is not None and abs(result["direction"].z - (-1.0)) < 1e-6
        test_results.append({"name": "T1 – Normal geliş (θi=0°)", "pass": pass_t1, "error": ""})
    except Exception as e:
        test_results.append({"name": "T1 – Normal geliş (θi=0°)", "pass": False, "error": str(e)})

    # Test 2: Air→Glass 30°
    try:
        theta_i = 30.0 * math.pi / 180.0
        i = Vec3(math.sin(theta_i), 0, -math.cos(theta_i))
        n = Vec3(0, 0, 1)
        result = vectorialSnellLaw(i, n, 1.0, 1.5)
        expectedSin = (1.0 / 1.5) * math.sin(theta_i)
        actualSin = math.sqrt(1.0 - result["cosTheta_t"] * result["cosTheta_t"])
        pass_t2 = abs(actualSin - expectedSin) < 1e-10
        test_results.append({"name": "T2 – Hava→Cam (30°)", "pass": pass_t2, "error": f"hata={abs(actualSin - expectedSin):.3e}"})
    except Exception as e:
        test_results.append({"name": "T2 – Hava→Cam (30°)", "pass": False, "error": str(e)})

    # Test 3: TIR
    try:
        critAngle = math.asin(1.0 / 1.5)
        theta = critAngle + 5.0 * math.pi / 180.0
        i = Vec3(math.sin(theta), 0, -math.cos(theta))
        result = vectorialSnellLaw(i, Vec3(0, 0, 1), 1.5, 1.0)
        pass_t3 = result is None
        test_results.append({"name": "T3 – TIR (θ>41.8°)", "pass": pass_t3, "error": ""})
    except Exception as e:
        test_results.append({"name": "T3 – TIR (θ>41.8°)", "pass": False, "error": str(e)})

    # Test 4: Atmosphere boundary (6 km realistic delta n)
    try:
        n1, n2 = 1.000293, 1.000270
        theta_i = 5.0 * math.pi / 180.0
        i = Vec3(math.cos(theta_i), math.sin(theta_i), 0)
        result = vectorialSnellLaw(i, Vec3(-1, 0, 0), n1, n2)
        verify = verifySnellLaw(n1, n2, result["cosTheta_i"], result["cosTheta_t"])
        test_results.append({"name": "T4 – Atmosfer sınırı (Δn=-2.3e-5)", "pass": verify["valid"], "error": f"hata={verify['error']:.3e}"})
    except Exception as e:
        test_results.append({"name": "T4 – Atmosfer sınırı (Δn=-2.3e-5)", "pass": False, "error": str(e)})

    # Test 5: Reciprocity
    try:
        n1, n2 = 1.0003, 1.0001
        theta_i = 10.0 * math.pi / 180.0
        i = Vec3(math.sin(theta_i), 0, -math.cos(theta_i))
        forward = vectorialSnellLaw(i, Vec3(0, 0, 1), n1, n2)
        backward = vectorialSnellLaw(forward["direction"].scale(-1.0), Vec3(0, 0, -1), n2, n1)
        recovered = backward["direction"].scale(-1.0)
        error = math.sqrt(math.pow(recovered.x - i.x, 2) + math.pow(recovered.y - i.y, 2) + math.pow(recovered.z - i.z, 2))
        pass_t5 = error < 1e-10
        test_results.append({"name": "T5 – Tersinirlik", "pass": pass_t5, "error": f"hata={error:.3e}"})
    except Exception as e:
        test_results.append({"name": "T5 – Tersinirlik", "pass": False, "error": str(e)})

    # Test 6: Tangential conservation
    try:
        n1, n2 = 1.33, 1.0
        theta_i = 20.0 * math.pi / 180.0
        i = Vec3(math.sin(theta_i), 0, -math.cos(theta_i))
        n = Vec3(0, 0, 1)
        result = vectorialSnellLaw(i, n, n1, n2)
        i_tan = i.sub(n.scale(i.dot(n)))
        t_tan = result["direction"].sub(n.scale(result["direction"].dot(n)))
        pass_t6 = abs(n1 * i_tan.length() - n2 * t_tan.length()) < 1e-10
        test_results.append({"name": "T6 – Teğetsel korunum", "pass": pass_t6, "error": ""})
    except Exception as e:
        test_results.append({"name": "T6 – Teğetsel korunum", "pass": False, "error": str(e)})

    # Test 7: Angle sweep with atmosphere indices
    try:
        sweepPass = True
        n1, n2 = 1.000293, 1.000310
        angles = [0, 5, 10, 15, 20, 30, 45, 60, 75, 85]
        for deg in angles:
            theta = deg * math.pi / 180.0
            i = Vec3(math.sin(theta), 0, -math.cos(theta))
            result = vectorialSnellLaw(i, Vec3(0, 0, 1), n1, n2)
            if result:
                v = verifySnellLaw(n1, n2, result["cosTheta_i"], result["cosTheta_t"])
                if not v["valid"]:
                    sweepPass = False
        test_results.append({"name": "T7 – Açı tarama (0°-85°)", "pass": sweepPass, "error": ""})
    except Exception as e:
        test_results.append({"name": "T7 – Açı tarama (0°-85°)", "pass": False, "error": str(e)})

    # Test 8: Beer-Lambert absorption (6 km clear)
    try:
        loss = AtmosphericLoss.absorptionLoss(6000.0, "clear", 1550.0)
        expectedLoss_dB = 0.2 * 6.0
        pass_t8 = abs(loss["loss_dB"] - expectedLoss_dB) < 0.5
        test_results.append({"name": "T8 – Beer-Lambert 6km clear", "pass": pass_t8, "error": f"{loss['loss_dB']:.2f} dB (beklenen ~{expectedLoss_dB:.1f} dB)"})
    except Exception as e:
        test_results.append({"name": "T8 – Beer-Lambert 6km clear", "pass": False, "error": str(e)})

    # Test 9: Geometric loss at 6 km
    try:
        geo = AtmosphericLoss.geometricLoss(6000.0, 0.001, 0.1, 0.004)
        expectedBeamDiam = 0.004 + 2.0 * 0.001 * 6000.0
        pass_t9 = abs(geo["beamDiamAtReceiver"] - expectedBeamDiam) < 0.01
        test_results.append({"name": "T9 – Geometrik yayılma 6km", "pass": pass_t9, "error": f"Ø={geo['beamDiamAtReceiver']:.2f}m (beklenen ~{expectedBeamDiam:.2f}m)"})
    except Exception as e:
        test_results.append({"name": "T9 – Geometrik yayılma 6km", "pass": False, "error": str(e)})

    # Test 10: Edlén equation verification
    try:
        atm = AtmosphereGrid(2, 2, 2, 1.0)
        n_15C = atm.refractiveIndexSimple(15.0, 101325.0)
        n_35C = atm.refractiveIndexSimple(35.0, 101325.0)
        pass_t10 = n_15C > n_35C and abs(n_15C - 1.000293) < 1e-5
        test_results.append({"name": "T10 – Edlén", "pass": pass_t10, "error": f"n(15°C)={n_15C:.8f}, n(35°C)={n_35C:.8f}"})
    except Exception as e:
        test_results.append({"name": "T10 – Edlén", "pass": False, "error": str(e)})

    # Test 11: Scintillation index
    try:
        scint = AtmosphericLoss.scintillationIndex(6000.0, 1e-15, 1550e-9)
        pass_t11 = scint["sigma2_R"] > 0 and not math.isnan(scint["sigma2_R"])
        test_results.append({"name": "T11 – Rytov scintilasyon", "pass": pass_t11, "error": f"σ²ᵣ={scint['sigma2_R']:.3e} ({scint['regime']})"})
    except Exception as e:
        test_results.append({"name": "T11 – Rytov scintilasyon", "pass": False, "error": str(e)})

    # Test 12: LensSystem – Collimating lens reduces divergence
    try:
        lens = LensSystem("collimating")
        result_beam = lens.apply_to_beam({"divergenceRad": 1e-3, "beamDiamM": 0.004, "receiverDiamM": 0.1})
        pass_t12 = result_beam["divergenceRad"] < 1e-3 and result_beam["receiverDiamM"] > 0.1
        test_results.append({"name": "T12 – Kollimating lens diverjans azaltma", "pass": pass_t12,
                             "error": f"div={result_beam['divergenceRad']:.2e} rad, recvR={result_beam['receiverDiamM']:.4f}m"})
    except Exception as e:
        test_results.append({"name": "T12 – Kollimating lens diverjans azaltma", "pass": False, "error": str(e)})

    # Test 13: LensSystem – Cassegrain gives highest gain
    try:
        lens_none = LensSystem("none")
        lens_cas = LensSystem("cassegrain")
        gain_none = lens_none.get_link_budget_gain_dB(6000.0)
        gain_cas = lens_cas.get_link_budget_gain_dB(6000.0)
        pass_t13 = gain_cas > gain_none
        test_results.append({"name": "T13 – Cassegrain en yüksek kazanç", "pass": pass_t13,
                             "error": f"Cassegrain={gain_cas:.1f}dB > None={gain_none:.1f}dB"})
    except Exception as e:
        test_results.append({"name": "T13 – Cassegrain en yüksek kazanç", "pass": False, "error": str(e)})

    # Test 14: EnvironmentPresets – all presets load correctly
    try:
        all_presets_ok = True
        required_keys = ["thermalProfile", "weather", "Cn2Level", "baseTemp", "deltaT"]
        for pk in EnvironmentPresets.get_preset_keys():
            preset = EnvironmentPresets.get_preset(pk)
            if not all(k in preset for k in required_keys):
                all_presets_ok = False
        test_results.append({"name": "T14 – Ortam presetleri geçerli", "pass": all_presets_ok,
                             "error": f"{len(EnvironmentPresets.get_preset_keys())} preset yüklendi"})
    except Exception as e:
        test_results.append({"name": "T14 – Ortam presetleri geçerli", "pass": False, "error": str(e)})

    # Test 15: ComprehensiveTransmissionTest – successRate in [0,100]
    try:
        results_matrix = ComprehensiveTransmissionTest.run_all({
            "gridX": 10, "gridY": 4, "gridZ": 4, "cellSize": 600.0,
            "numBits": 8, "pattern": "alternating"
        })
        rates_valid = all(0.0 <= r["successRate"] <= 100.0 for r in results_matrix)
        total_combos = len(results_matrix)
        pass_t15 = rates_valid and total_combos == len(EnvironmentPresets.get_preset_keys()) * len(LensSystem.LENS_TYPES)
        best = max(results_matrix, key=lambda x: x["successRate"]) if results_matrix else None
        worst = min(results_matrix, key=lambda x: x["successRate"]) if results_matrix else None
        detail = f"{total_combos} kombinasyon, En iyi: {best['successRate']}% ({best['environment']}/{best['lens']}), En kötü: {worst['successRate']}%" if best else ""
        test_results.append({"name": "T15 – Kapsamlı veri aktarım testi", "pass": pass_t15, "error": detail})
    except Exception as e:
        test_results.append({"name": "T15 – Kapsamlı veri aktarım testi", "pass": False, "error": str(e)})

    all_passed = all(tr["pass"] for tr in test_results)
    return coerce_numpy({
        "allPassed": all_passed,
        "results": test_results
    })


@app.post("/api/test-transmission")
def test_transmission(params: TransmissionTestParams):
    """
    Run comprehensive transmission test across all environment × lens combinations.
    Returns % success rate for each combination.
    """
    try:
        results = ComprehensiveTransmissionTest.run_all({
            "gridX": params.gridX,
            "gridY": params.gridY,
            "gridZ": params.gridZ,
            "cellSize": params.cellSize,
            "wavelengthNm": params.wavelengthNm,
            "numBits": params.numBits,
            "pattern": params.pattern,
            "laserPower_mW": params.laserPower_mW,
            "receiverDiamM": params.receiverDiamM,
            "beamDivRad": params.beamDivRad,
            "receiverSensitivity_dBm": params.receiverSensitivity_dBm
        })

        # Summary statistics
        rates = [r["successRate"] for r in results]
        best = max(results, key=lambda x: x["successRate"]) if results else None
        worst = min(results, key=lambda x: x["successRate"]) if results else None
        avg_rate = sum(rates) / len(rates) if rates else 0.0

        # Group by environment for easy display
        by_env = {}
        for r in results:
            env = r["environment"]
            if env not in by_env:
                by_env[env] = []
            by_env[env].append(r)

        return coerce_numpy({
            "results": results,
            "summary": {
                "totalCombinations": len(results),
                "avgSuccessRate": round(avg_rate, 1),
                "bestCombination": best,
                "worstCombination": worst,
                "numEnvironments": len(EnvironmentPresets.get_preset_keys()),
                "numLensTypes": len(LensSystem.LENS_TYPES)
            },
            "byEnvironment": by_env
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/presets")
def get_presets():
    """Return all available environment presets and lens types."""
    return {
        "environments": EnvironmentPresets.PRESETS,
        "lensTypes": LensSystem.LENS_TYPES
    }

# ── COMSOL Export Endpoints ─────────────────────────────────
@app.post("/api/export/csv-grid")
def export_csv_grid(p: SimParams):
    try:
        atmosphere, _, _, _, _, _ = run_simulation_backend(p)
        bridge = COMSOLBridge()
        csv_content = bridge.exportGridCSV(atmosphere)
        return {"filename": "atmosphere_grid.csv", "content": csv_content, "mimeType": "text/csv"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/export/csv-raypath")
def export_csv_raypath(p: SimParams):
    try:
        _, trace, _, _, _, _ = run_simulation_backend(p)
        bridge = COMSOLBridge()
        csv_content = bridge.exportRayPathCSV(trace)
        return {"filename": "ray_path.csv", "content": csv_content, "mimeType": "text/csv"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/export/comsol-java")
def export_comsol_java(p: SimParams):
    try:
        atmosphere, _, _, sourcePos, targetPos, _ = run_simulation_backend(p)
        bridge = COMSOLBridge()
        wavelengthUm = p.wavelengthNm / 1000.0
        laserPower_W = p.laserPower_mW / 1000.0
        script = bridge.generateModelScript(atmosphere, sourcePos, targetPos, {
            "wavelength": wavelengthUm * 1e-6,
            "laserPower": laserPower_W,
            "baseTemp": p.baseTemp,
            "deltaT": p.deltaT
        })
        return {"filename": "FSO_AtmosphericChannel.java", "content": script, "mimeType": "text/x-java-source"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/export/comsol-matlab")
def export_comsol_matlab(p: SimParams):
    try:
        atmosphere, _, _, sourcePos, targetPos, _ = run_simulation_backend(p)
        bridge = COMSOLBridge()
        wavelengthUm = p.wavelengthNm / 1000.0
        script = bridge.generateMatlabScript(atmosphere, sourcePos, targetPos, {
            "wavelength": wavelengthUm * 1e-6,
            "baseTemp": p.baseTemp
        })
        return {"filename": "FSO_COMSOL_LiveLink.m", "content": script, "mimeType": "text/x-matlab"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/export/comsol-params")
def export_comsol_params(p: SimParams):
    try:
        atmosphere, _, _, sourcePos, targetPos, _ = run_simulation_backend(p)
        bridge = COMSOLBridge()
        wavelengthUm = p.wavelengthNm / 1000.0
        laserPower_W = p.laserPower_mW / 1000.0
        params_txt = bridge.generateParametersFile(atmosphere, sourcePos, targetPos, {
            "wavelength": wavelengthUm * 1e-6,
            "laserPower": laserPower_W,
            "baseTemp": p.baseTemp,
            "deltaT": p.deltaT
        })
        return {"filename": "comsol_parameters.txt", "content": params_txt, "mimeType": "text/plain"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
