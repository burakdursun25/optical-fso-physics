/**
 * ============================================================
 *  FSO Physics Engine – Renderer
 *  Canvas-based 2D visualization (side-view cross section)
 * ============================================================
 */

class FSORenderer {
    /**
     * @param {HTMLCanvasElement} topCanvas  – Top-down view (X-Y plane)
     * @param {HTMLCanvasElement} sideCanvas – Side view (X-Z plane)
     */
    constructor(topCanvas, sideCanvas) {
        this.topCanvas = topCanvas;
        this.sideCanvas = sideCanvas;
        this.topCtx = topCanvas.getContext('2d');
        this.sideCtx = sideCanvas.getContext('2d');
        this.pixelRatio = window.devicePixelRatio || 1;

        this.colors = {
            bg: '#0f1625',
            gridLine: 'rgba(255,255,255,0.03)',
            gridBorder: 'rgba(255,255,255,0.08)',
            ray: '#3b82f6',
            rayGlow: 'rgba(59,130,246,0.3)',
            source: '#10b981',
            receiver: '#f59e0b',
            receiverMiss: '#f43f5e',
            refraction: '#8b5cf6',
            tir: '#f43f5e',
            text: '#94a3b8'
        };

        this._resize();
        window.addEventListener('resize', () => this._resize());
    }

    _resize() {
        const pr = this.pixelRatio;
        [this.topCanvas, this.sideCanvas].forEach(c => {
            const rect = c.getBoundingClientRect();
            c.width = rect.width * pr;
            c.height = rect.height * pr;
            c.getContext('2d').setTransform(pr, 0, 0, pr, 0, 0);
        });
    }

    /**
     * Render the atmosphere grid and ray trace result
     * @param {AtmosphereGrid} atmosphere
     * @param {object} traceResult - from RayTracer.trace()
     * @param {Vec3} sourcePos
     * @param {Vec3} targetPos
     * @param {number} receiverRadius
     */
    render(atmosphere, traceResult, sourcePos, targetPos, receiverRadius) {
        this._renderView(this.topCtx, this.topCanvas, atmosphere, traceResult, sourcePos, targetPos, receiverRadius, 'top');
        this._renderView(this.sideCtx, this.sideCanvas, atmosphere, traceResult, sourcePos, targetPos, receiverRadius, 'side');
    }

    _renderView(ctx, canvas, atm, trace, srcPos, tgtPos, recvR, viewType) {
        const w = canvas.width / this.pixelRatio;
        const h = canvas.height / this.pixelRatio;
        const padding = 40;

        // Clear
        ctx.fillStyle = this.colors.bg;
        ctx.fillRect(0, 0, w, h);

        // Grid dimensions based on view
        const gridW = atm.gridX;
        const gridH = viewType === 'top' ? atm.gridY : atm.gridZ;

        const cellW = (w - padding * 2) / gridW;
        const cellH = (h - padding * 2) / gridH;

        // Draw refractive index heatmap
        this._drawHeatmap(ctx, atm, gridW, gridH, cellW, cellH, padding, viewType);

        // Draw grid lines
        this._drawGrid(ctx, gridW, gridH, cellW, cellH, padding, w, h);

        // Draw receiver area
        this._drawReceiver(ctx, tgtPos, recvR, atm, cellW, cellH, padding, viewType, gridH, trace);

        // Draw source
        this._drawSource(ctx, srcPos, atm, cellW, cellH, padding, viewType, gridH);

        // Draw ray path
        if (trace && trace.path.length > 1) {
            this._drawRay(ctx, trace, atm, cellW, cellH, padding, viewType, gridH);
        }

        // Draw refraction events
        if (trace && trace.refractionEvents.length > 0) {
            this._drawRefractionEvents(ctx, trace, atm, cellW, cellH, padding, viewType, gridH);
        }

        // Draw axes labels
        this._drawLabels(ctx, w, h, padding, viewType, gridW, gridH, atm.cellSize);
    }

    _drawHeatmap(ctx, atm, gridW, gridH, cellW, cellH, padding, viewType) {
        // Find n range for color mapping
        let nMin = Infinity, nMax = -Infinity;
        for (let i = 0; i < atm.grid.length; i++) {
            if (atm.grid[i] < nMin) nMin = atm.grid[i];
            if (atm.grid[i] > nMax) nMax = atm.grid[i];
        }

        // Use middle slice for the other axis
        const midSlice = viewType === 'top'
            ? Math.floor(atm.gridZ / 2)
            : Math.floor(atm.gridY / 2);

        for (let gx = 0; gx < gridW; gx++) {
            for (let gy = 0; gy < gridH; gy++) {
                let n;
                if (viewType === 'top') {
                    n = atm.getRefractiveIndex(gx, gy, midSlice);
                } else {
                    // Side view: Y-axis is Z (altitude), flip so bottom = low Z
                    n = atm.getRefractiveIndex(gx, midSlice, gridH - 1 - gy);
                }

                const t = nMax > nMin ? (n - nMin) / (nMax - nMin) : 0.5;

                // Color: blue (cold/high-n) → red (hot/low-n)
                const r = Math.floor(40 + t * 60);
                const g = Math.floor(20 + (1 - Math.abs(t - 0.5) * 2) * 30);
                const b = Math.floor(100 - t * 60);

                ctx.fillStyle = `rgb(${r}, ${g}, ${b})`;
                ctx.fillRect(
                    padding + gx * cellW,
                    padding + gy * cellH,
                    cellW + 0.5,
                    cellH + 0.5
                );
            }
        }
    }

