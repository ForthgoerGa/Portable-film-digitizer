"""raw_ingest.py — Load RAW files into linear float32 RGB RawFrame objects.

Uses rawpy for IMX477 / DNG-compatible RAW files.  All demosaicing is done in
linear (gamma=1) mode with auto-brightness disabled so subsequent stages
operate on physically proportional values.

rawpy must be installed:
    pip install rawpy
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from .models import RawFrame

log = logging.getLogger(__name__)


def load_raw_frame(path: str) -> RawFrame:
    """Load a RAW file and return a RawFrame with linear float32 RGB data.

    Parameters
    ----------
    path:
        Filesystem path to the RAW file (DNG, ARW, RAF, etc.).

    Returns
    -------
    RawFrame
        .linear_rgb is float32 H x W x 3, RGB order, values in [0, 1]
        (after black-level subtraction and white-level normalisation via
        sensor_normalization.prepare_linear_rgb).
    """
    try:
        import rawpy  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "rawpy is required for RAW ingest. Install it with:  pip install rawpy"
        ) from exc

    path = str(path)
    log.info("Loading RAW frame: %s", path)

    with rawpy.imread(path) as raw:
        # --- Extract metadata before postprocess (it modifies internal state) ---
        metadata = _extract_metadata(raw)
        black_level, white_level, wb_multipliers, sensor_name = (
            metadata.pop("_black_level"),
            metadata.pop("_white_level"),
            metadata.pop("_wb_multipliers"),
            metadata.pop("_sensor_name"),
        )

        # --- Demosaic in linear mode -----------------------------------------
        # gamma=(1, 1) → linear, no_auto_bright=True → preserve absolute levels,
        # output_bps=16 → full bit depth, use_camera_wb=False → neutral WB so
        # sensor_normalization can apply calibrated multipliers later.
        rgb_u16: np.ndarray = raw.postprocess(
            gamma=(1, 1),
            no_auto_bright=True,
            output_bps=16,
            use_camera_wb=False,
            use_auto_wb=False,
            output_color=rawpy.ColorSpace.sRGB,
        )

    # rgb_u16 is uint16 H x W x 3, RGB order
    linear_rgb = rgb_u16.astype(np.float32) / 65535.0

    h, w = linear_rgb.shape[:2]
    log.info("Loaded %s: %d x %d, dtype=%s", Path(path).name, w, h, rgb_u16.dtype)

    return RawFrame(
        path=path,
        linear_rgb=linear_rgb,
        metadata=metadata,
        black_level=black_level,
        white_level=white_level,
        wb_multipliers=wb_multipliers,
        sensor_name=sensor_name,
        width=w,
        height=h,
    )


def load_image_as_raw_frame(path: str) -> RawFrame:
    """Fallback loader for JPEG/PNG files (testing / non-RAW paths).

    Returns a RawFrame where linear_rgb is uint8-derived float32 with no true
    RAW metadata.  black_level and white_level are set to None so
    sensor_normalization will skip calibration steps.
    """
    import cv2  # type: ignore

    img_bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img_bgr is None:
        raise FileNotFoundError(f"Could not read image: {path}")

    # Convert BGR uint8 → RGB float32 in [0, 1]
    linear_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    h, w = linear_rgb.shape[:2]
    log.warning(
        "Loaded %s as non-RAW fallback (no sensor metadata available).",
        Path(path).name,
    )
    return RawFrame(
        path=str(path),
        linear_rgb=linear_rgb,
        metadata={},
        black_level=None,
        white_level=None,
        wb_multipliers=None,
        sensor_name=None,
        width=w,
        height=h,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_metadata(raw: Any) -> dict[str, Any]:
    """Pull calibration fields out of a rawpy RawPy object.

    Returns a flat dict.  Keys prefixed with '_' are consumed by load_raw_frame
    and not stored in RawFrame.metadata.
    """
    meta: dict[str, Any] = {}

    # Black level — may be scalar or per-channel array
    try:
        bl = raw.black_level_per_channel  # shape (4,) for RGGB Bayer
        # Collapse RGGB → RGB by averaging the two G channels
        meta["_black_level"] = np.array([bl[0], (bl[1] + bl[2]) / 2, bl[3]], dtype=np.float32)
    except AttributeError:
        try:
            meta["_black_level"] = float(raw.black_level)
        except AttributeError:
            meta["_black_level"] = None

    # White / saturation level
    try:
        meta["_white_level"] = float(raw.white_level)
    except AttributeError:
        meta["_white_level"] = None

    # Camera white balance multipliers (RGGB order → take R, G_mean, B)
    try:
        cwb = raw.camera_whitebalance  # list of 4 floats
        if cwb and len(cwb) >= 3:
            g_avg = (cwb[1] + cwb[2]) / 2.0 if len(cwb) >= 4 else cwb[1]
            meta["_wb_multipliers"] = (float(cwb[0]), float(g_avg), float(cwb[3] if len(cwb) > 3 else cwb[2]))
        else:
            meta["_wb_multipliers"] = None
    except (AttributeError, TypeError):
        meta["_wb_multipliers"] = None

    # Sensor / camera name
    try:
        meta["_sensor_name"] = raw.camera_model
    except AttributeError:
        meta["_sensor_name"] = None

    # Daylight WB for reference
    try:
        meta["daylight_whitebalance"] = list(raw.daylight_whitebalance)
    except AttributeError:
        pass

    return meta
