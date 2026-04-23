// Motor control elements
const jogXForwardBtn = document.getElementById("jogXForward");
const jogXReverseBtn = document.getElementById("jogXReverse");
const jogYForwardBtn = document.getElementById("jogYForward");
const jogYReverseBtn = document.getElementById("jogYReverse");
const xStepsInput = document.getElementById("xSteps");
const yStepsInput = document.getElementById("ySteps");
const moveXBtn = document.getElementById("moveX");
const moveYBtn = document.getElementById("moveY");
const moveBothBtn = document.getElementById("moveBoth");
const stopAllBtn = document.getElementById("stopAll");
const motorStatus = document.getElementById("motorStatus");

// Motor control functions
function jogXForward() {
    setStatus("Jogging X forward...", "active");
    fetch("/serial/jog/x/forward", { method: "POST" })
        .then(() => setStatus("X axis jogging forward", "active"))
        .catch(() => setStatus("Error: Could not reach server", "error"));
}

function jogXReverse() {
    setStatus("Jogging X reverse...", "active");
    fetch("/serial/jog/x/reverse", { method: "POST" })
        .then(() => setStatus("X axis jogging reverse", "active"))
        .catch(() => setStatus("Error: Could not reach server", "error"));
}

function jogYForward() {
    setStatus("Jogging Y forward...", "active");
    fetch("/serial/jog/y/forward", { method: "POST" })
        .then(() => setStatus("Y axis jogging forward", "active"))
        .catch(() => setStatus("Error: Could not reach server", "error"));
}

function jogYReverse() {
    setStatus("Jogging Y reverse...", "active");
    fetch("/serial/jog/y/reverse", { method: "POST" })
        .then(() => setStatus("Y axis jogging reverse", "active"))
        .catch(() => setStatus("Error: Could not reach server", "error"));
}

function moveX() {
    const steps = parseInt(xStepsInput.value) || 0;
    setStatus(`Moving X by ${steps} steps...`, "active");
    fetch("/serial/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ x: steps, y: 0 })
    })
        .then(() => setStatus(`Moved X axis by ${steps} steps`, "ready"))
        .catch(() => setStatus("Error: Could not reach server", "error"));
}

function moveY() {
    const steps = parseInt(yStepsInput.value) || 0;
    setStatus(`Moving Y by ${steps} steps...`, "active");
    fetch("/serial/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ x: 0, y: steps })
    })
        .then(() => setStatus(`Moved Y axis by ${steps} steps`, "ready"))
        .catch(() => setStatus("Error: Could not reach server", "error"));
}

function moveBoth() {
    const xSteps = parseInt(xStepsInput.value) || 0;
    const ySteps = parseInt(yStepsInput.value) || 0;
    setStatus(`Moving X by ${xSteps}, Y by ${ySteps} steps...`, "active");
    fetch("/serial/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ x: xSteps, y: ySteps })
    })
        .then(() => setStatus(`Moved X by ${xSteps}, Y by ${ySteps} steps`, "ready"))
        .catch(() => setStatus("Error: Could not reach server", "error"));
}

function stopAll() {
    setStatus("Stopping all motors...", "active");
    fetch("/serial/stop", { method: "POST" })
        .then(() => setStatus("All motors stopped", "ready"))
        .catch(() => setStatus("Error: Could not reach server", "error"));
}

function setStatus(message, state = "ready") {
    motorStatus.textContent = message;
    motorStatus.className = `status-display status-${state}`;

    // Auto-reset to ready after 3 seconds for success messages
    if (state === "ready") {
        setTimeout(() => {
            motorStatus.textContent = "Ready";
            motorStatus.className = "status-display";
        }, 3000);
    }

    // Auto-reset error messages after 5 seconds
    if (state === "error") {
        setTimeout(() => {
            motorStatus.textContent = "Ready";
            motorStatus.className = "status-display";
        }, 5000);
    }
}

// Event handlers
jogXForwardBtn.onclick = jogXForward;
jogXReverseBtn.onclick = jogXReverse;
jogYForwardBtn.onclick = jogYForward;
jogYReverseBtn.onclick = jogYReverse;
moveXBtn.onclick = moveX;
moveYBtn.onclick = moveY;
moveBothBtn.onclick = moveBoth;
stopAllBtn.onclick = stopAll;

// Initialize
document.addEventListener("DOMContentLoaded", () => {
    setStatus("Ready");
});