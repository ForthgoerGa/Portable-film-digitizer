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

let selectedJobId = null;
let jobPoller = null;
let allJobs = [];
let _backlightScanPoller = null;

// Cache: last metadata URL rendered so we don't re-fetch on every poll tick
let _lastRenderedMetaUrl = null;

// DOM refs

const piScannerDot    = document.getElementById("piScannerDot");
const piScannerLabel  = document.getElementById("piScannerLabel");
const activeJobsLabel = document.getElementById("activeJobsLabel");

const backlightChip         = document.getElementById("backlightChip");
const backlightUpload       = document.getElementById("backlightUpload");
const backlightCapturePiBtn = document.getElementById("backlightCapturePiBtn");
const backlightUploadStatus = document.getElementById("backlightUploadStatus");
const backlightClearBtn     = document.getElementById("backlightClearBtn");

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
const inspClassDetect   = document.getElementById("inspClassDetect");
const inspClassResult   = document.getElementById("inspClassResult");
const inspGlobalBase    = document.getElementById("inspGlobalBase");
const inspBboxTiles     = document.getElementById("inspBboxTiles");
const inspArtifacts     = document.getElementById("inspArtifacts");

// ---------------------------------------------------------------------------
// System status
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Calibration
// ---------------------------------------------------------------------------

function applyChip(el, configured, ready, label) {
    el.textContent = label;
    el.className = "status-chip" + (ready ? " ready" : configured ? " pending" : "");
}

async function refreshCalibration() {
    try {
        const data = await (await fetch("/api/dev/calibration/summary")).json();
        const bl = data.backlight;

        const blTs = bl && bl.tile_set;
        const blTileComplete = !!(blTs && blTs.complete);
        const blTilePartial = !!(blTs && blTs.available && !blTs.complete);
        const blDngReady = !!(bl && bl.dng_available);
        const blReady = blTileComplete || blDngReady;
        let blLabel;
        if (blTileComplete) {
            blLabel = `Ready - ${blTs.tile_count} tiles`;
        } else if (blTilePartial) {
            blLabel = `Partial - ${blTs.tile_count}/${blTs.expected_count ?? "?"} tiles`;
        } else if (blDngReady) {
            const sz = bl.dng_size ? ` (${Math.round(bl.dng_size / 1024)} KB)` : "";
            blLabel = `Ready (single DNG${sz})`;
        } else {
            blLabel = "Not configured";
        }
        applyChip(backlightChip, !!(bl && bl.configured), blReady, blLabel);
    } catch (_) {
        backlightChip.textContent = "Unavailable";
        backlightChip.className = "status-chip error";
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
    } catch (err) {
        statusEl.textContent = `Error: ${err.message}`;
    }
}

async function clearCalibration(kind, statusEl) {
    statusEl.textContent = "Clearing...";
    try {
        const resp = await fetch(`/api/dev/calibration/${kind}`, { method: "DELETE" });
        if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || "Clear failed");
        statusEl.textContent = "Cleared.";
        await refreshCalibration();
        await refreshFlatFieldMapStatus();
    } catch (err) {
        statusEl.textContent = `Error: ${err.message}`;
    }
}

async function _startBacklightScan(statusEl) {
    statusEl.textContent = "Starting backlight tile scan on Pi...";
    backlightCapturePiBtn.disabled = true;
    backlightClearBtn.disabled = true;
    try {
        const resp = await fetch("/api/dev/calibration/backlight/capture-from-pi", { method: "POST" });
        const data = await resp.json();
        if (resp.status === 409) {
            statusEl.textContent = "Scan already in progress - monitoring...";
        } else if (!resp.ok) {
            throw new Error(data.detail || "Scan start failed");
        } else {
            statusEl.textContent = "Scan started - waiting for tiles...";
        }
        _startBacklightScanPoller(statusEl);
    } catch (err) {
        statusEl.textContent = `Error: ${err.message}`;
        backlightCapturePiBtn.disabled = false;
        backlightClearBtn.disabled = false;
    }
}

