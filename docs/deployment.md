# Deployment Configuration

This project runs two servers:

- PC server: FastAPI + web UI + processing pipeline, normally on port `8000`.
- Pi scanner server: motor/camera scan control, normally on port `5000`.

The active hotspot/Ethernet IPs change between deployments, so configure the
connection URLs explicitly before each field test.

## PC Server

Install:

```powershell
cd C:\ece_445
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-pc.txt
```

Configure:

```powershell
Copy-Item software\.env.example software\.env
notepad software\.env
Copy-Item Agentic_Post_Processing\.env.example Agentic_Post_Processing\.env
notepad Agentic_Post_Processing\.env
```

Required PC settings:

| Key | Meaning | Example |
| --- | --- | --- |
| `PC_SERVER_HOST` | Interface the PC server binds to | `0.0.0.0` |
| `PC_SERVER_PORT` | PC server port | `8000` |
| `PC_APP_URL` | URL the Pi can call back to | `http://10.12.194.14:8000` |
| `PI_SCANNER_URL` | Pi scanner API base URL | `http://10.12.194.1:5000` |
| `PI_CAMERA_URL` | Optional legacy Pi camera API | `http://10.12.194.1:8080` |
| `PI_SSH_HOST` | Pi SSH hostname/address for manual deploys | `rpi.local` |
| `PI_SSH_USER` | Pi SSH user | `arnav` |
| `PI_PROJECT_ROOT` | Project root on the Pi | `/home/arnav/ece-445-film-digitizer` |

Start:

```powershell
cd C:\ece_445\software
..\.venv\Scripts\python.exe main.py
```

If not using the venv command above, run:

```powershell
cd C:\ece_445\software
python main.py
```

Open:

- User UI: `http://localhost:8000/web/`
- PC dev UI: `http://localhost:8000/web/dev`

## Pi Scanner Server

Install system packages first:

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-libcamera python3-rpi.gpio
```

Install Python runtime:

```bash
cd /home/arnav/ece-445-film-digitizer/software/src
source venv/bin/activate
python -m pip install -r requirements.txt
```

Configure:

```bash
cd /home/arnav/ece-445-film-digitizer/software/src
cp .env.example .env
nano .env
```

Required Pi settings:

| Key | Meaning | Example |
| --- | --- | --- |
| `PI_SERVER_HOST` | Interface the Pi server binds to | `0.0.0.0` |
| `PI_SERVER_PORT` | Pi scanner server port | `5000` |
| `PC_APP_URL` | URL the Pi uses for tile uploads | `http://10.12.194.14:8000` |

Start:

```bash
cd /home/arnav/ece-445-film-digitizer/software/src
source venv/bin/activate
python3 main.py
```

Open:

- Pi scanner UI: `http://rpi.local:5000/web/`
- Pi dev UI: `http://rpi.local:5000/web/dev`
- Alignment UI: `http://rpi.local:5000/web/alignment.html`

## Connection Contract

The PC server sends scan commands to:

```text
{PI_SCANNER_URL}/scan/start
{PI_SCANNER_URL}/scan/status
{PI_SCANNER_URL}/scan/cancel
```

The Pi scanner uploads scan tiles back to:

```text
{PC_APP_URL}/internal/jobs/{job_id}/receive_tile
```

Backlight calibration tile scans upload to:

```text
{PC_APP_URL}/internal/calibration/backlight/receive_tile
```

Keep `PC_APP_URL` and `PI_SCANNER_URL` aligned with the current network before
running a full pipeline scan. A common failure mode is `No route to host` during
tile upload, which means the Pi cannot reach the configured `PC_APP_URL`.
