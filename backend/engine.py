import math
import numpy as np

# ── Vec3 Utility ──────────────────────────────────────────
class Vec3:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def clone(self):
        return Vec3(self.x, self.y, self.z)

    def add(self, v):
        return Vec3(self.x + v.x, self.y + v.y, self.z + v.z)

    def sub(self, v):
        return Vec3(self.x - v.x, self.y - v.y, self.z - v.z)

    def scale(self, s):
        return Vec3(self.x * s, self.y * s, self.z * s)

    def dot(self, v):
        return self.x * v.x + self.y * v.y + self.z * v.z

    def cross(self, v):
        return Vec3(
            self.y * v.z - self.z * v.y,
            self.z * v.x - self.x * v.z,
            self.x * v.y - self.y * v.x
        )

    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def normalize(self):
        length = self.length()
        if length < 1e-12:
            return Vec3(0.0, 0.0, 0.0)
        return self.scale(1.0 / length)

    def to_dict(self):
        return {"x": self.x, "y": self.y, "z": self.z}

    def __str__(self):
        return f"({self.x:.4f}, {self.y:.4f}, {self.z:.4f})"

# ── Atmospheric Constants ────────────────────────────────────
ATM_CONSTANTS = {
    "P0": 101325.0,           # Pa - sea level pressure
    "T0": 288.15,             # K  - standard temperature (15°C)
    "g": 9.80665,             # m/s² - gravitational acceleration
    "M": 0.0289644,           # kg/mol - molar mass of air
    "R": 8.31447,             # J/(mol·K) - universal gas constant
    "Lapse": 0.0065,          # K/m - temperature lapse rate
    "c": 299792458.0,         # m/s - speed of light

    # Edlén equation coefficients
    "A": 8342.54e-8,
    "B": 2406147e-8,
    "C": 130.0,
    "D": 15998e-8,
    "E": 38.9,

    # Beer-Lambert absorption (clear air at 1550nm)
    "alpha_clear": 0.2e-3,
    "alpha_haze": 4.0e-3,
    "alpha_fog": 40.0e-3,
    "alpha_rain": 6.0e-3,

    # Cn² typical values
    "Cn2_weak": 1e-17,
    "Cn2_moderate": 1e-15,
    "Cn2_strong": 1e-13
}

