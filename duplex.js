/**
 * Full-Duplex İletişim ve ARQ Demo panelleri
 * ------------------------------------------
 * POST /api/duplex  : iki yönlü (A<->B) WDM bağlantı, CRC + seçici tekrar (selective repeat) ARQ,
 *                     ACK/NACK'ın karşı yöndeki çerçeveye bindirilmesi (piggyback), slot zaman çizgisi.
 * POST /api/arq     : tek yönlü ARQ demosu.
 * Sonuçta: kartlar, animasyonlu slot çizgisi, segment tablosu, "bölge başına tekrar" ve uzamsal bölge grafikleri;
 * uzamsal bölgeler 3B görünümde (Viz3D.setRegions) renkli bantlar olarak da gösterilir.
 */
(function () {
    'use strict';

    const bus = (window.FSOBus = window.FSOBus || new EventTarget());
    const S = (window.FSOState = window.FSOState || {});
    S.terminals = S.terminals || { A: { wavelengthNm: 1550 }, B: { wavelengthNm: 1310 } };
    const app = () => window.FSOApp || { log() {}, BACKEND_URL: window.location.origin, getParams: () => ({}) };
    const log = (t, m) => app().log(t, m);
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const $ = (id) => document.getElementById(id);
    const num = (id, def) => { const v = parseFloat($(id).value); return isFinite(v) ? v : def; };

    function fmtBps(b) {
        if (b === null || b === undefined) return '—';
        const u = ['b/s', 'kb/s', 'Mb/s', 'Gb/s', 'Tb/s'];
        let i = 0;
        while (b >= 1000 && i < u.length - 1) { b /= 1000; i++; }
        return b.toFixed(2) + ' ' + u[i];
    }
    const fmtBer = (x) => (x === null || x === undefined) ? '—' : (x === 0 ? '0' : x.toExponential(2));

    // ───────────────────────── ortak çizimler ─────────────────────────
    function barChart(el, items, opts) {
        opts = opts || {};
        if (!items.length) { el.innerHTML = '<div class="v2-muted">Veri yok.</div>'; return; }
        const W = 600, H = 170, pl = 30, pr = 8, pt = 18, pb = 36;
        const max = Math.max(1, ...items.map((i) => i.value));
        const bw = (W - pl - pr) / items.length;
        let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.title || 'grafik')}">`;
        svg += `<text x="${pl}" y="11" fill="#94a3b8" font-size="10">${esc(opts.title || '')}</text>`;
        for (let g = 0; g <= 4; g++) {
            const y = pt + (H - pt - pb) * (1 - g / 4);
            svg += `<line x1="${pl}" x2="${W - pr}" y1="${y}" y2="${y}" stroke="rgba(255,255,255,0.06)"/>`;
            svg += `<text x="${pl - 4}" y="${y + 3}" fill="#64748b" font-size="8" text-anchor="end">${(max * g / 4).toFixed(max < 4 ? 1 : 0)}</text>`;
        }
        const step = Math.ceil(items.length / 20);
        items.forEach((it, i) => {
            const h = (H - pt - pb) * it.value / max;
            const x = pl + i * bw + bw * 0.12, w = bw * 0.76, y = H - pb - h;
            svg += `<rect x="${x}" y="${y}" width="${w}" height="${Math.max(h, 0.5)}" rx="2" fill="${it.color || '#3b82f6'}"><title>${esc(it.title || it.label)}: ${it.value}</title></rect>`;
            if (items.length <= 24) svg += `<text x="${x + w / 2}" y="${y - 2}" fill="#cbd5e1" font-size="8" text-anchor="middle">${it.value}</text>`;
            if (i % step === 0) svg += `<text x="${x + w / 2}" y="${H - pb + 11}" fill="#94a3b8" font-size="8" text-anchor="middle">${esc(it.label)}</text>`;
            if (it.sub && i % step === 0) svg += `<text x="${x + w / 2}" y="${H - pb + 21}" fill="#64748b" font-size="7" text-anchor="middle">${esc(it.sub)}</text>`;
        });
        svg += `<text x="${(W + pl) / 2}" y="${H - 3}" fill="#64748b" font-size="9" text-anchor="middle">${esc(opts.xlabel || '')}</text></svg>`;
        el.innerHTML = svg;
    }

    function retryColor(v, max) {
        const t = max ? v / max : 0;
        return t < 0.34 ? '#10b981' : t < 0.67 ? '#f59e0b' : '#f43f5e';
    }

    function segTable(el, arq) {
        const rows = arq.segments.map((sg) => {
            const mk = (ok) => `<span class="v2-chip ${ok ? 'ok' : 'bad'}">${ok ? '✓' : '✗'}</span>`;
            const half = sg.history_truncated ? sg.crc_history.length / 2 : 0;
            const chips = sg.history_truncated
                ? sg.crc_history.slice(0, half).map(mk).join('') + `<span title="${sg.history_omitted} deneme gösterilmiyor">…+${sg.history_omitted}…</span>` + sg.crc_history.slice(half).map(mk).join('')
                : sg.crc_history.map(mk).join('');
            return `<tr><td>${sg.index}</td><td>${sg.bytes_range[0]}–${sg.bytes_range[1]}</td><td>${sg.attempts}</td><td>${sg.retries}</td>
                <td>${sg.final_ok ? '<span class="v2-badge ok">alındı</span>' : '<span class="v2-badge bad">kayıp</span>'}</td>
                <td>${sg.spurious_retransmissions}</td><td>${chips}</td></tr>`;
        }).join('');
        el.innerHTML = `<table class="verify-table"><thead><tr><th>Seg.</th><th>Bayt</th><th>Deneme</th><th>Tekrar</th><th>Durum</th><th>Gereksiz</th><th>CRC geçmişi</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    function charts(segEl, regEl, arq, regionsOverride) {
        const per = arq.segments.map((sg) => sg.retries);
        const mx = Math.max(0, ...per);
        barChart(segEl, arq.segments.map((sg) => ({ label: String(sg.index), value: sg.retries, color: retryColor(sg.retries, mx), title: sg.region_tr })),
            { title: 'Bölge (segment) başına tekrar sayısı', xlabel: 'segment no' });
        const reg = regionsOverride || arq.retries_by_spatial_region;
        if (reg && reg.length) {
            const m2 = Math.max(0, ...reg.map((r) => r.sampled_retries));
            barChart(regEl, reg.map((r) => ({ label: (r.s_start / 1000).toFixed(1), sub: '–' + (r.s_end / 1000).toFixed(1) + ' km', value: r.sampled_retries, color: retryColor(r.sampled_retries, m2), title: r.label_tr + ` (skor ${r.score_mean})` })),
                { title: 'Uzamsal bölge başına tekrar (heuristik atama: yerel bozulma skoruna orantılı)', xlabel: 'bağlantı boyunca konum (km)' });
        } else {
            regEl.innerHTML = '<div class="v2-muted">Uzamsal bölge grafiği için "3B yayılımı kullan" seçeneğini açın.</div>';
        }
    }

    // ───────────────────────── Full-duplex paneli ─────────────────────────
    let dx = { res: null, dir: 'A→B', timer: null };

    function termCard(k, defWl, defText) {
        return `<div class="v2-card"><h4>${k === 'A' ? '🅰️' : '🅱️'} Terminal ${k}</h4>
            <div class="v2-grid cols-2">
                <div class="v2-field"><label>Güç (dBm)</label><input class="v2-input" id="dx-${k}-pw" type="number" step="1" value="10"></div>
                <div class="v2-field"><label>Dalga boyu</label><select id="dx-${k}-wl"><option value="850">850 nm</option><option value="1310">1310 nm</option><option value="1550">1550 nm</option></select></div>
                <div class="v2-field"><label>Filtre izolasyonu (dB)</label><input class="v2-input" id="dx-${k}-iso" type="number" step="1" min="0" max="120" value="40"></div>
                <div class="v2-field"><label>Alıcı lensi</label><span class="v2-muted" id="dx-${k}-lens">—</span></div>
                <div class="v2-field" style="grid-column:1/-1"><label>Gönderilecek metin</label><input class="v2-input" id="dx-${k}-txt" type="text" maxlength="400" value="${esc(defText)}"></div>
            </div></div>`;
    }

    function buildDuplexUI(pane) {
        pane.innerHTML = `
        <div class="v2-grid cols-2">${termCard('A', 1550, 'Terminal A -> B: merhaba FSO!')}${termCard('B', 1310, 'Terminal B -> A: selam!')}</div>
        <div class="v2-card" style="margin-top:12px"><h4>⚙️ Bağlantı ve ARQ ayarları</h4>
            <div class="v2-grid cols-4">
                <div class="v2-field"><label>Mesafe (m) </label><input class="v2-input" id="dx-dist" type="number" min="1" value="2000"></div>
                <div class="v2-field"><label>Hava</label><select id="dx-weather"><option value="clear">Açık</option><option value="haze">Pus</option><option value="fog">Sis</option><option value="rain">Yağmur</option><option value="snow">Kar</option><option value="storm">Fırtına</option></select></div>
                <div class="v2-field"><label>Cn² </label><select id="dx-cn2"><option value="1e-16">1e-16 (zayıf)</option><option value="1e-15" selected>1e-15</option><option value="1e-14">1e-14 (orta)</option><option value="1e-13">1e-13 (güçlü)</option></select></div>
                <div class="v2-field"><label>Bit hızı</label><select id="dx-rate"><option value="1e6">1 Mb/s</option><option value="1e8">100 Mb/s</option><option value="1e9" selected>1 Gb/s</option><option value="1e10">10 Gb/s</option></select></div>
                <div class="v2-field"><label>Segment (bayt)</label><input class="v2-input" id="dx-seg" type="number" min="1" max="255" value="8"></div>
                <div class="v2-field"><label>Maks. tekrar</label><input class="v2-input" id="dx-retries" type="number" min="0" max="200" value="5"></div>
                <div class="v2-field"><label>Tohum (seed)</label><input class="v2-input" id="dx-seed" type="number" min="0" value="1"></div>
                <div class="v2-field"><label>Sabit BER (boş = kanal modeli)</label><input class="v2-input" id="dx-ber" type="number" step="any" min="0" max="1" placeholder="örn. 0.01"></div>
            </div>
            <div class="v2-row">
                <label class="v2-check"><input type="checkbox" id="dx-use3d"> 3B yayılımı kullan (uzamsal bölgeler, anomaliler)</label>
                <span class="v2-muted" id="dx-3d-note"></span>
                <label class="v2-check"><input type="checkbox" id="dx-lenslosses" checked> Tasarlanan lens/filtre iletimini bağlantı kaybına ekle</label>
            </div>
            <div class="v2-btn-row"><button class="btn btn-primary" id="dx-run">📡 Full-Duplex İletişimi Başlat</button></div>
            <div id="dx-error" class="v2-error" style="display:none"></div>
        </div>
        <div id="dx-out" style="margin-top:12px"></div>`;
    }

    function syncTermLens() {
        ['A', 'B'].forEach((k) => {
            const T = S.terminals[k], el = $('dx-' + k + '-lens');
            if (!el) return;
            const d = T && T.design;
            el.textContent = d ? `${d.design.material}, D=${(d.design.diameter_m * 1000).toFixed(0)} mm, T=${(d.total_transmittance * 100).toFixed(1)} %` : 'varsayılan (25 mm yarıçap)';
        });
    }

    function lensLossDb() {
        const t = ['A', 'B'].map((k) => { const d = S.terminals[k] && S.terminals[k].design; return d ? d.total_transmittance : 1; });
        const mean = (t[0] + t[1]) / 2;
        return -10 * Math.log10(Math.max(mean, 1e-12));
    }

    // ── 3B yayılım ↔ mesafe tutarlılığı ──
    // 3B yayılım ızgarasının uzunluğu (gridX * hücre) mesafe alanıyla > %5 farklıysa mesafe
    // otomatik olarak 3B uzunluğa eşitlenir ve görünür bir not gösterilir.
    function propLength() {
        if (!window.Viz3D) return null;
        try { const r = window.Viz3D.buildRequest(); return r.distanceM || r.atmosphere.gridX * r.atmosphere.cellSize; } catch (e) { return null; }
    }
    function sync3d(prefix) {
        const cb = $(prefix + '-use3d'), note = $(prefix + '-3d-note'), dist = $(prefix + '-dist');
        if (!cb || !note) return;
        note.textContent = '';
        if (!cb.checked) return;
        if (!window.Viz3D || !window.Viz3D.getResult()) {
            cb.checked = false;
            note.textContent = '⚠️ Önce 3B Yayılım sekmesinde yayılımı hesaplayın.';
            return;
        }
        const L = propLength(), d = parseFloat(dist.value);
        if (L && (!(d > 0) || Math.abs(L - d) / d > 0.05)) {
            dist.value = String(Math.round(L));
            note.textContent = `ℹ️ Mesafe, 3B yayılım uzunluğuna (${Math.round(L)} m) eşitlendi.`;
        } else note.textContent = `3B yayılım uzunluğu: ${Math.round(L || d)} m.`;
    }

    function duplexBody() {
        const body = {
            payloadA: $('dx-A-txt').value || 'A', payloadB: $('dx-B-txt').value || 'B',
            distanceM: num('dx-dist', 2000), weather: $('dx-weather').value, cn2: parseFloat($('dx-cn2').value),
            segmentBytes: Math.round(num('dx-seg', 8)), maxRetries: Math.round(num('dx-retries', 5)),
            seed: Math.round(num('dx-seed', 1)), bitrateBps: parseFloat($('dx-rate').value),
        };
        ['A', 'B'].forEach((k) => {
            const d = S.terminals[k] && S.terminals[k].design;
            const t = { txPowerDbm: num(`dx-${k}-pw`, 10), wavelengthNm: parseInt($(`dx-${k}-wl`).value, 10), filterIsolationDb: num(`dx-${k}-iso`, 40) };
            if (d) t.rxRadiusM = Math.min(10, Math.max(1e-4, d.design.diameter_m / 2));
            body['terminal' + k] = t;
        });
        const ber = $('dx-ber').value.trim();
        if (ber !== '') body.berOverride = parseFloat(ber);
        let fixed = 3;
        if ($('dx-lenslosses').checked) fixed += lensLossDb();
        body.fixedLossDb = Math.min(100, fixed);
        sync3d('dx');
        body.distanceM = num('dx-dist', 2000);
        if ($('dx-use3d').checked && window.Viz3D) body.propagation = window.Viz3D.buildRequest();
        return body;
    }

    function stopTimeline() { if (dx.timer) { clearInterval(dx.timer); dx.timer = null; } }

    function buildTimeline(el, tl) {
        const head = '<div class="v2-tl-head"><span class="lab"></span>' + tl.map((t) => `<span>${t.slot}</span>`).join('') + '</div>';
        const cell = (tx, rxOk, who) => {
            const fb = tx.fb === 'ACK' ? '✓' : tx.fb === 'NACK' ? '✗' : '·';
            const main = tx.type === 'ACK' ? 'fb' : '#' + tx.seq;
            const tip = `${who}: ${tx.type}${tx.seq !== null && tx.seq !== undefined ? ' seq ' + tx.seq : ''}, piggyback ${tx.fb}${rxOk ? '' : ' — CRC HATALI, çerçeve kayboldu'}`;
            return `<div class="v2-tl-cell ${tx.type}${rxOk ? '' : ' lost'}" title="${esc(tip)}"><span>${main}</span><span>${fb}</span></div>`;
        };
        const rowA = '<div class="v2-tl-row"><div class="v2-tl-label">A→B</div>' + tl.map((t) => cell(t.a_tx, t.b_rx_ok, 'A gönderir')).join('') + '</div>';
        const rowB = '<div class="v2-tl-row"><div class="v2-tl-label">B→A</div>' + tl.map((t) => cell(t.b_tx, t.a_rx_ok, 'B gönderir')).join('') + '</div>';
        el.innerHTML = head + rowA + rowB;
    }

    function playTimeline(el, mode) {
        stopTimeline();
        const cols = Array.from(el.querySelectorAll('.v2-tl-row')).map((r) => Array.from(r.querySelectorAll('.v2-tl-cell')));
        const n = cols[0] ? cols[0].length : 0;
        cols.forEach((r) => r.forEach((c) => c.classList.remove('shown')));
        if (mode === 'instant') { cols.forEach((r) => r.forEach((c) => c.classList.add('shown'))); return; }
        const ms = mode === 'fast' ? 25 : 110;
        let i = 0;
        dx.timer = setInterval(() => {
            if (i >= n) { stopTimeline(); return; }
            cols.forEach((r) => { r[i].classList.add('shown'); });
            const sc = el.parentElement;
            if (sc && cols[0][i]) sc.scrollLeft = Math.max(0, cols[0][i].offsetLeft - sc.clientWidth + 60);
            i++;
        }, ms);
    }

    function dirCard(key, d) {
        const lb = d.link_budget, a = d.arq_result;
        const ok = a.delivered_ok;
        const label = key === 'A→B' ? 'A → B' : 'B → A';
        return `<div class="v2-card"><h4>${label} <span class="v2-badge ${ok ? 'ok' : 'bad'}">${ok ? 'CRC ✓ tüm veri doğru' : 'CRC ✗ hatalı/eksik'}</span>
              <span class="v2-badge ${lb.viable ? 'ok' : 'bad'}">${lb.viable ? 'bağlantı uygun' : 'marj yetersiz'}</span></h4>
            <div class="v2-stat">
              <span>λ</span><b>${lb.wavelength_nm.toFixed(0)} nm</b>
              <span>P_rx</span><b>${lb.p_rx_dBm.toFixed(2)} dBm</b>
              <span>Marj</span><b>${lb.margin_dB.toFixed(1)} dB</b>
              <span>BER (ARQ kanalı, sönümlü ort.)</span><b>${fmtBer(d.ber)}</b>
              <span>BER (ort. güçte)</span><b>${fmtBer(d.ber_mean_power)}</b>
              <span>Verim (throughput)</span><b>${fmtBps(d.throughput_bps)}</b>
              <span>Goodput</span><b>${fmtBps(d.goodput_bps)}</b>
              <span>ARQ verimliliği</span><b>${(a.efficiency * 100).toFixed(1)} %</b>
              <span>Tekrar / gereksiz</span><b>${a.retransmissions} / ${a.spurious_retransmissions}</b>
              <span>Çapraz konuşma SIR</span><b>${lb.sir_dB === null || lb.sir_dB === undefined ? '—' : lb.sir_dB.toFixed(1) + ' dB'}</b>
              <span>σ² (sintilasyon)</span><b>${lb.sigma_I2.toExponential(2)}</b>
            </div>
            <div class="v2-delivered">${esc(a.delivered_payload)}</div>
            <div class="v2-muted" style="margin-top:4px">Kalan hata: ${a.residual_errors.failed_segments} segment, ${a.residual_errors.lost_bytes} bayt, ${a.residual_errors.undetected_segments} tespit edilemeyen.</div></div>`;
    }

    function regionsFor(dirKey, res) {
        const d = res.directions[dirKey];
        const reg = d.arq_result.retries_by_spatial_region;
        if (!reg) return null;
        if (dirKey === 'A→B') return reg;
        const L = res.propagation_rescale ? res.propagation_rescale.distance_used_m
            : (res.propagation_summary && res.propagation_summary[dirKey] ? res.propagation_summary[dirKey].summary.length_m : null);
        if (!L) return reg;
        // B→A bölgeleri B'den başlayarak ölçülür: A'dan ölçülen konuma çevir
        return reg.map((r) => Object.assign({}, r, { s_start: L - r.s_end, s_end: L - r.s_start }));
    }

    function renderDirDetail() {
        const res = dx.res;
        if (!res) return;
        const a = res.directions[dx.dir].arq_result;
        segTable($('dx-seg-table'), a);
        charts($('dx-chart-seg'), $('dx-chart-reg'), a, regionsFor(dx.dir, res));
        document.querySelectorAll('#dx-dir-tabs .tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.dir === dx.dir));
        if (window.Viz3D) window.Viz3D.setRegions(regionsFor(dx.dir, res), dx.dir);
    }

    function renderDuplex(res) {
        dx.res = res;
        const s = res.summary;
        $('dx-out').innerHTML = `
        <div class="v2-card"><h4>Özet
            <span class="v2-badge ${s.both_delivered ? 'ok' : 'bad'}">${s.both_delivered ? 'iki yön de eksiksiz teslim' : 'teslim eksik'}</span>
            ${s.timed_out ? '<span class="v2-badge bad">zaman aşımı</span>' : ''}</h4>
            <div class="v2-stat"><span>Toplam slot</span><b>${s.total_slots}</b><span>Slot süresi</span><b>${(s.slot_duration_s * 1e6).toFixed(3)} µs</b>
            <span>Toplam süre</span><b>${(s.total_time_s * 1e6).toFixed(2)} µs</b><span>Asimetri P_rx(A→B) − P_rx(B→A)</span><b>${s.asymmetry_dB.toFixed(2)} dB</b></div>
            <div class="v2-muted" style="margin-top:6px">${esc(s.asymmetry_note || '')}</div></div>
        <div class="v2-grid cols-2" style="margin-top:12px">${dirCard('A→B', res.directions['A→B'])}${dirCard('B→A', res.directions['B→A'])}</div>
        <div class="v2-card" style="margin-top:12px"><h4>⏱️ Slot zaman çizgisi
            <span class="v2-btn-row" style="margin:0"><button class="btn btn-outline" id="dx-play" style="padding:3px 10px;font-size:0.68rem">▶ Oynat</button>
            <button class="btn btn-outline" id="dx-fast" style="padding:3px 10px;font-size:0.68rem">⏩ Hızlı</button>
            <button class="btn btn-outline" id="dx-instant" style="padding:3px 10px;font-size:0.68rem">Hepsini göster</button></span></h4>
            <div class="v2-muted">Her iki terminal her slotta gönderir. Hücre: üst = seq (fb = yalnız geri bildirim), alt = karşı yönden taşınan ACK ✓ / NACK ✗.
              <span class="v2-badge" style="background:rgba(59,130,246,0.3)">DATA</span> <span class="v2-badge" style="background:rgba(245,158,11,0.35)">RETX (tekrar)</span> <span class="v2-badge" style="background:rgba(148,163,184,0.18)">yalnız ACK</span> kırmızı çerçeve = alıcıda CRC hatası.</div>
            ${res.timeline_truncated ? `<div class="v2-muted">Zaman çizelgesi kısaltıldı: ${res.timeline_total_slots} slottan ilk ve son ${res.timeline.length / 2} slot gösteriliyor (${res.timeline_omitted} slot atlandı).</div>` : ''}
            <div class="v2-timeline" id="dx-timeline"></div></div>
        <div class="v2-card" style="margin-top:12px"><h4>📋 Segment tablosu ve tekrar grafikleri
            <span class="tab-bar v2-seg-tabs" id="dx-dir-tabs" style="margin:0"><button class="tab-btn active" data-dir="A→B">A → B</button><button class="tab-btn" data-dir="B→A">B → A</button></span></h4>
            <div class="v2-table-wrap" id="dx-seg-table"></div>
            <div class="v2-grid cols-2" style="margin-top:10px"><div class="v2-chart" id="dx-chart-seg"></div><div class="v2-chart" id="dx-chart-reg"></div></div>
            <div class="v2-muted">Uzamsal bölgeler 3B Yayılım sekmesinde de renkli bantlar olarak gösterilir (kırmızı = en çok tekrar).</div></div>`;
        buildTimeline($('dx-timeline'), res.timeline);
        playTimeline($('dx-timeline'), 'normal');
        $('dx-play').addEventListener('click', () => playTimeline($('dx-timeline'), 'normal'));
        $('dx-fast').addEventListener('click', () => playTimeline($('dx-timeline'), 'fast'));
        $('dx-instant').addEventListener('click', () => playTimeline($('dx-timeline'), 'instant'));
        $('dx-dir-tabs').addEventListener('click', (e) => { const b = e.target.closest('.tab-btn'); if (b) { dx.dir = b.dataset.dir; renderDirDetail(); } });
        renderDirDetail();
    }

    async function runDuplex() {
        const btn = $('dx-run'), err = $('dx-error');
        err.style.display = 'none';
        btn.disabled = true; btn.textContent = '⏳ Simüle ediliyor…';
        const r = await window.FSOApi('/api/duplex', duplexBody());
        btn.disabled = false; btn.textContent = '📡 Full-Duplex İletişimi Başlat';
        if (!r.ok) {
            err.style.display = 'block'; err.textContent = r.error;
            log('error', 'Full-duplex hatası: ' + r.error);
            return;
        }
        renderDuplex(r.data);
        const ab = r.data.directions['A→B'], ba = r.data.directions['B→A'];
        log('success', `📡 Full-duplex: ${r.data.summary.total_slots} slot, A→B tekrar ${ab.arq_result.retransmissions}, B→A tekrar ${ba.arq_result.retransmissions}, teslim ${r.data.summary.both_delivered ? 'tam' : 'eksik'}.`);
    }

    // ───────────────────────── ARQ paneli ─────────────────────────
    function buildArqUI(pane) {
        pane.innerHTML = `
        <div class="v2-card"><h4>🔁 Tek yönlü CRC + seçici tekrar (selective repeat) ARQ</h4>
            <div class="v2-muted">Yük, segmentlere bölünür (seq | len | veri | CRC-32). Alıcı her segment için ACK/NACK gönderir; yalnızca hatalı veya onaysız segmentler yeniden gönderilir. ACK kaybı gereksiz tekrara yol açar.</div>
            <div class="v2-grid cols-4" style="margin-top:8px">
                <div class="v2-field" style="grid-column:1/-1"><label>Yük (metin)</label><input class="v2-input" id="aq-payload" type="text" maxlength="600" value="Merhaba FSO! Seçici tekrar (selective repeat) ARQ testi."></div>
                <div class="v2-field"><label>Segment (bayt)</label><input class="v2-input" id="aq-seg" type="number" min="1" max="255" value="8"></div>
                <div class="v2-field"><label>Maks. tekrar</label><input class="v2-input" id="aq-retries" type="number" min="0" max="200" value="5"></div>
                <div class="v2-field"><label>ACK BER</label><input class="v2-input" id="aq-ackber" type="number" step="any" min="0" max="0.5" value="0.01"></div>
                <div class="v2-field"><label>İleri BER (boş = kanal modeli)</label><input class="v2-input" id="aq-ber" type="number" step="any" min="0" max="1" value="0.01"></div>
                <div class="v2-field"><label>Mesafe (m)</label><input class="v2-input" id="aq-dist" type="number" min="1" value="1000"></div>
                <div class="v2-field"><label>Güç (dBm)</label><input class="v2-input" id="aq-pw" type="number" value="10"></div>
                <div class="v2-field"><label>Dalga boyu</label><select id="aq-wl"><option value="850">850 nm</option><option value="1310">1310 nm</option><option value="1550" selected>1550 nm</option></select></div>
                <div class="v2-field"><label>Tohum</label><input class="v2-input" id="aq-seed" type="number" min="0" value="1"></div>
            </div>
            <div class="v2-row"><label class="v2-check"><input type="checkbox" id="aq-use3d"> 3B yayılım kullan (kanal 3B sonuçtan, uzamsal bölgeler)</label>
                <span class="v2-muted" id="aq-3d-note"></span></div>
            <div class="v2-btn-row"><button class="btn btn-primary" id="aq-run">🔁 ARQ Çalıştır</button></div>
            <div id="aq-error" class="v2-error" style="display:none"></div>
        </div>
        <div id="aq-out" style="margin-top:12px"></div>`;
    }

    async function runArq() {
        const btn = $('aq-run'), err = $('aq-error');
        err.style.display = 'none';
        btn.disabled = true; btn.textContent = '⏳ Çalışıyor…';
        const body = {
            payload: $('aq-payload').value || 'x', segmentBytes: Math.round(num('aq-seg', 8)), maxRetries: Math.round(num('aq-retries', 5)),
            ackBer: num('aq-ackber', 0), distanceM: num('aq-dist', 1000), txPowerDbm: num('aq-pw', 10),
            wavelengthNm: parseFloat($('aq-wl').value), seed: Math.round(num('aq-seed', 1)),
        };
        const ber = $('aq-ber').value.trim();
        if (ber !== '') body.ber = parseFloat(ber);
        sync3d('aq');
        body.distanceM = num('aq-dist', 1000);
        if ($('aq-use3d').checked && window.Viz3D) body.propagation = window.Viz3D.buildRequest();
        const r = await window.FSOApi('/api/arq', body);
        btn.disabled = false; btn.textContent = '🔁 ARQ Çalıştır';
        if (!r.ok) { err.style.display = 'block'; err.textContent = r.error; log('error', 'ARQ hatası: ' + r.error); return; }
        const a = r.data;
        $('aq-out').innerHTML = `
        <div class="v2-grid cols-2">
          <div class="v2-card"><h4>Sonuç <span class="v2-badge ${a.delivered_ok ? 'ok' : 'bad'}">${a.delivered_ok ? 'CRC ✓ tüm veri doğru' : 'CRC ✗ hatalı/eksik'}</span></h4>
            <div class="v2-stat"><span>Segment</span><b>${a.n_segments}</b><span>Toplam iletim</span><b>${a.total_transmissions}</b>
            <span>Tekrar / gereksiz</span><b>${a.retransmissions} / ${a.spurious_retransmissions}</b><span>Kaybolan ACK</span><b>${a.lost_acks}</b>
            <span>Verimlilik</span><b>${(a.efficiency * 100).toFixed(1)} %</b><span>Goodput oranı</span><b>${(a.goodput_ratio * 100).toFixed(1)} %</b>
            <span>Ort. ileri BER</span><b>${fmtBer(a.forward_mean_ber)}</b><span>Tur sayısı</span><b>${a.n_rounds}</b></div></div>
          <div class="v2-card"><h4>Teslim edilen metin</h4><div class="v2-delivered">${esc(a.delivered_payload)}</div>
            <div class="v2-muted" style="margin-top:4px">Kalan hata: ${a.residual_errors.failed_segments} segment, ${a.residual_errors.bit_errors} bit.</div></div>
        </div>
        <div class="v2-card" style="margin-top:12px"><h4>📋 Segment tablosu</h4><div class="v2-table-wrap" id="aq-seg-table"></div>
          <div class="v2-grid cols-2" style="margin-top:10px"><div class="v2-chart" id="aq-chart-seg"></div><div class="v2-chart" id="aq-chart-reg"></div></div></div>`;
        segTable($('aq-seg-table'), a);
        charts($('aq-chart-seg'), $('aq-chart-reg'), a, null);
        if (window.Viz3D) window.Viz3D.setRegions(a.retries_by_spatial_region || null, 'ARQ');
        log('success', `🔁 ARQ: ${a.total_transmissions} iletim, ${a.retransmissions} tekrar, teslim ${a.delivered_ok ? 'tam' : 'eksik'}.`);
    }

    function init() {
        const pd = $('v2-pane-duplex'), pa = $('v2-pane-arq');
        if (!pd || !pa) return;
        buildDuplexUI(pd);
        buildArqUI(pa);
        ['A', 'B'].forEach((k) => {
            const sel = $(`dx-${k}-wl`);
            sel.value = String(Math.round(S.terminals[k].wavelengthNm || (k === 'A' ? 1550 : 1310)));
            sel.addEventListener('change', () => {
                const nm = parseInt(sel.value, 10);
                if (window.LensDesigner) window.LensDesigner.setWavelength(k, nm); else S.terminals[k].wavelengthNm = nm;
            });
        });
        // tasarımcıdan dalga boyu değişirse duplex seçimini güncelle
        bus.addEventListener('terminal-wavelength', (e) => {
            const k = e.detail.terminal;
            $(`dx-${k}-wl`).value = String(Math.round(S.terminals[k].wavelengthNm));
        });
        bus.addEventListener('lens-changed', syncTermLens);
        syncTermLens();
        ['dx', 'aq'].forEach((px) => {
            $(px + '-use3d').addEventListener('change', () => sync3d(px));
            $(px + '-dist').addEventListener('input', () => { if ($(px + '-use3d').checked) $(px + '-3d-note').textContent = ''; });
        });
        bus.addEventListener('propagation-ready', () => ['dx', 'aq'].forEach(sync3d));
        $('dx-run').addEventListener('click', runDuplex);
        $('aq-run').addEventListener('click', runArq);
    }

    document.addEventListener('DOMContentLoaded', init);
})();