function _startBacklightScanPoller(statusEl) {
    if (_backlightScanPoller) clearInterval(_backlightScanPoller);
    _backlightScanPoller = setInterval(async () => {
        try {
            const state = await (await fetch("/api/dev/calibration/backlight/scan-status")).json();
            if (state.running) {
                const count = state.tile_count ?? 0;
                const total = state.expected_count ? `/${state.expected_count}` : "";
                statusEl.textContent = `Scanning: ${count}${total} tiles received...`;
            } else {
                clearInterval(_backlightScanPoller);
                _backlightScanPoller = null;
                backlightCapturePiBtn.disabled = false;
                backlightClearBtn.disabled = false;
                if (state.error) {
                    statusEl.textContent = `Scan error: ${state.error}`;
                } else {
                    const count = state.tile_count ?? 0;
                    const completeTag = state.complete ? " (complete)" : "";
                    statusEl.textContent = `Scan done - ${count} tile${count !== 1 ? "s" : ""} stored${completeTag}.`;
                }
                await refreshCalibration();
                await refreshFlatFieldMapStatus();
            }
        } catch (_) {}
    }, 1500);
}

async function _checkBacklightScanOnLoad() {
    try {
        const state = await (await fetch("/api/dev/calibration/backlight/scan-status")).json();
        if (state.running) {
            backlightCapturePiBtn.disabled = true;
            backlightClearBtn.disabled = true;
            backlightUploadStatus.textContent = "Scan in progress (resumed)...";
            _startBacklightScanPoller(backlightUploadStatus);
        }
    } catch (_) {}
}

backlightUpload.addEventListener("change", () => {
    if (backlightUpload.files[0]) uploadCalibDng("backlight", backlightUpload.files[0], backlightUploadStatus);
});
backlightCapturePiBtn.addEventListener("click", () => _startBacklightScan(backlightUploadStatus));
backlightClearBtn.addEventListener("click", () => clearCalibration("backlight", backlightUploadStatus));
document.getElementById("refreshCalibBtn").addEventListener("click", refreshCalibration);

// ---------------------------------------------------------------------------
// Classification & Detection
// ---------------------------------------------------------------------------

const CLF_TYPE_CSS = {
    negative_film: "negative",
    positive_film: "positive",
    instax_mini:   "instax",
    instax_instant_film: "instax",
};

function _clfLabel(classification, rawLabel) {
    const map = {
        negative_film: "Negative Film",
        positive_film: "Positive Film",
        instax_mini:   "Instax Mini",
        instax_instant_film: "Instax / Instant Film",
    };
    return map[classification] || (rawLabel ? rawLabel.replace(/_/g, " ") : classification || "Unknown");
}

function _fmtPct(value) {
    return value == null || Number.isNaN(Number(value))
        ? "n/a"
        : `${(Number(value) * 100).toFixed(0)}%`;
}

function _fmtRgb(rgb) {
    return Array.isArray(rgb)
        ? rgb.map(v => Number(v).toFixed(4)).join(", ")
        : "n/a";
}

function _linearRgbToCss(rgb) {
    if (!Array.isArray(rgb) || rgb.length < 3) return "#000";
    const ch = rgb.slice(0, 3).map(v => {
        const clamped = Math.max(0, Math.min(1, Number(v) || 0));
        return Math.round(Math.pow(clamped, 1 / 2.2) * 255);
    });
    return `rgb(${ch[0]}, ${ch[1]}, ${ch[2]})`;
}

function _bboxAreaFraction(bbox, width = 4056, height = 3040) {
    if (!Array.isArray(bbox) || bbox.length !== 4) return null;
    const [x0, y0, x1, y1] = bbox.map(Number);
    return Math.max(0, x1 - x0) * Math.max(0, y1 - y0) / (width * height);
}