# ── Atmosphere Grid (6 km scale) ─────────────────────────────
class AtmosphereGrid:
    def __init__(self, gridX=60, gridY=10, gridZ=10, cellSize=100.0):
        self.gridX = int(gridX)
        self.gridY = int(gridY)
        self.gridZ = int(gridZ)
        self.cellSize = float(cellSize)

        self.n_base = 1.000293

        total_cells = self.gridX * self.gridY * self.gridZ
        self.grid = np.full(total_cells, self.n_base, dtype=np.float64)
        self.temperature = np.full(total_cells, 20.0, dtype=np.float32)
        self.pressure = np.zeros(total_cells, dtype=np.float32)
        self.humidity = np.full(total_cells, 50.0, dtype=np.float32)
        self.wind = np.zeros(total_cells, dtype=np.float32)
        self.Cn2 = np.full(total_cells, ATM_CONSTANTS["Cn2_moderate"], dtype=np.float64)

        self.initializeDefault()

    def index(self, ix, iy, iz):
        return ix + self.gridX * (iy + self.gridY * iz)

    @property
    def totalDistanceM(self):
        return self.gridX * self.cellSize

    @property
    def totalDistanceKm(self):
        return self.totalDistanceM / 1000.0

    def initializeDefault(self):
        for iz in range(self.gridZ):
            for iy in range(self.gridY):
                for ix in range(self.gridX):
                    idx = self.index(ix, iy, iz)
                    altitude = (iz + 0.5) * self.cellSize
                    self.temperature[idx] = 20.0
                    self.pressure[idx] = self.pressureAtAltitude(altitude)
                    self.humidity[idx] = 50.0
                    self.wind[idx] = 0.0
                    self.Cn2[idx] = ATM_CONSTANTS["Cn2_moderate"]
                    self.grid[idx] = self.n_base

    def pressureAtAltitude(self, h):
        P0 = ATM_CONSTANTS["P0"]
        T0 = ATM_CONSTANTS["T0"]
        g = ATM_CONSTANTS["g"]
        M = ATM_CONSTANTS["M"]
        R = ATM_CONSTANTS["R"]
        Lapse = ATM_CONSTANTS["Lapse"]
        return P0 * math.pow(1.0 - Lapse * h / T0, (g * M) / (R * Lapse))

    def temperatureAtAltitude(self, h, baseTemp=20.0):
        return baseTemp - ATM_CONSTANTS["Lapse"] * h

    def refractiveIndexEdlen(self, tempC, pressurePa, humidity=50.0, wavelengthUm=1.55):
        T_K = tempC + 273.15
        sigma2 = 1.0 / (wavelengthUm * wavelengthUm)

        A = ATM_CONSTANTS["A"]
        B = ATM_CONSTANTS["B"]
        C = ATM_CONSTANTS["C"]
        D = ATM_CONSTANTS["D"]
        E = ATM_CONSTANTS["E"]
        P0 = ATM_CONSTANTS["P0"]
        T0 = ATM_CONSTANTS["T0"]

        n_s_minus_1 = A + B / (C - sigma2) + D / (E - sigma2)
        n_tp = n_s_minus_1 * (pressurePa / P0) * (T0 / T_K)

        e_s = 611.21 * math.exp((18.678 - tempC / 234.5) * (tempC / (257.14 + tempC)))
        e = (humidity / 100.0) * e_s
        humidity_correction = -4.15e-8 * e

        return 1.0 + n_tp + humidity_correction

    def refractiveIndexSimple(self, tempC, pressurePa=101325.0):
        return 1.0 + 0.000293 * (288.15 / (tempC + 273.15)) * (pressurePa / 101325.0)

    def Cn2HufnagelValley(self, h, v_rms=21.0, Cn2_ground=1.7e-14):
        term1 = 0.00594 * math.pow(v_rms / 27.0, 2) * math.pow(1e-5 * h, 10) * math.exp(-h / 1000.0)
        term2 = 2.7e-16 * math.exp(-h / 1500.0)
        term3 = Cn2_ground * math.exp(-h / 100.0)
        return term1 + term2 + term3

    def applyThermalProfile(self, profile, params=None):
        if params is None:
            params = {}

        baseTemp = float(params.get("baseTemp", 20.0))
        deltaT = float(params.get("deltaT", 15.0))
        turbulenceScale = float(params.get("turbulenceScale", 0.3))
        seed = int(params.get("seed", 42))
        humidity_val = float(params.get("humidity", 50.0))
        windSpeed = float(params.get("windSpeed", 5.0))
        Cn2Level = params.get("Cn2Level", "moderate")
        wavelengthUm = float(params.get("wavelengthUm", 1.55))
        useEdlen = bool(params.get("useEdlen", True))

        rng = seed
        def rand():
            nonlocal rng
            rng = (rng * 1664525 + 1013904223) & 0xffffffff
            return (rng >> 0) / 4294967295.0

        def hashVal(ix, iy, iz):
            # Perfect JS signed 32-bit replication in Python using numpy signed integer overflows
            h = np.int64(ix) * 374761393 + np.int64(iy) * 668265263 + np.int64(iz) * 1274126177
            h = np.int32(h)
            h = np.int32((h ^ (h >> 13)) * 1274126177)
            return float(h & np.int32(0x7fffffff)) / 2147483647.0

        def noise3D(x, y, z):
            ix = math.floor(x)
            iy = math.floor(y)
            iz = math.floor(z)
            fx = x - ix
            fy = y - iy
            fz = z - iz
            sx = fx * fx * (3.0 - 2.0 * fx)
            sy = fy * fy * (3.0 - 2.0 * fy)
            sz = fz * fz * (3.0 - 2.0 * fz)

            n000 = hashVal(ix, iy, iz)
            n100 = hashVal(ix + 1, iy, iz)
            n010 = hashVal(ix, iy + 1, iz)
            n110 = hashVal(ix + 1, iy + 1, iz)
            n001 = hashVal(ix, iy, iz + 1)
            n101 = hashVal(ix + 1, iy, iz + 1)
            n011 = hashVal(ix, iy + 1, iz + 1)
            n111 = hashVal(ix + 1, iy + 1, iz + 1)

            nx00 = n000 + sx * (n100 - n000)
            nx10 = n010 + sx * (n110 - n010)
            nx01 = n001 + sx * (n101 - n001)
            nx11 = n011 + sx * (n111 - n011)

            nxy0 = nx00 + sy * (nx10 - nx00)
            nxy1 = nx01 + sy * (nx11 - nx01)

            return nxy0 + sz * (nxy1 - nxy0)

        Cn2Base = {
            "weak": ATM_CONSTANTS["Cn2_weak"],
            "moderate": ATM_CONSTANTS["Cn2_moderate"],
            "strong": ATM_CONSTANTS["Cn2_strong"]
        }.get(Cn2Level, ATM_CONSTANTS["Cn2_moderate"])

        for iz in range(self.gridZ):
            for iy in range(self.gridY):
                for ix in range(self.gridX):
                    idx = self.index(ix, iy, iz)
                    altitude = (iz + 0.5) * self.cellSize
                    temp = baseTemp

                    if profile == "uniform":
                        temp = self.temperatureAtAltitude(altitude, baseTemp)
                    elif profile == "gradient":
                        temp = self.temperatureAtAltitude(altitude, baseTemp) + deltaT * (1.0 - iz / self.gridZ)
                    elif profile == "turbulent":
                        scale1 = noise3D(ix * 0.1, iy * 0.2, iz * 0.15) - 0.5
                        scale2 = noise3D(ix * 0.3, iy * 0.5, iz * 0.4) - 0.5
                        scale3 = noise3D(ix * 0.7, iy * 1.0, iz * 0.9) - 0.5
                        turb = scale1 * 0.6 + scale2 * 0.3 + scale3 * 0.1
                        temp = self.temperatureAtAltitude(altitude, baseTemp) + deltaT * turbulenceScale * turb * 2.0
                    elif profile == "hotspot":
                        cx = self.gridX * 0.4
                        cy = self.gridY * 0.5
                        dx_diff = (ix - cx) / (self.gridX * 0.15)
                        dy_diff = (iy - cy) / (self.gridY * 0.3)
                        dz_diff = iz / (self.gridZ * 0.3)
                        dist2 = dx_diff * dx_diff + dy_diff * dy_diff + dz_diff * dz_diff
                        temp = self.temperatureAtAltitude(altitude, baseTemp) + deltaT * math.exp(-dist2)
                        turbNoise = (noise3D(ix * 0.2, iy * 0.3, iz * 0.2) - 0.5) * 2.0
                        temp += math.exp(-dist2) * deltaT * 0.2 * turbNoise
                    elif profile == "layered":
                        layerThickness = self.gridZ * self.cellSize / 4.0
                        normalizedAlt = altitude / layerThickness
                        layerIndex = math.floor(normalizedAlt)
                        inLayer = normalizedAlt - layerIndex
                        layerTemp = deltaT * 0.5 if (layerIndex % 2 == 0) else -deltaT * 0.3
                        nextLayerTemp = deltaT * 0.5 if ((layerIndex + 1) % 2 == 0) else -deltaT * 0.3
                        smooth = inLayer * inLayer * (3.0 - 2.0 * inLayer)
                        temp = baseTemp + layerTemp + (nextLayerTemp - layerTemp) * smooth
                        temp += (noise3D(ix * 0.15, iy * 0.2, iz * 0.1) - 0.5) * 3.0
                    elif profile == "coastal":
                        transition = ix / self.gridX
                        seaTemp = baseTemp - deltaT * 0.3
                        landTemp = baseTemp + deltaT * 0.5
                        smoothT = transition * transition * (3.0 - 2.0 * transition)
                        temp = seaTemp + (landTemp - seaTemp) * smoothT
                        temp = self.temperatureAtAltitude(altitude, temp)
                        if transition > 0.4:
                            turbScale = (transition - 0.4) / 0.6
                            temp += (noise3D(ix * 0.2, iy * 0.3, iz * 0.25) - 0.5) * deltaT * 0.4 * turbScale

                    windPert = (noise3D(ix * 0.1 + 7.3, iy * 0.2 + 3.1, iz * 0.15 + 5.7) - 0.5) * windSpeed * 0.5
                    self.wind[idx] = windSpeed + windPert
                    temp += windPert * 0.2

                    self.temperature[idx] = temp
                    P = self.pressureAtAltitude(altitude)
                    self.pressure[idx] = P
                    self.humidity[idx] = humidity_val + (rand() - 0.5) * 10.0

                    if useEdlen:
                        self.grid[idx] = self.refractiveIndexEdlen(temp, P, self.humidity[idx], wavelengthUm)
                    else:
                        self.grid[idx] = self.refractiveIndexSimple(temp, P)

                    self.Cn2[idx] = self.Cn2HufnagelValley(altitude, windSpeed + abs(windPert), Cn2Base)

    def getRefractiveIndex(self, ix, iy, iz):
        ix = max(0, min(self.gridX - 1, int(ix)))
        iy = max(0, min(self.gridY - 1, int(iy)))
        iz = max(0, min(self.gridZ - 1, int(iz)))
        return self.grid[self.index(ix, iy, iz)]

    def getTemperature(self, ix, iy, iz):
        ix = max(0, min(self.gridX - 1, int(ix)))
        iy = max(0, min(self.gridY - 1, int(iy)))
        iz = max(0, min(self.gridZ - 1, int(iz)))
        return self.temperature[self.index(ix, iy, iz)]

    def getCn2(self, ix, iy, iz):
        ix = max(0, min(self.gridX - 1, int(ix)))
        iy = max(0, min(self.gridY - 1, int(iy)))
        iz = max(0, min(self.gridZ - 1, int(iz)))
        return self.Cn2[self.index(ix, iy, iz)]

    def worldToGrid(self, pos):
        return {
            "ix": int(math.floor(pos.x / self.cellSize)),
            "iy": int(math.floor(pos.y / self.cellSize)),
            "iz": int(math.floor(pos.z / self.cellSize))
        }

    def inBounds(self, ix, iy, iz):
        return (0 <= ix < self.gridX) and (0 <= iy < self.gridY) and (0 <= iz < self.gridZ)

