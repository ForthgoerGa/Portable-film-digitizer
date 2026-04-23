// ── Tab navigation ────────────────────────────────────────────────────────────

document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    });
});

// ── Camera & Capture ──────────────────────────────────────────────────────────

const captureBtn       = document.getElementById("captureBtn");
const processBtn       = document.getElementById("processBtn");
const cameraStatus     = document.getElementById("cameraStatus");
const processStatus    = document.getElementById("processStatus");
const captureGallery   = document.getElementById("captureGallery");
const captureCount     = document.getElementById("captureCount");
const selectedInfo     = document.getElementById("selectedInfo");
const selectedFilename = document.getElementById("selectedFilename");
const streamOverlay    = document.getElementById("streamOverlay");
const aiResultSection  = document.getElementById("aiResultSection");
const aiMeta           = document.getElementById("aiMeta");
const aiBeforeImg      = document.getElementById("aiBeforeImg");
const aiAfterImg       = document.getElementById("aiAfterImg");

// Processing panel elements
const processingPanel    = document.getElementById("processingPanel");
const pipelineHeader     = document.getElementById("pipelineHeader");
const iterLabel          = document.getElementById("iterLabel");
const iterBar            = document.getElementById("iterBar");
const scoreLabel         = document.getElementById("scoreLabel");
const scoreBar           = document.getElementById("scoreBar");
const liveFeedback       = document.getElementById("liveFeedback");
const iterationLog       = document.getElementById("iterationLog");
const liveParamsDetails  = document.getElementById("liveParamsDetails");
const liveParams         = document.getElementById("liveParams");

let selectedCapture = null;   // filename selected in gallery
let processJobId    = null;   // current AI pipeline job
let processPoller   = null;
let _loggedIters    = 0;      // how many iterations we've already appended to the log

function setCameraStatus(msg, isError = false) {
    cameraStatus.textContent = msg;
    cameraStatus.className = "camera-status" + (isError ? " error-text" : "");
}

function setProcessStatus(msg, isError = false) {
    processStatus.textContent = msg;
    processStatus.className = "camera-status" + (isError ? " error-text" : "");
}

function selectCapture(filename) {
    selectedCapture = filename;
    selectedInfo.style.display = "flex";
    selectedFilename.textContent = filename;
    processBtn.disabled = false;
    setProcessStatus("Ready — click Process to run AI pipeline.");

    // Highlight selected thumbnail
    document.querySelectorAll(".gallery-thumb").forEach((el) => {
        el.classList.toggle("selected", el.dataset.filename === filename);
    });
}

async function triggerCapture() {
    captureBtn.disabled = true;
    streamOverlay.classList.remove("hidden");
    setCameraStatus("Capturing…");

    try {
        const r = await fetch("/camera/capture", { method: "POST" });
        const d = await r.json();
        if (d.detail || d.error) throw new Error(d.detail || d.error);

        const sizeKB = (d.jpeg_size / 1024).toFixed(0);
        const sizeMB = (d.raw_size / 1024 / 1024).toFixed(1);
        setCameraStatus(`Saved: ${d.jpeg}  (${sizeKB} KB JPEG · ${sizeMB} MB DNG)`);

        await refreshGallery();
        // Auto-select the new capture
        selectCapture(d.jpeg);
    } catch (e) {
        setCameraStatus(`Capture failed: ${e.message}`, true);
    } finally {
        captureBtn.disabled = false;
        streamOverlay.classList.add("hidden");
    }
}

async function refreshGallery() {
    try {
        const r = await fetch("/camera/captures");
        const files = await r.json();            // plain array from Pi proxy
        const jpgs = Array.isArray(files) ? files.filter((f) => f.endsWith(".jpg")) : [];

        captureCount.textContent = jpgs.length ? `(${jpgs.length})` : "";
        captureGallery.innerHTML = "";

        jpgs.slice().reverse().forEach((filename) => {
            const div = document.createElement("div");
            div.className = "gallery-thumb" + (filename === selectedCapture ? " selected" : "");
            div.dataset.filename = filename;
            div.innerHTML = `
                <img src="/camera/captures/${encodeURIComponent(filename)}" loading="lazy" alt="${filename}" />
                <span>${filename.replace("capture_", "").replace(/_\d{6}\.jpg$/, "").replace(/_/g, " ")}</span>`;
            div.addEventListener("click", () => selectCapture(filename));
            captureGallery.appendChild(div);
        });
    } catch (_) {
        // gallery refresh is best-effort
    }
}

