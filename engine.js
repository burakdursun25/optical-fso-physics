/**
 * ============================================================
 *  FSO (Free-Space Optical) Physics Engine v2.0
 *  ─────────────────────────────────────────────
 *  6 km Range + COMSOL-Compatible Atmospheric Model
 *  
 *  Features:
 *  - Vectorial Snell's Law (ray bending at cell boundaries)
 *  - Modified Edlén equation for n(T, P, humidity)
 *  - Beer-Lambert atmospheric absorption
 *  - Kolmogorov turbulence model (Cn²)
 *  - Rytov scintillation index
 *  - Beam spreading & geometric loss
 *  - 3D atmosphere grid with realistic profiles
 * ============================================================
 */

// ── Vector3 Utility ──────────────────────────────────────────
class Vec3 {
    constructor(x = 0, y = 0, z = 0) {
        this.x = x; this.y = y; this.z = z;
    }
    clone() { return new Vec3(this.x, this.y, this.z); }
    add(v) { return new Vec3(this.x + v.x, this.y + v.y, this.z + v.z); }
    sub(v) { return new Vec3(this.x - v.x, this.y - v.y, this.z - v.z); }
    scale(s) { return new Vec3(this.x * s, this.y * s, this.z * s); }
    dot(v) { return this.x * v.x + this.y * v.y + this.z * v.z; }
    cross(v) {
        return new Vec3(
            this.y * v.z - this.z * v.y,
            this.z * v.x - this.x * v.z,
            this.x * v.y - this.y * v.x
        );
    }
    length() { return Math.sqrt(this.x * this.x + this.y * this.y + this.z * this.z); }
    normalize() {
        const len = this.length();
        if (len < 1e-12) return new Vec3(0, 0, 0);
        return this.scale(1 / len);
    }
    toString() { return `(${this.x.toFixed(4)}, ${this.y.toFixed(4)}, ${this.z.toFixed(4)})`; }
}

// ── Atmospheric Constants ────────────────────────────────────
const ATM_CONSTANTS = {
    // Standard atmosphere
    P0: 101325,           // Pa - sea level pressure
    T0: 288.15,           // K  - standard temperature (15°C)
    g: 9.80665,           // m/s² - gravitational acceleration
    M: 0.0289644,         // kg/mol - molar mass of air
    R: 8.31447,           // J/(mol·K) - universal gas constant
    Lapse: 0.0065,        // K/m - temperature lapse rate

    // Optical
    c: 299792458,         // m/s - speed of light

    // Edlén equation coefficients (modified)
    // n_air - 1 = (A + B/(C - σ²) + D/(E - σ²)) × (P/P0) × (T0/T)
    A: 8342.54e-8,
    B: 2406147e-8,
    C: 130,               // µm^-2
    D: 15998e-8,
    E: 38.9,              // µm^-2

    // Beer-Lambert absorption (clear air at 1550nm)
    alpha_clear: 0.2e-3,  // dB/m - very clear
    alpha_haze: 4.0e-3,   // dB/m - light haze
    alpha_fog: 40.0e-3,   // dB/m - moderate fog
    alpha_rain: 6.0e-3,   // dB/m - moderate rain

    // Cn² typical values
    Cn2_weak: 1e-17,      // m^(-2/3) - weak turbulence
    Cn2_moderate: 1e-15,  // m^(-2/3) - moderate
    Cn2_strong: 1e-13,    // m^(-2/3) - strong (near ground, hot)
};

// ── Atmosphere Grid (6 km scale) ─────────────────────────────
class AtmosphereGrid {
    /**
     * @param {number} gridX - Cells along propagation (X)
     * @param {number} gridY - Cells horizontal-lateral (Y)
     * @param {number} gridZ - Cells vertical/altitude (Z)
     * @param {number} cellSize - meters per cell
     */
    constructor(gridX = 60, gridY = 10, gridZ = 10, cellSize = 100.0) {
        this.gridX = gridX;
        this.gridY = gridY;
        this.gridZ = gridZ;
        this.cellSize = cellSize;

        this.n_base = 1.000293;

        this.grid = new Float64Array(gridX * gridY * gridZ);
        this.temperature = new Float32Array(gridX * gridY * gridZ);
        this.pressure = new Float32Array(gridX * gridY * gridZ);
        this.humidity = new Float32Array(gridX * gridY * gridZ);
        this.wind = new Float32Array(gridX * gridY * gridZ);
        this.Cn2 = new Float64Array(gridX * gridY * gridZ);

        this.initializeDefault();
    }

    index(ix, iy, iz) {
        return ix + this.gridX * (iy + this.gridY * iz);
    }

    get totalDistanceM() { return this.gridX * this.cellSize; }
    get totalDistanceKm() { return this.totalDistanceM / 1000; }

    initializeDefault() {
        for (let iz = 0; iz < this.gridZ; iz++) {
            for (let iy = 0; iy < this.gridY; iy++) {
                for (let ix = 0; ix < this.gridX; ix++) {
                    const idx = this.index(ix, iy, iz);
                    const altitude = (iz + 0.5) * this.cellSize;
                    this.temperature[idx] = 20;
                    this.pressure[idx] = this.pressureAtAltitude(altitude);
                    this.humidity[idx] = 50; // % RH
                    this.wind[idx] = 0;
                    this.Cn2[idx] = ATM_CONSTANTS.Cn2_moderate;
                    this.grid[idx] = this.n_base;
                }
            }
        }
    }

    /** Barometric formula: P(h) = P0 × (1 - Lh/T0)^(gM/RL) */
    pressureAtAltitude(h) {
        const { P0, T0, g, M, R, Lapse } = ATM_CONSTANTS;
        return P0 * Math.pow(1 - Lapse * h / T0, (g * M) / (R * Lapse));
    }

    /** Temperature at altitude with lapse rate */
    temperatureAtAltitude(h, baseTemp = 20) {
        return baseTemp - ATM_CONSTANTS.Lapse * h;
    }

