class AlignmentTool {
    constructor() {
        this.tiles = new Map();
        this.firstImage = null;
        this.secondImage = null;
        this.tileWidth = 0;
        this.tileHeight = 0;
        this.loadedPair = null;
        this.perPairMap = new Map(); // key -> {dx, dy}

        this.canvas = document.getElementById('alignmentCanvas');
        this.ctx = this.canvas.getContext('2d');
        this.axisSelect = document.getElementById('axisSelect');
        this.rowSelect = document.getElementById('rowSelect');
        this.colSelect = document.getElementById('colSelect');
        this.bandInput = document.getElementById('bandInput');
        this.viewSelect = document.getElementById('viewSelect');
        this.opacityInput = document.getElementById('opacityInput');
        this.modeSelect = document.getElementById('modeSelect');
        this.dxInput = document.getElementById('dxInput');
        this.dyInput = document.getElementById('dyInput');
        this.saveStatus = document.getElementById('saveStatus');
        this.measurementSummary = document.getElementById('measurementSummary');

        this.bindEvents();
        this.loadTiles();
        this.loadMeasurements();
    }

    bindEvents() {
        document.getElementById('loadPairBtn').addEventListener('click', () => this.loadSelectedPair());
        document.getElementById('resetPlacementBtn').addEventListener('click', () => this.resetPlacement());
        document.getElementById('saveMeasurementBtn').addEventListener('click', () => this.saveMeasurement());
        for (const input of [this.bandInput, this.viewSelect, this.opacityInput, this.modeSelect, this.dxInput, this.dyInput]) {
            input.addEventListener('input', () => this.render());
        }
        this.axisSelect.addEventListener('change', () => {
            this.populateColumns();
            this.resetPlacement();
            this.updateSaveButton();
        });
        this.rowSelect.addEventListener('change', () => {
            this.populateColumns();
            this.updateSaveButton();
        });
        this.colSelect.addEventListener('change', () => {
            this.updateSaveButton();
        });
        document.querySelectorAll('[data-nudge]').forEach((button) => {
            button.addEventListener('click', () => {
                const [dx, dy] = button.dataset.nudge.split(',').map(Number);
                this.dxInput.value = String(Number(this.dxInput.value) + dx);
                this.dyInput.value = String(Number(this.dyInput.value) + dy);
                this.render();
            });
        });
    }

    pairKey(axis, row, col, neighborRow, neighborCol) {
        return `${axis}-${row}-${col}-${neighborRow}-${neighborCol}`;
    }

    loadedPairKey() {
        if (!this.loadedPair) return null;
        const { axis, row, col, neighborRow, neighborCol } = this.loadedPair;
        return this.pairKey(axis, row, col, neighborRow, neighborCol);
    }

    selectedPairKey() {
        const axis = this.axisSelect.value;
        const row = Number(this.rowSelect.value || 0);
        const col = Number(this.colSelect.value || 0);
        const neighborRow = axis === 'x' ? row : row + 1;
        const neighborCol = axis === 'x' ? col + 1 : col;
        return this.pairKey(axis, row, col, neighborRow, neighborCol);
    }

    updateSaveButton() {
        const btn = document.getElementById('saveMeasurementBtn');
        const key = this.loadedPairKey() || this.selectedPairKey();
        btn.textContent = this.perPairMap.has(key) ? 'Update Measurement' : 'Save Measurement';
    }

    async loadTiles() {
        const response = await fetch('/captures/tiles');
        if (!response.ok) {
            this.saveStatus.textContent = 'Failed to load captures';
            return;
        }
        const data = await response.json();
        this.tiles.clear();
        for (const tile of data.tiles || []) {
            const row = Number(tile.row);
            const col = Number(tile.col);
            this.tiles.set(`${row},${col}`, { row, col, name: tile.path, path: tile.path });
        }
        this.populateRows();
        this.populateColumns();
        this.resetPlacement();
        const grid = (data.scan_profile && data.scan_profile.grid) || {};
        this.saveStatus.textContent = `${this.tiles.size} preview tiles loaded (${grid.cols || '?'} x ${grid.rows || '?'})`;
    }

    populateRows() {
        const rows = [...new Set([...this.tiles.values()].map((tile) => tile.row))].sort((a, b) => a - b);
        this.rowSelect.innerHTML = '';
        for (const row of rows) {
            const option = document.createElement('option');
            option.value = String(row);
            option.textContent = String(row);
            this.rowSelect.appendChild(option);
        }
    }

    populateColumns() {
        const row = Number(this.rowSelect.value || 0);
        const axis = this.axisSelect.value;
        const cols = [...this.tiles.values()]
            .filter((tile) => tile.row === row)
            .map((tile) => tile.col)
            .filter((col) => axis === 'x' ? this.tiles.has(`${row},${col + 1}`) : this.tiles.has(`${row + 1},${col}`))
            .sort((a, b) => a - b);
        this.colSelect.innerHTML = '';
        for (const col of cols) {
            const option = document.createElement('option');
            option.value = String(col);
            option.textContent = String(col);
            this.colSelect.appendChild(option);
        }
    }