async function startProcessing() {
    if (!selectedCapture) return;

    processBtn.disabled = true;
    setProcessStatus("Sending to PC… (ensure file is received first)");
    aiResultSection.classList.add("hidden");
    _resetProcessingPanel();

    // First make sure the capture is on the PC (send it if not)
    try {
        await fetch(`/camera/send-to-pc/${encodeURIComponent(selectedCapture)}`, { method: "POST" });
    } catch (_) { /* best effort */ }

    setProcessStatus("Running AI pipeline… this may take up to a minute.");

    try {
        const r = await fetch("/process_capture", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ filename: selectedCapture }),
        });
        const d = await r.json();
        if (d.detail) throw new Error(d.detail);
        processJobId = d.job_id;
        pollProcessStatus();
    } catch (e) {
        setProcessStatus(`Failed to start pipeline: ${e.message}`, true);
        processBtn.disabled = false;
    }
}

function _resetProcessingPanel() {
    _loggedIters = 0;
    iterationLog.innerHTML = "";
    pipelineHeader.innerHTML = "";
    iterLabel.textContent = "Iteration — / —";
    iterBar.style.width = "0%";
    scoreLabel.textContent = "Score —";
    scoreBar.style.width = "0%";
    scoreBar.className = "progress-bar score-bar";
    liveFeedback.textContent = "";
    liveFeedback.classList.add("hidden");
    liveParams.innerHTML = "";
    liveParamsDetails.classList.add("hidden");
    processingPanel.classList.remove("hidden");
    processingPanel.querySelector("h2").textContent = "AI Pipeline — Running";
}

function _updateProcessingPanel(d) {
    // Header tags: film type, modes
    const filmType = d.film_type || d.filename || "";
    const classMode = d.classifier_mode || "?";
    const evalMode  = d.evaluator_mode  || "?";
    pipelineHeader.innerHTML = [
        filmType  ? `<span class="ai-tag film-tag">${filmType}</span>` : "",
        `<span class="ai-tag mode-tag">Classifier: ${classMode}</span>`,
        `<span class="ai-tag mode-tag">Evaluator: ${evalMode}</span>`,
    ].join("");

    // Iteration progress bar
    const cur = d.current_iteration ?? 0;
    const max = d.max_iter ?? 1;
    const pct = max > 0 ? Math.round((cur / max) * 100) : 0;
    iterLabel.textContent = `Iteration ${cur} / ${max}`;
    iterBar.style.width = `${pct}%`;

    // Score bar (0–1 → 0–100%)
    const score = d.current_score ?? null;
    if (score !== null) {
        const sPct = Math.round(score * 100);
        scoreLabel.textContent = `Score ${score.toFixed(3)}`;
        scoreBar.style.width = `${sPct}%`;
        scoreBar.className = "progress-bar score-bar" +
            (sPct >= 70 ? " score-good" : sPct >= 45 ? " score-ok" : " score-low");
    }

    // Live feedback
    if (d.current_feedback) {
        liveFeedback.textContent = `"${d.current_feedback}"`;
        liveFeedback.classList.remove("hidden");
    }

    // Iterations log — append new rows only
    const log = d.iterations_log || [];
    while (_loggedIters < log.length) {
        const entry = log[_loggedIters];
        _loggedIters++;
        const row = document.createElement("div");
        row.className = "iter-row" + (entry.passed ? " iter-passed" : "");
        const sPct2 = Math.round((entry.score ?? 0) * 100);
        row.innerHTML = `
            <span class="iter-num">#${entry.iteration}</span>
            <div class="mini-score-track">
                <div class="mini-score-bar ${sPct2 >= 70 ? "score-good" : sPct2 >= 45 ? "score-ok" : "score-low"}"
                     style="width:${sPct2}%"></div>
            </div>
            <span class="iter-score">${entry.score?.toFixed(3) ?? "—"}</span>
            <span class="iter-verdict">${entry.passed ? "PASS" : "refine"}</span>
            <span class="iter-fb">${entry.feedback || ""}</span>`;
        iterationLog.appendChild(row);
    }

    // Current parameters
    const params = d.current_params;
    if (params && Object.keys(params).length > 0) {
        liveParamsDetails.classList.remove("hidden");
        liveParams.innerHTML = Object.entries(params)
            .map(([k, v]) => `<div class="param-chip"><b>${k.replace(/_/g, "\u00a0")}</b><span>${typeof v === "number" ? v.toFixed(v % 1 === 0 ? 0 : 3) : v}</span></div>`)
            .join("");
    }
}