    /**
     * Modified Edlén equation for refractive index of air
     * n(λ, T, P, RH) with wavelength, temperature, pressure, humidity
     *
     * @param {number} tempC - Temperature in °C
     * @param {number} pressurePa - Pressure in Pa
     * @param {number} humidity - Relative humidity 0-100
     * @param {number} wavelengthUm - Wavelength in µm (default 1.55 for telecom)
     */
    refractiveIndexEdlen(tempC, pressurePa, humidity = 50, wavelengthUm = 1.55) {
        const T_K = tempC + 273.15;
        const sigma2 = 1 / (wavelengthUm * wavelengthUm); // µm^-2

        // Dispersion formula (phase refractive index of standard air)
        const { A, B, C, D, E, P0, T0 } = ATM_CONSTANTS;
        const n_s_minus_1 = A + B / (C - sigma2) + D / (E - sigma2);

        // Temperature and pressure correction
        const n_tp = n_s_minus_1 * (pressurePa / P0) * (T0 / T_K);

        // Humidity correction (water vapor contribution)
        // Saturation vapor pressure (Buck equation)
        const e_s = 611.21 * Math.exp((18.678 - tempC / 234.5) * (tempC / (257.14 + tempC)));
        const e = (humidity / 100) * e_s;
        // Water vapor correction to refractive index
        const humidity_correction = -4.15e-8 * e; // simplified

        return 1 + n_tp + humidity_correction;
    }

    /**
     * Simplified Edlén for fast computation (compatible with COMSOL)
     */
    refractiveIndexSimple(tempC, pressurePa = 101325) {
        return 1 + 0.000293 * (288.15 / (tempC + 273.15)) * (pressurePa / 101325);
    }

    /**
     * Cn² structure parameter model (Hufnagel-Valley)
     * Cn²(h) = 0.00594(v/27)²(10⁻⁵h)¹⁰ exp(-h/1000) 
     *        + 2.7×10⁻¹⁶ exp(-h/1500)
     *        + Cn²(0) exp(-h/100)
     * @param {number} h - altitude in meters
     * @param {number} v_rms - rms wind speed (m/s)
     * @param {number} Cn2_ground - ground-level Cn² 
     */
    Cn2HufnagelValley(h, v_rms = 21, Cn2_ground = 1.7e-14) {
        const term1 = 0.00594 * Math.pow(v_rms / 27, 2)
            * Math.pow(1e-5 * h, 10)
            * Math.exp(-h / 1000);
        const term2 = 2.7e-16 * Math.exp(-h / 1500);
        const term3 = Cn2_ground * Math.exp(-h / 100);
        return term1 + term2 + term3;
    }

