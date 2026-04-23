import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests as _requests
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from coordinator import coordinator
from serial_comm import (
    connect_serial,
    disconnect_serial,
    get_serial_status,
    list_serial_ports,
)

app = FastAPI()

# ── Static directories ────────────────────────────────────────────────────────

web_dir = Path(__file__).parent / "web"
if web_dir.exists():
    app.mount("/web", StaticFiles(directory=str(web_dir), html=True), name="webclient")

scan_input_dir = coordinator.stitcher.tiles_dir
if scan_input_dir.exists():
    app.mount("/scan-input", StaticFiles(directory=str(scan_input_dir)), name="scan_input")

scan_output_dir = coordinator.stitcher.output_dir
scan_output_dir.mkdir(parents=True, exist_ok=True)
app.mount("/scan-output", StaticFiles(directory=str(scan_output_dir)), name="scan_output")

_pi_captures_dir = Path(__file__).parent / "web" / "pi_captures"
_pi_captures_dir.mkdir(parents=True, exist_ok=True)

_pi_processed_dir = Path(__file__).parent / "web" / "pi_processed"
_pi_processed_dir.mkdir(parents=True, exist_ok=True)
app.mount("/pi-processed", StaticFiles(directory=str(_pi_processed_dir)), name="pi_processed")

# ── Agentic pipeline config ───────────────────────────────────────────────────

_AGENTIC_DIR = Path(__file__).resolve().parent.parent / "Agentic_Post_Processing"
_RUNNER = _AGENTIC_DIR / "_run_single.py"

_process_jobs: dict = {}   # job_id -> status dict
_jobs_lock = threading.Lock()


