const cameraStream = document.getElementById("cameraStream");
const restartStreamBtn = document.getElementById("restartStreamBtn");
const singlePreviewBtn = document.getElementById("singlePreviewBtn");
const captureBacklightBtn = document.getElementById("captureBacklightBtn");
const captureBaseFrameBtn = document.getElementById("captureBaseFrameBtn");
const captureStatus = document.getElementById("captureStatus");
const lastCaptureLinks = document.getElementById("lastCaptureLinks");
const streamStatus = document.getElementById("streamStatus");

function restartStream() {
    cameraStream.src = `../../camera/stream.mjpg?t=${Date.now()}`;
    streamStatus.textContent = "Streaming from /camera/stream.mjpg";
}

async function captureCalibration(kind) {
    const label = kind === "backlight" ? "backlight" : "base frame";
    captureBacklightBtn.disabled = true;
    captureBaseFrameBtn.disabled = true;
    captureStatus.textContent = `Capturing ${label}...`;
    lastCaptureLinks.textContent = "";
    try {
        const response = await fetch("../../dev/calibration/capture", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ kind }),
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "Capture failed");
        }
        captureStatus.textContent = `Captured ${label} at X=${data.position.x}, Y=${data.position.y}.`;
        const rawUrl = `../../captures/file?path=${encodeURIComponent(data.raw_path)}`;
        const previewUrl = `../../captures/file?path=${encodeURIComponent(data.preview_path)}`;
        lastCaptureLinks.innerHTML = `
            RAW: <a href="${rawUrl}" target="_blank">${data.raw_path}</a><br>
            Preview: <a href="${previewUrl}" target="_blank">${data.preview_path}</a>
        `;
    } catch (error) {
        captureStatus.textContent = `Error: ${error.message}`;
    } finally {
        captureBacklightBtn.disabled = false;
        captureBaseFrameBtn.disabled = false;
    }
}

restartStreamBtn.addEventListener("click", restartStream);
singlePreviewBtn.addEventListener("click", () => {
    window.open(`../../camera/preview.jpg?t=${Date.now()}`, "_blank");
});
captureBacklightBtn.addEventListener("click", () => captureCalibration("backlight"));
captureBaseFrameBtn.addEventListener("click", () => captureCalibration("base_frame"));
