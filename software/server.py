from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests as _requests
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

import calibration_store
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

# Calibration artifact directories are owned by calibration_store. Mounting them
# as static surfaces here is intentional: developer tools can diff or inspect
# the raw captures the negative branch consumed.
_calibration_root = Path(__file__).parent / "web" / "calibration"
_calibration_root.mkdir(parents=True, exist_ok=True)

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

PI_CAMERA_URL = os.getenv("PI_CAMERA_URL", "http://10.12.194.1:8080").rstrip("/")
PI_SCANNER_URL = os.getenv("PI_SCANNER_URL", "http://10.12.194.1:5000").rstrip("/")

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


_CALIBRATION_KINDS = {
    "backlight": calibration_store.BACKLIGHT,
    "base_frame": calibration_store.BASE_FRAME,
}


def _resolve_calibration_kind(kind_name: str) -> calibration_store.CalibrationKind:
    kind = _CALIBRATION_KINDS.get(kind_name)
    if kind is None:
        raise HTTPException(status_code=404, detail=f"Unknown calibration kind: {kind_name}")
    return kind


def _download_pi_capture(path_name: str, dest: Path, read_timeout_s: int = 60) -> None:
    if Path(path_name).is_absolute() or ".." in Path(path_name).parts:
        raise RuntimeError(f"Unsafe Pi capture path: {path_name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _requests.get(
        f"{PI_SCANNER_URL}/captures/file",
        params={"path": path_name},
        stream=True,
        timeout=(5, read_timeout_s),
    ) as resp:
        resp.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".downloading")
        with tmp.open("wb") as out:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    out.write(chunk)
        tmp.replace(dest)


def _wait_for_pi_scan_idle(timeout_s: int = 900) -> dict:
    deadline = time.time() + timeout_s
    last_status: dict = {}
    while time.time() < deadline:
        resp = _requests.get(f"{PI_SCANNER_URL}/scan/status", timeout=5)
        resp.raise_for_status()
        status = resp.json()
        last_status = status
        state = status.get("state")
        if state == "idle":
            return status
        if state == "error":
            raise RuntimeError(status.get("error") or "Pi scan failed")
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for Pi full-area scan; last status={last_status}")


def _capture_backlight_full_scan_from_pi(kind: calibration_store.CalibrationKind) -> dict:
    """Capture a full-area stitched RAW on the Pi and store it as backlight.

    Backlight flat-field correction must match the stitched scan dimensions,
    so a single current-position DNG is invalid for this calibration. This
    intentionally runs the same full scan/integration path as one-click scans,
    but without triggering PC post-processing.
    """
    started_at = time.time()
    start_resp = _requests.post(
        f"{PI_SCANNER_URL}/scan/start",
        json={"profile": "standard", "calibration_kind": kind.name, "upload_enabled": False},
        timeout=10,
    )
    start_resp.raise_for_status()
    start_payload = start_resp.json()

    final_status = _wait_for_pi_scan_idle(timeout_s=900)
    artifacts = final_status.get("artifacts") or {}
    raw_path = artifacts.get("stitched_raw") or "stitched_raw.dng"

    staged = kind.directory / f"{kind.name}.full_scan_download.dng"
    _download_pi_capture(raw_path, staged, read_timeout_s=240)
    try:
        state = calibration_store.store_dng_file(
            kind,
            staged,
            Path(raw_path).name,
            source="pi_full_area_stitched_scan",
            extra={
                "pi_start_response": start_payload,
                "pi_final_status": final_status,
                "elapsed_s": time.time() - started_at,
                "capture_mode": "full_area_stitched_raw",
            },
        )
    finally:
        if staged.exists():
            staged.unlink()

    return {
        "captured": True,
        "capture_mode": "full_area_stitched_raw",
        "pi_response": final_status,
        "calibration": state,
    }


async def _handle_stitched_raw_upload(job_id: str, file: UploadFile) -> dict:
    if not _orchestrator.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    job_dir = _jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = job_dir / "stitched_raw.server_uploading"
    size = 0
    try:
        with tmp_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                out.write(chunk)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise

    accepted, reason = _orchestrator.receive_stitched_raw_file(job_id, tmp_path)
    if not accepted:
        if tmp_path.exists():
            tmp_path.unlink()
        if reason == "not_found":
            raise HTTPException(status_code=404, detail="Job not found")
        raise HTTPException(status_code=409, detail=reason)
    return {"saved": True, "size": size}

# Routes ------------------------------------------------------------------------


@app.get("/")
def root():
    return RedirectResponse(url="/web/")


@app.get("/dev")
def dev_page():
    return RedirectResponse(url="/web/dev.html")


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
    return FileResponse(path, media_type=_guess_media_type(path), filename=path.name)


@app.post("/api/jobs/{job_id}/receive_stitched_raw")
async def api_receive_stitched_raw(job_id: str, file: UploadFile = File(...)):
    """Transitional browser-namespace alias for the Pi upload callback.

    Prefer /internal/jobs/{job_id}/receive_stitched_raw for new Pi deployments.
    """
    return await _handle_stitched_raw_upload(job_id, file)


@app.post("/internal/jobs/{job_id}/receive_stitched_raw")
async def internal_receive_stitched_raw(job_id: str, file: UploadFile = File(...)):
    return await _handle_stitched_raw_upload(job_id, file)


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
    return FileResponse(path, media_type=_guess_media_type(path), filename=path.name)


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
    return api_dev_calibration_capture_from_pi("backlight")


