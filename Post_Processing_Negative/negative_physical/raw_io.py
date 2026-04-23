"""RAW Bayer ingest and sensor metadata extraction.

This module intentionally keeps the image in Bayer mosaic form.  It does not
call ``rawpy.postprocess`` and therefore does not apply demosaic, white balance,
color conversion, auto-brightening, or gamma.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class RawBayerFrame:
    """A single RAW frame after black/white normalization, still in Bayer form."""

    path: str
    raw_bayer: np.ndarray
    normalized_bayer: np.ndarray
    cfa_pattern: str
    cfa_pattern_matrix: tuple[tuple[str, ...], ...]
    cfa_index_matrix: tuple[tuple[int, ...], ...]
    color_desc: str
    black_level: list[float]
    white_level: float
    width: int
    height: int
    metadata: dict[str, Any]


def load_raw_bayer(path: str | Path) -> RawBayerFrame:
    """Load a RAW/DNG file as a normalized Bayer mosaic.

    Black and white levels are read from the file metadata and applied per CFA
    plane using the camera's raw pattern.  The returned ``normalized_bayer`` is
    float32, clipped to [0, 1], and is suitable for Bayer-domain flat-field
    correction.
    """

    try:
        import rawpy  # type: ignore
    except ImportError as exc:
        raise ImportError("rawpy is required for DNG Bayer ingest.") from exc

    raw_path = Path(path)
    with rawpy.imread(str(raw_path)) as raw:
        raw_bayer = raw.raw_image_visible.copy()
        raw_pattern = np.asarray(raw.raw_pattern, dtype=np.int16)
        color_desc = _decode_color_desc(raw.color_desc)
        cfa_matrix = _pattern_to_color_matrix(raw_pattern, color_desc)
        cfa_pattern = "".join("".join(row) for row in cfa_matrix)
        black_level = _get_black_level(raw)
        white_level = float(raw.white_level)
        metadata = _extract_rawpy_metadata(raw, raw_path, raw_pattern, color_desc)

    tiff_metadata = _extract_tiff_metadata(raw_path)
    metadata.update({k: v for k, v in tiff_metadata.items() if v is not None})

    normalized = normalize_bayer(
        raw_bayer=raw_bayer,
        raw_pattern=raw_pattern,
        black_level=black_level,
        white_level=white_level,
    )

    height, width = raw_bayer.shape
    metadata.update(
        {
            "path": str(raw_path),
            "file_name": raw_path.name,
            "width": int(width),
            "height": int(height),
            "cfa_pattern": cfa_pattern,
            "cfa_pattern_matrix": [list(row) for row in cfa_matrix],
            "cfa_index_matrix": raw_pattern.astype(int).tolist(),
            "color_desc": color_desc,
            "black_level": [float(v) for v in black_level],
            "white_level": float(white_level),
        }
    )

    return RawBayerFrame(
        path=str(raw_path),
        raw_bayer=raw_bayer,
        normalized_bayer=normalized,
        cfa_pattern=cfa_pattern,
        cfa_pattern_matrix=cfa_matrix,
        cfa_index_matrix=tuple(tuple(int(v) for v in row) for row in raw_pattern.tolist()),
        color_desc=color_desc,
        black_level=[float(v) for v in black_level],
        white_level=float(white_level),
        width=int(width),
        height=int(height),
        metadata=metadata,
    )


def normalize_bayer(
    raw_bayer: np.ndarray,
    raw_pattern: np.ndarray,
    black_level: list[float] | tuple[float, ...] | np.ndarray,
    white_level: float,
) -> np.ndarray:
    """Apply per-CFA-plane black subtraction and white normalization."""

    raw_f = raw_bayer.astype(np.float32)
    pattern = np.asarray(raw_pattern, dtype=np.int16)
    black = np.asarray(black_level, dtype=np.float32)
    if black.ndim == 0:
        black = np.full(int(pattern.max()) + 1, float(black), dtype=np.float32)

    normalized = np.empty_like(raw_f, dtype=np.float32)
    tile_h, tile_w = pattern.shape
    for y in range(tile_h):
        for x in range(tile_w):
            plane_index = int(pattern[y, x])
            plane_black = float(black[plane_index])
            denom = max(float(white_level) - plane_black, 1.0)
            normalized[y::tile_h, x::tile_w] = (
                raw_f[y::tile_h, x::tile_w] - plane_black
            ) / denom

    return np.clip(normalized, 0.0, 1.0).astype(np.float32)


def _decode_color_desc(color_desc: bytes | str) -> str:
    if isinstance(color_desc, bytes):
        return color_desc.decode("ascii", errors="replace")
    return str(color_desc)


def _pattern_to_color_matrix(
    raw_pattern: np.ndarray,
    color_desc: str,
) -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = []
    for row in raw_pattern:
        rows.append(tuple(color_desc[int(index)] for index in row))
    return tuple(rows)


def _get_black_level(raw: Any) -> list[float]:
    try:
        levels = list(raw.black_level_per_channel)
    except AttributeError:
        try:
            levels = [float(raw.black_level)]
        except AttributeError:
            levels = [0.0]
    return [float(v) for v in levels]


def _extract_rawpy_metadata(
    raw: Any,
    path: Path,
    raw_pattern: np.ndarray,
    color_desc: str,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "rawpy_source": "rawpy",
        "raw_pattern": raw_pattern.astype(int).tolist(),
        "color_desc": color_desc,
    }

    for attr in ("camera_whitebalance", "daylight_whitebalance"):
        try:
            value = getattr(raw, attr)
            metadata[attr] = [float(v) for v in value]
        except Exception:
            metadata[attr] = None

    try:
        metadata["rgb_xyz_matrix"] = np.asarray(raw.rgb_xyz_matrix).astype(float).tolist()
    except Exception:
        metadata["rgb_xyz_matrix"] = None

    try:
        sizes = raw.sizes
        metadata["sizes"] = {
            key: int(getattr(sizes, key))
            for key in (
                "raw_height",
                "raw_width",
                "height",
                "width",
                "top_margin",
                "left_margin",
                "iheight",
                "iwidth",
                "pixel_aspect",
                "flip",
            )
            if hasattr(sizes, key)
        }
    except Exception:
        metadata["sizes"] = None

    metadata["path"] = str(path)
    return metadata


def _extract_tiff_metadata(path: Path) -> dict[str, Any]:
    """Read DNG/TIFF tags used for reproducibility and later color work."""

    try:
        import tifffile  # type: ignore
    except ImportError:
        return {"tiff_metadata_available": False}

    wanted = {
        "Make": "make",
        "Model": "model",
        "ISOSpeedRatings": "iso",
        "PhotographicSensitivity": "photographic_sensitivity",
        "ExposureTime": "exposure_time",
        "BlackLevel": "tiff_black_level",
        "WhiteLevel": "tiff_white_level",
        "CFARepeatPatternDim": "tiff_cfa_repeat_pattern_dim",
        "CFAPattern": "tiff_cfa_pattern",
        "CFAPlaneColor": "tiff_cfa_plane_color",
        "ColorMatrix1": "color_matrix_1",
        "ColorMatrix2": "color_matrix_2",
        "AsShotNeutral": "as_shot_neutral",
    }

    metadata: dict[str, Any] = {"tiff_metadata_available": True}
    try:
        with tifffile.TiffFile(str(path)) as tif:
            tags = tif.pages[0].tags
            for tag_name, out_name in wanted.items():
                value = tags[tag_name].value if tag_name in tags else None
                metadata[out_name] = _normalise_tiff_tag(tag_name, value)
    except Exception as exc:
        metadata["tiff_metadata_available"] = False
        metadata["tiff_metadata_error"] = str(exc)
    return metadata


def _normalise_tiff_tag(tag_name: str, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bytes):
        return list(value)
    if tag_name in {"ExposureTime"}:
        return _rational_to_float(value)
    if tag_name in {"ColorMatrix1", "ColorMatrix2", "AsShotNeutral"}:
        return _rational_sequence_to_float_list(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, tuple):
        return [_normalise_scalar(v) for v in value]
    return _normalise_scalar(value)


def _rational_sequence_to_float_list(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        return [float(value)]
    if len(value) % 2 == 0 and all(isinstance(v, (int, np.integer)) for v in value):
        return [
            float(value[i]) / float(value[i + 1])
            for i in range(0, len(value), 2)
            if float(value[i + 1]) != 0.0
        ]
    return [float(v) for v in value]


def _rational_to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, tuple) and len(value) == 2:
        denom = float(value[1])
        return float(value[0]) / denom if denom else None
    return float(value)


def _normalise_scalar(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    return value