    /**
     * Apply thermal profile for 6 km range
     */
    applyThermalProfile(profile, params = {}) {
        const {
            baseTemp = 20,
            deltaT = 15,
            turbulenceScale = 0.3,
            seed = 42,
            humidity = 50,
            windSpeed = 5,
            Cn2Level = 'moderate',
            wavelengthUm = 1.55,
            useEdlen = true
        } = params;

        let rng = seed;
        const rand = () => {
            rng = (rng * 1664525 + 1013904223) & 0xffffffff;
            return (rng >>> 0) / 0xffffffff;
        };

        // Perlin-like noise for smooth turbulence
        const noise3D = (x, y, z) => {
            // Simple hash-based smooth noise
            const hashVal = (ix, iy, iz) => {
                let h = ix * 374761393 + iy * 668265263 + iz * 1274126177;
                h = (h ^ (h >> 13)) * 1274126177;
                return (h & 0x7fffffff) / 0x7fffffff;
            };

            const ix = Math.floor(x), iy = Math.floor(y), iz = Math.floor(z);
            const fx = x - ix, fy = y - iy, fz = z - iz;
            const sx = fx * fx * (3 - 2 * fx);
            const sy = fy * fy * (3 - 2 * fy);
            const sz = fz * fz * (3 - 2 * fz);

            const n000 = hashVal(ix, iy, iz);
            const n100 = hashVal(ix + 1, iy, iz);
            const n010 = hashVal(ix, iy + 1, iz);
            const n110 = hashVal(ix + 1, iy + 1, iz);
            const n001 = hashVal(ix, iy, iz + 1);
            const n101 = hashVal(ix + 1, iy, iz + 1);
            const n011 = hashVal(ix, iy + 1, iz + 1);
            const n111 = hashVal(ix + 1, iy + 1, iz + 1);

            const nx00 = n000 + sx * (n100 - n000);
            const nx10 = n010 + sx * (n110 - n010);
            const nx01 = n001 + sx * (n101 - n001);
            const nx11 = n011 + sx * (n111 - n011);

            const nxy0 = nx00 + sy * (nx10 - nx00);
            const nxy1 = nx01 + sy * (nx11 - nx01);

            return nxy0 + sz * (nxy1 - nxy0);
        };

        // Cn² base value
        const Cn2Base = {
            'weak': ATM_CONSTANTS.Cn2_weak,
            'moderate': ATM_CONSTANTS.Cn2_moderate,
            'strong': ATM_CONSTANTS.Cn2_strong
        }[Cn2Level] || ATM_CONSTANTS.Cn2_moderate;

        for (let iz = 0; iz < this.gridZ; iz++) {
            for (let iy = 0; iy < this.gridY; iy++) {
                for (let ix = 0; ix < this.gridX; ix++) {
                    const idx = this.index(ix, iy, iz);
                    const altitude = (iz + 0.5) * this.cellSize;
                    const distX = (ix + 0.5) * this.cellSize;
                    let temp = baseTemp;

                    switch (profile) {
                        case 'uniform':
                            temp = this.temperatureAtAltitude(altitude, baseTemp);
                            break;

                        case 'gradient':
                            // Vertical gradient + lapse rate
                            temp = this.temperatureAtAltitude(altitude, baseTemp)
                                + deltaT * (1 - iz / this.gridZ);
                            break;

                        case 'turbulent': {
                            // Multi-scale turbulence using noise
                            const scale1 = noise3D(ix * 0.1, iy * 0.2, iz * 0.15) - 0.5;
                            const scale2 = noise3D(ix * 0.3, iy * 0.5, iz * 0.4) - 0.5;
                            const scale3 = noise3D(ix * 0.7, iy * 1.0, iz * 0.9) - 0.5;
                            const turb = scale1 * 0.6 + scale2 * 0.3 + scale3 * 0.1;
                            temp = this.temperatureAtAltitude(altitude, baseTemp)
                                + deltaT * turbulenceScale * turb * 2;
                            break;
                        }

                        case 'hotspot': {
                            // Simulate heated ground area (e.g., asphalt, desert)
                            const cx = this.gridX * 0.4;
                            const cy = this.gridY * 0.5;
                            const cz = 0; // ground level
                            const dx = (ix - cx) / (this.gridX * 0.15);
                            const dy = (iy - cy) / (this.gridY * 0.3);
                            const dz = iz / (this.gridZ * 0.3);
                            const dist2 = dx * dx + dy * dy + dz * dz;
                            temp = this.temperatureAtAltitude(altitude, baseTemp)
                                + deltaT * Math.exp(-dist2);
                            // Add turbulence near hotspot
                            const turbNoise = (noise3D(ix * 0.2, iy * 0.3, iz * 0.2) - 0.5) * 2;
                            temp += Math.exp(-dist2) * deltaT * 0.2 * turbNoise;
                            break;
                        }

                        case 'layered': {
                            // Thermal inversion layers (common in coastal/valley areas)
                            const layerThickness = this.gridZ * this.cellSize / 4;
                            const normalizedAlt = altitude / layerThickness;
                            const layerIndex = Math.floor(normalizedAlt);
                            const inLayer = normalizedAlt - layerIndex;

                            // Smooth transition between layers
                            const layerTemp = layerIndex % 2 === 0 ? deltaT * 0.5 : -deltaT * 0.3;
                            const nextLayerTemp = (layerIndex + 1) % 2 === 0 ? deltaT * 0.5 : -deltaT * 0.3;
                            const smooth = inLayer * inLayer * (3 - 2 * inLayer); // smoothstep

                            temp = baseTemp + layerTemp + (nextLayerTemp - layerTemp) * smooth;
                            temp += (noise3D(ix * 0.15, iy * 0.2, iz * 0.1) - 0.5) * 3;
                            break;
                        }

                        case 'coastal': {
                            // Sea-land temperature contrast
                            // Left side: cooler (sea), right side: warmer (land)
                            const transition = ix / this.gridX;
                            const seaTemp = baseTemp - deltaT * 0.3;
                            const landTemp = baseTemp + deltaT * 0.5;
                            const smoothT = transition * transition * (3 - 2 * transition);
                            temp = seaTemp + (landTemp - seaTemp) * smoothT;
                            temp = this.temperatureAtAltitude(altitude, temp);
                            // Add convection turbulence over land
                            if (transition > 0.4) {
                                const turbScale = (transition - 0.4) / 0.6;
                                temp += (noise3D(ix * 0.2, iy * 0.3, iz * 0.25) - 0.5)
                                    * deltaT * 0.4 * turbScale;
                            }
                            break;
                        }
                    }

                    // Wind perturbation
                    const windPert = (noise3D(ix * 0.1 + 7.3, iy * 0.2 + 3.1, iz * 0.15 + 5.7) - 0.5)
                        * windSpeed * 0.5;
                    this.wind[idx] = windSpeed + windPert;
                    temp += windPert * 0.2; // Wind mixes temperature slightly

                    // Store values
                    this.temperature[idx] = temp;
                    const P = this.pressureAtAltitude(altitude);
                    this.pressure[idx] = P;
                    this.humidity[idx] = humidity + (rand() - 0.5) * 10;

                    // Calculate refractive index
                    if (useEdlen) {
                        this.grid[idx] = this.refractiveIndexEdlen(temp, P, this.humidity[idx], wavelengthUm);
                    } else {
                        this.grid[idx] = this.refractiveIndexSimple(temp, P);
                    }

                    // Cn² profile
                    this.Cn2[idx] = this.Cn2HufnagelValley(altitude, windSpeed + Math.abs(windPert), Cn2Base);
                }
            }
        }
    }

    getRefractiveIndex(ix, iy, iz) {
        ix = Math.max(0, Math.min(this.gridX - 1, Math.floor(ix)));
        iy = Math.max(0, Math.min(this.gridY - 1, Math.floor(iy)));
        iz = Math.max(0, Math.min(this.gridZ - 1, Math.floor(iz)));
        return this.grid[this.index(ix, iy, iz)];
    }

    getTemperature(ix, iy, iz) {
        ix = Math.max(0, Math.min(this.gridX - 1, Math.floor(ix)));
        iy = Math.max(0, Math.min(this.gridY - 1, Math.floor(iy)));
        iz = Math.max(0, Math.min(this.gridZ - 1, Math.floor(iz)));
        return this.temperature[this.index(ix, iy, iz)];
    }

    getCn2(ix, iy, iz) {
        ix = Math.max(0, Math.min(this.gridX - 1, Math.floor(ix)));
        iy = Math.max(0, Math.min(this.gridY - 1, Math.floor(iy)));
        iz = Math.max(0, Math.min(this.gridZ - 1, Math.floor(iz)));
        return this.Cn2[this.index(ix, iy, iz)];
    }

    worldToGrid(pos) {
        return {
            ix: Math.floor(pos.x / this.cellSize),
            iy: Math.floor(pos.y / this.cellSize),
            iz: Math.floor(pos.z / this.cellSize)
        };
    }

    inBounds(ix, iy, iz) {
        return ix >= 0 && ix < this.gridX &&
            iy >= 0 && iy < this.gridY &&
            iz >= 0 && iz < this.gridZ;
    }
}

// ── Vectorial Snell's Law ────────────────────────────────────
/**
 * t = (n1/n2) * i + ((n1/n2) * cos(θi) - sqrt(1 - (n1/n2)² * (1 - cos²(θi)))) * n
 */
