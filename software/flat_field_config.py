"""Persistent flat-field correction parameters shared across the processing pipeline.

Defaults are tuned to be aggressive enough for typical IMX477 vignetting.
Raise ``strength`` to 1.5–2.0 if bright/dark spots persist after correction.
Lower ``sigma_frac`` to ~0.01 to capture finer spatial variations.
"""
from __future__ import annotations

import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parent / "flat_field_config.json"

DEFAULTS: dict = {
    "strength": 1.0,
    "sigma_frac": 0.0005,
    "max_side": 4096,
}

_BOUNDS: dict = {
    "strength":   (0.0,   5.0),
    "sigma_frac": (0.0005, 0.10),
    "max_side":   (64,    4096),
}


def load() -> dict:
    if _CONFIG_PATH.exists():
        try:
            stored = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            return {**DEFAULTS, **{k: stored[k] for k in DEFAULTS if k in stored}}
        except Exception:
            pass
    return dict(DEFAULTS)


def save(updates: dict) -> dict:
    validated: dict = {}
    for key, (lo, hi) in _BOUNDS.items():
        if key not in updates:
            continue
        val = float(updates[key]) if key != "max_side" else int(updates[key])
        if not (lo <= val <= hi):
            raise ValueError(f"{key} must be in [{lo}, {hi}], got {val}")
        validated[key] = val
    merged = {**load(), **validated}
    _CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged
