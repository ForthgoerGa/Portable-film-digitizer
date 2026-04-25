// Constants

const STAGES = [
    { key: "dispatch",  label: "Dispatch"  },
    { key: "scan",      label: "Scan"      },
    { key: "stitch",    label: "Stitch"    },
    { key: "transfer",  label: "Transfer"  },
    { key: "classify",  label: "Classify"  },
    { key: "process",   label: "Process"   },
    { key: "complete",  label: "Done"      },
];

const STAGE_LABELS = Object.fromEntries(STAGES.map(s => [s.key, s.label]));

// State

let fullResW = 0, fullResH = 0;
let roiDraft = null;   // {x0,y0,x1,y1} in full-res pixels, being drawn
let roiSaved = null;   // last confirmed ROI from server
let isDragging = false;
let dragOrigin = null; // canvas pixel coordinates

let selectedJobId = null;
let jobPoller = null;
let allJobs = [];

// DOM refs

const piScannerDot    = document.getElementById("piScannerDot");
const piScannerLabel  = document.getElementById("piScannerLabel");
const activeJobsLabel = document.getElementById("activeJobsLabel");

const backlightChip         = document.getElementById("backlightChip");
const baseFrameChip         = document.getElementById("baseFrameChip");
const negBranchChip         = document.getElementById("negBranchChip");
const backlightUpload       = document.getElementById("backlightUpload");
const baseFrameUpload       = document.getElementById("baseFrameUpload");
const backlightCapturePiBtn = document.getElementById("backlightCapturePiBtn");
const baseFrameCapturePiBtn = document.getElementById("baseFrameCapturePiBtn");
const backlightUploadStatus = document.getElementById("backlightUploadStatus");
const baseFrameUploadStatus = document.getElementById("baseFrameUploadStatus");
const backlightClearBtn     = document.getElementById("backlightClearBtn");
const baseFrameClearBtn     = document.getElementById("baseFrameClearBtn");

const loadPreviewBtn  = document.getElementById("loadPreviewBtn");
const roiMsg          = document.getElementById("roiMsg");
const cropPlaceholder = document.getElementById("cropPlaceholder");
const cropWrap        = document.getElementById("cropWrap");
const cropImg         = document.getElementById("cropImg");
const cropCanvas      = document.getElementById("cropCanvas");
const roiCoords       = document.getElementById("roiCoords");
const saveRoiBtn      = document.getElementById("saveRoiBtn");
const clearRoiBtn     = document.getElementById("clearRoiBtn");

const jobsListContainer = document.getElementById("jobsListContainer");
const inspectPanel      = document.getElementById("inspectPanel");
const inspJobId         = document.getElementById("inspJobId");
const inspJobState      = document.getElementById("inspJobState");
const inspTimestamp     = document.getElementById("inspTimestamp");
const inspStageLabel    = document.getElementById("inspStageLabel");
const inspProgressPct   = document.getElementById("inspProgressPct");
const inspProgressBar   = document.getElementById("inspProgressBar");
const inspStageSteps    = document.getElementById("inspStageSteps");
const inspMeta          = document.getElementById("inspMeta");
const inspError         = document.getElementById("inspError");
const inspArtifacts     = document.getElementById("inspArtifacts");

// System status

async function checkSystemStatus() {
    try {
        const data = await (await fetch("/api/system/status")).json();
        const online = !!data.pi_scanner_reachable;
        piScannerDot.className = `sys-dot ${online ? "online" : "offline"}`;
        piScannerLabel.textContent = online ? "Pi Scanner" : "Pi Scanner (offline)";
        const n = data.active_jobs || 0;
        activeJobsLabel.textContent = n === 0 ? "No active jobs" : `${n} active job${n > 1 ? "s" : ""}`;
    } catch (_) {
        piScannerDot.className = "sys-dot offline";
        piScannerLabel.textContent = "Pi Scanner (offline)";
    }
}

// Calibration

function applyChip(el, configured, ready, label) {
    el.textContent = label;
    el.className = "status-chip" + (ready ? " ready" : configured ? " pending" : "");
}

