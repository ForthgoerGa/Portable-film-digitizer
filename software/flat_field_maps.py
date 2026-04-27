"""Precomputed per-tile flat-field illumination maps.

Backlight calibration now stores one DNG per scanner tile. Building the
low-frequency Bayer illumination model from each DNG is expensive, so the PC
server computes it once after a backlight scan or flat-field parameter update.
Processing code then loads the matching row/col map directly.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

import calibration_store
import flat_field_config

_SOFTWARE_DIR = Path(__file__).resolve().parent
_POST_DIR = _SOFTWARE_DIR.parent / "Post_Processing_Negative"
if str(_POST_DIR) not in sys.path:
    sys.path.insert(0, str(_POST_DIR))

from negative_physical.flat_field import FlatFieldModel, build_flat_model  # noqa: E402
from negative_physical.raw_io import load_raw_bayer  # noqa: E402

_MAP_DIR_NAME = "flat_maps"
_MANIFEST_NAME = "manifest.json"


def maps_dir() -> Path:
    path = calibration_store.BACKLIGHT.directory / _MAP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def manifest_path() -> Path:
    return maps_dir() / _MANIFEST_NAME


def _stem_for_tile(tile_dng: Path) -> str:
    return tile_dng.stem


def _map_path_for_stem(stem: str) -> Path:
    return maps_dir() / f"{stem}_illumination.npy"


def _meta_path_for_stem(stem: str) -> Path:
    return maps_dir() / f"{stem}_illumination.json"


def _config_snapshot() -> dict[str, Any]:
    cfg = flat_field_config.load()
    return {
        "strength": float(cfg["strength"]),
        "sigma_frac": float(cfg["sigma_frac"]),
        "max_side": int(cfg["max_side"]),
    }


def _source_signature(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {
        "path": str(path),
        "name": path.name,
        "size": int(st.st_size),
        "mtime": float(st.st_mtime),
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_manifest() -> dict[str, Any] | None:
    return _read_json(manifest_path()) if manifest_path().exists() else None


def clear() -> None:
    directory = maps_dir()
    for path in directory.glob("*"):
        if path.is_file():
            path.unlink()


def _build_one(tile_dng: Path, config: dict[str, Any]) -> dict[str, Any]:
    stem = _stem_for_tile(tile_dng)
    frame = load_raw_bayer(tile_dng)
    model = build_flat_model(
        frame,
        sigma_frac=float(config["sigma_frac"]),
        max_side=int(config["max_side"]),
    )
    map_path = _map_path_for_stem(stem)
    meta_path = _meta_path_for_stem(stem)

    # float32 is intentionally retained. These maps are used by the final RAW
    # path, so avoiding quantization is worth the disk cost.
    np.save(map_path, model.illumination_map.astype(np.float32))
    metadata = {
        "tile": stem,
        "source": _source_signature(tile_dng),
        "config": config,
        "cfa_pattern": model.cfa_pattern,
        "cfa_pattern_matrix": model.cfa_pattern_matrix,
        "cfa_index_matrix": model.cfa_index_matrix,
        "width": int(model.width),
        "height": int(model.height),
        "sigma_frac": float(model.sigma_frac),
        "max_side": int(model.max_side),
        "plane_stats": model.plane_stats,
        "diagnostics": model.diagnostics,
        "map_file": map_path.name,
        "built_at": time.time(),
    }
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return {
        "tile": stem,
        "map_file": map_path.name,
        "metadata_file": meta_path.name,
        "source": metadata["source"],
        "width": metadata["width"],
        "height": metadata["height"],
        "warnings": metadata["diagnostics"].get("warnings", []),
    }


def rebuild_all() -> dict[str, Any]:
    """Rebuild flat-field maps for the current backlight tile set."""

    tile_state = calibration_store.load_tile_set(calibration_store.BACKLIGHT)
    tile_dir = calibration_store.tile_set_dir(calibration_store.BACKLIGHT)
    tiles = sorted(tile_dir.glob("row_*_col_*.dng"))
    if not tiles:
        clear()
        manifest = {
            "available": False,
            "complete": False,
            "detail": "No backlight tile DNGs available.",
            "config": _config_snapshot(),
            "expected_count": tile_state.get("expected_count"),
            "map_count": 0,
            "maps": [],
            "rebuilt_at": time.time(),
        }
        manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    clear()
    config = _config_snapshot()
    built = []
    errors = []
    for tile in tiles:
        try:
            built.append(_build_one(tile, config))
        except Exception as exc:
            errors.append({"tile": tile.name, "error": str(exc)})

    expected_count = tile_state.get("expected_count")
    complete = bool(expected_count and len(built) >= int(expected_count) and not errors)
    manifest = {
        "available": bool(built),
        "complete": complete,
        "stale": False,
        "detail": None if not errors else "Some flat-field maps failed to build.",
        "config": config,
        "expected_count": expected_count,
        "source_tile_count": len(tiles),
        "map_count": len(built),
        "maps": built,
        "errors": errors,
        "rebuilt_at": time.time(),
    }
    manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return status()


def _map_record_by_tile(manifest: dict[str, Any], stem: str) -> dict[str, Any] | None:
    for item in manifest.get("maps", []):
        if item.get("tile") == stem:
            return item
    return None


def status() -> dict[str, Any]:
    tile_state = calibration_store.load_tile_set(calibration_store.BACKLIGHT)
    manifest = load_manifest()
    config = _config_snapshot()
    if manifest is None:
        return {
            "available": False,
            "complete": False,
            "stale": bool(tile_state.get("available")),
            "detail": "Flat-field maps have not been built yet.",
            "config": config,
            "expected_count": tile_state.get("expected_count"),
            "source_tile_count": tile_state.get("tile_count", 0),
            "map_count": 0,
            "maps": [],
            "errors": [],
            "rebuilt_at": None,
        }

    map_count = 0
    missing = []
    source_changed = []
    tile_dir = calibration_store.tile_set_dir(calibration_store.BACKLIGHT)
    for tile in sorted(tile_dir.glob("row_*_col_*.dng")):
        stem = _stem_for_tile(tile)
        record = _map_record_by_tile(manifest, stem)
        map_path = _map_path_for_stem(stem)
        meta_path = _meta_path_for_stem(stem)
        if map_path.exists() and meta_path.exists():
            map_count += 1
        else:
            missing.append(stem)
        if record and record.get("source") != _source_signature(tile):
            source_changed.append(stem)

    config_changed = manifest.get("config") != config
    expected_count = tile_state.get("expected_count")
    complete = bool(expected_count and map_count >= int(expected_count))
    stale = bool(config_changed or missing or source_changed)
    return {
        **manifest,
        "available": map_count > 0,
        "complete": complete and not stale and not manifest.get("errors"),
        "stale": stale,
        "config": config,
        "expected_count": expected_count,
        "source_tile_count": tile_state.get("tile_count", 0),
        "map_count": map_count,
        "missing": missing,
        "source_changed": source_changed,
        "config_changed": config_changed,
    }


def ensure_current() -> dict[str, Any]:
    current = status()
    if current.get("stale") or (current.get("source_tile_count", 0) and not current.get("available")):
        return rebuild_all()
    return current


def matching_map_paths(source_tile: Path) -> tuple[Path, Path] | None:
    stem = source_tile.stem
    map_path = _map_path_for_stem(stem)
    meta_path = _meta_path_for_stem(stem)
    if map_path.exists() and meta_path.exists():
        return map_path, meta_path
    return None


def _model_from_map(map_path: Path, meta_path: Path) -> FlatFieldModel:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    illumination = np.load(map_path).astype(np.float32)
    return FlatFieldModel(
        illumination_map=illumination,
        cfa_pattern=str(meta["cfa_pattern"]),
        cfa_pattern_matrix=tuple(tuple(str(v) for v in row) for row in meta["cfa_pattern_matrix"]),
        cfa_index_matrix=tuple(tuple(int(v) for v in row) for row in meta["cfa_index_matrix"]),
        width=int(meta["width"]),
        height=int(meta["height"]),
        sigma_frac=float(meta["sigma_frac"]),
        max_side=int(meta["max_side"]),
        plane_stats=meta.get("plane_stats", []),
        diagnostics=meta.get("diagnostics", {}),
    )


def load_model_for_tile(source_tile: Path) -> tuple[FlatFieldModel, dict[str, Any]] | None:
    current = status()
    if current.get("stale") or (current.get("source_tile_count", 0) and not current.get("available")):
        ensure_current()
    paths = matching_map_paths(source_tile)
    if paths is None:
        return None
    map_path, meta_path = paths
    return _model_from_map(map_path, meta_path), {
        "map_path": str(map_path),
        "metadata_path": str(meta_path),
        "metadata": json.loads(meta_path.read_text(encoding="utf-8")),
    }