    resetPlacement() {
        if (this.axisSelect.value === 'x') {
            this.dxInput.value = this.tileWidth ? String(this.tileWidth) : '4056';
            this.dyInput.value = '0';
        } else {
            this.dxInput.value = '0';
            this.dyInput.value = this.tileHeight ? String(this.tileHeight) : '3040';
        }
        this.render();
    }

    async loadSelectedPair() {
        const row = Number(this.rowSelect.value);
        const col = Number(this.colSelect.value);
        const axis = this.axisSelect.value;
        const neighborRow = axis === 'x' ? row : row + 1;
        const neighborCol = axis === 'x' ? col + 1 : col;
        const first = this.tiles.get(`${row},${col}`);
        const second = this.tiles.get(`${neighborRow},${neighborCol}`);
        if (!first || !second) {
            this.saveStatus.textContent = 'Selected pair is not available';
            return;
        }
        this.firstImage = await this.loadImage(first.name);
        this.secondImage = await this.loadImage(second.name);
        this.tileWidth = this.firstImage.naturalWidth;
        this.tileHeight = this.firstImage.naturalHeight;
        this.loadedPair = {
            axis,
            row,
            col,
            neighborRow,
            neighborCol,
            firstPath: first.path || first.name,
            secondPath: second.path || second.name
        };

        // Pre-populate dx/dy from saved measurement if one exists for this pair
        const key = this.loadedPairKey();
        if (key && this.perPairMap.has(key)) {
            const saved = this.perPairMap.get(key);
            this.dxInput.value = String(saved.dx);
            this.dyInput.value = String(saved.dy);
            this.render();
            this.saveStatus.textContent = `Loaded ${first.name} → ${second.name} (existing offset: dx=${saved.dx}, dy=${saved.dy})`;
        } else {
            this.resetPlacement();
            this.saveStatus.textContent = `Loaded ${first.name} → ${second.name} (no saved offset — adjust and save)`;
        }
        this.updateSaveButton();
    }

    loadImage(name) {
        return new Promise((resolve, reject) => {
            const image = new Image();
            image.onload = () => resolve(image);
            image.onerror = reject;
            image.src = `/captures/file?path=${encodeURIComponent(name)}`;
        });
    }

    render() {
        const ctx = this.ctx;
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        ctx.fillStyle = '#f4f6f8';
        ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        if (!this.firstImage || !this.secondImage) {
            ctx.fillStyle = '#4b5563';
            ctx.font = '20px sans-serif';
            ctx.fillText('Load a pair to begin alignment.', 32, 48);
            return;
        }

        const axis = this.axisSelect.value;
        const band = Math.max(128, Number(this.bandInput.value) || 900);
        const dx = Number(this.dxInput.value) || 0;
        const dy = Number(this.dyInput.value) || 0;
        const opacity = Number(this.opacityInput.value) || 0.55;
        const mode = this.modeSelect.value;
        const viewMode = this.viewSelect.value;

        const view = viewMode === 'seam'
            ? (axis === 'x'
                ? { x: this.tileWidth - band, y: 0, width: band * 2, height: this.tileHeight }
                : { x: 0, y: this.tileHeight - band, width: this.tileWidth, height: band * 2 })
            : this.fullPairView(dx, dy);
        const scale = Math.min(this.canvas.width / view.width, this.canvas.height / view.height);
        const xPad = (this.canvas.width - view.width * scale) / 2;
        const yPad = (this.canvas.height - view.height * scale) / 2;

        ctx.save();
        ctx.translate(xPad, yPad);
        ctx.scale(scale, scale);
        ctx.beginPath();
        ctx.rect(0, 0, view.width, view.height);
        ctx.clip();
        ctx.translate(-view.x, -view.y);
        ctx.drawImage(this.firstImage, 0, 0);
        ctx.globalAlpha = opacity;
        if (mode === 'difference') {
            ctx.globalCompositeOperation = 'difference';
        }
        ctx.drawImage(this.secondImage, dx, dy);
        ctx.restore();

        this.drawOverlayText(dx, dy, scale, view);
    }

    fullPairView(dx, dy) {
        const minX = Math.min(0, dx);
        const minY = Math.min(0, dy);
        const maxX = Math.max(this.tileWidth, dx + this.tileWidth);
        const maxY = Math.max(this.tileHeight, dy + this.tileHeight);
        const margin = Math.max(64, Math.round(Math.min(this.tileWidth, this.tileHeight) * 0.04));
        return {
            x: minX - margin,
            y: minY - margin,
            width: maxX - minX + margin * 2,
            height: maxY - minY + margin * 2
        };
    }