def _run_pipeline(job_id: str, source: Path, output_dir: Path):
    proc = subprocess.Popen(
        [sys.executable, str(_RUNNER), str(source), str(output_dir)],
        cwd=str(_AGENTIC_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    stderr_lines: list[str] = []

    def _read_stderr():
        for raw in proc.stderr:
            line = raw.strip()
            if not line:
                continue
            stderr_lines.append(line)
            if line.startswith("PROGRESS:"):
                try:
                    progress = json.loads(line[9:])
                    with _jobs_lock:
                        job = _process_jobs.setdefault(job_id, {})
                        job["status"] = "running"
                        job["current_iteration"] = progress["iteration"]
                        job["max_iter"] = progress["max_iter"]
                        job["current_score"] = progress["score"]
                        job["current_feedback"] = progress["feedback"]
                        job["film_type"] = progress.get("film_type")
                        job["classifier_mode"] = progress.get("classifier_mode")
                        job["evaluator_mode"] = progress.get("evaluator_mode")
                        job["current_params"] = progress.get("params", {})
                        job.setdefault("iterations_log", []).append(progress)
                except Exception:
                    pass

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    stdout_data, _ = proc.communicate()
    stderr_thread.join(timeout=5)

    if proc.returncode != 0:
        err = "\n".join(stderr_lines[-10:]) if stderr_lines else "Pipeline subprocess failed"
        with _jobs_lock:
            _process_jobs[job_id] = {**_process_jobs.get(job_id, {}), "status": "error", "error": err}
        return

    try:
        meta = json.loads(stdout_data)
    except Exception as exc:
        with _jobs_lock:
            _process_jobs[job_id] = {**_process_jobs.get(job_id, {}), "status": "error", "error": f"Bad output: {exc}"}
        return

    with _jobs_lock:
        _process_jobs[job_id] = {**_process_jobs.get(job_id, {}), "status": "done", **meta}


# ── Pi camera URL ─────────────────────────────────────────────────────────────

PI_CAMERA_URL = "http://10.12.194.1:8080"

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return RedirectResponse(url="/web/")


@app.post("/scan")
def start_scan(req: dict):
    film_format = req.get("format", "35mm")
    try:
        coordinator.start_job(film_format)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "started", "format": film_format}


@app.get("/status")
def get_status():
    return coordinator.status_dict()


@app.post("/reset")
def reset_job():
    coordinator.reset_job()
    return {"status": "reset"}


@app.post("/cancel")
def cancel_job():
    coordinator.cancel_job()
    return {"status": "cancel_requested"}


@app.get("/serial/ports")
def serial_ports():
    return list_serial_ports()


@app.post("/serial/connect")
def serial_connect(body: dict):
    port = body.get("port")
    if not port:
        raise HTTPException(status_code=400, detail="Field 'port' required")
    return connect_serial(port)


@app.post("/serial/disconnect")
def serial_disconnect():
    return disconnect_serial()


@app.get("/serial/status")
def serial_status():
    return get_serial_status()


# ── Pi camera receive ─────────────────────────────────────────────────────────

@app.post("/receive_capture")
async def receive_capture(file: UploadFile = File(...)):
    dest = _pi_captures_dir / file.filename
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {
        "saved": file.filename,
        "path": str(dest.relative_to(Path(__file__).parent)),
        "size": dest.stat().st_size,
    }


@app.get("/pi_captures")
def list_pi_captures():
    files = sorted(p.name for p in _pi_captures_dir.iterdir() if p.suffix == ".jpg")
    return {"captures": files, "count": len(files)}


# ── Pi camera proxy ───────────────────────────────────────────────────────────

@app.get("/camera/stream")
def camera_stream():
    """Proxy the Pi MJPEG stream so the browser stays same-origin."""
    try:
        r = _requests.get(f"{PI_CAMERA_URL}/stream", stream=True, timeout=10)
        return StreamingResponse(
            r.iter_content(chunk_size=4096),
            media_type=r.headers.get("content-type", "multipart/x-mixed-replace; boundary=frame"),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Pi camera unreachable: {exc}")


@app.post("/camera/capture")
def camera_capture():
    """Trigger a still capture on the Pi."""
    try:
        r = _requests.post(f"{PI_CAMERA_URL}/capture", timeout=30)
        r.raise_for_status()
        return r.json()
    except _requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Pi camera error: {exc}")


@app.get("/camera/captures")
def camera_captures():
    """List captures stored on the Pi."""
    try:
        r = _requests.get(f"{PI_CAMERA_URL}/captures", timeout=5)
        return r.json()
    except _requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Pi camera error: {exc}")


@app.get("/camera/captures/{filename}")
def camera_get_capture(filename: str):
    """Proxy a capture image file from the Pi."""
    try:
        r = _requests.get(f"{PI_CAMERA_URL}/captures/{filename}", timeout=15)
        r.raise_for_status()
        return Response(content=r.content, media_type=r.headers.get("content-type", "image/jpeg"))
    except _requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Pi camera error: {exc}")


@app.post("/camera/send-to-pc/{filename}")
def camera_send_to_pc(filename: str):
    """Tell Pi to send a capture to this server (receive_capture)."""
    try:
        r = _requests.post(f"{PI_CAMERA_URL}/send/{filename}", timeout=30)
        return r.json()
    except _requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Pi camera error: {exc}")


@app.get("/camera/status")
def camera_status():
    try:
        r = _requests.get(f"{PI_CAMERA_URL}/status", timeout=3)
        return r.json()
    except Exception:
        return {"camera_ready": False, "error": "Pi unreachable"}


# ── Agentic pipeline ──────────────────────────────────────────────────────────

@app.post("/process_capture")
def start_process_capture(body: dict):
    """
    Start the agentic post-processing pipeline on a Pi capture.
    Body: {"filename": "capture_xxx.jpg"}
    Returns: {"job_id": "...", "status": "running"}
    Poll /process_status/{job_id} for completion.
    """
    filename = body.get("filename")
    if not filename:
        raise HTTPException(status_code=400, detail="'filename' required")

    source = _pi_captures_dir / filename
    if not source.exists():
        raise HTTPException(status_code=404, detail="Capture not found on PC. Use Send first.")

    job_id = str(int(time.time() * 1000))
    with _jobs_lock:
        _process_jobs[job_id] = {"status": "running", "filename": filename}

    threading.Thread(
        target=_run_pipeline, args=(job_id, source, _pi_processed_dir), daemon=True
    ).start()
    return {"job_id": job_id, "status": "running"}


@app.get("/process_status/{job_id}")
def get_process_status(job_id: str):
    with _jobs_lock:
        return _process_jobs.get(job_id, {"status": "not_found"})
