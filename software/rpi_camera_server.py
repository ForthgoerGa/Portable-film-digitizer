#!/usr/bin/env python3
"""
ECE 445 – Raspberry Pi HQ Camera Server
========================================
Streams a live MJPEG preview to any browser on the LAN, supports
hardware-button and web-triggered full-resolution captures (JPEG + raw DNG),
and runs the Post_Processing_Negative pipeline locally after each capture.

Usage
-----
    python3 rpi_camera_server.py [--port 8080] [--button-pin 17] [--pc-host 192.168.x.x]

Endpoints
---------
    GET  /                    – Web UI  (live preview + capture button + capture gallery)
    GET  /stream              – MJPEG stream (embed as <img src="/stream">)
    POST /capture             – Trigger a still capture; returns capture metadata JSON
    GET  /captures            – JSON list of stored captures
    GET  /captures/<filename> – Download a JPEG or DNG file
    POST /send/<filename>     – Push a stored JPEG to the PC pipeline server
    GET  /status              – Camera + GPIO status JSON

Camera settings
---------------
Still captures use low-ISO settings (AnalogueGain ≈ 1.0 = ISO 100) with
auto-exposure disabled so that noise is minimised.  ExposureTime defaults to
20 000 µs (1/50 s) which is appropriate for a stable backlight; adjust with
--exposure-us.

Post-processing hook
--------------------
After each capture, on_capture() launches the Post_Processing_Negative
pipeline (run_physical_correction.py) in a background thread if the required
calibration files are present in the capture directory:
  - backlight_frame.dng  (or backlight.dng)
  - base_frame.dng
  - reference_layout.json
Results are written to <capture-dir>/processed/.
"""

import argparse
import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests
import RPi.GPIO as GPIO
from flask import Flask, Response, jsonify, render_template_string, request, send_file
from picamera2 import Picamera2
from PIL import Image

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_PORT        = 8080
DEFAULT_BUTTON_PIN  = 17
DEFAULT_CAPTURE_DIR = Path.home() / "captures"
DEFAULT_PC_HOST     = "http://192.168.1.100:8000"

PREVIEW_SIZE   = (1332, 990)    # binned 4:3 – fast preview
CAPTURE_SIZE   = (4056, 3040)   # full IMX477 resolution
STREAM_QUALITY = 85             # JPEG quality for MJPEG stream

# ── Fixed low-ISO still capture settings ──────────────────────────────────────
# All AE/AWB/NR controls are hard-locked for maximum fidelity DNG captures.
# AnalogueGain 1.0 = ISO 100 (minimum sensor gain, minimum noise floor).
# ColourGains (1.0, 1.0) locks R/B channel gains; rawpy reads the real WB from DNG metadata.
# NoiseReductionMode 0 disables all in-camera NR so the RAW data is unmodified.
# ExposureTime 50 000 µs (1/20 s) — longer exposure improves SNR under backlight.
STILL_ANALOGUE_GAIN  = 1.0          # ISO 100 — never raise; more gain = more noise
STILL_EXPOSURE_US    = 50_000       # 1/20 s; override with --exposure-us
STILL_AWB_ENABLE     = False        # AWB off — rawpy derives WB from DNG metadata
STILL_COLOUR_GAINS   = (1.0, 1.0)  # neutral R/B gains; no digital colour shift
STILL_NR_MODE        = 0            # 0 = off; keeps RAW data unprocessed

# ── Pipeline directories ───────────────────────────────────────────────────────
# Resolved after arg parsing; set here so the module-level type is clear.
_PIPELINE_SCRIPT: Path | None = None   # absolute path to run_physical_correction.py

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

# ── Shared state ─────────────────────────────────────────────────────────────
_frame_lock    = threading.Lock()
_latest_frame: bytes | None = None
_camera_ready  = threading.Event()
_capture_lock  = threading.Lock()
_capture_dir: Path = DEFAULT_CAPTURE_DIR
_pc_host: str  = DEFAULT_PC_HOST
_picam2: Picamera2 | None = None
_still_config  = None
_still_exposure_us: int = STILL_EXPOSURE_US

# ── Dev-mode state ────────────────────────────────────────────────────────────
_dev_roles: dict = {"base": None, "backlight": None, "film": None}
_dev_roi: list | None = None          # [x0, y0, x1, y1] full-res pixels
_dev_pipeline: dict = {
    "status": "idle",   # idle | running | done | error
    "log": [],
    "images": [],
    "returncode": None,
    "params": {},
}
_dev_lock = threading.Lock()
_processed_dir: Path = DEFAULT_CAPTURE_DIR / "processed" / "_none_"  # replaced per run
_dev_stop_event = threading.Event()   # set to request pipeline abort
_dev_proc: subprocess.Popen | None = None  # current pipeline subprocess


# ── Camera thread ─────────────────────────────────────────────────────────────

def camera_worker():
    """Background thread: continuous preview in video mode."""
    global _latest_frame, _picam2, _still_config

    picam2 = Picamera2()

    video_cfg = picam2.create_video_configuration(
        main={"size": PREVIEW_SIZE, "format": "RGB888"},
        controls={"FrameRate": 30},
    )
    # Still configuration: full sensor resolution + RAW DNG.
    # Low-ISO controls are injected here so they take effect the moment the
    # camera switches to still mode, before the shutter fires.
    still_cfg = picam2.create_still_configuration(
        main={"size": CAPTURE_SIZE},
        raw={"size": picam2.sensor_resolution},
        controls={
            "AeEnable":            False,
            "AwbEnable":           STILL_AWB_ENABLE,
            "AnalogueGain":        STILL_ANALOGUE_GAIN,
            "ExposureTime":        _still_exposure_us,
            "ColourGains":         STILL_COLOUR_GAINS,
            "NoiseReductionMode":  STILL_NR_MODE,
        },
    )
    picam2.configure(video_cfg)
    picam2.start()
    _picam2 = picam2
    _still_config = still_cfg
    _camera_ready.set()
    log.info(
        "Camera started – preview %dx%d  capture %dx%d  ISO~%.0f  exp=%dµs  NR=off  ColourGains=%s",
        *PREVIEW_SIZE, *CAPTURE_SIZE,
        STILL_ANALOGUE_GAIN * 100, _still_exposure_us, STILL_COLOUR_GAINS,
    )

    try:
        while True:
            if _capture_lock.locked():
                time.sleep(0.05)
                continue
            arr = picam2.capture_array("main")
            img = Image.fromarray(arr)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=STREAM_QUALITY)
            with _frame_lock:
                _latest_frame = buf.getvalue()
    finally:
        picam2.stop()
        picam2.close()


# ── Capture logic ─────────────────────────────────────────────────────────────

def do_capture() -> dict:
    """Switch to still mode, capture JPEG + DNG at ISO~100, return metadata."""
    with _capture_lock:
        if _picam2 is None:
            raise RuntimeError("Camera not ready")

        ts        = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        jpeg_path = _capture_dir / f"capture_{ts}.jpg"
        raw_path  = _capture_dir / f"capture_{ts}.dng"

        log.info(
            "Capturing → %s  (gain=%.1f, exp=%dµs)",
            jpeg_path.name, STILL_ANALOGUE_GAIN, _still_exposure_us,
        )

        request = _picam2.switch_mode_and_capture_request(_still_config)
        try:
            request.save("main", str(jpeg_path))
            request.save_dng(str(raw_path))
        finally:
            request.release()
            # ColourGains=(1.0,1.0) from the still config persists into video mode
            # and makes the preview green (R/B suppressed).  Re-enable AWB so the
            # camera recalculates proper colour gains for the live stream.
            _picam2.set_controls({"AwbEnable": True})

        # Read back camera metadata that picamera2 embedded in the request.
        try:
            meta_obj   = request.get_metadata()
            actual_gain = float(meta_obj.get("AnalogueGain", STILL_ANALOGUE_GAIN))
            actual_exp  = int(meta_obj.get("ExposureTime", _still_exposure_us))
        except Exception:
            actual_gain = STILL_ANALOGUE_GAIN
            actual_exp  = _still_exposure_us

        log.info(
            "Captured: %s  actual_gain=%.2f (~ISO %.0f)  actual_exp=%dµs",
            jpeg_path.name, actual_gain, actual_gain * 100, actual_exp,
        )

        meta = {
            "jpeg":          jpeg_path.name,
            "raw":           raw_path.name,
            "timestamp":     ts,
            "jpeg_size":     jpeg_path.stat().st_size,
            "raw_size":      raw_path.stat().st_size,
            "analogue_gain": actual_gain,
            "iso_approx":    round(actual_gain * 100),
            "exposure_us":   actual_exp,
        }
        threading.Thread(
            target=on_capture, args=(jpeg_path, raw_path), daemon=True
        ).start()
        return meta


# ── Post-processing hook ──────────────────────────────────────────────────────

def on_capture(jpeg_path: Path, raw_path: Path):
    """Called after every capture.  Runs the local physical correction pipeline."""
    run_local_pipeline(raw_path)


