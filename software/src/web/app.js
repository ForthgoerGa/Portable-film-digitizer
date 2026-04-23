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
        this.filmFormat = document.getElementById('filmFormat');
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

        this.bindEvents();
        this.startScanStatusPolling();
        this.startMotorStatusPolling();
    }

    bindEvents() {
        this.startScanBtn.addEventListener('click', () => this.startScan());
        this.cancelScanBtn.addEventListener('click', () => this.cancelScan());
        this.refreshCapturesBtn.addEventListener('click', () => this.loadCapturesTree());
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
            this.scanStatus.textContent = status.state.replace('_', ' ').toUpperCase();
            this.scanStatus.className = `value status-${status.state}`;
            this.scanProgress.textContent = `${status.progress.toFixed(1)}%`;
            this.progressFill.style.width = `${status.progress}%`;
            this.scanPosition.textContent = `${status.current_row}, ${status.current_col}`;

            const isWorking = status.state === 'scanning' || status.state === 'returning_home';
            this.startScanBtn.disabled = isWorking;
            this.cancelScanBtn.disabled = !isWorking;
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
