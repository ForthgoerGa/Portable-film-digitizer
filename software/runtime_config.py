"""Runtime deployment settings for the PC-side server.

Values come from environment variables or from ``.env`` files. Keep secrets in
``.env``; commit only ``.env.example``.
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_SOFTWARE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SOFTWARE_DIR.parent

_load_env_file(_REPO_ROOT / ".env")
_load_env_file(_SOFTWARE_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    value = _env(key)
    if not value:
        return default
    return int(value)


PC_SERVER_HOST = _env("PC_SERVER_HOST", "0.0.0.0")
PC_SERVER_PORT = _env_int("PC_SERVER_PORT", _env_int("PC_APP_PORT", 8000))

# PC_APP_URL is the URL the Pi can call back to. Set this explicitly for real
# deployments because hotspot / Ethernet adapter addresses change.
PC_APP_URL = (_env("PC_APP_URL") or _env("PC_PUBLIC_BASE_URL")).rstrip("/")
if PC_APP_URL:
    os.environ.setdefault("PC_APP_URL", PC_APP_URL)
os.environ.setdefault("PC_APP_PORT", str(PC_SERVER_PORT))

PI_SCANNER_URL = _env("PI_SCANNER_URL", "http://10.12.194.1:5000").rstrip("/")
PI_CAMERA_URL = _env("PI_CAMERA_URL", "http://10.12.194.1:8080").rstrip("/")

PI_SSH_HOST = _env("PI_SSH_HOST", "rpi.local")
PI_SSH_USER = _env("PI_SSH_USER", "arnav")
PI_PROJECT_ROOT = _env("PI_PROJECT_ROOT", "/home/arnav/ece-445-film-digitizer")
PI_SERVER_WORKDIR = _env("PI_SERVER_WORKDIR", f"{PI_PROJECT_ROOT}/software/src")
