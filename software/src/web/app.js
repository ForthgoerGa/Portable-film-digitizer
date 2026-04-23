// Film Digitizer Scanner Web UI
// Handles REST API interactions and UI updates

class ScannerUI {
    constructor() {
        this.statusInterval = null;
        this.init();
    }

    init() {
        // Get DOM elements
        this.startScanBtn = document.getElementById('startScanBtn');
        this.cancelScanBtn = document.getElementById('cancelScanBtn');
        this.filmFormat = document.getElementById('filmFormat');
        this.scanStatus = document.getElementById('scanStatus');
        this.scanProgress = document.getElementById('scanProgress');
        this.scanPosition = document.getElementById('scanPosition');
        this.progressFill = document.getElementById('progressFill');

        this.stepSize = document.getElementById('stepSize');
        this.xReverseBtn = document.getElementById('xReverseBtn');
        this.xForwardBtn = document.getElementById('xForwardBtn');
        this.yReverseBtn = document.getElementById('yReverseBtn');
        this.yForwardBtn = document.getElementById('yForwardBtn');
        this.homeBtn = document.getElementById('homeBtn');
        this.setHomeBtn = document.getElementById('setHomeBtn');
        this.refreshCapturesBtn = document.getElementById('refreshCapturesBtn');
        this.capturesTree = document.getElementById('captures-tree');
        this.capturesPath = document.getElementById('captures-path');
        this.captureImage = document.getElementById('capture-image');

        // Bind event handlers
        this.bindEvents();

        // Start status polling
        this.startStatusPolling();
    }

    bindEvents() {
        // Scan control events
        this.startScanBtn.addEventListener('click', () => this.startScan());
        this.cancelScanBtn.addEventListener('click', () => this.cancelScan());

        // Motor control events
        this.xReverseBtn.addEventListener('click', () => this.moveMotor('x', -this.getStepSize()));
        this.xForwardBtn.addEventListener('click', () => this.moveMotor('x', this.getStepSize()));
        this.yReverseBtn.addEventListener('click', () => this.moveMotor('y', -this.getStepSize()));
        this.yForwardBtn.addEventListener('click', () => this.moveMotor('y', this.getStepSize()));
        this.homeBtn.addEventListener('click', () => this.homeMotors());
        this.setHomeBtn.addEventListener('click', () => this.setHome());

        // Captures viewer events
        this.refreshCapturesBtn.addEventListener('click', () => this.loadCapturesTree());
    }

    getStepSize() {
        return parseInt(this.stepSize.value) || 1000;
    }

    async startScan() {
        const format = this.filmFormat.value;
        this.setScanStatus('Starting scan...', 'working');

        try {
            const response = await fetch('/scan/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ format: format })
            });

            if (response.ok) {
                const data = await response.json();
                this.setScanStatus('Scan started', 'working');
            } else {
                const error = await response.json();
                this.setScanStatus(`Error: ${error.detail}`, 'error');
            }
        } catch (error) {
            this.setScanStatus('Network error', 'error');
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

    async moveMotor(axis, steps) {
        const direction = steps > 0 ? 'forward' : 'reverse';
        const axisName = axis.toUpperCase();
        this.setMotorStatus(`Moving ${axisName} ${direction}...`);

        try {
            const response = await fetch('/motor/move', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    x: axis === 'x' ? steps : 0,
                    y: axis === 'y' ? steps : 0
                })
            });

            if (response.ok) {
                this.setMotorStatus(`${axisName} moved ${Math.abs(steps)} steps`);
            } else {
                const error = await response.json();
                this.setMotorStatus(`Error: ${error.detail}`);
            }
        } catch (error) {
            this.setMotorStatus('Network error');
        }
    }

    async homeMotors() {
        this.setMotorStatus('Returning to home...');

        try {
            const response = await fetch('/motor/home', { method: 'POST' });

            if (response.ok) {
                this.setMotorStatus('Returned to home position');
            } else {
                const error = await response.json();
                this.setMotorStatus(`Error: ${error.detail}`);
            }
        } catch (error) {
            this.setMotorStatus('Network error');
        }
    }

    async setHome() {
        this.setMotorStatus('Setting home...');

        try {
            const response = await fetch('/motor/set_home', { method: 'POST' });

            if (response.ok) {
                this.setMotorStatus('Home position set');
            } else {
                const error = await response.json();
                this.setMotorStatus(`Error: ${error.detail}`);
            }
        } catch (error) {
            this.setMotorStatus('Network error');
        }
    }

    startStatusPolling() {
        this.statusInterval = setInterval(() => this.updateStatus(), 1000);
    }

    stopStatusPolling() {
        if (this.statusInterval) {
            clearInterval(this.statusInterval);
            this.statusInterval = null;
        }
    }

    async updateStatus() {
        try {
            const response = await fetch('/scan/status');
            if (response.ok) {
                const status = await response.json();
                this.updateUI(status);
            }

            const posResponse = await fetch('/motor/position');
            if (posResponse.ok) {
                const pos = await posResponse.json();
                this.motorPosition.textContent = `X: ${pos.x}, Y: ${pos.y}`;
            }
        } catch (error) {
            console.error('Status update failed:', error);
        }
    }

    updateUI(status) {
        // Update scan status
        this.scanStatus.textContent = status.state.replace('_', ' ').toUpperCase();
        this.scanStatus.className = `value status-${status.state}`;

        // Update progress
        this.scanProgress.textContent = `${status.progress.toFixed(1)}%`;
        this.progressFill.style.width = `${status.progress}%`;

        // Update grid position (scan row/col)
        this.scanPosition.textContent = `${status.current_row}, ${status.current_col}`;

        // Update button states
        const isWorking = status.state === 'scanning' || status.state === 'returning_home';
        this.startScanBtn.disabled = isWorking;
        this.cancelScanBtn.disabled = !isWorking;
    }

    setScanStatus(message, state) {
        this.scanStatus.textContent = message.toUpperCase();
        this.scanStatus.className = `value status-${state}`;
    }

    setMotorStatus(message) {
        this.motorStatus.textContent = message;
        // Auto-clear status after 3 seconds
        setTimeout(() => {
            this.motorStatus.textContent = 'Ready';
        }, 3000);
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
            this.capturesTree.appendChild(this.buildTreeElement(tree, ""));
        }
    }

    buildTreeElement(node, parentPath = '') {
        const li = document.createElement('li');
        const item = document.createElement('div');
        item.className = `tree-item ${node.type}`;
        item.textContent = node.name;

        let fullPath;
        if (!parentPath && node.type === 'directory') {
            fullPath = "";
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

// Initialize UI when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    const ui = new ScannerUI();
    ui.loadCapturesTree(); // Load captures on startup
});