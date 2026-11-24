from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import logging
import math
import os
from typing import Any, Dict, List, Literal, Optional

import numpy as np
from pydantic import BaseModel, Field, model_validator

from backend.engine import (Vec3, AtmosphereGrid, RayTracer, DataTransmissionSimulator,
                             AtmosphericLoss, vectorialSnellLaw, verifySnellLaw,
                             LensSystem, EnvironmentPresets, ComprehensiveTransmissionTest)
from backend.comsol import COMSOLBridge
from backend import bitcheck, numerics, optics, materials
from backend.lens_stack import _surface_hit, _refract

app = FastAPI(title="FSO Physics Engine Backend")
logger = logging.getLogger(__name__)
MAX_GRID_CELLS = 1_000_000
MAX_ATMOSPHERE_HEIGHT_M = 20_000.0


def validate_grid_limits(grid_x: int, grid_y: int, grid_z: int, cell_size: float) -> None:
    if grid_x * grid_y * grid_z > MAX_GRID_CELLS:
        raise ValueError(f"grid exceeds the {MAX_GRID_CELLS} cell limit")
    if grid_z * cell_size > MAX_ATMOSPHERE_HEIGHT_M:
        raise ValueError(f"grid height exceeds {MAX_ATMOSPHERE_HEIGHT_M:g} m")


def internal_server_error(error: Exception) -> HTTPException:
    logger.error("Unhandled simulation request failure", exc_info=(type(error), error, error.__traceback__))
    return HTTPException(status_code=500, detail="Internal server error")

# ── Frontend statik dosyaları sun ───────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CORS_ORIGINS = [origin.strip() for origin in os.getenv("FSO_CORS_ORIGINS", "").split(",") if origin.strip()]
if _CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

# ── Pydantic Request Models ──────────────────────────────────
class SimParams(BaseModel):
    gridX: int = Field(60, ge=1, le=600)
    gridY: int = Field(10, ge=1, le=100)
    gridZ: int = Field(10, ge=1, le=100)
    cellSize: float = Field(100.0, gt=0.0, le=5000.0)
    thermalProfile: Literal["uniform", "gradient", "turbulent", "hotspot", "layered", "coastal"] = "gradient"
    baseTemp: float = Field(25.0, ge=-80.0, le=80.0)
    deltaT: float = Field(15.0, ge=0.0, le=100.0)
    humidity: float = Field(50.0, ge=0.0, le=100.0)
    weather: Literal["clear", "haze", "fog", "rain", "snow", "storm"] = "clear"
    Cn2Level: Literal["weak", "moderate", "strong"] = "moderate"
    windSpeed: float = Field(5.0, ge=0.0, le=100.0)
    wavelengthNm: int = Field(1550, ge=200, le=10000)
    laserPower_mW: float = Field(10.0, gt=0.0, le=100000.0)
    receiverDiamM: float = Field(0.10, gt=0.0, le=100.0)
    receiverSensitivity_dBm: float = Field(-40.0, ge=-200.0, le=100.0)
    pointingErrorRad: float = Field(0.0, ge=0.0, le=0.1)
    beamDivRad: float = Field(0.001, ge=0.0, le=1.0)
    # Lens system
    lensType: Literal["none", "collimating", "focusing", "galilean_telescope", "cassegrain", "diverging"] = "none"
    lensFocalLength_m: Optional[float] = Field(None, ge=-100.0, le=100.0)
    lensAperture_m: Optional[float] = Field(None, gt=0.0, le=10.0)
    # Environment preset (if set, overrides thermal/weather/Cn2 params)
    environmentPreset: Optional[Literal["urban", "maritime", "desert", "mountain", "industrial", "arctic", "storm"]] = None

    @model_validator(mode="after")
    def validate_grid(self):
        validate_grid_limits(self.gridX, self.gridY, self.gridZ, self.cellSize)
        return self

class SendDataParams(BaseModel):
    simParams: SimParams
    pattern: Literal["alternating", "all_ones", "all_zeros", "random", "burst", "prbs7", "ascii_hello"] = "random"
    numBits: int = Field(32, ge=1, le=10000)
    # end-to-end check + selective retransmission (arq.run_end_to_end)
    useArq: bool = True
    segmentBytes: int = Field(4, ge=1, le=255)
    maxRetries: int = Field(20, ge=0, le=200)
    seed: int = Field(0, ge=0, le=2**31 - 1)

class TransmissionTestParams(BaseModel):
    gridX: int = Field(30, ge=1, le=500)
    gridY: int = Field(6, ge=1, le=100)
    gridZ: int = Field(6, ge=1, le=100)
    cellSize: float = Field(200.0, gt=0.0, le=5000.0)
    wavelengthNm: float = Field(1550.0, ge=200.0, le=10000.0)
    numBits: int = Field(32, ge=1, le=10000)
    pattern: Literal["alternating", "all_ones", "all_zeros", "random", "burst", "prbs7", "ascii_hello"] = "alternating"
    laserPower_mW: float = Field(10.0, gt=0.0, le=100000.0)
    receiverDiamM: float = Field(0.10, gt=0.0, le=100.0)
    beamDivRad: float = Field(0.001, ge=0.0, le=1.0)
    receiverSensitivity_dBm: float = Field(-40.0, ge=-200.0, le=100.0)

    @model_validator(mode="after")
    def validate_grid(self):
        validate_grid_limits(self.gridX, self.gridY, self.gridZ, self.cellSize)
        total_cells = self.gridX * self.gridY * self.gridZ * len(EnvironmentPresets.PRESETS)
        if total_cells > MAX_GRID_CELLS:
            raise ValueError(f"transmission test grids exceed the {MAX_GRID_CELLS} combined cell limit")
        return self