def run_local_pipeline(raw_path: Path) -> bool:
    """Run Post_Processing_Negative/run_physical_correction.py on the new frame.

    Requires these calibration files to already exist in the same directory:
      backlight_frame.dng   – flat-field capture (no film)
      base_frame.dng        – unexposed orange-mask reference
      reference_layout.json – ROI definitions for base sampling

    Results are written to <capture_dir>/processed/.
    Returns True on success.
    """
    if _PIPELINE_SCRIPT is None or not _PIPELINE_SCRIPT.exists():
        log.warning("Pipeline script not found (%s); skipping post-processing.", _PIPELINE_SCRIPT)
        return False

    input_dir  = raw_path.parent
    output_dir = input_dir / "processed"

    # Check all required calibration files are present.
    required = [
        input_dir / "backlight_frame.dng",
        input_dir / "base_frame.dng",
        input_dir / "reference_layout.json",
    ]
    missing = [str(p.name) for p in required if not p.exists()]
    if missing:
        log.info(
            "Skipping pipeline for %s — missing calibration files: %s",
            raw_path.name, ", ".join(missing),
        )
        return False

    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(_PIPELINE_SCRIPT),
        "--input-dir",  str(input_dir),
        "--output-dir", str(output_dir),
        "--frame",      str(raw_path),
        "--skip-npy",
        "--skip-linear16",
        "--skip-stage3-debug-png",
        "--skip-stage4-debug-png",
        "--skip-stage2-debug-png",
    ]
    log.info("Running pipeline: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,   # 5 min max
        )
        if result.returncode == 0:
            log.info("Pipeline finished OK for %s → %s", raw_path.name, output_dir)
            return True
        log.error(
            "Pipeline exited %d for %s:\n%s",
            result.returncode, raw_path.name, result.stderr[-2000:],
        )
    except subprocess.TimeoutExpired:
        log.error("Pipeline timed out for %s", raw_path.name)
    except Exception as exc:
        log.exception("Pipeline error for %s: %s", raw_path.name, exc)
    return False


def send_to_pc(jpeg_path: Path) -> bool:
    """POST a JPEG to the PC's FastAPI server. Returns True on success."""
    url = f"{_pc_host}/receive_capture"
    try:
        with open(jpeg_path, "rb") as f:
            resp = requests.post(
                url,
                files={"file": (jpeg_path.name, f, "image/jpeg")},
                timeout=30,
            )
        if resp.ok:
            log.info("Sent %s → PC (%s)", jpeg_path.name, resp.status_code)
            return True
        log.warning("PC responded %s for %s", resp.status_code, jpeg_path.name)
    except requests.RequestException as exc:
        log.warning("Could not reach PC server: %s", exc)
    return False


# ── GPIO button ───────────────────────────────────────────────────────────────

def setup_button(pin: int):
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.add_event_detect(
        pin,
        GPIO.FALLING,
        callback=lambda _: threading.Thread(target=do_capture, daemon=True).start(),
        bouncetime=500,
    )
    log.info("GPIO button on BCM pin %d (active-low, 500 ms debounce)", pin)


