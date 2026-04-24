// Film Digitizer Scanner Web UI
// Handles REST API interactions and UI updates

class ScannerUI {
    constructor() {
        this.scanStatusInterval = null;
        this.motorStatusInterval = null;
        this.init();
    }

    init() {
        this.startScanBtn = document.getElementById('startScanBtn');
        this.cancelScanBtn = document.getElementById('cancelScanBtn');
        this.scanProfile = document.getElementById('scanProfile');
        this.scanStatus = document.getElementById('scanStatus');
        this.scanProgress = document.getElementById('scanProgress');
        this.scanPosition = document.getElementById('scanPosition');
        this.progressFill = document.getElementById('progressFill');

        this.motorXState = document.getElementById('motorXState');
        this.motorXPosition = document.getElementById('motorXPosition');
        this.motorYState = document.getElementById('motorYState');
        this.motorYPosition = document.getElementById('motorYPosition');

        this.refreshCapturesBtn = document.getElementById('refreshCapturesBtn');
        this.capturesTree = document.getElementById('captures-tree');
        this.capturesPath = document.getElementById('captures-path');
        this.captureImage = document.getElementById('capture-image');

        this.moveStepInput = document.getElementById('moveStepInput');
        this.moveLimit = document.getElementById('moveLimit');
        this.moveStatus = document.getElementById('moveStatus');
        this.targetXInput = document.getElementById('targetXInput');
        this.targetYInput = document.getElementById('targetYInput');
        this.cameraPreview = document.getElementById('cameraPreview');
        this.togglePreviewBtn = document.getElementById('togglePreviewBtn');
        this.checkpointLabelInput = document.getElementById('checkpointLabelInput');
        this.checkpointNoteInput = document.getElementById('checkpointNoteInput');
        this.checkpointList = document.getElementById('checkpointList');
        this.moveButtons = [
            document.getElementById('moveXNegBtn'),
            document.getElementById('moveXPosBtn'),
            document.getElementById('moveYNegBtn'),
            document.getElementById('moveYPosBtn'),
            document.getElementById('homeMotorsBtn'),
            document.getElementById('setZeroBtn'),
            document.getElementById('moveToBtn'),
            document.getElementById('saveCheckpointBtn')
        ];
        this.maxMove = { x: 0, y: 0 };
        this.maxPosition = { x: 0, y: 0 };
        this.scanState = 'idle';
        this.previewInterval = null;

        this.bindEvents();
        this.loadScanConfig();
        this.startScanStatusPolling();
        this.startMotorStatusPolling();
    }

    bindEvents() {
        this.startScanBtn.addEventListener('click', () => this.startScan());
        this.cancelScanBtn.addEventListener('click', () => this.cancelScan());
        this.refreshCapturesBtn.addEventListener('click', () => this.loadCapturesTree());
        document.getElementById('moveXNegBtn').addEventListener('click', () => this.moveAxis('x', -1));
        document.getElementById('moveXPosBtn').addEventListener('click', () => this.moveAxis('x', 1));
        document.getElementById('moveYNegBtn').addEventListener('click', () => this.moveAxis('y', -1));
        document.getElementById('moveYPosBtn').addEventListener('click', () => this.moveAxis('y', 1));
        document.getElementById('homeMotorsBtn').addEventListener('click', () => this.homeMotors());
        document.getElementById('setZeroBtn').addEventListener('click', () => this.setZero());
        document.getElementById('moveToBtn').addEventListener('click', () => this.moveToPosition());
        document.getElementById('refreshPreviewBtn').addEventListener('click', () => this.refreshPreviewFrame());
        document.getElementById('togglePreviewBtn').addEventListener('click', () => this.togglePreview());
        document.getElementById('saveCheckpointBtn').addEventListener('click', () => this.saveCheckpoint());
    }

