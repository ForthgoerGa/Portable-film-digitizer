const ACTIVE_JOB_KEY = "activeJobId";
const LAST_JOB_KEY = "lastJobId";

const STAGES = [
    { key: "dispatch", label: "Dispatching job to Pi" },
    { key: "scan", label: "Scanning frames" },
    { key: "stitch", label: "Stitching raw artifact" },
    { key: "transfer", label: "Transferring stitched raw to PC" },
    { key: "classify", label: "Classifying film" },
    { key: "process", label: "Post-processing" },
    { key: "complete", label: "Complete" },
];

const stageLookup = Object.fromEntries(STAGES.map((s) => [s.key, s.label]));

// DOM refs
const scanBtn = document.getElementById("scanBtn");
const cancelBtn = document.getElementById("cancelBtn");
const jobStatusText = document.getElementById("jobStatusText");
const progressSection = document.getElementById("progressSection");
const stageLabel = document.getElementById("stageLabel");
const progressPct = document.getElementById("progressPct");
const unifiedBar = document.getElementById("unifiedBar");
const resultsSection = document.getElementById("resultsSection");

const rawTileGrid = document.getElementById("rawTileGrid");
const rawGridPlaceholder = document.getElementById("rawGridPlaceholder");
const rawTileCount = document.getElementById("rawTileCount");

const procTileGrid = document.getElementById("procTileGrid");
const procGridPlaceholder = document.getElementById("procGridPlaceholder");
const procTileCount = document.getElementById("procTileCount");
const finalImg = document.getElementById("finalImg");

// Job state
let activeJobId = null;
let jobPoller = null;

// Tile grid state (reset per job)
let _gridBuilt = false;
let _gridRows = 0;
let _gridCols = 0;
let _rawLoaded = new Set();
let _procLoaded = new Set();
let _finalShown = false;

// ---------------------------------------------------------------------------
// Stage steps
// ---------------------------------------------------------------------------

function buildStageSteps() {
    const container = document.getElementById("stageSteps");
    container.innerHTML = STAGES.map((stage, i) => {
        const connector = i < STAGES.length - 1 ? '<div class="stage-connector"></div>' : "";
        return `
            <div class="stage-step" data-stage="${stage.key}">
                <div class="stage-dot"></div>
                <span>${stage.label}</span>
            </div>
            ${connector}`;
    }).join("");
}

function updateStageSteps(currentStage) {
    const currentIndex = STAGES.findIndex((s) => s.key === currentStage);
    document.querySelectorAll(".stage-step").forEach((el, i) => {
        el.classList.toggle("done", currentIndex >= 0 && i < currentIndex);
        el.classList.toggle("active", el.dataset.stage === currentStage);
        el.classList.toggle("pending", currentIndex < 0 || (i > currentIndex && el.dataset.stage !== currentStage));
    });
}

// ---------------------------------------------------------------------------
// System status
// ---------------------------------------------------------------------------

function setIndicator(dotId, labelId, isOnline, baseLabel) {
    const dot = document.getElementById(dotId);
    const label = document.getElementById(labelId);
    dot.className = `sys-dot ${isOnline ? "online" : "offline"}`;
    label.textContent = isOnline ? baseLabel : `${baseLabel} (offline)`;
}

async function checkSystemStatus() {
    try {
        const data = await (await fetch("/api/system/status")).json();
        setIndicator("piScannerDot", "piScannerLabel", !!data.pi_scanner_reachable, "Pi Scanner");
    } catch (_) {
        setIndicator("piScannerDot", "piScannerLabel", false, "Pi Scanner");
    }
}

// ---------------------------------------------------------------------------
// Tile grid
// ---------------------------------------------------------------------------

function _buildTileGrid(rows, cols) {
    if (_gridBuilt || rows <= 0 || cols <= 0) return;
    _gridBuilt = true;
    _gridRows = rows;
    _gridCols = cols;

    rawTileGrid.style.setProperty("--tile-cols", cols);
    procTileGrid.style.setProperty("--tile-cols", cols);
    rawTileGrid.innerHTML = "";
    procTileGrid.innerHTML = "";

    for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
            const stem = `row_${r}_col_${c}`;
            rawTileGrid.insertAdjacentHTML("beforeend", `<div class="tile-cell" id="raw_${stem}"></div>`);
            procTileGrid.insertAdjacentHTML("beforeend", `<div class="tile-cell" id="proc_${stem}"></div>`);
        }
    }

    rawGridPlaceholder.classList.add("hidden");
    rawTileGrid.classList.remove("hidden");
    procGridPlaceholder.classList.add("hidden");
    procTileGrid.classList.remove("hidden");
}

function _setRawTile(stem, url) {
    if (_rawLoaded.has(stem)) return;
    const cell = document.getElementById(`raw_${stem}`);
    if (!cell) return;
    _rawLoaded.add(stem);
    const img = new Image();
    img.src = `${url}?t=${Date.now()}`;
    img.alt = stem;
    cell.appendChild(img);
    const total = _gridRows * _gridCols || "?";
    rawTileCount.textContent = `${_rawLoaded.size}/${total}`;
}

