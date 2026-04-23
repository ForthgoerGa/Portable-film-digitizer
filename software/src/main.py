"""
FastAPI server for the film digitizer scanner.

Provides REST endpoints for scan control and motor operations.
"""

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pathlib import Path

try:
    from .coordinator import ScannerCoordinator
except ImportError:
    from coordinator import ScannerCoordinator


# Create FastAPI app
app = FastAPI(title="Film Digitizer Scanner API")

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


@app.on_event("shutdown")
def shutdown_event():
    """Clean up resources on application shutdown."""
    coordinator.cleanup()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)
