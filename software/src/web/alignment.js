class AlignmentTool {
    constructor() {
        this.tiles = new Map();
        this.firstImage = null;
        this.secondImage = null;
        this.tileWidth = 0;
        this.tileHeight = 0;
        this.loadedPair = null;
        this.perPairMap = new Map(); // key -> {dx, dy}
        this.scanProfile = null;

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
        document.getElementById('saveDefaultProfileBtn').addEventListener('click', () => this.saveDefaultProfile());
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

    neighborFor(axis, row, col) {
        return axis === 'x'
            ? { row, col: col + 1 }
            : { row: row + 1, col };
    }

    selectedPairKey() {
        const axis = this.axisSelect.value;
        const row = Number(this.rowSelect.value || 0);
        const col = Number(this.colSelect.value || 0);
        const neighbor = this.neighborFor(axis, row, col);
        return this.pairKey(axis, row, col, neighbor.row, neighbor.col);
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
        this.scanProfile = data.scan_profile || {};
        this.tiles.clear();
        for (const tile of data.tiles || []) {
            const row = Number(tile.row);
            const col = Number(tile.col);
            this.tiles.set(`${row},${col}`, {
                row,
                col,
                scanRow: Number(tile.scan_row ?? row),
                scanCol: Number(tile.scan_col ?? col),
                coordinateSpace: tile.coordinate_space || data.coordinate_space || 'physical_grid',
                name: tile.path,
                path: tile.path
            });
        }
        this.populateRows();
        this.populateColumns();
        this.resetPlacement();
        const grid = (data.scan_profile && data.scan_profile.grid) || {};
        this.saveStatus.textContent = `${this.tiles.size} preview tiles loaded (${grid.cols || '?'} x ${grid.rows || '?'}, physical grid)`;
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
            .filter((col) => {
                const neighbor = this.neighborFor(axis, row, col);
                return this.tiles.has(`${neighbor.row},${neighbor.col}`);
            })
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
        const stride = (this.scanProfile && this.scanProfile.stitch_stride_px) || {};
        const reversed = !!(this.scanProfile && this.scanProfile.stitch_x_axis_reversed);
        if (this.axisSelect.value === 'x') {
            const defaultDx = Number(stride.x || this.tileWidth || 4056);
            this.dxInput.value = String(reversed ? -defaultDx : defaultDx);
            this.dyInput.value = '0';
        } else {
            this.dxInput.value = '0';
            this.dyInput.value = String(Number(stride.y || this.tileHeight || 3040));
        }
        this.render();
    }

    async loadSelectedPair() {
        const row = Number(this.rowSelect.value);
        const col = Number(this.colSelect.value);
        const axis = this.axisSelect.value;
        const neighbor = this.neighborFor(axis, row, col);
        const neighborRow = neighbor.row;
        const neighborCol = neighbor.col;
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
            firstScanRow: first.scanRow,
            firstScanCol: first.scanCol,
            secondScanRow: second.scanRow,
            secondScanCol: second.scanCol,
            firstPath: first.path || first.name,
            secondPath: second.path || second.name
        };

        const key = this.loadedPairKey();
        if (key && this.perPairMap.has(key)) {
            const saved = this.perPairMap.get(key);
            this.dxInput.value = String(saved.dx);
            this.dyInput.value = String(saved.dy);
            this.render();
            this.saveStatus.textContent = `Loaded ${first.name} -> ${second.name} (existing offset: dx=${saved.dx}, dy=${saved.dy})`;
        } else {
            this.resetPlacement();
            this.saveStatus.textContent = `Loaded ${first.name} -> ${second.name} (no saved offset - adjust and save)`;
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
            ? this.seamView(axis, dx, dy, band)
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

    seamView(axis, dx, dy, band) {
        if (axis === 'x') {
            return dx >= 0
                ? { x: this.tileWidth - band, y: 0, width: band * 2, height: this.tileHeight }
                : { x: dx, y: 0, width: band * 2, height: this.tileHeight };
        }
        return dy >= 0
            ? { x: 0, y: this.tileHeight - band, width: this.tileWidth, height: band * 2 }
            : { x: 0, y: dy, width: this.tileWidth, height: band * 2 };
    }

    drawOverlayText(dx, dy, scale, view) {
        const ctx = this.ctx;
        const key = this.loadedPairKey();
        const hasSaved = key && this.perPairMap.has(key);
        ctx.save();
        ctx.fillStyle = 'rgba(0, 0, 0, 0.68)';
        ctx.fillRect(16, 16, 460, hasSaved ? 108 : 86);
        ctx.fillStyle = '#ffffff';
        ctx.font = '16px sans-serif';
        ctx.fillText(`Placement: dx=${dx}, dy=${dy}`, 30, 44);
        ctx.fillText(`View: ${Math.round(view.width)} x ${Math.round(view.height)} px, scale ${scale.toFixed(3)}`, 30, 70);
        ctx.fillText('Difference mode: darker overlap = better alignment.', 30, 94);
        if (hasSaved) {
            const s = this.perPairMap.get(key);
            ctx.fillStyle = '#86efac';
            ctx.fillText(`Saved: dx=${s.dx}, dy=${s.dy}  delta dx=${dx - s.dx}, dy=${dy - s.dy}`, 30, 118);
        }
        ctx.restore();
    }

    async saveMeasurement() {
        if (!this.loadedPair) {
            this.saveStatus.textContent = 'Load a pair before saving';
            return;
        }
        const payload = {
            coordinate_space: 'physical_grid',
            axis: this.loadedPair.axis,
            row: this.loadedPair.row,
            col: this.loadedPair.col,
            neighbor_row: this.loadedPair.neighborRow,
            neighbor_col: this.loadedPair.neighborCol,
            dx: Number(this.dxInput.value) || 0,
            dy: Number(this.dyInput.value) || 0,
            first_scan_row: this.loadedPair.firstScanRow,
            first_scan_col: this.loadedPair.firstScanCol,
            second_scan_row: this.loadedPair.secondScanRow,
            second_scan_col: this.loadedPair.secondScanCol,
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
        this.saveStatus.textContent = `Saved - ${result.count} pairs total`;
        await this.loadMeasurements();
        this.updateSaveButton();
    }

    async saveDefaultProfile() {
        this.saveStatus.textContent = 'Saving default profile...';
        const response = await fetch('/calibration/alignment/default-profile', { method: 'POST' });
        const result = await response.json().catch(() => ({ detail: 'save failed' }));
        if (!response.ok) {
            this.saveStatus.textContent = `Default profile failed: ${result.detail || 'save failed'}`;
            return;
        }
        this.saveStatus.textContent = `Default profile saved - ${result.measurement_count} pairs`;
        await this.loadMeasurements();
    }

    async loadMeasurements() {
        const response = await fetch('/calibration/alignment');
        if (!response.ok) return;
        const data = await response.json();
        const measurements = data.effective_measurements || data.measurements || [];
        const defaultProfile = data.default_profile || {};

        this.perPairMap.clear();
        for (const m of measurements) {
            const key = this.pairKey(m.axis, m.row, m.col, m.neighbor_row, m.neighbor_col);
            this.perPairMap.set(key, { dx: m.dx, dy: m.dy, source: m.source || 'current_scan' });
        }

        if (!this.perPairMap.size) {
            const defaultLine = defaultProfile.configured
                ? `Default profile exists (${defaultProfile.measurement_count} pairs), but none matched the current grid.`
                : 'No default profile saved.';
            this.measurementSummary.textContent = `No measurements saved.\n${defaultLine}\nLoad a pair, align it visually, then click Save.`;
            this.updateSaveButton();
            return;
        }

        const xPairs = [];
        const yPairs = [];
        for (const [key, { dx, dy, source }] of [...this.perPairMap.entries()].sort()) {
            const parts = key.split('-');
            const suffix = source === 'default_profile' ? ' [default]' : '';
            const label = `  ${parts[1]},${parts[2]} -> ${parts[3]},${parts[4]}  dx=${dx}, dy=${dy}${suffix}`;
            if (parts[0] === 'x') xPairs.push(label);
            else yPairs.push(label);
        }

        const uncalibrated = [];
        for (const [tileKey] of this.tiles) {
            const [r, c] = tileKey.split(',').map(Number);
            const right = this.neighborFor('x', r, c);
            if (this.tiles.has(`${right.row},${right.col}`)) {
                const k = this.pairKey('x', r, c, right.row, right.col);
                if (!this.perPairMap.has(k)) uncalibrated.push(`  x: ${r},${c} -> ${right.row},${right.col}`);
            }
            const down = this.neighborFor('y', r, c);
            if (this.tiles.has(`${down.row},${down.col}`)) {
                const k = this.pairKey('y', r, c, down.row, down.col);
                if (!this.perPairMap.has(k)) uncalibrated.push(`  y: ${r},${c} -> ${down.row},${down.col}`);
            }
        }

        const lines = [`${this.perPairMap.size} pairs calibrated`];
        if (defaultProfile.configured) {
            lines.push(`Default profile: ${defaultProfile.measurement_count} pairs`);
        } else {
            lines.push('Default profile: not saved');
        }
        if (xPairs.length) { lines.push('X (horizontal):'); lines.push(...xPairs); }
        if (yPairs.length) { lines.push('Y (vertical):'); lines.push(...yPairs); }
        if (uncalibrated.length) {
            lines.push('');
            lines.push(`${uncalibrated.length} pairs not yet calibrated:`);
            lines.push(...uncalibrated);
        } else if (this.tiles.size > 0) {
            lines.push('');
            lines.push('All pairs calibrated');
        }

        this.measurementSummary.textContent = lines.join('\n');
        this.updateSaveButton();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new AlignmentTool();
});