function vectorialSnellLaw(incident, normal, n1, n2) {
    const i = incident.normalize();
    const n = normal.normalize();
    const ratio = n1 / n2;
    const cosTheta_i = -i.dot(n);

    let adjustedNormal = n;
    let adjustedCos = cosTheta_i;
    if (cosTheta_i < 0) {
        adjustedNormal = n.scale(-1);
        adjustedCos = -cosTheta_i;
    }

    const sin2Theta_i = 1 - adjustedCos * adjustedCos;
    const sin2Theta_t = ratio * ratio * sin2Theta_i;

    if (sin2Theta_t > 1.0) return null; // TIR

    const cosTheta_t = Math.sqrt(1 - sin2Theta_t);
    const t = i.scale(ratio).add(
        adjustedNormal.scale(ratio * adjustedCos - cosTheta_t)
    );

    return {
        direction: t.normalize(),
        cosTheta_i: adjustedCos,
        cosTheta_t: cosTheta_t,
        ratio: ratio,
        sin2Theta_t: sin2Theta_t
    };
}

function verifySnellLaw(n1, n2, cosTheta_i, cosTheta_t) {
    const sinTheta_i = Math.sqrt(1 - cosTheta_i * cosTheta_i);
    const sinTheta_t = Math.sqrt(1 - cosTheta_t * cosTheta_t);
    const lhs = n1 * sinTheta_i;
    const rhs = n2 * sinTheta_t;
    const error = Math.abs(lhs - rhs);
    return { n1_sin_theta_i: lhs, n2_sin_theta_t: rhs, error, valid: error < 1e-10 };
}

// ── Atmospheric Loss Calculator ──────────────────────────────
class AtmosphericLoss {
    /**
     * Beer-Lambert absorption loss
     * P_received = P_transmitted × exp(-α × L)
     * α in Np/m (convert from dB/km: α_Np = α_dB × ln(10)/10 / 1000)
     *
     * @param {number} distance_m - propagation distance in meters
     * @param {string} weather - 'clear', 'haze', 'fog', 'rain'
     * @param {number} wavelengthNm - wavelength in nm
     */
    static absorptionLoss(distance_m, weather = 'clear', wavelengthNm = 1550) {
        // Atmospheric attenuation coefficient (dB/km)
        let alpha_dB_km;
        switch (weather) {
            case 'clear': alpha_dB_km = 0.2; break; // Visibility >10km
            case 'haze': alpha_dB_km = 4.0; break; // Visibility ~4km
            case 'fog': alpha_dB_km = 40.0; break; // Visibility ~0.5km
            case 'rain': alpha_dB_km = 6.0; break; // Moderate rain
            case 'snow': alpha_dB_km = 20.0; break; // Moderate snow
            case 'storm': alpha_dB_km = 62.0; break; // Fırtına: sis+yağmur+kar kombine (~62 dB/km)
            default: alpha_dB_km = 0.2;
        }

        // Kim model for fog/haze/storm (wavelength dependent)
        if (weather === 'haze' || weather === 'fog') {
            // V = visibility in km
            const V = weather === 'fog' ? 0.5 : 4.0;
            const q = wavelengthNm > 500 ? 1.6 : (V > 6 ? 1.3 : (V > 1 ? 0.585 * Math.pow(V, 1 / 3) : 0));
            alpha_dB_km = 3.91 / V * Math.pow(550 / wavelengthNm, q);
        } else if (weather === 'storm') {
            // Kombine: yoğun sis (V≈0.1km) + şiddetli yağmur + kar
            const V = 0.1; // çok düşük görüş mesafesi
            const q = 0; // en kötü Kim model katsayısı
            const fogComponent = 3.91 / V * Math.pow(550 / wavelengthNm, q);
            const rainComponent = 12.0;  // mm/h yoğun yağmur etkisi
            const snowComponent = 20.0;  // kar zayıflaması
            alpha_dB_km = fogComponent + rainComponent + snowComponent;
        }

        const alpha_Np_m = alpha_dB_km * Math.log(10) / 10 / 1000;
        const transmission = Math.exp(-alpha_Np_m * distance_m);
        const loss_dB = -10 * Math.log10(transmission);

        return {
            alpha_dB_km,
            alpha_Np_m,
            transmission,
            loss_dB,
            distance_m,
            weather
        };
    }

    /**
     * Geometric / beam spreading loss
     * For a Gaussian beam: L_geo = (D_receiver / D_beam(L))²
     * D_beam(L) = D0 + θ_div × L
     */
    static geometricLoss(distance_m, beamDivergenceRad = 1e-3, receiverDiameterM = 0.1, beamDiameterM = 0.004) {
        const beamDiamAtReceiver = beamDiameterM + 2 * beamDivergenceRad * distance_m;
        const ratio = receiverDiameterM / beamDiamAtReceiver;
        const capturedFraction = Math.min(1, ratio * ratio);
        const loss_dB = -10 * Math.log10(capturedFraction);

        return {
            beamDiamAtReceiver,
            capturedFraction,
            loss_dB,
            beamSpreadArea: Math.PI * (beamDiamAtReceiver / 2) * (beamDiamAtReceiver / 2)
        };
    }

    /**
     * Scintillation index (Rytov variance)
     * σ²_R = 1.23 × Cn² × k^(7/6) × L^(11/6)
     *
     * Measures intensity fluctuation of the beam.
     * σ²_R < 0.3: weak turbulence
     * σ²_R ~ 1: moderate
     * σ²_R > 1: strong (saturation regime)
     */
    static scintillationIndex(distance_m, Cn2, wavelengthM = 1550e-9) {
        const k = 2 * Math.PI / wavelengthM;
        const sigma2_R = 1.23 * Cn2 * Math.pow(k, 7 / 6) * Math.pow(distance_m, 11 / 6);

        let regime;
        if (sigma2_R < 0.3) regime = 'weak';
        else if (sigma2_R < 1.0) regime = 'moderate';
        else regime = 'strong';

        return {
            sigma2_R,
            sigma_R: Math.sqrt(sigma2_R),
            regime,
            Cn2,
            description: `Rytov σ²=${sigma2_R.toExponential(3)} (${regime})`
        };
    }