    async startScan() {
        this.setScanStatus('Starting scan...', 'working');

        try {
            const response = await fetch('/scan/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ profile: 'standard' })
            });

            if (response.ok) {
                this.setScanStatus('Scan started', 'working');
            } else {
                const error = await response.json();
                this.setScanStatus(`Error: ${error.detail}`, 'error');
            }
        } catch (error) {
            this.setScanStatus('Network error', 'error');
        }
    }

    async loadScanConfig() {
        if (!this.scanProfile) {
            return;
        }
        try {
            const response = await fetch('/scan/config');
            if (!response.ok) {
                return;
            }
            const config = await response.json();
            const grid = config.grid || {};
            const steps = config.steps_per_segment || {};
            const maxPosition = config.max_position || {};
            this.maxMove.x = Number(steps.x || 0);
            this.maxMove.y = Number(steps.y || 0);
            this.maxPosition.x = Number(maxPosition.x || 0);
            this.maxPosition.y = Number(maxPosition.y || 0);
            const maxInput = Math.max(this.maxMove.x, this.maxMove.y, 1);
            this.moveStepInput.max = String(maxInput);
            this.targetXInput.max = String(this.maxPosition.x || 0);
            this.targetYInput.max = String(this.maxPosition.y || 0);
            this.moveLimit.textContent = `Max X ${this.maxMove.x || '?'}, Y ${this.maxMove.y || '?'}`;
            this.scanProfile.textContent = `Standard (${grid.cols || '?'} x ${grid.rows || '?'}, X ${steps.x || '?'}, Y ${steps.y || '?'})`;
            this.loadCheckpoints();
        } catch (error) {
            console.error('Scan config load failed:', error);
        }
    }

    async cancelScan() {
        this.setScanStatus('Cancelling...', 'working');

        try {
            const response = await fetch('/scan/cancel', { method: 'POST' });

            if (response.ok) {
                this.setScanStatus('Cancel requested', 'working');
            } else {
                this.setScanStatus('Cancel failed', 'error');
            }
        } catch (error) {
            this.setScanStatus('Network error', 'error');
        }
    }

    startScanStatusPolling() {
        this.scanStatusInterval = setInterval(() => this.updateScanStatus(), 1000);
        this.updateScanStatus();
    }

    startMotorStatusPolling() {
        this.motorStatusInterval = setInterval(() => this.updateMotorStatus(), 250);
        this.updateMotorStatus();
    }

    stopScanStatusPolling() {
        if (this.scanStatusInterval) {
            clearInterval(this.scanStatusInterval);
            this.scanStatusInterval = null;
        }
    }

    stopMotorStatusPolling() {
        if (this.motorStatusInterval) {
            clearInterval(this.motorStatusInterval);
            this.motorStatusInterval = null;
        }
    }

    async updateScanStatus() {
        try {
            const response = await fetch('/scan/status');
            if (!response.ok) {
                return;
            }

            const status = await response.json();
            this.scanState = status.state;
            this.scanStatus.textContent = status.state.replace('_', ' ').toUpperCase();
            this.scanStatus.className = `value status-${status.state}`;
            this.scanProgress.textContent = `${status.progress.toFixed(1)}%`;
            this.progressFill.style.width = `${status.progress}%`;
            this.scanPosition.textContent = `${status.current_row}, ${status.current_col}`;

            const isWorking = ['scanning', 'stitching', 'uploading', 'returning_home'].includes(status.state);
            this.startScanBtn.disabled = isWorking;
            this.cancelScanBtn.disabled = !isWorking;
            this.setMoveButtonsDisabled(isWorking);
        } catch (error) {
            console.error('Scan status update failed:', error);
        }
    }

    async updateMotorStatus() {
        try {
            const response = await fetch('/motor/status');
            if (!response.ok) {
                return;
            }

            const motor = await response.json();
            this.updateMotorRow(this.motorXState, this.motorXPosition, motor.x);
            this.updateMotorRow(this.motorYState, this.motorYPosition, motor.y);
            this.targetXInput.placeholder = String(motor.x.position);
            this.targetYInput.placeholder = String(motor.y.position);
            const motorMoving = motor.x.mode === 'moving' || motor.y.mode === 'moving';
            const scanWorking = ['scanning', 'stitching', 'uploading', 'returning_home'].includes(this.scanState);
            this.setMoveButtonsDisabled(motorMoving || scanWorking);
        } catch (error) {
            console.error('Motor status update failed:', error);
        }
    }

    updateMotorRow(stateNode, positionNode, motor) {
        stateNode.textContent = motor.mode.toUpperCase();
        stateNode.className = `motor-state status-${motor.mode}`;
        positionNode.textContent = String(motor.position);
    }

    setScanStatus(message, state) {
        this.scanStatus.textContent = message.toUpperCase();
        this.scanStatus.className = `value status-${state}`;
    }

    setMoveButtonsDisabled(disabled) {
        this.moveButtons.forEach(button => {
            if (button) {
                button.disabled = disabled;
            }
        });
    }

    readMoveSteps(axis) {
        const requested = Math.abs(Number(this.moveStepInput.value) || 0);
        const limit = Number(this.maxMove[axis] || 0);
        if (!requested) {
            throw new Error('Move step must be greater than 0');
        }
        if (limit && requested > limit) {
            throw new Error(`${axis.toUpperCase()} move ${requested} exceeds safe limit ${limit}`);
        }
        return requested;
    }

    async moveAxis(axis, sign) {
        try {
            const steps = this.readMoveSteps(axis) * sign;
            const body = axis === 'x' ? { x: steps, y: 0 } : { x: 0, y: steps };
            this.moveStatus.textContent = `Moving ${axis.toUpperCase()} ${steps}...`;
            const response = await fetch('/scan/move', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            if (!response.ok) {
                const error = await response.json();
                this.moveStatus.textContent = `Move failed: ${error.detail}`;
                return;
            }
            this.moveStatus.textContent = `Moved ${axis.toUpperCase()} ${steps}`;
        } catch (error) {
            this.moveStatus.textContent = error.message || 'Move failed';
        }
    }

    async homeMotors() {
        this.moveStatus.textContent = 'Homing motors...';
        try {
            const response = await fetch('/scan/home', { method: 'POST' });
            if (!response.ok) {
                const error = await response.json();
                this.moveStatus.textContent = `Home failed: ${error.detail}`;
                return;
            }
            this.moveStatus.textContent = 'Home complete';
        } catch (error) {
            this.moveStatus.textContent = 'Home request failed';
        }
    }

    async setZero() {
        this.moveStatus.textContent = 'Setting current position as zero...';
        try {
            const response = await fetch('/scan/set_home', { method: 'POST' });
            if (!response.ok) {
                const error = await response.json();
                this.moveStatus.textContent = `Set zero failed: ${error.detail}`;
                return;
            }
            this.targetXInput.value = '0';
            this.targetYInput.value = '0';
            this.moveStatus.textContent = 'Current position set as zero';
        } catch (error) {
            this.moveStatus.textContent = 'Set zero request failed';
        }
    }

    readTargetPosition() {
        const x = Number(this.targetXInput.value);
        const y = Number(this.targetYInput.value);
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
            throw new Error('Target X/Y must be numbers');
        }
        if (x < 0 || y < 0) {
            throw new Error('Target X/Y must be non-negative');
        }
        if (this.maxPosition.x && x > this.maxPosition.x) {
            throw new Error(`Target X exceeds boundary ${this.maxPosition.x}`);
        }
        if (this.maxPosition.y && y > this.maxPosition.y) {
            throw new Error(`Target Y exceeds boundary ${this.maxPosition.y}`);
        }
        return { x: Math.round(x), y: Math.round(y) };
    }

    async moveToPosition() {
        try {
            const target = this.readTargetPosition();
            this.moveStatus.textContent = `Moving to X ${target.x}, Y ${target.y}...`;
            const response = await fetch('/scan/move_to', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(target)
            });
            if (!response.ok) {
                const error = await response.json();
                this.moveStatus.textContent = `Move to failed: ${error.detail}`;
                return;
            }
            this.moveStatus.textContent = `Moved to X ${target.x}, Y ${target.y}`;
        } catch (error) {
            this.moveStatus.textContent = error.message || 'Move to failed';
        }
    }

    async refreshPreviewFrame() {
        const url = `/camera/preview.jpg?ts=${Date.now()}`;
        this.cameraPreview.src = url;
        this.cameraPreview.style.display = 'block';
    }

    togglePreview() {
        if (this.previewInterval) {
            clearInterval(this.previewInterval);
            this.previewInterval = null;
            this.togglePreviewBtn.textContent = 'Start Preview';
            return;
        }
        this.refreshPreviewFrame();
        this.previewInterval = setInterval(() => this.refreshPreviewFrame(), 1200);
        this.togglePreviewBtn.textContent = 'Stop Preview';
    }

    async saveCheckpoint() {
        this.moveStatus.textContent = 'Saving checkpoint...';
        try {
            const response = await fetch('/calibration/checkpoints', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    label: this.checkpointLabelInput.value,
                    note: this.checkpointNoteInput.value
                })
            });
            if (!response.ok) {
                const error = await response.json();
                this.moveStatus.textContent = `Save checkpoint failed: ${error.detail}`;
                return;
            }
            const result = await response.json();
            this.moveStatus.textContent = `Saved checkpoint ${result.count}`;
            this.loadCheckpoints();
        } catch (error) {
            this.moveStatus.textContent = 'Save checkpoint request failed';
        }
    }

    async loadCheckpoints() {
        try {
            const response = await fetch('/calibration/checkpoints');
            if (!response.ok) {
                return;
            }
            const data = await response.json();
            const checkpoints = data.checkpoints || [];
            if (!checkpoints.length) {
                this.checkpointList.textContent = 'No checkpoints saved.';
                return;
            }
            this.checkpointList.textContent = checkpoints.slice(-12).map((item, index) => {
                return `${index + 1}. ${item.label}: X ${item.x}, Y ${item.y} ${item.note || ''}`;
            }).join('\n');
        } catch (error) {
            console.error('Checkpoint load failed:', error);
        }
    }

    async loadCapturesTree() {
        try {
            const response = await fetch('/captures/tree');
            if (response.ok) {
                const tree = await response.json();
                this.renderCapturesTree(tree);
            } else {
                console.error('Failed to load captures tree');
            }
        } catch (error) {
            console.error('Error loading captures tree:', error);
        }
    }

    renderCapturesTree(tree) {
        this.capturesTree.innerHTML = '';
        if (tree) {
            this.capturesTree.appendChild(this.buildTreeElement(tree, ''));
        }
    }

    buildTreeElement(node, parentPath = '') {
        const li = document.createElement('li');
        const item = document.createElement('div');
        item.className = `tree-item ${node.type}`;
        item.textContent = node.name;

        let fullPath;
        if (!parentPath && node.type === 'directory') {
            fullPath = '';
        } else {
            fullPath = parentPath ? `${parentPath}/${node.name}` : node.name;
        }

        li.appendChild(item);

        if (node.type === 'directory' && node.children) {
            const ul = document.createElement('ul');
            ul.className = 'tree-children tree-collapsed';

            item.onclick = (e) => {
                e.stopPropagation();
                ul.classList.toggle('tree-collapsed');
                item.classList.toggle('tree-expanded');
                this.selectCaptureItem(fullPath, node.type);
            };

            node.children.forEach(child => {
                ul.appendChild(this.buildTreeElement(child, fullPath));
            });
            li.appendChild(ul);
        } else {
            item.onclick = () => this.selectCaptureItem(fullPath, node.type);
        }

        return li;
    }

    async selectCaptureItem(path, type) {
        this.capturesPath.textContent = path;

        if (type === 'file') {
            this.captureImage.src = `/captures/file?path=${encodeURIComponent(path)}`;
            this.captureImage.style.display = 'block';
        } else {
            this.captureImage.style.display = 'none';
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const ui = new ScannerUI();
    ui.loadCapturesTree();
});
