/**
 * 3B Yayılım paneli (three.js)
 * ----------------------------
 * POST /api/propagation3d sonucunu çizer: yer ızgarası, atmosfer kutusu, Terminal A/B, ışın tüpü (halkalardan),
 * merkez hattı, BOZULMA BULUTU (voksel noktaları), hotspot işareti, anomali kutuları, lens kesitleri
 * (LatheGeometry + yatay kesit düzlemi) ve ARQ/duplex "bölge başına tekrar" bantları.
 *
 * Ölçek: 1 sahne birimi = 100 m. Sahne eksenleri: X = bağlantı (x), Y = irtifa (z), Z = yanal (y).
 * Işın yarıçapı santimetre mertebesinde olduğundan, yalnızca ışın tüpü/merkez hattının doğrultudan sapması
 * "dikey ölçek abartma" katsayısı ile büyütülür; voksel bulutu, kutu ve terminaller gerçek ölçektedir.
 */
(function () {
    'use strict';

    const U = 100; // metre / sahne birimi
    const bus = (window.FSOBus = window.FSOBus || new EventTarget());
    const S = (window.FSOState = window.FSOState || {});
    S.terminals = S.terminals || { A: {}, B: {} };
    const app = () => window.FSOApp || { log() {}, BACKEND_URL: window.location.origin, getParams: () => ({}) };
    const log = (t, m) => app().log(t, m);
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const clamp = (x, a, b) => Math.max(a, Math.min(b, x));

    const STOPS = [[0, [44, 123, 182]], [0.35, [171, 217, 233]], [0.6, [255, 255, 191]], [0.8, [253, 174, 97]], [1, [215, 25, 28]]];
    function scoreRGB(t) {
        t = clamp(t, 0, 1);
        for (let i = 1; i < STOPS.length; i++) {
            if (t <= STOPS[i][0]) {
                const a = STOPS[i - 1], b = STOPS[i];
                const u = (t - a[0]) / (b[0] - a[0]);
                return [0, 1, 2].map((k) => (a[1][k] + (b[1][k] - a[1][k]) * u) / 255);
            }
        }
        return [215 / 255, 25 / 255, 28 / 255];
    }

    const ANOM_TYPES = {
        fog: { name: 'Sis yaması', color: 0x94a3b8, key: 'extra_alpha_dB_km', def: 60, unit: 'dB/km', min: 0, max: 1000 },
        turb: { name: 'Türbülans cebi', color: 0xa855f7, key: 'cn2_factor', def: 1000, unit: '× Cn²', min: 0, max: 1e6 },
        warm: { name: 'Sıcak/soğuk kabarcık', color: 0xf97316, key: 'delta_T', def: 15, unit: '°C', min: -100, max: 100 },
    };

    const st = {
        result: null, request: null, anomalies: [], regions: null, regionsTitle: '',
        exag: 1, auto: true, three: false, linked: true, distKm: 6, timer: null,
    };

    // ───────────────────────── Arayüz ─────────────────────────
    function buildUI(pane) {
        pane.innerHTML = `
        <div class="v2-btn-row">
            <button class="btn btn-primary" id="v3-run">🛰️ 3B Yayılımı Hesapla</button>
            <button class="btn btn-outline" id="v3-reset-cam">🎥 Kamerayı sıfırla</button>
            <button class="btn btn-outline" id="v3-clear-reg">🧹 Tekrar bantlarını temizle</button>
        </div>
        <div class="v2-grid cols-2" style="margin-bottom:10px">
            <div class="v2-field"><label>Yayılım mesafesi <b class="control-value" id="v3-dist-val">6.0 km</b></label>
                <input type="range" id="v3-dist" min="0.1" max="20" step="0.1" value="6">
                <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                    <input class="v2-input" id="v3-dist-num" type="number" min="0.1" max="20" step="0.1" value="6" style="width:90px"> <span class="v2-muted">km</span>
                    <label class="v2-check"><input type="checkbox" id="v3-dist-link" checked> Ana ızgarayla eşitle</label>
                </div>
                <div class="v2-muted" id="v3-dist-read">—</div></div>
        </div>
        <div class="v2-grid cols-4" style="margin-bottom:10px">
            <div class="v2-field"><label>Bozulma bulutu eşiği <b class="control-value" id="v3-thr-val">0.15</b></label>
                <input type="range" id="v3-thr" min="0" max="1" step="0.01" value="0.15"></div>
            <div class="v2-field"><label>Dikey ölçek abartma <b class="control-value" id="v3-ex-val">oto</b></label>
                <input type="range" id="v3-ex" min="0" max="100" step="1" value="50">
                <label class="v2-check"><input type="checkbox" id="v3-ex-auto" checked> Otomatik</label></div>
            <div class="v2-field"><label>Işın tüpü rengi</label>
                <select id="v3-color"><option value="power">Güç kaybı (dB)</option><option value="score">Bozulma skoru</option><option value="w_lt">Uzun dönem ışın yarıçapı</option></select></div>
            <div class="v2-field"><label>Katmanlar</label>
                <label class="v2-check"><input type="checkbox" id="v3-show-cloud" checked> Bozulma bulutu</label>
                <label class="v2-check"><input type="checkbox" id="v3-show-beam" checked> Işın tüpü</label>
                <label class="v2-check"><input type="checkbox" id="v3-show-clip"> Lens yatay kesit düzlemi</label></div>
        </div>
        <div class="viz3d-wrap" id="v3-wrap">
            <canvas class="viz3d-gl" id="v3-canvas"></canvas>
            <div class="viz3d-msg" id="v3-msg" style="display:none"></div>
            <div class="viz3d-legend">
                <div>Bozulma skoru</div><div class="bar"></div><div class="ticks"><span>0</span><span>0.5</span><span>1</span></div>
                <div style="margin-top:4px">Eksenler: x = bağlantı (km), yukarı = irtifa, derinlik = yanal. Bulut/kutu gerçek ölçek; ışın tüpü abartılı.</div>
            </div>
            <div class="viz3d-inset left"><div class="cap">Terminal A · lens kesit</div><canvas id="v3-inset-A"></canvas></div>
            <div class="viz3d-inset right"><div class="cap">Terminal B · lens kesit</div><canvas id="v3-inset-B"></canvas></div>
        </div>
        <div class="v2-grid cols-2" style="margin-top:12px">
            <div class="v2-card"><h4>📊 Yayılım özeti</h4><div class="v2-stat" id="v3-summary"><span class="v2-muted" style="grid-column:1/-1">Henüz hesaplanmadı.</span></div>
                <div class="v2-tooltip" id="v3-hotspot">Hotspot: —</div></div>
            <div class="v2-card"><h4>🌫️ Anomali düzenleyici <span class="v2-muted">(en çok 8)</span></h4>
                <div class="v2-grid cols-2">
                    <div class="v2-field"><label>Tür</label><select id="an-type"></select></div>
                    <div class="v2-field"><label>Konum (bağlantı boyunca) <b class="control-value" id="an-pos-val">50 %</b></label><input type="range" id="an-pos" min="0" max="100" value="50"></div>
                    <div class="v2-field"><label>Uzunluk (m)</label><input class="v2-input" id="an-len" type="number" min="10" value="300"></div>
                    <div class="v2-field"><label>Genişlik / yükseklik (m)</label><input class="v2-input" id="an-wid" type="number" min="10" value="200"></div>
                    <div class="v2-field" style="grid-column:1/-1"><label>Şiddet <span id="an-unit">dB/km</span></label><input class="v2-input" id="an-str" type="number" value="60"></div>
                </div>
                <div class="v2-btn-row"><button class="btn btn-outline" id="an-add">➕ Anomali ekle</button><button class="btn btn-outline" id="an-clear">Temizle</button></div>
                <div class="v2-anom-list" id="an-list"></div>
            </div>
        </div>`;
    }

    const $ = (id) => document.getElementById(id);

    // ───────────────────────── three.js ─────────────────────────
    let renderer, scene, camera, controls, wrap, canvas;
    let gStatic, gBeam, gCloud, gHot, gAnom, gRegions, gLens;
    let cloudMat = null, clipPlane = null, clipHelper = null;
    let lensClipTargets = [];

    function clearGroup(g) {
        while (g.children.length) {
            const c = g.children[0];
            g.remove(c);
            c.traverse((o) => {
                if (o.geometry) o.geometry.dispose();
                if (o.material) {
                    if (o.material.map) o.material.map.dispose();
                    o.material.dispose();
                }
            });
        }
    }

    function label(text, color, scale) {
        const c = document.createElement('canvas');
        c.width = 512; c.height = 96;
        const x = c.getContext('2d');
        x.font = 'bold 38px Inter, Arial, sans-serif';
        x.textAlign = 'center'; x.textBaseline = 'middle';
        x.fillStyle = 'rgba(10,14,26,0.7)';
        const w = Math.min(500, x.measureText(text).width + 24);
        x.fillRect(256 - w / 2, 14, w, 68);
        x.fillStyle = color || '#e2e8f0';
        x.fillText(text, 256, 49);
        const tex = new THREE.CanvasTexture(c);
        const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
        const s = scale || 3;
        sp.scale.set(s, s * 96 / 512, 1);
        sp.renderOrder = 10;
        return sp;
    }

    function initThree() {
        wrap = $('v3-wrap'); canvas = $('v3-canvas');
        const msg = $('v3-msg');
        if (typeof THREE === 'undefined') {
            msg.style.display = 'flex';
            msg.innerHTML = '3B görünüm için three.js yüklenemedi (internet bağlantısı/CDN?). 2B görünümler ve diğer paneller çalışmaya devam eder.';
            log('warn', '3B görünüm: three.js yüklenemedi, 3B sahne devre dışı.');
            return false;
        }
        try {
            renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
        } catch (e) {
            msg.style.display = 'flex';
            msg.textContent = 'WebGL kullanılamıyor: 3B sahne gösterilemiyor.';
            log('warn', '3B görünüm: WebGL başlatılamadı.');
            return false;
        }
        renderer.setClearColor(0x05080f, 1);
        renderer.localClippingEnabled = true;
        scene = new THREE.Scene();
        camera = new THREE.PerspectiveCamera(50, 1, 0.05, 2000);
        scene.add(new THREE.AmbientLight(0xffffff, 0.6));
        const dl = new THREE.DirectionalLight(0xffffff, 0.8);
        dl.position.set(10, 30, 20);
        scene.add(dl);
        [gStatic, gBeam, gCloud, gHot, gAnom, gRegions, gLens] = [0, 1, 2, 3, 4, 5, 6].map(() => { const g = new THREE.Group(); scene.add(g); return g; });
        if (THREE.OrbitControls) {
            controls = new THREE.OrbitControls(camera, canvas);
            controls.enableDamping = true;
        } else {
            log('warn', '3B görünüm: OrbitControls yüklenemedi, kamera sabit.');
        }
        clipPlane = new THREE.Plane(new THREE.Vector3(0, -1, 0), 0);
        const resize = () => {
            const w = wrap.clientWidth, h = wrap.clientHeight;
            if (!w || !h) return;
            if (canvas.width !== Math.round(w * renderer.getPixelRatio()) || canvas.height !== Math.round(h * renderer.getPixelRatio())) {
                renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
                renderer.setSize(w, h, false);
                camera.aspect = w / h;
                camera.updateProjectionMatrix();
                if (cloudMat) cloudMat.uniforms.uScale.value = renderer.domElement.height / (2 * Math.tan(camera.fov * Math.PI / 360));
            }
        };
        const loop = () => {
            requestAnimationFrame(loop);
            if (!wrap.offsetParent) return; // sekme gizli
            resize();
            if (controls) controls.update();
            renderer.render(scene, camera);
        };
        loop();
        return true;
    }

    function resetCamera() {
        if (!camera) return;
        const b = (st.result && st.result.bounds) || { x: 3000, y: 600, z: 600 };
        const cx = b.x / U / 2, cy = b.z / U / 2, cz = b.y / U / 2;
        const d = Math.max(b.x / U * 0.95, 20);
        camera.position.set(cx - d * 0.2, cy + d * 0.45, cz + d * 0.85);
        if (controls) { controls.target.set(cx, cy * 0.6, cz); controls.update(); } else camera.lookAt(cx, cy, cz);
    }

    const P = (x, y, z) => new THREE.Vector3(x / U, z / U, y / U);

    // doğrultudan sapmayı abartan dönüşüm (yalnız ışın tüpü/merkez hattı)
    function makeTransform() {
        const m = st.result.meta;
        const s = m.source, t = m.target;
        const dx = t[0] - s[0], dy = t[1] - s[1], dz = t[2] - s[2];
        const L2 = dx * dx + dy * dy + dz * dz;
        const ex = st.exag;
        return (p) => {
            const u = ((p[0] - s[0]) * dx + (p[1] - s[1]) * dy + (p[2] - s[2]) * dz) / L2;
            const bx = s[0] + dx * u, by = s[1] + dy * u, bz = s[2] + dz * u;
            return P(bx + (p[0] - bx) * ex, by + (p[1] - by) * ex, bz + (p[2] - bz) * ex);
        };
    }

    function niceStep(range) {
        const raw = range / 8;
        const mag = Math.pow(10, Math.floor(Math.log10(raw)));
        const n = raw / mag;
        return (n < 1.5 ? 1 : n < 3.5 ? 2 : n < 7.5 ? 5 : 10) * mag;
    }

    function buildStatic() {
        clearGroup(gStatic);
        const b = st.result ? st.result.bounds : { x: 3000, y: 600, z: 600 };
        // atmosfer kutusu
        const box = new THREE.BoxGeometry(b.x / U, b.z / U, b.y / U);
        const edges = new THREE.LineSegments(new THREE.EdgesGeometry(box), new THREE.LineBasicMaterial({ color: 0x3b82f6, transparent: true, opacity: 0.45 }));
        edges.position.set(b.x / U / 2, b.z / U / 2, b.y / U / 2);
        gStatic.add(edges);
        box.dispose();
        // zemin ızgarası
        const step = niceStep(b.x);
        const pts = [];
        for (let x = 0; x <= b.x + 1e-6; x += step) pts.push(x / U, 0, 0, x / U, 0, b.y / U);
        for (let y = 0; y <= b.y + 1e-6; y += step) pts.push(0, 0, y / U, b.x / U, 0, y / U);
        const gg = new THREE.BufferGeometry();
        gg.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
        gStatic.add(new THREE.LineSegments(gg, new THREE.LineBasicMaterial({ color: 0x334155, transparent: true, opacity: 0.7 })));
        // eksen etiketleri (km)
        for (let x = 0; x <= b.x + 1e-6; x += step) {
            const l = label((x / 1000).toFixed(x % 1000 === 0 ? 0 : 1) + ' km', '#94a3b8', 2.4);
            l.position.set(x / U, 0, -0.9);
            gStatic.add(l);
        }
        const ly = label('yanal y (m)', '#94a3b8', 3); ly.position.set(-1.8, 0, b.y / U / 2); gStatic.add(ly);
        const lz = label('irtifa z (m)', '#94a3b8', 3); lz.position.set(-1.8, b.z / U, 0); gStatic.add(lz);
        const lx = label('bağlantı mesafesi x (km)', '#cbd5e1', 4.5); lx.position.set(b.x / U / 2, -0.6, -2.2); gStatic.add(lx);
        const ly1 = label(b.y.toFixed(0) + ' m', '#64748b', 1.8); ly1.position.set(0, 0, b.y / U + 0.6); gStatic.add(ly1);
        const lz1 = label(b.z.toFixed(0) + ' m', '#64748b', 1.8); lz1.position.set(0, b.z / U + 0.5, 0); gStatic.add(lz1);
        // terminaller + nominal hat
        if (st.result) {
            const m = st.result.meta;
            const a = P(m.source[0], m.source[1], m.source[2]);
            const c = P(m.target[0], m.target[1], m.target[2]);
            [['A', a, 0x3b82f6, -1], ['B', c, 0x8b5cf6, 1]].forEach(([nm, pos, col, side]) => {
                const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.5, 0.5), new THREE.MeshPhongMaterial({ color: col }));
                mesh.position.copy(pos).add(new THREE.Vector3(0.25 * side, 0, 0));
                gStatic.add(mesh);
                const l = label('Terminal ' + nm, '#e2e8f0', 3);
                l.position.copy(pos).add(new THREE.Vector3(0.25 * side, 1.1, 0));
                gStatic.add(l);
            });
            const lg = new THREE.BufferGeometry().setFromPoints([a, c]);
            const nominal = new THREE.Line(lg, new THREE.LineDashedMaterial({ color: 0x64748b, dashSize: 0.4, gapSize: 0.3 }));
            nominal.computeLineDistances();
            gStatic.add(nominal);
        }
    }

    function tValue(mode, cl, ref) {
        if (mode === 'score') return cl.score;
        if (mode === 'w_lt') return (cl.w_lt - ref.minW) / (ref.maxW - ref.minW + 1e-12);
        return -10 * Math.log10(Math.max(cl.power_frac, 1e-12)) / ref.lossRef;
    }

    function buildBeam() {
        clearGroup(gBeam);
        if (!st.result || !THREE.BufferGeometry) return;
        const res = st.result, cl = res.centerline, T = makeTransform();
        const mode = $('v3-color').value;
        const ws = cl.map((c) => c.w_lt);
        const ref = { minW: Math.min(...ws), maxW: Math.max(...ws), lossRef: Math.max(10, ...cl.map((c) => -10 * Math.log10(Math.max(c.power_frac, 1e-12)))) };
        // merkez hattı
        const lp = [], lc = [];
        cl.forEach((c) => {
            const v = T([c.x, c.y, c.z]);
            lp.push(v.x, v.y, v.z);
            lc.push(...scoreRGB(tValue(mode, c, ref)));
        });
        const lgm = new THREE.BufferGeometry();
        lgm.setAttribute('position', new THREE.Float32BufferAttribute(lp, 3));
        lgm.setAttribute('color', new THREE.Float32BufferAttribute(lc, 3));
        gBeam.add(new THREE.Line(lgm, new THREE.LineBasicMaterial({ vertexColors: true })));
        // tüp
        const rings = res.rings, idx = res.ring_sample_indices;
        if (rings.length < 2) return;
        const m = rings[0].length;
        const pos = [], col = [], ind = [];
        rings.forEach((ring, i) => {
            const rgb = scoreRGB(tValue(mode, cl[idx[i]], ref));
            ring.forEach((p) => { const v = T(p); pos.push(v.x, v.y, v.z); col.push(...rgb); });
        });
        for (let i = 0; i < rings.length - 1; i++) {
            for (let j = 0; j < m; j++) {
                const a = i * m + j, b = i * m + (j + 1) % m, c = (i + 1) * m + j, d = (i + 1) * m + (j + 1) % m;
                ind.push(a, c, b, b, c, d);
            }
        }
        const g = new THREE.BufferGeometry();
        g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
        g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
        g.setIndex(ind);
        gBeam.add(new THREE.Mesh(g, new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.38, side: THREE.DoubleSide, depthWrite: false })));
        gBeam.visible = $('v3-show-beam').checked;
    }

    function buildCloud() {
        clearGroup(gCloud);
        cloudMat = null;
        if (!st.result) return;
        const vox = st.result.degradation_cloud.voxels;
        if (!vox.length) return;
        const pos = new Float32Array(vox.length * 3), col = new Float32Array(vox.length * 3), sc = new Float32Array(vox.length);
        vox.forEach((v, i) => {
            pos[3 * i] = v.x / U; pos[3 * i + 1] = v.z / U; pos[3 * i + 2] = v.y / U;
            const rgb = scoreRGB(v.score);
            col.set(rgb, 3 * i);
            sc[i] = v.score;
        });
        const g = new THREE.BufferGeometry();
        g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
        g.setAttribute('aColor', new THREE.BufferAttribute(col, 3));
        g.setAttribute('aScore', new THREE.BufferAttribute(sc, 1));
        cloudMat = new THREE.ShaderMaterial({
            transparent: true, depthWrite: false,
            uniforms: {
                uThreshold: { value: parseFloat($('v3-thr').value) },
                uSize: { value: Math.max(st.result.degradation_cloud.cell_size / U * 1.1, 0.4) },
                uScale: { value: renderer.domElement.height / (2 * Math.tan(camera.fov * Math.PI / 360)) },
            },
            vertexShader: `attribute vec3 aColor; attribute float aScore; uniform float uScale; uniform float uSize; uniform float uThreshold;
                varying vec3 vColor; varying float vA;
                void main(){ vColor = aColor; vA = aScore >= uThreshold ? clamp(0.12 + 0.88 * aScore, 0.0, 1.0) * 0.6 : 0.0;
                  vec4 mv = modelViewMatrix * vec4(position, 1.0);
                  gl_PointSize = vA > 0.0 ? clamp(uSize * uScale / max(-mv.z, 0.01), 1.0, 160.0) : 0.0;
                  gl_Position = projectionMatrix * mv; }`,
            fragmentShader: `varying vec3 vColor; varying float vA;
                void main(){ float r = length(gl_PointCoord - vec2(0.5)) * 2.0; if (r > 1.0 || vA <= 0.0) discard;
                  gl_FragColor = vec4(vColor, vA * (1.0 - 0.6 * r * r)); }`,
        });
        gCloud.add(new THREE.Points(g, cloudMat));
        gCloud.visible = $('v3-show-cloud').checked;
    }

    function buildHotspot() {
        clearGroup(gHot);
        if (!st.result) return;
        const hs = st.result.degradation_cloud.hotspot;
        if (!hs) return;
        const T = makeTransform();
        const pos = T([hs.x, hs.y, hs.z]);
        const ring = new THREE.Mesh(new THREE.TorusGeometry(0.7, 0.07, 8, 32), new THREE.MeshBasicMaterial({ color: 0xf43f5e }));
        ring.position.copy(pos);
        ring.rotation.y = Math.PI / 2;
        gHot.add(ring);
        const l = label(`Hotspot · ${(hs.s / 1000).toFixed(2)} km · skor ${hs.score.toFixed(2)}`, '#fda4af', 5.5);
        l.position.copy(pos).add(new THREE.Vector3(0, 1.6, 0));
        gHot.add(l);
    }

    function buildAnomalies() {
        clearGroup(gAnom);
        st.anomalies.forEach((a) => {
            const sz = a.size;
            const g = new THREE.BoxGeometry(sz[0] / U, sz[2] / U, sz[1] / U);
            const col = ANOM_TYPES[a.type].color;
            const mesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.18, depthWrite: false }));
            const e = new THREE.LineSegments(new THREE.EdgesGeometry(g), new THREE.LineBasicMaterial({ color: col }));
            const m = st.result ? st.result.meta : null;
            const c = m ? [m.source[0] + (m.target[0] - m.source[0]) * a.pos, m.source[1], m.source[2]] : [0, 0, 0];
            [mesh, e].forEach((o) => { o.position.copy(P(c[0], c[1], c[2])); gAnom.add(o); });
        });
    }

    function buildRegions() {
        clearGroup(gRegions);
        if (!st.result || !st.regions || !st.regions.length) return;
        const m = st.result.meta;
        const s = m.source, t = m.target;
        const L = Math.hypot(t[0] - s[0], t[1] - s[1], t[2] - s[2]);
        const dir = new THREE.Vector3((t[0] - s[0]) / U, (t[2] - s[2]) / U, (t[1] - s[1]) / U).normalize();
        const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(1, 0, 0), dir);
        const maxR = Math.max(1, ...st.regions.map((r) => r.sampled_retries));
        st.regions.forEach((r) => {
            const len = (r.s_end - r.s_start) / U, mid = (r.s_start + r.s_end) / 2 / L;
            const tt = clamp(r.sampled_retries / maxR, 0, 1);
            const rgb = scoreRGB(tt);
            const g = new THREE.BoxGeometry(len * 0.98, 1.5, 1.5);
            const mesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: new THREE.Color(rgb[0], rgb[1], rgb[2]), transparent: true, opacity: 0.12 + 0.4 * tt, depthWrite: false }));
            const c = P(s[0] + (t[0] - s[0]) * mid, s[1] + (t[1] - s[1]) * mid, s[2] + (t[2] - s[2]) * mid);
            mesh.position.copy(c); mesh.quaternion.copy(q);
            gRegions.add(mesh);
            const l = label(`${r.sampled_retries} tekrar`, '#fde68a', 2.6);
            l.position.copy(c).add(new THREE.Vector3(0, 1.4, 0));
            gRegions.add(l);
        });
    }

    // lens: LatheGeometry (dönel gövde) + yatay kesit çizgileri ve ışın demeti
    function buildLenses() {
        clearGroup(gLens);
        lensClipTargets = [];
        clipHelper = null;
        if (!st.result) return;
        const m = st.result.meta;
        [['A', m.source, 1], ['B', m.target, -1]].forEach(([key, pt, dir]) => {
            const T = S.terminals[key];
            const res = T && T.design;
            const base = P(pt[0], pt[1], pt[2]).add(new THREE.Vector3(0.5 * dir, 0, 0));
            if (!res) return;
            if (res.stack) { buildStackLens(key, base, res.stack); return; }
            const d = res.design, fan = res.ray_fan;
            const s = Math.min(0.9 / d.diameter_m, 4 / (fan.z_end - fan.z_start));
            const half = d.profile.filter((p) => p[0] >= -1e-12);
            const pts = [];
            half.forEach((p) => pts.push(new THREE.Vector2(Math.max(p[0], 0) * s, p[1] * s)));
            for (let i = half.length - 1; i >= 0; i--) pts.push(new THREE.Vector2(Math.max(half[i][0], 0) * s, half[i][2] * s));
            const geo = new THREE.LatheGeometry(pts, 64);
            const mat = new THREE.MeshPhongMaterial({ color: 0x67e8f9, transparent: true, opacity: 0.55, side: THREE.DoubleSide, shininess: 90, clippingPlanes: [], clipShadows: true });
            const mesh = new THREE.Mesh(geo, mat);
            mesh.rotation.z = -Math.PI / 2; // ışık +x yönünde ilerler: ön yüz her iki terminalde de A tarafına bakar
            mesh.position.copy(base);
            gLens.add(mesh);
            lensClipTargets.push({ mat, y: base.y });
            // yatay kesit: profil konturu + ışınlar (y = lens ekseni yüksekliği düzleminde)
            const sec = new THREE.Group();
            const X = (z) => base.x + z * s, Z = (y) => base.z + y * s;
            const outline = [];
            d.profile.forEach((p) => outline.push(new THREE.Vector3(X(p[1]), base.y, Z(p[0]))));
            for (let i = d.profile.length - 1; i >= 0; i--) outline.push(new THREE.Vector3(X(d.profile[i][2]), base.y, Z(d.profile[i][0])));
            outline.push(outline[0].clone());
            sec.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(outline), new THREE.LineBasicMaterial({ color: 0x22d3ee })));
            fan.rays.forEach((r) => {
                const rp = r.points.map((p) => new THREE.Vector3(X(p[0]), base.y, Z(p[1])));
                sec.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(rp), new THREE.LineBasicMaterial({ color: 0xfacc15 })));
            });
            if (fan.focus_z !== null && fan.focus_z !== undefined) {
                const fm = new THREE.Mesh(new THREE.SphereGeometry(0.09, 12, 12), new THREE.MeshBasicMaterial({ color: 0xf43f5e }));
                fm.position.set(X(fan.focus_z), base.y, Z(0));
                sec.add(fm);
                const fl = label(`F (${(d.focal_length_m * 1000).toFixed(0)} mm)`, '#fda4af', 2.2);
                fl.position.set(X(fan.focus_z), base.y + 0.5, Z(0));
                sec.add(fl);
            }
            const ll = label(`Lens ${key}: ${res.design.material}, D=${(d.diameter_m * 1000).toFixed(0)} mm`, '#a5f3fc', 4);
            ll.position.set(base.x, base.y + 1.0, base.z + 0.3);
            sec.add(ll);
            sec.userData.isSection = true;
            gLens.add(sec);
        });
        // yatay kesit düzlemi (translucent)
        const yc = P(m.source[0], m.source[1], m.source[2]).y;
        clipPlane.constant = yc;
        clipHelper = new THREE.Mesh(new THREE.PlaneGeometry(14, 4), new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.08, side: THREE.DoubleSide, depthWrite: false }));
        clipHelper.rotation.x = -Math.PI / 2;
        clipHelper.position.set(P(m.source[0], 0, 0).x + 5, yc, P(0, m.source[1], 0).z);
        gLens.add(clipHelper);
        applyClip();
    }

    // lens yığını: her eleman için LatheGeometry (mutlak z konumunda), yatay kesit çizgileri ve tüm yığından geçen ışın demeti
    function buildStackLens(key, base, r) {
        const fan = r.ray_fan, sys = r.system, els = r.elements;
        const maxD = Math.max(...els.map((e) => e.diameter_m));
        const s = Math.min(0.9 / maxD, 4 / (fan.z_end - fan.z_start));
        const shades = [0x67e8f9, 0x93c5fd, 0xa5b4fc, 0x86efac, 0xfde68a, 0xfda4af, 0xc4b5fd, 0x99f6e4];
        const sec = new THREE.Group();
        const X = (z) => base.x + z * s, Z = (y) => base.z + y * s;
        els.forEach((e, i) => {
            const half = e.profile.filter((p) => p[0] >= -1e-12);
            const pts = [];
            half.forEach((p) => pts.push(new THREE.Vector2(Math.max(p[0], 0) * s, p[1] * s)));
            for (let k = half.length - 1; k >= 0; k--) pts.push(new THREE.Vector2(Math.max(half[k][0], 0) * s, half[k][2] * s));
            const mat = new THREE.MeshPhongMaterial({
                color: e.is_stop ? 0xfbbf24 : shades[i % shades.length], transparent: true, opacity: 0.5,
                side: THREE.DoubleSide, shininess: 90, clippingPlanes: [], clipShadows: true,
            });
            const mesh = new THREE.Mesh(new THREE.LatheGeometry(pts, 64), mat);
            mesh.rotation.z = -Math.PI / 2;
            mesh.position.copy(base);
            gLens.add(mesh);
            lensClipTargets.push({ mat, y: base.y });
            // yatay kesit konturu
            const outline = [];
            e.profile.forEach((p) => outline.push(new THREE.Vector3(X(p[1]), base.y, Z(p[0]))));
            for (let k = e.profile.length - 1; k >= 0; k--) outline.push(new THREE.Vector3(X(e.profile[k][2]), base.y, Z(e.profile[k][0])));
            outline.push(outline[0].clone());
            sec.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(outline),
                new THREE.LineBasicMaterial({ color: e.is_stop ? 0xf59e0b : (e.focal_length_m < 0 ? 0xa855f7 : 0x22d3ee) })));
        });
        const addRays = (rays, color) => rays.forEach((ray) => {
            const rp = ray.points.map((p) => new THREE.Vector3(X(p[0]), base.y, Z(p[1])));
            sec.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(rp), new THREE.LineBasicMaterial({ color })));
        });
        addRays(fan.rays, 0xfacc15);
        if (fan.rays_field && fan.rays_field.length) addRays(fan.rays_field, 0x22d3ee);
        if (fan.focus_z !== null && fan.focus_z !== undefined) {
            const fm = new THREE.Mesh(new THREE.SphereGeometry(0.09, 12, 12), new THREE.MeshBasicMaterial({ color: 0xf43f5e }));
            fm.position.set(X(fan.focus_z), base.y, Z(0));
            sec.add(fm);
            const fl = label(`F′ (${sys.efl_m === null ? '' : (sys.efl_m * 1000).toFixed(0) + ' mm'})`, '#fda4af', 2.2);
            fl.position.set(X(fan.focus_z), base.y + 0.5, Z(0));
            sec.add(fl);
        }
        const efl = sys.afocal ? `afokal ×${Math.abs(sys.beam_magnification).toFixed(2)}` : `EFL ${(sys.efl_m * 1000).toFixed(0)} mm`;
        const ll = label(`Lens ${key}: yığın ${els.length} lens, ${efl}`, '#a5f3fc', 4);
        ll.position.set(base.x, base.y + 1.0, base.z + 0.3);
        sec.add(ll);
        sec.userData.isSection = true;
        gLens.add(sec);
    }

    function applyClip() {
        const on = $('v3-show-clip').checked;
        lensClipTargets.forEach((t) => { t.mat.clippingPlanes = on ? [clipPlane] : []; t.mat.needsUpdate = true; });
        gLens.children.forEach((c) => { if (c.userData.isSection) c.visible = on; });
        if (clipHelper) clipHelper.visible = on;
    }

    function drawInsets() {
        ['A', 'B'].forEach((k) => {
            const c = $('v3-inset-' + k);
            if (c && window.LensSection) {
                const T = S.terminals[k];
                window.LensSection.draw(c, T && T.design, { compact: true, emptyText: T && T.error ? 'Tasarım yok' : 'Lens bekleniyor…' });
            }
        });
    }

    function rebuildAll() {
        if (!st.three) return;
        buildStatic(); buildBeam(); buildCloud(); buildHotspot(); buildAnomalies(); buildRegions(); buildLenses();
    }

    // ───────────────────────── istek / sonuç ─────────────────────────
    const MAX_CELLS = 40000;
    function mainDistKm() {
        const p = app().getParams();
        return clamp(Math.round(((p.gridX || 60) * (p.cellSize || 100)) / 100) / 10, 0.1, 20);
    }
    function currentDistKm() { return st.linked ? mainDistKm() : st.distKm; }
    // Seçilen mesafe için ızgara (arka uçtaki _grid_for_distance ile aynı kural)
    function gridFor(distM, p) {
        let gy = 6, gz = Math.min(p.gridZ || 10, 10);
        const gxMax = Math.min(600, Math.floor(MAX_CELLS / (gy * gz)));
        const gx = clamp(Math.round(distM / (p.cellSize || 100)), 20, gxMax);
        return { gx, gy, gz, cell: distM / gx };
    }
    function updateReadout() {
        const el = $('v3-dist-read');
        if (!el) return;
        const p = app().getParams(), d = currentDistKm() * 1000, g = gridFor(d, p);
        el.textContent = `Izgara ${g.gx}×${g.gy}×${g.gz}, hücre ${g.cell.toFixed(g.cell < 10 ? 1 : 0)} m` + (st.linked ? ' · ana ızgara' : '');
    }
    function setDistUI(km) {
        $('v3-dist').value = String(km); $('v3-dist-num').value = String(km);
        $('v3-dist-val').textContent = km.toFixed(1) + ' km';
        updateReadout();
    }
    function scheduleRun() {
        clearTimeout(st.timer);
        st.timer = setTimeout(() => { if ($('v3-run') && !$('v3-run').disabled) run(); }, 400);
    }
    function buildRequest() {
        const p = app().getParams();
        const distM = Math.round(currentDistKm() * 1000);
        const { gx, gy, gz, cell } = gridFor(distM, p);
        const atmosphere = {
            gridX: gx, gridY: gy, gridZ: gz, cellSize: cell,
            thermalProfile: p.thermalProfile || 'gradient', baseTemp: p.baseTemp ?? 25, deltaT: p.deltaT ?? 15,
            humidity: p.humidity ?? 50, weather: p.weather || 'clear', Cn2Level: p.Cn2Level || 'moderate',
            windSpeed: p.windSpeed ?? 5, wavelengthNm: p.wavelengthNm || 1550,
        };
        const src = [0, gy * cell / 2, gz * cell / 2], tgt = [gx * cell, gy * cell / 2, gz * cell / 2];
        const B = S.terminals.B && S.terminals.B.design;
        const body = {
            atmosphere, distanceM: distM, nSamples: 160, nRays: 16, maxVoxels: 2000,
            anomalies: st.anomalies.map((a) => {
                const o = { center: [src[0] + (tgt[0] - src[0]) * a.pos, src[1], src[2]], size: a.size, cn2_factor: 1, extra_alpha_dB_km: 0, delta_T: 0 };
                o[ANOM_TYPES[a.type].key] = a.strength;
                return o;
            }),
        };
        if (B) body.rxRadiusM = clamp(B.design.diameter_m / 2, 1e-4, 10);
        return body;
    }

    async function run() {
        const btn = $('v3-run');
        btn.disabled = true; btn.textContent = '⏳ Hesaplanıyor…';
        const body = buildRequest();
        const r = await window.FSOApi('/api/propagation3d', body);
        btn.disabled = false; btn.textContent = '🛰️ 3B Yayılımı Hesapla';
        if (!r.ok) {
            log('error', '3B yayılım hatası: ' + r.error);
            $('v3-summary').innerHTML = `<div class="v2-error" style="grid-column:1/-1">${esc(r.error)}</div>`;
            return null;
        }
        st.request = body;
        st.result = r.data;
        const mw = Math.max(...r.data.centerline.map((c) => c.w_lt));
        if (st.auto) setExag(clamp(0.3 * U / mw, 1, 20000), true);
        rebuildAll();
        const L = r.data.bounds.x;
        if (!st.cameraSet || st.lastLen !== L) { resetCamera(); st.cameraSet = true; st.lastLen = L; }
        showSummary(r.data);
        const sm = r.data.summary;
        log('info', `🛰️ 3B yayılım: kayıp ${sm.power_at_rx_dB.toFixed(1)} dB, hotspot ${(r.data.degradation_cloud.hotspot.s / 1000).toFixed(2)} km, ${r.data.degradation_cloud.n_voxels} voksel.`);
        bus.dispatchEvent(new CustomEvent('propagation-ready', { detail: r.data }));
        return r.data;
    }

    function showSummary(d) {
        const s = d.summary, h = d.degradation_cloud.hotspot;
        const rows = [
            ['Link uzunluğu', (s.length_m / 1000).toFixed(2) + ' km'], ['Alıcıda güç kaybı', s.power_at_rx_dB.toFixed(2) + ' dB'],
            ['Toplam sönüm', s.total_atten_dB.toFixed(2) + ' dB'], ['Işın yarıçapı (w_LT)', (s.final_w_lt * 100).toFixed(2) + ' cm'],
            ['Sapma / dalgalanma', `${(s.deflection_m * 100).toFixed(1)} / ${(s.wander_m * 100).toFixed(1)} cm`], ['Sintilasyon σ²', s.scint_index.toExponential(2)],
            ['En kötü dilim', `${(s.worst_segment.s_start / 1000).toFixed(2)}–${(s.worst_segment.s_end / 1000).toFixed(2)} km`], ['Voksel sayısı', String(d.degradation_cloud.n_voxels)],
        ];
        $('v3-summary').innerHTML = rows.map(([a, b]) => `<span>${a}</span><b>${esc(b)}</b>`).join('');
        $('v3-hotspot').innerHTML = `<b>Hotspot</b> @ ${(h.s / 1000).toFixed(2)} km, skor ${h.score.toFixed(2)} (${esc(h.dominant)}): ${esc(h.reason_tr)}`;
    }

    // ───────────────────────── etkileşim ─────────────────────────
    function exagFromSlider() { return Math.pow(10, parseFloat($('v3-ex').value) / 100 * 4.3); }
    function setExag(v, fromAuto) {
        st.exag = v;
        $('v3-ex-val').textContent = (st.auto ? 'oto ' : '') + '×' + (v >= 100 ? v.toFixed(0) : v.toFixed(1));
        if (fromAuto) $('v3-ex').value = String(Math.round(Math.log10(v) / 4.3 * 100));
    }

    function renderAnomList() {
        const el = $('an-list');
        el.innerHTML = st.anomalies.length ? '' : '<div class="v2-muted">Anomali yok.</div>';
        st.anomalies.forEach((a, i) => {
            const t = ANOM_TYPES[a.type];
            const d = document.createElement('div');
            d.className = 'v2-anom-item';
            d.innerHTML = `<span>${t.name} · %${(a.pos * 100).toFixed(0)} · ${a.size[0]}×${a.size[1]} m · ${a.strength} ${t.unit}</span>`;
            const b = document.createElement('button');
            b.textContent = '✕'; b.title = 'Sil';
            b.addEventListener('click', () => { st.anomalies.splice(i, 1); renderAnomList(); });
            d.appendChild(b);
            el.appendChild(d);
        });
    }

    function bindUI() {
        const sel = $('an-type');
        Object.entries(ANOM_TYPES).forEach(([k, v]) => { const o = document.createElement('option'); o.value = k; o.textContent = v.name; sel.appendChild(o); });
        const syncType = () => { const t = ANOM_TYPES[sel.value]; $('an-unit').textContent = t.unit; $('an-str').value = t.def; };
        sel.addEventListener('change', syncType); syncType();
        $('an-pos').addEventListener('input', () => { $('an-pos-val').textContent = $('an-pos').value + ' %'; });
        $('an-add').addEventListener('click', () => {
            if (st.anomalies.length >= 8) { log('warn', 'En çok 8 anomali eklenebilir.'); return; }
            const t = ANOM_TYPES[sel.value];
            const strength = clamp(parseFloat($('an-str').value) || 0, t.min, t.max);
            const len = Math.max(10, parseFloat($('an-len').value) || 300), wid = Math.max(10, parseFloat($('an-wid').value) || 200);
            st.anomalies.push({ type: sel.value, pos: parseFloat($('an-pos').value) / 100, size: [len, wid, wid], strength });
            renderAnomList();
            log('info', `🌫️ Anomali eklendi: ${t.name}. Etkiyi görmek için yayılımı yeniden hesaplayın.`);
        });
        $('an-clear').addEventListener('click', () => { st.anomalies = []; renderAnomList(); });
        renderAnomList();
        $('v3-run').addEventListener('click', run);
        const onDist = (km) => {
            km = clamp(Math.round(km * 10) / 10 || 0.1, 0.1, 20);
            st.linked = false; $('v3-dist-link').checked = false; st.distKm = km;
            setDistUI(km); scheduleRun();
        };
        $('v3-dist').addEventListener('input', () => onDist(parseFloat($('v3-dist').value)));
        $('v3-dist-num').addEventListener('change', () => onDist(parseFloat($('v3-dist-num').value)));
        $('v3-dist-link').addEventListener('change', () => {
            st.linked = $('v3-dist-link').checked;
            if (st.linked) setDistUI(mainDistKm()); else { st.distKm = parseFloat($('v3-dist').value) || st.distKm; updateReadout(); }
            scheduleRun();
        });
        ['grid-x', 'cell-size'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('input', () => { if (st.linked) { setDistUI(mainDistKm()); scheduleRun(); } });
        });
        st.distKm = mainDistKm(); setDistUI(st.distKm);
        $('v3-reset-cam').addEventListener('click', resetCamera);
        $('v3-clear-reg').addEventListener('click', () => { st.regions = null; if (st.three) buildRegions(); });
        $('v3-thr').addEventListener('input', () => {
            $('v3-thr-val').textContent = parseFloat($('v3-thr').value).toFixed(2);
            if (cloudMat) cloudMat.uniforms.uThreshold.value = parseFloat($('v3-thr').value);
        });
        $('v3-ex').addEventListener('input', () => { st.auto = false; $('v3-ex-auto').checked = false; setExag(exagFromSlider(), false); if (st.three) buildBeam(); buildHotspot && st.three && buildHotspot(); });
        $('v3-ex-auto').addEventListener('change', () => {
            st.auto = $('v3-ex-auto').checked;
            if (st.auto && st.result) {
                const mw = Math.max(...st.result.centerline.map((c) => c.w_lt));
                setExag(clamp(0.3 * U / mw, 1, 20000), true);
                if (st.three) { buildBeam(); buildHotspot(); }
            } else setExag(exagFromSlider(), false);
        });
        $('v3-color').addEventListener('change', () => { if (st.three) buildBeam(); });
        $('v3-show-cloud').addEventListener('change', () => { gCloud && (gCloud.visible = $('v3-show-cloud').checked); });
        $('v3-show-beam').addEventListener('change', () => { gBeam && (gBeam.visible = $('v3-show-beam').checked); });
        $('v3-show-clip').addEventListener('change', applyClip);
        setExag(1, false);
    }

    function initTabs() {
        const bar = $('v2-tabs');
        if (!bar) return;
        bar.addEventListener('click', (e) => {
            const b = e.target.closest('.tab-btn');
            if (!b) return;
            bar.querySelectorAll('.tab-btn').forEach((x) => x.classList.toggle('active', x === b));
            document.querySelectorAll('.v2-tab-pane').forEach((p) => p.classList.toggle('active', p.id === 'v2-pane-' + b.dataset.tab));
            if (b.dataset.tab === 'prop3d') setTimeout(drawInsets, 30);
            bus.dispatchEvent(new CustomEvent('v2-tab', { detail: b.dataset.tab }));
        });
    }

    function init() {
        const pane = $('v2-pane-prop3d');
        if (!pane) return;
        buildUI(pane);
        initTabs();
        st.three = initThree();
        bindUI();
        drawInsets();
        bus.addEventListener('lens-changed', () => { drawInsets(); if (st.three && st.result) buildLenses(); });
        window.addEventListener('resize', drawInsets);
        setTimeout(() => { run(); }, 500); // ilk açılışta otomatik hesapla
    }

    window.Viz3D = {
        buildRequest,
        run,
        getResult: () => st.result,
        setRegions(regions, title) {
            st.regions = regions || null; st.regionsTitle = title || '';
            if (st.three) buildRegions();
        },
        clearRegions() { this.setRegions(null); },
    };
    document.addEventListener('DOMContentLoaded', init);
})();
