"""Runtime deployment settings for the Raspberry Pi scanner server."""

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


_SRC_DIR = Path(__file__).resolve().parent
_SOFTWARE_DIR = _SRC_DIR.parent
_REPO_ROOT = _SOFTWARE_DIR.parent

_load_env_file(_REPO_ROOT / ".env")
_load_env_file(_SOFTWARE_DIR / ".env")
_load_env_file(_SRC_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    value = _env(key)
    if not value:
        return default
    return int(value)


PI_SERVER_HOST = _env("PI_SERVER_HOST", "0.0.0.0")
PI_SERVER_PORT = _env_int("PI_SERVER_PORT", 5000)

# URL the Pi uses to push tile/DNG uploads back to the PC server.
PC_APP_URL = (_env("PC_APP_URL") or _env("PC_PUBLIC_BASE_URL")).rstrip("/")
if PC_APP_URL:
    os.environ.setdefault("PC_APP_URL", PC_APP_URL)