async function refreshCalibration() {
    try {
        const data = await (await fetch("/api/dev/calibration/summary")).json();
        const bl = data.backlight;
        const bf = data.base_frame;
        const blReady = !!(bl && bl.dng_available);
        const bfReady = !!(bf && bf.dng_available);
        const blSize = blReady && bl.dng_size ? ` (${Math.round(bl.dng_size / 1024)} KB)` : "";
        const bfSize = bfReady && bf.dng_size ? ` (${Math.round(bf.dng_size / 1024)} KB)` : "";
        applyChip(backlightChip, !!(bl && bl.configured), blReady, blReady ? `Ready${blSize}` : "Not configured");
        applyChip(baseFrameChip, !!(bf && bf.configured), bfReady, bfReady ? `Ready${bfSize}` : "Not configured");
        applyChip(
            negBranchChip,
            data.negative_branch_ready,
            data.negative_branch_ready,
            data.negative_branch_ready ? "Ready to run" : "Waiting for both DNGs",
        );
        loadPreviewBtn.disabled = !bfReady;
        if (!bfReady) {
            cropPlaceholder.style.display = "";
            cropWrap.style.display = "none";
        }
    } catch (_) {
        [backlightChip, baseFrameChip, negBranchChip].forEach(el => {
            el.textContent = "Unavailable";
            el.className = "status-chip error";
        });
    }
}

async function uploadCalibDng(kind, file, statusEl) {
    statusEl.textContent = `Uploading ${file.name}...`;
    try {
        const fd = new FormData();
        fd.append("file", file, file.name);
        const resp = await fetch(`/api/dev/calibration/${kind}/upload`, { method: "POST", body: fd });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "Upload failed");
        statusEl.textContent = `Stored ${file.name}.`;
        await refreshCalibration();
        if (kind === "base_frame") await loadCurrentRoi();
    } catch (err) {
        statusEl.textContent = `Error: ${err.message}`;
    }
}

async function captureCalibrationFromPi(kind, statusEl) {
    const label = kind === "backlight" ? "backlight" : "base frame";
    statusEl.textContent = kind === "backlight"
        ? "Running full-area backlight scan on Pi..."
        : `Capturing ${label} on Pi...`;
    backlightCapturePiBtn.disabled = true;
    baseFrameCapturePiBtn.disabled = true;
    try {
        const resp = await fetch(`/api/dev/calibration/${kind}/capture-from-pi`, { method: "POST" });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "Capture failed");
        const pos = data.pi_response && data.pi_response.position;
        const mode = data.capture_mode === "full_area_stitched_raw" ? " full-area stitched raw" : "";
        const suffix = pos ? ` at X=${pos.x}, Y=${pos.y}` : "";
        statusEl.textContent = `Captured ${label}${mode}${suffix}.`;
        await refreshCalibration();
        if (kind === "base_frame") await loadCurrentRoi();
    } catch (err) {
        statusEl.textContent = `Error: ${err.message}`;
    } finally {
        backlightCapturePiBtn.disabled = false;
        baseFrameCapturePiBtn.disabled = false;
    }
}

async function clearCalibration(kind, statusEl) {
    statusEl.textContent = "Clearing...";
    try {
        const resp = await fetch(`/api/dev/calibration/${kind}`, { method: "DELETE" });
        if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || "Clear failed");
        statusEl.textContent = "Cleared.";
        await refreshCalibration();
    } catch (err) {
        statusEl.textContent = `Error: ${err.message}`;
    }
}

backlightUpload.addEventListener("change", () => {
    if (backlightUpload.files[0]) uploadCalibDng("backlight", backlightUpload.files[0], backlightUploadStatus);
});
baseFrameUpload.addEventListener("change", () => {
    if (baseFrameUpload.files[0]) uploadCalibDng("base_frame", baseFrameUpload.files[0], baseFrameUploadStatus);
});
backlightCapturePiBtn.addEventListener("click", () =>
    captureCalibrationFromPi("backlight", backlightUploadStatus),
);
baseFrameCapturePiBtn.addEventListener("click", () =>
    captureCalibrationFromPi("base_frame", baseFrameUploadStatus),
);
backlightClearBtn.addEventListener("click", () => clearCalibration("backlight", backlightUploadStatus));
baseFrameClearBtn.addEventListener("click", () => clearCalibration("base_frame", baseFrameUploadStatus));
document.getElementById("refreshCalibBtn").addEventListener("click", refreshCalibration);

