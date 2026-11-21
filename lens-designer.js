/**
 * Lens Tasarımcısı (v2)
 * ---------------------
 * Backend: GET /api/lens/catalog, POST /api/lens/design, POST /api/lens/stack, GET /api/lens/stack/presets
 * Mod "Lens yığını": birden fazla lens seri (üst üste) eklenir; T.mode === 'stack' iken T.design, duplex/3B
 * ile uyumlu bir kılıftır ({design:{material, diameter_m = 2×giriş gözbebeği}, total_transmittance, stack: ham sonuç}).
 * window.FSOLensDesigner.getTerminalLens('A') → {mode, rxRadiusM, transmittance, efl_m, bfl_m, fNumber, ...}.
 * Terminal A ve B için ayrı lens tasarımı tutar (window.FSOState.terminals), sonucu
 * 'lens-changed' olayı ile (window.FSOBus) 3B görünüm ve full-duplex paneline iletir.
 * Ayrıca gerçek bir lens kesiti çizen window.LensSection.draw(canvas, sonuç) sağlar.
 */
(function () {
    'use strict';

    const bus = (window.FSOBus = window.FSOBus || new EventTarget());
    const S = (window.FSOState = window.FSOState || {});
    S.terminals = S.terminals || {
        A: { wavelengthNm: 1550 },
        B: { wavelengthNm: 1310 },
    };
    const MAX_STACK = 8;
    const defaultElement = (o) => Object.assign({
        shape: 'plano_convex', material: 'n_bk7', f_mm: 100, D_mm: 25, tcAuto: true, tc_mm: 5,
        coating: 'none', filter: 'none', gap_mm: 20,
    }, o || {});
    function defaultStack() {
        return { preset: '', fieldDeg: 0, elements: [defaultElement(), defaultElement({ f_mm: 150 })] };
    }
    const DEFAULT_LENS = () => ({
        shape: 'plano_convex', material: 'n_bk7', filter: 'none', coating: 'none',
        f_mm: 100, D_mm: 50, tcAuto: true, tc_mm: 5, design: null, error: null,
        mode: 'single', stack: defaultStack(), stackResult: null,
    });
    ['A', 'B'].forEach((k) => { S.terminals[k] = Object.assign(DEFAULT_LENS(), S.terminals[k]); });

    const app = () => window.FSOApp || { log() {}, BACKEND_URL: window.location.origin, getParams: () => ({}) };
    const log = (t, m) => app().log(t, m);
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

    async function api(path, body) {
        const opt = body === undefined ? {} : {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        };
        let res;
        try {
            res = await fetch(app().BACKEND_URL + path, opt);
        } catch (e) {
            return { ok: false, error: 'Backend\'e ulaşılamadı (Python sunucusu çalışıyor mu?)' };
        }
        let data = null;
        try { data = await res.json(); } catch (e) { /* yoksay */ }
        if (!res.ok) {
            let msg = (data && data.detail) || ('HTTP ' + res.status);
            if (Array.isArray(msg)) msg = msg.map((m) => (m.loc ? m.loc.slice(1).join('.') + ': ' : '') + m.msg).join('; ');
            return { ok: false, error: String(msg) };
        }
        return { ok: true, data };
    }
    window.FSOApi = api;

    const fmtMm = (m) => {
        if (m === null || m === undefined || !isFinite(m) || Math.abs(m) >= 1e3) return '∞';
        if (Math.abs(m) >= 1) return m.toFixed(2) + ' m';
        return (m * 1000).toFixed(Math.abs(m) < 0.01 ? 3 : 2) + ' mm';
    };
    const fmtDb = (x) => (x >= 0 ? '+' : '') + x.toFixed(2) + ' dB';

    // ───────────────────────── Lens kesit çizimi ─────────────────────────
    function prepCanvas(canvas) {
        const dpr = window.devicePixelRatio || 1;
        const w = canvas.clientWidth || 300;
        const h = canvas.clientHeight || 150;
        if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
            canvas.width = Math.round(w * dpr);
            canvas.height = Math.round(h * dpr);
        }
        const ctx = canvas.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);
        return { ctx, w, h };
    }

    /**
     * res: /api/lens/design sonucu. opts.compact: etiketleri azalt. opts.title: üst etiket.
     * Yatay eksen z (optik eksen), dikey y. Eksen ölçekleri farklı olabilir (alt notta belirtilir).
     */
    function drawLensSection(canvas, res, opts) {
        opts = opts || {};
        if (res && res.stack) return drawStackSection(canvas, res.stack, opts);
        const { ctx, w, h } = prepCanvas(canvas);
        ctx.font = '10px Inter, sans-serif';
        if (!res || !res.design) {
            ctx.fillStyle = '#64748b';
            ctx.textAlign = 'center';
            ctx.fillText(opts.emptyText || 'Lens tasarımı bekleniyor…', w / 2, h / 2);
            return;
        }
        const d = res.design;
        const fan = res.ray_fan;
        const hh = d.diameter_m / 2;
        const zmin = fan.z_start;
        const zmax = fan.z_end;
        // üst etiketler: parçalar ölçülür, genişliğe göre ayrı satırlara sarılır (üst üste binmez)
        const n = d.n_used;
        const tokens = opts.compact
            ? [`f=${fmtMm(d.focal_length_m)}`, `D=${fmtMm(d.diameter_m)}`]
            : [`R1=${fmtMm(d.R1_m)}`, `R2=${fmtMm(d.R2_m)}`, `t_c=${fmtMm(d.center_thickness_m)}`, `t_e=${fmtMm(d.edge_thickness_m)}`,
               `f=${fmtMm(d.focal_length_m)}`, `D=${fmtMm(d.diameter_m)}`, `n(${Math.round(d.wavelength_m * 1e9)} nm)=${n.toFixed(4)}`];
        const lineH = 12, gap = ctx.measureText('  ').width, maxW = w - 12;
        const lines = [];
        let cur = '';
        tokens.forEach((t) => {
            const cand = cur ? cur + '  ' + t : t;
            if (cur && ctx.measureText(cand).width > maxW) { lines.push(cur); cur = t; } else cur = cand;
        });
        if (cur) lines.push(cur);
        const labelH = 4 + lines.length * lineH;
        const padL = 8, padR = 8, padT = Math.max(opts.compact ? 14 : 20, labelH + 4), padB = opts.compact ? 14 : 26;
        const sx = (w - padL - padR) / (zmax - zmin);
        const sy = (h - padT - padB) / (2 * hh * 1.08);
        const X = (z) => padL + (z - zmin) * sx;
        const Y = (y) => padT + (h - padT - padB) / 2 - y * sy;
        // optik eksen
        ctx.strokeStyle = 'rgba(148,163,184,0.35)';
        ctx.setLineDash([4, 4]);
        ctx.beginPath(); ctx.moveTo(padL, Y(0)); ctx.lineTo(w - padR, Y(0)); ctx.stroke();
        ctx.setLineDash([]);
        // lens gövdesi (profil: y, z_front, z_back)
        const prof = d.profile;
        ctx.beginPath();
        prof.forEach((p, i) => { const x = X(p[1]), y = Y(p[0]); if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
        for (let i = prof.length - 1; i >= 0; i--) ctx.lineTo(X(prof[i][2]), Y(prof[i][0]));
        ctx.closePath();
        ctx.fillStyle = 'rgba(6,182,212,0.22)';
        ctx.fill();
        ctx.strokeStyle = '#06b6d4';
        ctx.lineWidth = 1.4;
        ctx.stroke();
        // ışın demeti
        ctx.lineWidth = 1;
        fan.rays.forEach((r) => {
            const t = Math.abs(r.y_in) / hh;
            ctx.strokeStyle = `rgba(${Math.round(250 - 40 * t)},${Math.round(204 - 70 * t)},${Math.round(21 + 40 * t)},0.9)`;
            ctx.beginPath();
            r.points.forEach((p, i) => { const x = X(p[0]), y = Y(p[1]); if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
            ctx.stroke();
        });
        // sanal odak için kesikli uzantı (ıraksak lens)
        if (fan.focus_z !== null && fan.focus_z !== undefined && fan.focus_z < d.center_thickness_m) {
            ctx.setLineDash([3, 3]);
            ctx.strokeStyle = 'rgba(250,204,21,0.5)';
            fan.rays.forEach((r) => {
                const p = r.points[r.points.length - 2];
                ctx.beginPath(); ctx.moveTo(X(p[0]), Y(p[1])); ctx.lineTo(X(fan.focus_z), Y(0)); ctx.stroke();
            });
            ctx.setLineDash([]);
        }
        // odak noktası
        if (fan.focus_z !== null && fan.focus_z !== undefined) {
            const fx = X(fan.focus_z), fy = Y(0);
            ctx.fillStyle = '#f43f5e';
            ctx.beginPath(); ctx.arc(fx, fy, 3.5, 0, 6.2832); ctx.fill();
            ctx.fillStyle = '#fda4af';
            ctx.textAlign = 'center';
            ctx.fillText(res.design.focal_length_m < 0 ? 'F (sanal)' : 'F', fx, fy - 8);
        }
        // etiketler (üst-sol blok, yarı saydam zemin üzerinde)
        ctx.fillStyle = 'rgba(10,14,26,0.7)';
        ctx.fillRect(2, 2, Math.min(w - 4, Math.max(...lines.map((t) => ctx.measureText(t).width)) + 8), labelH);
        ctx.fillStyle = '#cbd5e1';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'alphabetic';
        lines.forEach((t, i) => ctx.fillText(t, 6, 3 + lineH * (i + 1) - 3));
        // D ölçü çizgisi
        const xd = X(0) - 5;
        ctx.strokeStyle = '#94a3b8';
        ctx.beginPath(); ctx.moveTo(xd, Y(hh)); ctx.lineTo(xd, Y(-hh)); ctx.stroke();
        ctx.fillStyle = '#94a3b8';
        ctx.textAlign = 'right';
        ctx.fillText('D', xd - 3, Y(0) + 3);
        // ölçek notu
        const aspect = sx / sy;
        ctx.textAlign = 'right';
        ctx.fillStyle = '#64748b';
        ctx.fillText(`z:y ölçek 1:${(sy / sx).toFixed(aspect < 1 ? 1 : 2)} · BFD ${fmtMm(fan.bfd_m)}`, w - 6, h - 4);
    }

    // ───────────────────────── Lens yığını kesiti ─────────────────────────
    // Parçalar ölçülüp genişliğe göre satırlara sarılır (üst üste binmez).
    function wrapLines(ctx, tokens, maxW) {
        const lines = [];
        let cur = '';
        tokens.forEach((t) => {
            const cand = cur ? cur + '  ' + t : t;
            if (cur && ctx.measureText(cand).width > maxW) { lines.push(cur); cur = t; } else cur = cand;
        });
        if (cur) lines.push(cur);
        return lines;
    }

    /** r: /api/lens/stack ham sonucu. Tüm elemanlar mutlak z konumunda, ışın yelpazesi tüm yüzeylerden geçer. */
    function drawStackSection(canvas, r, opts) {
        opts = opts || {};
        const { ctx, w, h } = prepCanvas(canvas);
        ctx.font = '10px Inter, sans-serif';
        if (!r || !r.elements) {
            ctx.fillStyle = '#64748b';
            ctx.textAlign = 'center';
            ctx.fillText(opts.emptyText || 'Lens yığını bekleniyor…', w / 2, h / 2);
            return;
        }
        const fan = r.ray_fan, sys = r.system, els = r.elements;
        const hhMax = Math.max(...els.map((e) => e.diameter_m / 2));
        const zmin = fan.z_start, zmax = fan.z_end;
        const tt = (r.total_transmittance * 100).toPrecision(3) + ' %';
        const efl = sys.afocal ? `afokal ×${Math.abs(sys.beam_magnification).toFixed(2)}` : `EFL=${fmtMm(sys.efl_m)}`;
        const tokens = opts.compact
            ? [`${els.length} lens`, efl, `T=${tt}`]
            : [`${els.length} lens`, efl, sys.afocal ? '' : `BFL=${fmtMm(sys.bfl_m)}`,
                sys.f_number ? `f/${sys.f_number.toFixed(2)}` : '', `L=${fmtMm(sys.total_length_m)}`, `T=${tt}`].filter(Boolean);
        const lineH = 12, maxW = w - 12;
        const lines = wrapLines(ctx, tokens, maxW);
        const headH = 4 + lines.length * lineH;
        // eleman etiketleri: ilk uygun satıra yerleştirilir
        const labs = els.map((e, i) => ({
            i, z: 0.5 * (e.z_front_m + e.z_back_m), e,
            text: `L${i + 1}` + (opts.compact ? '' : ` f=${fmtMm(e.focal_length_m).replace(' mm', '')}`) + (e.is_stop && !opts.compact ? ' ◉' : ''),
        }));
        const padL = 8, padR = 8, padB = opts.compact ? 14 : 26;
        // x ölçeği önce (etiket satırı hesabı için); dikey ölçek sonra
        const sx = (w - padL - padR) / (zmax - zmin);
        const X = (z) => padL + (z - zmin) * sx;
        const rowRight = [];
        labs.forEach((l) => {
            l.tw = ctx.measureText(l.text).width;
            l.x0 = Math.max(2, Math.min(w - l.tw - 2, X(l.z) - l.tw / 2));
            let r0 = 0;
            while (r0 < rowRight.length && rowRight[r0] + 4 > l.x0) r0++;
            rowRight[r0] = l.x0 + l.tw;
            l.row = r0;
        });
        const bandH = rowRight.length * lineH;
        const padT = headH + bandH + 4;
        const sy = (h - padT - padB) / (2 * hhMax * 1.08);
        const Y = (y) => padT + (h - padT - padB) / 2 - y * sy;
        ctx.strokeStyle = 'rgba(148,163,184,0.35)';
        ctx.setLineDash([4, 4]);
        ctx.beginPath(); ctx.moveTo(padL, Y(0)); ctx.lineTo(w - padR, Y(0)); ctx.stroke();
        ctx.setLineDash([]);
        // ana düzlemler (soluk, kesikli)
        const pp = sys.principal_planes_m;
        if (pp) {
            ctx.strokeStyle = 'rgba(167,139,250,0.35)';
            ctx.setLineDash([2, 3]);
            [pp.front_z, pp.rear_z].forEach((z) => {
                if (z < zmin || z > zmax) return;
                ctx.beginPath(); ctx.moveTo(X(z), padT); ctx.lineTo(X(z), h - padB); ctx.stroke();
            });
            ctx.setLineDash([]);
        }
        // lensler
        els.forEach((e, i) => {
            const prof = e.profile;
            ctx.beginPath();
            prof.forEach((p, k) => { const x = X(p[1]), y = Y(p[0]); if (k === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
            for (let k = prof.length - 1; k >= 0; k--) ctx.lineTo(X(prof[k][2]), Y(prof[k][0]));
            ctx.closePath();
            ctx.fillStyle = e.focal_length_m < 0 ? 'rgba(168,85,247,0.22)' : 'rgba(6,182,212,0.22)';
            ctx.fill();
            ctx.strokeStyle = e.is_stop ? '#f59e0b' : (e.focal_length_m < 0 ? '#a855f7' : '#06b6d4');
            ctx.lineWidth = e.is_stop ? 1.8 : 1.3;
            ctx.stroke();
        });
        ctx.lineWidth = 1;
        // ışınlar (eksene paralel: sarı; alan açılı: camgöbeği)
        const drawRays = (rays, rgb) => rays.forEach((ray) => {
            const t = Math.abs(ray.y_in) / (sys.entrance_pupil_radius_m || 1);
            ctx.strokeStyle = `rgba(${rgb(t)},${ray.vignetted_at === null ? 0.9 : 0.35})`;
            ctx.beginPath();
            ray.points.forEach((p, k) => { const x = X(p[0]), y = Y(p[1]); if (k === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
            ctx.stroke();
        });
        drawRays(fan.rays, (t) => `${Math.round(250 - 40 * t)},${Math.round(204 - 70 * t)},${Math.round(21 + 40 * t)}`);
        if (fan.rays_field && fan.rays_field.length) drawRays(fan.rays_field, (t) => `${Math.round(34 + 40 * t)},${Math.round(211 - 60 * t)},238`);
        // odak noktası
        if (fan.focus_z !== null && fan.focus_z !== undefined && fan.focus_z >= zmin && fan.focus_z <= zmax) {
            const fx = X(fan.focus_z), fy = Y(0), real = fan.focus_z >= sys.z_out_m;
            ctx.fillStyle = real ? '#f43f5e' : 'rgba(244,63,94,0.4)';
            ctx.beginPath(); ctx.arc(fx, fy, 3.5, 0, 6.2832); ctx.fill();
            ctx.fillStyle = '#fda4af';
            ctx.textAlign = 'center';
            ctx.fillText(real ? "F′" : "F′ (sanal)", Math.max(18, Math.min(w - 18, fx)), fy - 8);
        }
        // başlık bloğu
        ctx.textBaseline = 'alphabetic';
        ctx.fillStyle = 'rgba(10,14,26,0.7)';
        ctx.fillRect(2, 2, Math.min(w - 4, Math.max(...lines.map((t) => ctx.measureText(t).width)) + 8), headH);
        ctx.fillStyle = '#cbd5e1';
        ctx.textAlign = 'left';
        lines.forEach((t, i) => ctx.fillText(t, 6, 3 + lineH * (i + 1) - 3));
        // eleman etiketleri (üst-üste binmeyen satırlarda) + küçük işaret çizgisi
        labs.forEach((l) => {
            const ty = headH + 2 + (l.row + 1) * lineH - 3;
            ctx.fillStyle = l.e.is_stop ? '#fbbf24' : '#a5f3fc';
            ctx.textAlign = 'left';
            ctx.fillText(l.text, l.x0, ty);
            ctx.strokeStyle = 'rgba(148,163,184,0.35)';
            ctx.beginPath(); ctx.moveTo(X(l.z), ty + 2); ctx.lineTo(X(l.z), Y(l.e.diameter_m / 2)); ctx.stroke();
        });
        // alt satır: ana düzlem göstergesi + ölçek notu (sığmazsa gösterge atlanır)
        const note = `z:y ölçek 1:${(sy / sx).toFixed(sy / sx < 10 ? 2 : 1)}`;
        ctx.fillStyle = '#64748b';
        ctx.textAlign = 'right';
        ctx.fillText(note, w - 6, h - 4);
        if (pp && !opts.compact) {
            const lg = `H z=${(pp.front_z * 1000).toFixed(1)} · H′ z=${(pp.rear_z * 1000).toFixed(1)} mm`;
            if (ctx.measureText(lg).width + ctx.measureText(note).width + 24 < w) {
                ctx.textAlign = 'left';
                ctx.fillStyle = 'rgba(167,139,250,0.8)';
                ctx.fillText(lg, 6, h - 4);
            }
        }
    }
    window.LensSection = { draw: drawLensSection };

    // ───────────────────────── Panel arayüzü ─────────────────────────
    let catalog = null;
    let currentTerm = 'A';
    let timer = null;
    let reqSeq = 0;
    const R = {}; // DOM referansları

    function buildUI(mount) {
        mount.innerHTML = `
        <div class="v2-field" style="margin-bottom:10px">
            <label>Düzenlenen terminal</label>
            <select id="ld-terminal">
                <option value="A">Terminal A</option>
                <option value="B">Terminal B</option>
            </select>
            <label class="v2-check"><input type="checkbox" id="ld-copy"> A'nın tasarımını B'ye kopyala</label>
        </div>
        <div class="v2-field" style="margin-bottom:10px"><label>Dalga boyu</label>
            <select id="ld-wl"><option value="850">850 nm</option><option value="1310">1310 nm</option><option value="1550">1550 nm</option></select>
        </div>
        <div class="v2-field" style="margin-bottom:10px"><label>Mod</label>
            <select id="ld-mode"><option value="single">Tek lens</option><option value="stack">Lens yığını (seri, üst üste)</option></select></div>
        <div id="ld-single">
        <div class="v2-field" style="margin-bottom:10px"><label>Lens şekli</label><select id="ld-shape"></select></div>
        <div class="v2-field" style="margin-bottom:10px"><label>Malzeme</label><select id="ld-material"></select>
            <div class="v2-tooltip" id="ld-mat-info">Katalog yükleniyor…</div></div>
        <div class="v2-field" style="margin-bottom:10px"><label>Filtre</label><select id="ld-filter"></select></div>
        <div class="v2-field" style="margin-bottom:10px"><label>Kaplama</label><select id="ld-coating"></select></div>
        <div class="control-group"><div class="control-label">Odak uzaklığı <span class="control-value" id="ld-f-val">100 mm</span></div>
            <input type="range" id="ld-f" min="10" max="1000" step="1" value="100"></div>
        <div class="control-group"><div class="control-label">Lens çapı <span class="control-value" id="ld-d-val">50 mm</span></div>
            <input type="range" id="ld-d" min="5" max="300" step="1" value="50"></div>
        <div class="control-group"><div class="control-label">Merkez kalınlığı <span class="control-value" id="ld-t-val">otomatik</span></div>
            <label class="v2-check"><input type="checkbox" id="ld-t-auto" checked> Otomatik</label>
            <input type="range" id="ld-t" min="0.5" max="100" step="0.5" value="5" disabled></div>
        </div>
        <div id="ld-stack" style="display:none">
            <div class="v2-field" style="margin-bottom:8px"><label>Ön ayar</label><select id="ld-st-preset"><option value="">— ön ayar seçin —</option></select>
                <div class="v2-tooltip" id="ld-st-preset-info"></div></div>
            <div class="control-group"><div class="control-label">Alan açısı <span class="control-value" id="ld-st-field-val">0°</span></div>
                <input type="range" id="ld-st-field" min="0" max="10" step="0.5" value="0"></div>
            <div id="ld-st-list"></div>
            <div class="v2-btn-row"><button type="button" class="btn" id="ld-st-add">➕ Lens ekle</button></div>
        </div>
        <div id="ld-error" class="v2-error" style="display:none"></div>
        <canvas id="ld-canvas" class="v2-section-canvas"></canvas>
        <div class="v2-readouts" id="ld-readouts"></div>
        <div class="v2-tooltip" id="ld-shape-info"></div>`;
        ['terminal', 'copy', 'wl', 'shape', 'material', 'filter', 'coating', 'f', 'd', 't', 't-auto',
            'f-val', 'd-val', 't-val', 'error', 'canvas', 'readouts', 'mat-info', 'shape-info',
            'mode', 'single', 'stack', 'st-preset', 'st-preset-info', 'st-field', 'st-field-val', 'st-list', 'st-add'].forEach((k) => { R[k] = mount.querySelector('#ld-' + k); });
        injectStyle();
    }

    function injectStyle() {
        if (document.getElementById('ld-style')) return;
        const st = document.createElement('style');
        st.id = 'ld-style';
        st.textContent = `
        .ld-card{border:1px solid rgba(148,163,184,.25);border-radius:8px;padding:6px 8px;margin-bottom:8px;background:rgba(15,23,42,.35)}
        .ld-card-h{display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;font-size:12px}
        .ld-card-h button{background:transparent;border:1px solid rgba(148,163,184,.3);color:inherit;border-radius:4px;cursor:pointer;padding:0 6px;margin-left:3px;font-size:12px;line-height:20px}
        .ld-card-h button:disabled{opacity:.35;cursor:default}
        .ld-card-g{display:grid;grid-template-columns:1fr 1fr;gap:4px 6px}
        .ld-card-g label{display:flex;flex-direction:column;font-size:10px;color:#94a3b8;min-width:0}
        .ld-card-g select,.ld-card-g input{width:100%;min-width:0;box-sizing:border-box;font-size:11px}
        .ld-card-g .ld-wide{grid-column:1/-1}`;
        document.head.appendChild(st);
    }

    function opt(value, text, extra) {
        const o = document.createElement('option');
        o.value = value; o.textContent = text;
        if (extra && extra.disabled) o.disabled = true;
        if (extra && extra.title) o.title = extra.title;
        return o;
    }

    function fillSelects() {
        R.shape.innerHTML = '';
        Object.entries(catalog.shapes).forEach(([k, v]) => { if (v.designable) R.shape.appendChild(opt(k, v.name_tr)); });
        R.filter.innerHTML = '';
        R.filter.appendChild(opt('none', 'Filtre yok'));
        Object.entries(catalog.filters).forEach(([k, v]) => R.filter.appendChild(opt(k, v.name_tr)));
        R.coating.innerHTML = '';
        Object.entries(catalog.coatings).forEach(([k, v]) => R.coating.appendChild(opt(k, v.name_tr)));
        refreshMaterials();
    }

    function refreshMaterials() {
        const T = S.terminals[currentTerm];
        const wl = String(Math.round(T.wavelengthNm));
        R.material.innerHTML = '';
        Object.entries(catalog.materials).forEach(([k, m]) => {
            const tr = m.transparent && m.transparent[wl];
            const n = m.n && m.n[wl];
            let text = m.name + (n ? ` (n=${n.toFixed(3)})` : '');
            let ex = {};
            if (tr === false) {
                const t = m.transmission_um || [];
                text += ' — opak';
                ex = { disabled: true, title: `${wl} nm bu malzemenin geçirgenlik bandı (${t[0]}–${t[1]} µm) dışında` };
            }
            R.material.appendChild(opt(k, text, ex));
        });
        // seçili malzeme opaksa ilk saydamı seç
        const cur = T.material;
        const curOpt = Array.from(R.material.options).find((o) => o.value === cur);
        if (!curOpt || curOpt.disabled) {
            const firstOk = Array.from(R.material.options).find((o) => !o.disabled);
            if (firstOk) T.material = firstOk.value;
        }
        R.material.value = T.material;
        updateMatInfo();
    }

    function updateMatInfo() {
        const m = catalog && catalog.materials[S.terminals[currentTerm].material];
        if (!m) return;
        const wl = String(Math.round(S.terminals[currentTerm].wavelengthNm));
        const abbe = m.abbe ? `Abbe ${m.abbe.toFixed(1)}` : 'Abbe —';
        R['mat-info'].innerHTML = `${esc(m.notes_tr || '')}<br>Maliyet: <b>${esc(m.cost || '—')}</b> · ` +
            `n(${wl} nm)=${m.n && m.n[wl] ? m.n[wl].toFixed(4) : '—'} · ${abbe} · ` +
            `geçirgenlik ${m.transmission_um ? m.transmission_um[0] + '–' + m.transmission_um[1] + ' µm' : '—'}`;
        const sh = catalog.shapes[S.terminals[currentTerm].shape];
        if (sh) R['shape-info'].innerHTML = `${esc(sh.description_tr)}<br><i>Kullanım: ${esc(sh.use_case_tr)}</i>`;
    }

    function loadControls() {
        const T = S.terminals[currentTerm];
        R.wl.value = String(Math.round(T.wavelengthNm));
        R.mode.value = T.mode;
        R.single.style.display = T.mode === 'single' ? '' : 'none';
        R.stack.style.display = T.mode === 'stack' ? '' : 'none';
        R['shape-info'].style.display = T.mode === 'single' ? '' : 'none';
        R['st-field'].value = T.stack.fieldDeg;
        R['st-field-val'].textContent = T.stack.fieldDeg + '°';
        if (catalog) { refreshMaterials(); renderCards(); }
        R.shape.value = T.shape; R.filter.value = T.filter; R.coating.value = T.coating;
        R.f.value = T.f_mm; R.d.value = T.D_mm; R.t.value = T.tc_mm; R['t-auto'].checked = T.tcAuto;
        R.t.disabled = T.tcAuto;
        syncLabels();
        render(T);
    }

    // ───────────────────────── Lens yığını: eleman kartları ─────────────────────────
    let stackPresets = {};   // dalga boyu (nm) → /api/lens/stack/presets yanıtı
    const stackEls = () => S.terminals[currentTerm].stack.elements;

    function optsHTML(entries, sel) {
        return entries.map(([v, t, dis]) => `<option value="${esc(v)}"${v === sel ? ' selected' : ''}${dis ? ' disabled' : ''}>${esc(t)}</option>`).join('');
    }

    function cardHTML(e, i, n, wl) {
        const shapes = Object.entries(catalog.shapes).filter(([, v]) => v.designable).map(([k, v]) => [k, v.name_tr]);
        const mats = Object.entries(catalog.materials).map(([k, m]) => {
            const opaque = m.transparent && m.transparent[wl] === false;
            return [k, m.name + (m.n && m.n[wl] ? ` (n=${m.n[wl].toFixed(3)})` : '') + (opaque ? ' — opak' : ''), opaque];
        });
        const coats = Object.entries(catalog.coatings).map(([k, v]) => [k, v.name_tr]);
        const filts = [['none', 'Filtre yok']].concat(Object.entries(catalog.filters).map(([k, v]) => [k, v.name_tr]));
        const btn = (act, label, title, dis) => `<button type="button" data-act="${act}" title="${title}"${dis ? ' disabled' : ''}>${label}</button>`;
        return `<div class="ld-card" data-i="${i}">
            <div class="ld-card-h"><b>Lens ${i + 1}</b><span>${btn('up', '↑', 'Yukarı taşı', i === 0)}${btn('down', '↓', 'Aşağı taşı', i === n - 1)}` +
            `${btn('dup', '⧉', 'Çoğalt', n >= MAX_STACK)}${btn('del', '🗑', 'Sil', n <= 1)}</span></div>
            <div class="ld-card-g">
              <label class="ld-wide">Şekil<select data-k="shape">${optsHTML(shapes, e.shape)}</select></label>
              <label class="ld-wide">Malzeme<select data-k="material">${optsHTML(mats, e.material)}</select></label>
              <label>Odak uzaklığı (mm)<input type="number" data-k="f_mm" min="1" max="10000" step="1" value="${e.f_mm}"></label>
              <label>Çap (mm)<input type="number" data-k="D_mm" min="1" max="1000" step="1" value="${e.D_mm}"></label>
              <label>Merkez kalınlığı (mm)<input type="number" data-k="tc_mm" min="0.1" max="1000" step="0.5" placeholder="otomatik" value="${e.tcAuto ? '' : e.tc_mm}"></label>
              ${i < n - 1 ? `<label>Sonraki lense mesafe (mm)<input type="number" data-k="gap_mm" min="0" max="10000" step="1" value="${e.gap_mm}"></label>` : '<label>&nbsp;<span style="font-size:10px">son eleman</span></label>'}
              <label>Kaplama<select data-k="coating">${optsHTML(coats, e.coating)}</select></label>
              <label>Filtre<select data-k="filter">${optsHTML(filts, e.filter)}</select></label>
            </div></div>`;
    }

    function renderCards() {
        if (!catalog) return;
        const T = S.terminals[currentTerm];
        const wl = String(Math.round(T.wavelengthNm));
        const els = T.stack.elements;
        R['st-list'].innerHTML = els.map((e, i) => cardHTML(e, i, els.length, wl)).join('');
        R['st-add'].textContent = `➕ Lens ekle (${els.length}/${MAX_STACK})`;
        R['st-add'].disabled = els.length >= MAX_STACK;
    }

    function stackBody(T) {
        const p = app().getParams();
        const dist = (p.gridX || 60) * (p.cellSize || 100);
        return {
            elements: T.stack.elements.map((e) => ({
                shape: e.shape, material: e.material, focalLengthM: e.f_mm / 1000, diameterM: e.D_mm / 1000,
                centerThicknessM: e.tcAuto ? null : e.tc_mm / 1000, coating: e.coating, filter: e.filter || 'none',
                spacingM: Math.max(0, e.gap_mm) / 1000,
            })),
            wavelengthNm: T.wavelengthNm, linkDistanceM: Math.min(dist, 1e6),
            bareRxRadiusM: Math.max((p.receiverDiamM || 0.1) / 2, 1e-4), fieldAngleDeg: T.stack.fieldDeg, nRays: 9,
        };
    }

    function elementFromApi(e) {
        return defaultElement({
            shape: e.shape, material: e.material, f_mm: +(e.focalLengthM * 1000).toFixed(3), D_mm: +(e.diameterM * 1000).toFixed(3),
            tcAuto: e.centerThicknessM === null || e.centerThicknessM === undefined,
            tc_mm: e.centerThicknessM ? +(e.centerThicknessM * 1000).toFixed(3) : 5,
            coating: e.coating || 'none', filter: e.filter || 'none', gap_mm: +((e.spacingM || 0) * 1000).toFixed(3),
        });
    }

    async function getPresets(wl) {
        if (!stackPresets[wl]) {
            const r = await api('/api/lens/stack/presets?wavelengthNm=' + wl);
            if (!r.ok) return null;
            stackPresets[wl] = r.data;
        }
        return stackPresets[wl];
    }

    async function fillPresetSelect() {
        const wl = Math.round(S.terminals[currentTerm].wavelengthNm);
        const pr = await getPresets(wl);
        if (!pr) return;
        const cur = S.terminals[currentTerm].stack.preset;
        R['st-preset'].innerHTML = '<option value="">— ön ayar seçin —</option>' + pr.presets.map((p) => {
            const sm = p.summary || {};
            const info = sm.afocal ? `afokal ×${Math.abs(sm.beam_magnification).toFixed(2)}` : `EFL ${fmtMm(sm.efl_m)}`;
            return `<option value="${esc(p.key)}"${p.key === cur ? ' selected' : ''}>${esc(p.name_tr)} · ${info}</option>`;
        }).join('');
        const sel = pr.presets.find((p) => p.key === cur);
        R['st-preset-info'].textContent = sel ? sel.description_tr : '';
    }

    async function applyPreset(key) {
        const T = S.terminals[currentTerm];
        if (!key) { T.stack.preset = ''; R['st-preset-info'].textContent = ''; return; }
        const pr = await getPresets(Math.round(T.wavelengthNm));
        const p = pr && pr.presets.find((q) => q.key === key);
        if (!p) return;
        T.stack.preset = key;
        T.stack.elements = p.elements.map(elementFromApi);
        R['st-preset-info'].textContent = p.description_tr;
        renderCards();
        scheduleDesign();
    }

    function onCardAction(act, i) {
        const els = stackEls();
        const T = S.terminals[currentTerm];
        if (act === 'del' && els.length > 1) els.splice(i, 1);
        else if (act === 'dup' && els.length < MAX_STACK) els.splice(i + 1, 0, JSON.parse(JSON.stringify(els[i])));
        else if (act === 'up' && i > 0) [els[i - 1], els[i]] = [els[i], els[i - 1]];
        else if (act === 'down' && i < els.length - 1) [els[i + 1], els[i]] = [els[i], els[i + 1]];
        else return;
        T.stack.preset = '';
        R['st-preset'].value = '';
        renderCards();
        scheduleDesign();
    }

    function onCardInput(target) {
        const card = target.closest('.ld-card');
        const k = target.dataset.k;
        if (!card || !k) return;
        const e = stackEls()[+card.dataset.i];
        if (!e) return;
        if (k === 'tc_mm') {
            const v = parseFloat(target.value);
            e.tcAuto = !(v > 0);
            if (v > 0) e.tc_mm = v;
        } else if (k === 'f_mm' || k === 'D_mm' || k === 'gap_mm') {
            const v = parseFloat(target.value);
            if (!isFinite(v)) return;
            e[k] = v;
        } else {
            e[k] = target.value;
        }
        S.terminals[currentTerm].stack.preset = '';
        scheduleDesign();
    }

    function syncLabels() {
        R['f-val'].textContent = R.f.value + ' mm';
        R['d-val'].textContent = R.d.value + ' mm';
        R['t-val'].textContent = R['t-auto'].checked ? 'otomatik' : R.t.value + ' mm';
    }

    function readControls() {
        const T = S.terminals[currentTerm];
        T.mode = R.mode.value;
        T.stack.fieldDeg = parseFloat(R['st-field'].value) || 0;
        R['st-field-val'].textContent = T.stack.fieldDeg + '°';
        T.wavelengthNm = parseInt(R.wl.value, 10);
        T.shape = R.shape.value; T.material = R.material.value; T.filter = R.filter.value; T.coating = R.coating.value;
        T.f_mm = parseFloat(R.f.value); T.D_mm = parseFloat(R.d.value);
        T.tcAuto = R['t-auto'].checked; T.tc_mm = parseFloat(R.t.value);
        R.t.disabled = T.tcAuto;
        syncLabels();
    }

    function requestBody(T) {
        const p = app().getParams();
        const dist = (p.gridX || 60) * (p.cellSize || 100);
        const b = {
            shape: T.shape, material: T.material, focalLengthM: T.f_mm / 1000, diameterM: T.D_mm / 1000,
            wavelengthNm: T.wavelengthNm, coating: T.coating, filter: T.filter,
            linkDistanceM: Math.min(dist, 1e6), bareRxRadiusM: Math.max((p.receiverDiamM || 0.1) / 2, 1e-4),
        };
        if (!T.tcAuto) b.centerThicknessM = T.tc_mm / 1000;
        return b;
    }

    // Yığın sonucu → duplex/3B ile uyumlu T.design kılıfı
    function stackShim(r) {
        const sys = r.system;
        return {
            isStack: true, stack: r,
            design: { material: `yığın (${r.n_elements} lens)`, diameter_m: 2 * sys.entrance_pupil_radius_m,
                focal_length_m: sys.efl_m, wavelength_m: r.wavelength_m },
            total_transmittance: r.total_transmittance,
            abcd: { efl_m: sys.efl_m, bfd_m: sys.bfl_m },
            link: { gain_dB: r.link.gain_dB },
        };
    }

    async function designStack(key) {
        const T = S.terminals[key];
        const my = (T._stackSeq = (T._stackSeq || 0) + 1);
        const r = await api('/api/lens/stack', stackBody(T));
        if (my !== T._stackSeq) return r;      // daha yeni bir istek var: eski yanıtı yoksay
        if (!r.ok) {
            T.design = null; T.stackResult = null; T.error = r.error;
            log('warn', `🔬 Lens yığını (${key}): ${r.error}`);
        } else {
            T.stackResult = r.data; T.design = stackShim(r.data); T.error = null;
        }
        bus.dispatchEvent(new CustomEvent('lens-changed', { detail: { terminal: key } }));
        return r;
    }

    async function designTerminal(key) {
        if (S.terminals[key].mode === 'stack') return designStack(key);
        const T = S.terminals[key];
        const my = ++reqSeq;
        const r = await api('/api/lens/design', requestBody(T));
        if (my !== reqSeq && key === currentTerm) { /* eski yanıt: yine de kaydet */ }
        if (!r.ok) {
            T.design = null; T.error = r.error;
            log('warn', `🔬 Lens tasarımı (${key}): ${r.error}`);
        } else {
            T.design = r.data; T.error = null;
        }
        bus.dispatchEvent(new CustomEvent('lens-changed', { detail: { terminal: key } }));
        return r;
    }

    function render(T) {
        if (T.error) {
            R.error.style.display = 'block';
            R.error.textContent = T.error;
        } else {
            R.error.style.display = 'none';
        }
        drawLensSection(R.canvas, T.design, { emptyText: T.error ? 'Tasarım yapılamadı' : (T.mode === 'stack' ? 'Lens yığını bekleniyor…' : 'Lens tasarımı bekleniyor…') });
        const res = T.design;
        if (!res) { R.readouts.innerHTML = ''; return; }
        if (res.stack) { renderStackReadouts(res.stack); return; }
        const d = res.design;
        const rows = [
            ['n(λ)', d.n_used.toFixed(4)], ['EFL (ABCD)', fmtMm(res.abcd.efl_m)],
            ['R1', fmtMm(d.R1_m)], ['R2', fmtMm(d.R2_m)],
            ['f/#', d.f_number.toFixed(2)], ['NA', d.NA.toFixed(3)],
            ['t_c / t_e', `${fmtMm(d.center_thickness_m)} / ${fmtMm(d.edge_thickness_m)}`], ['BFD', fmtMm(res.ray_fan.bfd_m)],
            ['Lens iletimi', (d.transmittance * 100).toFixed(2) + ' %'], ['Filtre T', (res.filter.transmittance * 100).toPrecision(3) + ' %'],
            ['Toplam T', (res.total_transmittance * 100).toPrecision(3) + ' %'], ['Küresel sapma', res.ray_fan.lsa_m === null ? '—' : fmtMm(res.ray_fan.lsa_m)],
            ['Alıcı kazancı', fmtDb(res.link.gain_dB)], ['Airy yarıçapı', res.link.airy_radius_m ? (res.link.airy_radius_m * 1e6).toFixed(1) + ' µm' : '—'],
        ];
        R.readouts.innerHTML = rows.map(([a, b]) => `<span>${a}</span><b>${esc(b)}</b>`).join('') +
            `<span style="grid-column:1/-1">${esc(res.link.note_tr)}</span>`;
    }

    function renderStackReadouts(r) {
        const s = r.system, l = r.link, v = r.vignetting, pp = s.principal_planes_m;
        const rows = [
            ['Eleman sayısı', String(r.n_elements)],
            ['EFL', s.afocal ? 'afokal (∞)' : fmtMm(s.efl_m)], ['BFL', s.afocal ? '—' : fmtMm(s.bfl_m)],
            ['FFL', s.afocal ? '—' : fmtMm(s.ffl_m)], ['f/#', s.f_number ? s.f_number.toFixed(2) : '—'],
            ['Toplam uzunluk', fmtMm(s.total_length_m)], ['Giriş gözbebeği Ø', fmtMm(2 * s.entrance_pupil_radius_m)],
            ['Durak (limitleyen)', s.stop_name], ['Toplam T', (r.total_transmittance * 100).toPrecision(3) + ' %'],
            ['Alıcı kazancı', fmtDb(l.gain_dB)], ['Verici kazancı', fmtDb(l.transmit.gain_dB)],
            [s.afocal ? 'Işın büyütmesi' : 'Ana düzlemler H / H′', s.afocal ? '×' + s.beam_magnification.toFixed(3) : `${fmtMm(pp.front_z)} / ${fmtMm(pp.rear_z)}`],
            ['Vinyet yok (± alan)', v.unvignetted_half_field_deg.toFixed(2) + '°'],
            [`Geçen ışın (${v.field_angle_deg}°)`, (v.pass_fraction_at_field * 100).toFixed(0) + ' %'],
            ['Küresel sapma', r.ray_fan.lsa_m === null ? '—' : fmtMm(r.ray_fan.lsa_m)],
            ['Gauss ışın yarıçapı (alıcıda)', fmtMm(r.gaussian.w)],
        ];
        const per = r.elements.map((e) => `L${e.index + 1}: T=${(e.transmittance * 100).toFixed(1)} %`).join(' · ');
        R.readouts.innerHTML = rows.map(([a, b]) => `<span>${a}</span><b>${esc(b)}</b>`).join('') +
            `<span style="grid-column:1/-1">${esc(per)}</span><span style="grid-column:1/-1">${esc(l.note_tr)}</span>`;
    }

    function scheduleDesign() {
        readControls();
        updateMatInfo();
        clearTimeout(timer);
        timer = setTimeout(async () => {
            const key = currentTerm;
            await designTerminal(key);
            if (R.copy.checked && key === 'A') {
                const A = S.terminals.A, B = S.terminals.B;
                ['shape', 'material', 'filter', 'coating', 'f_mm', 'D_mm', 'tcAuto', 'tc_mm', 'mode'].forEach((k) => { B[k] = A[k]; });
                B.stack = JSON.parse(JSON.stringify(A.stack));
                await designTerminal('B');
            }
            if (key === currentTerm) render(S.terminals[key]);
        }, 250);
    }

    async function init() {
        const mount = document.getElementById('lens-designer-mount');
        if (!mount) return;
        buildUI(mount);
        const r = await api('/api/lens/catalog');
        if (!r.ok) {
            mount.innerHTML = `<div class="v2-warn">Lens tasarımcısı Python backend gerektirir: ${esc(r.error)}.<br>
                Sunucuyu başlatın: <code>python3 run.py</code></div>`;
            log('warn', 'Lens kataloğu yüklenemedi: ' + r.error);
            return;
        }
        catalog = r.data;
        S.catalog = catalog;
        fillSelects();
        loadControls();
        R.terminal.addEventListener('change', () => { currentTerm = R.terminal.value; loadControls(); fillPresetSelect(); });
        R.mode.addEventListener('change', () => {
            readControls();
            loadControls();
            fillPresetSelect();
            scheduleDesign();
        });
        R['st-preset'].addEventListener('change', () => applyPreset(R['st-preset'].value));
        R['st-add'].addEventListener('click', () => {
            const els = stackEls();
            if (els.length >= MAX_STACK) return;
            const last = els[els.length - 1];
            els.push(defaultElement(last ? { material: last.material, coating: last.coating, D_mm: last.D_mm } : {}));
            S.terminals[currentTerm].stack.preset = '';
            R['st-preset'].value = '';
            renderCards();
            scheduleDesign();
        });
        R['st-field'].addEventListener('input', scheduleDesign);
        R['st-list'].addEventListener('click', (ev) => {
            const b = ev.target.closest('button[data-act]');
            const card = ev.target.closest('.ld-card');
            if (b && card && !b.disabled) onCardAction(b.dataset.act, +card.dataset.i);
        });
        R['st-list'].addEventListener('input', (ev) => onCardInput(ev.target));
        R.wl.addEventListener('change', () => {
            readControls();
            refreshMaterials();
            fixStackMaterials(currentTerm);
            renderCards();
            fillPresetSelect();
            R.material.value = S.terminals[currentTerm].material;
            bus.dispatchEvent(new CustomEvent('terminal-wavelength', { detail: { terminal: currentTerm } }));
            scheduleDesign();
        });
        ['shape', 'material', 'filter', 'coating', 'f', 'd', 't', 't-auto'].forEach((k) => {
            R[k].addEventListener(k === 'f' || k === 'd' || k === 't' ? 'input' : 'change', scheduleDesign);
        });
        R.copy.addEventListener('change', () => { if (R.copy.checked) scheduleDesign(); });
        window.addEventListener('resize', () => render(S.terminals[currentTerm]));
        const p = app().getParams();
        ['grid-x', 'cell-size', 'receiver-diameter'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('change', scheduleDesign);
        });
        fillPresetSelect();
        await designTerminal('A');
        await designTerminal('B');
        render(S.terminals[currentTerm]);
        log('info', '🔬 Lens tasarımcısı hazır (' + Object.keys(catalog.materials).length + ' malzeme).');
        void p;
    }

    // dalga boyunda opak kalan yığın malzemelerini ilk saydam malzemeyle değiştir
    function fixStackMaterials(key) {
        if (!catalog) return;
        const wl = String(Math.round(S.terminals[key].wavelengthNm));
        const ok = Object.entries(catalog.materials).find(([, v]) => v.transparent[wl]);
        S.terminals[key].stack.elements.forEach((e) => {
            const m = catalog.materials[e.material];
            if (m && m.transparent[wl] === false && ok) e.material = ok[0];
        });
    }

    // duplex panelinden dalga boyu değişirse: opak malzemeyi saydam olanla değiştir ve yeniden tasarla
    async function setWavelength(key, nm) {
        const T = S.terminals[key];
        T.wavelengthNm = nm;
        if (catalog) {
            const m = catalog.materials[T.material];
            if (m && m.transparent[String(nm)] === false) {
                const ok = Object.entries(catalog.materials).find(([, v]) => v.transparent[String(nm)]);
                if (ok) T.material = ok[0];
            }
        }
        fixStackMaterials(key);
        if (key === currentTerm && catalog) loadControls();
        await designTerminal(key);
        if (key === currentTerm && catalog) render(T);
    }

    // Duplex / bağlantı hesapları için etkin lens: tek lens ya da yığının giriş gözbebeği ve toplam iletimi
    function getTerminalLens(key) {
        const T = S.terminals[key];
        const d = T && T.design;
        if (!d) return null;
        if (d.stack) {
            const sys = d.stack.system;
            return {
                mode: 'stack', nElements: d.stack.n_elements, rxRadiusM: sys.entrance_pupil_radius_m,
                transmittance: d.total_transmittance, efl_m: sys.efl_m, bfl_m: sys.bfl_m, fNumber: sys.f_number,
                afocal: sys.afocal, stopElement: sys.stop_element, wavelengthNm: T.wavelengthNm, result: d.stack,
            };
        }
        return {
            mode: 'single', nElements: 1, rxRadiusM: d.design.diameter_m / 2, transmittance: d.total_transmittance,
            efl_m: d.abcd.efl_m, bfl_m: d.abcd.bfd_m, fNumber: d.design.f_number, afocal: false, stopElement: 0,
            wavelengthNm: T.wavelengthNm, result: d,
        };
    }
    window.FSOLensDesigner = { getTerminalLens };

    window.LensDesigner = {
        getTerminalLens,
        setWavelength,
        refresh: (key) => designTerminal(key || currentTerm),
        get: (key) => S.terminals[key],
    };
    document.addEventListener('DOMContentLoaded', init);
})();
