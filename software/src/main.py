"""
FastAPI server for the film digitizer scanner.

Provides REST endpoints for scan control and motor operations.
"""

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, FileResponse, StreamingResponse
from pathlib import Path
import hashlib
import json
import mimetypes
import os
import re
import time
from typing import Dict, Any

try:
    from .coordinator import ScannerCoordinator
    from . import config as scan_config
    from .config import (
        CAPTURES_DIR,
        X_SEGMENTS,
        Y_SEGMENTS,
        X_STEPS_PER_SEG,
        Y_STEPS_PER_SEG,
    )
except ImportError:
    from coordinator import ScannerCoordinator
    import config as scan_config
    from config import (
        CAPTURES_DIR,
        X_SEGMENTS,
        Y_SEGMENTS,
        X_STEPS_PER_SEG,
        Y_STEPS_PER_SEG,
    )


# Create FastAPI app
app = FastAPI(title="Film Digitizer Scanner API")

SCAN_PROFILE_NAME = "standard"
CALIBRATION_DIR = Path(__file__).parent / "calibration"
ALIGNMENT_MEASUREMENTS_PATH = CALIBRATION_DIR / "alignment_measurements.json"
MANUAL_CHECKPOINTS_PATH = CALIBRATION_DIR / "manual_checkpoints.json"
PREVIEW_FRAME_PATH = CALIBRATION_DIR / "manual_preview.jpg"
CALIBRATION_CAPTURE_DIR = CAPTURES_DIR / "calibration"
_TILE_RE = re.compile(r"row_(\d+)_col_(\d+)\.(jpg|jpeg|png)$", re.IGNORECASE)