function _setProcTile(stem, url) {
    if (_procLoaded.has(stem)) return;
    const cell = document.getElementById(`proc_${stem}`);
    if (!cell) return;
    _procLoaded.add(stem);
    const img = new Image();
    img.src = `${url}?t=${Date.now()}`;
    img.alt = stem;
    cell.appendChild(img);
    const total = _gridRows * _gridCols || "?";
    procTileCount.textContent = `${_procLoaded.size}/${total}`;
}

function _showFinalImage(url) {
    if (_finalShown) return;
    _finalShown = true;
    procTileGrid.classList.add("hidden");
    procGridPlaceholder.classList.add("hidden");
    finalImg.src = `${url}?t=${Date.now()}`;
    finalImg.classList.remove("hidden");
    procTileCount.textContent = "Final";
}

async function _updateTileGrids(job) {
    // Try to init grid from scan dimensions when known
    const sRows = job.scan && job.scan.rows > 0 ? job.scan.rows : 0;
    const sCols = job.scan && job.scan.cols > 0 ? job.scan.cols : 0;

    if (!job.artifacts || !job.artifacts.tile_set_url) return;

    let data;
    try {
        const resp = await fetch(job.artifacts.tile_set_url);
        if (!resp.ok) return;
        data = await resp.json();
    } catch (_) {
        return;
    }

    // Infer grid size from tile stems if scan dims not yet set
    let rows = sRows, cols = sCols;
    if ((!rows || !cols) && data.tiles.length > 0) {
        let maxR = 0, maxC = 0;
        for (const t of data.tiles) {
            const m = t.stem && t.stem.match(/row_(\d+)_col_(\d+)/);
            if (m) { maxR = Math.max(maxR, +m[1]); maxC = Math.max(maxC, +m[2]); }
        }
        if (maxR >= 0 && maxC >= 0 && data.tiles.length > 0) {
            rows = maxR + 1;
            cols = maxC + 1;
        }
    }

    if (rows > 0 && cols > 0) _buildTileGrid(rows, cols);

    for (const tile of data.tiles) {
        if (tile.preview_url) _setRawTile(tile.stem, tile.preview_url);
        if (tile.processed_url) _setProcTile(tile.stem, tile.processed_url);
    }

    // Once processing is done, replace processed grid with the integrated final image
    if (job.artifacts.final_preview_url && job.state === "completed") {
        _showFinalImage(job.artifacts.final_preview_url);
    }
}

// ---------------------------------------------------------------------------
// Job API
// ---------------------------------------------------------------------------

async function createJob() {
    const response = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_kind: "one_click_scan" }),
    });
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "Failed to create job");
    }
    return (await response.json()).job_id;
}

async function fetchJob(jobId) {
    const response = await fetch(`/api/jobs/${jobId}`);
    if (!response.ok) throw new Error("Job not found");
    return response.json();
}

function rememberActiveJob(jobId) {
    activeJobId = jobId;
    localStorage.setItem(ACTIVE_JOB_KEY, jobId);
}

function rememberLastJob(jobId) {
    if (jobId) localStorage.setItem(LAST_JOB_KEY, jobId);
}