    /**
     * Total link budget for FSO
     */
    static linkBudget(params = {}) {
        const {
            distance_m = 6000,
            laserPower_W = 0.01,
            weather = 'clear',
            wavelengthNm = 1550,
            beamDivRad = 1e-3,
            receiverDiamM = 0.1,
            beamDiamM = 0.004,
            Cn2 = 1e-15,
            receiverSensitivity_dBm = -40
        } = params;

        const P_tx_dBm = 10 * Math.log10(laserPower_W * 1000);
        const absorption = this.absorptionLoss(distance_m, weather, wavelengthNm);
        const geometric = this.geometricLoss(distance_m, beamDivRad, receiverDiamM, beamDiamM);
        const scintillation = this.scintillationIndex(distance_m, Cn2, wavelengthNm * 1e-9);

        // Scintillation-induced power penalty (dB)
        const scintPenalty = scintillation.sigma2_R < 5
            ? 5 * Math.log10(1 + scintillation.sigma2_R)
            : 10;

        const totalLoss_dB = absorption.loss_dB + geometric.loss_dB + scintPenalty;
        const P_rx_dBm = P_tx_dBm - totalLoss_dB;
        const linkMargin_dB = P_rx_dBm - receiverSensitivity_dBm;

        return {
            P_tx_dBm,
            P_rx_dBm,
            absorption,
            geometric,
            scintillation,
            scintPenalty_dB: scintPenalty,
            totalLoss_dB,
            linkMargin_dB,
            linkViable: linkMargin_dB > 0,
            P_rx_W: Math.pow(10, P_rx_dBm / 10) / 1000
        };
    }
}

// ── Ray Tracer (6 km optimized) ──────────────────────────────
class RayTracer {
    constructor(atmosphere) {
        this.atmosphere = atmosphere;
        this.maxSteps = 50000; // More steps for 6 km
    }

    trace(origin, direction) {
        const atm = this.atmosphere;
        const cs = atm.cellSize;
        const path = [];
        let pos = origin.clone();
        let dir = direction.normalize();
        let totalDistance = 0;
        let totalInternalReflections = 0;
        let refractionEvents = [];
        let steps = 0;
        let accumulatedCn2 = 0;

        // Downsample path recording for 6 km (every N steps)
        const pathRecordInterval = Math.max(1, Math.floor(atm.gridX / 500));

        let cell = atm.worldToGrid(pos);
        if (!atm.inBounds(cell.ix, cell.iy, cell.iz)) {
            return {
                success: false, reason: 'Origin out of bounds',
                path: [], refractionEvents: [],
                totalDistance: 0, totalInternalReflections: 0,
                avgCn2: 0, steps: 0
            };
        }

        let currentN = atm.getRefractiveIndex(cell.ix, cell.iy, cell.iz);

        path.push({
            position: pos.clone(), gridCell: { ...cell },
            n: currentN, direction: dir.clone(), distance: 0
        });

        while (steps < this.maxSteps) {
            steps++;

            const boundary = this.findNextBoundary(pos, dir, cell);
            if (!boundary) break;

            const newPos = pos.add(dir.scale(boundary.t));
            totalDistance += boundary.t;

            const nextCell = {
                ix: cell.ix + boundary.stepX,
                iy: cell.iy + boundary.stepY,
                iz: cell.iz + boundary.stepZ
            };

            if (!atm.inBounds(nextCell.ix, nextCell.iy, nextCell.iz)) {
                path.push({
                    position: newPos.clone(), gridCell: { ...nextCell },
                    n: currentN, direction: dir.clone(), distance: totalDistance
                });
                break;
            }

            const nextN = atm.getRefractiveIndex(nextCell.ix, nextCell.iy, nextCell.iz);

            // Accumulate Cn² along path
            accumulatedCn2 += atm.getCn2(nextCell.ix, nextCell.iy, nextCell.iz) * boundary.t;

            if (Math.abs(nextN - currentN) > 1e-15) {
                const result = vectorialSnellLaw(dir, boundary.normal, currentN, nextN);

                if (result === null) {
                    totalInternalReflections++;
                    const reflectDir = dir.sub(boundary.normal.scale(2 * dir.dot(boundary.normal)));
                    dir = reflectDir.normalize();

                    refractionEvents.push({
                        position: newPos.clone(),
                        type: 'total_internal_reflection',
                        n1: currentN, n2: nextN, cell: { ...cell }
                    });

                    pos = newPos;
                    continue;
                } else {
                    const oldDir = dir.clone();
                    dir = result.direction;

                    const verification = verifySnellLaw(currentN, nextN, result.cosTheta_i, result.cosTheta_t);

                    // Only record significant refraction events
                    if (refractionEvents.length < 1000) {
                        refractionEvents.push({
                            position: newPos.clone(),
                            type: 'refraction',
                            n1: currentN, n2: nextN,
                            incidentDir: oldDir, refractedDir: dir.clone(),
                            cosTheta_i: result.cosTheta_i,
                            cosTheta_t: result.cosTheta_t,
                            verification, cell: { ...nextCell }
                        });
                    }

                    currentN = nextN;
                }
            } else {
                currentN = nextN;
            }

            pos = newPos;
            cell = nextCell;

            // Record path at intervals
            if (steps % pathRecordInterval === 0) {
                path.push({
                    position: pos.clone(), gridCell: { ...cell },
                    n: currentN, direction: dir.clone(), distance: totalDistance
                });
            }
        }

        const lastPos = path[path.length - 1];
        const reachedEnd = lastPos && lastPos.gridCell.ix >= atm.gridX;

        // Path-averaged Cn²
        const avgCn2 = totalDistance > 0 ? accumulatedCn2 / totalDistance : 0;

        return {
            success: reachedEnd,
            path, refractionEvents,
            totalDistance, totalInternalReflections,
            steps, avgCn2,
            exitPosition: lastPos ? lastPos.position : null,
            exitDirection: lastPos ? lastPos.direction : null
        };
    }