// ROI Crop

async function loadCurrentRoi() {
    try {
        const data = await (await fetch("/api/dev/calibration/roi")).json();
        roiSaved = data.roi ? { x0: data.roi[0], y0: data.roi[1], x1: data.roi[2], y1: data.roi[3] } : null;
        redrawCanvas();
        updateRoiCoordsText();
    } catch (_) {}
}

loadPreviewBtn.addEventListener("click", async () => {
    loadPreviewBtn.disabled = true;
    roiMsg.textContent = "Loading preview...";
    try {
        const resp = await fetch(`/api/dev/calibration/base_frame/preview?t=${Date.now()}`);
        if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || "Load failed");
        fullResW = parseInt(resp.headers.get("X-Full-Width") || "0", 10);
        fullResH = parseInt(resp.headers.get("X-Full-Height") || "0", 10);
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        cropImg.onload = () => {
            cropCanvas.width  = cropImg.naturalWidth;
            cropCanvas.height = cropImg.naturalHeight;
            if (!fullResW) fullResW = cropImg.naturalWidth;
            if (!fullResH) fullResH = cropImg.naturalHeight;
            redrawCanvas();
        };
        cropImg.src = url;
        cropPlaceholder.style.display = "none";
        cropWrap.style.display = "";
        roiMsg.textContent = fullResW ? `Full-res: ${fullResW} x ${fullResH} px` : "";
        await loadCurrentRoi();
    } catch (err) {
        roiMsg.textContent = `Error: ${err.message}`;
    } finally {
        loadPreviewBtn.disabled = false;
    }
});

function canvasEventCoords(e) {
    const rect = cropCanvas.getBoundingClientRect();
    return {
        cx: Math.round((e.clientX - rect.left) * (cropCanvas.width  / rect.width)),
        cy: Math.round((e.clientY - rect.top)  * (cropCanvas.height / rect.height)),
    };
}

function canvasToFull(cx, cy) {
    return {
        x: Math.round(cx * fullResW / cropCanvas.width),
        y: Math.round(cy * fullResH / cropCanvas.height),
    };
}

cropCanvas.addEventListener("mousedown", e => {
    const { cx, cy } = canvasEventCoords(e);
    isDragging = true;
    dragOrigin = { cx, cy };
    roiDraft = null;
    e.preventDefault();
});

cropCanvas.addEventListener("mousemove", e => {
    if (!isDragging) return;
    const { cx, cy } = canvasEventCoords(e);
    const ax = Math.min(dragOrigin.cx, cx), bx = Math.max(dragOrigin.cx, cx);
    const ay = Math.min(dragOrigin.cy, cy), by = Math.max(dragOrigin.cy, cy);
    const p0 = canvasToFull(ax, ay), p1 = canvasToFull(bx, by);
    roiDraft = { x0: p0.x, y0: p0.y, x1: p1.x, y1: p1.y };
    redrawCanvas();
    updateRoiCoordsText();
});

cropCanvas.addEventListener("mouseup", () => {
    isDragging = false;
    saveRoiBtn.disabled = !roiDraft || roiDraft.x1 <= roiDraft.x0 || roiDraft.y1 <= roiDraft.y0;
});

cropCanvas.addEventListener("mouseleave", () => { isDragging = false; });

function drawRoiRect(ctx, roi, fillColor, strokeColor) {
    if (!roi || !cropCanvas.width) return;
    const sx = cropCanvas.width  / fullResW;
    const sy = cropCanvas.height / fullResH;
    const x = roi.x0 * sx, y = roi.y0 * sy;
    const w = (roi.x1 - roi.x0) * sx, h = (roi.y1 - roi.y0) * sy;
    ctx.fillStyle = fillColor;
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, w, h);
}