# ── Vectorial Snell's Law ────────────────────────────────────
def vectorialSnellLaw(incident, normal, n1, n2):
    i = incident.normalize()
    n = normal.normalize()
    ratio = n1 / n2
    cosTheta_i = -i.dot(n)

    adjustedNormal = n
    adjustedCos = cosTheta_i
    if cosTheta_i < 0:
        adjustedNormal = n.scale(-1.0)
        adjustedCos = -cosTheta_i

    sin2Theta_i = 1.0 - adjustedCos * adjustedCos
    sin2Theta_t = ratio * ratio * sin2Theta_i

    if sin2Theta_t > 1.0:
        return None  # Total Internal Reflection (TIR)

    cosTheta_t = math.sqrt(1.0 - sin2Theta_t)
    t = i.scale(ratio).add(adjustedNormal.scale(ratio * adjustedCos - cosTheta_t))

    return {
        "direction": t.normalize(),
        "cosTheta_i": adjustedCos,
        "cosTheta_t": cosTheta_t,
        "ratio": ratio,
        "sin2Theta_t": sin2Theta_t
    }

def verifySnellLaw(n1, n2, cosTheta_i, cosTheta_t):
    sinTheta_i = math.sqrt(max(0.0, 1.0 - cosTheta_i * cosTheta_i))
    sinTheta_t = math.sqrt(max(0.0, 1.0 - cosTheta_t * cosTheta_t))
    lhs = n1 * sinTheta_i
    # Clip to avoid float roundoff issues
    rhs = n2 * sinTheta_t
    error = abs(lhs - rhs)
    return {
        "n1_sin_theta_i": lhs,
        "n2_sin_theta_t": rhs,
        "error": error,
        "valid": error < 1e-10
    }