# ── Serialization Helpers ─────────────────────────────────────
def coerce_numpy(obj):
    t = type(obj)
    if obj is None or t in (int, float, bool, str):
        return obj
    if t is dict:
        return {k: coerce_numpy(v) for k, v in obj.items()}
    if t is list:
        return [coerce_numpy(x) for x in obj]
    if t is tuple:
        return tuple(coerce_numpy(x) for x in obj)
    if isinstance(obj, (np.floating, float)):
        return float(obj)
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.integer, int)):
        return int(obj)
    if isinstance(obj, dict):
        return {k: coerce_numpy(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [coerce_numpy(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(coerce_numpy(x) for x in obj)
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
        # incidence/refraction cosines (the verification table needs them; they were
        # previously dropped here, so the UI computed acos(undefined) = NaN)
        for ck in ("cosTheta_i", "cosTheta_t"):
            if ck in e:
                evt_dict[ck] = e[ck]
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
def build_atmosphere(p: SimParams):
    """Build the AtmosphereGrid for SimParams (honouring environmentPreset).
    Returns (atmosphere, weather); shared by /api/simulate and /api/propagation3d."""
    wavelengthUm = p.wavelengthNm / 1000.0

    # Apply environment preset if set (overrides individual params)
    thermal_profile = p.thermalProfile
    weather = p.weather
    cn2_level = p.Cn2Level
    base_temp = p.baseTemp
    delta_t = p.deltaT
    wind_speed = p.windSpeed
    humidity = p.humidity

    if p.environmentPreset:
        preset = EnvironmentPresets.get_preset(p.environmentPreset)
        thermal_profile = preset["thermalProfile"]
        weather = preset["weather"]
        cn2_level = preset["Cn2Level"]
        base_temp = preset["baseTemp"]
        delta_t = preset["deltaT"]
        wind_speed = preset["windSpeed"]
        humidity = preset["humidity"]

    atmosphere = AtmosphereGrid(p.gridX, p.gridY, p.gridZ, p.cellSize)
    atmosphere.applyThermalProfile(thermal_profile, {
        "baseTemp": base_temp,
        "deltaT": delta_t,
        "humidity": humidity,
        "windSpeed": wind_speed,
        "Cn2Level": cn2_level,
        "wavelengthUm": wavelengthUm,
        "useEdlen": True
    })
    return atmosphere, weather


def run_simulation_backend(p: SimParams):
    laserPower_W = p.laserPower_mW / 1000.0
    atmosphere, weather = build_atmosphere(p)

    rayTracer = RayTracer(atmosphere)

    sourcePos = Vec3(0.0, p.gridY * p.cellSize * 0.5, p.gridZ * p.cellSize * 0.5)
    targetPos = Vec3(p.gridX * p.cellSize, p.gridY * p.cellSize * 0.5, p.gridZ * p.cellSize * 0.5)

    # Apply lens system
    lens = LensSystem(p.lensType, p.lensFocalLength_m, p.lensAperture_m)
    lensed_beam = lens.apply_to_beam({
        "divergenceRad": p.beamDivRad,
        "beamDiamM": 0.004,
        "receiverDiamM": p.receiverDiamM,
        "wavelengthNm": p.wavelengthNm,
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
        "beamDiamM": lensed_beam["beamDiamM"],
        "Cn2": trace["avgCn2"] if trace["avgCn2"] > 0 else 1e-15,
        "pointingErrorRad": p.pointingErrorRad,
        "receiverSensitivity_dBm": p.receiverSensitivity_dBm
    })
    # Apply lens transmittance power penalty
    link_budget["P_rx_dBm"] += 10.0 * math.log10(max(1e-300, lensed_beam["transmittance"]))
    link_budget["P_rx_W"] = math.pow(10.0, link_budget["P_rx_dBm"] / 10.0) / 1000.0
    link_budget["linkMargin_dB"] = link_budget["P_rx_dBm"] - p.receiverSensitivity_dBm
    link_budget["linkViable"] = link_budget["linkMargin_dB"] > 0
    link_budget["lensType"] = p.lensType
    link_budget["lensGain_dB"] = round(lens.get_link_budget_gain_dB(distance, p.beamDivRad, p.receiverDiamM, 0.004), 2)

    return atmosphere, trace, link_budget, sourcePos, targetPos, recvR

# ── Endpoints ────────────────────────────────────────────────
@app.get("/")
def serve_index():
    return FileResponse(os.path.join(_ROOT, "index.html"))

@app.get("/style.css")
def serve_css():
    return FileResponse(os.path.join(_ROOT, "style.css"), media_type="text/css")

@app.get("/engine.js")
def serve_engine_js():
    return FileResponse(os.path.join(_ROOT, "engine.js"), media_type="application/javascript")

@app.get("/comsol.js")
def serve_comsol_js():
    return FileResponse(os.path.join(_ROOT, "comsol.js"), media_type="application/javascript")

@app.get("/renderer.js")
def serve_renderer_js():
    return FileResponse(os.path.join(_ROOT, "renderer.js"), media_type="application/javascript")

@app.get("/app.js")
def serve_app_js():
    return FileResponse(os.path.join(_ROOT, "app.js"), media_type="application/javascript")

@app.get("/viz3d.js")
def serve_viz3d_js():
    return FileResponse(os.path.join(_ROOT, "viz3d.js"), media_type="application/javascript")

@app.get("/duplex.js")
def serve_duplex_js():
    return FileResponse(os.path.join(_ROOT, "duplex.js"), media_type="application/javascript")

@app.get("/lens-designer.js")
def serve_lens_designer_js():
    return FileResponse(os.path.join(_ROOT, "lens-designer.js"), media_type="application/javascript")

@app.get("/ui-v2.css")
def serve_ui_v2_css():
    return FileResponse(os.path.join(_ROOT, "ui-v2.css"), media_type="text/css")

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
    except Exception as error:
        raise internal_server_error(error) from error

def _send_data_end_to_end(params, p, trace, link_budget, sourcePos, targetPos, recvR, data,
                          serialized_results, res):
    """Whole message crosses the full grid distance first; only then the receiver checks CRCs and
    asks for the failed segments again (see arq.run_end_to_end).  The legacy per-bit fields in `res`
    are rewritten to describe the FIRST PASS (what arrived before any retransmission)."""
    distance = sourcePos.sub(targetPos).length()
    lat = trace.get("exitPosition")
    lateral = None if not lat else math.hypot(lat.y - targetPos.y, lat.z - targetPos.z)
    blocked = (not trace.get("success", False)) or lateral is None or lateral > recvR
    # noise floor such that BER = 1e-9 exactly at the receiver sensitivity (P/noise = 12 -> SNR 144)
    noise_dBm = p.receiverSensitivity_dBm - 10.0 * math.log10(12.0)
    sigma_I2 = propagation3d.scintillation_index_from_rytov(float(link_budget["scintillation"]["sigma2_R"]))
    channel = arq_mod.ArqChannel(p_rx_dBm=float(link_budget["P_rx_dBm"]), noise_rms_dBm=noise_dBm,
                                 sigma_I2=sigma_I2, fading_rho=0.0,
                                 ber_override=0.5 if blocked else None)
    e2e = arq_mod.run_end_to_end(data, params.segmentBytes, params.maxRetries, channel, 0.0, params.seed)
    first = e2e["first_pass_bits"]
    for i, r in enumerate(serialized_results):
        r["received"] = first[i]
        r["correct"] = first[i] == data[i]
    correct = sum(1 for a, b in zip(data, first) if a == b)
    n = len(data)
    res.update({
        "correctBits": correct, "bitErrorRate": 1.0 - correct / n, "successRate": 100.0 * correct / n,
        "totalOnes": sum(data), "totalZeros": n - sum(data),
        "missedOnes": sum(1 for a, b in zip(data, first) if a == 1 and b != 1),
        "missedZeros": sum(1 for a, b in zip(data, first) if a == 0 and b != 0),
    })
    a = e2e["arq"]
    return {
        "distance_used_m": distance, "grid_distance_m": p.gridX * p.cellSize,
        "link": {"p_rx_dBm": channel.p_rx_dBm, "noise_rms_dBm": noise_dBm, "sigma_I2": sigma_I2,
                 "mean_ber": channel.mean_ber(), "effective_ber": channel.faded_mean_ber(),
                 "beam_blocked": bool(blocked), "lateral_deviation_m": lateral,
                 "one_way_delay_s": distance / 299792458.0},
        "sent_bits": list(data), "first_pass_bits": first, "reassembled_bits": e2e["reassembled_bits"],
        "first_pass_bit_errors": e2e["first_pass_bit_errors"],
        "segment_bytes_used": e2e["segment_bytes_used"], "pad_bits": e2e["pad_bits"],
        "n_segments": e2e["n_segments"], "n_rounds": e2e["n_rounds"],
        "rounds": e2e["rounds"], "final_check": e2e["final_check"],
        "arq": {k: a[k] for k in ("segments", "retries_per_region", "retries_histogram", "total_transmissions",
                                   "retransmissions", "efficiency", "goodput_ratio", "spurious_retransmissions",
                                   "histories_truncated", "budget_exhausted", "rounds_truncated",
                                   "data_wire_bits", "feedback_wire_bits", "max_retries", "segment_bytes",
                                   "residual_errors", "n_segments")},
        "totalRetransmissions": a["retransmissions"], "efficiency": a["efficiency"],
        "retransmissionRounds": max(0, e2e["n_rounds"] - 1),
    }


@app.post("/api/send-data")
def send_data(params: SendDataParams):
    try:
        p = params.simParams
        _, trace, link_budget, sourcePos, targetPos, recvR = run_simulation_backend(p)
        simulator = DataTransmissionSimulator(
            None, sourcePos, targetPos, recvR,
            trace=trace,
            linkBudget=link_budget
        )
        data = DataTransmissionSimulator.generateTestData(params.pattern, params.numBits)

        results = simulator.sendPacket(data)

        # Serialize shared ray trace once and attach only to the last entry to prevent O(N) payload bloat
        raw_results = results["results"]
        last_idx = len(raw_results) - 1
        serialized_trace = serialize_trace(trace) if trace else None
        compact_lb = len(raw_results) > 512
        serialized_results = [
            {
                "sent": r["sent"],
                "received": r["received"],
                "correct": r["correct"],
                "lateralDeviation": r["lateralDeviation"],
                "signalPower": r["signalPower"],
                "linkBudget": None if (compact_lb and i < last_idx) else r["linkBudget"],
                "trace": serialized_trace if (i == last_idx and r.get("trace")) else None,
            }
            for i, r in enumerate(raw_results)
        ]

        res = {
            "data": results["data"],
            "results": serialized_results,
            "totalBits": results["totalBits"],
            "correctBits": results["correctBits"],
            "bitErrorRate": results["bitErrorRate"],
            "totalOnes": results["totalOnes"],
            "totalZeros": results["totalZeros"],
            "missedOnes": results["missedOnes"],
            "missedZeros": results["missedZeros"],
            "successRate": results["successRate"]
        }
        if params.useArq:
            res.update(_send_data_end_to_end(params, p, trace, link_budget, sourcePos, targetPos, recvR,
                                             data, serialized_results, res))
        else:
            res["arq"] = None
        return coerce_numpy(res)
    except Exception as error:
        raise internal_server_error(error) from error

@app.post("/api/test")
def run_tests():
    # Lightweight API smoke checks; pytest remains the authoritative test suite.
    test_results = []
    
    # Test 1: Normal incidence
    try:
        i = Vec3(0, 0, -1)
        n = Vec3(0, 0, 1)
        result = vectorialSnellLaw(i, n, 1.0, 1.5)
        pass_t1 = result is not None and abs(result["direction"].z - (-1.0)) < 1e-6
        test_results.append({"name": "T1 – Normal geliş (θi=0°)", "pass": pass_t1, "error": ""})
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T1 – Normal geliş (θi=0°)", "pass": False, "error": "test hatası"})

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
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T2 – Hava→Cam (30°)", "pass": False, "error": "test hatası"})

    # Test 3: TIR
    try:
        critAngle = math.asin(1.0 / 1.5)
        theta = critAngle + 5.0 * math.pi / 180.0
        i = Vec3(math.sin(theta), 0, -math.cos(theta))
        result = vectorialSnellLaw(i, Vec3(0, 0, 1), 1.5, 1.0)
        pass_t3 = result is None
        test_results.append({"name": "T3 – TIR (θ>41.8°)", "pass": pass_t3, "error": ""})
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T3 – TIR (θ>41.8°)", "pass": False, "error": "test hatası"})

    # Test 4: Atmosphere boundary (6 km realistic delta n)
    try:
        n1, n2 = 1.000293, 1.000270
        theta_i = 5.0 * math.pi / 180.0
        i = Vec3(math.cos(theta_i), math.sin(theta_i), 0)
        result = vectorialSnellLaw(i, Vec3(-1, 0, 0), n1, n2)
        verify = verifySnellLaw(n1, n2, result["cosTheta_i"], result["cosTheta_t"])
        test_results.append({"name": "T4 – Atmosfer sınırı (Δn=-2.3e-5)", "pass": verify["valid"], "error": f"hata={verify['error']:.3e}"})
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T4 – Atmosfer sınırı (Δn=-2.3e-5)", "pass": False, "error": "test hatası"})

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
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T5 – Tersinirlik", "pass": False, "error": "test hatası"})

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
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T6 – Teğetsel korunum", "pass": False, "error": "test hatası"})

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
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T7 – Açı tarama (0°-85°)", "pass": False, "error": "test hatası"})

    # Test 8: Beer-Lambert absorption (6 km clear)
    try:
        loss = AtmosphericLoss.absorptionLoss(6000.0, "clear", 1550.0)
        expectedLoss_dB = 0.2 * 6.0
        pass_t8 = abs(loss["loss_dB"] - expectedLoss_dB) < 0.5
        test_results.append({"name": "T8 – Beer-Lambert 6km clear", "pass": pass_t8, "error": f"{loss['loss_dB']:.2f} dB (beklenen ~{expectedLoss_dB:.1f} dB)"})
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T8 – Beer-Lambert 6km clear", "pass": False, "error": "test hatası"})

    # Test 9: Geometric loss at 6 km
    try:
        geo = AtmosphericLoss.geometricLoss(6000.0, 0.001, 0.1, 0.004)
        expectedBeamDiam = 0.004 + 2.0 * 0.001 * 6000.0
        pass_t9 = abs(geo["beamDiamAtReceiver"] - expectedBeamDiam) < 0.01
        test_results.append({"name": "T9 – Geometrik yayılma 6km", "pass": pass_t9, "error": f"Ø={geo['beamDiamAtReceiver']:.2f}m (beklenen ~{expectedBeamDiam:.2f}m)"})
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T9 – Geometrik yayılma 6km", "pass": False, "error": "test hatası"})

    # Test 10: Edlén equation verification
    try:
        atm = AtmosphereGrid(2, 2, 2, 1.0)
        n_15C = atm.refractiveIndexSimple(15.0, 101325.0)
        n_35C = atm.refractiveIndexSimple(35.0, 101325.0)
        pass_t10 = n_15C > n_35C and abs(n_15C - 1.000293) < 1e-5
        test_results.append({"name": "T10 – Edlén", "pass": pass_t10, "error": f"n(15°C)={n_15C:.8f}, n(35°C)={n_35C:.8f}"})
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T10 – Edlén", "pass": False, "error": "test hatası"})

    # Test 11: Scintillation index
    try:
        scint = AtmosphericLoss.scintillationIndex(6000.0, 1e-15, 1550e-9)
        pass_t11 = scint["sigma2_R"] > 0 and not math.isnan(scint["sigma2_R"])
        test_results.append({"name": "T11 – Rytov scintilasyon", "pass": pass_t11, "error": f"σ²ᵣ={scint['sigma2_R']:.3e} ({scint['regime']})"})
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T11 – Rytov scintilasyon", "pass": False, "error": "test hatası"})

    # Test 12: LensSystem – Collimating lens reduces divergence
    try:
        lens = LensSystem("collimating")
        result_beam = lens.apply_to_beam({"divergenceRad": 1e-3, "beamDiamM": 0.004, "receiverDiamM": 0.1})
        pass_t12 = result_beam["divergenceRad"] < 1e-3 and result_beam["receiverDiamM"] > 0.1
        test_results.append({"name": "T12 – Kollimating lens diverjans azaltma", "pass": pass_t12,
                             "error": f"div={result_beam['divergenceRad']:.2e} rad, recvR={result_beam['receiverDiamM']:.4f}m"})
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T12 – Kollimating lens diverjans azaltma", "pass": False, "error": "test hatası"})

    # Test 13: LensSystem – Cassegrain gives highest gain
    try:
        lens_none = LensSystem("none")
        lens_cas = LensSystem("cassegrain")
        gain_none = lens_none.get_link_budget_gain_dB(6000.0)
        gain_cas = lens_cas.get_link_budget_gain_dB(6000.0)
        pass_t13 = gain_cas > gain_none
        test_results.append({"name": "T13 – Cassegrain en yüksek kazanç", "pass": pass_t13,
                             "error": f"Cassegrain={gain_cas:.1f}dB > None={gain_none:.1f}dB"})
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T13 – Cassegrain en yüksek kazanç", "pass": False, "error": "test hatası"})

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
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T14 – Ortam presetleri geçerli", "pass": False, "error": "test hatası"})

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
    except Exception:
        logger.exception("Built-in smoke check failed")
        test_results.append({"name": "T15 – Kapsamlı veri aktarım testi", "pass": False, "error": "test hatası"})

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
    except Exception as error:
        raise internal_server_error(error) from error


@app.get("/api/presets")
def get_presets():
    """Return all available environment presets and lens types."""
    return {
        "environments": EnvironmentPresets.PRESETS,
        "lensTypes": LensSystem.LENS_TYPES
    }

# ── Bit check / optics / numerics endpoints ─────────────────

LensPresetName = Literal["none", "collimating", "focusing", "keplerian_telescope",
                         "galilean_telescope", "cassegrain", "diverging"]


class BitCheckParams(BaseModel):
    payload: str = Field("Merhaba FSO", min_length=0, max_length=512)
    distanceM: float = Field(1000.0, ge=0.0, le=1e6)
    lensPreset: LensPresetName = "none"
    seed: int = Field(0, ge=0, le=2**31 - 1)
    txPowerDbm: float = Field(10.0, ge=-60.0, le=60.0)
    noiseRmsDbm: float = Field(-30.0, ge=-120.0, le=0.0)
    sensitivityDbm: Optional[float] = Field(None, ge=-150.0, le=60.0)
    includeBits: bool = True

    @model_validator(mode="after")
    def _payload_size(self):
        if len(self.payload.encode("utf-8")) > 512:
            raise ValueError("payload must be at most 512 bytes")
        return self


class LinkGainParams(BaseModel):
    lensPreset: LensPresetName = "keplerian_telescope"
    distancesM: List[float] = Field(default_factory=lambda: [100.0, 1000.0, 5000.0],
                                    min_length=1, max_length=200)
    wavelengthM: float = Field(1550e-9, ge=200e-9, le=20e-6)
    rxRadiusM: float = Field(0.025, gt=0.0, le=10.0)

    @model_validator(mode="after")
    def _distances(self):
        if any((not math.isfinite(d)) or d < 0 or d > 1e6 for d in self.distancesM):
            raise ValueError("every distance must be within [0, 1e6] m")
        return self


CONVERGENCE_METHODS = ("trapezoid", "simpson", "gauss_legendre", "euler", "rk4")
CONVERGENCE_PROBLEMS = {
    # name: (integrand f, a, b, exact)   -- ODE methods use y' = -y, y(0)=1 on [0, 1]
    "sin": (math.sin, 0.0, math.pi, 2.0),
    "exp": (math.exp, 0.0, 1.0, math.e - 1.0),
    "gaussian": (lambda x: math.exp(-x * x), 0.0, 1.0, 0.7468241328124271),
}


class ConvergenceParams(BaseModel):
    method: Literal["trapezoid", "simpson", "gauss_legendre", "euler", "rk4"] = "simpson"
    problem: Literal["sin", "exp", "gaussian"] = "sin"
    ns: List[int] = Field(default_factory=lambda: [2, 4, 8, 16, 32], min_length=2, max_length=20)

    @model_validator(mode="after")
    def _ns(self):
        if any(n < 1 or n > 4096 for n in self.ns):
            raise ValueError("each n must be within [1, 4096]")
        if self.method == "simpson" and any(n % 2 for n in self.ns):
            raise ValueError("simpson requires even n")
        if self.method == "gauss_legendre" and any(n > 64 for n in self.ns):
            raise ValueError("gauss_legendre n must be <= 64")
        if len(set(self.ns)) != len(self.ns):
            raise ValueError("ns must be distinct")
        return self


@app.post("/api/bitcheck")
def bitcheck_endpoint(p: BitCheckParams):
    """Frame a text payload, send it over the modelled link, compare bits + CRC."""
    try:
        rep = bitcheck.run_link_check(
            p.payload, p.distanceM, p.lensPreset, seed=p.seed,
            tx_power_dBm=p.txPowerDbm, noise_rms_dBm=p.noiseRmsDbm,
            sensitivity_dBm=p.sensitivityDbm)
        if not p.includeBits:
            rep.pop("sent_bits", None)
            rep.pop("received_bits", None)
        return coerce_numpy(rep)
    except Exception as error:
        raise internal_server_error(error) from error


@app.get("/api/optics/presets")
def optics_presets():
    try:
        out = []
        for key in optics.FSO_LENS_PRESETS:
            pr = optics.build_preset(key)
            mag = pr.magnification
            out.append({
                "key": key, "name": pr.name, "description": pr.description, "role": pr.role,
                "defaultW0M": pr.default_w0, "sourceOffsetM": pr.source_offset_m,
                "magnification": None if (mag is None or not math.isfinite(mag)) else mag,
                "params": pr.params,
            })
        return {"presets": out}
    except Exception as error:
        raise internal_server_error(error) from error


@app.post("/api/optics/link-gain")
def optics_link_gain(p: LinkGainParams):
    try:
        rows = []
        for d in p.distancesM:
            b = optics.lens_link_budget(p.lensPreset, d, wavelength=p.wavelengthM,
                                        rx_radius=p.rxRadiusM)
            rows.append({"distanceM": d, **b})
        return coerce_numpy({"preset": p.lensPreset, "rows": rows})
    except Exception as error:
        raise internal_server_error(error) from error


@app.post("/api/numerics/convergence")
def numerics_convergence(p: ConvergenceParams):
    try:
        f, a, b, exact = CONVERGENCE_PROBLEMS[p.problem]
        if p.method in ("euler", "rk4"):
            solver = numerics.euler if p.method == "euler" else numerics.rk4
            exact = math.exp(-1.0)
            rows = []
            for n in p.ns:
                _, y = solver(lambda t, y: -y, 1.0, 0.0, 1.0, n)
                v = float(y[-1][0])
                rows.append({"n": n, "h": 1.0 / n, "value": v, "abs_error": abs(v - exact)})
            problem = "y' = -y, y(0)=1 on [0,1]"
        else:
            method = getattr(numerics, p.method)
            rows = numerics.convergence_study(method, f, exact, p.ns, a, b)
            problem = p.problem
        usable = [r for r in rows if r["abs_error"] > 1e-14]
        order = None
        if len(usable) >= 2:
            order = numerics.convergence_order([r["abs_error"] for r in usable],
                                               [r["h"] for r in usable])
        return coerce_numpy({"method": p.method, "problem": problem, "exact": exact,
                             "rows": rows, "order": order})
    except Exception as error:
        raise internal_server_error(error) from error


# ── COMSOL Export Endpoints ─────────────────────────────────
@app.post("/api/export/csv-grid")
def export_csv_grid(p: SimParams):
    try:
        atmosphere, _, _, _, _, _ = run_simulation_backend(p)
        bridge = COMSOLBridge()
        csv_content = bridge.exportGridCSV(atmosphere)
        return {"filename": "atmosphere_grid.csv", "content": csv_content, "mimeType": "text/csv"}
    except Exception as error:
        raise internal_server_error(error) from error

@app.post("/api/export/csv-raypath")
def export_csv_raypath(p: SimParams):
    try:
        _, trace, _, _, _, _ = run_simulation_backend(p)
        bridge = COMSOLBridge()
        csv_content = bridge.exportRayPathCSV(trace)
        return {"filename": "ray_path.csv", "content": csv_content, "mimeType": "text/csv"}
    except Exception as error:
        raise internal_server_error(error) from error

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
            "deltaT": p.deltaT,
            "beamDivRad": p.beamDivRad,
            "Cn2": float(atmosphere.Cn2.mean()) if len(atmosphere.Cn2) else 1e-15,
        })
        return {"filename": "FSO_AtmosphericChannel.java", "content": script, "mimeType": "text/x-java-source"}
    except Exception as error:
        raise internal_server_error(error) from error

@app.post("/api/export/comsol-matlab")
def export_comsol_matlab(p: SimParams):
    try:
        atmosphere, _, _, sourcePos, targetPos, _ = run_simulation_backend(p)
        bridge = COMSOLBridge()
        wavelengthUm = p.wavelengthNm / 1000.0
        script = bridge.generateMatlabScript(atmosphere, sourcePos, targetPos, {
            "wavelength": wavelengthUm * 1e-6,
            "baseTemp": p.baseTemp,
            "beamDivRad": p.beamDivRad,
            "Cn2": float(atmosphere.Cn2.mean()) if len(atmosphere.Cn2) else 1e-15,
        })
        return {"filename": "FSO_COMSOL_LiveLink.m", "content": script, "mimeType": "text/x-matlab"}
    except Exception as error:
        raise internal_server_error(error) from error

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
            "deltaT": p.deltaT,
            "beamDivRad": p.beamDivRad,
            "Cn2": float(atmosphere.Cn2.mean()) if len(atmosphere.Cn2) else 1e-15,
        })
        return {"filename": "comsol_parameters.txt", "content": params_txt, "mimeType": "text/plain"}
    except Exception as error:
        raise internal_server_error(error) from error


