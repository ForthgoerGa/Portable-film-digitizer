"""Persistent calibration artifact store for the PC app server.

Tracks the backlight and base-frame DNG captures used by the negative-film RAW
branch. The current physical correction entry point expects both frames plus a
reference layout when running in RAW mode.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_WEB_DIR = Path(__file__).resolve().parent / "web"
_CALIBRATION_ROOT = _WEB_DIR / "calibration"

_BACKLIGHT_DIR = _CALIBRATION_ROOT / "backlight"
_BASE_FRAME_DIR = _CALIBRATION_ROOT / "base_frame"

_BACKLIGHT_DNG_NAME = "backlight_frame.dng"
_BASE_FRAME_DNG_NAME = "base_frame.dng"
_LATEST_NAME = "latest.json"

BACKLIGHT_ARTIFACT_URL = "/api/dev/calibration/backlight/artifact"
BASE_FRAME_ARTIFACT_URL = "/api/dev/calibration/base_frame/artifact"


@dataclass(frozen=True)
class CalibrationKind:
    name: str
    directory: Path
    dng_name: str
    artifact_url: str


BACKLIGHT = CalibrationKind("backlight", _BACKLIGHT_DIR, _BACKLIGHT_DNG_NAME, BACKLIGHT_ARTIFACT_URL)
BASE_FRAME = CalibrationKind("base_frame", _BASE_FRAME_DIR, _BASE_FRAME_DNG_NAME, BASE_FRAME_ARTIFACT_URL)


def _ensure_dir(kind: CalibrationKind) -> Path:
    kind.directory.mkdir(parents=True, exist_ok=True)
    return kind.directory


def _latest_path(kind: CalibrationKind) -> Path:
    return _ensure_dir(kind) / _LATEST_NAME


def dng_path(kind: CalibrationKind) -> Path:
    return _ensure_dir(kind) / kind.dng_name


def tile_set_dir(kind: CalibrationKind) -> Path:
    path = _ensure_dir(kind) / "tiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_dng_path_if_ready(kind: CalibrationKind) -> Path | None:
    path = dng_path(kind)
    return path if path.exists() and path.stat().st_size > 0 else None


def load_latest(kind: CalibrationKind) -> dict:
    path = _latest_path(kind)
    if not path.exists():
        return _empty_state(kind)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "kind": kind.name,
            "configured": False,
            "detail": f"Calibration record unreadable: {exc}",
            "dng_available": False,
            "dng_url": None,
        }
    payload["kind"] = kind.name
    payload.setdefault("configured", True)
    dng = get_dng_path_if_ready(kind)
    payload["dng_available"] = dng is not None
    payload["dng_url"] = kind.artifact_url if dng is not None else None
    if dng is not None:
        payload["dng_filename"] = dng.name
        payload["dng_size"] = dng.stat().st_size
        payload["dng_signature"] = dng_signature(dng)
    tile_state = load_tile_set(kind)
    payload["tile_set_available"] = tile_state["available"]
    payload["tile_count"] = tile_state["tile_count"]
    payload["tile_set"] = tile_state
    return payload


def _empty_state(kind: CalibrationKind) -> dict:
    return {
        "kind": kind.name,
        "configured": False,
        "detail": f"No {kind.name.replace('_', ' ')} capture available yet.",
        "dng_available": False,
        "dng_url": None,
        "tile_set_available": False,
        "tile_count": 0,
    }


def record_pi_capture(kind: CalibrationKind, pi_response: dict) -> dict:
    """Record Pi-side capture metadata without a locally stored DNG."""
    record = {
        "configured": True,
        "captured_at": time.time(),
        "source": "pi_run_pipeline",
        "pi_response": pi_response,
    }
    _latest_path(kind).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return load_latest(kind)


def store_dng_upload(kind: CalibrationKind, data: bytes, original_filename: str | None) -> dict:
    """Persist a DNG uploaded directly to the PC and mark calibration ready."""
    if not data:
        raise ValueError("Uploaded calibration DNG is empty")
    target = dng_path(kind)
    target.write_bytes(data)
    record = {
        "configured": True,
        "captured_at": time.time(),
        "source": "pc_upload",
        "original_filename": original_filename,
    }
    _latest_path(kind).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return load_latest(kind)


def store_dng_file(
    kind: CalibrationKind,
    source_path: Path,
    original_filename: str | None,
    source: str,
    extra: dict[str, Any] | None = None,
) -> dict:
    """Persist a DNG already staged on disk and mark calibration ready."""
    if not source_path.exists() or source_path.stat().st_size == 0:
        raise ValueError("Calibration DNG is empty or missing")
    target = dng_path(kind)
    shutil.copy2(source_path, target)
    record = {
        "configured": True,
        "captured_at": time.time(),
        "source": source,
        "original_filename": original_filename,
    }
    if extra:
        record.update(extra)
    _latest_path(kind).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return load_latest(kind)


def clear_tile_set(kind: CalibrationKind) -> None:
    path = tile_set_dir(kind)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def store_tile_files(
    kind: CalibrationKind,
    *,
    row: int,
    col: int,
    total_rows: int,
    total_cols: int,
    raw_tmp: Path,
    preview_tmp: Path | None,
    source: str,
    extra: dict[str, Any] | None = None,
) -> dict:
    """Persist one calibration tile without synthesizing a stitched DNG."""
    if not raw_tmp.exists() or raw_tmp.stat().st_size == 0:
        raise ValueError("Calibration tile DNG is empty or missing")

    directory = tile_set_dir(kind)
    stem = f"row_{int(row)}_col_{int(col)}"
    raw_dest = directory / f"{stem}.dng"
    preview_dest = directory / f"{stem}.jpg"
    shutil.move(str(raw_tmp), raw_dest)
    if preview_tmp and preview_tmp.exists() and preview_tmp.stat().st_size > 0:
        shutil.move(str(preview_tmp), preview_dest)

    record = {
        "configured": True,
        "captured_at": time.time(),
        "source": source,
        "capture_mode": "per_tile_raw_set",
        "expected_grid": {"rows": int(total_rows), "cols": int(total_cols)},
    }
    if extra:
        record.update(extra)
    _latest_path(kind).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return load_latest(kind)


def load_tile_set(kind: CalibrationKind) -> dict[str, Any]:
    directory = tile_set_dir(kind)
    raw_tiles = sorted(directory.glob("row_*_col_*.dng"))
    preview_tiles = sorted(directory.glob("row_*_col_*.jpg"))
    tiles = []
    for raw in raw_tiles:
        tiles.append(
            {
                "raw": raw.name,
                "raw_size": raw.stat().st_size,
                "preview": f"{raw.stem}.jpg" if (directory / f"{raw.stem}.jpg").exists() else None,
            }
        )
    latest = _latest_path(kind)
    expected = None
    if latest.exists():
        try:
            expected = json.loads(latest.read_text(encoding="utf-8")).get("expected_grid")
        except Exception:
            expected = None
    expected_count = None
    if expected:
        expected_count = int(expected.get("rows", 0)) * int(expected.get("cols", 0))
    return {
        "available": bool(raw_tiles),
        "directory": str(directory),
        "tile_count": len(raw_tiles),
        "preview_count": len(preview_tiles),
        "expected_count": expected_count,
        "complete": bool(expected_count and len(raw_tiles) >= expected_count),
        "tiles": tiles,
    }


def matching_tile_dng(kind: CalibrationKind, source_tile: Path) -> Path | None:
    """Return the calibration tile with the same row/col stem as a scan tile."""
    candidate = tile_set_dir(kind) / f"{source_tile.stem}.dng"
    return candidate if candidate.exists() and candidate.stat().st_size > 0 else None


def representative_tile_dng(kind: CalibrationKind) -> Path | None:
    """Return one tile DNG for readiness/signature checks."""
    tiles = sorted(tile_set_dir(kind).glob("row_*_col_*.dng"))
    return tiles[0] if tiles else None


def clear(kind: CalibrationKind) -> dict:
    target = dng_path(kind)
    if target.exists():
        target.unlink()
    tiles = tile_set_dir(kind)
    if tiles.exists():
        shutil.rmtree(tiles)
    latest = _latest_path(kind)
    if latest.exists():
        latest.unlink()
    return _empty_state(kind)


def dng_signature(path: Path) -> dict[str, Any]:
    """Return the raw-frame compatibility signature used by the RAW branch."""
    try:
        import rawpy  # type: ignore
        import numpy as np
    except Exception as exc:
        return {"available": False, "error": f"rawpy unavailable: {exc}"}

    try:
        with rawpy.imread(str(path)) as raw:
            height, width = raw.raw_image_visible.shape
            pattern = np.asarray(raw.raw_pattern, dtype=int).tolist()
            color_desc = raw.color_desc.decode("ascii", errors="replace")
            black = [float(v) for v in raw.black_level_per_channel]
            white = float(raw.white_level)
    except Exception as exc:
        return {"available": False, "path": str(path), "error": str(exc)}

    return {
        "available": True,
        "path": str(path),
        "width": int(width),
        "height": int(height),
        "raw_pattern": pattern,
        "color_desc": color_desc,
        "black_level": black,
        "white_level": white,
    }


def signatures_compatible(*signatures: dict[str, Any]) -> tuple[bool, str | None]:
    valid = [sig for sig in signatures if sig and sig.get("available")]
    if len(valid) != len(signatures):
        return False, "DNG signature unavailable"
    first = valid[0]
    fields = ("width", "height", "raw_pattern", "color_desc")
    for sig in valid[1:]:
        for field in fields:
            if sig.get(field) != first.get(field):
                return (
                    False,
                    "DNG calibration dimensions/CFA do not match: "
                    f"{first.get('width')}x{first.get('height')} {first.get('color_desc')} "
                    f"vs {sig.get('width')}x{sig.get('height')} {sig.get('color_desc')}",
                )
    return True, None


def calibration_pair_compatibility() -> dict[str, Any]:
    backlight_tile = representative_tile_dng(BACKLIGHT)
    backlight = backlight_tile or get_dng_path_if_ready(BACKLIGHT)
    base_frame = get_dng_path_if_ready(BASE_FRAME)
    missing = []
    if backlight is None:
        missing.append("backlight")
    if base_frame is None:
        missing.append("base_frame")
    if missing:
        return {
            "ok": False,
            "reason": "missing_calibration_dng",
            "missing_calibrations": missing,
            "backlight_signature": None,
            "base_frame_signature": None,
        }

    backlight_sig = dng_signature(backlight)
    base_frame_sig = dng_signature(base_frame)
    roi_path = dng_path(BASE_FRAME).parent / "reference_layout.json"
    roi_available = roi_path.exists()
    ok, reason = signatures_compatible(backlight_sig, base_frame_sig)
    if ok and not roi_available:
        ok = False
        reason = "missing_reference_layout"
    return {
        "ok": ok,
        "reason": reason,
        "missing_calibrations": [],
        "backlight_signature": backlight_sig,
        "base_frame_signature": base_frame_sig,
        "backlight_usage": "per_tile_backlight_set" if backlight_tile else "single_dng",
        "base_frame_usage": "reference_layout_required",
        "base_frame_roi_available": roi_available,
        "base_frame_roi_path": str(roi_path),
        "base_detection": "reference_layout",
    }


def negative_branch_ready() -> bool:
    return bool(calibration_pair_compatibility()["ok"])


def summary() -> dict:
    negative_branch = calibration_pair_compatibility()
    return {
        "backlight": load_latest(BACKLIGHT),
        "base_frame": load_latest(BASE_FRAME),
        "negative_branch_ready": bool(negative_branch["ok"]),
        "negative_branch": negative_branch,
    }