async function _loadGlobalParams(job, meta) {
    if (meta && meta.global_negative_params) return meta.global_negative_params;
    const candidates = [];
    if (meta && meta.global_negative_params_url) candidates.push(meta.global_negative_params_url);
    if (job && job.job_id) candidates.push(`/job-files/${job.job_id}/global_negative_params.json`);
    for (const url of candidates) {
        try {
            const resp = await fetch(url, { cache: "no-store" });
            if (resp.ok) return await resp.json();
        } catch (_) {}
    }
    return null;
}

function _drawBbox(canvas, bbox, srcW, srcH, strokeColor, fillColor, alpha) {
    const ctx = canvas.getContext("2d");
    const sx = canvas.width / srcW;
    const sy = canvas.height / srcH;
    const [x0, y0, x1, y1] = bbox;
    const cx = x0 * sx, cy = y0 * sy, cw = (x1 - x0) * sx, ch = (y1 - y0) * sy;
    ctx.globalAlpha = alpha;
    ctx.fillStyle = fillColor;
    ctx.fillRect(cx, cy, cw, ch);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = Math.max(1.5, canvas.width / 200);
    ctx.strokeRect(cx, cy, cw, ch);

    // Confidence label positioned inside top-left of box
    return { cx, cy, cw, ch };
}

function _drawConfLabel(canvas, cx, cy, text, bgColor) {
    const ctx = canvas.getContext("2d");
    const pad = 3, fontSize = Math.max(9, Math.round(canvas.width / 28));
    ctx.font = `bold ${fontSize}px Consolas, monospace`;
    const tw = ctx.measureText(text).width;
    ctx.fillStyle = bgColor;
    ctx.globalAlpha = 0.88;
    ctx.fillRect(cx, cy, tw + pad * 2, fontSize + pad * 2);
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#fff";
    ctx.fillText(text, cx + pad, cy + fontSize + pad - 1);
}

function _renderGlobalBase(meta, globalParams) {
    const bootstrap = meta.bootstrap || {};
    const baseRef = (globalParams && globalParams.base_reference) || bootstrap.global_base_reference;
    const baseRgb = baseRef && baseRef.base_rgb;
    const source = (baseRef && baseRef.source_frame) || bootstrap.mode || "n/a";
    const count = (baseRef && baseRef.stats && baseRef.stats.global_average_count) ||
        bootstrap.accepted_base_bbox_count || 0;
    const sampleBoxes = baseRef && baseRef.stats && baseRef.stats.per_tile_sample_bbox;
    inspGlobalBase.innerHTML = `
        <div class="global-base-card">
            <div class="base-swatch" style="background:${_linearRgbToCss(baseRgb)}"></div>
            <div class="global-base-lines">
                <span>global base rgb: ${_fmtRgb(baseRgb)}</span>
                <span>base samples: ${count}; source: ${source}</span>
                <span>sample bboxes: ${Array.isArray(sampleBoxes) ? sampleBoxes.map(b => `[${b.join(", ")}]`).join(" | ") : "pending"}</span>
            </div>
        </div>
    `;
}