# ══════════════════════════════════════════════════════════════
# v2 endpoints: 3D propagation, selective-repeat ARQ, full duplex
# (contract: reports/api_contract_v2.md)
# ══════════════════════════════════════════════════════════════
from backend import propagation3d, arq as arq_mod, duplex as duplex_mod  # noqa: E402

MAX_PROP_CELLS = 40_000
Vec3Param = Field(None, min_length=3, max_length=3)


class AnomalyParams(BaseModel):
    """Box-shaped local disturbance (metres, world coordinates)."""
    center: List[float] = Field(..., min_length=3, max_length=3)
    size: List[float] = Field(..., min_length=3, max_length=3)
    cn2_factor: float = Field(1.0, ge=0.0, le=1e6)
    extra_alpha_dB_km: float = Field(0.0, ge=0.0, le=1000.0)
    delta_T: float = Field(0.0, ge=-100.0, le=100.0)

    @model_validator(mode="after")
    def _finite(self):
        if any((not math.isfinite(v)) for v in self.center + self.size) or any(v <= 0 for v in self.size):
            raise ValueError("anomaly center must be finite and size positive")
        return self


def _grid_for_distance(atm: SimParams, distance_m: float) -> SimParams:
    """Rebuild the grid so gridX * cellSize == distance_m within MAX_PROP_CELLS.

    Transverse cells (gridY/gridZ) are kept (reduced only if needed so >= 20 x-cells fit); the target cell
    size is the sidebar cellSize, but gridX is clamped to [20, limit/(gridY*gridZ)] so the cell size adapts.
    """
    gy, gz = atm.gridY, atm.gridZ
    while 20 * gy * gz > MAX_PROP_CELLS:
        if gz >= gy and gz > 1:
            gz -= 1
        else:
            gy -= 1
    gx_max = max(1, min(600, MAX_PROP_CELLS // (gy * gz)))
    gx = int(min(max(round(distance_m / atm.cellSize), 20), gx_max))
    cell = distance_m / gx
    while gz * cell > MAX_ATMOSPHERE_HEIGHT_M and gz > 1:
        gz -= 1
    return atm.model_copy(update={"gridX": gx, "gridY": gy, "gridZ": gz, "cellSize": cell})


class Propagation3DParams(BaseModel):
    atmosphere: SimParams = Field(default_factory=SimParams)
    source: Optional[List[float]] = Vec3Param
    target: Optional[List[float]] = Vec3Param
    w0: Optional[float] = Field(None, gt=0.0, le=1.0)
    lensPreset: LensPresetName = "none"
    nSamples: int = Field(200, ge=20, le=400)
    nRays: int = Field(16, ge=3, le=24)
    seed: int = Field(0, ge=0, le=2**31 - 1)
    rxRadiusM: float = Field(0.05, gt=0.0, le=10.0)
    m2: float = Field(1.0, ge=1.0, le=10.0)
    maxVoxels: int = Field(2000, ge=1, le=4000)
    anomalies: List[AnomalyParams] = Field(default_factory=list, max_length=8)
    distanceM: Optional[float] = Field(None, ge=100.0, le=20000.0)

    @model_validator(mode="after")
    def _limits(self):
        if self.distanceM is not None:
            self.atmosphere = _grid_for_distance(self.atmosphere, self.distanceM)
        a = self.atmosphere
        if a.gridX * a.gridY * a.gridZ > MAX_PROP_CELLS:
            raise ValueError(f"grid exceeds the {MAX_PROP_CELLS} cell limit for 3D propagation")
        if self.lensPreset == "focusing":
            raise ValueError("lensPreset 'focusing' is a receive lens; use a transmit preset")
        bx, by, bz = a.gridX * a.cellSize, a.gridY * a.cellSize, a.gridZ * a.cellSize
        for name, pt in (("source", self.source), ("target", self.target)):
            if pt is not None:
                if (not all(math.isfinite(c) for c in pt) or not (0 <= pt[0] <= bx and 0 <= pt[1] <= by
                                                                  and 0 <= pt[2] <= bz)):
                    raise ValueError(f"{name} must lie inside the grid bounds")
        if self.source is not None and self.source == self.target:
            raise ValueError("source and target must differ")
        return self


def _default_endpoints(p: Propagation3DParams):
    a = p.atmosphere
    src = p.source or [0.0, a.gridY * a.cellSize * 0.5, a.gridZ * a.cellSize * 0.5]
    tgt = p.target or [a.gridX * a.cellSize, a.gridY * a.cellSize * 0.5, a.gridZ * a.cellSize * 0.5]
    return src, tgt


def run_propagation(p: Propagation3DParams, grid_weather=None, wavelength_nm=None,
                    reverse: bool = False) -> Dict[str, Any]:
    """Build the grid from SimParams (same builder as /api/simulate) and propagate."""
    atmosphere, weather = grid_weather or build_atmosphere(p.atmosphere)
    src, tgt = _default_endpoints(p)
    if reverse:
        src, tgt = tgt, src
    lam = (wavelength_nm or p.atmosphere.wavelengthNm) * 1e-9
    return propagation3d.propagate_beam_3d(
        atmosphere, src, tgt, w0=p.w0, wavelength=lam,
        lens_preset=None if p.lensPreset == "none" else p.lensPreset,
        n_samples=p.nSamples, n_rays=p.nRays, seed=p.seed, weather=weather,
        rx_radius=p.rxRadiusM, m2=p.m2, anomalies=[a.model_dump() for a in p.anomalies],
        max_voxels=p.maxVoxels)


def _value_error_422(error: ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


@app.post("/api/propagation3d")
def propagation3d_endpoint(p: Propagation3DParams):
    """3D beam propagation: centerline, 1/e^2 rings, degradation cloud, summary."""
    try:
        res = run_propagation(p)
        res["meta"] = {"wavelengthNm": p.atmosphere.wavelengthNm, "weather": p.atmosphere.weather,
                       "cells": p.atmosphere.gridX * p.atmosphere.gridY * p.atmosphere.gridZ,
                       "grid": {"gridX": p.atmosphere.gridX, "gridY": p.atmosphere.gridY,
                                "gridZ": p.atmosphere.gridZ, "cellSize": p.atmosphere.cellSize},
                       "source": _default_endpoints(p)[0], "target": _default_endpoints(p)[1]}
        return coerce_numpy(res)
    except ValueError as error:
        raise _value_error_422(error) from error
    except Exception as error:
        raise internal_server_error(error) from error


class ArqParams(BaseModel):
    payload: str = Field("Merhaba FSO! Seçici tekrar (selective repeat) ARQ testi.", min_length=1,
                         max_length=2048)
    segmentBytes: int = Field(8, ge=1, le=255)
    maxRetries: int = Field(5, ge=0, le=200)
    seed: int = Field(0, ge=0, le=2**31 - 1)
    ackBer: float = Field(0.0, ge=0.0, le=0.5)
    ber: Optional[float] = Field(None, ge=0.0, le=1.0)      # fixed forward BER (disables fading/link model)
    # link model (used when ber is None and propagation is None)
    distanceM: float = Field(1000.0, gt=0.0, le=1e6)
    lensPreset: LensPresetName = "none"
    txPowerDbm: float = Field(10.0, ge=-60.0, le=60.0)
    wavelengthNm: float = Field(1550.0, ge=200.0, le=20000.0)
    rxRadiusM: float = Field(0.025, gt=0.0, le=10.0)
    atmosDbPerKm: float = Field(0.2, ge=0.0, le=1000.0)
    fixedLossDb: float = Field(3.0, ge=0.0, le=100.0)
    extraLossDb: float = Field(0.0, ge=-50.0, le=200.0)
    noiseRmsDbm: float = Field(-30.0, ge=-120.0, le=0.0)
    cn2: float = Field(1e-15, ge=0.0, le=1e-10)
    fadingRho: float = Field(0.0, ge=0.0, lt=1.0)
    spatialBins: int = Field(10, ge=2, le=20)
    propagation: Optional[Propagation3DParams] = None

    @model_validator(mode="after")
    def _sizes(self):
        if len(self.payload.encode("utf-8")) > 2048:
            raise ValueError("payload must be at most 2048 bytes")
        n = math.ceil(len(self.payload.encode("utf-8")) / self.segmentBytes)
        if n > arq_mod.MAX_SEGMENTS:
            raise ValueError(f"{n} segments exceed the {arq_mod.MAX_SEGMENTS} segment limit")
        return self


@app.post("/api/arq")
def arq_endpoint(p: ArqParams):
    """CRC-based selective-repeat ARQ over a fading link (optionally fed by a 3D propagation)."""
    try:
        prop = None
        channel = None
        if p.ber is None:
            if p.propagation is not None:
                prop = run_propagation(p.propagation)
                channel = arq_mod.channel_from_propagation(
                    prop, p.txPowerDbm, p.fixedLossDb, p.noiseRmsDbm, p.fadingRho)
            else:
                channel = arq_mod.channel_from_link(
                    p.lensPreset, p.distanceM, p.txPowerDbm, p.wavelengthNm * 1e-9, p.rxRadiusM,
                    p.atmosDbPerKm, p.fixedLossDb, p.noiseRmsDbm, p.cn2, p.fadingRho, p.extraLossDb)
        elif p.propagation is not None:
            prop = run_propagation(p.propagation)      # spatial view only
        res = arq_mod.run_arq(p.payload, p.segmentBytes, p.maxRetries, channel=channel, ber=p.ber,
                              ack_ber=p.ackBer, seed=p.seed, propagation=prop,
                              n_spatial_bins=p.spatialBins)
        res["channel"] = {"p_rx_dBm": None if channel is None else channel.p_rx_dBm,
                          "sigma_I2": None if channel is None else channel.sigma_I2,
                          "mean_ber": res["forward_mean_ber"],
                          "effective_ber": res["forward_effective_ber"],
                          # with a 3D propagation the channel IS that propagation's link:
                          # distanceM is ignored and the link length is reported here
                          "distance_used_m": prop["summary"]["length_m"] if (prop is not None and channel is not None) else p.distanceM}
        if prop is not None:      # bounded summary only, not the whole cloud
            res["propagation_summary"] = {"summary": prop["summary"],
                                          "hotspot": prop["degradation_cloud"]["hotspot"]}
        return coerce_numpy(res)
    except ValueError as error:
        raise _value_error_422(error) from error
    except Exception as error:
        raise internal_server_error(error) from error


class TerminalParams(BaseModel):
    txPowerDbm: float = Field(10.0, ge=-60.0, le=60.0)
    wavelengthNm: float = Field(1550.0, ge=200.0, le=20000.0)
    txLens: LensPresetName = "none"
    rxLens: LensPresetName = "none"
    rxRadiusM: float = Field(0.025, gt=0.0, le=10.0)
    filterIsolationDb: float = Field(40.0, ge=0.0, le=120.0)
    sensitivityDbm: float = Field(-40.0, ge=-150.0, le=60.0)
    noiseRmsDbm: float = Field(-30.0, ge=-120.0, le=0.0)
    nearEndCouplingDb: float = Field(30.0, ge=0.0, le=120.0)

    @model_validator(mode="after")
    def _lens_roles(self):
        if self.txLens == "focusing":
            raise ValueError("txLens 'focusing' is a receive lens")
        if self.rxLens not in ("none", "focusing"):
            raise ValueError("rxLens must be 'none' or 'focusing'")
        return self


class DuplexParams(BaseModel):
    terminalA: TerminalParams = Field(default_factory=lambda: TerminalParams(wavelengthNm=1550.0))
    terminalB: TerminalParams = Field(default_factory=lambda: TerminalParams(wavelengthNm=1310.0))
    payloadA: str = Field("Terminal A -> B: merhaba FSO!", min_length=1, max_length=512)
    payloadB: str = Field("Terminal B -> A: selam!", min_length=1, max_length=512)
    distanceM: float = Field(2000.0, gt=0.0, le=1e6)
    weather: Literal["clear", "haze", "fog", "rain", "snow", "storm"] = "clear"
    cn2: float = Field(1e-15, ge=0.0, le=1e-10)
    segmentBytes: int = Field(8, ge=1, le=255)
    maxRetries: int = Field(5, ge=0, le=200)
    reciprocityRho: float = Field(0.5, ge=0.0, le=1.0)
    fadingRho: float = Field(0.0, ge=0.0, lt=1.0)
    bitrateBps: float = Field(1e9, ge=1e3, le=1e12)
    seed: int = Field(0, ge=0, le=2**31 - 1)
    fixedLossDb: float = Field(3.0, ge=0.0, le=100.0)
    berOverride: Optional[float] = Field(None, ge=0.0, le=1.0)
    propagation: Optional[Propagation3DParams] = None

    @model_validator(mode="after")
    def _sizes(self):
        for name in ("payloadA", "payloadB"):
            nb = len(getattr(self, name).encode("utf-8"))
            if nb > 512 or math.ceil(nb / self.segmentBytes) > 64:
                raise ValueError(f"{name}: at most 512 bytes and 64 segments per direction")
        return self


def _terminal(name: str, t: TerminalParams) -> duplex_mod.Terminal:
    return duplex_mod.Terminal(name, t.txPowerDbm, t.wavelengthNm, t.txLens, t.rxLens, t.rxRadiusM,
                               t.filterIsolationDb, t.sensitivityDbm, t.noiseRmsDbm, t.nearEndCouplingDb)


@app.post("/api/duplex")
def duplex_endpoint(p: DuplexParams):
    """Full-duplex WDM link simulation: both directions + ARQ with piggybacked ACKs."""
    try:
        overrides = None
        distance = p.distanceM
        weather = p.weather
        if p.propagation is not None:
            # The duplex distance is authoritative; the propagation supplies the atmospheric
            # state (extinction, turbulence) rescaled to that distance (see rescale_propagation)
            # and the relative spatial profile for the retry-region map.
            built = build_atmosphere(p.propagation.atmosphere)
            weather = built[1]
            fwd = run_propagation(p.propagation, built, p.terminalA.wavelengthNm)
            rev = run_propagation(p.propagation, built, p.terminalB.wavelengthNm, reverse=True)
            overrides = {duplex_mod.AB: duplex_mod.rescale_propagation(fwd["summary"], distance),
                         duplex_mod.BA: duplex_mod.rescale_propagation(rev["summary"], distance)}
            scales = {k: v.pop("scale") for k, v in overrides.items()}
            for v in overrides.values():
                v.pop("source_length_m")
        res = duplex_mod.run_duplex(
            _terminal("A", p.terminalA), _terminal("B", p.terminalB), p.payloadA, p.payloadB,
            distance, weather, p.cn2, p.segmentBytes, p.maxRetries, p.reciprocityRho, p.fadingRho,
            p.bitrateBps, p.seed, p.fixedLossDb, None, overrides, p.berOverride)
        if p.propagation is not None:
            import random as _random
            for key, prop_res in ((duplex_mod.AB, fwd), (duplex_mod.BA, rev)):
                arq_res = res["directions"][key]["arq_result"]
                failed = sum(sg["failed_attempts"] for sg in arq_res["segments"])
                arq_res["retries_by_spatial_region"] = arq_mod.spatial_retries(
                    prop_res, failed, _random.Random(p.seed), 10, length_m=distance)
            res["propagation_summary"] = {duplex_mod.AB: {"summary": fwd["summary"],
                                                           "hotspot": fwd["degradation_cloud"]["hotspot"]},
                                          duplex_mod.BA: {"summary": rev["summary"],
                                                           "hotspot": rev["degradation_cloud"]["hotspot"]}}
            res["propagation_rescale"] = {
                "distance_used_m": distance, "propagation_length_m": fwd["summary"]["length_m"],
                "scale": scales[duplex_mod.AB], "rescaled": abs(scales[duplex_mod.AB] - 1.0) > 0.05,
                "note": "Mesafe dubleks ayarından alınır; 3B sonuçtan zayıflama ve sintilasyon bu mesafeye ölçeklenir."}
        return coerce_numpy(res)
    except ValueError as error:
        raise _value_error_422(error) from error
    except Exception as error:
        raise internal_server_error(error) from error


# ── Lens designer (materials catalogue, design, ray fan, link effect) ─────────
@app.get("/api/lens/catalog")
def lens_catalog():
    try:
        return materials.catalog()
    except Exception as error:
        raise internal_server_error(error) from error


class LensDesignParams(BaseModel):
    shape: str = "plano_convex"
    material: str = "n_bk7"
    focalLengthM: float = Field(0.1, gt=0.0, le=10.0)
    diameterM: float = Field(0.05, gt=0.0, le=1.0)
    wavelengthNm: float = Field(1550.0, ge=200.0, le=20000.0)
    centerThicknessM: Optional[float] = Field(None, gt=0.0, le=1.0)
    coating: str = "none"
    filter: Optional[str] = "none"
    linkDistanceM: float = Field(1000.0, gt=0.0, le=1e6)
    beamW0M: float = Field(0.002, gt=0.0, le=1.0)
    bareRxRadiusM: float = Field(0.0125, gt=0.0, le=10.0)
    nPoints: int = Field(81, ge=41, le=401)
    nRays: int = Field(9, ge=3, le=21)


def _trace_ray(y_in: float, z_start: float, vz2: float, R1: float, R2: float, k2: float, n: float):
    z1 = _surface_hit(z_start, y_in, 1.0, 0.0, 0.0, R1, 0.0)
    if z1 is None:
        return None
    za, ya, nza, nya = z1
    d1 = _refract(1.0, 0.0, nza, nya, 1.0, n)
    if d1 is None:
        return None
    n2 = _surface_hit(za, ya, d1[0], d1[1], vz2, R2, k2)
    if n2 is None:
        return None
    zb, yb, nzb, nyb = n2
    d2 = _refract(d1[0], d1[1], nzb, nyb, n, 1.0)
    if d2 is None:
        return None
    return (za, ya), (zb, yb), d2


def design_lens_full(p: LensDesignParams) -> Dict[str, Any]:
    lam = p.wavelengthNm * 1e-9
    if p.shape not in materials.LENS_SHAPES:
        raise ValueError(f"Bilinmeyen lens şekli: {p.shape!r}")
    if p.material not in materials.LENS_MATERIALS:
        raise ValueError(f"Bilinmeyen malzeme: {p.material!r}")
    if p.coating not in materials.COATINGS:
        raise ValueError(f"Bilinmeyen kaplama: {p.coating!r}")
    filt = p.filter if p.filter not in (None, "", "none") else None
    if filt is not None and filt not in materials.FILTERS:
        raise ValueError(f"Bilinmeyen filtre: {filt!r}")
    if not materials.LENS_SHAPES[p.shape]["designable"]:
        raise ValueError(f"'{p.shape}' şekli yalnızca açıklayıcıdır, tasarlanamaz (çok malzemeli/GRIN).")
    if not materials.is_transparent(p.material, lam):
        lo, hi = materials.LENS_MATERIALS[p.material]["transmission_um"]
        raise ValueError(f"{p.material} malzemesi {p.wavelengthNm:.0f} nm dalga boyunda opak "
                         f"(geçirgenlik bandı {lo:g}–{hi:g} µm). Başka bir malzeme seçin.")
    try:
        d = materials.design_lens(p.shape, p.material, p.focalLengthM, p.diameterM, lam,
                                  p.centerThicknessM, p.coating, None, p.nPoints)
    except ValueError as error:
        raise ValueError(f"Lens tasarlanamadı: {error}") from error

    n = d["n_used"]
    R1 = d["R1_m"] if d["R1_m"] is not None else math.inf
    R2 = d["R2_m"] if d["R2_m"] is not None else math.inf
    tc = d["center_thickness_m"]
    D = d["diameter_m"]
    h = D / 2.0
    f_sig = d["focal_length_m"]
    k2 = d["conic_k2"]

    # ABCD summary (thick lens, principal planes)
    M = optics.thick_lens(n, R1, R2, tc)
    C = float(M[1][0])
    efl = math.inf if abs(C) < 1e-15 else -1.0 / C
    abcd = {"A": float(M[0][0]), "B": float(M[0][1]), "C": C, "D": float(M[1][1]),
            "efl_m": None if math.isinf(efl) else efl,
            "bfd_m": None if abs(C) < 1e-15 else -float(M[0][0]) / C,
            "ffd_m": None if abs(C) < 1e-15 else -float(M[1][1]) / C}

    # exact-Snell ray fan (rays parallel to the axis)
    sag_max = 0.0
    try:
        sag_max = abs(materials._sag(R1, h))
    except ValueError:
        pass
    z_start = -(0.25 * D + sag_max + 0.02 * abs(f_sig))
    vz2 = tc
    para = _trace_ray(h * 1e-3, z_start, vz2, R1, R2, k2, n)
    focus_z = None
    if para is not None:
        (_, _), (zb, yb), dd = para
        if abs(dd[1]) > 1e-15:
            focus_z = zb - yb * dd[0] / dd[1]
    bfd = None if focus_z is None else focus_z - tc
    z_end = tc + (0.6 * abs(f_sig) if (focus_z is None or f_sig < 0) else max(1.3 * (focus_z - tc), 0.1 * abs(f_sig)))
    if focus_z is not None and f_sig > 0:
        z_end = max(z_end, focus_z + 0.15 * abs(f_sig))
    rays = []
    marginal_cross = None
    for i in range(p.nRays):
        frac = -0.9 + 1.8 * i / (p.nRays - 1)
        y_in = frac * h
        try:
            tr = _trace_ray(y_in, z_start, vz2, R1, R2, k2, n)
        except ValueError:
            tr = None
        if tr is None:
            continue
        (za, ya), (zb, yb), dd = tr
        t_end = (z_end - zb) / dd[0] if dd[0] > 1e-12 else 0.0
        pts = [[z_start, y_in], [za, ya], [zb, yb], [z_end, yb + dd[1] * t_end]]
        rays.append({"y_in": y_in, "points": pts})
        if abs(frac - 0.9) < 1e-9 and abs(dd[1]) > 1e-15:
            marginal_cross = zb - yb * dd[0] / dd[1]
    lsa = None if (marginal_cross is None or focus_z is None) else marginal_cross - focus_z

    t_filter = materials.filter_transmittance(filt, lam) if filt else 1.0
    t_total = d["transmittance"] * t_filter

    # link effect: receiver aperture = lens diameter x transmittance, vs a bare receiver
    w = optics.beam_radius(p.linkDistanceM, p.beamW0M, lam)
    frac_bare = max(optics.received_power_fraction(w, p.bareRxRadiusM), 1e-300)
    frac_lens = max(optics.received_power_fraction(w, h) * t_total, 1e-300)
    gain = 10.0 * math.log10(frac_lens / frac_bare)
    airy = optics.airy_radius(lam, abs(f_sig), D) if f_sig > 0 else None
    link = {"gain_dB": gain, "beam_radius_at_rx_m": w, "frac_bare": frac_bare, "frac_lens": frac_lens,
            "bare_rx_radius_m": p.bareRxRadiusM, "lens_rx_radius_m": h, "link_distance_m": p.linkDistanceM,
            "receiver_suitable": f_sig > 0, "airy_radius_m": airy,
            "note_tr": ("Iraksatıcı (negatif) lens alıcı olarak kullanılamaz; kazanç yalnızca açıklık etkisini gösterir."
                        if f_sig <= 0 else
                        "Kazanç = (lens açıklığı × toplam iletim) ile çıplak alıcı arasındaki toplanan güç oranı.")}
    d_out = {k: v for k, v in d.items()}
    return {
        "design": d_out,
        "filter": {"key": filt or "none", "transmittance": t_filter,
                   "name_tr": materials.FILTERS[filt]["name_tr"] if filt else "Filtre yok"},
        "total_transmittance": t_total,
        "abcd": abcd,
        "ray_fan": {"rays": rays, "z_start": z_start, "z_end": z_end, "focus_z": focus_z, "bfd_m": bfd,
                    "lsa_m": lsa, "front_vertex_z": 0.0, "back_vertex_z": tc,
                    "note_tr": "Işınlar eksene paralel; her yüzeyde tam (vektörel) Snell yasası uygulandı."
                               + (" Fresnel için eşdeğer düz-dışbükey yüzey kullanıldı." if p.shape == "fresnel" else "")},
        "link": link,
        "meta": {"wavelengthNm": p.wavelengthNm, "n": n},
    }


@app.post("/api/lens/design")
def lens_design(p: LensDesignParams):
    try:
        return coerce_numpy(design_lens_full(p))
    except ValueError as error:
        raise _value_error_422(error) from error
    except Exception as error:
        raise internal_server_error(error) from error

from backend.lens_stack_api import lens_stack_router  # noqa: E402
app.include_router(lens_stack_router)