    drawOverlayText(dx, dy, scale, view) {
        const ctx = this.ctx;
        const key = this.loadedPairKey();
        const hasSaved = key && this.perPairMap.has(key);
        ctx.save();
        ctx.fillStyle = 'rgba(0, 0, 0, 0.68)';
        ctx.fillRect(16, 16, 420, hasSaved ? 108 : 86);
        ctx.fillStyle = '#ffffff';
        ctx.font = '16px sans-serif';
        ctx.fillText(`Placement: dx=${dx}, dy=${dy}`, 30, 44);
        ctx.fillText(`View: ${Math.round(view.width)} x ${Math.round(view.height)} px, scale ${scale.toFixed(3)}`, 30, 70);
        ctx.fillText('Difference mode: darker overlap = better alignment.', 30, 94);
        if (hasSaved) {
            const s = this.perPairMap.get(key);
            ctx.fillStyle = '#86efac';
            ctx.fillText(`Saved: dx=${s.dx}, dy=${s.dy}  Δ dx=${dx - s.dx}, dy=${dy - s.dy}`, 30, 118);
        }
        ctx.restore();
    }

    async saveMeasurement() {
        if (!this.loadedPair) {
            this.saveStatus.textContent = 'Load a pair before saving';
            return;
        }
        const payload = {
            axis: this.loadedPair.axis,
            row: this.loadedPair.row,
            col: this.loadedPair.col,
            neighbor_row: this.loadedPair.neighborRow,
            neighbor_col: this.loadedPair.neighborCol,
            dx: Number(this.dxInput.value) || 0,
            dy: Number(this.dyInput.value) || 0,
            tile_width: this.tileWidth,
            tile_height: this.tileHeight,
            first_path: this.loadedPair.firstPath,
            second_path: this.loadedPair.secondPath,
            note: 'manual preview alignment'
        };
        const response = await fetch('/calibration/alignment', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'save failed' }));
            this.saveStatus.textContent = `Save failed: ${error.detail}`;
            return;
        }
        const result = await response.json();
        this.saveStatus.textContent = `Saved — ${result.count} pairs total`;
        await this.loadMeasurements();
        this.updateSaveButton();
    }

    async loadMeasurements() {
        const response = await fetch('/calibration/alignment');
        if (!response.ok) return;
        const data = await response.json();
        const measurements = data.measurements || [];

        // Rebuild per-pair map (last entry per pair wins — upsert-safe)
        this.perPairMap.clear();
        for (const m of measurements) {
            const key = this.pairKey(m.axis, m.row, m.col, m.neighbor_row, m.neighbor_col);
            this.perPairMap.set(key, { dx: m.dx, dy: m.dy });
        }

        if (!this.perPairMap.size) {
            this.measurementSummary.textContent = 'No measurements saved.\nLoad a pair, align it visually, then click Save.';
            this.updateSaveButton();
            return;
        }

        // Build summary: list all pairs grouped by axis, mark uncalibrated ones
        const xPairs = [];
        const yPairs = [];
        for (const [key, { dx, dy }] of [...this.perPairMap.entries()].sort()) {
            const parts = key.split('-');
            const label = `  ${parts[1]},${parts[2]} → ${parts[3]},${parts[4]}  dx=${dx}, dy=${dy}`;
            if (parts[0] === 'x') xPairs.push(label);
            else yPairs.push(label);
        }

        // Identify uncalibrated pairs from loaded tiles
        const uncalibrated = [];
        for (const [tileKey, tile] of this.tiles) {
            const [r, c] = tileKey.split(',').map(Number);
            if (this.tiles.has(`${r},${c + 1}`)) {
                const k = this.pairKey('x', r, c, r, c + 1);
                if (!this.perPairMap.has(k)) uncalibrated.push(`  x: ${r},${c} → ${r},${c + 1}`);
            }
            if (this.tiles.has(`${r + 1},${c}`)) {
                const k = this.pairKey('y', r, c, r + 1, c);
                if (!this.perPairMap.has(k)) uncalibrated.push(`  y: ${r},${c} → ${r + 1},${c}`);
            }
        }

        const lines = [`${this.perPairMap.size} pairs calibrated`];
        if (xPairs.length) { lines.push('X (horizontal):'); lines.push(...xPairs); }
        if (yPairs.length) { lines.push('Y (vertical):'); lines.push(...yPairs); }
        if (uncalibrated.length) {
            lines.push('');
            lines.push(`${uncalibrated.length} pairs not yet calibrated:`);
            lines.push(...uncalibrated);
        } else if (this.tiles.size > 0) {
            lines.push('');
            lines.push('✓ All pairs calibrated');
        }

        this.measurementSummary.textContent = lines.join('\n');
        this.updateSaveButton();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new AlignmentTool();
});