# ── Atmospheric Loss Calculator ──────────────────────────────
class AtmosphericLoss:
    @staticmethod
    def absorptionLoss(distance_m, weather="clear", wavelengthNm=1550.0):
        # Attenuation coefficient in dB/km
        if weather == "clear":
            alpha_dB_km = 0.2
        elif weather == "haze":
            alpha_dB_km = 4.0
        elif weather == "fog":
            alpha_dB_km = 40.0
        elif weather == "rain":
            alpha_dB_km = 6.0
        elif weather == "snow":
            alpha_dB_km = 20.0
        elif weather == "storm":
            alpha_dB_km = 62.0  # Fırtına: sis+yağmur+kar kombine (~62 dB/km)
        else:
            alpha_dB_km = 0.2

        if weather in ["haze", "fog"]:
            V = 0.5 if (weather == "fog") else 4.0
            if wavelengthNm > 500.0:
                if V > 6.0:
                    q = 1.3
                elif V > 1.0:
                    q = 0.585 * math.pow(V, 1.0 / 3.0)
                else:
                    q = 0.0
            else:
                q = 1.6
            alpha_dB_km = 3.91 / V * math.pow(550.0 / wavelengthNm, q)
        elif weather == "storm":
            # Kombine: yoğun sis (V≈0.1km) + şiddetli yağmur + kar
            V = 0.1
            q = 0.0
            fog_component  = 3.91 / V * math.pow(550.0 / wavelengthNm, q)
            rain_component = 12.0
            snow_component = 20.0
            alpha_dB_km = fog_component + rain_component + snow_component

        alpha_Np_m = alpha_dB_km * math.log(10.0) / 10.0 / 1000.0
        transmission = math.exp(-alpha_Np_m * distance_m)
        loss_dB = -10.0 * math.log10(max(1e-300, transmission))

        return {
            "alpha_dB_km": alpha_dB_km,
            "alpha_Np_m": alpha_Np_m,
            "transmission": transmission,
            "loss_dB": loss_dB,
            "distance_m": distance_m,
            "weather": weather
        }

    @staticmethod
    def geometricLoss(distance_m, beamDivergenceRad=1e-3, receiverDiameterM=0.1, beamDiameterM=0.004):
        beamDiamAtReceiver = beamDiameterM + 2.0 * beamDivergenceRad * distance_m
        ratio = receiverDiameterM / beamDiamAtReceiver
        capturedFraction = min(1.0, ratio * ratio)
        loss_dB = -10.0 * math.log10(max(1e-300, capturedFraction))

        return {
            "beamDiamAtReceiver": beamDiamAtReceiver,
            "capturedFraction": capturedFraction,
            "loss_dB": loss_dB,
            "beamSpreadArea": math.PI * (beamDiamAtReceiver / 2.0) * (beamDiamAtReceiver / 2.0) if hasattr(math, "PI") else 3.141592653589793 * (beamDiamAtReceiver / 2.0) * (beamDiamAtReceiver / 2.0)
        }

    @staticmethod
    def scintillationIndex(distance_m, Cn2, wavelengthM=1550e-9):
        k = 2.0 * math.pi / wavelengthM
        sigma2_R = 1.23 * Cn2 * math.pow(k, 7.0 / 6.0) * math.pow(distance_m, 11.0 / 6.0)

        if sigma2_R < 0.3:
            regime = "weak"
        elif sigma2_R < 1.0:
            regime = "moderate"
        else:
            regime = "strong"

        return {
            "sigma2_R": sigma2_R,
            "sigma_R": math.sqrt(sigma2_R),
            "regime": regime,
            "Cn2": Cn2,
            "description": f"Rytov σ²={sigma2_R:.3e} ({regime})"
        }

    @staticmethod
    def linkBudget(params=None):
        if params is None:
            params = {}

        distance_m = float(params.get("distance_m", 6000.0))
        laserPower_W = float(params.get("laserPower_W", 0.01))
        weather = params.get("weather", "clear")
        wavelengthNm = float(params.get("wavelengthNm", 1550.0))
        beamDivRad = float(params.get("beamDivRad", 1e-3))
        receiverDiamM = float(params.get("receiverDiamM", 0.1))
        beamDiamM = float(params.get("beamDiamM", 0.004))
        Cn2 = float(params.get("Cn2", 1e-15))
        receiverSensitivity_dBm = float(params.get("receiverSensitivity_dBm", -40.0))

        P_tx_dBm = 10.0 * math.log10(laserPower_W * 1000.0)
        absorption = AtmosphericLoss.absorptionLoss(distance_m, weather, wavelengthNm)
        geometric = AtmosphericLoss.geometricLoss(distance_m, beamDivRad, receiverDiamM, beamDiamM)
        scintillation = AtmosphericLoss.scintillationIndex(distance_m, Cn2, wavelengthNm * 1e-9)

        scintPenalty = 5.0 * math.log10(1.0 + scintillation["sigma2_R"]) if (scintillation["sigma2_R"] < 5.0) else 10.0

        totalLoss_dB = absorption["loss_dB"] + geometric["loss_dB"] + scintPenalty
        P_rx_dBm = P_tx_dBm - totalLoss_dB
        linkMargin_dB = P_rx_dBm - receiverSensitivity_dBm

        return {
            "P_tx_dBm": P_tx_dBm,
            "P_rx_dBm": P_rx_dBm,
            "absorption": absorption,
            "geometric": geometric,
            "scintillation": scintillation,
            "scintPenalty_dB": scintPenalty,
            "totalLoss_dB": totalLoss_dB,
            "linkMargin_dB": linkMargin_dB,
            "linkViable": linkMargin_dB > 0,
            "P_rx_W": math.pow(10.0, P_rx_dBm / 10.0) / 1000.0
        }