async function renderClassificationDetection(job) {
    const metaUrl = job.artifacts && job.artifacts.metadata_url;
    if (!metaUrl) {
        inspClassDetect.style.display = "none";
        return;
    }

    // Metadata is rewritten in-place as bootstrap advances, so include job
    // progress/update fields instead of caching only by URL.
    const renderKey = `${metaUrl}:${job.updated_at || ""}:${job.stage || ""}:${job.progress_pct || ""}`;
    if (renderKey === _lastRenderedMetaUrl) return;

    let meta;
    try {
        meta = await (await fetch(metaUrl)).json();
    } catch (_) {
        return;
    }

    const bootstrap = meta.bootstrap;
    if (!bootstrap || !bootstrap.classification) {
        inspClassDetect.style.display = "none";
        return;
    }

    _lastRenderedMetaUrl = renderKey;
    const globalParams = await _loadGlobalParams(job, meta);

    // Classification row
    const cls = bootstrap.classification;
    const raw = bootstrap.classifier_raw_label || "";
    const mode = bootstrap.classifier_mode || "";
    const cssCls = CLF_TYPE_CSS[cls] || "unknown";
    const acceptedCount = bootstrap.accepted_base_bbox_count ?? "?";
    const totalBootstrap = (bootstrap.bbox_results || []).length;
    const clfConfidence = bootstrap.classification_confidence;
    const voteCount = bootstrap.classification_vote_count;
    const voteTotal = bootstrap.classification_vote_total;

    inspClassResult.innerHTML = `
        <span class="clf-type-chip ${cssCls}">${_clfLabel(cls, raw)}</span>
        <span class="clf-raw-label">${raw}</span>
        <span class="clf-mode-text">${mode}</span>
        <span class="clf-mode-text" style="color:#526170">classification confidence: ${_fmtPct(clfConfidence)}${voteTotal ? ` (${voteCount}/${voteTotal} vote)` : ""}</span>
        <span class="clf-mode-text" style="color:#526170">${acceptedCount}/${totalBootstrap} bootstrap tiles accepted for global base</span>
    `;
    _renderGlobalBase(meta, globalParams);

    // Per-tile bbox visualization
    inspBboxTiles.innerHTML = "";
    const bboxResults = bootstrap.bbox_results || [];

    for (const result of bboxResults) {
        const stem = `row_${result.row}_col_${result.col}`;
        const thumbUrl = `/api/jobs/${job.job_id}/tiles/${stem}/thumb`;
        const bd = result.bbox_detection || {};
        const filmBbox = bd.film_content_bbox;      // in preview_source_pixels (full-res)
        const baseBbox = bd.base_candidate_bbox;
        const conf = bd.confidence;
        const confHigh = conf != null && conf >= 0.5;
        const baseAccept = bd.base_acceptance || {};
        const baseAccepted = !!baseAccept.accepted;
        const baseReason = baseAccept.reason;
        const baseFrac = bd.base_area_fraction;

        const card = document.createElement("div");
        card.className = "bbox-tile-card";

        const imgWrap = document.createElement("div");
        imgWrap.className = "bbox-img-wrap";

        const img = document.createElement("img");
        img.className = "bbox-tile-img";
        img.alt = stem;

        const cvs = document.createElement("canvas");
        cvs.className = "bbox-canvas";

        imgWrap.appendChild(img);
        imgWrap.appendChild(cvs);

        const metaDiv = document.createElement("div");
        metaDiv.className = "bbox-meta";
        metaDiv.innerHTML = `
            <span class="bbox-stem">${stem}</span>
            ${conf != null
                ? `<span class="bbox-conf${confHigh ? "" : " low"}">conf: ${conf.toFixed(3)}</span>`
                : '<span class="bbox-conf low">no confidence</span>'}
            ${filmBbox
                ? `<span class="bbox-coords">film [${filmBbox.join(", ")}]</span>`
                : '<span class="bbox-coords">no film bbox detected</span>'}
            ${baseBbox
                ? `<span class="bbox-coords">base [${baseBbox.join(", ")}] ${baseAccepted ? "accepted" : `rejected:${baseReason || "unknown"}`} ${baseFrac != null ? `area=${_fmtPct(baseFrac)}` : ""}</span>`
                : '<span class="bbox-coords">no base bbox detected</span>'}
        `;

        card.appendChild(imgWrap);
        card.appendChild(metaDiv);
        inspBboxTiles.appendChild(card);

        // Draw bboxes once thumbnail loads
        img.onload = () => {
            // Canvas covers the displayed image at its natural thumbnail resolution
            cvs.width  = img.naturalWidth;
            cvs.height = img.naturalHeight;

            // Source coords are in full-res preview_source_pixels (4056x3040 default)
            const srcW = Number(bd.preview_width) || 4056;
            const srcH = Number(bd.preview_height) || 3040;

            // Draw the first-three bootstrap base candidate. This is averaged
            // into one global base reference; later tiles do not use per-tile base.
            if (baseBbox) {
                const { cx, cy } = _drawBbox(
                    cvs,
                    baseBbox,
                    srcW,
                    srcH,
                    baseAccepted ? "#1aa35f" : "#b45309",
                    baseAccepted ? "#34d399" : "#fbbf24",
                    0.1
                );
                _drawConfLabel(cvs, cx, Math.max(0, cy + 16), baseAccepted ? "base" : "base reject", baseAccepted ? "#1aa35f" : "#b45309");
            }

            // Draw film content bbox (blue)
            if (filmBbox) {
                const { cx, cy } = _drawBbox(cvs, filmBbox, srcW, srcH, "#2563eb", "#93c5fd", 0.18);
                if (conf != null) {
                    _drawConfLabel(cvs, cx, cy, `${(conf * 100).toFixed(0)}%`, confHigh ? "#2563eb" : "#d14343");
                }
            }
        };
        img.src = thumbUrl;
    }

    inspClassDetect.style.display = "";
}

// ---------------------------------------------------------------------------
// Real-time inspection
// ---------------------------------------------------------------------------

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
    return new Date(ts * 1000).toLocaleTimeString();
}

function applyJobToInspector(job) {
    inspJobId.textContent     = job.job_id || "-";
    inspTimestamp.textContent = job.started_at ? `started ${fmtTime(job.started_at)}` : "";

    const state = job.state || "-";
    inspJobState.textContent = state;
    inspJobState.className   = "status-chip" + stateChipClass(state);

    const pct   = job.progress_pct || 0;
    const stage = job.stage || "dispatch";
    inspStageLabel.textContent  = STAGE_LABELS[stage] || stage;
    inspProgressPct.textContent = `${pct}%`;
    inspProgressBar.style.width = `${pct}%`;
    inspProgressBar.className   = "progress-bar unified-bar" +
        (state === "completed" ? " bar-complete" : state === "failed" ? " bar-error" : state === "cancelled" ? " bar-cancelled" : "");
    applyStageSteps(stage);

    // Processing metadata chips
    const p = job.processing || {};
    const metaFields = [
        ["Branch",     p.selected_branch],
        ["Runner",     p.runner_used],
        ["Score",      p.score != null ? Number(p.score).toFixed(3) : null],
        ["Iteration",  p.current_iteration != null ? `${p.current_iteration}/${p.max_iterations ?? "?"}` : null],
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

    // Classification & detection (async; uses metadata artifact)
    renderClassificationDetection(job);

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
    _lastRenderedMetaUrl = null;  // force re-render for new job
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

    const running = allJobs.find(j => j.state === "running");
    if (running && selectedJobId !== running.job_id) {
        selectJob(running.job_id);
    } else if (!selectedJobId && allJobs.length) {
        selectJob(allJobs[0].job_id);
    } else if (selectedJobId) {
        document.querySelectorAll(".job-chip").forEach(el => {
            el.classList.toggle("selected", el.dataset.jobId === selectedJobId);
        });
    }
}

document.getElementById("refreshJobsBtn").addEventListener("click", refreshJobs);

// ---------------------------------------------------------------------------
// Flat-field parameters
// ---------------------------------------------------------------------------

async function loadFlatFieldConfig() {
    try {
        const data = await (await fetch("/api/dev/flat_field_config")).json();
        document.getElementById("ffStrength").value  = (data.strength   ?? 1.0).toFixed(2);
        document.getElementById("ffSigmaFrac").value = (data.sigma_frac ?? 0.0005).toFixed(4);
        document.getElementById("ffMaxSide").value   = data.max_side ?? 4096;
    } catch (_) {}
}

function renderFlatFieldMapStatus(data) {
    const chip = document.getElementById("ffMapChip");
    const status = document.getElementById("ffMapStatus");
    if (!chip || !status) return;
    const build = data.build_state || {};
    if (build.running) {
        chip.textContent = "Building";
        chip.className = "status-chip pending";
        status.textContent = `Recomputing maps (${build.reason || "update"})...`;
        return;
    }
    if (build.error) {
        chip.textContent = "Error";
        chip.className = "status-chip error";
        status.textContent = build.error;
        return;
    }
    const count = data.map_count ?? 0;
    const expected = data.expected_count ?? data.source_tile_count ?? 0;
    if (data.complete) {
        chip.textContent = `Ready ${count}/${expected}`;
        chip.className = "status-chip ready";
    } else if (data.stale) {
        chip.textContent = `Stale ${count}/${expected || "?"}`;
        chip.className = "status-chip pending";
    } else if (count > 0) {
        chip.textContent = `Partial ${count}/${expected || "?"}`;
        chip.className = "status-chip pending";
    } else {
        chip.textContent = "Not built";
        chip.className = "status-chip";
    }
    const cfg = data.config || {};
    const updated = data.rebuilt_at ? new Date(data.rebuilt_at * 1000).toLocaleTimeString() : "never";
    status.textContent = `Maps: ${count}/${expected || "?"}; strength ${cfg.strength}, sigma ${cfg.sigma_frac}, max side ${cfg.max_side}; rebuilt ${updated}.`;
}

async function refreshFlatFieldMapStatus() {
    try {
        const resp = await fetch("/api/dev/flat_field_maps/status");
        renderFlatFieldMapStatus(await resp.json());
    } catch (_) {}
}

async function saveFlatFieldConfig() {
    const statusEl = document.getElementById("ffSaveStatus");
    const btn = document.getElementById("ffApplyBtn");
    btn.disabled = true;
    statusEl.textContent = "Saving...";
    try {
        const body = {
            strength:   parseFloat(document.getElementById("ffStrength").value),
            sigma_frac: parseFloat(document.getElementById("ffSigmaFrac").value),
            max_side:   parseInt(document.getElementById("ffMaxSide").value, 10),
        };
        const resp = await fetch("/api/dev/flat_field_config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "Save failed");
        const cfg = data.config || data;
        document.getElementById("ffStrength").value  = cfg.strength.toFixed(2);
        document.getElementById("ffSigmaFrac").value = cfg.sigma_frac.toFixed(4);
        document.getElementById("ffMaxSide").value   = cfg.max_side;
        statusEl.textContent = data.flat_maps?.rebuild_started ? "Saved; rebuilding maps." : "Saved.";
        if (data.flat_maps) renderFlatFieldMapStatus(data.flat_maps);
    } catch (err) {
        statusEl.textContent = `Error: ${err.message}`;
    } finally {
        btn.disabled = false;
    }
}

document.getElementById("ffApplyBtn").addEventListener("click", saveFlatFieldConfig);
document.getElementById("ffRebuildMapsBtn").addEventListener("click", async () => {
    const btn = document.getElementById("ffRebuildMapsBtn");
    btn.disabled = true;
    try {
        const resp = await fetch("/api/dev/flat_field_maps/rebuild", { method: "POST" });
        renderFlatFieldMapStatus(await resp.json());
    } finally {
        btn.disabled = false;
    }
});

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

buildStageSteps();
checkSystemStatus();
setInterval(checkSystemStatus, 10000);
refreshCalibration();
setInterval(refreshCalibration, 15000);
refreshJobs();
setInterval(refreshJobs, 3000);
_checkBacklightScanOnLoad();
loadFlatFieldConfig();
refreshFlatFieldMapStatus();
setInterval(refreshFlatFieldMapStatus, 5000);