    findNextBoundary(pos, dir, cell) {
        const cs = this.atmosphere.cellSize;
        const eps = 1e-10;

        const xMin = cell.ix * cs, xMax = (cell.ix + 1) * cs;
        const yMin = cell.iy * cs, yMax = (cell.iy + 1) * cs;
        const zMin = cell.iz * cs, zMax = (cell.iz + 1) * cs;

        let tMin = Infinity;
        let normal = null;
        let stepX = 0, stepY = 0, stepZ = 0;

        if (Math.abs(dir.x) > eps) {
            const tX = dir.x > 0 ? (xMax - pos.x) / dir.x : (xMin - pos.x) / dir.x;
            if (tX > eps && tX < tMin) {
                tMin = tX;
                normal = dir.x > 0 ? new Vec3(-1, 0, 0) : new Vec3(1, 0, 0);
                stepX = dir.x > 0 ? 1 : -1; stepY = 0; stepZ = 0;
            }
        }
        if (Math.abs(dir.y) > eps) {
            const tY = dir.y > 0 ? (yMax - pos.y) / dir.y : (yMin - pos.y) / dir.y;
            if (tY > eps && tY < tMin) {
                tMin = tY;
                normal = dir.y > 0 ? new Vec3(0, -1, 0) : new Vec3(0, 1, 0);
                stepX = 0; stepY = dir.y > 0 ? 1 : -1; stepZ = 0;
            }
        }
        if (Math.abs(dir.z) > eps) {
            const tZ = dir.z > 0 ? (zMax - pos.z) / dir.z : (zMin - pos.z) / dir.z;
            if (tZ > eps && tZ < tMin) {
                tMin = tZ;
                normal = dir.z > 0 ? new Vec3(0, 0, -1) : new Vec3(0, 0, 1);
                stepX = 0; stepY = 0; stepZ = dir.z > 0 ? 1 : -1;
            }
        }

        if (tMin === Infinity) return null;
        return { t: tMin + eps, normal, stepX, stepY, stepZ };
    }
}

// ── Data Transmission Simulator (6 km) ───────────────────────
class DataTransmissionSimulator {
    constructor(rayTracer, sourcePos, targetPos, receiverRadius = 0.5) {
        this.rayTracer = rayTracer;
        this.sourcePos = sourcePos;
        this.targetPos = targetPos;
        this.receiverRadius = receiverRadius;
        this.beamDivergence = 0.001;
    }

    sendBit(bit) {
        if (bit === 0) {
            return { sent: 0, received: 0, correct: true, trace: null, signalPower: 0 };
        }

        const dir = this.targetPos.sub(this.sourcePos).normalize();
        const trace = this.rayTracer.trace(this.sourcePos, dir);
        const distance = this.sourcePos.sub(this.targetPos).length();

        // Calculate atmospheric losses
        const linkBudget = AtmosphericLoss.linkBudget({
            distance_m: distance,
            Cn2: trace.avgCn2 || 1e-15
        });

        let received = 0;
        let lateralDeviation = Infinity;
        if (trace.exitPosition) {
            lateralDeviation = Math.sqrt(
                Math.pow(trace.exitPosition.y - this.targetPos.y, 2) +
                Math.pow(trace.exitPosition.z - this.targetPos.z, 2)
            );
            if (lateralDeviation <= this.receiverRadius && linkBudget.linkViable) {
                received = 1;
            }
        }

        return {
            sent: 1, received, correct: received === 1,
            trace, lateralDeviation,
            signalPower: linkBudget.P_rx_dBm,
            linkBudget
        };
    }

    sendPacket(data) {
        const results = [];
        let correctBits = 0, totalOnes = 0, totalZeros = 0, missedOnes = 0;

        for (let i = 0; i < data.length; i++) {
            const result = this.sendBit(data[i]);
            results.push(result);
            if (result.correct) correctBits++;
            if (data[i] === 1) { totalOnes++; if (!result.correct) missedOnes++; }
            else totalZeros++;
        }

        return {
            data, results,
            totalBits: data.length, correctBits,
            bitErrorRate: 1 - (correctBits / data.length),
            totalOnes, totalZeros, missedOnes,
            successRate: (correctBits / data.length) * 100
        };
    }

    static generateTestData(pattern, length = 32) {
        const data = [];
        switch (pattern) {
            case 'alternating':
                for (let i = 0; i < length; i++) data.push(i % 2);
                break;
            case 'all_ones':
                for (let i = 0; i < length; i++) data.push(1);
                break;
            case 'all_zeros':
                for (let i = 0; i < length; i++) data.push(0);
                break;
            case 'random':
                for (let i = 0; i < length; i++) data.push(Math.random() > 0.5 ? 1 : 0);
                break;
            case 'burst':
                for (let i = 0; i < length; i++) data.push(Math.floor(i / 8) % 2 === 0 ? 1 : 0);
                break;
            case 'ascii_hello':
                return [0, 1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 1, 1, 1, 1];
            case 'prbs7':
                // Pseudo-random binary sequence (PRBS-7)
                {
                    let lfsr = 0x7F;
                    for (let i = 0; i < length; i++) {
                        const bit = lfsr & 1;
                        data.push(bit);
                        const fb = ((lfsr >> 0) ^ (lfsr >> 1)) & 1;
                        lfsr = (lfsr >> 1) | (fb << 6);
                    }
                }
                break;
            default:
                for (let i = 0; i < length; i++) data.push(Math.random() > 0.5 ? 1 : 0);
        }
        return data;
    }
}

// ── Lens System ──────────────────────────────────────────────
/**
 * Optical lens system for FSO links.
 * Models 5 lens types and their effects on beam divergence,
 * aperture, and power transmission.
 */
class LensSystem {
    /**
     * Available lens types with their optical characteristics.
     * divergenceReduction: fraction of divergence reduction (0=none, 0.9=90% reduced)
     * apertureBoost: multiplier on effective receiving aperture
     * transmittance: fraction of power passed through lens (0–1)
     * description: human-readable description
     */
    static LENS_TYPES = {
        none: {
            name: 'Lens Yok',
            divergenceReduction: 0,
            apertureBoost: 1.0,
            transmittance: 1.0,
            focalLength_m: null,
            description: 'Lens sistemi yok – ham ışın'
        },
        collimating: {
            name: 'Kollimating Lens',
            divergenceReduction: 0.85,
            apertureBoost: 1.2,
            transmittance: 0.96,
            focalLength_m: 0.05,   // 50 mm default focal length
            description: 'Işını paralel hale getirir, diverjansı minimize eder'
        },
        focusing: {
            name: 'Odaklayıcı Lens',
            divergenceReduction: 0.70,
            apertureBoost: 1.8,
            transmittance: 0.94,
            focalLength_m: 0.10,   // 100 mm
            description: 'Alıcı odak noktasına odaklar, spot boyutunu küçültür'
        },
        galilean_telescope: {
            name: 'Galilean Teleskop',
            divergenceReduction: 0.92,
            apertureBoost: 2.5,
            transmittance: 0.90,
            focalLength_m: 0.20,   // 200 mm
            description: 'Işın genişletici, uzun mesafe için optimum'
        },
        cassegrain: {
            name: 'Cassegrain Teleskop',
            divergenceReduction: 0.95,
            apertureBoost: 4.0,
            transmittance: 0.88,
            focalLength_m: 0.50,   // 500 mm
            description: 'Büyük açıklık, en yüksek kazanç, profesyonel FSO'
        },
        diverging: {
            name: 'Iraksak Lens',
            divergenceReduction: -0.5,  // negative = increases divergence
            apertureBoost: 0.8,
            transmittance: 0.92,
            focalLength_m: -0.08,  // negative = diverging
            description: 'Test amaçlı ıraksak lens – performans düşürür'
        }
    };