function pollProcessStatus() {
    clearTimeout(processPoller);
    if (!processJobId) return;

    fetch(`/process_status/${processJobId}`)
        .then((r) => r.json())
        .then((d) => {
            if (d.status === "running") {
                setProcessStatus(`AI pipeline running… (iter ${d.current_iteration ?? "?"}/${d.max_iter ?? "?"})`);
                _updateProcessingPanel(d);
                processPoller = setTimeout(pollProcessStatus, 1200);
                return;
            }
            if (d.status === "error") {
                processingPanel.querySelector("h2").textContent = "AI Pipeline — Error";
                setProcessStatus(`Pipeline error: ${d.error}`, true);
                processBtn.disabled = false;
                return;
            }
            if (d.status === "done") {
                _updateProcessingPanel(d);
                processingPanel.querySelector("h2").textContent = "AI Pipeline — Complete";
                setProcessStatus(`Done — score ${d.final_score?.toFixed(3) ?? "?"} · ${d.film_type ?? ""} · ${d.iterations ?? "?"} iteration(s)`);
                processBtn.disabled = false;
                renderAiResult(d);
            }
        })
        .catch(() => {
            processPoller = setTimeout(pollProcessStatus, 2000);
        });
}

function renderAiResult(meta) {
    const t = Date.now();
    aiBeforeImg.src = `/camera/captures/${encodeURIComponent(selectedCapture)}?t=${t}`;

    const stem = selectedCapture.replace(/\.[^.]+$/, "");
    aiAfterImg.src = `/pi-processed/${encodeURIComponent(stem)}_processed.png?t=${t}`;

    const score = meta.final_score != null ? (meta.final_score * 100).toFixed(0) + "%" : "—";
    const passed = meta.passed ? "✓ Pass" : "✗ Below threshold";
    const metrics = meta.evaluation_metrics || {};
    const metricItems = Object.entries(metrics)
        .map(([k, v]) => `<span class="metric-chip"><b>${k.replace(/_/g, " ")}</b> ${Number(v).toFixed(3)}</span>`)
        .join("");

    aiMeta.innerHTML = `
        <div class="ai-meta-row">
            <span class="ai-tag film-tag">${meta.film_type ?? "unknown"}</span>
            <span class="ai-tag score-tag">Score ${score}</span>
            <span class="ai-tag ${meta.passed ? "pass-tag" : "fail-tag"}">${passed}</span>
            <span class="ai-tag mode-tag">Classifier: ${meta.classifier_mode ?? "?"}</span>
            <span class="ai-tag mode-tag">Evaluator: ${meta.evaluator_mode ?? "?"}</span>
            <span class="ai-tag iter-tag">${meta.iterations ?? 1} iteration(s)</span>
        </div>
        ${meta.feedback ? `<p class="ai-feedback">"${meta.feedback}"</p>` : ""}
        ${metricItems ? `<div class="metrics-row">${metricItems}</div>` : ""}`;

    aiResultSection.classList.remove("hidden");
    aiResultSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

captureBtn.addEventListener("click", triggerCapture);
processBtn.addEventListener("click", startProcessing);

// Add send-to-pc proxy endpoint call (route wired in server.py)
// Also expose a manual send button in the gallery via context — handled inline above.

// ── Scan Debug (existing logic) ───────────────────────────────────────────────

const startBtn       = document.getElementById("start");
const cancelBtn      = document.getElementById("cancel");
const formatSelect   = document.getElementById("format");
const badge          = document.getElementById("stateBadge");
const resultArea     = document.getElementById("resultArea");
const fractionsMeta  = document.getElementById("fractionsMeta");
const fractionsGrid  = document.getElementById("fractionsGrid");
const rawImage       = document.getElementById("rawImage");
const finalImage     = document.getElementById("finalImage");
const serialPortSelect = document.getElementById("serialPortSelect");

let lastRenderedResultKey = null;

function buildResultKey(result) {
    if (!result || !Array.isArray(result.tiles)) return null;
    const tileSignature = result.tiles.map((t) => t.filename).join("|");
    return `${result.completed_at || ""}|${result.stitched_raw_url}|${result.final_url}|${result.tile_count}|${tileSignature}|${result.cloud_url || ""}`;
}

function clearScanViews() {
    fractionsMeta.textContent = "";
    fractionsGrid.innerHTML = "";
    rawImage.removeAttribute("src");
    finalImage.removeAttribute("src");
    lastRenderedResultKey = null;
}

function renderScanViews(result) {
    if (!result || !Array.isArray(result.tiles)) { clearScanViews(); return; }
    const resultKey = buildResultKey(result);
    if (resultKey === lastRenderedResultKey) return;
    lastRenderedResultKey = resultKey;

    fractionsMeta.textContent = `${result.tile_count || result.tiles.length} fractions (${result.rows} x ${result.cols})`;
    fractionsGrid.style.gridTemplateColumns = `repeat(${Math.max(1, result.cols || 1)}, minmax(80px, 1fr))`;
    fractionsGrid.innerHTML = result.tiles
        .map((tile) => `
            <figure class="tile">
                <img src="${tile.url}" alt="fraction r${tile.row} c${tile.col}" />
                <figcaption>r${tile.row} c${tile.col}</figcaption>
            </figure>`)
        .join("");

    const cacheTag = Date.now();
    if (result.stitched_raw_url) rawImage.src = `${result.stitched_raw_url}?t=${cacheTag}`;
    if (result.final_url)        finalImage.src = `${result.final_url}?t=${cacheTag}`;
}

function updateUI(state, result) {
    badge.textContent = state.charAt(0).toUpperCase() + state.slice(1);
    badge.className = `badge ${state}`;

    const isWorking = state === "working";
    startBtn.disabled = isWorking;
    cancelBtn.disabled = !isWorking;
    formatSelect.disabled = isWorking;

    if (result?.error) {
        resultArea.innerHTML = `<span class="error-text"><b>Error:</b> ${result.error}</span>`;
        clearScanViews();
        return;
    }
    if (result?.cancelled) {
        resultArea.innerHTML = `<span><b>Scan cancelled.</b></span>`;
        clearScanViews();
        return;
    }

    if (result && result.final_url) {
        const cloudLink = result.cloud_url ? ` <a href="${result.cloud_url}" target="_blank">View uploaded image</a>` : "";
        resultArea.innerHTML = `<span><b>Scan complete.</b> ${result.final_label || "Output ready"}.${cloudLink}</span>`;
        renderScanViews(result);
        return;
    }
    if (isWorking) { resultArea.textContent = "Scanning and processing fractions..."; return; }
    resultArea.textContent = "";
}

function pollStatus() {
    fetch("/status")
        .then((r) => r.json())
        .then((d) => updateUI(d.state, d.result))
        .catch(() => {
            resultArea.innerHTML = '<span class="error-text"><b>Error:</b> Cannot reach backend.</span>';
        })
        .finally(() => setTimeout(pollStatus, 1000));
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

startBtn.addEventListener("click", startJob);
cancelBtn.addEventListener("click", () => { cancelBtn.disabled = true; fetch("/cancel", { method: "POST" }); });

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    clearScanViews();
    pollStatus();
    loadPorts();
    refreshGallery();
    setInterval(refreshGallery, 5000);   // refresh gallery every 5 s
});