def safe_resolve_path(path: str) -> Path:
    """Resolve a path relative to CAPTURES_DIR, rejecting traversal attempts."""
    if path.startswith("/") or ".." in path:
        raise HTTPException(status_code=400, detail="Invalid path")

    full_path = CAPTURES_DIR / path
    # Ensure it's still under CAPTURES_DIR
    try:
        full_path.relative_to(CAPTURES_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path outside captures directory")

    return full_path


def _scan_profile() -> dict[str, Any]:
    max_x = int(getattr(scan_config, "SCAN_MAX_X_POSITION", max(0, (X_SEGMENTS - 1) * X_STEPS_PER_SEG)))
    max_y = int(getattr(scan_config, "SCAN_MAX_Y_POSITION", max(0, (Y_SEGMENTS - 1) * Y_STEPS_PER_SEG)))
    return {
        "profile": SCAN_PROFILE_NAME,
        "grid": {"rows": Y_SEGMENTS, "cols": X_SEGMENTS},
        "steps_per_segment": {"x": X_STEPS_PER_SEG, "y": Y_STEPS_PER_SEG},
        "max_position": {"x": max_x, "y": max_y},
        "capture_fov_steps": {
            "x": getattr(scan_config, "CAPTURE_FOV_X_STEPS", None),
            "y": getattr(scan_config, "CAPTURE_FOV_Y_STEPS", None),
        },
        "stitch_stride_px": {
            "x": getattr(scan_config, "STITCH_TILE_STRIDE_X_PX", None),
            "y": getattr(scan_config, "STITCH_TILE_STRIDE_Y_PX", None),
        },
    }


def _current_capture_tiles() -> list[dict[str, Any]]:
    tiles = []
    for path in CAPTURES_DIR.iterdir() if CAPTURES_DIR.exists() else []:
        if not path.is_file():
            continue
        match = _TILE_RE.match(path.name)
        if not match:
            continue
        stat = path.stat()
        tiles.append(
            {
                "row": int(match.group(1)),
                "col": int(match.group(2)),
                "path": path.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
        )
    return sorted(tiles, key=lambda item: (item["row"], item["col"], item["path"]))


def _current_capture_signature() -> str | None:
    tiles = _current_capture_tiles()
    if not tiles:
        return None
    digest = hashlib.sha256()
    for tile in tiles:
        digest.update(
            f"{tile['row']},{tile['col']},{tile['path']},{tile['size']},{tile['modified']:.6f}\n".encode()
        )
    return digest.hexdigest()


def _read_alignment_store() -> dict[str, Any]:
    if not ALIGNMENT_MEASUREMENTS_PATH.exists():
        return {"measurements": []}
    try:
        return json.loads(ALIGNMENT_MEASUREMENTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"measurements": []}


def _write_alignment_store(payload: dict[str, Any]) -> None:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = ALIGNMENT_MEASUREMENTS_PATH.with_suffix(ALIGNMENT_MEASUREMENTS_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(ALIGNMENT_MEASUREMENTS_PATH)


def _write_current_alignment_copy(measurements: list[dict[str, Any]]) -> None:
    """Keep a scan-local copy for integrator compatibility and inspection."""
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = CAPTURES_DIR / "alignment_measurements.json"
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps({"measurements": measurements}, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _read_checkpoint_store() -> dict[str, Any]:
    if not MANUAL_CHECKPOINTS_PATH.exists():
        return {"checkpoints": []}
    try:
        return json.loads(MANUAL_CHECKPOINTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"checkpoints": []}


def _write_checkpoint_store(payload: dict[str, Any]) -> None:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = MANUAL_CHECKPOINTS_PATH.with_suffix(MANUAL_CHECKPOINTS_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(MANUAL_CHECKPOINTS_PATH)


def _validate_absolute_position(x_position: int, y_position: int) -> None:
    max_position = _scan_profile()["max_position"]
    if x_position < 0 or y_position < 0:
        raise HTTPException(status_code=400, detail="Target position must be non-negative")
    if x_position > max_position["x"]:
        raise HTTPException(status_code=400, detail=f"X target exceeds configured boundary {max_position['x']}")
    if y_position > max_position["y"]:
        raise HTTPException(status_code=400, detail=f"Y target exceeds configured boundary {max_position['y']}")


# Create coordinator instance
coordinator = ScannerCoordinator()


# Serve web UI if it exists
web_dir = Path(__file__).parent / "web"
if web_dir.exists():
    app.mount("/web", StaticFiles(directory=str(web_dir), html=True), name="webclient")


@app.get("/")
def root():
    """Redirect to web UI."""
    return RedirectResponse(url="/web/")


# Scan endpoints
@app.get("/scan/config")
def get_scan_config():
    """Return the active scanner profile. Phase 1 uses a single standard profile."""
    return _scan_profile()


@app.post("/scan/start")
def start_scan(request: dict):
    """
    Start the standard scanning operation using the configured grid and step sizes.
    """
    profile = request.get("profile", SCAN_PROFILE_NAME)
    if profile != SCAN_PROFILE_NAME:
        raise HTTPException(status_code=400, detail="Only the standard scan profile is supported")
    upload_url = request.get("upload_url")
    if not upload_url:
        job_id = request.get("job_id")
        pc_base_url = request.get("pc_base_url") or os.getenv("PC_APP_URL")
        if job_id and pc_base_url:
            upload_url = f"{pc_base_url.rstrip('/')}/internal/jobs/{job_id}/receive_stitched_raw"

    try:
        coordinator.start_scan(SCAN_PROFILE_NAME, upload_url=upload_url)
        return {
            "status": "started",
            "profile": SCAN_PROFILE_NAME,
            "scan_config": _scan_profile(),
            "upload_enabled": bool(upload_url),
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/worker/scan-jobs")
def worker_start_scan(request: dict):
    """Framework-aligned alias for the scanner worker start endpoint."""
    return start_scan(request)


@app.post("/scan/cancel")
def cancel_scan():
    """Cancel the current scanning operation."""
    coordinator.cancel_scan()
    return {"status": "cancel_requested"}


@app.post("/worker/scan-jobs/{job_id}/cancel")
def worker_cancel_scan(job_id: str):
    """Framework-aligned alias; current Pi worker runs one scan at a time."""
    return cancel_scan()


@app.post("/scan/reset")
def reset_scan_state():
    """Clear an error/cancelled state after motors are confirmed at home."""
    try:
        coordinator.reset_state()
        return {"status": "reset", "state": "idle"}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/scan/status")
def get_scan_status():
    """
    Get the current scan status and progress.

    Response:
    {
        "state": "idle" | "scanning" | "returning_home" | "cancelled" | "error",
        "current_row": int,
        "current_col": int,
        "total_rows": int,
        "total_cols": int,
        "progress": float,  # percentage 0-100
        "error": str  # only present if state is "error"
    }
    """
    return coordinator.get_status()


@app.get("/worker/scan-jobs/{job_id}")
def worker_get_scan_status(job_id: str):
    """Framework-aligned alias; current Pi worker exposes one active scan."""
    return coordinator.get_status()


# Motor control endpoints (read-only status remains under /motor)
@app.get("/motor/status")
def get_motor_status():
    """Get the current motor positions and modes."""
    x_state, y_state = coordinator.scanner.get_motor_states()
    return {
        "x": {
            "position": x_state.position,
            "mode": x_state.mode.value,
        },
        "y": {
            "position": y_state.position,
            "mode": y_state.mode.value,
        },
    }


@app.get("/motor/position")
def get_motor_position():
    """Return current motor position and configured absolute boundaries."""
    x_position, y_position = coordinator.get_position()
    return {
        "x": x_position,
        "y": y_position,
        "max_position": _scan_profile()["max_position"],
    }


@app.post("/scan/move")
def move_motors(request: dict):
    """
    Move motors by specified steps.

    Request body:
    {
        "x": int,  # steps to move X-axis (positive = forward, negative = backward)
        "y": int   # steps to move Y-axis (positive = forward, negative = backward)
    }
    """
    x_steps = request.get("x", 0)
    y_steps = request.get("y", 0)

    try:
        coordinator.move_motors(x_steps, y_steps)
        return {"status": "moved", "x_steps": x_steps, "y_steps": y_steps}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/scan/move_to")
def move_to_position(request: dict):
    """Move motors to an absolute target position within configured boundaries."""
    try:
        x_position = int(request.get("x", 0))
        y_position = int(request.get("y", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="x and y must be integers") from exc
    _validate_absolute_position(x_position, y_position)
    try:
        coordinator.move_to_position(x_position, y_position)
        return {"status": "moved", "x": x_position, "y": y_position}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/scan/home")
def home_motors():
    """Return motors to home/origin position."""
    try:
        coordinator.home_motors()
        return {"status": "homed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Home operation failed: {str(e)}")


@app.post("/scan/set_home")
def set_home():
    """Set current motor position as home (origin)."""
    try:
        coordinator.set_home_motors()
        return {"status": "home_set"}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/camera/preview.jpg")
def camera_preview():
    """Capture and return a live preview frame for manual positioning."""
    try:
        CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
        coordinator.capture_preview(PREVIEW_FRAME_PATH)
        return FileResponse(PREVIEW_FRAME_PATH, media_type="image/jpeg")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview capture failed: {e}")


@app.get("/camera/stream.mjpg")
def camera_stream():
    """Best-effort MJPEG stream for scanner-side manual positioning."""

    def _frames():
        while True:
            try:
                CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
                coordinator.capture_preview(PREVIEW_FRAME_PATH)
                payload = PREVIEW_FRAME_PATH.read_bytes()
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Cache-Control: no-cache\r\n\r\n"
                    + payload
                    + b"\r\n"
                )
            except Exception as exc:
                message = f"camera stream paused: {exc}\n".encode()
                yield (
                    b"--frame\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Cache-Control: no-cache\r\n\r\n"
                    + message
                    + b"\r\n"
                )
                time.sleep(1.0)
            else:
                time.sleep(0.25)

    return StreamingResponse(
        _frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache"},
    )


@app.post("/dev/calibration/capture")
def capture_calibration_frame(request: dict):
    """Capture one calibration RAW+JPEG at the current scanner position."""
    kind = str(request.get("kind", "")).strip().lower()
    filenames = {
        "backlight": ("backlight_frame.dng", "backlight_frame.jpg"),
        "base_frame": ("base_frame.dng", "base_frame.jpg"),
    }
    if kind not in filenames:
        raise HTTPException(status_code=400, detail="kind must be 'backlight' or 'base_frame'")

    raw_name, preview_name = filenames[kind]
    raw_path = CALIBRATION_CAPTURE_DIR / raw_name
    preview_path = CALIBRATION_CAPTURE_DIR / preview_name
    try:
        result = coordinator.capture_calibration_frame(raw_path, preview_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Calibration capture failed: {exc}") from exc

    return {
        "kind": kind,
        "raw_path": str(raw_path.relative_to(CAPTURES_DIR)),
        "preview_path": str(preview_path.relative_to(CAPTURES_DIR)),
        "raw_size": result["raw_size"],
        "preview_size": result["preview_size"],
        "position": result["position"],
    }


@app.get("/calibration/checkpoints")
def get_manual_checkpoints():
    """Return manually recorded motor-position checkpoints."""
    payload = _read_checkpoint_store()
    return {
        "storage_path": str(MANUAL_CHECKPOINTS_PATH),
        "scan_profile": _scan_profile(),
        "current_position": dict(zip(("x", "y"), coordinator.get_position())),
        "checkpoints": payload.get("checkpoints", []),
    }


@app.post("/calibration/checkpoints")
def save_manual_checkpoint(request: dict):
    """Record current motor position as a named calibration checkpoint."""
    x_position, y_position = coordinator.get_position()
    payload = _read_checkpoint_store()
    checkpoints = payload.setdefault("checkpoints", [])
    checkpoint = {
        "created_at": time.time(),
        "label": str(request.get("label", f"checkpoint_{len(checkpoints) + 1}")),
        "note": str(request.get("note", "")),
        "x": int(x_position),
        "y": int(y_position),
        "scan_profile": _scan_profile(),
    }
    checkpoints.append(checkpoint)
    _write_checkpoint_store(payload)
    return {"status": "saved", "checkpoint": checkpoint, "count": len(checkpoints)}


# Manual calibration endpoints
@app.get("/calibration/alignment")
def get_alignment_measurements():
    """Return manually recorded alignments for the current capture set only."""
    capture_signature = _current_capture_signature()
    payload = _read_alignment_store()
    all_measurements = payload.get("measurements", [])
    measurements = [
        item for item in all_measurements
        if capture_signature is not None and item.get("capture_signature") == capture_signature
    ]
    if measurements:
        _write_current_alignment_copy(measurements)
    return {
        "capture_signature": capture_signature,
        "storage_path": str(ALIGNMENT_MEASUREMENTS_PATH),
        "measurements": measurements,
        "all_measurement_count": len(all_measurements),
    }


@app.post("/calibration/alignment")
def save_alignment_measurement(request: dict):
    """Append one manually recorded tile-to-tile displacement measurement."""
    axis = request.get("axis")
    if axis not in {"x", "y"}:
        raise HTTPException(status_code=400, detail="axis must be 'x' or 'y'")
    capture_signature = _current_capture_signature()
    if capture_signature is None:
        raise HTTPException(status_code=409, detail="No current preview tiles are available for alignment")
    try:
        measurement = {
            "created_at": time.time(),
            "capture_signature": capture_signature,
            "scan_profile": _scan_profile(),
            "axis": axis,
            "row": int(request["row"]),
            "col": int(request["col"]),
            "neighbor_row": int(request["neighbor_row"]),
            "neighbor_col": int(request["neighbor_col"]),
            "dx": int(request["dx"]),
            "dy": int(request["dy"]),
            "tile_width": int(request.get("tile_width", 0)),
            "tile_height": int(request.get("tile_height", 0)),
            "first_path": str(request.get("first_path", "")),
            "second_path": str(request.get("second_path", "")),
            "note": str(request.get("note", "")),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid alignment payload: {exc}")

    payload = _read_alignment_store()
    # Upsert: replace any existing entry for the same pair so JSON stays clean.
    existing = payload.setdefault("measurements", [])
    payload["measurements"] = [
        m for m in existing
        if not (
            m.get("capture_signature") == measurement["capture_signature"]
            and m.get("axis") == measurement["axis"]
            and m.get("row") == measurement["row"]
            and m.get("col") == measurement["col"]
            and m.get("neighbor_row") == measurement["neighbor_row"]
            and m.get("neighbor_col") == measurement["neighbor_col"]
        )
    ]
    payload["measurements"].append(measurement)
    _write_alignment_store(payload)

    current_measurements = [
        item for item in payload["measurements"]
        if item.get("capture_signature") == capture_signature
    ]
    _write_current_alignment_copy(current_measurements)
    return {"status": "saved", "measurement": measurement, "count": len(current_measurements)}


# Capture file browser endpoints
@app.get("/captures/tiles")
def get_capture_tiles():
    """Return the normalized root-level preview tiles used by alignment."""
    tiles = _current_capture_tiles()
    return {
        "capture_signature": _current_capture_signature(),
        "scan_profile": _scan_profile(),
        "tiles": tiles,
        "tile_count": len(tiles),
    }


@app.get("/captures/tree")
def get_captures_tree():
    """
    Get the full directory tree under captures/.

    Response:
    {
        "name": "captures",
        "type": "directory",
        "children": [...]
    }
    """

    def build_tree(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return None
        if path.is_file():
            return {
                "name": path.name,
                "type": "file",
                "size": path.stat().st_size,
                "modified": path.stat().st_mtime,
            }
        else:
            children = []
            for child in sorted(path.iterdir()):
                child_tree = build_tree(child)
                if child_tree:
                    children.append(child_tree)
            return {"name": path.name, "type": "directory", "children": children}

    tree = build_tree(CAPTURES_DIR)
    if tree:
        tree["name"] = "captures"  # Override the full path name
    return tree or {"name": "captures", "type": "directory", "children": []}


@app.get("/captures/list")
def list_captures(path: str = ""):
    """
    List immediate children of a directory under captures/.

    Query param:
        path: relative path from captures/ (default: root)

    Response:
    {
        "path": "path",
        "entries": [
            {"name": "file.jpg", "type": "file", "size": 1234, "modified": 1234567890.0}
        ]
    }
    """
    dir_path = safe_resolve_path(path)
    if not dir_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not dir_path.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    entries = []
    for item in sorted(dir_path.iterdir()):
        stat = item.stat()
        entries.append(
            {
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
                "size": stat.st_size if item.is_file() else 0,
                "modified": stat.st_mtime,
            }
        )

    return {"path": path, "entries": entries}


@app.get("/captures/file")
def get_capture_file(path: str):
    """
    Serve a file from captures/.

    Query param:
        path: relative path from captures/

    Returns the file content with appropriate Content-Type.
    """
    file_path = safe_resolve_path(path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    media_type, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(file_path, media_type=media_type)


@app.get("/captures/meta")
def get_capture_meta(path: str):
    """
    Get metadata for a file or directory in captures/.

    Query param:
        path: relative path from captures/

    Response:
    {
        "name": "file.jpg",
        "type": "file",
        "size": 1234,
        "modified": 1234567890.0,
        "scan_row": 0,  # parsed from filename if applicable
        "scan_col": 0
    }
    """
    file_path = safe_resolve_path(path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    stat = file_path.stat()
    meta = {
        "name": file_path.name,
        "type": "directory" if file_path.is_dir() else "file",
        "size": stat.st_size if file_path.is_file() else 0,
        "modified": stat.st_mtime,
    }

    # Try to parse scan position from filename (row_X_col_Y.jpg)
    if file_path.is_file() and file_path.suffix.lower() in [".jpg", ".jpeg", ".png"]:
        import re

        match = re.match(r"row_(\d+)_col_(\d+)\.", file_path.name)
        if match:
            meta["scan_row"] = int(match.group(1))
            meta["scan_col"] = int(match.group(2))

    return meta


@app.on_event("shutdown")
def shutdown_event():
    """Clean up resources on application shutdown."""
    coordinator.cleanup()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)