    /**
     * @param {string} lensType - key from LENS_TYPES
     * @param {number} customFocalLength_m - override focal length (optional)
     * @param {number} customAperture_m - override aperture diameter (optional)
     */
    constructor(lensType = 'none', customFocalLength_m = null, customAperture_m = null) {
        this.lensType = lensType;
        const spec = LensSystem.LENS_TYPES[lensType] || LensSystem.LENS_TYPES.none;
        this.spec = spec;
        this.focalLength_m = customFocalLength_m ?? spec.focalLength_m;
        this.aperture_m = customAperture_m ?? 0.05; // 50 mm default
    }

    /**
     * Apply lens effects to beam parameters.
     * Returns modified beam divergence and transmittance factor.
     * @param {object} beamParams - { divergenceRad, beamDiamM, receiverDiamM }
     * @returns {object} modified params
     */
    applyToBeam(beamParams) {
        const { divergenceRad = 1e-3, beamDiamM = 0.004, receiverDiamM = 0.1 } = beamParams;
        const spec = this.spec;

        // Effective divergence after lens
        let effectiveDivergence;
        if (spec.divergenceReduction >= 0) {
            effectiveDivergence = divergenceRad * (1 - spec.divergenceReduction);
        } else {
            // Diverging lens increases divergence
            effectiveDivergence = divergenceRad * (1 + Math.abs(spec.divergenceReduction));
        }
        effectiveDivergence = Math.max(effectiveDivergence, 1e-6); // physical minimum

        // Effective receiver aperture boosted by lens collection
        const effectiveReceiverDiam = receiverDiamM * spec.apertureBoost;

        return {
            divergenceRad: effectiveDivergence,
            beamDiamM,
            receiverDiamM: effectiveReceiverDiam,
            transmittance: spec.transmittance
        };
    }

    /**
     * Calculate the dB gain/loss introduced by this lens system.
     * @param {number} distance_m
     * @param {number} baseDivRad
     * @param {number} baseReceiverDiam_m
     * @param {number} baseBeamDiam_m
     * @returns {number} gain in dB (positive = improvement)
     */
    getLinkBudgetGain_dB(distance_m, baseDivRad = 1e-3, baseReceiverDiam_m = 0.1, baseBeamDiam_m = 0.004) {
        const withLens = this.applyToBeam({
            divergenceRad: baseDivRad,
            beamDiamM: baseBeamDiam_m,
            receiverDiamM: baseReceiverDiam_m
        });

        const geoBase = AtmosphericLoss.geometricLoss(distance_m, baseDivRad, baseReceiverDiam_m, baseBeamDiam_m);
        const geoLens = AtmosphericLoss.geometricLoss(distance_m, withLens.divergenceRad, withLens.receiverDiamM, baseBeamDiam_m);

        const lensTxLoss_dB = -10 * Math.log10(withLens.transmittance);
        const geometricGain_dB = geoBase.loss_dB - geoLens.loss_dB;

        return geometricGain_dB - lensTxLoss_dB; // net gain
    }

    toString() {
        return `LensSystem(${this.lensType}, f=${this.focalLength_m}m)`;
    }
}

// ── Environment Presets ───────────────────────────────────────
/**
 * Real-world environment presets for FSO link simulation.
 * Each preset configures atmosphere, weather, turbulence, and temperature.
 */
class EnvironmentPresets {
    static PRESETS = {
        urban: {
            name: '🏙️ Şehir İçi (Urban)',
            thermalProfile: 'hotspot',
            weather: 'haze',
            Cn2Level: 'moderate',
            baseTemp: 28,
            deltaT: 20,
            windSpeed: 3,
            humidity: 60,
            description: 'Şehir merkezi, ısı adaları, orta türbülans, hafif pus'
        },
        maritime: {
            name: '🌊 Deniz / Kıyı (Maritime)',
            thermalProfile: 'coastal',
            weather: 'haze',
            Cn2Level: 'weak',
            baseTemp: 18,
            deltaT: 8,
            windSpeed: 12,
            humidity: 85,
            description: 'Deniz-kara geçişi, yüksek nem, güçlü rüzgar, zayıf türbülans'
        },
        desert: {
            name: '🏜️ Çöl / Arid (Desert)',
            thermalProfile: 'hotspot',
            weather: 'clear',
            Cn2Level: 'strong',
            baseTemp: 42,
            deltaT: 35,
            windSpeed: 8,
            humidity: 10,
            description: 'Aşırı sıcaklık gradyanı, güçlü termal türbülans, berrak hava'
        },
        mountain: {
            name: '⛰️ Dağlık (Mountain)',
            thermalProfile: 'layered',
            weather: 'clear',
            Cn2Level: 'weak',
            baseTemp: 8,
            deltaT: 12,
            windSpeed: 15,
            humidity: 45,
            description: 'Yüksek irtifa, katmanlı atmosfer, berrak hava, düşük türbülans'
        },
        industrial: {
            name: '🏭 Endüstriyel (Industrial)',
            thermalProfile: 'turbulent',
            weather: 'fog',
            Cn2Level: 'strong',
            baseTemp: 32,
            deltaT: 25,
            windSpeed: 5,
            humidity: 75,
            description: 'Fabrika sahası, yoğun türbülans, sanayi sisi, yüksek kayıp'
        },
        arctic: {
            name: '❄️ Arktik (Arctic)',
            thermalProfile: 'uniform',
            weather: 'snow',
            Cn2Level: 'weak',
            baseTemp: -15,
            deltaT: 5,
            windSpeed: 20,
            humidity: 80,
            description: 'Kutup bölgesi, kar yağışı, düşük türbülans, çok soğuk'
        },
        storm: {
            name: '⛈️ Fırtına (Storm)',
            thermalProfile: 'turbulent',
            weather: 'storm',
            Cn2Level: 'strong',
            baseTemp: 10,
            deltaT: 8,
            windSpeed: 28,
            humidity: 98,
            description: 'Yağmurlu + sisli + karlı + şiddetli rüzgar · En kötü FSO koşulu'
        }
    };