    _drawGrid(ctx, gridW, gridH, cellW, cellH, padding, w, h) {
        ctx.strokeStyle = this.colors.gridLine;
        ctx.lineWidth = 0.5;

        for (let i = 0; i <= gridW; i++) {
            const x = padding + i * cellW;
            ctx.beginPath();
            ctx.moveTo(x, padding);
            ctx.lineTo(x, padding + gridH * cellH);
            ctx.stroke();
        }

        for (let i = 0; i <= gridH; i++) {
            const y = padding + i * cellH;
            ctx.beginPath();
            ctx.moveTo(padding, y);
            ctx.lineTo(padding + gridW * cellW, y);
            ctx.stroke();
        }

        // Border
        ctx.strokeStyle = this.colors.gridBorder;
        ctx.lineWidth = 1;
        ctx.strokeRect(padding, padding, gridW * cellW, gridH * cellH);
    }

    _drawSource(ctx, srcPos, atm, cellW, cellH, padding, viewType, gridH) {
        const cs = atm.cellSize;
        const sx = padding + (srcPos.x / cs) * cellW;
        let sy;
        if (viewType === 'top') {
            sy = padding + (srcPos.y / cs) * cellH;
        } else {
            sy = padding + (gridH - srcPos.z / cs) * cellH;
        }

        // Glow
        const grad = ctx.createRadialGradient(sx, sy, 0, sx, sy, 20);
        grad.addColorStop(0, 'rgba(16, 185, 129, 0.4)');
        grad.addColorStop(1, 'rgba(16, 185, 129, 0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(sx, sy, 20, 0, Math.PI * 2);
        ctx.fill();

        // Dot
        ctx.fillStyle = this.colors.source;
        ctx.beginPath();
        ctx.arc(sx, sy, 6, 0, Math.PI * 2);
        ctx.fill();

        // Label
        ctx.fillStyle = this.colors.source;
        ctx.font = '10px Inter, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('LASER', sx, sy - 14);
    }

    _drawReceiver(ctx, tgtPos, recvR, atm, cellW, cellH, padding, viewType, gridH, trace) {
        const cs = atm.cellSize;
        const tx = padding + (tgtPos.x / cs) * cellW;
        let ty;
        if (viewType === 'top') {
            ty = padding + (tgtPos.y / cs) * cellH;
        } else {
            ty = padding + (gridH - tgtPos.z / cs) * cellH;
        }

        const rPx = (recvR / cs) * (viewType === 'top' ? cellH : cellH);
        const hit = trace && trace.success;

        // Receiver zone
        ctx.strokeStyle = hit ? this.colors.receiver : this.colors.receiverMiss;
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.arc(tx, ty, rPx, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);

        // Fill
        ctx.fillStyle = hit
            ? 'rgba(245, 158, 11, 0.08)'
            : 'rgba(244, 63, 94, 0.08)';
        ctx.beginPath();
        ctx.arc(tx, ty, rPx, 0, Math.PI * 2);
        ctx.fill();

        // Center dot
        ctx.fillStyle = hit ? this.colors.receiver : this.colors.receiverMiss;
        ctx.beginPath();
        ctx.arc(tx, ty, 4, 0, Math.PI * 2);
        ctx.fill();

        // Label
        ctx.fillStyle = hit ? this.colors.receiver : this.colors.receiverMiss;
        ctx.font = '10px Inter, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('ALICI', tx, ty - rPx - 8);
    }

    _drawRay(ctx, trace, atm, cellW, cellH, padding, viewType, gridH) {
        const cs = atm.cellSize;
        const path = trace.path;

        // Glow layer
        ctx.save();
        ctx.strokeStyle = this.colors.rayGlow;
        ctx.lineWidth = 6;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.beginPath();

        for (let i = 0; i < path.length; i++) {
            const p = path[i].position;
            const px = padding + (p.x / cs) * cellW;
            let py;
            if (viewType === 'top') {
                py = padding + (p.y / cs) * cellH;
            } else {
                py = padding + (gridH - p.z / cs) * cellH;
            }

            if (i === 0) ctx.moveTo(px, py);
            else ctx.lineTo(px, py);
        }
        ctx.stroke();

        // Main ray
        ctx.strokeStyle = this.colors.ray;
        ctx.lineWidth = 2;
        ctx.beginPath();

        for (let i = 0; i < path.length; i++) {
            const p = path[i].position;
            const px = padding + (p.x / cs) * cellW;
            let py;
            if (viewType === 'top') {
                py = padding + (p.y / cs) * cellH;
            } else {
                py = padding + (gridH - p.z / cs) * cellH;
            }

            if (i === 0) ctx.moveTo(px, py);
            else ctx.lineTo(px, py);
        }
        ctx.stroke();
        ctx.restore();

        // Animated pulse along the ray
        this._drawRayPulse(ctx, path, atm, cellW, cellH, padding, viewType, gridH);
    }

    _drawRayPulse(ctx, path, atm, cellW, cellH, padding, viewType, gridH) {
        if (path.length < 2) return;
        const cs = atm.cellSize;
        const time = (Date.now() % 2000) / 2000;
        const totalPts = path.length;
        const idx = Math.floor(time * (totalPts - 1));
        const frac = time * (totalPts - 1) - idx;

        if (idx >= totalPts - 1) return;

        const p0 = path[idx].position;
        const p1 = path[idx + 1].position;
        const px = p0.x + (p1.x - p0.x) * frac;
        const py_w = viewType === 'top'
            ? p0.y + (p1.y - p0.y) * frac
            : p0.z + (p1.z - p0.z) * frac;

        const sx = padding + (px / cs) * cellW;
        const sy = viewType === 'top'
            ? padding + (py_w / cs) * cellH
            : padding + (gridH - py_w / cs) * cellH;

        const grad = ctx.createRadialGradient(sx, sy, 0, sx, sy, 10);
        grad.addColorStop(0, 'rgba(59, 130, 246, 0.8)');
        grad.addColorStop(1, 'rgba(59, 130, 246, 0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(sx, sy, 10, 0, Math.PI * 2);
        ctx.fill();
    }

    _drawRefractionEvents(ctx, trace, atm, cellW, cellH, padding, viewType, gridH) {
        const cs = atm.cellSize;

        for (const evt of trace.refractionEvents) {
            const p = evt.position;
            const px = padding + (p.x / cs) * cellW;
            let py;
            if (viewType === 'top') {
                py = padding + (p.y / cs) * cellH;
            } else {
                py = padding + (gridH - p.z / cs) * cellH;
            }

            if (evt.type === 'total_internal_reflection') {
                ctx.fillStyle = this.colors.tir;
                ctx.beginPath();
                ctx.arc(px, py, 4, 0, Math.PI * 2);
                ctx.fill();
            } else {
                // Small refraction marker
                ctx.fillStyle = this.colors.refraction;
                ctx.globalAlpha = 0.5;
                ctx.beginPath();
                ctx.arc(px, py, 2.5, 0, Math.PI * 2);
                ctx.fill();
                ctx.globalAlpha = 1;
            }
        }
    }

    _drawLabels(ctx, w, h, padding, viewType, gridW, gridH, cellSize) {
        ctx.fillStyle = this.colors.text;
        ctx.font = '10px Inter, sans-serif';

        // X axis
        ctx.textAlign = 'center';
        ctx.fillText(`Mesafe (${gridW * cellSize}m)`, w / 2, h - 8);

        // Y axis
        ctx.save();
        ctx.translate(12, h / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.textAlign = 'center';
        ctx.fillText(viewType === 'top' ? 'Y (Yatay)' : 'Z (Yükseklik)', 0, 0);
        ctx.restore();

        // Tick marks (every 5 cells)
        ctx.font = '8px JetBrains Mono, monospace';
        ctx.fillStyle = 'rgba(148, 163, 184, 0.5)';

        const cellWpx = (w - padding * 2) / gridW;
        const cellHpx = (h - padding * 2) / gridH;

        for (let i = 0; i <= gridW; i += Math.max(1, Math.floor(gridW / 10))) {
            const x = padding + i * cellWpx;
            ctx.textAlign = 'center';
            ctx.fillText(`${(i * cellSize).toFixed(0)}`, x, h - padding + 16);
        }

        for (let i = 0; i <= gridH; i += Math.max(1, Math.floor(gridH / 5))) {
            const y = padding + i * cellHpx;
            ctx.textAlign = 'right';
            if (viewType === 'top') {
                ctx.fillText(`${(i * cellSize).toFixed(0)}`, padding - 6, y + 3);
            } else {
                ctx.fillText(`${((gridH - i) * cellSize).toFixed(0)}`, padding - 6, y + 3);
            }
        }
    }
}

window.FSORenderer = FSORenderer;
