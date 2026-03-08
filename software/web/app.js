const startBtn = document.getElementById("start");
const cancelBtn = document.getElementById("cancel");
const formatSelect = document.getElementById("format");
const badge = document.getElementById("stateBadge");
const resultArea = document.getElementById("resultArea");

const fractionsMeta = document.getElementById("fractionsMeta");
const fractionsGrid = document.getElementById("fractionsGrid");
const rawImage = document.getElementById("rawImage");
const finalImage = document.getElementById("finalImage");
const serialPortSelect = document.getElementById("serialPortSelect");

let lastRenderedResultKey = null;

function buildResultKey(result) {
    if (!result || !Array.isArray(result.tiles)) return null;
    const tileSignature = result.tiles.map((t) => t.filename).join("|");
    return `${result.completed_at || ""}|${result.stitched_raw_url}|${result.final_url}|${result.tile_count}|${tileSignature}`;
}

function clearScanViews() {
    fractionsMeta.textContent = "";
    fractionsGrid.innerHTML = "";
    rawImage.removeAttribute("src");
    finalImage.removeAttribute("src");
    lastRenderedResultKey = null;
}

function renderScanViews(result) {
    if (!result || !Array.isArray(result.tiles)) {
        clearScanViews();
        return;
    }

    const resultKey = buildResultKey(result);
    if (resultKey === lastRenderedResultKey) {
        return;
    }
    lastRenderedResultKey = resultKey;

    fractionsMeta.textContent = `${result.tile_count || result.tiles.length} fractions (${result.rows} x ${result.cols})`;
    fractionsGrid.style.gridTemplateColumns = `repeat(${Math.max(1, result.cols || 1)}, minmax(80px, 1fr))`;
    fractionsGrid.innerHTML = result.tiles
        .map(
            (tile) => `
            <figure class="tile">
                <img src="${tile.url}" alt="fraction r${tile.row} c${tile.col}" />
                <figcaption>r${tile.row} c${tile.col}</figcaption>
            </figure>
        `
        )
        .join("");

    const cacheTag = Date.now();
    if (result.stitched_raw_url) {
        rawImage.src = `${result.stitched_raw_url}?t=${cacheTag}`;
    }
    if (result.final_url) {
        finalImage.src = `${result.final_url}?t=${cacheTag}`;
    }
}

function updateUI(state, result) {
    badge.textContent = state.charAt(0).toUpperCase() + state.slice(1);
    badge.className = `badge ${state}`;

    const isWorking = state === "working";
    startBtn.disabled = isWorking;
    cancelBtn.disabled = !isWorking;
    formatSelect.disabled = isWorking;

    if (result && result.error) {
        resultArea.innerHTML = `<span class="error-text"><b>Error:</b> ${result.error}</span>`;
        clearScanViews();
        return;
    }

    if (result && result.cancelled) {
        resultArea.innerHTML = `<span><b>Scan cancelled.</b></span>`;
        clearScanViews();
        return;
    }

    if (result && result.final_url) {
        resultArea.innerHTML = `<span><b>Scan complete.</b> ${result.final_label || "Output ready"}.</span>`;
        renderScanViews(result);
        return;
    }

    if (isWorking) {
        resultArea.textContent = "Scanning and processing fractions...";
        return;
    }

    resultArea.textContent = "";
}

function pollStatus() {
    fetch("/status")
        .then((response) => response.json())
        .then((data) => updateUI(data.state, data.result))
        .catch(() => {
            resultArea.innerHTML = '<span class="error-text"><b>Error:</b> Cannot reach backend.</span>';
        })
        .finally(() => {
            setTimeout(pollStatus, 1000);
        });
}

function startJob() {
    fetch("/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ format: formatSelect.value }),
    })
        .then(async (res) => {
            if (!res.ok) {
                const payload = await res.json().catch(() => ({}));
                throw new Error(payload.detail || "Failed to start scan");
            }
        })
        .catch((err) => {
            resultArea.innerHTML = `<span class="error-text"><b>Error:</b> ${err.message}</span>`;
        });
}

function cancelJob() {
    cancelBtn.disabled = true;
    fetch("/cancel", { method: "POST" });
}

function loadPorts() {
    fetch("/serial/ports")
        .then((r) => r.json())
        .then((list) => {
            serialPortSelect.innerHTML = '<option value="">Select Port</option>';
            list.forEach((p) => {
                const opt = document.createElement("option");
                opt.value = p.port;
                opt.textContent = `${p.port} - ${p.description}`;
                serialPortSelect.appendChild(opt);
            });

            if (list.length > 0) {
                serialPortSelect.value = list[0].port;
                serialPortSelect.onchange();
            }
        });
}

serialPortSelect.onchange = () => {
    const port = serialPortSelect.value;
    if (port) {
        fetch("/serial/connect", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ port }),
        });
    } else {
        fetch("/serial/disconnect", { method: "POST" });
    }
};

startBtn.onclick = startJob;
cancelBtn.onclick = cancelJob;

document.addEventListener("DOMContentLoaded", () => {
    clearScanViews();
    pollStatus();
    loadPorts();
});
