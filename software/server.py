from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from coordinator import coordinator, ScanState
from serial_comm import (
    list_serial_ports,
    connect_serial,
    disconnect_serial,
    get_serial_status,
)

from fastapi.staticfiles import StaticFiles
from pathlib import Path

app = FastAPI()

# Serve webclient files at /web, and redirect / to /web for user-friendliness
web_dir = Path(__file__).parent / "web"
if web_dir.exists():
    app.mount("/web", StaticFiles(directory=str(web_dir), html=True), name="webclient")

from fastapi.responses import RedirectResponse


@app.get("/")
def root():
    return RedirectResponse(url="/web/")


@app.post("/scan")
def start_scan(req: dict):
    format = req.get("format", "35mm")
    coordinator.start_job(format)
    return {"status": "started", "format": format}


@app.get("/status")
def get_status():
    return coordinator.status_dict()


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


@app.post("/cancel")
def cancel_job():
    coordinator.cancel_job()
    return {"status": "cancel_requested"}