# ── Ray Tracer (6 km optimized) ──────────────────────────────
class RayTracer:
    def __init__(self, atmosphere):
        self.atmosphere = atmosphere
        self.maxSteps = 50000

    def trace(self, origin, direction):
        atm = self.atmosphere
        path = []
        pos = origin.clone()
        dir = direction.normalize()
        totalDistance = 0.0
        totalInternalReflections = 0
        refractionEvents = []
        steps = 0
        accumulatedCn2 = 0.0

        pathRecordInterval = max(1, int(math.floor(atm.gridX / 500.0)))

        cell = atm.worldToGrid(pos)
        if not atm.inBounds(cell["ix"], cell["iy"], cell["iz"]):
            return {
                "success": False,
                "reason": "Origin out of bounds",
                "path": [],
                "refractionEvents": [],
                "totalDistance": 0.0,
                "totalInternalReflections": 0,
                "avgCn2": 0.0,
                "steps": 0
            }

        currentN = atm.getRefractiveIndex(cell["ix"], cell["iy"], cell["iz"])

        path.append({
            "position": pos.clone(),
            "gridCell": cell.copy(),
            "n": currentN,
            "direction": dir.clone(),
            "distance": 0.0
        })

        while steps < self.maxSteps:
            steps += 1

            boundary = self.findNextBoundary(pos, dir, cell)
            if not boundary:
                break

            newPos = pos.add(dir.scale(boundary["t"]))
            totalDistance += boundary["t"]

            nextCell = {
                "ix": cell["ix"] + boundary["stepX"],
                "iy": cell["iy"] + boundary["stepY"],
                "iz": cell["iz"] + boundary["stepZ"]
            }

            if not atm.inBounds(nextCell["ix"], nextCell["iy"], nextCell["iz"]):
                path.append({
                    "position": newPos.clone(),
                    "gridCell": nextCell.copy(),
                    "n": currentN,
                    "direction": dir.clone(),
                    "distance": totalDistance
                })
                break

            nextN = atm.getRefractiveIndex(nextCell["ix"], nextCell["iy"], nextCell["iz"])
            accumulatedCn2 += atm.getCn2(nextCell["ix"], nextCell["iy"], nextCell["iz"]) * boundary["t"]

            if abs(nextN - currentN) > 1e-15:
                result = vectorialSnellLaw(dir, boundary["normal"], currentN, nextN)

                if result is None:
                    totalInternalReflections += 1
                    reflectDir = dir.sub(boundary["normal"].scale(2.0 * dir.dot(boundary["normal"])))
                    dir = reflectDir.normalize()

                    refractionEvents.append({
                        "position": newPos.clone(),
                        "type": "total_internal_reflection",
                        "n1": currentN,
                        "n2": nextN,
                        "cell": cell.copy()
                    })

                    pos = newPos
                    continue
                else:
                    oldDir = dir.clone()
                    dir = result["direction"]

                    verification = verifySnellLaw(currentN, nextN, result["cosTheta_i"], result["cosTheta_t"])

                    if len(refractionEvents) < 1000:
                        refractionEvents.append({
                            "position": newPos.clone(),
                            "type": "refraction",
                            "n1": currentN,
                            "n2": nextN,
                            "incidentDir": oldDir,
                            "refractedDir": dir.clone(),
                            "cosTheta_i": result["cosTheta_i"],
                            "cosTheta_t": result["cosTheta_t"],
                            "verification": verification,
                            "cell": nextCell.copy()
                        })
                    currentN = nextN
            else:
                currentN = nextN

            pos = newPos
            cell = nextCell

            if steps % pathRecordInterval == 0:
                path.append({
                    "position": pos.clone(),
                    "gridCell": cell.copy(),
                    "n": currentN,
                    "direction": dir.clone(),
                    "distance": totalDistance
                })

        lastPos = path[-1] if path else None
        reachedEnd = bool(lastPos and lastPos["gridCell"]["ix"] >= atm.gridX)
        avgCn2 = accumulatedCn2 / totalDistance if (totalDistance > 0.0) else 0.0

        return {
            "success": reachedEnd,
            "path": path,
            "refractionEvents": refractionEvents,
            "totalDistance": totalDistance,
            "totalInternalReflections": totalInternalReflections,
            "steps": steps,
            "avgCn2": avgCn2,
            "exitPosition": lastPos["position"] if lastPos else None,
            "exitDirection": lastPos["direction"] if lastPos else None
        }

    def findNextBoundary(self, pos, dir, cell):
        cs = self.atmosphere.cellSize
        eps = 1e-10

        xMin, xMax = cell["ix"] * cs, (cell["ix"] + 1) * cs
        yMin, yMax = cell["iy"] * cs, (cell["iy"] + 1) * cs
        zMin, zMax = cell["iz"] * cs, (cell["iz"] + 1) * cs

        tMin = float("inf")
        normal = None
        stepX = 0
        stepY = 0
        stepZ = 0

        if abs(dir.x) > eps:
            tX = (xMax - pos.x) / dir.x if (dir.x > 0) else (xMin - pos.x) / dir.x
            if eps < tX < tMin:
                tMin = tX
                normal = Vec3(-1, 0, 0) if (dir.x > 0) else Vec3(1, 0, 0)
                stepX = 1 if (dir.x > 0) else -1
                stepY = 0
                stepZ = 0

        if abs(dir.y) > eps:
            tY = (yMax - pos.y) / dir.y if (dir.y > 0) else (yMin - pos.y) / dir.y
            if eps < tY < tMin:
                tMin = tY
                normal = Vec3(0, -1, 0) if (dir.y > 0) else Vec3(0, 1, 0)
                stepX = 0
                stepY = 1 if (dir.y > 0) else -1
                stepZ = 0

        if abs(dir.z) > eps:
            tZ = (zMax - pos.z) / dir.z if (dir.z > 0) else (zMin - pos.z) / dir.z
            if eps < tZ < tMin:
                tMin = tZ
                normal = Vec3(0, 0, -1) if (dir.z > 0) else Vec3(0, 0, 1)
                stepX = 0
                stepY = 0
                stepZ = 1 if (dir.z > 0) else -1

        if tMin == float("inf"):
            return None
        return {
            "t": tMin + eps,
            "normal": normal,
            "stepX": stepX,
            "stepY": stepY,
            "stepZ": stepZ
        }