function redrawCanvas() {
    if (!cropCanvas.width || !cropCanvas.height) return;
    const ctx = cropCanvas.getContext("2d");
    ctx.clearRect(0, 0, cropCanvas.width, cropCanvas.height);
    if (roiSaved) drawRoiRect(ctx, roiSaved, "rgba(26,163,95,0.25)", "#1aa35f");
    if (roiDraft)  drawRoiRect(ctx, roiDraft,  "rgba(37,99,235,0.20)", "#2563eb");
}

function updateRoiCoordsText() {
    const roi = roiDraft || roiSaved;
    if (!roi) {
        roiCoords.textContent = "";
        return;
    }
    const tag = roiDraft ? "draft" : "saved";
    roiCoords.textContent =
        `[${tag}]  x0=${roi.x0}  y0=${roi.y0}  x1=${roi.x1}  y1=${roi.y1}  ` +
        `(${roi.x1 - roi.x0} x ${roi.y1 - roi.y0} px full-res)`;
}

saveRoiBtn.addEventListener("click", async () => {
    if (!roiDraft) return;
    saveRoiBtn.disabled = true;
    roiMsg.textContent = "Saving...";
    try {
        const resp = await fetch("/api/dev/calibration/roi", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(roiDraft),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "Save failed");
        roiSaved = roiDraft;
        roiDraft = null;
        saveRoiBtn.disabled = true;
        redrawCanvas();
        updateRoiCoordsText();
        roiMsg.textContent = `Saved - ${roiSaved.x1 - roiSaved.x0} x ${roiSaved.y1 - roiSaved.y0} px`;
    } catch (err) {
        roiMsg.textContent = `Error: ${err.message}`;
        saveRoiBtn.disabled = false;
    }
});

clearRoiBtn.addEventListener("click", async () => {
    try {
        await fetch("/api/dev/calibration/roi", { method: "DELETE" });
    } catch (_) {}
    roiSaved = null;
    roiDraft = null;
    saveRoiBtn.disabled = true;
    redrawCanvas();
    updateRoiCoordsText();
    roiMsg.textContent = "ROI cleared.";
});

// Real-time inspection

function buildStageSteps() {
    inspStageSteps.innerHTML = STAGES.map((s, i) => {
        const connector = i < STAGES.length - 1 ? '<div class="stage-connector"></div>' : "";
        return `<div class="stage-step" data-stage="${s.key}">
            <div class="stage-dot"></div>
            <span>${s.label}</span>
        </div>${connector}`;
    }).join("");
}

function applyStageSteps(currentStage) {
    const idx = STAGES.findIndex(s => s.key === currentStage);
    inspStageSteps.querySelectorAll(".stage-step").forEach((el, i) => {
        el.classList.toggle("done",   idx >= 0 && i < idx);
        el.classList.toggle("active", el.dataset.stage === currentStage);
    });
}

function stateChipClass(state) {
    return { running: " ready", completed: " ready", failed: " error", cancelled: " pending" }[state] || "";
}

function fmtTime(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString();
}

