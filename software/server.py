from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from coordinator import coordinator
from serial_comm import (
    connect_serial,
    disconnect_serial,
    get_serial_status,
    list_serial_ports,
)

app = FastAPI()

# Serve web client.
web_dir = Path(__file__).parent / "web"
if web_dir.exists():
    app.mount("/web", StaticFiles(directory=str(web_dir), html=True), name="webclient")

# Serve example fractions used by scan demo.
scan_input_dir = coordinator.stitcher.tiles_dir
if scan_input_dir.exists():
    app.mount("/scan-input", StaticFiles(directory=str(scan_input_dir)), name="scan_input")

# Serve generated scan outputs.
scan_output_dir = coordinator.stitcher.output_dir
scan_output_dir.mkdir(parents=True, exist_ok=True)
app.mount("/scan-output", StaticFiles(directory=str(scan_output_dir)), name="scan_output")


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
