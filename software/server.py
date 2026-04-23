from __future__ import annotations

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
from pydantic import BaseModel, ConfigDict

from coordinator import coordinator
from job_orchestrator import JobOrchestrator
from serial_comm import (
    connect_serial,
    disconnect_serial,
    get_serial_status,
    list_serial_ports,
)

app = FastAPI()

# Static directories ------------------------------------------------------------

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
app.mount("/pi-captures", StaticFiles(directory=str(_pi_captures_dir)), name="pi_captures_files")

_pi_processed_dir = Path(__file__).parent / "web" / "pi_processed"
_pi_processed_dir.mkdir(parents=True, exist_ok=True)
app.mount("/pi-processed", StaticFiles(directory=str(_pi_processed_dir)), name="pi_processed")

_jobs_dir = Path(__file__).parent / "web" / "jobs"
_jobs_dir.mkdir(parents=True, exist_ok=True)

# Legacy agentic pipeline config ------------------------------------------------

_AGENTIC_DIR = Path(__file__).resolve().parent.parent / "Agentic_Post_Processing"
_RUNNER = _AGENTIC_DIR / "_run_single.py"

_process_jobs: dict = {}
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
                except Exception:
                    continue
                with _jobs_lock:
                    job = _process_jobs.setdefault(job_id, {})
                    job["status"] = "running"
                    job["current_iteration"] = progress.get("iteration")
                    job["max_iter"] = progress.get("max_iter")
                    job["current_score"] = progress.get("score")
                    job["current_feedback"] = progress.get("feedback")
                    job["film_type"] = progress.get("film_type")
                    job["classifier_mode"] = progress.get("classifier_mode")
                    job["evaluator_mode"] = progress.get("evaluator_mode")
                    job["current_params"] = progress.get("params", {})
                    job.setdefault("iterations_log", []).append(progress)

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()
    stdout_data, _ = proc.communicate()
    stderr_thread.join(timeout=5)

    if proc.returncode != 0:
        err = "\n".join(stderr_lines[-10:]) if stderr_lines else "Pipeline subprocess failed"
        with _jobs_lock:
            _process_jobs[job_id] = {
                **_process_jobs.get(job_id, {}),
                "status": "error",
                "error": err,
            }
        return

    try:
        meta = json.loads(stdout_data)
    except Exception as exc:
        with _jobs_lock:
            _process_jobs[job_id] = {
                **_process_jobs.get(job_id, {}),
                "status": "error",
                "error": f"Bad output: {exc}",
            }
        return

    with _jobs_lock:
        _process_jobs[job_id] = {**_process_jobs.get(job_id, {}), "status": "done", **meta}


def _guess_media_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".dng": "image/x-adobe-dng",
        ".json": "application/json",
    }.get(path.suffix.lower(), "application/octet-stream")


def _list_local_captures() -> list[str]:
    return sorted(
        path.name
        for path in _pi_captures_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )


def _resolve_local_capture(filename: str) -> Path:
    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid capture filename")
    path = _pi_captures_dir / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Capture not found")
    return path


