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

const stageLookup = Object.fromEntries(STAGES.map((stage) => [stage.key, stage.label]));

const scanBtn = document.getElementById("scanBtn");
const cancelBtn = document.getElementById("cancelBtn");
const jobStatusText = document.getElementById("jobStatusText");
const progressSection = document.getElementById("progressSection");
const stageLabel = document.getElementById("stageLabel");
const progressPct = document.getElementById("progressPct");
const unifiedBar = document.getElementById("unifiedBar");
const resultsSection = document.getElementById("resultsSection");
const rawImg = document.getElementById("rawImg");
const rawPlaceholder = document.getElementById("rawPlaceholder");
const finalImg = document.getElementById("finalImg");
const finalPlaceholder = document.getElementById("finalPlaceholder");

const devProcessBtn = document.getElementById("devProcessBtn");
const devSelectedInfo = document.getElementById("devSelectedInfo");
const devCaptureBtn = document.getElementById("devCaptureBtn");
const devCaptureStatus = document.getElementById("devCaptureStatus");
const captureGallery = document.getElementById("captureGallery");
const captureCount = document.getElementById("captureCount");
const backlightBtn = document.getElementById("backlightBtn");
const backlightStatus = document.getElementById("backlightStatus");
const serialPortSelect = document.getElementById("serialPortSelect");
const piCameraDevStatus = document.getElementById("piCameraDevStatus");

let activeJobId = null;
let jobPoller = null;
let devSelected = null;
let rawLoaded = false;
let finalLoaded = false;

function buildStageSteps() {
    const container = document.getElementById("stageSteps");
    container.innerHTML = STAGES.map((stage, index) => {
        const connector = index < STAGES.length - 1 ? '<div class="stage-connector"></div>' : "";
        return `
            <div class="stage-step" data-stage="${stage.key}">
                <div class="stage-dot"></div>
                <span>${stage.label}</span>
            </div>
            ${connector}
        `;
    }).join("");
}

function updateStageSteps(currentStage) {
    const currentIndex = STAGES.findIndex((stage) => stage.key === currentStage);
    document.querySelectorAll(".stage-step").forEach((element, index) => {
        element.classList.toggle("done", currentIndex >= 0 && index < currentIndex);
        element.classList.toggle("active", element.dataset.stage === currentStage);
        element.classList.toggle("pending", currentIndex < 0 || (index > currentIndex && element.dataset.stage !== currentStage));
    });
}

function setIndicator(dotId, labelId, isOnline, baseLabel) {
    const dot = document.getElementById(dotId);
    const label = document.getElementById(labelId);
    dot.className = `sys-dot ${isOnline ? "online" : "offline"}`;
    label.textContent = isOnline ? baseLabel : `${baseLabel} (offline)`;
}

async function checkSystemStatus() {
    try {
        const response = await fetch("/api/system/status");
        const data = await response.json();
        setIndicator("piScannerDot", "piScannerLabel", !!data.pi_scanner_reachable, "Pi Scanner");
        piCameraDevStatus.textContent = data.pi_camera_reachable ? "Online" : "Offline";
    } catch (_) {
        setIndicator("piScannerDot", "piScannerLabel", false, "Pi Scanner");
        piCameraDevStatus.textContent = "Offline";
    }
}

async function createJob(sourceFilename = null) {
    const path = sourceFilename ? "/api/dev/process_capture" : "/api/jobs";
    const body = sourceFilename
        ? { source_filename: sourceFilename }
        : { job_kind: "one_click_scan" };

    const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });

    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "Failed to create job");
    }

    return (await response.json()).job_id;
}

async function fetchJob(jobId) {
    const response = await fetch(`/api/jobs/${jobId}`);
    if (!response.ok) {
        throw new Error("Job not found");
    }
    return response.json();
}

function rememberActiveJob(jobId) {
    activeJobId = jobId;
    localStorage.setItem(ACTIVE_JOB_KEY, jobId);
}

function rememberLastJob(jobId) {
    if (jobId) {
        localStorage.setItem(LAST_JOB_KEY, jobId);
    }
}