    /**
     * Get preset parameters ready for use with AtmosphereGrid.applyThermalProfile
     * @param {string} presetKey
     * @returns {object} preset config
     */
    static getPreset(presetKey) {
        return EnvironmentPresets.PRESETS[presetKey] || EnvironmentPresets.PRESETS.urban;
    }

    /**
     * Get all preset keys
     * @returns {string[]}
     */
    static getPresetKeys() {
        return Object.keys(EnvironmentPresets.PRESETS);
    }
}

// ── Comprehensive Transmission Test ─────────────────────────
/**
 * Tests all environment × lens combinations and returns success rates.
 * This is the "how much data gets through" benchmark.
 */
class ComprehensiveTransmissionTest {
    /**
     * Run all combinations synchronously (JS engine, no backend needed)
     * @param {object} baseParams - { gridX, gridY, gridZ, cellSize, wavelengthNm, numBits, pattern }
     * @returns {object} results matrix
     */
    static runAll(baseParams = {}) {
        const {
            gridX = 30,  // smaller grid for speed
            gridY = 6,
            gridZ = 6,
            cellSize = 200,
            wavelengthNm = 1550,
            numBits = 32,
            pattern = 'alternating',
            laserPower_mW = 10,
            receiverDiamM = 0.10,
            beamDivRad = 1e-3
        } = baseParams;

        const environments = EnvironmentPresets.getPresetKeys();
        const lensTypes = Object.keys(LensSystem.LENS_TYPES);
        const results = [];

        for (const envKey of environments) {
            const envPreset = EnvironmentPresets.getPreset(envKey);
            const { FSOEngine } = window;

            // Build atmosphere
            const atmosphere = new AtmosphereGrid(gridX, gridY, gridZ, cellSize);
            atmosphere.applyThermalProfile(envPreset.thermalProfile, {
                baseTemp: envPreset.baseTemp,
                deltaT: envPreset.deltaT,
                humidity: envPreset.humidity,
                windSpeed: envPreset.windSpeed,
                Cn2Level: envPreset.Cn2Level,
                wavelengthUm: wavelengthNm / 1000,
                useEdlen: true
            });

            const rayTracer = new RayTracer(atmosphere);
            const sourcePos = new Vec3(0.5 * cellSize, gridY * cellSize * 0.5, gridZ * cellSize * 0.5);
            const targetPos = new Vec3((gridX - 0.5) * cellSize, gridY * cellSize * 0.5, gridZ * cellSize * 0.5);

            for (const lensKey of lensTypes) {
                const lens = new LensSystem(lensKey);
                const lensedBeam = lens.applyToBeam({
                    divergenceRad: beamDivRad,
                    beamDiamM: 0.004,
                    receiverDiamM
                });
                const effectiveRecvR = lensedBeam.receiverDiamM / 2;

                const simulator = new DataTransmissionSimulator(rayTracer, sourcePos, targetPos, effectiveRecvR);

                // Generate test data
                const data = DataTransmissionSimulator.generateTestData(pattern, numBits);
                const distance = sourcePos.sub(targetPos).length();

                let correctBits = 0;
                for (const bit of data) {
                    if (bit === 0) { correctBits++; continue; }
                    const dir = targetPos.sub(sourcePos).normalize();
                    const trace = rayTracer.trace(sourcePos, dir);
                    const linkBudget = AtmosphericLoss.linkBudget({
                        distance_m: distance,
                        laserPower_W: laserPower_mW / 1000,
                        weather: envPreset.weather,
                        wavelengthNm,
                        beamDivRad: lensedBeam.divergenceRad,
                        receiverDiamM: lensedBeam.receiverDiamM,
                        beamDiamM: 0.004,
                        Cn2: trace.avgCn2 || 1e-15
                    });
                    // Apply lens transmittance power penalty
                    const effectivePrx = linkBudget.P_rx_dBm + 10 * Math.log10(lensedBeam.transmittance);
                    const effectiveLinkViable = effectivePrx > -40; // receiver sensitivity

                    let received = 0;
                    if (trace.exitPosition) {
                        const latDev = Math.sqrt(
                            Math.pow(trace.exitPosition.y - targetPos.y, 2) +
                            Math.pow(trace.exitPosition.z - targetPos.z, 2)
                        );
                        if (latDev <= effectiveRecvR && effectiveLinkViable) received = 1;
                    }
                    if (received === 1) correctBits++;
                }

                const successRate = (correctBits / numBits) * 100;
                const lensGain = lens.getLinkBudgetGain_dB(distance, beamDivRad, receiverDiamM, 0.004);

                results.push({
                    environment: envKey,
                    envName: envPreset.name,
                    lens: lensKey,
                    lensName: LensSystem.LENS_TYPES[lensKey].name,
                    successRate: Math.round(successRate * 10) / 10,
                    correctBits,
                    totalBits: numBits,
                    lensGain_dB: Math.round(lensGain * 100) / 100,
                    weather: envPreset.weather,
                    Cn2Level: envPreset.Cn2Level
                });
            }
        }

        return results;
    }
}

// ── Exports ──────────────────────────────────────────────────
window.FSOEngine = {
    Vec3, AtmosphereGrid,
    vectorialSnellLaw, verifySnellLaw,
    RayTracer, DataTransmissionSimulator,
    AtmosphericLoss, ATM_CONSTANTS,
    LensSystem, EnvironmentPresets, ComprehensiveTransmissionTest
};