function clearActiveJob() {
    activeJobId = null;
    localStorage.removeItem(ACTIVE_JOB_KEY);
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

function startPolling(jobId) {
    clearTimeout(jobPoller);
    rememberActiveJob(jobId);
    jobPoller = setTimeout(pollJob, 0);
}

async function pollJob() {
    if (!activeJobId) return;
    try {
        const job = await fetchJob(activeJobId);
        applyJobToUI(job);
        if (job.state === "running") {
            jobPoller = setTimeout(pollJob, 800);
        } else {
            onJobTerminal(job);
        }
    } catch (_) {
        jobPoller = setTimeout(pollJob, 2000);
    }
}

// ---------------------------------------------------------------------------
// UI updates
// ---------------------------------------------------------------------------

function applyJobToUI(job) {
    const pct = job.progress_pct || 0;
    const stage = job.stage || "dispatch";

    progressSection.classList.remove("hidden");
    resultsSection.classList.remove("hidden");
    unifiedBar.style.width = `${pct}%`;
    progressPct.textContent = `${pct}%`;
    stageLabel.textContent = stageLookup[stage] || stage;
    updateStageSteps(stage);

    if (stage === "scan" && job.scan && job.scan.rows > 0) {
        jobStatusText.textContent = `Scan in progress — row ${job.scan.current_row + 1} of ${job.scan.rows}.`;
    } else if (stage === "dispatch") {
        jobStatusText.textContent = "Starting one-click scan.";
    } else if (stage === "stitch") {
        jobStatusText.textContent = "Stitching raw artifact on Pi.";
    } else if (stage === "transfer") {
        jobStatusText.textContent = "Transferring stitched raw to PC.";
    } else if (stage === "classify") {
        jobStatusText.textContent = "Classifying film.";
    } else if (job.processing && job.processing.current_iteration != null) {
        const max = job.processing.max_iterations ?? "?";
        jobStatusText.textContent = `Post-processing — pass ${job.processing.current_iteration}/${max}.`;
    } else if (job.error) {
        jobStatusText.textContent = `Error: ${job.error}`;
    } else {
        jobStatusText.textContent = `${stageLookup[stage] || stage}.`;
    }

    // Tile grid update (fire-and-forget; does not block polling)
    _updateTileGrids(job);
}

function onJobTerminal(job) {
    clearTimeout(jobPoller);
    clearActiveJob();
    rememberLastJob(job.job_id);

    if (job.state === "completed") {
        stageLabel.textContent = stageLookup.complete;
        progressPct.textContent = "100%";
        unifiedBar.style.width = "100%";
        unifiedBar.classList.add("bar-complete");
        updateStageSteps("complete");
        jobStatusText.textContent = "Scan complete.";
    } else if (job.state === "failed") {
        stageLabel.textContent = "Failed";
        unifiedBar.classList.add("bar-error");
        jobStatusText.textContent = `Error: ${job.error || "Unknown error"}`;
    } else if (job.state === "cancelled") {
        stageLabel.textContent = "Cancelled";
        unifiedBar.classList.add("bar-cancelled");
        jobStatusText.textContent = "Job cancelled.";
    }

    // One final tile grid refresh to pick up any last processed tiles / final image
    _updateTileGrids(job);

    scanBtn.disabled = false;
    cancelBtn.disabled = true;
}

function resetJobUI() {
    // Reset tile state
    _gridBuilt = false;
    _gridRows = 0;
    _gridCols = 0;
    _rawLoaded = new Set();
    _procLoaded = new Set();
    _finalShown = false;

    rawTileGrid.innerHTML = "";
    rawTileGrid.classList.add("hidden");
    rawGridPlaceholder.classList.remove("hidden");
    rawGridPlaceholder.textContent = "Waiting for scan to start.";
    rawTileCount.textContent = "";

    procTileGrid.innerHTML = "";
    procTileGrid.classList.add("hidden");
    procGridPlaceholder.classList.remove("hidden");
    procGridPlaceholder.textContent = "Waiting for processing.";
    procTileCount.textContent = "";

    finalImg.classList.add("hidden");
    finalImg.src = "";

    unifiedBar.style.width = "0%";
    unifiedBar.className = "progress-bar unified-bar";
    progressPct.textContent = "0%";
    stageLabel.textContent = stageLookup.dispatch;
    updateStageSteps("dispatch");

    progressSection.classList.remove("hidden");
    resultsSection.classList.remove("hidden");
}

// ---------------------------------------------------------------------------
// Button handlers
// ---------------------------------------------------------------------------

scanBtn.addEventListener("click", async () => {
    scanBtn.disabled = true;
    cancelBtn.disabled = false;
    resetJobUI();
    jobStatusText.textContent = "Creating one-click job.";
    try {
        const jobId = await createJob();
        rememberLastJob(jobId);
        startPolling(jobId);
    } catch (error) {
        jobStatusText.textContent = `Error: ${error.message}`;
        scanBtn.disabled = false;
        cancelBtn.disabled = true;
    }
});

cancelBtn.addEventListener("click", async () => {
    if (!activeJobId) return;
    cancelBtn.disabled = true;
    try {
        await fetch(`/api/jobs/${activeJobId}/cancel`, { method: "POST" });
        jobStatusText.textContent = "Cancelling job.";
    } catch (_) {}
});

// ---------------------------------------------------------------------------
// Restore saved job on load
// ---------------------------------------------------------------------------

async function restoreSavedJob() {
    const activeId = localStorage.getItem(ACTIVE_JOB_KEY);
    if (activeId) {
        try {
            const job = await fetchJob(activeId);
            resetJobUI();
            applyJobToUI(job);
            if (job.state === "running") {
                scanBtn.disabled = true;
                cancelBtn.disabled = false;
                startPolling(activeId);
                return;
            }
            onJobTerminal(job);
            return;
        } catch (_) {
            localStorage.removeItem(ACTIVE_JOB_KEY);
        }
    }

    const lastId = localStorage.getItem(LAST_JOB_KEY);
    if (!lastId) return;
    try {
        const job = await fetchJob(lastId);
        resetJobUI();
        applyJobToUI(job);
        if (job.state === "running") {
            scanBtn.disabled = true;
            cancelBtn.disabled = false;
            startPolling(lastId);
            return;
        }
        onJobTerminal(job);
    } catch (_) {
        localStorage.removeItem(LAST_JOB_KEY);
    }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

buildStageSteps();
checkSystemStatus();
setInterval(checkSystemStatus, 10000);
restoreSavedJob();