function applyJobToInspector(job) {
    inspJobId.textContent    = job.job_id || "-";
    inspTimestamp.textContent = job.started_at ? `started ${fmtTime(job.started_at)}` : "";

    const state = job.state || "-";
    inspJobState.textContent  = state;
    inspJobState.className    = "status-chip" + stateChipClass(state);

    const pct   = job.progress_pct || 0;
    const stage = job.stage || "dispatch";
    inspStageLabel.textContent = STAGE_LABELS[stage] || stage;
    inspProgressPct.textContent = `${pct}%`;
    inspProgressBar.style.width = `${pct}%`;
    inspProgressBar.className   = "progress-bar unified-bar" +
        (state === "completed" ? " bar-complete" : state === "failed" ? " bar-error" : state === "cancelled" ? " bar-cancelled" : "");
    applyStageSteps(stage);

    // Processing metadata
    const p = job.processing || {};
    const metaFields = [
        ["Classification",  p.classification],
        ["Classifier",      p.classifier_mode],
        ["Branch",          p.selected_branch],
        ["Runner",          p.runner_used],
        ["Score",           p.score != null ? Number(p.score).toFixed(3) : null],
        ["Iteration",       p.current_iteration != null ? `${p.current_iteration}/${p.max_iterations ?? "?"}` : null],
    ].filter(([, v]) => v != null);

    if (metaFields.length) {
        inspMeta.style.display = "";
        inspMeta.innerHTML = metaFields.map(([label, value]) =>
            `<div class="meta-chip">
                <span class="meta-label">${label}</span>
                <span class="meta-value">${value}</span>
            </div>`
        ).join("");
    } else {
        inspMeta.style.display = "none";
    }

    // Error
    if (job.error) {
        inspError.style.display = "";
        inspError.textContent = job.error;
    } else {
        inspError.style.display = "none";
    }

    // Artifacts
    const arts = job.artifacts || {};
    const pairs = [
        ["Raw Preview",   arts.raw_preview_url],
        ["Final Preview", arts.final_preview_url],
    ];
    inspArtifacts.innerHTML = pairs.map(([title, url]) => {
        const body = url
            ? `<img class="artifact-img" src="${url}?t=${Date.now()}" alt="${title}" />`
            : `<div class="artifact-empty">Not available yet.</div>`;
        return `<div class="artifact-card">
            <div class="artifact-title">${title}</div>
            ${body}
        </div>`;
    }).join("");

    inspectPanel.style.display = "";
}

async function pollSelectedJob() {
    if (!selectedJobId) return;
    try {
        const resp = await fetch(`/api/jobs/${selectedJobId}`);
        if (!resp.ok) return;
        const job = await resp.json();
        applyJobToInspector(job);
        if (job.state === "running") {
            jobPoller = setTimeout(pollSelectedJob, 1000);
        }
    } catch (_) {
        jobPoller = setTimeout(pollSelectedJob, 2000);
    }
}

function selectJob(jobId) {
    clearTimeout(jobPoller);
    selectedJobId = jobId;
    document.querySelectorAll(".job-chip").forEach(el => {
        el.classList.toggle("selected", el.dataset.jobId === jobId);
    });
    const job = allJobs.find(j => j.job_id === jobId);
    if (job) applyJobToInspector(job);
    if (job && job.state === "running") {
        jobPoller = setTimeout(pollSelectedJob, 800);
    }
}

async function refreshJobs() {
    try {
        const data = await (await fetch("/api/jobs")).json();
        allJobs = (data.jobs || []).slice().sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
    } catch (_) {
        return;
    }

    if (!allJobs.length) {
        jobsListContainer.innerHTML = '<span class="no-jobs-hint">No jobs found.</span>';
        return;
    }

    jobsListContainer.innerHTML = "";
    allJobs.forEach(job => {
        const btn = document.createElement("button");
        btn.className = `job-chip state-${job.state || "unknown"}${job.job_id === selectedJobId ? " selected" : ""}`;
        btn.dataset.jobId = job.job_id;
        btn.innerHTML = `${job.job_id.slice(0, 8)} <span class="job-chip-state">${job.state}</span>`;
        btn.addEventListener("click", () => selectJob(job.job_id));
        jobsListContainer.appendChild(btn);
    });

    // Auto-select: prefer a running job, else keep current selection, else pick the newest
    const running = allJobs.find(j => j.state === "running");
    if (running && selectedJobId !== running.job_id) {
        selectJob(running.job_id);
    } else if (!selectedJobId && allJobs.length) {
        selectJob(allJobs[0].job_id);
    } else if (selectedJobId) {
        // Re-apply current selection chip highlight
        document.querySelectorAll(".job-chip").forEach(el => {
            el.classList.toggle("selected", el.dataset.jobId === selectedJobId);
        });
    }
}

document.getElementById("refreshJobsBtn").addEventListener("click", refreshJobs);

// Init

buildStageSteps();
checkSystemStatus();
setInterval(checkSystemStatus, 10000);
refreshCalibration();
setInterval(refreshCalibration, 15000);
refreshJobs();
setInterval(refreshJobs, 3000);