function clearActiveJob() {
    activeJobId = null;
    localStorage.removeItem(ACTIVE_JOB_KEY);
}

function startPolling(jobId) {
    clearTimeout(jobPoller);
    rememberActiveJob(jobId);
    jobPoller = setTimeout(pollJob, 0);
}

async function pollJob() {
    if (!activeJobId) {
        return;
    }

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

function updateArtifactImage(imageElement, placeholderElement, url) {
    placeholderElement.classList.add("hidden");
    imageElement.classList.remove("hidden");
    imageElement.src = `${url}?t=${Date.now()}`;
}

function applyJobToUI(job) {
    const pct = job.progress_pct || 0;
    const stage = job.stage || "dispatch";
    const stageText = stageLookup[stage] || stage;

    progressSection.classList.remove("hidden");
    resultsSection.classList.remove("hidden");
    unifiedBar.style.width = `${pct}%`;
    progressPct.textContent = `${pct}%`;
    stageLabel.textContent = stageText;
    updateStageSteps(stage);

    if (stage === "scan" && job.scan && job.scan.rows > 0) {
        jobStatusText.textContent = `Scan in progress. Row ${job.scan.current_row + 1} of ${job.scan.rows}.`;
    } else if (stage === "dispatch") {
        jobStatusText.textContent = "Starting one-click scan.";
    } else if (stage === "stitch") {
        jobStatusText.textContent = "Stitching raw artifact on Pi.";
    } else if (stage === "transfer") {
        jobStatusText.textContent = "Transferring stitched raw to PC.";
    } else if (stage === "classify") {
        jobStatusText.textContent = "Classifying film.";
    } else if (job.processing && job.processing.current_iteration != null) {
        const maxIterations = job.processing.max_iterations ?? "?";
        jobStatusText.textContent = `Post-processing image. Pass ${job.processing.current_iteration}/${maxIterations}.`;
    } else if (job.error) {
        jobStatusText.textContent = `Error: ${job.error}`;
    } else {
        jobStatusText.textContent = `${stageText}.`;
    }

    if (!rawLoaded && job.artifacts && job.artifacts.raw_preview_url) {
        rawLoaded = true;
        updateArtifactImage(rawImg, rawPlaceholder, job.artifacts.raw_preview_url);
    }

    if (!finalLoaded && job.artifacts && job.artifacts.final_preview_url) {
        finalLoaded = true;
        updateArtifactImage(finalImg, finalPlaceholder, job.artifacts.final_preview_url);
    }
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

    scanBtn.disabled = false;
    cancelBtn.disabled = true;
    devProcessBtn.disabled = devSelected == null;
}

function resetJobUI() {
    rawLoaded = false;
    finalLoaded = false;
    unifiedBar.style.width = "0%";
    unifiedBar.className = "progress-bar unified-bar";
    progressPct.textContent = "0%";
    stageLabel.textContent = stageLookup.dispatch;
    updateStageSteps("dispatch");

    rawImg.classList.add("hidden");
    rawImg.src = "";
    rawPlaceholder.classList.remove("hidden");
    rawPlaceholder.textContent = "Waiting for raw preview.";

    finalImg.classList.add("hidden");
    finalImg.src = "";
    finalPlaceholder.classList.remove("hidden");
    finalPlaceholder.textContent = "Waiting for final preview.";
    progressSection.classList.remove("hidden");
    resultsSection.classList.remove("hidden");
}

function selectDevCapture(filename) {
    devSelected = filename;
    devSelectedInfo.textContent = filename;
    devSelectedInfo.classList.remove("dev-selected-none");
    devProcessBtn.disabled = activeJobId != null;
    document.querySelectorAll(".gallery-thumb").forEach((element) => {
        element.classList.toggle("selected", element.dataset.filename === filename);
    });
}

async function refreshGallery() {
    try {
        const response = await fetch("/api/dev/captures");
        const data = await response.json();
        const files = data.captures || [];
        captureCount.textContent = files.length ? `${files.length}` : "";
        captureGallery.innerHTML = "";

        files.slice().reverse().forEach((filename) => {
            const card = document.createElement("button");
            card.type = "button";
            card.className = `gallery-thumb${filename === devSelected ? " selected" : ""}`;
            card.dataset.filename = filename;
            card.innerHTML = `
                <img src="/api/dev/captures/${encodeURIComponent(filename)}" loading="lazy" alt="${filename}" />
                <span>${filename}</span>
            `;
            card.addEventListener("click", () => selectDevCapture(filename));
            captureGallery.appendChild(card);
        });
    } catch (_) {
        captureCount.textContent = "";
    }
}

async function loadPorts() {
    try {
        const response = await fetch("/api/dev/serial/ports");
        const ports = await response.json();
        serialPortSelect.innerHTML = '<option value="">Select Port</option>';
        ports.forEach((portInfo) => {
            const option = document.createElement("option");
            option.value = portInfo.port;
            option.textContent = `${portInfo.port} - ${portInfo.description}`;
            serialPortSelect.appendChild(option);
        });
    } catch (_) {
        serialPortSelect.innerHTML = '<option value="">Select Port</option>';
    }
}

scanBtn.addEventListener("click", async () => {
    scanBtn.disabled = true;
    cancelBtn.disabled = false;
    devProcessBtn.disabled = true;
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
        devProcessBtn.disabled = devSelected == null;
    }
});