@app.get("/api/dev/calibration/summary")
def api_dev_calibration_summary():
    return calibration_store.summary()


@app.get("/api/dev/calibration/base_frame/preview")
def api_dev_base_frame_preview():
    import io as _io
    import cv2 as _cv2
    import numpy as _np
    import rawpy as _rawpy

    path = calibration_store.get_dng_path_if_ready(calibration_store.BASE_FRAME)
    if path is None:
        raise HTTPException(status_code=404, detail="No base frame DNG available")
    try:
        with _rawpy.imread(str(path)) as raw:
            full_w = raw.sizes.raw_width
            full_h = raw.sizes.raw_height
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
        bgr = _cv2.cvtColor(rgb, _cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]
        if max(w, h) > 1920:
            scale = 1920 / max(w, h)
            bgr = _cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=_cv2.INTER_AREA)
        ok, buf = _cv2.imencode(".jpg", bgr, [int(_cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            raise RuntimeError("JPEG encode failed")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Preview render failed: {exc}") from exc
    return Response(
        content=buf.tobytes(),
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache",
            "Access-Control-Expose-Headers": "X-Full-Width, X-Full-Height",
            "X-Full-Width": str(full_w),
            "X-Full-Height": str(full_h),
        },
    )


@app.get("/api/dev/calibration/roi")
def api_dev_get_roi():
    roi_path = calibration_store.dng_path(calibration_store.BASE_FRAME).parent / "reference_layout.json"
    if not roi_path.exists():
        return {"roi": None}
    try:
        layout = json.loads(roi_path.read_text(encoding="utf-8"))
        return {"roi": layout.get("base", {}).get("bbox")}
    except Exception:
        return {"roi": None}


@app.post("/api/dev/calibration/roi")
def api_dev_set_roi(body: dict):
    for field in ("x0", "y0", "x1", "y1"):
        if field not in body:
            raise HTTPException(status_code=400, detail=f"Missing field: {field}")
    x0, y0, x1, y1 = int(body["x0"]), int(body["y0"]), int(body["x1"]), int(body["y1"])
    if x0 >= x1 or y0 >= y1:
        raise HTTPException(status_code=400, detail="ROI must have positive area")
    layout = {"base": {"bbox": [x0, y0, x1, y1]}}
    roi_path = calibration_store.dng_path(calibration_store.BASE_FRAME).parent / "reference_layout.json"
    roi_path.write_text(json.dumps(layout, indent=2), encoding="utf-8")
    return {"ok": True, "roi": [x0, y0, x1, y1]}


@app.delete("/api/dev/calibration/roi")
def api_dev_clear_roi():
    roi_path = calibration_store.dng_path(calibration_store.BASE_FRAME).parent / "reference_layout.json"
    if roi_path.exists():
        roi_path.unlink()
    return {"ok": True}


@app.get("/api/dev/calibration/{kind_name}/latest")
def api_dev_calibration_latest(kind_name: str):
    kind = _resolve_calibration_kind(kind_name)
    return calibration_store.load_latest(kind)


@app.post("/api/dev/calibration/{kind_name}/upload")
async def api_dev_calibration_upload(kind_name: str, file: UploadFile = File(...)):
    kind = _resolve_calibration_kind(kind_name)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded calibration file is empty")
    try:
        return calibration_store.store_dng_upload(kind, data, file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/dev/calibration/{kind_name}/capture-from-pi")
def api_dev_calibration_capture_from_pi(kind_name: str):
    kind = _resolve_calibration_kind(kind_name)
    if kind.name == calibration_store.BACKLIGHT.name:
        try:
            return _capture_backlight_full_scan_from_pi(kind)
        except _requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Pi full-area backlight scan failed: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Backlight full-area scan failed: {exc}") from exc

    try:
        resp = _requests.post(
            f"{PI_SCANNER_URL}/dev/calibration/capture",
            json={"kind": kind.name},
            timeout=45,
        )
        resp.raise_for_status()
        pi_payload = resp.json()
        raw_path = pi_payload.get("raw_path")
        if not raw_path:
            raise RuntimeError("Pi response did not include raw_path")

        staged = kind.directory / f"{kind.name}.pi_download.dng"
        _download_pi_capture(raw_path, staged)
        state = calibration_store.store_dng_file(
            kind,
            staged,
            Path(raw_path).name,
            source="pi_scanner_capture",
            extra={"pi_response": pi_payload},
        )
        if staged.exists():
            staged.unlink()
        return {"captured": True, "pi_response": pi_payload, "calibration": state}
    except _requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Pi scanner capture failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Calibration capture failed: {exc}") from exc


@app.delete("/api/dev/calibration/{kind_name}")
def api_dev_calibration_clear(kind_name: str):
    kind = _resolve_calibration_kind(kind_name)
    return calibration_store.clear(kind)


@app.get("/api/dev/calibration/{kind_name}/artifact")
def api_dev_calibration_artifact(kind_name: str):
    kind = _resolve_calibration_kind(kind_name)
    path = calibration_store.get_dng_path_if_ready(kind)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No {kind_name} DNG available")
    return FileResponse(path, media_type="image/x-adobe-dng", filename=path.name)
