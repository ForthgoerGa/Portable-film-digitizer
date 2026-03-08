const startBtn = document.getElementById('start');
const cancelBtn = document.getElementById('cancel');
const formatSelect = document.getElementById('format');
const badge = document.getElementById('stateBadge');
const resultArea = document.getElementById('resultArea');

function updateUI(state, result) {
    badge.textContent = state.charAt(0).toUpperCase() + state.slice(1);
    badge.className = 'badge ' + state;
    startBtn.disabled = (state === 'working');  // Allow starting even in error state
    cancelBtn.disabled = (state !== 'working');
    if (result && result.url) resultArea.innerHTML = `<b>Image URL:</b> <a href="${result.url}" target="_blank">${result.url}</a>`;
    else if (result && result.error) resultArea.innerHTML = `<span style="color:#d32f2f"><b>Error:</b> ${result.error}</span>`;
    else resultArea.textContent = '';
}

function pollStatus() {
    fetch('/status').then(r => r.json()).then(data => updateUI(data.state, data.result));
    setTimeout(pollStatus, 1000);
}

function startJob() {
    startBtn.disabled = true;
    formatSelect.disabled = true;
    fetch('/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ format: formatSelect.value })
    }).then(res => {
        if (!res.ok) res.json().then(data => alert(data.detail || 'Error'));
    });
}

function resetJob() {
    fetch('/reset', { method: 'POST' });
}

function cancelJob() {
    cancelBtn.disabled = true;
    fetch('/cancel', { method: 'POST' });
}

startBtn.onclick = startJob;
cancelBtn.onclick = cancelJob;

// Serial controls - simple API calls
const serialPortSelect = document.getElementById('serialPortSelect');

function loadPorts() {
    fetch('/serial/ports').then(r => r.json()).then(list => {
        // Clear existing options except the first
        serialPortSelect.innerHTML = '<option value="">Select Port</option>';
        list.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.port;
            opt.textContent = `${p.port} - ${p.description}`;
            serialPortSelect.appendChild(opt);
        });
        // Select the first port by default if available
        if (list.length > 0) {
            serialPortSelect.value = list[0].port;
            // Auto-connect to the first port
            serialPortSelect.onchange();
        }
    });
}

serialPortSelect.onchange = () => {
    const port = serialPortSelect.value;
    if (port) {
        fetch('/serial/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ port: port })
        });
    } else {
        fetch('/serial/disconnect', { method: 'POST' });
    }
};

document.addEventListener('DOMContentLoaded', () => {
    pollStatus();
    loadPorts();
});