cancelBtn.addEventListener("click", async () => {
    if (!activeJobId) {
        return;
    }
    cancelBtn.disabled = true;
    try {
        await fetch(`/api/jobs/${activeJobId}/cancel`, { method: "POST" });
        jobStatusText.textContent = "Cancelling job.";
    } catch (_) {}
});

devProcessBtn.addEventListener("click", async () => {
    if (!devSelected) {
        return;
    }
    devProcessBtn.disabled = true;
    scanBtn.disabled = true;
    cancelBtn.disabled = false;
    resetJobUI();
    rawPlaceholder.textContent = "Loading selected capture.";
    jobStatusText.textContent = `Creating developer job for ${devSelected}.`;
    try {
        const jobId = await createJob(devSelected);
        rememberLastJob(jobId);
        startPolling(jobId);
    } catch (error) {
        jobStatusText.textContent = `Error: ${error.message}`;
        scanBtn.disabled = false;
        cancelBtn.disabled = true;
        devProcessBtn.disabled = false;
    }
});

devCaptureBtn.addEventListener("click", async () => {
    devCaptureBtn.disabled = true;
    devCaptureStatus.textContent = "Capturing from Pi camera.";
    try {
        const response = await fetch("/api/dev/camera/capture-import", { method: "POST" });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "Capture import failed");
        }
        devCaptureStatus.textContent = `Imported ${data.filename}.`;
        await refreshGallery();
        if (data.filename) {
            selectDevCapture(data.filename);
        }
    } catch (error) {
        devCaptureStatus.textContent = `Error: ${error.message}`;
    } finally {
        devCaptureBtn.disabled = false;
    }
});

backlightBtn.addEventListener("click", async () => {
    backlightBtn.disabled = true;
    backlightStatus.textContent = "Capturing backlight frame.";
    try {
        const response = await fetch("/api/dev/calibration/backlight", { method: "POST" });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "Backlight capture failed");
        }
        backlightStatus.textContent = "Backlight frame captured.";
    } catch (error) {
        backlightStatus.textContent = `Error: ${error.message}`;
    } finally {
        backlightBtn.disabled = false;
    }
});

serialPortSelect.addEventListener("change", async () => {
    const port = serialPortSelect.value;
    try {
        await fetch(port ? "/api/dev/serial/connect" : "/api/dev/serial/disconnect", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: port ? JSON.stringify({ port }) : undefined,
        });
    } catch (_) {}
});

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
    if (!lastId) {
        return;
    }
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

buildStageSteps();
checkSystemStatus();
setInterval(checkSystemStatus, 10000);
loadPorts();
refreshGallery();
setInterval(refreshGallery, 5000);
restoreSavedJob();