def _capture_from_pi_camera() -> tuple[str, dict]:
    resp = _requests.post(f"{PI_CAMERA_URL}/capture", timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    filename = payload.get("jpeg") or payload.get("filename")
    if not filename:
        raise RuntimeError("Pi camera capture response did not include a filename")
    return filename, payload


def _send_pi_capture_to_pc(filename: str) -> dict:
    resp = _requests.post(f"{PI_CAMERA_URL}/send/{filename}", timeout=30)
    resp.raise_for_status()
    return resp.json()


# Pi URLs and unified orchestrator ---------------------------------------------

PI_CAMERA_URL = "http://10.12.194.1:8080"
PI_SCANNER_URL = "http://10.12.194.1:5000"

_orchestrator = JobOrchestrator(artifacts_dir=_jobs_dir, pi_scanner_url=PI_SCANNER_URL)


class ApiCreateOneClickJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_kind: str


class ApiDevProcessCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_filename: str


def _map_orchestrator_state_to_legacy_status(state: str | None) -> str:
    return {
        "running": "running",
        "completed": "done",
        "failed": "error",
        "cancelled": "cancelled",
    }.get(state or "", state or "not_found")


def _start_orchestrator_process_capture(filename: str) -> str:
    source_path = _resolve_local_capture(filename)
    return _orchestrator.create_job(job_kind="process_capture", source_path=source_path)

# Routes ------------------------------------------------------------------------


@app.get("/")
def root():
    return RedirectResponse(url="/web/")


# Legacy scan routes ------------------------------------------------------------


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


# Legacy serial routes ----------------------------------------------------------


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


# Pi camera receive + legacy camera proxy ---------------------------------------


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
    captures = _list_local_captures()
    return {"captures": captures, "count": len(captures)}


@app.get("/camera/stream")
def camera_stream():
    try:
        resp = _requests.get(f"{PI_CAMERA_URL}/stream", stream=True, timeout=10)
        return StreamingResponse(
            resp.iter_content(chunk_size=4096),
            media_type=resp.headers.get("content-type", "multipart/x-mixed-replace; boundary=frame"),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Pi camera unreachable: {exc}") from exc


@app.post("/camera/capture")
def camera_capture():
    try:
        _, payload = _capture_from_pi_camera()
        return payload
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Pi camera error: {exc}") from exc


@app.get("/camera/captures")
def camera_captures():
    try:
        resp = _requests.get(f"{PI_CAMERA_URL}/captures", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except _requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Pi camera error: {exc}") from exc


@app.get("/camera/captures/{filename}")
def camera_get_capture(filename: str):
    try:
        resp = _requests.get(f"{PI_CAMERA_URL}/captures/{filename}", timeout=15)
        resp.raise_for_status()
        return Response(content=resp.content, media_type=resp.headers.get("content-type", "image/jpeg"))
    except _requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Pi camera error: {exc}") from exc


@app.post("/camera/send-to-pc/{filename}")
def camera_send_to_pc(filename: str):
    try:
        return _send_pi_capture_to_pc(filename)
    except _requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Pi camera error: {exc}") from exc


@app.get("/camera/status")
def camera_status():
    try:
        resp = _requests.get(f"{PI_CAMERA_URL}/status", timeout=3)
        return resp.json()
    except Exception:
        return {"camera_ready": False, "error": "Pi unreachable"}


# Legacy processing routes ------------------------------------------------------


@app.post("/process_capture")
def start_process_capture(body: dict):
    filename = body.get("filename")
    if not filename:
        raise HTTPException(status_code=400, detail="'filename' required")
    job_id = _start_orchestrator_process_capture(filename)
    return {"job_id": job_id, "status": "running", "state": "running"}


@app.get("/process_status/{job_id}")
def get_process_status(job_id: str):
    job = _orchestrator.get_job(job_id)
    if job:
        return {"status": _map_orchestrator_state_to_legacy_status(job.get("state")), **job}
    with _jobs_lock:
        return _process_jobs.get(job_id, {"status": "not_found"})


# Unified /api/* routes ---------------------------------------------------------


@app.post("/api/jobs")
def api_create_job(body: ApiCreateOneClickJobRequest):
    if body.job_kind != "one_click_scan":
        raise HTTPException(status_code=400, detail="Only one_click_scan is supported on /api/jobs")
    job_id = _orchestrator.create_job(job_kind="one_click_scan")
    return {"job_id": job_id, "state": "running"}


@app.get("/api/jobs")
def api_list_jobs():
    return {"jobs": _orchestrator.list_jobs()}


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str):
    job = _orchestrator.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: str):
    ok = _orchestrator.cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Job not running or not found")
    return {"status": "cancel_requested"}


@app.get("/api/jobs/{job_id}/artifacts/{artifact_type}")
def api_get_artifact(job_id: str, artifact_type: str):
    path = _orchestrator.get_artifact_path(job_id, artifact_type)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Artifact not available yet")
    return Response(content=path.read_bytes(), media_type=_guess_media_type(path))


@app.post("/api/jobs/{job_id}/receive_stitched_raw")
async def api_receive_stitched_raw(job_id: str, file: UploadFile = File(...)):
    data = await file.read()
    accepted, reason = _orchestrator.receive_stitched_raw(job_id, data)
    if not accepted:
        if reason == "not_found":
            raise HTTPException(status_code=404, detail="Job not found")
        raise HTTPException(status_code=409, detail=reason)
    return {"saved": True, "size": len(data)}


@app.get("/api/system/status")
def api_system_status():
    pi_scanner_ok = False
    pi_camera_ok = False
    try:
        resp = _requests.get(f"{PI_SCANNER_URL}/scan/status", timeout=2)
        pi_scanner_ok = resp.ok
    except Exception:
        pass
    try:
        resp = _requests.get(f"{PI_CAMERA_URL}/status", timeout=2)
        pi_camera_ok = resp.json().get("camera_ready", False)
    except Exception:
        pass
    active_jobs = sum(1 for job in _orchestrator.list_jobs() if job.get("state") == "running")
    return {
        "pc_server_ready": True,
        "pi_scanner_reachable": pi_scanner_ok,
        "pi_camera_reachable": pi_camera_ok,
        "active_jobs": active_jobs,
    }


# /api/dev/* aliases ------------------------------------------------------------


@app.post("/api/dev/process_capture")
def api_dev_process_capture(body: ApiDevProcessCaptureRequest):
    job_id = _start_orchestrator_process_capture(body.source_filename)
    return {"job_id": job_id, "state": "running"}


@app.post("/api/dev/camera/capture-import")
def api_dev_camera_capture_import():
    try:
        filename, capture_payload = _capture_from_pi_camera()
        transfer_payload = _send_pi_capture_to_pc(filename)
        return {
            "filename": filename,
            "capture": capture_payload,
            "transfer": transfer_payload,
            "imported": True,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Pi camera import failed: {exc}") from exc


@app.get("/api/dev/captures")
def api_dev_list_captures():
    captures = _list_local_captures()
    return {"captures": captures, "count": len(captures)}


@app.get("/api/dev/captures/{filename}")
def api_dev_get_capture(filename: str):
    path = _resolve_local_capture(filename)
    return Response(content=path.read_bytes(), media_type=_guess_media_type(path))


@app.get("/api/dev/serial/ports")
def api_dev_serial_ports():
    return list_serial_ports()


@app.post("/api/dev/serial/connect")
def api_dev_serial_connect(body: dict):
    port = body.get("port")
    if not port:
        raise HTTPException(status_code=400, detail="Field 'port' required")
    return connect_serial(port)


@app.post("/api/dev/serial/disconnect")
def api_dev_serial_disconnect():
    return disconnect_serial()


@app.post("/api/dev/calibration/backlight")
def api_dev_backlight_capture():
    try:
        resp = _requests.post(
            f"{PI_CAMERA_URL}/dev/run-pipeline",
            json={"mode": "backlight"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Backlight capture failed: {exc}") from exc