# ── Data Transmission Simulator (6 km) ───────────────────────
class DataTransmissionSimulator:
    def __init__(self, rayTracer, sourcePos, targetPos, receiverRadius=0.5):
        self.rayTracer = rayTracer
        self.sourcePos = sourcePos
        self.targetPos = targetPos
        self.receiverRadius = receiverRadius
        self.beamDivergence = 0.001

    def sendBit(self, bit):
        if bit == 0:
            return {
                "sent": 0,
                "received": 0,
                "correct": True,
                "trace": None,
                "signalPower": 0.0,
                "lateralDeviation": float("inf")
            }

        dir = self.targetPos.sub(self.sourcePos).normalize()
        trace = self.rayTracer.trace(self.sourcePos, dir)
        distance = self.sourcePos.sub(self.targetPos).length()

        linkBudget = AtmosphericLoss.linkBudget({
            "distance_m": distance,
            "Cn2": trace["avgCn2"] if trace["avgCn2"] > 0 else 1e-15
        })

        received = 0
        lateralDeviation = float("inf")
        if trace["exitPosition"]:
            exit_pos = trace["exitPosition"]
            lateralDeviation = math.sqrt(
                math.pow(exit_pos.y - self.targetPos.y, 2.0) +
                math.pow(exit_pos.z - self.targetPos.z, 2.0)
            )
            if lateralDeviation <= self.receiverRadius and linkBudget["linkViable"]:
                received = 1

        return {
            "sent": 1,
            "received": received,
            "correct": received == 1,
            "trace": trace,
            "lateralDeviation": lateralDeviation,
            "signalPower": linkBudget["P_rx_dBm"],
            "linkBudget": linkBudget
        }

    def sendPacket(self, data):
        results = []
        correctBits = 0
        totalOnes = 0
        totalZeros = 0
        missedOnes = 0

        for bit in data:
            result = self.sendBit(bit)
            results.append(result)
            if result["correct"]:
                correctBits += 1
            if bit == 1:
                totalOnes += 1
                if not result["correct"]:
                    missedOnes += 1
            else:
                totalZeros += 1

        bit_error_rate = 1.0 - (correctBits / len(data)) if len(data) > 0 else 0.0
        success_rate = (correctBits / len(data)) * 100.0 if len(data) > 0 else 0.0

        return {
            "data": data,
            "results": results,
            "totalBits": len(data),
            "correctBits": correctBits,
            "bitErrorRate": bit_error_rate,
            "totalOnes": totalOnes,
            "totalZeros": totalZeros,
            "missedOnes": missedOnes,
            "successRate": success_rate
        }

    @staticmethod
    def generateTestData(pattern, length=32):
        data = []
        if pattern == "alternating":
            for i in range(length):
                data.append(i % 2)
        elif pattern == "all_ones":
            for _ in range(length):
                data.append(1)
        elif pattern == "all_zeros":
            for _ in range(length):
                data.append(0)
        elif pattern == "random":
            import random
            for _ in range(length):
                data.append(1 if random.random() > 0.5 else 0)
        elif pattern == "burst":
            for i in range(length):
                data.append(1 if (i // 8) % 2 == 0 else 0)
        elif pattern == "ascii_hello":
            return [0,1,0,0,1,0,0,0, 0,1,0,0,0,1,0,1, 0,1,0,0,1,1,0,0, 0,1,0,0,1,1,0,0, 0,1,0,0,1,1,1,1]
        elif pattern == "prbs7":
            lfsr = 0x7F
            for _ in range(length):
                bit = lfsr & 1
                data.append(bit)
                fb = ((lfsr >> 0) ^ (lfsr >> 1)) & 1
                lfsr = (lfsr >> 1) | (fb << 6)
        else:
            import random
            for _ in range(length):
                data.append(1 if random.random() > 0.5 else 0)
        return data

# ── Lens System ──────────────────────────────────────────────
class LensSystem:
    """
    Optical lens system for FSO links.
    Models 5 lens types and their effects on beam divergence,
    aperture, and power transmission.
    """

    LENS_TYPES = {
        "none": {
            "name": "Lens Yok",
            "divergenceReduction": 0.0,
            "apertureBoost": 1.0,
            "transmittance": 1.0,
            "focalLength_m": None,
            "description": "Lens sistemi yok – ham ışın"
        },
        "collimating": {
            "name": "Kollimating Lens",
            "divergenceReduction": 0.85,
            "apertureBoost": 1.2,
            "transmittance": 0.96,
            "focalLength_m": 0.05,
            "description": "Işını paralel hale getirir, diverjansı minimize eder"
        },
        "focusing": {
            "name": "Odaklayıcı Lens",
            "divergenceReduction": 0.70,
            "apertureBoost": 1.8,
            "transmittance": 0.94,
            "focalLength_m": 0.10,
            "description": "Alıcı odak noktasına odaklar, spot boyutunu küçültür"
        },
        "galilean_telescope": {
            "name": "Galilean Teleskop",
            "divergenceReduction": 0.92,
            "apertureBoost": 2.5,
            "transmittance": 0.90,
            "focalLength_m": 0.20,
            "description": "Işın genişletici, uzun mesafe için optimum"
        },
        "cassegrain": {
            "name": "Cassegrain Teleskop",
            "divergenceReduction": 0.95,
            "apertureBoost": 4.0,
            "transmittance": 0.88,
            "focalLength_m": 0.50,
            "description": "Büyük açıklık, en yüksek kazanç, profesyonel FSO"
        },
        "diverging": {
            "name": "Iraksak Lens",
            "divergenceReduction": -0.5,
            "apertureBoost": 0.8,
            "transmittance": 0.92,
            "focalLength_m": -0.08,
            "description": "Test amaçlı ıraksak lens – performans düşürür"
        }
    }

    def __init__(self, lens_type="none", custom_focal_length_m=None, custom_aperture_m=None):
        self.lens_type = lens_type
        self.spec = self.LENS_TYPES.get(lens_type, self.LENS_TYPES["none"])
        self.focal_length_m = custom_focal_length_m if custom_focal_length_m is not None else self.spec["focalLength_m"]
        self.aperture_m = custom_aperture_m if custom_aperture_m is not None else 0.05

    def apply_to_beam(self, beam_params):
        """
        Apply lens effects to beam parameters.
        Returns modified beam divergence and transmittance factor.
        beam_params: dict with divergenceRad, beamDiamM, receiverDiamM
        """
        divergence_rad = beam_params.get("divergenceRad", 1e-3)
        beam_diam_m = beam_params.get("beamDiamM", 0.004)
        receiver_diam_m = beam_params.get("receiverDiamM", 0.1)
        spec = self.spec

        dr = spec["divergenceReduction"]
        if dr >= 0:
            effective_divergence = divergence_rad * (1.0 - dr)
        else:
            effective_divergence = divergence_rad * (1.0 + abs(dr))
        effective_divergence = max(effective_divergence, 1e-6)

        effective_receiver_diam = receiver_diam_m * spec["apertureBoost"]

        return {
            "divergenceRad": effective_divergence,
            "beamDiamM": beam_diam_m,
            "receiverDiamM": effective_receiver_diam,
            "transmittance": spec["transmittance"]
        }

    def get_link_budget_gain_dB(self, distance_m, base_div_rad=1e-3, base_receiver_diam_m=0.1, base_beam_diam_m=0.004):
        """Calculate net dB gain from this lens (positive = improvement)."""
        with_lens = self.apply_to_beam({
            "divergenceRad": base_div_rad,
            "beamDiamM": base_beam_diam_m,
            "receiverDiamM": base_receiver_diam_m
        })
        geo_base = AtmosphericLoss.geometricLoss(distance_m, base_div_rad, base_receiver_diam_m, base_beam_diam_m)
        geo_lens = AtmosphericLoss.geometricLoss(distance_m, with_lens["divergenceRad"], with_lens["receiverDiamM"], base_beam_diam_m)

        lens_tx_loss_dB = -10.0 * math.log10(max(1e-300, with_lens["transmittance"]))
        geometric_gain_dB = geo_base["loss_dB"] - geo_lens["loss_dB"]
        return geometric_gain_dB - lens_tx_loss_dB

    def __repr__(self):
        return f"LensSystem({self.lens_type}, f={self.focal_length_m}m)"


# ── Environment Presets ───────────────────────────────────────
class EnvironmentPresets:
    """
    Real-world environment presets for FSO link simulation.
    Each preset configures atmosphere, weather, turbulence, and temperature.
    """

    PRESETS = {
        "urban": {
            "name": "Şehir İçi (Urban)",
            "thermalProfile": "hotspot",
            "weather": "haze",
            "Cn2Level": "moderate",
            "baseTemp": 28.0,
            "deltaT": 20.0,
            "windSpeed": 3.0,
            "humidity": 60.0,
            "description": "Şehir merkezi, ısı adaları, orta türbülans, hafif pus"
        },
        "maritime": {
            "name": "Deniz / Kıyı (Maritime)",
            "thermalProfile": "coastal",
            "weather": "haze",
            "Cn2Level": "weak",
            "baseTemp": 18.0,
            "deltaT": 8.0,
            "windSpeed": 12.0,
            "humidity": 85.0,
            "description": "Deniz-kara geçişi, yüksek nem, güçlü rüzgar, zayıf türbülans"
        },
        "desert": {
            "name": "Çöl / Arid (Desert)",
            "thermalProfile": "hotspot",
            "weather": "clear",
            "Cn2Level": "strong",
            "baseTemp": 42.0,
            "deltaT": 35.0,
            "windSpeed": 8.0,
            "humidity": 10.0,
            "description": "Aşırı sıcaklık gradyanı, güçlü termal türbülans, berrak hava"
        },
        "mountain": {
            "name": "Dağlık (Mountain)",
            "thermalProfile": "layered",
            "weather": "clear",
            "Cn2Level": "weak",
            "baseTemp": 8.0,
            "deltaT": 12.0,
            "windSpeed": 15.0,
            "humidity": 45.0,
            "description": "Yüksek irtifa, katmanlı atmosfer, berrak hava, düşük türbülans"
        },
        "industrial": {
            "name": "Endüstriyel (Industrial)",
            "thermalProfile": "turbulent",
            "weather": "fog",
            "Cn2Level": "strong",
            "baseTemp": 32.0,
            "deltaT": 25.0,
            "windSpeed": 5.0,
            "humidity": 75.0,
            "description": "Fabrika sahası, yoğun türbülans, sanayi sisi, yüksek kayıp"
        },
        "arctic": {
            "name": "Arktik (Arctic)",
            "thermalProfile": "uniform",
            "weather": "snow",
            "Cn2Level": "weak",
            "baseTemp": -15.0,
            "deltaT": 5.0,
            "windSpeed": 20.0,
            "humidity": 80.0,
            "description": "Kutup bölgesi, kar yağışı, düşük türbülans, çok soğuk"
        },
        "storm": {
            "name": "Fırtına (Storm)",
            "thermalProfile": "turbulent",
            "weather": "storm",
            "Cn2Level": "strong",
            "baseTemp": 10.0,
            "deltaT": 8.0,
            "windSpeed": 28.0,
            "humidity": 98.0,
            "description": "Yağmurlu + sisli + karlı + şiddetli rüzgar · En kötü FSO koşulu"
        }
    }

    @classmethod
    def get_preset(cls, preset_key):
        return cls.PRESETS.get(preset_key, cls.PRESETS["urban"])

    @classmethod
    def get_preset_keys(cls):
        return list(cls.PRESETS.keys())


# ── Comprehensive Transmission Test ─────────────────────────
class ComprehensiveTransmissionTest:
    """
    Tests all environment × lens combinations and returns success rates.
    This is the 'how much data gets through' benchmark.
    """

    @staticmethod
    def run_all(base_params=None):
        """
        Run all environment × lens combinations.
        Returns list of result dicts with successRate (%).
        """
        if base_params is None:
            base_params = {}

        grid_x = int(base_params.get("gridX", 30))
        grid_y = int(base_params.get("gridY", 6))
        grid_z = int(base_params.get("gridZ", 6))
        cell_size = float(base_params.get("cellSize", 200.0))
        wavelength_nm = float(base_params.get("wavelengthNm", 1550.0))
        num_bits = int(base_params.get("numBits", 32))
        pattern = base_params.get("pattern", "alternating")
        laser_power_mw = float(base_params.get("laserPower_mW", 10.0))
        receiver_diam_m = float(base_params.get("receiverDiamM", 0.10))
        beam_div_rad = float(base_params.get("beamDivRad", 1e-3))
        receiver_sensitivity_dBm = float(base_params.get("receiverSensitivity_dBm", -40.0))

        environments = EnvironmentPresets.get_preset_keys()
        lens_types = list(LensSystem.LENS_TYPES.keys())
        results = []

        for env_key in environments:
            env_preset = EnvironmentPresets.get_preset(env_key)

            atmosphere = AtmosphereGrid(grid_x, grid_y, grid_z, cell_size)
            atmosphere.applyThermalProfile(env_preset["thermalProfile"], {
                "baseTemp": env_preset["baseTemp"],
                "deltaT": env_preset["deltaT"],
                "humidity": env_preset["humidity"],
                "windSpeed": env_preset["windSpeed"],
                "Cn2Level": env_preset["Cn2Level"],
                "wavelengthUm": wavelength_nm / 1000.0,
                "useEdlen": True
            })

            ray_tracer = RayTracer(atmosphere)
            source_pos = Vec3(0.5 * cell_size, grid_y * cell_size * 0.5, grid_z * cell_size * 0.5)
            target_pos = Vec3((grid_x - 0.5) * cell_size, grid_y * cell_size * 0.5, grid_z * cell_size * 0.5)
            distance = source_pos.sub(target_pos).length()

            # Pre-compute one trace (ray path doesn't change per lens)
            dir_vec = target_pos.sub(source_pos).normalize()
            trace = ray_tracer.trace(source_pos, dir_vec)

            for lens_key in lens_types:
                lens = LensSystem(lens_key)
                lensed_beam = lens.apply_to_beam({
                    "divergenceRad": beam_div_rad,
                    "beamDiamM": 0.004,
                    "receiverDiamM": receiver_diam_m
                })
                effective_recv_r = lensed_beam["receiverDiamM"] / 2.0

                # Generate test data
                data = DataTransmissionSimulator.generateTestData(pattern, num_bits)

                link_budget = AtmosphericLoss.linkBudget({
                    "distance_m": distance,
                    "laserPower_W": laser_power_mw / 1000.0,
                    "weather": env_preset["weather"],
                    "wavelengthNm": wavelength_nm,
                    "beamDivRad": lensed_beam["divergenceRad"],
                    "receiverDiamM": lensed_beam["receiverDiamM"],
                    "beamDiamM": 0.004,
                    "Cn2": trace["avgCn2"] if trace["avgCn2"] > 0 else 1e-15,
                    "receiverSensitivity_dBm": receiver_sensitivity_dBm
                })

                # Apply lens transmittance penalty
                effective_prx = link_budget["P_rx_dBm"] + 10.0 * math.log10(max(1e-300, lensed_beam["transmittance"]))
                effective_link_viable = effective_prx > receiver_sensitivity_dBm

                correct_bits = 0
                for bit in data:
                    if bit == 0:
                        correct_bits += 1
                        continue
                    received = 0
                    if trace["exitPosition"]:
                        exit_pos = trace["exitPosition"]
                        lat_dev = math.sqrt(
                            math.pow(exit_pos.y - target_pos.y, 2.0) +
                            math.pow(exit_pos.z - target_pos.z, 2.0)
                        )
                        if lat_dev <= effective_recv_r and effective_link_viable:
                            received = 1
                    if received == 1:
                        correct_bits += 1

                success_rate = (correct_bits / num_bits) * 100.0 if num_bits > 0 else 0.0
                lens_gain = lens.get_link_budget_gain_dB(distance, beam_div_rad, receiver_diam_m, 0.004)

                results.append({
                    "environment": env_key,
                    "envName": env_preset["name"],
                    "lens": lens_key,
                    "lensName": LensSystem.LENS_TYPES[lens_key]["name"],
                    "successRate": round(success_rate, 1),
                    "correctBits": correct_bits,
                    "totalBits": num_bits,
                    "lensGain_dB": round(lens_gain, 2),
                    "weather": env_preset["weather"],
                    "Cn2Level": env_preset["Cn2Level"],
                    "P_rx_dBm": round(effective_prx, 2),
                    "linkViable": effective_link_viable
                })

        return results
