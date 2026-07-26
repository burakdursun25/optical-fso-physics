/**
 * ============================================================
 *  FSO Physics Engine v2.0 – Application Controller
 *  ─────────────────────────────────────────────────
 *  6 km Range + COMSOL Multiphysics Integration
 * ============================================================
 */

document.addEventListener('DOMContentLoaded', () => {
    const { Vec3, AtmosphereGrid, vectorialSnellLaw, verifySnellLaw,
            RayTracer, DataTransmissionSimulator,
            AtmosphericLoss, ATM_CONSTANTS,
            LensSystem, EnvironmentPresets, ComprehensiveTransmissionTest } = window.FSOEngine;

    // ── DOM Elements ──────────────────────────────────────────
    const topCanvas    = document.getElementById('canvas-top');
    const sideCanvas   = document.getElementById('canvas-side');
    const consoleBody  = document.getElementById('console-body');

    // Controls
    const gridXSlider       = document.getElementById('grid-x');
    const gridZSlider       = document.getElementById('grid-z');
    const cellSizeSlider    = document.getElementById('cell-size');
    const thermalSelect     = document.getElementById('thermal-profile');
    const baseTempSlider    = document.getElementById('base-temp');
    const deltaTSlider      = document.getElementById('delta-t');
    const weatherSelect     = document.getElementById('weather-condition');
    const cn2Select         = document.getElementById('cn2-level');
    const windSpeedSlider   = document.getElementById('wind-speed');
    const wavelengthSlider  = document.getElementById('wavelength');
    const laserPowerSlider  = document.getElementById('laser-power');
    const receiverDiamSlider= document.getElementById('receiver-diameter');
    const beamDivSlider     = document.getElementById('beam-div');
    const dataPatternSelect = document.getElementById('data-pattern');
    const dataBitsSlider    = document.getElementById('data-bits');

    // Value displays
    const gridXVal       = document.getElementById('grid-x-val');
    const gridZVal       = document.getElementById('grid-z-val');
    const cellSizeVal    = document.getElementById('cell-size-val');
    const baseTempVal    = document.getElementById('base-temp-val');
    const deltaTVal      = document.getElementById('delta-t-val');
    const windSpeedVal   = document.getElementById('wind-speed-val');
    const wavelengthVal  = document.getElementById('wavelength-val');
    const laserPowerVal  = document.getElementById('laser-power-val');
    const receiverDiamVal= document.getElementById('receiver-diameter-val');
    const beamDivVal     = document.getElementById('beam-div-val');
    const dataBitsVal    = document.getElementById('data-bits-val');
    const totalDistVal   = document.getElementById('total-distance-val');

    // Result cards – link budget
    const resultLinkMargin = document.getElementById('result-link-margin');
    const linkMarginDetail = document.getElementById('link-margin-detail');
    const resultRxPower    = document.getElementById('result-rx-power');
    const rxPowerDetail    = document.getElementById('rx-power-detail');
    const resultAtmLoss    = document.getElementById('result-atm-loss');
    const atmLossDetail    = document.getElementById('atm-loss-detail');
    const resultScint      = document.getElementById('result-scint');
    const scintDetail      = document.getElementById('scint-detail');

    // Result cards – ray trace
    const resultBER       = document.getElementById('result-ber');
    const resultDeviation = document.getElementById('result-deviation');
    const resultRefract   = document.getElementById('result-refract');
    const resultTIR       = document.getElementById('result-tir');
    const resultDistance  = document.getElementById('result-distance');
    const resultSnell     = document.getElementById('result-snell');
    const berDetail       = document.getElementById('ber-detail');
    const devDetail       = document.getElementById('dev-detail');
    const refractDetail   = document.getElementById('refract-detail');
    const tirDetail       = document.getElementById('tir-detail');

    // Bit display
    const sentBitsEl = document.getElementById('sent-bits');
    const recvBitsEl = document.getElementById('recv-bits');

    // Buttons
    const btnRunSim   = document.getElementById('btn-run-sim');
    const btnRunTest  = document.getElementById('btn-run-test');
    const btnSendData = document.getElementById('btn-send-data');

    // COMSOL buttons
    const btnComsolJava    = document.getElementById('btn-comsol-java');
    const btnComsolMatlab  = document.getElementById('btn-comsol-matlab');
    const btnComsolCsv     = document.getElementById('btn-comsol-csv');
    const btnComsolRaypath = document.getElementById('btn-comsol-raypath');
    const btnComsolParams  = document.getElementById('btn-comsol-params');

    // Tables
    const verifyTableBody = document.getElementById('verify-table-body');
    const linkBudgetBody  = document.getElementById('link-budget-body');

    // Progress
    const progressFill = document.getElementById('progress-fill');

    // ── State ─────────────────────────────────────────────────
    let atmosphere, rayTracer, renderer, transmitter, comsolBridge;
    let currentTrace = null;
    let currentSourcePos = null;
    let currentTargetPos = null;
    let animFrameId = null;
    let usePythonBackend = false;
    const BACKEND_URL = 'http://localhost:8000';

    comsolBridge = new COMSOLBridge();

    async function checkBackendStatus() {
        const statusEl = document.getElementById('backend-status');
        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 1000);
            const res = await fetch(`${BACKEND_URL}/api/health`, { signal: controller.signal });
            clearTimeout(timeoutId);
            const data = await res.json();
            if (data.status === 'ok' && data.backend === 'python') {
                usePythonBackend = true;
                statusEl.style.background = 'rgba(16, 185, 129, 0.08)';
                statusEl.style.borderColor = 'rgba(16, 185, 129, 0.2)';
                statusEl.style.color = 'var(--accent-emerald)';
                statusEl.innerHTML = '<span class="dot" style="background: var(--accent-emerald)"></span>Backend: Python (FastAPI)';
                log('info', 'Python backend tespit edildi. Fizik hesaplamaları Python üzerinde yürütülecek.');
                return;
            }
        } catch (e) {
            // fallback to JS
        }
        usePythonBackend = false;
        statusEl.style.background = 'rgba(244, 63, 94, 0.08)';
        statusEl.style.borderColor = 'rgba(244, 63, 94, 0.2)';
        statusEl.style.color = 'var(--accent-rose)';
        statusEl.innerHTML = '<span class="dot" style="background: var(--accent-rose); animation: none;"></span>Backend: JS Fallback';
        log('warn', 'Python backend aktif değil. Simülasyon tarayıcı üzerinde (JS Fallback) çalışıyor.');
    }

    // ── Logger ────────────────────────────────────────────────
    const logLines = [];
    function log(type, msg) {
        const now = new Date();
        const time = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`;
        logLines.push({ time, type, msg });
        if (logLines.length > 300) logLines.shift();

        const line = document.createElement('div');
        line.className = 'log-line';
        line.innerHTML = `
            <span class="log-time">${time}</span>
            <span class="log-type ${type}">[${type.toUpperCase()}]</span>
            <span class="log-msg">${msg}</span>
        `;
        consoleBody.appendChild(line);
        consoleBody.scrollTop = consoleBody.scrollHeight;
    }

    // ── Slider bindings ───────────────────────────────────────
    function bindSlider(slider, valEl, suffix = '', transform = null) {
        slider.addEventListener('input', () => {
            valEl.textContent = (transform ? transform(slider.value) : slider.value) + suffix;
            updateTotalDistance();
        });
    }

    bindSlider(gridXSlider, gridXVal);
    bindSlider(gridZSlider, gridZVal);
    bindSlider(cellSizeSlider, cellSizeVal, 'm');
    bindSlider(baseTempSlider, baseTempVal, '°C');
    bindSlider(deltaTSlider, deltaTVal, '°C');
    bindSlider(windSpeedSlider, windSpeedVal, ' m/s');
    bindSlider(wavelengthSlider, wavelengthVal, ' nm');
    bindSlider(laserPowerSlider, laserPowerVal, ' mW');
    bindSlider(receiverDiamSlider, receiverDiamVal, ' m');
    bindSlider(beamDivSlider, beamDivVal, ' mrad');
    bindSlider(dataBitsSlider, dataBitsVal, ' bit');

    function updateTotalDistance() {
        const totalM = parseInt(gridXSlider.value) * parseFloat(cellSizeSlider.value);
        totalDistVal.textContent = (totalM / 1000).toFixed(1) + ' km';
    }
    updateTotalDistance();

    // ── Get current params ───────────────────────────────────────────
    function getParams() {
        const lensTypeEl = document.getElementById('lens-type');
        return {
            gridX: parseInt(gridXSlider.value),
            gridY: 10,
            gridZ: parseInt(gridZSlider.value),
            cellSize: parseFloat(cellSizeSlider.value),
            thermalProfile: thermalSelect.value,
            baseTemp: parseFloat(baseTempSlider.value),
            deltaT: parseFloat(deltaTSlider.value),
            weather: weatherSelect.value,
            Cn2Level: cn2Select.value,
            windSpeed: parseFloat(windSpeedSlider.value),
            wavelengthNm: parseInt(wavelengthSlider.value),
            wavelengthUm: parseInt(wavelengthSlider.value) / 1000,
            laserPower_mW: parseFloat(laserPowerSlider.value),
            laserPower_W: parseFloat(laserPowerSlider.value) / 1000,
            receiverDiamM: parseFloat(receiverDiamSlider.value),
            beamDivRad: parseFloat(beamDivSlider.value) / 1000,
            lensType: lensTypeEl ? lensTypeEl.value : 'none',
        };
    }

    // ── Lens UI ───────────────────────────────────────────────
    const lensTypeEl = document.getElementById('lens-type');
    const lensInfoBox = document.getElementById('lens-info-box');

    function updateLensInfo(lensKey) {
        const spec = LensSystem.LENS_TYPES[lensKey];
        if (!spec || lensKey === 'none') {
            lensInfoBox.style.display = 'none';
            return;
        }
        lensInfoBox.style.display = 'block';
        document.getElementById('lens-div-reduction').textContent =
            spec.divergenceReduction >= 0
            ? `${(spec.divergenceReduction * 100).toFixed(0)}% azaltılır`
            : `${(Math.abs(spec.divergenceReduction) * 100).toFixed(0)}% artırılır`;
        document.getElementById('lens-aperture-boost').textContent = `×${spec.apertureBoost.toFixed(1)}`;
        document.getElementById('lens-transmittance').textContent = `${(spec.transmittance * 100).toFixed(0)}%`;
        document.getElementById('lens-description').textContent = spec.description;
    }

    if (lensTypeEl) {
        lensTypeEl.addEventListener('change', () => {
            updateLensInfo(lensTypeEl.value);
            const p = getParams();
            const lens = new LensSystem(lensTypeEl.value);
            const distance = p.gridX * p.cellSize;
            const gain = lens.getLinkBudgetGain_dB(distance, p.beamDivRad, p.receiverDiamM, 0.004);
            log('info', `🔭 Lens değiştirildi: ${LensSystem.LENS_TYPES[lensTypeEl.value].name} | Kazanç: ${gain >= 0 ? '+' : ''}${gain.toFixed(2)} dB @ ${(distance/1000).toFixed(1)} km`);
        });
    }

    // ── Environment Preset Buttons ───────────────────────────────
    const presetButtons = document.querySelectorAll('.env-preset-btn');
    const presetBadgeEl = document.getElementById('active-preset-badge');

    presetButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const presetKey = btn.dataset.preset;
            const preset = EnvironmentPresets.getPreset(presetKey);

            // Update all relevant sliders/selects
            thermalSelect.value = preset.thermalProfile;
            document.getElementById('base-temp').value = preset.baseTemp;
            document.getElementById('base-temp-val').textContent = preset.baseTemp + '°C';
            document.getElementById('delta-t').value = preset.deltaT;
            document.getElementById('delta-t-val').textContent = preset.deltaT + '°C';
            weatherSelect.value = preset.weather;
            cn2Select.value = preset.Cn2Level;
            document.getElementById('wind-speed').value = preset.windSpeed;
            document.getElementById('wind-speed-val').textContent = preset.windSpeed + ' m/s';

            // Highlight active button
            presetButtons.forEach(b => b.style.borderColor = '');
            btn.style.borderColor = 'var(--accent-cyan)';
            btn.style.color = 'var(--accent-cyan)';

            // Update badge
            if (presetBadgeEl) {
                presetBadgeEl.textContent = `Aktif: ${preset.name}`;
                presetBadgeEl.style.color = 'var(--accent-cyan)';
            }

            log('success', `🌍 Ortam Preset: ${preset.name} | ${preset.description}`);
        });
    });

    // ── Comprehensive Transmission Test ─────────────────────────
    const btnComprehensiveTest = document.getElementById('btn-comprehensive-test');

    function renderComprehensiveTestResults(results) {
        const tbody = document.getElementById('transmission-test-body');
        const table = document.getElementById('transmission-test-table');
        const summaryCards = document.getElementById('test-summary-cards');

        if (!tbody || !table) return;
        tbody.innerHTML = '';

        if (!results || results.length === 0) return;

        // Sort by successRate descending
        const sorted = [...results].sort((a, b) => b.successRate - a.successRate);
        const rates = results.map(r => r.successRate);
        const avgRate = (rates.reduce((s, x) => s + x, 0) / rates.length).toFixed(1);
        const best = sorted[0];
        const worst = sorted[sorted.length - 1];

        // Summary cards
        if (summaryCards) {
            summaryCards.style.display = 'grid';
            document.getElementById('test-avg-rate').textContent = avgRate + '%';
            document.getElementById('test-best').textContent =
                `${best.successRate}% \u2013 ${best.envName} + ${best.lensName}`;
            document.getElementById('test-worst').textContent =
                `${worst.successRate}% \u2013 ${worst.envName} + ${worst.lensName}`;
        }

        // Table rows
        const weatherEmoji = { clear: '☀️', haze: '🌫️', fog: '🌨️', rain: '🌧️', snow: '❄️' };
        const cn2Color = { weak: 'var(--accent-emerald)', moderate: 'var(--accent-amber)', strong: 'var(--accent-rose)' };

        sorted.forEach(r => {
            const rate = r.successRate;
            const color = rate >= 80 ? 'var(--accent-emerald)'
                        : rate >= 50 ? 'var(--accent-amber)'
                        : 'var(--accent-rose)';
            const bar = `<div style="width:${rate}%; height:3px; background:${color}; border-radius:2px; margin-top:2px;"></div>`;
            const gainStr = r.lensGain_dB >= 0 ? `+${r.lensGain_dB}` : `${r.lensGain_dB}`;
            const prx = r.P_rx_dBm !== undefined ? r.P_rx_dBm.toFixed(1) : '—';
            const viable = r.linkViable !== undefined ? (r.linkViable ? '✅' : '❌') : '—';

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td style="font-size:0.7rem">${r.envName}</td>
                <td style="font-size:0.7rem">${r.lensName}</td>
                <td style="color:${color}; font-weight:700">${rate}%${bar}</td>
                <td>${r.correctBits}/${r.totalBits}</td>
                <td style="color:${r.lensGain_dB >= 0 ? 'var(--accent-emerald)' : 'var(--accent-rose)'}">${gainStr} dB</td>
                <td>${weatherEmoji[r.weather] || r.weather}</td>
                <td style="color:${cn2Color[r.Cn2Level] || 'inherit'}">${r.Cn2Level}</td>
                <td>${prx} dBm</td>
                <td>${viable}</td>
            `;
            tbody.appendChild(tr);
        });

        table.style.display = 'table';
        log('success', `🔬 Kapsamlı Test Tamamlandı | Ortalama Başarı: ${avgRate}% | En İyi: ${best.successRate}% (${best.environment}/${best.lens}) | En Kötü: ${worst.successRate}% (${worst.environment}/${worst.lens})`);
    }

    if (btnComprehensiveTest) {
        btnComprehensiveTest.addEventListener('click', async () => {
            const statusEl = document.getElementById('comprehensive-test-status');
            btnComprehensiveTest.disabled = true;
            btnComprehensiveTest.textContent = '⏳ Hesaplanıyor...';
            if (statusEl) statusEl.textContent = 'Tüm ortam × lens kombinasyonları test ediliyor...';

            log('info', '🔬 Kapsamlı veri aktarım testi başlatılıyor (6 ortam × 6 lens = 36 kombinasyon)...');

            const p = getParams();
            const testParams = {
                gridX: 20,
                gridY: 6,
                gridZ: 6,
                cellSize: p.cellSize || 300,
                wavelengthNm: p.wavelengthNm,
                numBits: parseInt(document.getElementById('data-bits').value) || 32,
                pattern: document.getElementById('data-pattern').value || 'alternating',
                laserPower_mW: p.laserPower_mW,
                receiverDiamM: p.receiverDiamM,
                beamDivRad: p.beamDivRad
            };

            // Try backend first, fallback to JS
            let results = null;
            if (usePythonBackend) {
                try {
                    const res = await fetch(`${BACKEND_URL}/api/test-transmission`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(testParams)
                    });
                    const data = await res.json();
                    results = data.results;
                    log('info', `[Python Backend] ${data.summary.totalCombinations} kombinasyon test edildi`);
                } catch (e) {
                    log('warn', `Backend test hatası: ${e.message}. JS Fallback kullanılıyor.`);
                }
            }

            if (!results) {
                // JS fallback – run synchronously
                results = ComprehensiveTransmissionTest.runAll(testParams);
                log('info', `[JS Fallback] ${results.length} kombinasyon hesaplandı`);
            }

            renderComprehensiveTestResults(results);

            btnComprehensiveTest.disabled = false;
            btnComprehensiveTest.textContent = '▶ Tüm Kombinasyonları Test Et';
            if (statusEl) statusEl.textContent = `${results.length} kombinasyon tamamlandı.`;
        });
    }

    // ── Initialize System ─────────────────────────────────────
    function initSystem() {
        const p = getParams();

        atmosphere = new AtmosphereGrid(p.gridX, p.gridY, p.gridZ, p.cellSize);
        atmosphere.applyThermalProfile(p.thermalProfile, {
            baseTemp: p.baseTemp,
            deltaT: p.deltaT,
            seed: Math.floor(Math.random() * 100000),
            humidity: 50,
            windSpeed: p.windSpeed,
            Cn2Level: p.Cn2Level,
            wavelengthUm: p.wavelengthUm,
            useEdlen: true
        });

        rayTracer = new RayTracer(atmosphere);

        const sourcePos = new Vec3(0.5 * p.cellSize, p.gridY * p.cellSize * 0.5, p.gridZ * p.cellSize * 0.5);
        const targetPos = new Vec3((p.gridX - 0.5) * p.cellSize, p.gridY * p.cellSize * 0.5, p.gridZ * p.cellSize * 0.5);
        const recvR = p.receiverDiamM / 2;

        currentSourcePos = sourcePos;
        currentTargetPos = targetPos;

        transmitter = new DataTransmissionSimulator(rayTracer, sourcePos, targetPos, recvR);

        if (!renderer) {
            renderer = new FSORenderer(topCanvas, sideCanvas);
        }

        const totalKm = (p.gridX * p.cellSize / 1000).toFixed(1);
        log('info', `Atmosfer grid: <span class="highlight">${p.gridX}×${p.gridY}×${p.gridZ}</span> hücre, boyut: <span class="highlight">${p.cellSize}m</span>, toplam: <span class="highlight">${totalKm} km</span>`);
        log('info', `Termal: <span class="highlight">${p.thermalProfile}</span>, T=${p.baseTemp}°C, ΔT=${p.deltaT}°C, Hava: <span class="highlight">${p.weather}</span>`);
        log('info', `Lazer: <span class="highlight">λ=${p.wavelengthNm}nm</span>, P=${p.laserPower_mW}mW, div=${p.beamDivRad*1000}mrad`);
        log('info', `Alıcı: çap=${p.receiverDiamM}m, Cn²: <span class="highlight">${p.Cn2Level}</span>`);

        return { sourcePos, targetPos, recvR, params: p };
    }

    // ── Update Link Budget Display ────────────────────────────
    function updateLinkBudget(p, distance_m, avgCn2) {
        const lb = AtmosphericLoss.linkBudget({
            distance_m,
            laserPower_W: p.laserPower_W,
            weather: p.weather,
            wavelengthNm: p.wavelengthNm,
            beamDivRad: p.beamDivRad,
            receiverDiamM: p.receiverDiamM,
            beamDiamM: 0.004,
            Cn2: avgCn2 || 1e-15
        });

        // Link Margin
        resultLinkMargin.textContent = `${lb.linkMargin_dB.toFixed(1)} dB`;
        resultLinkMargin.className = `result-value ${lb.linkViable ? 'emerald' : 'rose'}`;
        linkMarginDetail.textContent = lb.linkViable ? 'Link Aktif ✓' : 'Link Kopuk ✗';
        resultLinkMargin.closest('.result-card').className = `result-card ${lb.linkViable ? 'success' : 'danger'}`;

        // Rx Power
        resultRxPower.textContent = `${lb.P_rx_dBm.toFixed(1)} dBm`;
        resultRxPower.className = `result-value ${lb.P_rx_dBm > -40 ? 'cyan' : 'rose'}`;
        rxPowerDetail.textContent = `Tx: ${lb.P_tx_dBm.toFixed(1)} dBm`;

        // Atmospheric Loss
        resultAtmLoss.textContent = `${lb.totalLoss_dB.toFixed(1)} dB`;
        resultAtmLoss.className = `result-value ${lb.totalLoss_dB < 30 ? 'amber' : 'rose'}`;
        atmLossDetail.textContent = `Abs: ${lb.absorption.loss_dB.toFixed(1)}dB + Geo: ${lb.geometric.loss_dB.toFixed(1)}dB`;

        // Scintillation
        resultScint.textContent = lb.scintillation.sigma2_R.toExponential(2);
        resultScint.className = `result-value ${lb.scintillation.regime === 'weak' ? 'emerald' : lb.scintillation.regime === 'moderate' ? 'amber' : 'rose'}`;
        scintDetail.textContent = `${lb.scintillation.regime} türbülans`;

        // Link budget table
        linkBudgetBody.innerHTML = '';
        const rows = [
            ['Verici Gücü (Pₜₓ)', lb.P_tx_dBm.toFixed(2), 'dBm', `${p.laserPower_mW} mW lazer`],
            ['Dalga Boyu (λ)', p.wavelengthNm, 'nm', 'Telecom bant'],
            ['Mesafe (L)', (distance_m/1000).toFixed(2), 'km', 'Kaynak → Alıcı'],
            ['Atmosferik Kayıp', lb.absorption.loss_dB.toFixed(2), 'dB', `${p.weather} – α=${lb.absorption.alpha_dB_km.toFixed(2)} dB/km`],
            ['Geometrik Kayıp', lb.geometric.loss_dB.toFixed(2), 'dB', `Beam Ø at recv: ${lb.geometric.beamDiamAtReceiver.toFixed(2)}m`],
            ['Scintilasyon Penalty', lb.scintPenalty_dB.toFixed(2), 'dB', `σ²ᵣ=${lb.scintillation.sigma2_R.toExponential(2)} (${lb.scintillation.regime})`],
            ['Toplam Kayıp', lb.totalLoss_dB.toFixed(2), 'dB', 'Abs + Geo + Scint'],
            ['Alıcı Güç (Pᵣₓ)', lb.P_rx_dBm.toFixed(2), 'dBm', `${lb.P_rx_W.toExponential(2)} W`],
            ['Alıcı Hassasiyeti', '-40.00', 'dBm', 'APD/PIN diode'],
            ['Link Marjı', lb.linkMargin_dB.toFixed(2), 'dB', lb.linkViable ? '✓ Yeterli' : '✗ Yetersiz'],
        ];

        rows.forEach(([param, val, unit, desc]) => {
            const tr = document.createElement('tr');
            tr.innerHTML = `<td>${param}</td><td style="font-family:'JetBrains Mono',monospace;color:var(--accent-cyan)">${val}</td><td>${unit}</td><td style="color:var(--text-tertiary)">${desc}</td>`;
            linkBudgetBody.appendChild(tr);
        });

        log('info', `Link bütçesi: Pₜₓ=<span class="highlight">${lb.P_tx_dBm.toFixed(1)}dBm</span> → Pᵣₓ=<span class="highlight">${lb.P_rx_dBm.toFixed(1)}dBm</span>, Marj: <span class="highlight">${lb.linkMargin_dB.toFixed(1)}dB</span>`);
        log(lb.linkViable ? 'success' : 'error', lb.linkViable ? '✓ 6 km link aktif – veri iletilebilir' : '✗ Link kopuk – sinyal yetersiz');

        return lb;
    }

    // ── Run Simulation ────────────────────────────────────────
    async function runSimulation() {
        const { sourcePos, targetPos, recvR, params: p } = initSystem();
        
        if (usePythonBackend) {
            log('info', '─── Python Backend ile Simülasyon Çalıştırılıyor ───');
            const t0 = performance.now();
            try {
                const res = await fetch(`${BACKEND_URL}/api/simulate`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(p)
                });
                if (!res.ok) throw new Error(await res.text());
                const data = await res.json();
                const elapsed = (performance.now() - t0).toFixed(2);
                
                // Reconstruct response data into local states
                currentTrace = data.trace;
                currentTrace.path = currentTrace.path.map(pt => ({
                    ...pt,
                    position: new Vec3(pt.position.x, pt.position.y, pt.position.z),
                    direction: new Vec3(pt.direction.x, pt.direction.y, pt.direction.z)
                }));
                if (currentTrace.exitPosition) {
                    currentTrace.exitPosition = new Vec3(currentTrace.exitPosition.x, currentTrace.exitPosition.y, currentTrace.exitPosition.z);
                }
                if (currentTrace.exitDirection) {
                    currentTrace.exitDirection = new Vec3(currentTrace.exitDirection.x, currentTrace.exitDirection.y, currentTrace.exitDirection.z);
                }
                currentTrace.refractionEvents = currentTrace.refractionEvents.map(evt => ({
                    ...evt,
                    position: new Vec3(evt.position.x, evt.position.y, evt.position.z),
                    incidentDir: evt.incidentDir ? new Vec3(evt.incidentDir.x, evt.incidentDir.y, evt.incidentDir.z) : null,
                    refractedDir: evt.refractedDir ? new Vec3(evt.refractedDir.x, evt.refractedDir.y, evt.refractedDir.z) : null
                }));

                log('info', `[Python] Ray trace: <span class="highlight">${elapsed}ms</span>, ${currentTrace.steps} adım, ${currentTrace.path.length} path noktası`);

                if (currentTrace.success) {
                    log('success', `✓ [Python] Işın 6 km sonunda alıcıya ulaştı!`);
                    if (currentTrace.exitPosition) {
                        const latDev = Math.sqrt(
                            Math.pow(currentTrace.exitPosition.y - targetPos.y, 2) +
                            Math.pow(currentTrace.exitPosition.z - targetPos.z, 2)
                        );
                        log('info', `[Python] Yanal sapma: <span class="highlight">${(latDev * 100).toFixed(4)} cm</span> (${latDev < recvR ? '✓ alıcı içinde' : '✗ alıcı dışında'})`);
                    }
                } else {
                    log('warn', `✗ [Python] Işın 6 km'de alıcıya ulaşamadı!`);
                }

                log('info', `[Python] Kırılma: <span class="highlight">${currentTrace.refractionEvents.length}</span>, TIR: <span class="highlight">${currentTrace.totalInternalReflections}</span>`);
                log('info', `[Python] Ort. Cn²: <span class="highlight">${currentTrace.avgCn2.toExponential(3)}</span> m⁻²/³`);

                updateResults(currentTrace, targetPos, recvR);
                
                const lb = data.linkBudget;
                resultLinkMargin.textContent = `${lb.linkMargin_dB.toFixed(1)} dB`;
                resultLinkMargin.className = `result-value ${lb.linkViable ? 'emerald' : 'rose'}`;
                linkMarginDetail.textContent = lb.linkViable ? 'Link Aktif ✓' : 'Link Kopuk ✗';
                resultLinkMargin.closest('.result-card').className = `result-card ${lb.linkViable ? 'success' : 'danger'}`;

                resultRxPower.textContent = `${lb.P_rx_dBm.toFixed(1)} dBm`;
                resultRxPower.className = `result-value ${lb.P_rx_dBm > -40 ? 'cyan' : 'rose'}`;
                rxPowerDetail.textContent = `Tx: ${lb.P_tx_dBm.toFixed(1)} dBm`;

                resultAtmLoss.textContent = `${lb.totalLoss_dB.toFixed(1)} dB`;
                resultAtmLoss.className = `result-value ${lb.totalLoss_dB < 30 ? 'amber' : 'rose'}`;
                atmLossDetail.textContent = `Abs: ${lb.absorption.loss_dB.toFixed(1)}dB + Geo: ${lb.geometric.loss_dB.toFixed(1)}dB`;

                resultScint.textContent = lb.scintillation.sigma2_R.toExponential(2);
                resultScint.className = `result-value ${lb.scintillation.regime === 'weak' ? 'emerald' : lb.scintillation.regime === 'moderate' ? 'amber' : 'rose'}`;
                scintDetail.textContent = `${lb.scintillation.regime} türbülans`;

                // Update table
                linkBudgetBody.innerHTML = '';
                const rows = [
                    ['Verici Gücü (Pₜₓ)', lb.P_tx_dBm.toFixed(2), 'dBm', `${p.laserPower_mW} mW lazer`],
                    ['Dalga Boyu (λ)', p.wavelengthNm, 'nm', 'Telecom bant'],
                    ['Mesafe (L)', (currentTrace.totalDistance/1000).toFixed(2), 'km', 'Kaynak → Alıcı'],
                    ['Atmosferik Kayıp', lb.absorption.loss_dB.toFixed(2), 'dB', `${p.weather} – α=${lb.absorption.alpha_dB_km.toFixed(2)} dB/km`],
                    ['Geometrik Kayıp', lb.geometric.loss_dB.toFixed(2), 'dB', `Beam Ø at recv: ${lb.geometric.beamDiamAtReceiver.toFixed(2)}m`],
                    ['Scintilasyon Penalty', lb.scintPenalty_dB.toFixed(2), 'dB', `σ²ᵣ=${lb.scintillation.sigma2_R.toExponential(2)} (${lb.scintillation.regime})`],
                    ['Toplam Kayıp', lb.totalLoss_dB.toFixed(2), 'dB', 'Abs + Geo + Scint'],
                    ['Alıcı Güç (Pᵣₓ)', lb.P_rx_dBm.toFixed(2), 'dBm', `${lb.P_rx_W.toExponential(2)} W`],
                    ['Alıcı Hassasiyeti', '-40.00', 'dBm', 'APD/PIN diode'],
                    ['Link Marjı', lb.linkMargin_dB.toFixed(2), 'dB', lb.linkViable ? '✓ Yeterli' : '✗ Yetersiz'],
                ];
                rows.forEach(([param, val, unit, desc]) => {
                    const tr = document.createElement('tr');
                    tr.innerHTML = `<td>${param}</td><td style="font-family:'JetBrains Mono',monospace;color:var(--accent-cyan)">${val}</td><td>${unit}</td><td style="color:var(--text-tertiary)">${desc}</td>`;
                    linkBudgetBody.appendChild(tr);
                });

                verifyAllSnellEvents(currentTrace);
                renderer.render(atmosphere, currentTrace, sourcePos, targetPos, recvR);
                startAnimation(sourcePos, targetPos, recvR);
                return;
            } catch (err) {
                log('error', `Python API Simülasyonu başarısız: ${err.message}. JS fallback motoru devralıyor.`);
            }
        }

        const dir = targetPos.sub(sourcePos).normalize();
        log('info', `Işın yönü: ${dir.toString()}`);

        const t0 = performance.now();
        currentTrace = rayTracer.trace(sourcePos, dir);
        const elapsed = (performance.now() - t0).toFixed(2);

        log('info', `Ray trace: <span class="highlight">${elapsed}ms</span>, ${currentTrace.steps} adım, ${currentTrace.path.length} path noktası`);

        if (currentTrace.success) {
            log('success', `✓ Işın 6 km sonunda alıcıya ulaştı!`);
            if (currentTrace.exitPosition) {
                const latDev = Math.sqrt(
                    Math.pow(currentTrace.exitPosition.y - targetPos.y, 2) +
                    Math.pow(currentTrace.exitPosition.z - targetPos.z, 2)
                );
                log('info', `Yanal sapma: <span class="highlight">${(latDev * 100).toFixed(4)} cm</span> (${latDev < recvR ? '✓ alıcı içinde' : '✗ alıcı dışında'})`);
            }
        } else {
            log('warn', `✗ Işın 6 km'de alıcıya ulaşamadı!`);
        }

        log('info', `Kırılma: <span class="highlight">${currentTrace.refractionEvents.length}</span>, TIR: <span class="highlight">${currentTrace.totalInternalReflections}</span>`);
        log('info', `Ort. Cn²: <span class="highlight">${currentTrace.avgCn2.toExponential(3)}</span> m⁻²/³`);

        updateResults(currentTrace, targetPos, recvR);
        updateLinkBudget(p, currentTrace.totalDistance, currentTrace.avgCn2);
        verifyAllSnellEvents(currentTrace);
        renderer.render(atmosphere, currentTrace, sourcePos, targetPos, recvR);
        startAnimation(sourcePos, targetPos, recvR);
    }

    // ── Formula Tests ─────────────────────────────────────────
    async function runFormulaTests() {
        if (usePythonBackend) {
            log('test', '═══════════════════════════════════════════');
            log('test', '  VEKTÖREL SNELL YASASI – PYTHON BİRİM TESTLERİ');
            log('test', '═══════════════════════════════════════════');
            try {
                const res = await fetch(`${BACKEND_URL}/api/test`, { method: 'POST' });
                if (!res.ok) throw new Error(await res.text());
                const data = await res.json();
                
                data.results.forEach(t => {
                    log(t.pass ? 'success' : 'error', `${t.name}: ${t.pass ? 'GEÇTİ ✓' : 'BAŞARISIZ ✗'} ${t.error}`);
                });
                
                log('test', '═══════════════════════════════════════════');
                log(data.allPassed ? 'success' : 'error', data.allPassed
                    ? '  TÜM 11 PYTHON TESTİ GEÇTİ ✓ – Python Motoru Doğrulandı!'
                    : '  BAZI PYTHON TESTLERİ BAŞARISIZ ✗');
                log('test', '═══════════════════════════════════════════');
                
                resultSnell.className = `verification-badge ${data.allPassed ? 'pass' : 'fail'}`;
                resultSnell.textContent = data.allPassed ? '✓ 11/11 PYTHON OK' : '✗ HATA';
                return data.allPassed;
            } catch (e) {
                log('error', `Python testleri çalıştırılamadı: ${e.message}. JS testleri çalıştırılıyor.`);
            }
        }
        return runLocalFormulaTests();
    }

    function runLocalFormulaTests() {
        log('test', '═══════════════════════════════════════════');
        log('test', '  VEKTÖREL SNELL YASASI – 6 km BİRİM TESTLERİ (JS)');
        log('test', '═══════════════════════════════════════════');

        let allPassed = true;

        // Test 1: Normal incidence
        {
            const i = new Vec3(0, 0, -1);
            const n = new Vec3(0, 0, 1);
            const result = vectorialSnellLaw(i, n, 1.0, 1.5);
            const pass = result && Math.abs(result.direction.z - (-1)) < 1e-6;
            log(pass ? 'success' : 'error', `T1 – Normal geliş (θi=0°): ${pass ? 'GEÇTİ ✓' : 'BAŞARISIZ ✗'}`);
            if (!pass) allPassed = false;
        }

        // Test 2: Air→Glass 30°
        {
            const theta_i = 30 * Math.PI / 180;
            const i = new Vec3(Math.sin(theta_i), 0, -Math.cos(theta_i));
            const n = new Vec3(0, 0, 1);
            const result = vectorialSnellLaw(i, n, 1.0, 1.5);
            const expectedSin = (1.0 / 1.5) * Math.sin(theta_i);
            const actualSin = Math.sqrt(1 - result.cosTheta_t * result.cosTheta_t);
            const pass = Math.abs(actualSin - expectedSin) < 1e-10;
            const verify = verifySnellLaw(1.0, 1.5, result.cosTheta_i, result.cosTheta_t);
            log(pass ? 'success' : 'error', `T2 – Hava→Cam (30°): ${pass ? 'GEÇTİ ✓' : 'BAŞARISIZ ✗'} hata=${verify.error.toExponential(3)}`);
            if (!pass) allPassed = false;
        }

        // Test 3: TIR
        {
            const critAngle = Math.asin(1.0 / 1.5);
            const theta = critAngle + 5 * Math.PI / 180;
            const i = new Vec3(Math.sin(theta), 0, -Math.cos(theta));
            const result = vectorialSnellLaw(i, new Vec3(0, 0, 1), 1.5, 1.0);
            const pass = result === null;
            log(pass ? 'success' : 'error', `T3 – TIR (θ>${(critAngle*180/Math.PI).toFixed(1)}°): ${pass ? 'GEÇTİ ✓' : 'BAŞARISIZ ✗'}`);
            if (!pass) allPassed = false;
        }

        // Test 4: Atmosphere boundary (6 km realistic Δn)
        {
            const n1 = 1.000293, n2 = 1.000270;
            const theta_i = 5 * Math.PI / 180;
            const i = new Vec3(Math.cos(theta_i), Math.sin(theta_i), 0);
            const result = vectorialSnellLaw(i, new Vec3(-1, 0, 0), n1, n2);
            const verify = verifySnellLaw(n1, n2, result.cosTheta_i, result.cosTheta_t);
            log(verify.valid ? 'success' : 'error', `T4 – Atmosfer sınırı (Δn=${(n2-n1).toExponential(3)}): ${verify.valid ? 'GEÇTİ ✓' : 'BAŞARISIZ ✗'}`);
            if (!verify.valid) allPassed = false;
        }

        // Test 5: Reciprocity
        {
            const n1 = 1.0003, n2 = 1.0001;
            const theta_i = 10 * Math.PI / 180;
            const i = new Vec3(Math.sin(theta_i), 0, -Math.cos(theta_i));
            const forward = vectorialSnellLaw(i, new Vec3(0, 0, 1), n1, n2);
            const backward = vectorialSnellLaw(forward.direction.scale(-1), new Vec3(0, 0, -1), n2, n1);
            const recovered = backward.direction.scale(-1);
            const error = Math.sqrt(Math.pow(recovered.x - i.x, 2) + Math.pow(recovered.y - i.y, 2) + Math.pow(recovered.z - i.z, 2));
            const pass = error < 1e-10;
            log(pass ? 'success' : 'error', `T5 – Tersinirlik: ${pass ? 'GEÇTİ ✓' : 'BAŞARISIZ ✗'} hata=${error.toExponential(3)}`);
            if (!pass) allPassed = false;
        }

        // Test 6: Tangential conservation
        {
            const n1 = 1.33, n2 = 1.0;
            const theta_i = 20 * Math.PI / 180;
            const i = new Vec3(Math.sin(theta_i), 0, -Math.cos(theta_i));
            const n = new Vec3(0, 0, 1);
            const result = vectorialSnellLaw(i, n, n1, n2);
            const i_tan = i.sub(n.scale(i.dot(n)));
            const t_tan = result.direction.sub(n.scale(result.direction.dot(n)));
            const pass = Math.abs(n1 * i_tan.length() - n2 * t_tan.length()) < 1e-10;
            log(pass ? 'success' : 'error', `T6 – Teğetsel korunum: ${pass ? 'GEÇTİ ✓' : 'BAŞARISIZ ✗'}`);
            if (!pass) allPassed = false;
        }

        // Test 7: Angle sweep with atmosphere indices
        {
            let sweepPass = true;
            const n1 = 1.000293, n2 = 1.000310;
            const angles = [0, 5, 10, 15, 20, 30, 45, 60, 75, 85];
            for (const deg of angles) {
                const theta = deg * Math.PI / 180;
                const i = new Vec3(Math.sin(theta), 0, -Math.cos(theta));
                const result = vectorialSnellLaw(i, new Vec3(0, 0, 1), n1, n2);
                if (result) {
                    const v = verifySnellLaw(n1, n2, result.cosTheta_i, result.cosTheta_t);
                    if (!v.valid) sweepPass = false;
                }
            }
            log(sweepPass ? 'success' : 'error', `T7 – Açı tarama (0°-85°): ${sweepPass ? 'TÜM GEÇTİ ✓' : 'BAŞARISIZ ✗'}`);
            if (!sweepPass) allPassed = false;
        }

        // Test 8: Beer-Lambert absorption (6 km clear)
        {
            const loss = AtmosphericLoss.absorptionLoss(6000, 'clear', 1550);
            const expectedLoss_dB = 0.2 * 6; // 0.2 dB/km × 6 km = 1.2 dB
            const pass = Math.abs(loss.loss_dB - expectedLoss_dB) < 0.5; // reasonable tolerance
            log(pass ? 'success' : 'error', `T8 – Beer-Lambert 6km clear: ${pass ? 'GEÇTİ ✓' : 'BAŞARISIZ ✗'} → ${loss.loss_dB.toFixed(2)} dB (beklenen ~${expectedLoss_dB.toFixed(1)} dB)`);
            if (!pass) allPassed = false;
        }

        // Test 9: Geometric loss at 6 km
        {
            const geo = AtmosphericLoss.geometricLoss(6000, 0.001, 0.1, 0.004);
            const expectedBeamDiam = 0.004 + 2 * 0.001 * 6000; // ~12m at 6km
            const pass = Math.abs(geo.beamDiamAtReceiver - expectedBeamDiam) < 0.01;
            log(pass ? 'success' : 'error', `T9 – Geometrik yayılma 6km: ${pass ? 'GEÇTİ ✓' : 'BAŞARISIZ ✗'} → Beam Ø=${geo.beamDiamAtReceiver.toFixed(2)}m (beklenen ~${expectedBeamDiam.toFixed(2)}m)`);
            if (!pass) allPassed = false;
        }

        // Test 10: Edlén equation verification
        {
            const atm = new AtmosphereGrid(2, 2, 2, 1);
            const n_15C = atm.refractiveIndexSimple(15, 101325);
            const n_35C = atm.refractiveIndexSimple(35, 101325);
            const pass = n_15C > n_35C && Math.abs(n_15C - 1.000293) < 1e-5;
            log(pass ? 'success' : 'error', `T10 – Edlén: ${pass ? 'GEÇTİ ✓' : 'BAŞARISIZ ✗'} → n(15°C)=${n_15C.toFixed(8)}, n(35°C)=${n_35C.toFixed(8)} (sıcak hava → düşük n)`);
            if (!pass) allPassed = false;
        }

        // Test 11: Scintillation index
        {
            const scint = AtmosphericLoss.scintillationIndex(6000, 1e-15, 1550e-9);
            const pass = scint.sigma2_R > 0 && !isNaN(scint.sigma2_R);
            log(pass ? 'success' : 'error', `T11 – Rytov scintilasyon: ${pass ? 'GEÇTİ ✓' : 'BAŞARISIZ ✗'} → σ²ᵣ=${scint.sigma2_R.toExponential(3)} (${scint.regime})`);
            if (!pass) allPassed = false;
        }

        log('test', '═══════════════════════════════════════════');
        log(allPassed ? 'success' : 'error', allPassed
            ? '  TÜM 11 TEST GEÇTİ ✓ – Motor doğrulandı (6 km)!'
            : '  BAZI TESTLER BAŞARISIZ ✗');
        log('test', '═══════════════════════════════════════════');

        resultSnell.className = `verification-badge ${allPassed ? 'pass' : 'fail'}`;
        resultSnell.textContent = allPassed ? '✓ 11/11 DOĞRULANDI' : '✗ HATA';
        return allPassed;
    }

    // ── Send Data ─────────────────────────────────────────────
    async function sendData() {
        const { sourcePos, targetPos, recvR, params: p } = initSystem();
        const pattern = dataPatternSelect.value;
        const numBits = parseInt(dataBitsSlider.value);
        const data = DataTransmissionSimulator.generateTestData(pattern, numBits);

        log('info', `6 km veri gönderimi: <span class="highlight">${data.length} bit</span>, desen: <span class="highlight">${pattern}</span>`);

        if (usePythonBackend) {
            log('info', `[Python] Veri paketi gönderiliyor...`);
            const t0 = performance.now();
            try {
                const res = await fetch(`${BACKEND_URL}/api/send-data`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ simParams: p, pattern, numBits })
                });
                if (!res.ok) throw new Error(await res.text());
                const result = await res.json();
                const elapsed = (performance.now() - t0).toFixed(2);

                log('info', `[Python] Gönderim: <span class="highlight">${elapsed}ms</span>`);
                log(result.bitErrorRate === 0 ? 'success' : 'warn',
                    `[Python] BER: <span class="highlight">${(result.bitErrorRate * 100).toFixed(2)}%</span>, Başarı: <span class="highlight">${result.successRate.toFixed(1)}%</span>`);

                if (result.missedOnes > 0) {
                    log('warn', `[Python] Kayıp: <span class="highlight">${result.missedOnes}</span> bit alıcıya ulaşamadı`);
                }

                resultBER.textContent = `${(result.bitErrorRate * 100).toFixed(2)}%`;
                resultBER.className = `result-value ${result.bitErrorRate === 0 ? 'emerald' : result.bitErrorRate < 0.1 ? 'amber' : 'rose'}`;
                berDetail.textContent = `${result.correctBits}/${result.totalBits} doğru`;

                sentBitsEl.innerHTML = '';
                recvBitsEl.innerHTML = '';
                for (let i = 0; i < data.length; i++) {
                    const s = document.createElement('div');
                    s.className = `bit sent-${data[i]}`;
                    s.textContent = data[i];
                    sentBitsEl.appendChild(s);

                    const r = document.createElement('div');
                    r.className = `bit ${result.results[i].correct ? 'recv-ok' : 'recv-fail'}`;
                    r.textContent = result.results[i].received;
                    recvBitsEl.appendChild(r);
                }

                progressFill.style.width = `${result.successRate}%`;
                progressFill.style.background = result.bitErrorRate === 0
                    ? 'var(--gradient-success)'
                    : result.bitErrorRate < 0.1
                        ? 'linear-gradient(135deg, #f59e0b, #f97316)'
                        : 'var(--gradient-danger)';

                const lastOneTrace = result.results.filter(r => r.trace).pop();
                if (lastOneTrace && lastOneTrace.trace) {
                    // Reconstruct exit vectors for animation
                    currentTrace = lastOneTrace.trace;
                    currentTrace.path = currentTrace.path.map(pt => ({
                        ...pt,
                        position: new Vec3(pt.position.x, pt.position.y, pt.position.z),
                        direction: new Vec3(pt.direction.x, pt.direction.y, pt.direction.z)
                    }));
                    if (currentTrace.exitPosition) {
                        currentTrace.exitPosition = new Vec3(currentTrace.exitPosition.x, currentTrace.exitPosition.y, currentTrace.exitPosition.z);
                    }
                    if (currentTrace.exitDirection) {
                        currentTrace.exitDirection = new Vec3(currentTrace.exitDirection.x, currentTrace.exitDirection.y, currentTrace.exitDirection.z);
                    }
                    currentTrace.refractionEvents = currentTrace.refractionEvents.map(evt => ({
                        ...evt,
                        position: new Vec3(evt.position.x, evt.position.y, evt.position.z),
                        incidentDir: evt.incidentDir ? new Vec3(evt.incidentDir.x, evt.incidentDir.y, evt.incidentDir.z) : null,
                        refractedDir: evt.refractedDir ? new Vec3(evt.refractedDir.x, evt.refractedDir.y, evt.refractedDir.z) : null
                    }));

                    updateResults(currentTrace, targetPos, recvR);
                    
                    const lb = lastOneTrace.linkBudget;
                    resultLinkMargin.textContent = `${lb.linkMargin_dB.toFixed(1)} dB`;
                    resultLinkMargin.className = `result-value ${lb.linkViable ? 'emerald' : 'rose'}`;
                    linkMarginDetail.textContent = lb.linkViable ? 'Link Aktif ✓' : 'Link Kopuk ✗';
                    resultLinkMargin.closest('.result-card').className = `result-card ${lb.linkViable ? 'success' : 'danger'}`;

                    resultRxPower.textContent = `${lb.P_rx_dBm.toFixed(1)} dBm`;
                    resultRxPower.className = `result-value ${lb.P_rx_dBm > -40 ? 'cyan' : 'rose'}`;
                    rxPowerDetail.textContent = `Tx: ${lb.P_tx_dBm.toFixed(1)} dBm`;

                    resultAtmLoss.textContent = `${lb.totalLoss_dB.toFixed(1)} dB`;
                    resultAtmLoss.className = `result-value ${lb.totalLoss_dB < 30 ? 'amber' : 'rose'}`;
                    atmLossDetail.textContent = `Abs: ${lb.absorption.loss_dB.toFixed(1)}dB + Geo: ${lb.geometric.loss_dB.toFixed(1)}dB`;

                    resultScint.textContent = lb.scintillation.sigma2_R.toExponential(2);
                    resultScint.className = `result-value ${lb.scintillation.regime === 'weak' ? 'emerald' : lb.scintillation.regime === 'moderate' ? 'amber' : 'rose'}`;
                    scintDetail.textContent = `${lb.scintillation.regime} türbülans`;

                    renderer.render(atmosphere, currentTrace, sourcePos, targetPos, recvR);
                    startAnimation(sourcePos, targetPos, recvR);
                }
                return;
            } catch (err) {
                log('error', `Python Veri Gönderimi hatası: ${err.message}. JS Fallback kullanılıyor.`);
            }
        }

        const t0 = performance.now();
        const result = transmitter.sendPacket(data);
        const elapsed = (performance.now() - t0).toFixed(2);

        log('info', `Gönderim: <span class="highlight">${elapsed}ms</span>`);
        log(result.bitErrorRate === 0 ? 'success' : 'warn',
            `BER: <span class="highlight">${(result.bitErrorRate * 100).toFixed(2)}%</span>, Başarı: <span class="highlight">${result.successRate.toFixed(1)}%</span>`);

        if (result.missedOnes > 0) {
            log('warn', `Kayıp: <span class="highlight">${result.missedOnes}</span> bit alıcıya ulaşamadı`);
        }

        resultBER.textContent = `${(result.bitErrorRate * 100).toFixed(2)}%`;
        resultBER.className = `result-value ${result.bitErrorRate === 0 ? 'emerald' : result.bitErrorRate < 0.1 ? 'amber' : 'rose'}`;
        berDetail.textContent = `${result.correctBits}/${result.totalBits} doğru`;

        sentBitsEl.innerHTML = '';
        recvBitsEl.innerHTML = '';
        for (let i = 0; i < data.length; i++) {
            const s = document.createElement('div');
            s.className = `bit sent-${data[i]}`;
            s.textContent = data[i];
            sentBitsEl.appendChild(s);

            const r = document.createElement('div');
            r.className = `bit ${result.results[i].correct ? 'recv-ok' : 'recv-fail'}`;
            r.textContent = result.results[i].received;
            recvBitsEl.appendChild(r);
        }

        progressFill.style.width = `${result.successRate}%`;
        progressFill.style.background = result.bitErrorRate === 0
            ? 'var(--gradient-success)'
            : result.bitErrorRate < 0.1
                ? 'linear-gradient(135deg, #f59e0b, #f97316)'
                : 'var(--gradient-danger)';

        const lastOneTrace = result.results.filter(r => r.trace).pop();
        if (lastOneTrace && lastOneTrace.trace) {
            currentTrace = lastOneTrace.trace;
            updateResults(currentTrace, targetPos, recvR);
            updateLinkBudget(p, currentTrace.totalDistance, currentTrace.avgCn2);
            renderer.render(atmosphere, currentTrace, sourcePos, targetPos, recvR);
            startAnimation(sourcePos, targetPos, recvR);
        }
    }

    // ── Update Results ────────────────────────────────────────
    function updateResults(trace, targetPos, recvR) {
        if (!trace) return;
        if (trace.exitPosition) {
            const latDev = Math.sqrt(
                Math.pow(trace.exitPosition.y - targetPos.y, 2) +
                Math.pow(trace.exitPosition.z - targetPos.z, 2)
            );
            resultDeviation.textContent = `${(latDev * 100).toFixed(3)} cm`;
            resultDeviation.className = `result-value ${latDev <= recvR ? 'emerald' : 'rose'}`;
            devDetail.textContent = `Alıcı r=${recvR}m`;
            resultDeviation.closest('.result-card').className = `result-card ${latDev <= recvR ? 'success' : 'danger'}`;
        }
        const refrCount = trace.refractionEvents.filter(e => e.type === 'refraction').length;
        resultRefract.textContent = refrCount;
        refractDetail.textContent = `${trace.steps} adım`;
        resultTIR.textContent = trace.totalInternalReflections;
        tirDetail.textContent = trace.totalInternalReflections > 0 ? 'Uyarı!' : 'Normal';
        resultTIR.className = `result-value ${trace.totalInternalReflections > 0 ? 'rose' : 'emerald'}`;
        resultDistance.textContent = `${(trace.totalDistance / 1000).toFixed(3)} km`;
    }

    // ── Verify All Snell Events ───────────────────────────────
    function verifyAllSnellEvents(trace) {
        if (!trace || !trace.refractionEvents.length) return;
        verifyTableBody.innerHTML = '';
        let allValid = true;

        const refractions = trace.refractionEvents.filter(e => e.type === 'refraction');
        const evts = refractions.slice(0, 20);

        for (let i = 0; i < evts.length; i++) {
            const evt = evts[i];
            const v = evt.verification;
            if (!v.valid) allValid = false;
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>#${i + 1}</td>
                <td>${evt.n1.toFixed(6)}</td><td>${evt.n2.toFixed(6)}</td>
                <td>${(Math.acos(evt.cosTheta_i) * 180 / Math.PI).toFixed(4)}°</td>
                <td>${(Math.acos(evt.cosTheta_t) * 180 / Math.PI).toFixed(4)}°</td>
                <td>${v.error.toExponential(2)}</td>
                <td style="color:${v.valid ? 'var(--accent-emerald)' : 'var(--accent-rose)'}">${v.valid ? '✓' : '✗'}</td>
            `;
            verifyTableBody.appendChild(row);
        }
        if (refractions.length > 20) {
            const row = document.createElement('tr');
            row.innerHTML = `<td colspan="7" style="text-align:center;color:var(--text-tertiary)">... ve ${refractions.length - 20} daha</td>`;
            verifyTableBody.appendChild(row);
        }
        resultSnell.className = `verification-badge ${allValid ? 'pass' : 'fail'}`;
        resultSnell.textContent = allValid ? `✓ DOĞRULANDI (${refractions.length} olay)` : '✗ HATA';
    }

    // ── Animation Loop ────────────────────────────────────────
    function startAnimation(sourcePos, targetPos, recvR) {
        if (animFrameId) cancelAnimationFrame(animFrameId);
        function animate() {
            renderer.render(atmosphere, currentTrace, sourcePos, targetPos, recvR);
            animFrameId = requestAnimationFrame(animate);
        }
        animate();
    }

    // ── COMSOL Export Handlers ─────────────────────────────────
    btnComsolJava.addEventListener('click', async () => {
        if (!atmosphere) { log('warn', 'Önce simülasyon çalıştırın!'); return; }
        const p = getParams();
        if (usePythonBackend) {
            try {
                const res = await fetch(`${BACKEND_URL}/api/export/comsol-java`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(p)
                });
                const data = await res.json();
                COMSOLBridge.download(data.content, data.filename, data.mimeType);
                log('success', `✓ [Python] COMSOL Java model script indirildi: ${data.filename}`);
                return;
            } catch (e) {
                log('error', `Backend COMSOL export hatası: ${e.message}. Yerel sürüm kullanılıyor.`);
            }
        }
        const script = comsolBridge.generateModelScript(atmosphere, currentSourcePos, currentTargetPos, {
            wavelength: p.wavelengthNm * 1e-9,
            laserPower: p.laserPower_W,
            baseTemp: p.baseTemp,
            deltaT: p.deltaT
        });
        COMSOLBridge.download(script, 'FSO_AtmosphericChannel.java', 'text/x-java-source');
        log('success', '✓ COMSOL Java model script indirildi: FSO_AtmosphericChannel.java');
    });

    btnComsolMatlab.addEventListener('click', async () => {
        if (!atmosphere) { log('warn', 'Önce simülasyon çalıştırın!'); return; }
        const p = getParams();
        if (usePythonBackend) {
            try {
                const res = await fetch(`${BACKEND_URL}/api/export/comsol-matlab`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(p)
                });
                const data = await res.json();
                COMSOLBridge.download(data.content, data.filename, data.mimeType);
                log('success', `✓ [Python] MATLAB LiveLink script indirildi: ${data.filename}`);
                return;
            } catch (e) {
                log('error', `Backend COMSOL export hatası: ${e.message}. Yerel sürüm kullanılıyor.`);
            }
        }
        const script = comsolBridge.generateMatlabScript(atmosphere, currentSourcePos, currentTargetPos, {
            wavelength: p.wavelengthNm * 1e-9,
            baseTemp: p.baseTemp
        });
        COMSOLBridge.download(script, 'FSO_COMSOL_LiveLink.m', 'text/x-matlab');
        log('success', '✓ MATLAB LiveLink script indirildi: FSO_COMSOL_LiveLink.m');
    });

    btnComsolCsv.addEventListener('click', async () => {
        if (!atmosphere) { log('warn', 'Önce simülasyon çalıştırın!'); return; }
        const p = getParams();
        if (usePythonBackend) {
            try {
                const res = await fetch(`${BACKEND_URL}/api/export/csv-grid`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(p)
                });
                const data = await res.json();
                COMSOLBridge.download(data.content, data.filename, data.mimeType);
                log('success', `✓ [Python] Atmosfer grid CSV indirildi: ${data.filename}`);
                return;
            } catch (e) {
                log('error', `Backend COMSOL export hatası: ${e.message}. Yerel sürüm kullanılıyor.`);
            }
        }
        const csv = comsolBridge.exportGridCSV(atmosphere);
        COMSOLBridge.download(csv, 'atmosphere_grid.csv', 'text/csv');
        log('success', `✓ Atmosfer grid CSV indirildi: ${atmosphere.gridX}×${atmosphere.gridY}×${atmosphere.gridZ} hücre`);
    });

    btnComsolRaypath.addEventListener('click', async () => {
        if (!currentTrace) { log('warn', 'Önce simülasyon çalıştırın!'); return; }
        const p = getParams();
        if (usePythonBackend) {
            try {
                const res = await fetch(`${BACKEND_URL}/api/export/csv-raypath`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(p)
                });
                const data = await res.json();
                COMSOLBridge.download(data.content, data.filename, data.mimeType);
                log('success', `✓ [Python] Ray path CSV indirildi: ${data.filename}`);
                return;
            } catch (e) {
                log('error', `Backend COMSOL export hatası: ${e.message}. Yerel sürüm kullanılıyor.`);
            }
        }
        const csv = comsolBridge.exportRayPathCSV(currentTrace);
        COMSOLBridge.download(csv, 'ray_path.csv', 'text/csv');
        log('success', `✓ Ray path CSV indirildi: ${currentTrace.path.length} nokta`);
    });

    btnComsolParams.addEventListener('click', async () => {
        if (!atmosphere) { log('warn', 'Önce simülasyon çalıştırın!'); return; }
        const p = getParams();
        if (usePythonBackend) {
            try {
                const res = await fetch(`${BACKEND_URL}/api/export/comsol-params`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(p)
                });
                const data = await res.json();
                COMSOLBridge.download(data.content, data.filename, data.mimeType);
                log('success', `✓ [Python] COMSOL parametreler dosyası indirildi: ${data.filename}`);
                return;
            } catch (e) {
                log('error', `Backend COMSOL export hatası: ${e.message}. Yerel sürüm kullanılıyor.`);
            }
        }
        const params = comsolBridge.generateParametersFile(atmosphere, currentSourcePos, currentTargetPos, {
            wavelength: p.wavelengthNm * 1e-9,
            laserPower: p.laserPower_W,
            beamWaist: 0.002,
            baseTemp: p.baseTemp,
            deltaT: p.deltaT
        });
        COMSOLBridge.download(params, 'comsol_parameters.txt', 'text/plain');
        log('success', '✓ COMSOL parametreler dosyası indirildi');
    });

    // ── Button Event Handlers ─────────────────────────────────
    btnRunSim.addEventListener('click', () => {
        log('info', '─── 6 km Simülasyon Başlatılıyor ───');
        runSimulation();
    });

    btnRunTest.addEventListener('click', () => {
        log('info', '─── Formül & Motor Testi ───');
        runFormulaTests();
    });

    btnSendData.addEventListener('click', () => {
        log('info', '─── 6 km Veri İletim Testi ───');
        sendData();
    });

    // ── Boot ──────────────────────────────────────────────────
    log('info', 'FSO Fizik Motoru v2.0 – 6 km + COMSOL Entegrasyonu');
    log('info', 'Modüller: Snell Vektörel, Beer-Lambert, Cn² Türbülans, Rytov Scintilasyon');
    log('info', `Yeni: LensSystem (${Object.keys(LensSystem.LENS_TYPES).length} lens), EnvironmentPresets (${Object.keys(EnvironmentPresets.PRESETS).length} ortam)`);
    log('info', 'COMSOL: Java Model, MATLAB LiveLink, CSV Export hazır');


    async function boot() {
        await checkBackendStatus();
        await runFormulaTests();
        setTimeout(() => runSimulation(), 400);
    }

    setTimeout(() => {
        boot();
    }, 600);
});
