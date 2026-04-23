"""
FastAPI server for the film digitizer scanner.

Provides REST endpoints for scan control and motor operations.
"""

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, FileResponse
from pathlib import Path
import mimetypes
from typing import Dict, Any

try:
    from .coordinator import ScannerCoordinator
    from .config import CAPTURES_DIR
except ImportError:
    from coordinator import ScannerCoordinator
    from config import CAPTURES_DIR


# Create FastAPI app
app = FastAPI(title="Film Digitizer Scanner API")


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
@app.post("/scan/start")
def start_scan(request: dict):
    """
    Start a scanning operation.

    Request body:
    {
        "format": "35mm" | "120" | "4x5"  (optional, defaults to "35mm")
    }
    """
    film_format = request.get("format", "35mm")

    try:
        coordinator.start_scan(film_format)
        return {"status": "started", "format": film_format}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/scan/cancel")
def cancel_scan():
    """Cancel the current scanning operation."""
    coordinator.cancel_scan()
    return {"status": "cancel_requested"}


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


# Motor control endpoints
@app.post("/motor/move")
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
        if x_steps != 0:
            coordinator.scanner.move_x(x_steps)
        if y_steps != 0:
            coordinator.scanner.move_y(y_steps)

        return {"status": "moved", "x_steps": x_steps, "y_steps": y_steps}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Motor movement failed: {str(e)}")


@app.post("/motor/home")
def home_motors():
    """Return motors to home/origin position."""
    try:
        coordinator.scanner.return_to_origin()
        return {"status": "homed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Home operation failed: {str(e)}")


@app.post("/motor/set_home")
def set_home():
    """Set current motor position as home (origin)."""
    coordinator.scanner.set_home()
    return {"status": "home_set"}


@app.get("/motor/position")
def get_position():
    """Get current motor position."""
    x, y = coordinator.scanner.get_position()
    return {"x": x, "y": y}


# Capture file browser endpoints
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