# ── Flask routes ──────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ECE 445 – Camera</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #111; color: #eee; display: flex;
           flex-direction: column; align-items: center; padding: 1.5rem; gap: 1.5rem; }
    h1 { font-size: 1.2rem; letter-spacing: .05em; color: #aaa; }
    #preview { width: 100%; max-width: 860px; border-radius: 6px; background: #000; }
    #controls { display: flex; gap: 1rem; flex-wrap: wrap; justify-content: center; }
    button { padding: .6rem 1.6rem; border: none; border-radius: 4px; font-size: 1rem;
             cursor: pointer; transition: opacity .15s; }
    button:active { opacity: .7; }
    #btn-capture { background: #e05; color: #fff; font-weight: 600; font-size: 1.1rem; }
    #btn-send    { background: #27ae60; color: #fff; }
    #status { font-size: .85rem; color: #888; min-height: 1.2em; }
    #gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
               gap: .75rem; width: 100%; max-width: 860px; }
    .thumb { border-radius: 4px; overflow: hidden; background: #222; cursor: pointer;
             transition: transform .15s; }
    .thumb:hover { transform: scale(1.03); }
    .thumb img { width: 100%; display: block; }
    .thumb span { display: block; font-size: .7rem; color: #888; padding: .3rem .4rem;
                  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  </style>
</head>
<body>
  <h1>ECE 445 · HQ Camera</h1>
  <img id="preview" src="/stream" alt="live preview">
  <div id="controls">
    <button id="btn-capture" onclick="capture()">&#9679; Capture</button>
    <button id="btn-send" onclick="sendLatest()">&#8593; Send to PC</button>
  </div>
  <div id="status">Ready</div>
  <div id="gallery"></div>

<script>
  let lastCapture = null;

  async function capture() {
    setStatus('Capturing…');
    try {
      const r = await fetch('/capture', { method: 'POST' });
      const d = await r.json();
      if (d.error) { setStatus('Error: ' + d.error); return; }
      lastCapture = d.jpeg;
      setStatus(`Saved: ${d.jpeg}  ISO~${d.iso_approx}  exp=${d.exposure_us}µs  (${(d.jpeg_size/1024).toFixed(0)} KB JPEG · ${(d.raw_size/1024/1024).toFixed(1)} MB DNG)`);
      loadGallery();
    } catch(e) { setStatus('Capture failed: ' + e); }
  }

  async function sendLatest() {
    if (!lastCapture) { setStatus('No capture yet'); return; }
    setStatus('Sending to PC…');
    try {
      const r = await fetch('/send/' + encodeURIComponent(lastCapture), { method: 'POST' });
      const d = await r.json();
      setStatus(d.ok ? `Sent ${lastCapture} ✓` : `Send failed: ${d.error}`);
    } catch(e) { setStatus('Send failed: ' + e); }
  }

  async function loadGallery() {
    const r = await fetch('/captures');
    const captures = await r.json();
    const g = document.getElementById('gallery');
    g.innerHTML = '';
    captures.slice().reverse().forEach(c => {
      if (!c.endsWith('.jpg')) return;
      const div = document.createElement('div');
      div.className = 'thumb';
      div.innerHTML = `<img src="/captures/${c}" loading="lazy">
                       <span>${c}</span>`;
      div.onclick = () => { lastCapture = c; setStatus('Selected: ' + c); };
      g.appendChild(div);
    });
  }

  function setStatus(msg) { document.getElementById('status').textContent = msg; }

  loadGallery();
</script>
</body>
</html>"""


@app.get("/")
def index():
    return render_template_string(_HTML)


def _generate_mjpeg():
    """Generator: yields MJPEG frames from the shared buffer."""
    _camera_ready.wait(timeout=10)
    while True:
        with _frame_lock:
            frame = _latest_frame
        if frame is None:
            time.sleep(0.05)
            continue
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )
        time.sleep(1 / 30)


@app.get("/stream")
def stream():
    return Response(
        _generate_mjpeg(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.post("/capture")
def trigger_capture():
    try:
        meta = do_capture()
        return jsonify(meta)
    except Exception as exc:
        log.exception("Capture failed")
        return jsonify({"error": str(exc)}), 500


@app.get("/captures")
def list_captures():
    files = sorted(p.name for p in _capture_dir.iterdir() if p.is_file())
    return jsonify(files)


@app.get("/captures/<filename>")
def download_capture(filename: str):
    path = _capture_dir / filename
    if not path.exists() or not path.is_file():
        return jsonify({"error": "not found"}), 404
    if path.parent.resolve() != _capture_dir.resolve():
        return jsonify({"error": "forbidden"}), 403
    return send_file(str(path))


@app.post("/send/<filename>")
def send_capture(filename: str):
    path = _capture_dir / filename
    if not path.exists():
        return jsonify({"error": "not found"}), 404
    if path.parent.resolve() != _capture_dir.resolve():
        return jsonify({"error": "forbidden"}), 403
    ok = send_to_pc(path)
    return jsonify({"ok": ok, "error": None if ok else "PC unreachable"})


@app.get("/status")
def status():
    return jsonify({
        "camera_ready":      _camera_ready.is_set(),
        "capture_busy":      _capture_lock.locked(),
        "capture_dir":       str(_capture_dir),
        "pc_host":           _pc_host,
        "analogue_gain":     STILL_ANALOGUE_GAIN,
        "iso_approx":        round(STILL_ANALOGUE_GAIN * 100),
        "exposure_us":       _still_exposure_us,
        "pipeline_script":   str(_PIPELINE_SCRIPT) if _PIPELINE_SCRIPT else None,
        "captures":          len(list(_capture_dir.iterdir())) if _capture_dir.exists() else 0,
    })


# ── Dev-mode pipeline runner ──────────────────────────────────────────────────

def _dev_run_pipeline_thread():
    global _dev_pipeline, _dev_proc
    pipeline_dir = Path(_PIPELINE_SCRIPT).parent if _PIPELINE_SCRIPT else None
    if pipeline_dir is None or not pipeline_dir.exists():
        with _dev_lock:
            _dev_pipeline["status"] = "error"
            _dev_pipeline["log"].append("ERROR: pipeline directory not found")
        return

    with _dev_lock:
        roles = dict(_dev_roles)
        extra_args = _build_pipeline_extra_args(_dev_pipeline.get("params", {}))

    # _processed_dir was already set to a fresh per-run directory by the route handler.
    output_dir = _processed_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "run_physical_correction.py",
        "--input-dir",       str(_capture_dir),
        "--backlight-frame", str(_capture_dir / roles["backlight"]),
        "--base-frame",      str(_capture_dir / roles["base"]),
        "--frame",           str(_capture_dir / roles["film"]),
        "--reference-layout",str(_capture_dir / "reference_layout.json"),
        "--output-dir",      str(output_dir),
    ] + extra_args
    log.info("Dev pipeline: %s", " ".join(cmd))

    with _dev_lock:
        _dev_pipeline["log"] = ["$ " + " ".join(cmd), ""]
        _dev_pipeline["images"] = []
        _dev_pipeline["returncode"] = None

    try:
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen(
            [sys.executable, "-u"] + cmd[1:],
            cwd=str(pipeline_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env,
        )
        _dev_proc = proc
        stopped = False
        for line in proc.stdout:
            if _dev_stop_event.is_set():
                proc.terminate()
                stopped = True
                break
            with _dev_lock:
                _dev_pipeline["log"].append(line.rstrip())
        proc.wait()
        _dev_proc = None

        with _dev_lock:
            if stopped:
                _dev_pipeline["status"] = "error"
                _dev_pipeline["returncode"] = proc.returncode
                _dev_pipeline["log"].append("\n--- Pipeline stopped by user ---")
            else:
                _dev_pipeline["status"] = "done" if proc.returncode == 0 else "error"
                _dev_pipeline["returncode"] = proc.returncode
                _dev_pipeline["log"].append(
                    f"\n--- Pipeline {'completed OK' if proc.returncode == 0 else f'exited {proc.returncode}'} ---"
                )
    except Exception as exc:
        log.exception("Dev pipeline error")
        _dev_proc = None
        with _dev_lock:
            _dev_pipeline["status"] = "error"
            _dev_pipeline["log"].append(f"EXCEPTION: {exc}")


def _build_pipeline_extra_args(params: dict) -> list:
    """Map the UI params dict to extra CLI args for run_physical_correction.py."""
    args = []
    simple = {
        "flat_strength":                 "--flat-strength",
        "flat_sigma_frac":               "--flat-sigma-frac",
        "color_unmix_strength":          "--color-unmix-strength",
        "stage10_matrix_preset":         "--stage10-matrix-preset",
        "stage10_gray_anchor_strength":  "--stage10-gray-anchor-strength",
        "stage10_soft_matrix_strength":  "--stage10-soft-matrix-strength",
        "stage10_neutral_damp_strength": "--stage10-neutral-damp-strength",
        "stage10_red_guard_strength":    "--stage10-red-guard-strength",
        "stage4_exposure_ev":            "--stage4-final-exposure-ev",
        "stage4_contrast":               "--stage4-final-contrast",
        "stage4_black_point":            "--stage4-final-black-point",
        "stage4_white_point":            "--stage4-final-white-point",
        "stage4_temp_shift":             "--stage4-final-temp-shift",
        "stage4_tint_shift":             "--stage4-final-tint-shift",
    }
    for key, flag in simple.items():
        val = params.get(key)
        if val is not None:
            args += [flag, str(val)]
    # --inversion-channel-gains needs all three values present
    r, g, b = params.get("inv_r"), params.get("inv_g"), params.get("inv_b")
    if r is not None and g is not None and b is not None:
        args += ["--inversion-channel-gains", str(r), str(g), str(b)]
    return args


# ── Dev-mode routes ───────────────────────────────────────────────────────────

@app.get("/dev")
def dev_ui():
    return _DEV_HTML


@app.get("/dev/state")
def dev_state():
    with _dev_lock:
        pipe = dict(_dev_pipeline)
        pipe["log"] = list(pipe["log"])
    # Scan output dir live so images appear as each file is written,
    # not only after the subprocess finishes.
    if _processed_dir.exists():
        pipe["images"] = sorted(
            p.name for p in _processed_dir.iterdir()
            if p.suffix.lower() == ".png"
        )
    else:
        pipe["images"] = []
    return jsonify({
        "camera_ready": _camera_ready.is_set(),
        "roles": _dev_roles,
        "roi": _dev_roi,
        "pipeline": pipe,
    })


@app.post("/dev/set-role")
def dev_set_role():
    body = request.get_json(force=True)
    filename = body.get("filename", "").strip()
    role = body.get("role", "").strip()
    if role not in ("base", "backlight", "film"):
        return jsonify({"error": "role must be base|backlight|film"}), 400
    path = _capture_dir / filename
    if not path.exists():
        return jsonify({"error": "file not found"}), 404
    _dev_roles[role] = filename
    log.info("Dev: set %s = %s", role, filename)
    return jsonify({"ok": True, "role": role, "filename": filename})


@app.post("/dev/set-roi")
def dev_set_roi():
    global _dev_roi
    body = request.get_json(force=True)
    try:
        x0, y0 = int(body["x0"]), int(body["y0"])
        x1, y1 = int(body["x1"]), int(body["y1"])
    except (KeyError, ValueError) as exc:
        return jsonify({"error": f"bad coords: {exc}"}), 400
    # Normalise so x0<x1, y0<y1
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    _dev_roi = [x0, y0, x1, y1]
    layout = {"base": {"bbox": _dev_roi}}
    roi_path = _capture_dir / "reference_layout.json"
    roi_path.write_text(json.dumps(layout, indent=2), encoding="utf-8")
    log.info("Dev: ROI saved %s → %s", _dev_roi, roi_path)
    return jsonify({"ok": True, "roi": _dev_roi, "saved_to": str(roi_path)})


@app.post("/dev/run-pipeline")
def dev_run_pipeline():
    body = request.get_json(force=True) or {}
    params = body.get("params", {})
    with _dev_lock:
        missing = [r for r in ("base", "backlight", "film") if not _dev_roles.get(r)]
        if missing:
            return jsonify({"error": f"Missing roles: {', '.join(missing)}"}), 400
        if _dev_roi is None:
            return jsonify({"error": "No base ROI set"}), 400
        if _dev_pipeline["status"] == "running":
            return jsonify({"error": "Pipeline already running"}), 409
        _dev_pipeline["status"] = "running"
        _dev_pipeline["log"] = []
        _dev_pipeline["images"] = []
        _dev_pipeline["params"] = params

    # Each run gets its own timestamped output directory so previous images
    # never bleed into the current run's stage diagram.
    global _processed_dir
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _processed_dir = _capture_dir / "processed" / f"run_{run_ts}"
    _processed_dir.mkdir(parents=True, exist_ok=True)

    _dev_stop_event.clear()
    threading.Thread(target=_dev_run_pipeline_thread, daemon=True).start()
    return jsonify({"ok": True, "status": "running"})


@app.post("/dev/stop-pipeline")
def dev_stop_pipeline():
    with _dev_lock:
        if _dev_pipeline["status"] != "running":
            return jsonify({"error": "not running"}), 400
    _dev_stop_event.set()
    if _dev_proc is not None:
        _dev_proc.terminate()
    return jsonify({"ok": True})


@app.get("/dev/images/<filename>")
def dev_get_image(filename: str):
    path = _processed_dir / filename
    if not path.exists() or not path.is_file():
        return jsonify({"error": "not found"}), 404
    if path.parent.resolve() != _processed_dir.resolve():
        return jsonify({"error": "forbidden"}), 403
    return send_file(str(path))


# ── Dev HTML ──────────────────────────────────────────────────────────────────

_DEV_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ECE 445 · Dev Mode</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg0:#0d1117;--bg1:#161b22;--bg2:#21262d;
  --border:#30363d;--text:#e6edf3;--muted:#7d8590;
  --green:#3fb950;--blue:#58a6ff;--orange:#f0883e;
  --red:#f85149;--yellow:#e3b341;--purple:#bc8cff;
  --radius:8px;
  --mono:'JetBrains Mono','Fira Code',Consolas,monospace;
}
html,body{height:100%;overflow:hidden}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg0);color:var(--text);
     display:grid;grid-template-rows:48px 1fr}

/* header */
header{background:var(--bg1);border-bottom:1px solid var(--border);
       padding:0 1.25rem;display:flex;align-items:center;gap:.75rem;flex-shrink:0}
header h1{font:600 .9rem/1 var(--mono)}
.badge-dev{font:700 .65rem/1 var(--mono);background:var(--orange);color:#000;
           padding:.15rem .5rem;border-radius:99px;letter-spacing:.07em}
#cam-dot{margin-left:auto;font:500 .72rem/1 var(--mono);color:var(--muted)}

/* layout */
main{display:grid;grid-template-columns:380px 1fr;gap:1px;background:var(--border);
     overflow:hidden}
.col{background:var(--bg0);overflow-y:auto;padding:.9rem;display:flex;
     flex-direction:column;gap:.75rem}

/* section titles */
.sec-title{font:600 .68rem/1 var(--mono);color:var(--muted);text-transform:uppercase;
           letter-spacing:.1em;display:flex;align-items:center;gap:.4rem}

/* stream */
.stream-wrap{position:relative;background:#000;border-radius:var(--radius);
             overflow:hidden;aspect-ratio:4/3;flex-shrink:0}
.stream-wrap img{width:100%;height:100%;object-fit:contain;display:block}
.stream-overlay{position:absolute;inset:0;background:rgba(0,0,0,.75);
                display:none;align-items:center;justify-content:center;
                font:600 .9rem/1 var(--mono);color:var(--green)}
#cap-status{font:500 .7rem/1.5 var(--mono);color:var(--muted);word-break:break-all;min-height:1em}

/* buttons */
.btn{display:inline-flex;align-items:center;gap:.35rem;padding:.42rem .9rem;
     border:1px solid var(--border);border-radius:6px;font:600 .78rem/1 var(--mono);
     cursor:pointer;background:var(--bg2);color:var(--text);
     transition:background .1s,border-color .1s;white-space:nowrap}
.btn:hover{background:#30363d;border-color:#8b949e}
.btn:active{transform:scale(.97)}
.btn:disabled{opacity:.35;cursor:not-allowed;transform:none}
.btn-capture{background:#da3633;border-color:var(--red);color:#fff;font-size:.85rem;padding:.5rem 1.1rem}
.btn-capture:hover{background:#b91c1c}
.btn-run{background:#1a7f37;border-color:var(--green);color:#fff;
         width:100%;justify-content:center;padding:.55rem}
.btn-run:hover{background:#166f30}
.btn-sm{padding:.22rem .5rem;font-size:.67rem}
.btn-base{border-color:var(--blue);color:var(--blue)}
.btn-backlight{border-color:var(--orange);color:var(--orange)}
.btn-film{border-color:var(--green);color:var(--green)}
.btn-base.active{background:#1f6feb;color:#fff;border-color:#1f6feb}
.btn-backlight.active{background:#9b4a0f;color:#fff}
.btn-film.active{background:#1a7f37;color:#fff}

/* role cards */
.role-row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.45rem}
.role-card{background:var(--bg1);border:1px solid var(--border);border-radius:var(--radius);
           padding:.45rem .55rem;min-width:0}
.role-card.ok{border-color:var(--green)}
.role-label{font:600 .62rem/1 var(--mono);color:var(--muted);text-transform:uppercase;
            letter-spacing:.08em;margin-bottom:.2rem}
.role-card.ok .role-label{color:var(--green)}
.role-val{font:500 .65rem/1.3 var(--mono);color:var(--text);
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* roi section */
.roi-box{background:var(--bg1);border:1px solid var(--border);border-radius:var(--radius);padding:.65rem}
.crop-wrap{position:relative;display:inline-block;width:100%;user-select:none;margin-top:.45rem}
.crop-wrap img{width:100%;display:block;border-radius:4px}
.crop-canvas{position:absolute;top:0;left:0;width:100%;height:100%;cursor:crosshair}
#roi-coords{font:500 .67rem/1.4 var(--mono);color:var(--muted);margin-top:.35rem}

/* gallery */
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:.45rem}
.gthumb{background:var(--bg1);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.gthumb img{width:100%;aspect-ratio:4/3;object-fit:cover;display:block;cursor:zoom-in}
.gthumb-info{padding:.3rem .35rem}
.gthumb-name{font:.62rem/1.3 var(--mono);color:var(--muted);
             white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:.25rem}
.gthumb-btns{display:flex;gap:.2rem;flex-wrap:wrap}

/* pipeline right column */
.checklist{display:flex;gap:.75rem;flex-wrap:wrap;background:var(--bg1);
           border:1px solid var(--border);border-radius:var(--radius);padding:.55rem .75rem}
.chk{display:flex;align-items:center;gap:.3rem;font:500 .72rem/1 var(--mono)}
.chk .dot{width:7px;height:7px;border-radius:50%;background:var(--border);flex-shrink:0}
.chk.ok .dot{background:var(--green)}
.chk:not(.ok){color:var(--muted)}

.pipe-badge{display:inline-flex;align-items:center;gap:.35rem;font:600 .72rem/1 var(--mono);
            padding:.18rem .55rem;border-radius:99px;border:1px solid}
.pipe-badge.idle{color:var(--muted);border-color:var(--border)}
.pipe-badge.running{color:var(--blue);border-color:var(--blue);animation:pulse 1s infinite}
.pipe-badge.done{color:var(--green);border-color:var(--green)}
.pipe-badge.error{color:var(--red);border-color:var(--red)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}

/* log */
.log-box{background:#010409;border:1px solid var(--border);border-radius:var(--radius);
         padding:.65rem .75rem;height:260px;overflow-y:auto;
         font:.68rem/1.65 var(--mono);flex-shrink:0}
.ll{color:var(--muted)}.ll.info{color:var(--text)}.ll.warn{color:var(--yellow)}
.ll.err{color:var(--red)}.ll.cmd{color:var(--blue)}.ll.done{color:var(--green)}
.ll.blank{min-height:.8em}

/* ── Final result card ────────────────────────────────────────────────────── */
.final-card{background:var(--bg1);border:2px solid var(--green);border-radius:var(--radius);overflow:hidden}
.final-hdr{padding:.5rem .75rem;background:rgba(63,185,80,.08);display:flex;
           align-items:center;gap:.6rem;flex-wrap:wrap}
.final-title{font:700 .75rem/1 var(--mono);color:var(--green);flex:1;min-width:0;
             white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.final-card img{width:100%;display:block;cursor:zoom-in;max-height:500px;
                object-fit:contain;background:#000}

/* ── Pipeline stage diagram ───────────────────────────────────────────────── */
.pipe-stages{display:flex;flex-direction:column;gap:0}
.stage-arrow{height:22px;display:flex;align-items:center;justify-content:center;
             color:var(--border);font-size:.85rem;flex-shrink:0;pointer-events:none}
.stage-node{background:var(--bg1);border:1px solid var(--border);
            border-radius:var(--radius);overflow:hidden;transition:border-color .2s}
.stage-node.has-imgs{border-color:#1f6feb55}
.stage-hdr{padding:.42rem .65rem;display:flex;align-items:center;gap:.45rem;
           cursor:pointer;user-select:none}
.stage-hdr:hover{background:rgba(255,255,255,.03)}
.stage-num{font:700 .7rem/1 var(--mono);color:var(--muted);min-width:1.1rem}
.stage-name{font:600 .72rem/1 var(--mono);color:var(--text)}
.stage-desc{font:500 .6rem/1 var(--mono);color:var(--muted);margin-left:.25rem}
.stage-count{font:600 .62rem/1 var(--mono);color:var(--blue);margin-left:auto;flex-shrink:0}
.stage-chev{font-size:.7rem;color:var(--muted);margin-left:.3rem;flex-shrink:0}
.stage-imgs{display:flex;gap:.45rem;padding:.35rem .65rem .55rem;
            flex-wrap:wrap;overflow:hidden}
.stage-imgs.collapsed{display:none}
.simg{display:flex;flex-direction:column;align-items:center;gap:.2rem}
.simg img{width:100px;height:75px;object-fit:contain;background:#000;
          border-radius:4px;border:1px solid var(--border);cursor:zoom-in;
          transition:border-color .12s,transform .12s;display:block}
.simg img:hover{border-color:var(--blue);transform:scale(1.04)}
.simg span{font:500 .57rem/1.3 var(--mono);color:var(--muted);
           text-align:center;max-width:100px;word-break:break-word}

/* lightbox */
#lb{position:fixed;inset:0;background:rgba(0,0,0,.93);display:none;
    align-items:center;justify-content:center;z-index:999;cursor:zoom-out}
#lb.open{display:flex}
#lb img{max-width:95vw;max-height:95vh;object-fit:contain;border-radius:6px}

::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}

/* ── param panel ──────────────────────────────────────────────────────────── */
.param-group{background:var(--bg1);border:1px solid var(--border);border-radius:var(--radius)}
.param-summary{padding:.42rem .65rem;cursor:pointer;list-style:none;
               font:600 .67rem/1 var(--mono);color:var(--muted);
               text-transform:uppercase;letter-spacing:.08em;
               display:flex;align-items:center;gap:.35rem;user-select:none}
.param-summary::-webkit-details-marker{display:none}
.param-summary::before{content:'▶';font-size:.55rem;flex-shrink:0;transition:transform .15s}
details[open]>.param-summary::before{transform:rotate(90deg)}
details[open]>.param-summary{color:var(--text)}
.param-body{padding:.35rem .65rem .6rem;display:flex;flex-direction:column;gap:.45rem;
            border-top:1px solid var(--border)}
.param-row{display:flex;align-items:center;gap:.4rem}
.param-label{font:600 .65rem/1 var(--mono);color:var(--text);flex:1;min-width:0}
.param-hint{font:400 .58rem/1.4 var(--mono);color:var(--muted)}
.param-val{font:700 .67rem/1 var(--mono);color:var(--blue);min-width:3.8rem;text-align:right;flex-shrink:0}
input[type=range]{width:100%;accent-color:var(--blue);cursor:pointer;height:4px}
.param-select{background:var(--bg2);border:1px solid var(--border);color:var(--text);
              font:600 .68rem/1 var(--mono);padding:.22rem .45rem;border-radius:4px;cursor:pointer}
.param-gains{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.4rem}
.gain-item{display:flex;flex-direction:column;gap:.15rem;align-items:center}
.gain-item label{font:.58rem/1 var(--mono);color:var(--muted)}
.gain-item input[type=number]{width:100%;background:var(--bg2);border:1px solid var(--border);
  color:var(--text);font:.68rem/1 var(--mono);padding:.22rem .3rem;border-radius:4px;
  text-align:center;-moz-appearance:textfield}
.gain-item input[type=number]::-webkit-inner-spin-button{-webkit-appearance:none}
</style>
</head>
<body>
<header>
  <h1>ECE 445 / rpi_camera_server</h1>
  <span class="badge-dev">DEV</span>
  <a href="/" style="font:500 .72rem/1 var(--mono);color:var(--muted);text-decoration:none;margin-left:.5rem">← simple UI</a>
  <span id="cam-dot">● checking…</span>
</header>

<main>
  <!-- ═══ LEFT COLUMN ═══════════════════════════════════════════ -->
  <div class="col" id="col-left">

    <div class="sec-title">▶ live stream</div>
    <div class="stream-wrap">
      <img src="/stream" alt="live">
      <div class="stream-overlay" id="cap-overlay">Capturing…</div>
    </div>
    <div style="display:flex;align-items:center;gap:.6rem">
      <button class="btn btn-capture" id="btn-cap" onclick="doCapture()">● Capture</button>
      <div id="cap-status">Ready</div>
    </div>

    <!-- Role assignment cards -->
    <div class="sec-title">◈ frame assignments</div>
    <div class="role-row">
      <div class="role-card" id="rc-base">
        <div class="role-label">Base Frame</div>
        <div class="role-val" id="rv-base">—</div>
      </div>
      <div class="role-card" id="rc-backlight">
        <div class="role-label">Backlight</div>
        <div class="role-val" id="rv-backlight">—</div>
      </div>
      <div class="role-card" id="rc-film">
        <div class="role-label">Film Frame</div>
        <div class="role-val" id="rv-film">—</div>
      </div>
    </div>

    <!-- ROI crop tool -->
    <div class="roi-box" id="roi-box" style="display:none">
      <div class="sec-title">✂ base roi crop</div>
      <div class="crop-wrap">
        <img id="crop-img" src="" alt="base frame">
        <canvas id="crop-cv" class="crop-canvas"></canvas>
      </div>
      <div id="roi-coords">Draw a rectangle over the clear-base film region</div>
      <div style="display:flex;gap:.4rem;margin-top:.45rem;align-items:center;flex-wrap:wrap">
        <button class="btn btn-sm" id="btn-roi" onclick="applyROI()" disabled>Apply ROI</button>
        <button class="btn btn-sm" onclick="clearDraft()">Clear</button>
        <span id="roi-msg" style="font:.67rem/1 var(--mono);color:var(--muted)"></span>
      </div>
    </div>

    <!-- Gallery -->
    <div class="sec-title">⬡ captures <span id="gal-count" style="color:var(--muted)"></span></div>
    <div class="gallery" id="gallery"></div>
  </div>

  <!-- ═══ RIGHT COLUMN ══════════════════════════════════════════ -->
  <div class="col" id="col-right">

    <div style="display:flex;align-items:center;gap:.65rem">
      <div class="sec-title" style="margin:0">⚙ pipeline</div>
      <span class="pipe-badge idle" id="pipe-badge">idle</span>
    </div>

    <div class="checklist" id="checklist">
      <div class="chk" id="chk-base">    <span class="dot"></span>base frame</div>
      <div class="chk" id="chk-backlight"><span class="dot"></span>backlight</div>
      <div class="chk" id="chk-film">    <span class="dot"></span>film frame</div>
      <div class="chk" id="chk-roi">     <span class="dot"></span>base ROI</div>
    </div>

    <!-- ── Parameters ─────────────────────────────────────────── -->
    <div class="sec-title">◆ parameters</div>
    <div style="display:flex;flex-direction:column;gap:.3rem">

      <details class="param-group">
        <summary class="param-summary">① Flat Field</summary>
        <div class="param-body">
          <div class="param-row">
            <label class="param-label">flat-strength</label>
            <span class="param-val" id="pv-fstr">1.00</span>
          </div>
          <input type="range" id="p-fstr" min="0" max="2" step="0.05" value="1.0"
                 oninput="pv('fstr',this.value,2)">
          <div class="param-hint">Vignetting/hot-spot correction intensity. 1.0&nbsp;=&nbsp;physical divide, 0&nbsp;=&nbsp;off, &gt;1&nbsp;amplifies residuals.</div>

          <div class="param-row">
            <label class="param-label">flat-sigma-frac</label>
            <span class="param-val" id="pv-fsig">0.020</span>
          </div>
          <input type="range" id="p-fsig" min="0.005" max="0.12" step="0.005" value="0.02"
                 oninput="pv('fsig',this.value,3)">
          <div class="param-hint">Blur radius as fraction of image diagonal. Smaller = sharper illumination map, preserves local hot-spots.</div>
        </div>
      </details>

      <details class="param-group">
        <summary class="param-summary">③ Dye Unmix</summary>
        <div class="param-body">
          <div class="param-row">
            <label class="param-label">color-unmix-strength</label>
            <span class="param-val" id="pv-unmix">1.00</span>
          </div>
          <input type="range" id="p-unmix" min="0" max="1" step="0.05" value="1.0"
                 oninput="pv('unmix',this.value,2)">
          <div class="param-hint">Cross-channel dye bleed removal. Reduce if colors look artificial or over-separated.</div>
        </div>
      </details>

      <details class="param-group">
        <summary class="param-summary">④ Density Inversion</summary>
        <div class="param-body">
          <label class="param-label">inversion-channel-gains</label>
          <div class="param-gains">
            <div class="gain-item">
              <label>R</label>
              <input type="number" id="p-inv-r" value="1.35" min="0.5" max="3.0" step="0.05">
            </div>
            <div class="gain-item">
              <label>G</label>
              <input type="number" id="p-inv-g" value="1.20" min="0.5" max="3.0" step="0.05">
            </div>
            <div class="gain-item">
              <label>B</label>
              <input type="number" id="p-inv-b" value="1.10" min="0.5" max="3.0" step="0.05">
            </div>
          </div>
          <div class="param-hint">Per-channel brightness boost after expm1 inversion. Raise all to brighten; skew R vs B to warm/cool the inversion base.</div>
        </div>
      </details>

      <details class="param-group" open>
        <summary class="param-summary">⑤ Stage 10 · Color Reference</summary>
        <div class="param-body">
          <div class="param-row">
            <label class="param-label">matrix-preset</label>
            <select class="param-select" id="p-s10pre" oninput="updatePresetHint()">
              <option value="conservative">conservative</option>
              <option value="medium" selected>medium</option>
              <option value="aggressive">aggressive</option>
            </select>
          </div>
          <div class="param-hint" id="s10pre-hint">Balanced shift toward reference primaries. Default — start here. Switch to conservative if reds look over-corrected.</div>

          <div class="param-row">
            <label class="param-label">gray-anchor-strength</label>
            <span class="param-val" id="pv-s10ga">0.35</span>
          </div>
          <input type="range" id="p-s10ga" min="0" max="1" step="0.05" value="0.35"
                 oninput="pv('s10ga',this.value,2)">
          <div class="param-hint">Weak luma normalisation toward a neutral grey point. Lower to preserve intentional warmth or coolness.</div>

          <div class="param-row">
            <label class="param-label">soft-matrix-strength</label>
            <span class="param-val" id="pv-s10sm">0.15</span>
          </div>
          <input type="range" id="p-s10sm" min="0" max="1" step="0.05" value="0.15"
                 oninput="pv('s10sm',this.value,2)">
          <div class="param-hint">Blend factor for the empirical color matrix. Lower = closer to raw-inversion hue; higher = stronger preset correction.</div>

          <div class="param-row">
            <label class="param-label">neutral-damp-strength</label>
            <span class="param-val" id="pv-s10nd">0.25</span>
          </div>
          <input type="range" id="p-s10nd" min="0" max="1" step="0.05" value="0.25"
                 oninput="pv('s10nd',this.value,2)">
          <div class="param-hint">Suppresses colour cast in near-neutral (grey) pixels. Raise to clean up grey and white areas.</div>

          <div class="param-row">
            <label class="param-label">red-guard-strength</label>
            <span class="param-val" id="pv-s10rg">0.30</span>
          </div>
          <input type="range" id="p-s10rg" min="0" max="1" step="0.05" value="0.30"
                 oninput="pv('s10rg',this.value,2)">
          <div class="param-hint">Clamps oversaturated reds. Raise if skin tones or warm highlights look too orange.</div>
        </div>
      </details>

      <details class="param-group" open>
        <summary class="param-summary">⑧ Stage 4 · Final Trim</summary>
        <div class="param-body">
          <div class="param-row">
            <label class="param-label">exposure-ev</label>
            <span class="param-val" id="pv-ev">+0.0</span>
          </div>
          <input type="range" id="p-ev" min="-3" max="3" step="0.1" value="0"
                 oninput="pv('ev',this.value,1,true)">
          <div class="param-hint">Global exposure in stops. +1&nbsp;=&nbsp;one stop brighter.</div>

          <div class="param-row">
            <label class="param-label">contrast</label>
            <span class="param-val" id="pv-con">1.02</span>
          </div>
          <input type="range" id="p-con" min="0.5" max="2.0" step="0.05" value="1.02"
                 oninput="pv('con',this.value,2)">
          <div class="param-hint">S-curve contrast. &gt;1 deepens; &lt;1 lifts shadows.</div>

          <div class="param-row">
            <label class="param-label">black-point</label>
            <span class="param-val" id="pv-bp">0.000</span>
          </div>
          <input type="range" id="p-bp" min="0" max="0.1" step="0.002" value="0"
                 oninput="pv('bp',this.value,3)">
          <div class="param-hint">Shadow clip. Raise to deepen blacks and reduce dark-area colour cast.</div>

          <div class="param-row">
            <label class="param-label">white-point</label>
            <span class="param-val" id="pv-wp">1.000</span>
          </div>
          <input type="range" id="p-wp" min="0.85" max="1.0" step="0.005" value="1.0"
                 oninput="pv('wp',this.value,3)">
          <div class="param-hint">Highlight clip. Lower to recover blown highlights.</div>

          <div class="param-row">
            <label class="param-label">temp-shift</label>
            <span class="param-val" id="pv-tmp">+0.000</span>
          </div>
          <input type="range" id="p-tmp" min="-0.15" max="0.15" step="0.005" value="0"
                 oninput="pv('tmp',this.value,3,true)">
          <div class="param-hint">Colour temperature: positive&nbsp;=&nbsp;warmer (more R), negative&nbsp;=&nbsp;cooler (more B).</div>

          <div class="param-row">
            <label class="param-label">tint-shift</label>
            <span class="param-val" id="pv-tnt">+0.000</span>
          </div>
          <input type="range" id="p-tnt" min="-0.15" max="0.15" step="0.005" value="0"
                 oninput="pv('tnt',this.value,3,true)">
          <div class="param-hint">Tint: positive&nbsp;=&nbsp;green push, negative&nbsp;=&nbsp;magenta push.</div>
        </div>
      </details>

    </div>

    <div style="display:flex;gap:.5rem">
      <button class="btn btn-run" id="btn-run" onclick="runPipeline()" disabled style="flex:1">▶ Run Pipeline</button>
      <button class="btn" id="btn-stop" onclick="stopPipeline()" disabled
              style="border-color:var(--red);color:var(--red);padding:.55rem .85rem">■ Stop</button>
    </div>

    <div class="sec-title">⌥ log</div>
    <div class="log-box" id="log-box"></div>

      <!-- Final result (large, shown on completion) -->
      <div id="final-section" style="display:none">
        <div class="sec-title">★ final result</div>
        <div class="final-card">
          <div class="final-hdr">
            <span style="color:var(--green);font-size:.9rem">✓</span>
            <span class="final-title" id="final-name">stage4_final_finish.png</span>
            <button class="btn btn-sm" id="btn-toggle-stages" onclick="toggleStages()">▼ Show stages</button>
          </div>
          <img id="final-img" src="" alt="Final result" onclick="openLB(this.src)">
        </div>
      </div>

      <!-- Pipeline stage diagram -->
      <div id="stages-wrap">
        <div class="sec-title" id="stages-hdr" style="display:none">◎ pipeline stages</div>
        <div class="pipe-stages" id="pipe-stages"></div>
      </div>
  </div>
</main>

<div id="lb" onclick="closeLB()"><img id="lb-img" src="" alt=""></div>

<script>
// ─── state ────────────────────────────────────────────────────────────────────
let roles={base:null,backlight:null,film:null}, roi=null, pipeStatus='idle';
let logLen=0, _stagesVisible=false, _pipelineWasDone=false;
let drafting=false, draft=null, roiFull=null;
let cropNW=0, cropNH=0;

// ─── polling ──────────────────────────────────────────────────────────────────
async function tick(){
  try{
    const d=await(await fetch('/dev/state')).json();
    roles=d.roles; roi=d.roi;
    updateRoles(); updateChecklist();
    if(d.pipeline.status!==pipeStatus){ pipeStatus=d.pipeline.status; updateBadge(); }
    appendLog(d.pipeline.log);
    renderPipeline(d.pipeline.images, d.pipeline.status);
    const dot=document.getElementById('cam-dot');
    dot.textContent=d.camera_ready?'● camera ready':'○ not ready';
    dot.style.color=d.camera_ready?'#3fb950':'#f85149';
  }catch(_){}
}
setInterval(tick,1400); tick();

// ─── gallery ──────────────────────────────────────────────────────────────────
async function loadGallery(){
  const files=await(await fetch('/captures')).json();
  const jpgs=files.filter(f=>f.endsWith('.jpg')).reverse();
  document.getElementById('gal-count').textContent=jpgs.length?`(${jpgs.length})`:'';
  const g=document.getElementById('gallery'); g.innerHTML='';
  jpgs.forEach(jpg=>{
    const dng=jpg.replace('.jpg','.dng');
    const div=document.createElement('div'); div.className='gthumb';
    const ab=roles.base===dng?'active':'', ac=roles.backlight===dng?'active':'', af=roles.film===dng?'active':'';
    div.innerHTML=`<img src="/captures/${enc(jpg)}" loading="lazy" onclick="openLB(this.src)">
      <div class="gthumb-info">
        <div class="gthumb-name">${shortName(jpg)}</div>
        <div class="gthumb-btns">
          <button class="btn btn-sm btn-base ${ab}" onclick="setRole('${dng}','base')">Base</button>
          <button class="btn btn-sm btn-backlight ${ac}" onclick="setRole('${dng}','backlight')">BG</button>
          <button class="btn btn-sm btn-film ${af}" onclick="setRole('${dng}','film')">Film</button>
        </div>
      </div>`;
    g.appendChild(div);
  });
}
function shortName(n){return n.replace('capture_','').replace(/\\.jpg$/,'').replace(/_/g,' ')}
setInterval(loadGallery,8000); loadGallery();

// ─── roles ────────────────────────────────────────────────────────────────────
async function setRole(f,r){
  await post('/dev/set-role',{filename:f,role:r});
  await tick(); loadGallery();
  if(r==='base') updateCropSrc(f);
}

function updateRoles(){
  ['base','backlight','film'].forEach(r=>{
    const v=roles[r];
    document.getElementById(`rv-${r}`).textContent=v?shortDng(v):'—';
    document.getElementById(`rc-${r}`).classList.toggle('ok',!!v);
  });
  const rb=document.getElementById('roi-box');
  if(roles.base){ rb.style.display=''; updateCropSrc(roles.base); }
  else rb.style.display='none';
}
function shortDng(n){return n.replace('capture_','').replace(/\\.dng$/,'')}

// ─── ROI crop canvas ──────────────────────────────────────────────────────────
const cv=document.getElementById('crop-cv');
const ci=document.getElementById('crop-img');

function updateCropSrc(dng){
  const jpg=dng.replace('.dng','.jpg');
  const url='/captures/'+enc(jpg);
  if(ci.src.endsWith(enc(jpg))) return;
  ci.onload=()=>{ cropNW=ci.naturalWidth; cropNH=ci.naturalHeight; resizeCv(); drawCv(); };
  ci.src=url;
}

function resizeCv(){
  cv.width=ci.offsetWidth; cv.height=ci.offsetHeight;
  cv.style.width=ci.offsetWidth+'px'; cv.style.height=ci.offsetHeight+'px';
}
window.addEventListener('resize',()=>{ if(cropNW){ resizeCv(); drawCv(); } });

function mpos(e){
  const r=cv.getBoundingClientRect();
  const src=e.touches?e.touches[0]:e;
  return{x:src.clientX-r.left, y:src.clientY-r.top};
}
cv.addEventListener('mousedown',e=>{ drafting=true; const p=mpos(e); draft={x0:p.x,y0:p.y,x1:p.x,y1:p.y}; });
cv.addEventListener('mousemove',e=>{ if(!drafting)return; const p=mpos(e); draft.x1=p.x; draft.y1=p.y; drawCv(); });
cv.addEventListener('mouseup',()=>finishDraft());
cv.addEventListener('mouseleave',()=>{ if(drafting)finishDraft(); });
cv.addEventListener('touchstart',e=>{e.preventDefault();cv.dispatchEvent(new MouseEvent('mousedown',{clientX:e.touches[0].clientX,clientY:e.touches[0].clientY}));});
cv.addEventListener('touchmove',e=>{e.preventDefault();cv.dispatchEvent(new MouseEvent('mousemove',{clientX:e.touches[0].clientX,clientY:e.touches[0].clientY}));});
cv.addEventListener('touchend',e=>{e.preventDefault();finishDraft();});

function finishDraft(){
  drafting=false;
  if(!draft)return;
  const sx=cropNW/cv.width, sy=cropNH/cv.height;
  roiFull={
    x0:Math.round(Math.min(draft.x0,draft.x1)*sx),
    y0:Math.round(Math.min(draft.y0,draft.y1)*sy),
    x1:Math.round(Math.max(draft.x0,draft.x1)*sx),
    y1:Math.round(Math.max(draft.y0,draft.y1)*sy),
  };
  document.getElementById('roi-coords').textContent=
    `[${roiFull.x0}, ${roiFull.y0}, ${roiFull.x1}, ${roiFull.y1}]  —  ${roiFull.x1-roiFull.x0}×${roiFull.y1-roiFull.y0} px`;
  document.getElementById('btn-roi').disabled=false;
  drawCv();
}

function drawCv(){
  const ctx=cv.getContext('2d'); ctx.clearRect(0,0,cv.width,cv.height);
  // Saved ROI in green
  if(roi){
    const sx=cv.width/cropNW, sy=cv.height/cropNH;
    const x=roi[0]*sx,y=roi[1]*sy,w=(roi[2]-roi[0])*sx,h=(roi[3]-roi[1])*sy;
    ctx.strokeStyle='#3fb950'; ctx.lineWidth=2; ctx.setLineDash([]);
    ctx.strokeRect(x,y,w,h);
    ctx.fillStyle='rgba(63,185,80,.09)'; ctx.fillRect(x,y,w,h);
    ctx.fillStyle='#3fb950'; ctx.font='11px monospace'; ctx.fillText('base ROI',x+4,y+14);
  }
  // Draft in blue dashed
  if(draft){
    const x=Math.min(draft.x0,draft.x1),y=Math.min(draft.y0,draft.y1);
    const w=Math.abs(draft.x1-draft.x0),h=Math.abs(draft.y1-draft.y0);
    ctx.strokeStyle='#58a6ff'; ctx.lineWidth=1.5; ctx.setLineDash([4,3]);
    ctx.strokeRect(x,y,w,h);
    ctx.fillStyle='rgba(88,166,255,.07)'; ctx.fillRect(x,y,w,h);
  }
}

async function applyROI(){
  if(!roiFull)return;
  await post('/dev/set-roi',roiFull);
  document.getElementById('roi-msg').textContent='✓ ROI saved';
  draft=null; await tick();
}
function clearDraft(){
  draft=null; roiFull=null;
  document.getElementById('roi-coords').textContent='Draw a rectangle over the clear-base film region';
  document.getElementById('btn-roi').disabled=true;
  document.getElementById('roi-msg').textContent='';
  drawCv();
}

// ─── capture ──────────────────────────────────────────────────────────────────
async function doCapture(){
  document.getElementById('btn-cap').disabled=true;
  document.getElementById('cap-overlay').style.display='flex';
  const st=document.getElementById('cap-status');
  st.textContent='Capturing…'; st.style.color='';
  try{
    const d=await(await fetch('/capture',{method:'POST'})).json();
    if(d.error)throw new Error(d.error);
    st.textContent=`${d.jpeg}  ISO~${d.iso_approx}  exp=${d.exposure_us}µs  ${(d.raw_size/1024/1024).toFixed(1)} MB DNG`;
    loadGallery();
  }catch(e){ st.textContent='Error: '+e.message; st.style.color='var(--red)'; }
  finally{
    document.getElementById('btn-cap').disabled=false;
    document.getElementById('cap-overlay').style.display='none';
  }
}

// ─── param helpers ────────────────────────────────────────────────────────────
function pv(id, val, dec=2, sign=false){
  const n=parseFloat(val);
  const s=n.toFixed(dec);
  document.getElementById('pv-'+id).textContent=sign&&n>=0?'+'+s:s;
}
const PRESET_HINTS={
  conservative:'Subtle shift — use if reds or skin tones look over-corrected.',
  medium:'Balanced shift toward reference primaries. Default — start here.',
  aggressive:'Strong shift — use when colours look flat or washed-out after inversion.',
};
function updatePresetHint(){
  const v=document.getElementById('p-s10pre').value;
  document.getElementById('s10pre-hint').textContent=PRESET_HINTS[v]||'';
}
function collectParams(){
  const g=id=>document.getElementById(id);
  return {
    flat_strength:                 parseFloat(g('p-fstr').value),
    flat_sigma_frac:               parseFloat(g('p-fsig').value),
    color_unmix_strength:          parseFloat(g('p-unmix').value),
    inv_r:                         parseFloat(g('p-inv-r').value),
    inv_g:                         parseFloat(g('p-inv-g').value),
    inv_b:                         parseFloat(g('p-inv-b').value),
    stage10_matrix_preset:         g('p-s10pre').value,
    stage10_gray_anchor_strength:  parseFloat(g('p-s10ga').value),
    stage10_soft_matrix_strength:  parseFloat(g('p-s10sm').value),
    stage10_neutral_damp_strength: parseFloat(g('p-s10nd').value),
    stage10_red_guard_strength:    parseFloat(g('p-s10rg').value),
    stage4_exposure_ev:            parseFloat(g('p-ev').value),
    stage4_contrast:               parseFloat(g('p-con').value),
    stage4_black_point:            parseFloat(g('p-bp').value),
    stage4_white_point:            parseFloat(g('p-wp').value),
    stage4_temp_shift:             parseFloat(g('p-tmp').value),
    stage4_tint_shift:             parseFloat(g('p-tnt').value),
  };
}

// ─── pipeline ─────────────────────────────────────────────────────────────────
function updateChecklist(){
  const ok={base:!!roles.base,backlight:!!roles.backlight,film:!!roles.film,roi:!!roi};
  Object.entries(ok).forEach(([k,v])=>document.getElementById('chk-'+k).classList.toggle('ok',v));
  const ready=Object.values(ok).every(Boolean);
  const running=pipeStatus==='running';
  document.getElementById('btn-run').disabled=!ready||running;
  document.getElementById('btn-stop').disabled=!running;
}

function updateBadge(){
  const b=document.getElementById('pipe-badge');
  b.className='pipe-badge '+pipeStatus;
  b.textContent={idle:'idle',running:'▶ running',done:'✓ done',error:'✗ error'}[pipeStatus]||pipeStatus;
  updateChecklist();
}

async function runPipeline(){
  logLen=0; _stagesVisible=false; _pipelineWasDone=false;
  document.getElementById('log-box').innerHTML='';
  document.getElementById('pipe-stages').innerHTML='';
  document.getElementById('final-section').style.display='none';
  document.getElementById('stages-hdr').style.display='none';
  document.getElementById('stages-wrap').style.display='';
  const r=await post('/dev/run-pipeline',{params:collectParams()});
  if(r.error){ appendLog(['ERROR: '+r.error]); }
}

async function stopPipeline(){
  document.getElementById('btn-stop').disabled=true;
  await post('/dev/stop-pipeline',{});
}

function appendLog(lines){
  if(!lines||lines.length<=logLen)return;
  const box=document.getElementById('log-box');
  const atBottom=box.scrollHeight-box.scrollTop-box.clientHeight<50;
  for(let i=logLen;i<lines.length;i++){
    const t=lines[i]; const d=document.createElement('div');
    d.className='ll '+logClass(t); d.textContent=t||' '; box.appendChild(d);
  }
  logLen=lines.length;
  if(atBottom)box.scrollTop=box.scrollHeight;
}
function logClass(l){
  if(!l)return 'blank';
  if(l.startsWith('$'))return 'cmd';
  if(/warning/i.test(l))return 'warn';
  if(/error|exception|traceback/i.test(l))return 'err';
  if(/completed|finish|saved.*\\.png/i.test(l))return 'done';
  if(/INFO/i.test(l))return 'info';
  return '';
}

// ─── pipeline stage definitions ───────────────────────────────────────────────
const STAGES=[
  {id:'s0',num:'①',name:'Flat Field',        desc:'backlight illumination map',
   imgs:[{m:'flat_illumination_preview',c:'Illumination map'},
         {m:'flat_corrected_preview',   c:'Flat-field corrected'}]},
  {id:'s1',num:'②',name:'Demosaic',           desc:'Bayer → linear RGB',
   imgs:[{m:'base_reference_roi_overlay',c:'Base ROI'},
         {m:'_05_demosaic_rgb.png',      c:'Demosaiced RGB'}]},
  {id:'s2',num:'③',name:'Base & Density',     desc:'orange mask removal → density domain',
   imgs:[{m:'_06_base_corrected.png',   c:'Base corrected'},
         {m:'_07_density.png',           c:'Optical density'},
         {m:'_08_color_unmixed',         c:'Dye unmixed'}]},
  {id:'s3',num:'④',name:'Density Inversion',  desc:'expm1 curve → inverted positive',
   imgs:[{m:'_09_inverted_positive.png',c:'Inverted positive'}]},
  {id:'s4',num:'⑤',name:'Soft Reference Map', desc:'gray anchor + empirical matrix + neutral damp',
   imgs:[{m:'_10_stage10_soft_reference_mapping.png',c:'Stage 10 output'}]},
  {id:'s5',num:'⑥',name:'Perceptual Tone',    desc:'Lab-space zoned tone curve',
   imgs:[{m:'stage2_L_before',        c:'L channel in'},
         {m:'stage2_L_after_zoned',   c:'After zoned tone'},
         {m:'stage2_L_after_rolloff', c:'After rolloff'},
         {m:'stage2_perceptual_tone_base.png',c:'Stage 2 output'}]},
  {id:'s6',num:'⑦',name:'Color Refinement',   desc:'luma-chroma + hue LUT + neutral protection',
   imgs:[{m:'stage3_C_before',        c:'Chroma before'},
         {m:'stage3_sat_scale',        c:'Sat scale'},
         {m:'stage3_hue_map',          c:'Hue map'},
         {m:'stage3_red_mask',         c:'Red mask'},
         {m:'stage3_green_mask',       c:'Green mask'},
         {m:'stage3_blue_mask',        c:'Blue mask'},
         {m:'stage3_neutral_mask',     c:'Neutral mask'},
         {m:'stage3_C_after',          c:'Chroma after'},
         {m:'stage3_pseudo_lut_color_refinement.png',c:'Stage 3 output'}]},
  {id:'s7',num:'⑧',name:'Final Finish',       desc:'display mapping + global trim + export',
   imgs:[{m:'stage4_display_base',           c:'Display base'},
         {m:'stage4_after_film_finish',      c:'Film finish'},
         {m:'stage4_after_local_refinement', c:'Local refinement'},
         {m:'stage4_after_trim',             c:'After trim'}]},
];
const FINAL_MATCH='stage4_final_finish.png';

function renderPipeline(imgs, status){
  if(!imgs) return;
  const finalName=imgs.find(n=>n.includes(FINAL_MATCH));
  const done=status==='done';

  // ── Final result card ──
  if(finalName){
    const sec=document.getElementById('final-section');
    const fi=document.getElementById('final-img');
    if(!fi.src.includes(enc(finalName)))
      fi.src=`/dev/images/${enc(finalName)}?t=${Date.now()}`;
    sec.style.display='';
    // On first completion: auto-collapse stages
    if(done && !_pipelineWasDone){
      _pipelineWasDone=true;
      _stagesVisible=false;
      document.getElementById('stages-wrap').style.display='none';
      document.getElementById('btn-toggle-stages').textContent='▼ Show stages';
    }
  }

  // ── Stage diagram ──
  const hdr=document.getElementById('stages-hdr');
  if(imgs.length>0) hdr.style.display='';
  const container=document.getElementById('pipe-stages');

  STAGES.forEach((stage,si)=>{
    // Create node if not exists
    let node=document.getElementById(stage.id);
    if(!node){
      node=document.createElement('div');
      node.id=stage.id; node.className='stage-node';
      node.innerHTML=`<div class="stage-hdr" onclick="toggleNode('${stage.id}')">
        <span class="stage-num">${stage.num}</span>
        <div style="min-width:0;flex:1">
          <div class="stage-name">${stage.name}</div>
          <div class="stage-desc">${stage.desc}</div>
        </div>
        <span class="stage-count" id="${stage.id}-ct"></span>
        <span class="stage-chev" id="${stage.id}-chev">▼</span>
      </div>
      <div class="stage-imgs" id="${stage.id}-imgs"></div>`;
      container.appendChild(node);
      // Arrow connector (not after last stage)
      if(si<STAGES.length-1){
        const arr=document.createElement('div');
        arr.className='stage-arrow'; arr.textContent='↓';
        container.appendChild(arr);
      }
    }

    // Match images for this stage (exclude linear16/npy)
    const matched=[];
    stage.imgs.forEach(def=>{
      const name=imgs.find(n=>n.includes(def.m)&&!n.includes('linear16')&&!n.includes('.npy'));
      if(name) matched.push({name,cap:def.c});
    });

    if(matched.length){
      node.classList.add('has-imgs');
      document.getElementById(`${stage.id}-ct`).textContent=`${matched.length} img`;
    }

    // Add new thumbnails (don't re-add existing)
    const imgDiv=document.getElementById(`${stage.id}-imgs`);
    matched.forEach(({name,cap})=>{
      if(document.getElementById(`si-${name}`)) return;
      const d=document.createElement('div');
      d.className='simg'; d.id=`si-${name}`;
      d.innerHTML=`<img src="/dev/images/${enc(name)}?t=${Date.now()}" alt="${cap}" onclick="openLB(this.src)" loading="lazy"><span>${cap}</span>`;
      imgDiv.appendChild(d);
    });

    // Collapse all when done (first time)
    if(done && !_pipelineWasDone){
      imgDiv.classList.add('collapsed');
      document.getElementById(`${stage.id}-chev`).textContent='▶';
    }
  });
}

function toggleNode(id){
  const imgs=document.getElementById(`${id}-imgs`);
  const chev=document.getElementById(`${id}-chev`);
  chev.textContent=imgs.classList.toggle('collapsed')?'▶':'▼';
}

function toggleStages(){
  _stagesVisible=!_stagesVisible;
  document.getElementById('stages-wrap').style.display=_stagesVisible?'':'none';
  document.getElementById('btn-toggle-stages').textContent=
    _stagesVisible?'▲ Hide stages':'▼ Show stages';
}

// ─── lightbox ─────────────────────────────────────────────────────────────────
function openLB(src){document.getElementById('lb-img').src=src;document.getElementById('lb').classList.add('open')}
function closeLB(){document.getElementById('lb').classList.remove('open')}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeLB()});

// ─── utils ────────────────────────────────────────────────────────────────────
function enc(s){return encodeURIComponent(s)}
async function post(url,body){
  return (await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
}
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    global _capture_dir, _pc_host, _still_exposure_us, _PIPELINE_SCRIPT, _processed_dir

    parser = argparse.ArgumentParser(description="ECE 445 HQ Camera Server")
    parser.add_argument("--port",         type=int, default=DEFAULT_PORT)
    parser.add_argument("--button-pin",   type=int, default=DEFAULT_BUTTON_PIN)
    parser.add_argument("--pc-host",      type=str, default=DEFAULT_PC_HOST)
    parser.add_argument("--capture-dir",  type=str, default=str(DEFAULT_CAPTURE_DIR))
    parser.add_argument(
        "--exposure-us",
        type=int,
        default=STILL_EXPOSURE_US,
        help="Still capture exposure time in microseconds (default: 50000 = 1/20 s).",
    )
    parser.add_argument(
        "--pipeline-dir",
        type=str,
        default=str(Path.home() / "ece_445" / "Post_Processing_Negative"),
        help="Path to Post_Processing_Negative directory containing run_physical_correction.py.",
    )
    args = parser.parse_args()

    _capture_dir      = Path(args.capture_dir)
    _capture_dir.mkdir(parents=True, exist_ok=True)
    _processed_dir    = _capture_dir / "processed" / "_none_"  # replaced on each run
    _pc_host          = args.pc_host.rstrip("/")
    _still_exposure_us = args.exposure_us
    pipeline_dir      = Path(args.pipeline_dir)
    _PIPELINE_SCRIPT  = pipeline_dir / "run_physical_correction.py"

    if not _PIPELINE_SCRIPT.exists():
        log.warning(
            "Pipeline script not found at %s — post-processing will be skipped. "
            "Pass --pipeline-dir to override.",
            _PIPELINE_SCRIPT,
        )

    cam_thread = threading.Thread(target=camera_worker, daemon=True)
    cam_thread.start()

    log.info("Waiting for camera…")
    _camera_ready.wait(timeout=10)

    try:
        setup_button(args.button_pin)
    except Exception as exc:
        log.warning("GPIO button not available (%s) – use web UI to capture", exc)

    log.info("Server starting on http://0.0.0.0:%d", args.port)
    log.info("Still: AnalogueGain=%.1f (~ISO %.0f)  ExposureTime=%dµs",
             STILL_ANALOGUE_GAIN, STILL_ANALOGUE_GAIN * 100, _still_exposure_us)
    log.info("Pipeline: %s", _PIPELINE_SCRIPT)

    try:
        app.run(host="0.0.0.0", port=args.port, threaded=True)
    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    main()
