"""Unified scan job orchestrator - PC App Server side."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np

import calibration_store
import flat_field_config
import flat_field_maps

try:
    import requests as _req

    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

_TRANSFER_TIMEOUT_S = 180.0
_WAIT_SLICE_S = 0.25
_JPEG_QUALITY = 92
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DETECTION_PREVIEW_MATRIX_E0 = np.array(
    [
        [0.84, 0.06, 0.02],
        [0.06, 1.08, 0.04],
        [0.02, 0.04, 1.18],
    ],
    dtype=np.float32,
)
_DETECTION_PREVIEW_COLOR_MODE = "flat_field_then_camera_space_white_norm_then_e0_matrix"
_XYZ_TO_SRGB = np.array(
    [
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ],
    dtype=np.float32,
)

_STAGE_RANK: dict[str, int] = {
    "dispatch": 0,
    "scan": 1,
    "stitch": 2,
    "transfer": 3,
    "classify": 4,
    "process": 5,
    "complete": 6,
}

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _new_job(job_id: str, job_kind: str) -> dict:
    now = time.time()
    is_bypass = job_kind == "process_capture"
    return {
        "job_id": job_id,
        "job_kind": job_kind,
        "state": "running",
        "stage": "classify" if is_bypass else "dispatch",
        "progress_pct": 78 if is_bypass else 0,
        "started_at": now,
        "updated_at": now,
        "error": None,
        "scan": {"current_row": 0, "current_col": 0, "rows": 0, "cols": 0},
        "tile_upload": {
            "mode": None,
            "received": 0,
            "expected": None,
            "complete": False,
        },
        "processing": {
            "classification": None,
            "classifier_raw_label": None,
            "classifier_mode": None,
            "classifier_confidence": None,
            "selected_branch": None,
            "runner_used": None,
            "current_iteration": None,
            "max_iterations": None,
            "score": None,
            "calibration_skip_reason": None,
            "missing_calibrations": None,
        },
        "artifacts": {
            "stitched_raw_dng_url": None,
            "raw_preview_url": None,
            "final_preview_url": None,
            "final_download_url": None,
            "metadata_url": None,
            "backlight_reference_url": None,
            "tile_set_url": None,
        },
        # Internal fields stripped from the public job model.
        "_cancel": False,
        "_source_path": None,
        "_stitched_upload_event": threading.Event(),
        "_tile_arrival_order": [],
        "_tile_processing_started": False,
        "_tile_processing_done_event": threading.Event(),
    }


class JobOrchestrator:
    def __init__(
        self,
        artifacts_dir: Path,
        pi_scanner_url: str = "http://10.12.194.1:5000",
        pc_app_url: str | None = None,
    ):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.artifacts_dir = artifacts_dir
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._pi_url = pi_scanner_url.rstrip("/")
        self._configured_pc_app_url = (pc_app_url or os.getenv("PC_APP_URL") or "").rstrip("/")
        self._pc_app_url = self._configured_pc_app_url or self._detect_pc_app_url()

    # Public API -----------------------------------------------------------------

    def create_job(self, job_kind: str = "one_click_scan", source_path: Path | None = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = _new_job(job_id, job_kind)
        job["_source_path"] = str(source_path) if source_path else None
        if calibration_store.get_dng_path_if_ready(calibration_store.BACKLIGHT) is not None:
            job["artifacts"]["backlight_reference_url"] = calibration_store.BACKLIGHT_ARTIFACT_URL
        (self.artifacts_dir / job_id).mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._jobs[job_id] = job
        threading.Thread(target=self._run, args=(job_id,), daemon=True).start()
        return job_id

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._public_job(job) if job else None

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job["state"] != "running":
                return False
            job["_cancel"] = True
        return True

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [self._public_job(job) for job in self._jobs.values()]

    def get_artifact_path(self, job_id: str, artifact_type: str) -> Path | None:
        job_dir = self.artifacts_dir / job_id
        if artifact_type == "raw":
            path = job_dir / "raw_preview.jpg"
            return path if path.exists() else None
        if artifact_type == "final":
            for name in ("final.png", "final.jpg", "final.jpeg"):
                path = job_dir / name
                if path.exists():
                    return path
            return None
        if artifact_type == "metadata":
            path = job_dir / "metadata.json"
            return path if path.exists() else None
        if artifact_type == "stitched_raw_dng":
            path = job_dir / "stitched_raw.dng"
            return path if path.exists() else None
        return None

    def receive_stitched_raw(self, job_id: str, data: bytes) -> tuple[bool, str]:
        """Persist canonical stitched raw and release any waiting transfer stage."""
        job_dir, reason = self._validate_stitched_raw_receive(job_id)
        if job_dir is None:
            return False, reason

        tmp_path = job_dir / "stitched_raw.uploading"
        tmp_path.write_bytes(data)
        return self.receive_stitched_raw_file(job_id, tmp_path)

    def receive_stitched_raw_file(self, job_id: str, tmp_path: Path) -> tuple[bool, str]:
        """Accept a staged upload file without loading the DNG into memory."""
        job_dir, reason = self._validate_stitched_raw_receive(job_id)
        if job_dir is None:
            return False, reason

        final_path = job_dir / "stitched_raw.dng"
        preview_path = job_dir / "raw_preview.jpg"
        tmp_path.replace(final_path)
        if preview_path.exists():
            preview_path.unlink()
        self._up(
            job_id,
            stage="transfer",
            progress_pct=72,
            artifacts={
                "stitched_raw_dng_url": f"/api/jobs/{job_id}/artifacts/stitched_raw_dng",
                "raw_preview_url": None,
            },
        )
        event = self._get_upload_event(job_id)
        if event:
            event.set()
        return True, "accepted"

    def receive_scan_tile_files(
        self,
        job_id: str,
        *,
        row: int,
        col: int,
        total_rows: int,
        total_cols: int,
        raw_tmp: Path,
        preview_tmp: Path | None,
    ) -> tuple[bool, str]:
        """Accept one Pi tile DNG/JPEG pair for preview-free RAW processing."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False, "not_found"
            if job.get("job_kind") != "one_click_scan":
                return False, "receive_scan_tile only applies to one_click_scan jobs"
            if job.get("state") != "running":
                return False, "Job is not running"

        if not raw_tmp.exists() or raw_tmp.stat().st_size == 0:
            return False, "raw tile upload is empty"

        job_dir = self._job_dir(job_id)
        tiles_dir = job_dir / "tiles"
        tiles_dir.mkdir(parents=True, exist_ok=True)
        stem = f"row_{int(row)}_col_{int(col)}"
        raw_dest = tiles_dir / f"{stem}.dng"
        preview_dest = tiles_dir / f"{stem}.jpg"
        raw_tmp.replace(raw_dest)
        if preview_tmp and preview_tmp.exists() and preview_tmp.stat().st_size > 0:
            preview_tmp.replace(preview_dest)
            raw_preview = job_dir / "raw_preview.jpg"
            if not raw_preview.exists():
                shutil.copy2(preview_dest, raw_preview)

        received = len(list(tiles_dir.glob("row_*_col_*.dng")))
        expected = int(total_rows) * int(total_cols) if total_rows and total_cols else None
        artifacts = {"tile_set_url": f"/api/jobs/{job_id}/tiles"}
        if (job_dir / "raw_preview.jpg").exists():
            artifacts["raw_preview_url"] = f"/api/jobs/{job_id}/artifacts/raw"
        self._up(
            job_id,
            artifacts=artifacts,
            tile_upload={
                "mode": "per_tile_raw",
                "received": received,
                "expected": expected,
                "complete": bool(expected and received >= expected),
            },
        )
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                key = (int(row), int(col))
                if key not in job["_tile_arrival_order"]:
                    job["_tile_arrival_order"].append(key)
        self._maybe_start_streaming_tile_processing(job_id)
        return True, "accepted"

    def _validate_stitched_raw_receive(self, job_id: str) -> tuple[Path | None, str]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None, "not_found"
            if job.get("job_kind") != "one_click_scan":
                return None, "receive_stitched_raw only applies to one_click_scan jobs"
            if job.get("state") != "running":
                return None, "Job is not running"
            if job.get("stage") not in {"scan", "stitch", "transfer"}:
                return None, f"Job is not ready for stitched raw upload (stage={job.get('stage')})"

        job_dir = self.artifacts_dir / job_id
        if not job_dir.exists():
            return None, "not_found"
        return job_dir, "ok"

    # Internal helpers -----------------------------------------------------------

    def _public_job(self, job: dict) -> dict:
        public: dict = {}
        for key, value in job.items():
            if key.startswith("_"):
                continue
            public[key] = dict(value) if isinstance(value, dict) else value
        return public

    def _up(self, job_id: str, **updates):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            new_state = updates.get("state", job.get("state", "running"))
            is_terminal = new_state in ("cancelled", "failed")
            for key, value in updates.items():
                if key == "progress_pct" and not is_terminal:
                    if value > (job.get("progress_pct") or 0):
                        job[key] = value
                elif key == "stage" and not is_terminal:
                    cur_rank = _STAGE_RANK.get(job.get("stage", "dispatch"), 0)
                    new_rank = _STAGE_RANK.get(value, 0)
                    if new_rank >= cur_rank:
                        job[key] = value
                elif key in ("scan", "processing", "artifacts", "tile_upload") and isinstance(value, dict):
                    job[key].update(value)
                else:
                    job[key] = value
            job["updated_at"] = time.time()

    def _cancelled(self, job_id: str) -> bool:
        with self._lock:
            return bool(self._jobs.get(job_id, {}).get("_cancel"))

    def _src(self, job_id: str) -> Path | None:
        with self._lock:
            src = self._jobs.get(job_id, {}).get("_source_path")
        return Path(src) if src else None

    def _job_kind(self, job_id: str) -> str | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.get("job_kind") if job else None

    def _get_upload_event(self, job_id: str) -> threading.Event | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return job["_stitched_upload_event"]

    def _get_tile_processing_event(self, job_id: str) -> threading.Event | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return job["_tile_processing_done_event"]

    def _fail(self, job_id: str, message: str):
        self._up(job_id, state="failed", stage="failed", error=message)

    def _job_dir(self, job_id: str) -> Path:
        return self.artifacts_dir / job_id

    def _set_raw_preview(self, job_id: str):
        self._up(
            job_id,
            artifacts={"raw_preview_url": f"/api/jobs/{job_id}/artifacts/raw"},
        )

    def _wait_for_stitched_upload(self, job_id: str, timeout_s: float = _TRANSFER_TIMEOUT_S) -> bool:
        event = self._get_upload_event(job_id)
        if event is None:
            self._fail(job_id, "Job not found while waiting for stitched raw upload")
            return False

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._cancelled(job_id):
                self._up(job_id, state="cancelled", stage="cancelled", progress_pct=0)
                return False
            if event.wait(_WAIT_SLICE_S):
                canonical = self._job_dir(job_id) / "stitched_raw.dng"
                if canonical.exists():
                    return True
            self._up(job_id, stage="transfer", progress_pct=72)

        self._fail(job_id, "Timed out waiting for stitched raw upload from Pi")
        return False

    def _save_jpeg_preview(self, image_rgb: np.ndarray, out_path: Path):
        preview_rgb = np.clip(image_rgb, 0.0, 1.0)
        image_u8 = np.clip(preview_rgb * 255.0, 0, 255).astype(np.uint8)
        ok = cv2.imwrite(
            str(out_path),
            cv2.cvtColor(image_u8, cv2.COLOR_RGB2BGR),
            [int(cv2.IMWRITE_JPEG_QUALITY), _JPEG_QUALITY],
        )
        if not ok:
            raise RuntimeError(f"Failed to write raw preview: {out_path}")

    def _copy_preview_from_local_capture(self, source: Path, preview_path: Path):
        raster = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if raster is None:
            raise RuntimeError(f"Could not read local capture: {source}")
        rgb = cv2.cvtColor(raster, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        self._save_jpeg_preview(rgb, preview_path)

    def _fetch_pi_stitched_preview(self, preview_path: Path) -> bool:
        """Fetch the Pi-side stitched JPG preview when it is available.

        Phase 1 processing is preview-raster based while the stitched DNG is
        kept as the canonical artifact. The Pi preview is generated from the
        same scan tiles with the camera ISP color path, so it is currently a
        more truthful browser preview than ad-hoc PC color conversion of the
        stitched Bayer DNG.
        """
        if not _HAS_REQUESTS:
            return False

        tmp_path = preview_path.with_suffix(preview_path.suffix + ".fetching")
        try:
            with _req.get(
                f"{self._pi_url}/captures/file",
                params={"path": "stitched_preview.jpg"},
                stream=True,
                timeout=(3, 30),
            ) as resp:
                resp.raise_for_status()
                with tmp_path.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            fh.write(chunk)

            if cv2.imread(str(tmp_path), cv2.IMREAD_COLOR) is None:
                tmp_path.unlink(missing_ok=True)
                return False
            tmp_path.replace(preview_path)
            return True
        except Exception:
            tmp_path.unlink(missing_ok=True)
            return False

    def _derive_preview_from_dng(self, canonical_path: Path, preview_path: Path):
        """Create a fast display RGB preview for classification/detection only."""

        try:
            import rawpy  # type: ignore
        except ImportError as exc:
            raise RuntimeError("rawpy is required for fast DNG preview rendering") from exc

        rgb, color_matrix = self._render_fast_raw_rgb(canonical_path, rawpy)
        rgb = self._apply_fast_preview_flat_field(canonical_path, rgb, color_matrix, rawpy)
        if np.isfinite(color_matrix).all() and np.abs(color_matrix).sum() > 1e-6:
            rgb = np.tensordot(rgb, color_matrix.T, axes=([2], [0])).astype(np.float32)
        rgb = np.clip(rgb, 0.0, None)

        # The DNGs currently do not carry useful camera WB, and the scanner
        # light is strongly green. Apply a display-only high-percentile balance
        # after the DNG color matrix so classifier/detector input has visible
        # structure. This preview is not used by the physical RAW pipeline.
        flat = rgb.reshape(-1, 3)
        luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
        # Prefer the white backlight area: bright, unsaturated, smooth, and
        # outside the dark film content. This gives a better preview white
        # point than using the whole frame when the first tile sees backlight.
        finite_luma = luma[np.isfinite(luma)]
        bright_lo, bright_hi = np.percentile(finite_luma, [78.0, 99.3])
        luma_u8 = np.round(np.clip(luma / max(float(np.percentile(finite_luma, 99.5)), 1e-6), 0, 1) * 255).astype(np.uint8)
        texture = cv2.absdiff(luma_u8, cv2.blur(luma_u8, (31, 31)))
        smooth_limit = float(np.percentile(texture, 45.0))
        backlight_mask = (
            (luma >= bright_lo)
            & (luma <= bright_hi)
            & (texture <= smooth_limit)
            & np.all(rgb > 1e-5, axis=2)
        )
        if int(backlight_mask.sum()) > 512:
            balance_pixels = rgb[backlight_mask]
        else:
            lo_luma, hi_luma = np.percentile(luma, [65.0, 98.0])
            mask = (luma >= lo_luma) & (luma <= hi_luma) & np.all(rgb > 1e-5, axis=2)
            balance_pixels = rgb[mask] if int(mask.sum()) > 512 else flat
        med = np.median(balance_pixels, axis=0).astype(np.float32)
        target_med = float(np.mean(med))
        raw_gains = np.clip(target_med / np.clip(med, 1e-6, None), 0.08, 12.0)
        raw_gains[1] = min(float(raw_gains[1]), 0.65)
        raw_gains[2] = min(float(raw_gains[2]), 2.0)
        gains = 1.0 + (raw_gains - 1.0) * 0.70
        rgb *= gains[np.newaxis, np.newaxis, :]

        finite = rgb[np.isfinite(rgb)]
        if finite.size == 0:
            raise RuntimeError(f"Could not render finite preview values from {canonical_path}")
        lo, hi = np.percentile(finite, [0.3, 99.7])
        preview = np.clip((rgb - float(lo)) / max(float(hi - lo), 1e-6), 0.0, 1.0)
        gray = (0.2126 * preview[..., 0] + 0.7152 * preview[..., 1] + 0.0722 * preview[..., 2])[..., None]
        preview = np.clip(gray + (preview - gray) * 1.18, 0.0, 1.0)
        preview = np.power(preview, 1.0 / 2.2)

        max_side = max(256, int(os.getenv("FAST_PREVIEW_MAX_SIDE", "1600")))
        h, w = preview.shape[:2]
        scale = min(1.0, max_side / max(h, w))
        if scale < 1.0:
            preview = cv2.resize(
                preview,
                (max(1, round(w * scale)), max(1, round(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        self._save_jpeg_preview(preview, preview_path)

    def _derive_flat_corrected_preview_from_dng(self, raw_path: Path, preview_path: Path) -> Path:
        """Render a classifier preview after full Bayer-domain flat-field correction.

        This is the shared boundary before film classification.  It applies the
        same RAW flat-field model used by the physical negative pipeline, then
        demosaics the corrected Bayer frame only for browser/VLM preview.
        """

        post_dir = _REPO_ROOT / "Post_Processing_Negative"
        if str(post_dir) not in sys.path:
            sys.path.insert(0, str(post_dir))

        from negative_physical.flat_field import apply_flat_field, build_flat_model
        from negative_physical.raw_io import load_raw_bayer
        from negative_physical.render_preview import demosaic_to_rgb

        backlight_path = calibration_store.matching_tile_dng(calibration_store.BACKLIGHT, raw_path)
        if backlight_path is None:
            backlight_path = calibration_store.get_dng_path_if_ready(calibration_store.BACKLIGHT)
        if backlight_path is None:
            raise RuntimeError(f"No matching backlight DNG available for {raw_path.name}")

        ff = flat_field_config.load()
        strength = float(ff.get("strength", 1.0))
        sigma_frac = float(ff.get("sigma_frac", 0.02))
        max_side = int(ff.get("max_side", 1024))

        frame = load_raw_bayer(raw_path)
        map_record = flat_field_maps.load_model_for_tile(raw_path)
        if map_record is not None:
            model, map_meta = map_record
        else:
            flat = load_raw_bayer(backlight_path)
            model = build_flat_model(flat, sigma_frac=sigma_frac, max_side=max_side)
            map_meta = None
        corrected_bayer = apply_flat_field(frame, model, strength=strength)
        rgb = demosaic_to_rgb(corrected_bayer, frame.cfa_pattern)
        rgb = np.clip(rgb, 0.0, None)
        self._save_balanced_detection_preview(rgb, preview_path)

        preview_path.with_suffix(".flat_field.json").write_text(
            json.dumps(
                {
                    "source": str(raw_path),
                    "backlight": str(backlight_path),
                    "strength": strength,
                    "sigma_frac": sigma_frac,
                    "max_side": max_side,
                    "flat_map": map_meta,
                    "preview_color_matrix": _DETECTION_PREVIEW_MATRIX_E0.tolist(),
                    "preview_color_mode": _DETECTION_PREVIEW_COLOR_MODE,
                    "model_diagnostics": model.diagnostics,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return preview_path

    @staticmethod
    def _read_dng_color_matrix(path: Path) -> np.ndarray | None:
        try:
            import rawpy  # type: ignore

            with rawpy.imread(str(path)) as raw:
                matrix = np.asarray(raw.color_matrix[:3, :3], dtype=np.float32)
            if np.isfinite(matrix).all() and np.abs(matrix).sum() > 1e-6:
                return matrix
        except Exception:
            return None
        return None

    def _save_balanced_detection_preview(self, rgb: np.ndarray, preview_path: Path) -> None:
        """Save a display preview from already-corrected linear-ish RGB data."""

        rgb = np.clip(rgb.astype(np.float32), 0.0, None)
        flat = rgb.reshape(-1, 3)
        luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
        finite_luma = luma[np.isfinite(luma)]
        if finite_luma.size == 0:
            raise RuntimeError("Could not render finite flat-field corrected preview values")
        bright_lo, bright_hi = np.percentile(finite_luma, [88.0, 99.6])
        luma_u8 = np.round(
            np.clip(luma / max(float(np.percentile(finite_luma, 99.5)), 1e-6), 0, 1) * 255
        ).astype(np.uint8)
        texture = cv2.absdiff(luma_u8, cv2.blur(luma_u8, (31, 31)))
        smooth_limit = float(np.percentile(texture, 45.0))
        backlight_mask = (
            (luma >= bright_lo)
            & (luma <= bright_hi)
            & (texture <= smooth_limit)
            & np.all(rgb > 1e-5, axis=2)
        )
        if int(backlight_mask.sum()) > 512:
            balance_pixels = rgb[backlight_mask]
        else:
            lo_luma, hi_luma = np.percentile(luma, [65.0, 98.0])
            mask = (luma >= lo_luma) & (luma <= hi_luma) & np.all(rgb > 1e-5, axis=2)
            balance_pixels = rgb[mask] if int(mask.sum()) > 512 else flat

        med = np.median(balance_pixels, axis=0).astype(np.float32)
        target_med = float(np.mean(med))
        raw_gains = np.clip(target_med / np.clip(med, 1e-6, None), 0.10, 8.0)
        wb_mix = float(os.getenv("DETECTION_PREVIEW_WB_MIX", "1.00"))
        gains = 1.0 + (raw_gains - 1.0) * wb_mix
        rgb *= gains[np.newaxis, np.newaxis, :]
        rgb = np.tensordot(rgb, _DETECTION_PREVIEW_MATRIX_E0, axes=([2], [0])).astype(np.float32)
        rgb = np.clip(rgb, 0.0, None)

        finite = rgb[np.isfinite(rgb)]
        lo, hi = np.percentile(finite, [0.3, 99.7])
        preview = np.clip((rgb - float(lo)) / max(float(hi - lo), 1e-6), 0.0, 1.0)
        gray = (0.2126 * preview[..., 0] + 0.7152 * preview[..., 1] + 0.0722 * preview[..., 2])[..., None]
        saturation = float(os.getenv("DETECTION_PREVIEW_SATURATION", "1.10"))
        preview = np.clip(gray + (preview - gray) * saturation, 0.0, 1.0)
        preview = np.power(preview, 1.0 / 2.2)

        max_side = max(256, int(os.getenv("FAST_PREVIEW_MAX_SIDE", "1600")))
        h, w = preview.shape[:2]
        scale = min(1.0, max_side / max(h, w))
        if scale < 1.0:
            preview = cv2.resize(
                preview,
                (max(1, round(w * scale)), max(1, round(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        self._save_jpeg_preview(preview, preview_path)

    @staticmethod
    def _render_fast_raw_rgb(path: Path, rawpy_module) -> tuple[np.ndarray, np.ndarray]:
        with rawpy_module.imread(str(path)) as raw:
            rgb16 = raw.postprocess(
                use_camera_wb=False,
                no_auto_bright=True,
                output_bps=16,
                gamma=(1, 1),
                half_size=True,
                output_color=rawpy_module.ColorSpace.raw,
            )
            color_matrix = np.asarray(raw.color_matrix[:3, :3], dtype=np.float32)
        return rgb16.astype(np.float32) / 65535.0, color_matrix

    def _apply_fast_preview_flat_field(
        self,
        frame_path: Path,
        rgb_raw: np.ndarray,
        color_matrix: np.ndarray,
        rawpy_module,
    ) -> np.ndarray:
        if os.getenv("FAST_PREVIEW_FLAT_FIELD", "0").strip().lower() in {"0", "false", "off"}:
            return rgb_raw
        backlight_path = calibration_store.matching_tile_dng(calibration_store.BACKLIGHT, frame_path)
        if backlight_path is None:
            backlight_path = calibration_store.get_dng_path_if_ready(calibration_store.BACKLIGHT)
        if backlight_path is None or not backlight_path.exists():
            return rgb_raw
        try:
            flat_raw, flat_matrix = self._render_fast_raw_rgb(backlight_path, rawpy_module)
            if flat_raw.shape[:2] != rgb_raw.shape[:2]:
                flat_raw = cv2.resize(
                    flat_raw,
                    (rgb_raw.shape[1], rgb_raw.shape[0]),
                    interpolation=cv2.INTER_AREA,
                )
            matrix = color_matrix if np.abs(color_matrix).sum() > 1e-6 else flat_matrix
            if np.isfinite(matrix).all() and np.abs(matrix).sum() > 1e-6:
                flat_rgb = np.tensordot(flat_raw, matrix.T, axes=([2], [0])).astype(np.float32)
            else:
                flat_rgb = flat_raw.astype(np.float32)
            flat_rgb = np.clip(flat_rgb, 1e-6, None)
            ff = flat_field_config.load()
            strength = float(ff.get("strength", 1.0))
            max_side = max(64, int(ff.get("max_side", 1024)))
            sigma_frac = max(0.001, float(ff.get("sigma_frac", 0.02)))
            h, w = flat_rgb.shape[:2]
            scale = min(1.0, max_side / max(h, w))
            if scale < 1.0:
                small = cv2.resize(
                    flat_rgb,
                    (max(1, round(w * scale)), max(1, round(h * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            else:
                small = flat_rgb
            sigma = max(1.0, sigma_frac * max(small.shape[:2]))
            low = cv2.GaussianBlur(small, (0, 0), sigmaX=sigma, sigmaY=sigma)
            if low.shape[:2] != (h, w):
                low = cv2.resize(low, (w, h), interpolation=cv2.INTER_LINEAR)
            mean = np.clip(low.reshape(-1, 3).mean(axis=0), 1e-6, None)
            illum = np.clip(low / mean[np.newaxis, np.newaxis, :], 1e-6, None)
            if abs(strength - 1.0) > 1e-6:
                illum = np.power(illum, strength).astype(np.float32)
            return (rgb_raw / illum).astype(np.float32)
        except Exception:
            return rgb_raw

    def _ensure_processing_inputs(self, job_id: str) -> tuple[Path | None, Path]:
        job_dir = self._job_dir(job_id)
        canonical_path = job_dir / "stitched_raw.dng"
        preview_path = job_dir / "raw_preview.jpg"

        if preview_path.exists() and canonical_path.exists() and preview_path.stat().st_mtime < canonical_path.stat().st_mtime:
            preview_path.unlink()
            self._up(job_id, artifacts={"raw_preview_url": None})

        if preview_path.exists():
            self._set_raw_preview(job_id)
            return (canonical_path if canonical_path.exists() else None), preview_path

        if canonical_path.exists():
            if not self._fetch_pi_stitched_preview(preview_path):
                self._derive_preview_from_dng(canonical_path, preview_path)
            self._set_raw_preview(job_id)
            return canonical_path, preview_path

        tile_input = self._select_tile_processing_input(job_id)
        if tile_input:
            tile_dng, tile_preview = tile_input
            if not preview_path.exists() and tile_preview and tile_preview.exists():
                shutil.copy2(tile_preview, preview_path)
            if preview_path.exists():
                self._set_raw_preview(job_id)
                return tile_dng, preview_path

        raise RuntimeError("No browser-safe raw preview or stitched raw artifact available")

    def _select_tile_processing_input(self, job_id: str) -> tuple[Path, Path | None] | None:
        """Phase transition: use one uploaded tile as canonical input.

        Full tile-by-tile parallel processing and final mosaic assembly are the
        next step. This keeps the scanner/PC connection usable without relying
        on synthetic stitched DNGs that break RAW calibration assumptions.
        """
        tiles_dir = self._job_dir(job_id) / "tiles"
        if not tiles_dir.exists():
            return None
        raw_tiles = sorted(tiles_dir.glob("row_*_col_*.dng"))
        if not raw_tiles:
            return None
        with self._lock:
            scan = dict(self._jobs.get(job_id, {}).get("scan", {}))
        rows = int(scan.get("rows") or 0)
        cols = int(scan.get("cols") or 0)
        preferred = None
        if rows and cols:
            preferred = tiles_dir / f"row_{rows // 2}_col_{cols // 2}.dng"
        tile_dng = preferred if preferred and preferred.exists() else raw_tiles[len(raw_tiles) // 2]
        preview = tiles_dir / f"{tile_dng.stem}.jpg"
        return tile_dng, preview if preview.exists() else None

    def _tile_processing_inputs(self, job_id: str) -> list[tuple[int, int, Path, Path | None]]:
        tiles_dir = self._job_dir(job_id) / "tiles"
        if not tiles_dir.exists():
            return []
        items: list[tuple[int, int, Path, Path | None]] = []
        for raw_path in sorted(tiles_dir.glob("row_*_col_*.dng")):
            parts = raw_path.stem.split("_")
            try:
                row = int(parts[1])
                col = int(parts[3])
            except (IndexError, ValueError):
                continue
            preview = tiles_dir / f"{raw_path.stem}.jpg"
            items.append((row, col, raw_path, preview if preview.exists() else None))
        return items

    def _ensure_tile_preview(self, raw_path: Path, preview_path: Path | None, output_path: Path) -> Path:
        # Pi-side JPEGs can be green sensor previews. Classification and bbox
        # detection should use a PC-rendered preview after RAW flat-field
        # correction, so routing sees the same corrected boundary as later
        # processing branches.
        del preview_path
        target = raw_path.with_name(f"{raw_path.stem}_flat_corrected_preview.jpg") if raw_path.parent.name == "tiles" else output_path
        backlight_path = calibration_store.matching_tile_dng(calibration_store.BACKLIGHT, raw_path)
        if backlight_path is None:
            backlight_path = calibration_store.get_dng_path_if_ready(calibration_store.BACKLIGHT)
        if target.exists() and target.stat().st_mtime >= raw_path.stat().st_mtime:
            metadata_path = target.with_suffix(".flat_field.json")
            color_mode_ok = False
            if metadata_path.exists():
                try:
                    preview_meta = json.loads(metadata_path.read_text(encoding="utf-8"))
                    color_mode_ok = (
                        preview_meta.get("preview_color_mode") == _DETECTION_PREVIEW_COLOR_MODE
                        and preview_meta.get("preview_color_matrix") == _DETECTION_PREVIEW_MATRIX_E0.tolist()
                    )
                except Exception:
                    color_mode_ok = False
            if color_mode_ok and (backlight_path is None or target.stat().st_mtime >= backlight_path.stat().st_mtime):
                return target
        try:
            self._derive_flat_corrected_preview_from_dng(raw_path, target)
        except Exception:
            logger.exception("Flat-field corrected preview failed for %s; falling back to fast DNG preview", raw_path)
            self._derive_preview_from_dng(raw_path, target)
        return target

    def _maybe_start_streaming_tile_processing(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("_tile_processing_started"):
                return
            if len(job.get("_tile_arrival_order", [])) < 3:
                return
            job["_tile_processing_started"] = True
            event = job["_tile_processing_done_event"]
            event.clear()
        threading.Thread(
            target=self._process_streaming_tile_set,
            args=(job_id,),
            daemon=True,
        ).start()

    def _wait_for_streaming_tile_processing(self, job_id: str) -> bool:
        event = self._get_tile_processing_event(job_id)
        if event is None:
            self._fail(job_id, "Job not found while waiting for tile processing")
            return False
        while True:
            job = self.get_job(job_id)
            if not job:
                self._fail(job_id, "Job disappeared while waiting for tile processing")
                return False
            if job.get("state") in {"failed", "cancelled"}:
                return False
            if event.wait(_WAIT_SLICE_S):
                return job.get("state") != "failed"

    def _bootstrap_tile_context(
        self,
        job_id: str,
        adapter,
        detect_preview_bboxes,
        map_preview_bbox_to_raw,
        classifier_cls,
    ) -> dict:
        job_dir = self._job_dir(job_id)
        bootstrap_dir = job_dir / "bootstrap"
        bootstrap_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            arrival = list(self._jobs.get(job_id, {}).get("_tile_arrival_order", []))[:3]
        if len(arrival) < 3:
            raise RuntimeError("Tile bootstrap requires the first three uploaded tiles")

        classifier_results = []
        bootstrap_items = []
        bbox_results = []
        accepted_base_bboxes = []
        threshold = float(os.getenv("BASE_BBOX_CONFIDENCE_THRESHOLD", "0.25"))
        max_base_area_fraction = float(os.getenv("BASE_BBOX_MAX_AREA_FRACTION", "0.45"))

        def _bbox_area_fraction(bbox: list | tuple | None, width: int = 4056, height: int = 3040) -> float | None:
            if not bbox or len(bbox) != 4:
                return None
            x0, y0, x1, y1 = [float(v) for v in bbox]
            area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
            return area / float(width * height)

        for row, col in arrival:
            raw_path = job_dir / "tiles" / f"row_{row}_col_{col}.dng"
            preview_path = job_dir / "tiles" / f"row_{row}_col_{col}.jpg"
            if not raw_path.exists():
                raise RuntimeError(f"Bootstrap tile is missing: {raw_path.name}")
            usable_preview = self._ensure_tile_preview(
                raw_path,
                preview_path if preview_path.exists() else None,
                bootstrap_dir / f"row_{row}_col_{col}_preview.jpg",
            )
            classifier_results.append(adapter._classify(usable_preview))
            bootstrap_items.append(
                {
                    "row": row,
                    "col": col,
                    "raw_path": raw_path,
                    "preview_path": usable_preview,
                }
            )

        classifier_result = self._vote_classifier(classifier_results, classifier_cls)
        valid_classifications = [item.classification for item in classifier_results if item.classification]
        vote_counts = Counter(valid_classifications)
        classifier_vote_count = int(vote_counts.get(classifier_result.classification, 0)) if classifier_result.classification else 0
        classifier_vote_total = len(valid_classifications)
        classifier_confidence = (
            float(classifier_vote_count / classifier_vote_total)
            if classifier_vote_total
            else None
        )

        self._up(
            job_id,
            stage="classify",
            progress_pct=78,
            artifacts={"metadata_url": f"/api/jobs/{job_id}/artifacts/metadata"},
            processing={
                "classification": classifier_result.classification,
                "classifier_raw_label": classifier_result.raw_label,
                "classifier_mode": classifier_result.mode,
                "classifier_confidence": getattr(classifier_result, "confidence", None),
            },
        )
        classification_metadata = {
            "mode": "first_three_tile_bootstrap",
            "arrival_order": [{"row": r, "col": c} for r, c in arrival],
            "classification": classifier_result.classification,
            "classifier_raw_label": classifier_result.raw_label,
            "classifier_mode": classifier_result.mode,
            "classifier_confidence": getattr(classifier_result, "confidence", None),
            "classification_confidence": classifier_confidence,
            "classification_vote_count": classifier_vote_count,
            "classification_vote_total": classifier_vote_total,
            "base_confidence_threshold": threshold,
            "base_max_area_fraction": max_base_area_fraction,
            "accepted_base_bbox_count": 0,
            "base_bbox": None,
            "bbox_results": [],
            "reference_layout_path": None,
        }
        (job_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "processing_mode": "streaming_classification_complete",
                    "bootstrap": classification_metadata,
                    "tiles": [],
                    "final_output_path": None,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        reference_layout_path: Path | None = None
        if classifier_result.classification == "negative_film":
            self._up(job_id, stage="classify", progress_pct=79)

        for item in bootstrap_items if classifier_result.classification == "negative_film" else []:
            row = int(item["row"])
            col = int(item["col"])
            raw_path = Path(item["raw_path"])
            usable_preview = Path(item["preview_path"])
            bbox_dir = bootstrap_dir / f"row_{row}_col_{col}"
            bbox_dir.mkdir(parents=True, exist_ok=True)
            bbox = detect_preview_bboxes(usable_preview, bbox_dir)
            bbox_payload = dict(bbox or {})
            base_bbox = bbox_payload.get("base_candidate_bbox")
            confidence = float(bbox_payload.get("confidence") or 0.0)
            area_fraction = _bbox_area_fraction(base_bbox)
            bbox_payload["base_area_fraction"] = area_fraction
            accepted = False
            reason = None
            if not base_bbox:
                reason = "missing_base_candidate_bbox"
            elif confidence < threshold:
                reason = "confidence_below_threshold"
            elif area_fraction is not None and area_fraction > max_base_area_fraction:
                reason = "base_bbox_too_large"
            else:
                mapped = map_preview_bbox_to_raw(
                    base_bbox,
                    preview_source=usable_preview,
                    raw_source=raw_path,
                )
                if mapped:
                    accepted_base_bboxes.append([float(v) for v in mapped])
                    bbox_payload["base_candidate_bbox_raw"] = [int(v) for v in mapped]
                    accepted = True
                else:
                    reason = "base_bbox_mapping_failed"
            bbox_payload["base_acceptance"] = {
                "accepted": accepted,
                "reason": reason,
                "confidence_threshold": threshold,
                "max_area_fraction": max_base_area_fraction,
            }
            bbox_results.append({"row": row, "col": col, "bbox_detection": bbox_payload})

        if classifier_result.classification == "negative_film" and not accepted_base_bboxes:
            raise RuntimeError(
                "Could not bootstrap base reference: first three tiles produced no "
                f"base_candidate_bbox above confidence {threshold} and below area fraction {max_base_area_fraction}"
            )
        metadata = {
            "mode": "first_three_tile_bootstrap",
            "arrival_order": [{"row": r, "col": c} for r, c in arrival],
            "classification": classifier_result.classification,
            "classifier_raw_label": classifier_result.raw_label,
            "classifier_mode": classifier_result.mode,
            "classifier_confidence": getattr(classifier_result, "confidence", None),
            "classification_confidence": classifier_confidence,
            "classification_vote_count": classifier_vote_count,
            "classification_vote_total": classifier_vote_total,
            "base_confidence_threshold": threshold,
            "base_max_area_fraction": max_base_area_fraction,
            "accepted_base_bbox_count": len(accepted_base_bboxes),
            "base_bbox": None,
            "base_reference_mode": (
                "global_base_rgb_from_first_three_bbox_pixels"
                if classifier_result.classification == "negative_film"
                else "not_required_for_non_negative"
            ),
            "bbox_results": bbox_results,
            "reference_layout_path": None,
        }
        (job_dir / "tile_bootstrap.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        (job_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "processing_mode": "streaming_bootstrap_pending",
                    "bootstrap": metadata,
                    "tiles": [],
                    "final_output_path": None,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self._up(
            job_id,
            stage="classify",
            progress_pct=79,
            artifacts={"metadata_url": f"/api/jobs/{job_id}/artifacts/metadata"},
            processing={
                "classification": classifier_result.classification,
                "classifier_raw_label": classifier_result.raw_label,
                "classifier_mode": classifier_result.mode,
                "classifier_confidence": getattr(classifier_result, "confidence", None),
            },
        )
        return {
            "classifier_result": classifier_result,
            "reference_layout_path": reference_layout_path,
            "bootstrap_metadata": metadata,
            "bootstrap_preview_map": {
                (int(item["row"]), int(item["col"])): str(item["preview_path"])
                for item in bootstrap_items
            },
        }

    def _vote_classifier(self, results: list, classifier_cls):
        valid = [item for item in results if item.classification]
        if not valid:
            return classifier_cls(None, None, "bootstrap_vote_empty")
        counts: dict[str, int] = {}
        for item in valid:
            counts[item.classification] = counts.get(item.classification, 0) + 1
        winner = max(counts.items(), key=lambda item: item[1])[0]
        first = next(item for item in valid if item.classification == winner)
        return classifier_cls(
            winner,
            first.raw_label,
            f"first_three_vote:{','.join(item.mode for item in results)}",
            first.error,
            getattr(first, "confidence", None),
        )

    def _tile_processing_job(
        self,
        job_id: str,
        row: int,
        col: int,
        raw_path: Path,
        preview_path: Path | None,
        adapter,
        classifier_result,
        reference_layout_path: Path | None,
        global_params_path: Path | None = None,
        bootstrap_preview_map: dict[tuple[int, int], str] | None = None,
    ) -> dict:
        job_dir = self._job_dir(job_id)
        tile_dir = job_dir / "processed_tiles" / raw_path.stem
        tile_dir.mkdir(parents=True, exist_ok=True)
        usable_preview: Path
        bootstrap_preview = (bootstrap_preview_map or {}).get((row, col))
        if bootstrap_preview:
            usable_preview = Path(bootstrap_preview)
        elif global_params_path is not None or classifier_result.classification == "positive_film":
            # Downstream RAW branches receive classifier_result/global params
            # from the first-three bootstrap and do not inspect preview pixels.
            # Keep a tiny placeholder to satisfy the adapter's path contract
            # without generating expensive E0 previews for every tile.
            usable_preview = tile_dir / f"{raw_path.stem}_processing_placeholder.jpg"
            if not usable_preview.exists():
                self._save_processing_placeholder_preview(usable_preview)
        else:
            usable_preview = self._ensure_tile_preview(
                raw_path,
                preview_path if preview_path and preview_path.exists() else None,
                tile_dir / f"{raw_path.stem}_preview.jpg",
            )
        if not (job_dir / "raw_preview.jpg").exists():
            shutil.copy2(usable_preview, job_dir / "raw_preview.jpg")
            self._set_raw_preview(job_id)
        result = adapter.run(
            canonical_source=raw_path,
            preview_source=usable_preview,
            output_dir=tile_dir,
            classifier_result=classifier_result,
            bbox_metadata={
                "mode": "streaming_first_three_base_reference",
                "coordinate_space": "preview_source_pixels",
                "preview_source": str(usable_preview),
                "film_content_bbox": None,
                "base_candidate_bbox": None,
                "confidence": None,
            },
            reference_layout_path=reference_layout_path,
            global_params_path=global_params_path,
            progress_cb=None,
        )
        result.update({"row": row, "col": col, "raw_tile_path": str(raw_path)})
        return result

    def _process_streaming_tile_set(self, job_id: str) -> None:
        from processing_adapter import (
            ProcessingAdapter,
            _ClassifierResult,
            _detect_preview_bboxes,
            _map_preview_bbox_to_raw,
        )

        event = self._get_tile_processing_event(job_id)
        if event is None:
            return
        adapter = ProcessingAdapter()
        tile_results: list[dict] = []
        processed: set[tuple[int, int]] = set()
        futures = {}
        max_workers = max(1, int(os.getenv("TILE_PROCESS_WORKERS", "2")))
        try:
            context = self._bootstrap_tile_context(
                job_id,
                adapter,
                _detect_preview_bboxes,
                _map_preview_bbox_to_raw,
                _ClassifierResult,
            )
            classifier_result = context["classifier_result"]
            reference_layout_path = context["reference_layout_path"]
            global_params_path = self._build_global_negative_params_from_bootstrap(
                job_id,
                context["bootstrap_metadata"],
            )
            global_params = None
            if global_params_path and global_params_path.exists():
                try:
                    global_params = json.loads(global_params_path.read_text(encoding="utf-8"))
                except Exception:
                    global_params = None
            self._up(job_id, stage="process", progress_pct=80)
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                while True:
                    if self._cancelled(job_id):
                        self._up(job_id, state="cancelled", stage="cancelled", progress_pct=0)
                        return
                    current_job = self.get_job(job_id) or {}
                    if current_job.get("state") in {"failed", "cancelled"}:
                        return
                    inputs = self._tile_processing_inputs(job_id)
                    for row, col, raw_path, preview_path in inputs:
                        key = (row, col)
                        if key in processed or key in futures:
                            continue
                        futures[key] = pool.submit(
                            self._tile_processing_job,
                            job_id,
                            row,
                            col,
                            raw_path,
                            preview_path,
                            ProcessingAdapter(),
                            classifier_result,
                            reference_layout_path,
                            global_params_path,
                            context.get("bootstrap_preview_map"),
                        )
                    done = set()
                    if futures:
                        done, _pending = wait(
                            list(futures.values()),
                            timeout=_WAIT_SLICE_S,
                            return_when=FIRST_COMPLETED,
                        )
                    for future in done:
                        key = next(k for k, v in futures.items() if v is future)
                        del futures[key]
                        result = future.result()
                        processed.add(key)
                        tile_results.append(result)
                        job = self.get_job(job_id) or {}
                        expected = (job.get("tile_upload") or {}).get("expected")
                        total = int(expected or max(len(processed), 1))
                        pct = min(96, 80 + int((len(processed) / max(total, 1)) * 16))
                        self._up(
                            job_id,
                            stage="process",
                            progress_pct=pct,
                            tile_upload={"processing_index": len(processed)},
                        )
                    job = self.get_job(job_id) or {}
                    tile_state = job.get("tile_upload") or {}
                    expected = tile_state.get("expected")
                    if expected and len(processed) >= int(expected) and not futures:
                        break
                    time.sleep(_WAIT_SLICE_S)

            final_path = self._stitch_processed_tiles(
                tile_results,
                self._job_dir(job_id) / "final.png",
                job_id=job_id,
            )
            metadata_path = self._job_dir(job_id) / "metadata.json"
            metadata = {
                "processing_mode": "streaming_first_three_bootstrap_per_tile_raw",
                "tile_count": len(tile_results),
                "bootstrap": context["bootstrap_metadata"],
                "global_negative_params_path": str(global_params_path) if global_params_path else None,
                "global_negative_params_url": f"/job-files/{job_id}/global_negative_params.json" if global_params_path else None,
                "global_negative_params": global_params,
                "tiles": tile_results,
                "final_output_path": str(final_path) if final_path else None,
            }
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            artifacts = {
                "raw_preview_url": f"/api/jobs/{job_id}/artifacts/raw",
                "metadata_url": f"/api/jobs/{job_id}/artifacts/metadata",
                "tile_set_url": f"/api/jobs/{job_id}/tiles",
            }
            if final_path:
                artifacts["final_preview_url"] = f"/api/jobs/{job_id}/artifacts/final"
                artifacts["final_download_url"] = f"/api/jobs/{job_id}/artifacts/final"
            self._up(
                job_id,
                stage="process",
                progress_pct=98,
                artifacts=artifacts,
                processing={
                    "classification": classifier_result.classification,
                    "classifier_raw_label": classifier_result.raw_label,
                    "classifier_mode": classifier_result.mode,
                    "runner_used": (tile_results[0] or {}).get("runner_used") if tile_results else None,
                    "score": (tile_results[0] or {}).get("score") if tile_results else None,
                },
            )
        except Exception as exc:
            self._fail(job_id, f"Tile streaming processing failed: {exc}")
        finally:
            event.set()

    def _save_processing_placeholder_preview(self, preview_path: Path) -> None:
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        image = np.zeros((16, 16, 3), dtype=np.uint8)
        image[:, :] = (32, 32, 32)
        ok = cv2.imwrite(
            str(preview_path),
            cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
            [int(cv2.IMWRITE_JPEG_QUALITY), 50],
        )
        if not ok:
            raise RuntimeError(f"Failed to write processing placeholder preview: {preview_path}")

    def _build_global_negative_params_from_bootstrap(
        self,
        job_id: str,
        bootstrap_metadata: dict,
    ) -> Path | None:
        """Build the negative branch global base reference from first-three detection only.

        This must not run the negative processing pipeline.  The first three
        tiles are used only for RGB preview classification and base ROI
        detection; every tile, including the first three, is processed exactly
        once later by _tile_processing_job().
        """

        if bootstrap_metadata.get("classification") != "negative_film":
            return None

        post_dir = _REPO_ROOT / "Post_Processing_Negative"
        if str(post_dir) not in sys.path:
            sys.path.insert(0, str(post_dir))

        from negative_physical.flat_field import apply_flat_field, build_flat_model
        from negative_physical.negative_inversion import (
            base_reference_to_dict,
            estimate_base_reference,
        )
        from negative_physical.raw_io import load_raw_bayer
        from negative_physical.render_preview import demosaic_to_rgb

        job_dir = self._job_dir(job_id)
        refs: list[dict] = []
        errors: list[str] = []
        for result in bootstrap_metadata.get("bbox_results") or []:
            row = int(result.get("row", 0))
            col = int(result.get("col", 0))
            detection = result.get("bbox_detection") or {}
            acceptance = detection.get("base_acceptance") or {}
            raw_bbox = detection.get("base_candidate_bbox_raw")
            if not acceptance.get("accepted") or not raw_bbox:
                continue

            raw_path = job_dir / "tiles" / f"row_{row}_col_{col}.dng"
            backlight_path = calibration_store.matching_tile_dng(calibration_store.BACKLIGHT, raw_path)
            if backlight_path is None:
                backlight_path = calibration_store.get_dng_path_if_ready(calibration_store.BACKLIGHT)
            if backlight_path is None:
                errors.append(f"row_{row}_col_{col}: missing matching backlight tile")
                continue

            try:
                frame = load_raw_bayer(raw_path)
                map_record = flat_field_maps.load_model_for_tile(raw_path)
                if map_record is not None:
                    flat_model, _map_meta = map_record
                else:
                    ff = flat_field_config.load()
                    flat = load_raw_bayer(backlight_path)
                    flat_model = build_flat_model(
                        flat,
                        sigma_frac=float(ff.get("sigma_frac", 0.02)),
                        max_side=int(ff.get("max_side", 1024)),
                    )
                corrected = apply_flat_field(frame, flat_model, strength=1.0)
                rgb_linear = demosaic_to_rgb(corrected, frame.cfa_pattern)
                ref = estimate_base_reference(
                    rgb_linear,
                    [{"name": "base", "bbox": [int(v) for v in raw_bbox]}],
                    source_frame=str(raw_path),
                    roi_name="base",
                )
                refs.append(base_reference_to_dict(ref))
            except Exception as exc:
                errors.append(f"row_{row}_col_{col}: {exc}")

        if not refs:
            detail = "; ".join(errors) if errors else "no accepted bootstrap base references"
            raise RuntimeError(f"Could not build global base reference from first three tiles: {detail}")

        def _avg_vec(values: list[list[float]]) -> list[float]:
            arr = np.asarray(values, dtype=np.float32)
            return [float(v) for v in arr.mean(axis=0)]

        base_rgb = _avg_vec([ref["base_rgb"] for ref in refs])
        sample_count = int(sum(int(ref.get("sample_count") or 0) for ref in refs))
        stats = {
            "global_average_count": len(refs),
            "source": "first_three_tile_bbox_pixel_average",
            "bootstrap_errors": errors,
            "per_tile_base_rgb": [ref["base_rgb"] for ref in refs],
            "per_tile_sample_bbox": [ref.get("bbox") for ref in refs],
        }
        base_reference = {
            "base_rgb": base_rgb,
            "roi_name": "base",
            "sample_count": sample_count,
            "source_frame": f"job:{job_id}:first_three_bbox_pixel_average",
            "stats": stats,
        }
        payload = {
            "mode": "first_three_tile_global_base_reference",
            "job_id": job_id,
            "bootstrap_tile_count": len(refs),
            "base_reference": base_reference,
            "negative_stats": {},
            "stage3": {},
            "stage4": {},
        }
        path = job_dir / "global_negative_params.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        bootstrap_metadata["global_negative_params_path"] = str(path)
        bootstrap_metadata["global_negative_params_url"] = f"/job-files/{job_id}/global_negative_params.json"
        bootstrap_metadata["global_base_reference"] = base_reference
        (job_dir / "tile_bootstrap.json").write_text(
            json.dumps(bootstrap_metadata, indent=2),
            encoding="utf-8",
        )
        (job_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "processing_mode": "streaming_bootstrap_complete",
                    "bootstrap": bootstrap_metadata,
                    "global_negative_params_path": str(path),
                    "global_negative_params_url": f"/job-files/{job_id}/global_negative_params.json",
                    "global_negative_params": payload,
                    "tiles": [],
                    "final_output_path": None,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self._up(
            job_id,
            stage="classify",
            progress_pct=80,
            artifacts={
                "metadata_url": f"/api/jobs/{job_id}/artifacts/metadata",
            },
        )
        return path

    _STITCH_MAX_PX = 4096  # longest axis target for the integrated output
    _REFINE_MAX_SHIFT_PX = 8
    _REFINE_STEP_PX = 2
    _REFINE_MIN_OVERLAP_PX = 80
    _REFINE_MIN_IMPROVEMENT = 0.08

    def _stitch_processed_tiles(
        self,
        tile_results: list[dict],
        output_path: Path,
        job_id: str | None = None,
    ) -> Path | None:
        images: list[dict] = []
        for item in tile_results:
            final_path = item.get("final_output_path")
            if not final_path:
                continue
            path = Path(final_path)
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                continue
            images.append(
                {
                    "row": int(item["row"]),
                    "col": int(item["col"]),
                    "image": image,
                    "path": str(path),
                    "raw_preview_path": item.get("raw_preview_path"),
                }
            )
        if not images:
            return None

        alignment = self._load_pi_alignment_for_mosaic()
        if alignment is None:
            return self._stitch_processed_tiles_grid(images, output_path, reason="alignment_unavailable")

        try:
            return self._stitch_processed_tiles_with_alignment(images, output_path, alignment, job_id)
        except Exception as exc:
            print(f"[job_orchestrator] alignment mosaic fallback: {exc}", flush=True)
            return self._stitch_processed_tiles_grid(images, output_path, reason=str(exc))

    def _stitch_processed_tiles_grid(
        self,
        images: list[dict],
        output_path: Path,
        reason: str,
    ) -> Path | None:
        rows = sorted({item["row"] for item in images})
        cols = sorted({item["col"] for item in images})
        max_h = max(item["image"].shape[0] for item in images)
        max_w = max(item["image"].shape[1] for item in images)

        # Downscale each tile so the full canvas fits within _STITCH_MAX_PX on the longest axis.
        full_h = len(rows) * max_h
        full_w = len(cols) * max_w
        scale = min(1.0, self._STITCH_MAX_PX / max(full_h, full_w))
        tile_h = max(1, round(max_h * scale))
        tile_w = max(1, round(max_w * scale))

        canvas = np.zeros((len(rows) * tile_h, len(cols) * tile_w, 3), dtype=np.uint8)
        row_index = {row: idx for idx, row in enumerate(rows)}
        col_index = {col: idx for idx, col in enumerate(cols)}
        num_cols = len(cols)
        placements = []
        for item in images:
            row, col, img = item["row"], item["col"], item["image"]
            small = cv2.resize(img, (tile_w, tile_h), interpolation=cv2.INTER_AREA)
            y = row_index[row] * tile_h
            # Serpentine scan: even 0-indexed rows captured right-to-left.
            # Map scan-order col to physical col so the canvas is spatially correct.
            phys_col_idx = (num_cols - 1 - col_index[col]) if row % 2 == 0 else col_index[col]
            x = phys_col_idx * tile_w
            canvas[y : y + small.shape[0], x : x + small.shape[1]] = small
            placements.append({"row": row, "col": col, "x": x, "y": y, "w": small.shape[1], "h": small.shape[0]})

        # Always write as JPEG regardless of what suffix output_path carries.
        out_jpg = output_path.with_suffix(".jpg")
        out_jpg.parent.mkdir(parents=True, exist_ok=True)
        ok = cv2.imwrite(str(out_jpg), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise RuntimeError(f"Failed to write processed tile mosaic: {out_jpg}")
        self._write_mosaic_alignment_report(
            output_path=out_jpg,
            report={
                "mode": "grid_fallback",
                "fallback_reason": reason,
                "scale": scale,
                "placements": placements,
            },
        )
        return out_jpg

    def _stitch_processed_tiles_with_alignment(
        self,
        images: list[dict],
        output_path: Path,
        alignment: dict,
        job_id: str | None,
    ) -> Path | None:
        measurements = alignment.get("measurements") or []
        profile = alignment.get("profile") or {}
        grid = profile.get("grid") or {}
        rows = int(grid.get("rows") or max(item["row"] for item in images) + 1)
        cols = int(grid.get("cols") or max(item["col"] for item in images) + 1)
        scan_index_mode = str(profile.get("stitch_capture_indexing_mode") or "")
        pair_offsets = self._alignment_pair_offsets(measurements, rows, cols, scan_index_mode)
        positions = self._manual_abs_positions(pair_offsets, rows, cols)
        if positions is None:
            raise RuntimeError("manual alignment profile does not cover the full tile grid")

        nominal_w = self._first_int(measurements, "tile_width") or max(item["image"].shape[1] for item in images)
        nominal_h = self._first_int(measurements, "tile_height") or max(item["image"].shape[0] for item in images)
        scale = min(
            1.0,
            self._STITCH_MAX_PX
            / max(
                self._aligned_extent_px(positions, nominal_w, axis="x"),
                self._aligned_extent_px(positions, nominal_h, axis="y"),
            ),
        )

        placements = []
        min_x = min(x for x, _ in positions.values())
        min_y = min(y for _, y in positions.values())
        for item in images:
            phys = self._scan_to_physical_index(item["row"], item["col"], rows, cols, scan_index_mode)
            if phys not in positions:
                raise RuntimeError(f"missing alignment position for tile {phys}")
            raw_x, raw_y = positions[phys]
            img = item["image"]
            sx = (img.shape[1] / nominal_w) * scale
            sy = (img.shape[0] / nominal_h) * scale
            small_w = max(1, round(img.shape[1] * scale))
            small_h = max(1, round(img.shape[0] * scale))
            x = round((raw_x - min_x) * sx)
            y = round((raw_y - min_y) * sy)
            small = cv2.resize(img, (small_w, small_h), interpolation=cv2.INTER_AREA)
            placements.append(
                {
                    "row": item["row"],
                    "col": item["col"],
                    "physical_row": phys[0],
                    "physical_col": phys[1],
                    "x": x,
                    "y": y,
                    "w": small_w,
                    "h": small_h,
                    "image": small,
                }
            )

        placements, refinement_report = self._refine_aligned_placements(placements, rows, cols)
        canvas_w = max(p["x"] + p["w"] for p in placements)
        canvas_h = max(p["y"] + p["h"] for p in placements)
        accum = np.zeros((canvas_h, canvas_w, 3), dtype=np.float32)
        weight = np.zeros((canvas_h, canvas_w, 1), dtype=np.float32)
        for p in placements:
            img = p["image"].astype(np.float32)
            y0, y1 = p["y"], p["y"] + p["h"]
            x0, x1 = p["x"], p["x"] + p["w"]
            accum[y0:y1, x0:x1] += img
            weight[y0:y1, x0:x1] += 1.0

        covered = weight[:, :, 0] > 0
        if not np.any(covered):
            raise RuntimeError("alignment mosaic produced no covered pixels")
        canvas = np.zeros_like(accum, dtype=np.uint8)
        canvas[covered] = np.clip(accum[covered] / weight[covered], 0, 255).astype(np.uint8)
        ys, xs = np.where(covered)
        crop = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        canvas = canvas[crop[1] : crop[3], crop[0] : crop[2]]

        out_jpg = output_path.with_suffix(".jpg")
        out_jpg.parent.mkdir(parents=True, exist_ok=True)
        ok = cv2.imwrite(str(out_jpg), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise RuntimeError(f"Failed to write processed tile mosaic: {out_jpg}")
        self._write_mosaic_alignment_report(
            output_path=out_jpg,
            report={
                "mode": "manual_alignment_profile",
                "source": alignment.get("source"),
                "job_id": job_id,
                "scan_index_mode": scan_index_mode,
                "grid": {"rows": rows, "cols": cols},
                "nominal_tile_size": {"width": nominal_w, "height": nominal_h},
                "scale": scale,
                "crop": {"left": crop[0], "top": crop[1], "right": crop[2], "bottom": crop[3]},
                "measurement_count": len(measurements),
                "overlap_refinement": refinement_report,
                "placements": [
                    {key: value for key, value in p.items() if key != "image"}
                    for p in placements
                ],
            },
        )
        return out_jpg

    def _refine_aligned_placements(
        self,
        placements: list[dict],
        rows: int,
        cols: int,
    ) -> tuple[list[dict], dict]:
        if os.getenv("PROCESSED_TILE_OVERLAP_REFINEMENT", "1").strip().lower() in {"0", "false", "off"}:
            return placements, {"enabled": False, "reason": "disabled_by_env"}
        radius = max(0, int(os.getenv("PROCESSED_TILE_REFINE_RADIUS_PX", str(self._REFINE_MAX_SHIFT_PX))))
        step = max(1, int(os.getenv("PROCESSED_TILE_REFINE_STEP_PX", str(self._REFINE_STEP_PX))))
        min_overlap = max(1, int(os.getenv("PROCESSED_TILE_REFINE_MIN_OVERLAP_PX", str(self._REFINE_MIN_OVERLAP_PX))))
        min_improvement = max(0.0, float(os.getenv("PROCESSED_TILE_REFINE_MIN_IMPROVEMENT", str(self._REFINE_MIN_IMPROVEMENT))))
        if radius <= 0:
            return placements, {"enabled": False, "reason": "radius_is_zero"}

        by_phys = {(p["physical_row"], p["physical_col"]): p for p in placements}
        pair_reports = []
        edge_offsets: dict[tuple[tuple[int, int], tuple[int, int]], tuple[int, int]] = {}
        for row in range(rows):
            for col in range(cols):
                src_key = (row, col)
                src = by_phys.get(src_key)
                if src is None:
                    continue
                for dst_key, axis in (((row, col + 1), "x"), ((row + 1, col), "y")):
                    dst = by_phys.get(dst_key)
                    if dst is None:
                        continue
                    result = self._best_overlap_shift(src, dst, radius, step, min_overlap)
                    if result is None:
                        shift_x = shift_y = 0
                        applied = False
                        reason = "insufficient_overlap_or_texture"
                        baseline = best = None
                    else:
                        shift_x, shift_y, baseline, best = result
                        improvement = 0.0 if baseline <= 0 else (baseline - best) / baseline
                        applied = improvement >= min_improvement
                        reason = "applied" if applied else "below_improvement_threshold"
                        if not applied:
                            shift_x = shift_y = 0
                    edge_offsets[(src_key, dst_key)] = (
                        int(dst["x"] - src["x"] + shift_x),
                        int(dst["y"] - src["y"] + shift_y),
                    )
                    pair_reports.append(
                        {
                            "axis": axis,
                            "src": {"row": src_key[0], "col": src_key[1]},
                            "dst": {"row": dst_key[0], "col": dst_key[1]},
                            "shift_x": int(shift_x),
                            "shift_y": int(shift_y),
                            "baseline_score": baseline,
                            "best_score": best,
                            "applied": applied,
                            "reason": reason,
                        }
                    )

        refined_positions = self._manual_abs_positions(edge_offsets, rows, cols)
        if refined_positions is None:
            return placements, {
                "enabled": True,
                "applied": False,
                "reason": "refined_graph_incomplete",
                "radius_px": radius,
                "step_px": step,
                "pairs": pair_reports,
            }

        min_x = min(x for x, _ in refined_positions.values())
        min_y = min(y for _, y in refined_positions.values())
        refined = []
        for p in placements:
            key = (p["physical_row"], p["physical_col"])
            if key not in refined_positions:
                refined.append(p)
                continue
            x, y = refined_positions[key]
            item = dict(p)
            item["manual_x"] = int(p["x"])
            item["manual_y"] = int(p["y"])
            proposed_x = int(x - min_x)
            proposed_y = int(y - min_y)
            # Keep refinement conservative: pair corrections may accumulate
            # through the graph, but per-tile deviation from manual placement
            # must remain within the configured local search radius.
            item["x"] = int(item["manual_x"] + max(-radius, min(radius, proposed_x - item["manual_x"])))
            item["y"] = int(item["manual_y"] + max(-radius, min(radius, proposed_y - item["manual_y"])))
            item["refine_dx"] = int(item["x"] - item["manual_x"])
            item["refine_dy"] = int(item["y"] - item["manual_y"])
            refined.append(item)

        return refined, {
            "enabled": True,
            "applied": any(pair.get("applied") for pair in pair_reports),
            "radius_px": radius,
            "step_px": step,
            "min_overlap_px": min_overlap,
            "min_improvement": min_improvement,
            "pairs": pair_reports,
        }

    def _best_overlap_shift(
        self,
        src: dict,
        dst: dict,
        radius: int,
        step: int,
        min_overlap: int,
    ) -> tuple[int, int, float, float] | None:
        baseline = self._overlap_score_at_shift(src, dst, 0, 0, min_overlap)
        if baseline is None:
            return None
        best_score = baseline
        best_shift = (0, 0)
        for dy in range(-radius, radius + 1, step):
            for dx in range(-radius, radius + 1, step):
                if dx == 0 and dy == 0:
                    continue
                score = self._overlap_score_at_shift(src, dst, dx, dy, min_overlap)
                if score is not None and score < best_score:
                    best_score = score
                    best_shift = (dx, dy)
        return best_shift[0], best_shift[1], float(baseline), float(best_score)

    def _overlap_score_at_shift(
        self,
        src: dict,
        dst: dict,
        shift_x: int,
        shift_y: int,
        min_overlap: int,
    ) -> float | None:
        ax0, ay0 = int(src["x"]), int(src["y"])
        bx0, by0 = int(dst["x"] + shift_x), int(dst["y"] + shift_y)
        ax1, ay1 = ax0 + int(src["w"]), ay0 + int(src["h"])
        bx1, by1 = bx0 + int(dst["w"]), by0 + int(dst["h"])
        x0, y0 = max(ax0, bx0), max(ay0, by0)
        x1, y1 = min(ax1, bx1), min(ay1, by1)
        if x1 - x0 < min_overlap or y1 - y0 < min_overlap:
            return None
        a = src["image"][y0 - ay0 : y1 - ay0, x0 - ax0 : x1 - ax0]
        b = dst["image"][y0 - by0 : y1 - by0, x0 - bx0 : x1 - bx0]
        return self._normalized_gradient_difference(a, b)

    @staticmethod
    def _normalized_gradient_difference(a: np.ndarray, b: np.ndarray) -> float | None:
        if a.size == 0 or b.size == 0 or a.shape[:2] != b.shape[:2]:
            return None
        max_dim = max(a.shape[0], a.shape[1])
        if max_dim > 320:
            scale = 320.0 / max_dim
            size = (max(1, round(a.shape[1] * scale)), max(1, round(a.shape[0] * scale)))
            a = cv2.resize(a, size, interpolation=cv2.INTER_AREA)
            b = cv2.resize(b, size, interpolation=cv2.INTER_AREA)
        ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float32)
        ea = cv2.Sobel(ga, cv2.CV_32F, 1, 0, ksize=3) + cv2.Sobel(ga, cv2.CV_32F, 0, 1, ksize=3)
        eb = cv2.Sobel(gb, cv2.CV_32F, 1, 0, ksize=3) + cv2.Sobel(gb, cv2.CV_32F, 0, 1, ksize=3)
        std_a = float(ea.std())
        std_b = float(eb.std())
        if std_a < 1e-3 or std_b < 1e-3:
            return None
        ea = (ea - float(ea.mean())) / std_a
        eb = (eb - float(eb.mean())) / std_b
        return float(np.mean(np.abs(ea - eb)))

    def _load_pi_alignment_for_mosaic(self) -> dict | None:
        if not _HAS_REQUESTS:
            return None
        try:
            resp = _req.get(f"{self._pi_url}/calibration/alignment", timeout=5)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            print(f"[job_orchestrator] failed to fetch Pi alignment profile: {exc}", flush=True)
            return None
        measurements = payload.get("effective_measurements") or payload.get("default_measurements") or []
        if not measurements:
            return None
        first_profile = next((m.get("scan_profile") for m in measurements if isinstance(m.get("scan_profile"), dict)), {})
        return {
            "source": "pi_calibration_alignment_effective_measurements",
            "measurements": measurements,
            "profile": first_profile,
        }

    def _alignment_pair_offsets(
        self,
        measurements: list[dict],
        rows: int,
        cols: int,
        scan_index_mode: str,
    ) -> dict[tuple[tuple[int, int], tuple[int, int]], tuple[int, int]]:
        offsets: dict[tuple[tuple[int, int], tuple[int, int]], tuple[int, int]] = {}
        for item in measurements:
            try:
                src = (int(item["row"]), int(item["col"]))
                dst = (int(item["neighbor_row"]), int(item["neighbor_col"]))
                if item.get("coordinate_space") != "physical_grid":
                    src = self._scan_to_physical_index(src[0], src[1], rows, cols, scan_index_mode)
                    dst = self._scan_to_physical_index(dst[0], dst[1], rows, cols, scan_index_mode)
                offsets.setdefault((src, dst), (int(round(float(item["dx"]))), int(round(float(item["dy"])))))
            except Exception:
                continue
        return offsets

    def _manual_abs_positions(
        self,
        pair_offsets: dict[tuple[tuple[int, int], tuple[int, int]], tuple[int, int]],
        rows: int,
        cols: int,
    ) -> dict[tuple[int, int], tuple[int, int]] | None:
        positions: dict[tuple[int, int], tuple[int, int]] = {(0, 0): (0, 0)}
        changed = True
        while changed:
            changed = False
            for (src, dst), (dx, dy) in pair_offsets.items():
                if src in positions and dst not in positions:
                    sx, sy = positions[src]
                    positions[dst] = (sx + dx, sy + dy)
                    changed = True
                elif dst in positions and src not in positions:
                    dx0, dy0 = positions[dst]
                    positions[src] = (dx0 - dx, dy0 - dy)
                    changed = True
        expected = {(row, col) for row in range(rows) for col in range(cols)}
        return positions if expected.issubset(positions.keys()) else None

    @staticmethod
    def _scan_to_physical_index(
        row: int,
        col: int,
        rows: int,
        cols: int,
        scan_index_mode: str,
    ) -> tuple[int, int]:
        del rows
        if scan_index_mode == "serpentine_scan_order" and row % 2 == 1:
            return row, cols - 1 - col
        return row, col

    @staticmethod
    def _aligned_extent_px(
        positions: dict[tuple[int, int], tuple[int, int]],
        tile_size: int,
        axis: str,
    ) -> int:
        idx = 0 if axis == "x" else 1
        values = [pos[idx] for pos in positions.values()]
        return max(values) - min(values) + tile_size

    @staticmethod
    def _first_int(items: list[dict], key: str) -> int | None:
        for item in items:
            value = item.get(key)
            if value is not None:
                try:
                    return int(value)
                except Exception:
                    pass
        return None

    @staticmethod
    def _write_mosaic_alignment_report(output_path: Path, report: dict) -> None:
        report_path = output_path.with_name(f"{output_path.stem}_alignment.json")
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Orchestration --------------------------------------------------------------

    def _run(self, job_id: str):
        try:
            job_kind = self._job_kind(job_id)
            source_path = self._src(job_id)

            if job_kind == "process_capture":
                if not source_path or not source_path.exists():
                    raise RuntimeError("Developer bypass job requires a valid source capture")
                preview_path = self._job_dir(job_id) / "raw_preview.jpg"
                self._copy_preview_from_local_capture(source_path, preview_path)
                self._up(job_id, stage="classify", progress_pct=78)
                self._set_raw_preview(job_id)
            elif job_kind == "one_click_scan":
                if source_path is not None:
                    raise RuntimeError("One-click jobs cannot use a local source capture")
                if not self._scan(job_id):
                    return
            else:
                raise RuntimeError(f"Unsupported job kind: {job_kind}")

            if self._cancelled(job_id):
                self._up(job_id, state="cancelled", stage="cancelled", progress_pct=0)
                return

            with self._lock:
                tile_processing_started = bool(
                    self._jobs.get(job_id, {}).get("_tile_processing_started")
                )
            if tile_processing_started:
                if not self._wait_for_streaming_tile_processing(job_id):
                    return
            else:
                self._process(job_id)

            if self._cancelled(job_id):
                self._up(job_id, state="cancelled", stage="cancelled", progress_pct=0)
            else:
                self._up(job_id, state="completed", stage="complete", progress_pct=100)
        except Exception as exc:
            self._fail(job_id, str(exc))

    def _scan(self, job_id: str) -> bool:
        """Drive the Pi scanner. Returns True on success, False on cancel/error."""
        if not _HAS_REQUESTS:
            self._fail(job_id, "Pi scanner unavailable (requests library missing)")
            return False

        self._up(job_id, stage="dispatch", progress_pct=2)
        payload = {"format": "35mm", "job_id": job_id}
        pc_app_url = self._current_pc_app_url()
        if pc_app_url:
            payload["pc_base_url"] = pc_app_url
            payload["upload_mode"] = "tiles"
            payload["tile_upload_url"] = f"{pc_app_url}/internal/jobs/{job_id}/receive_tile"
        try:
            resp = _req.post(f"{self._pi_url}/scan/start", json=payload, timeout=5)
            resp.raise_for_status()
        except Exception as exc:
            self._fail(job_id, f"Pi scanner unreachable: {exc}")
            return False

        self._up(job_id, stage="scan", progress_pct=5)
        while True:
            if self._cancelled(job_id):
                try:
                    _req.post(f"{self._pi_url}/scan/cancel", timeout=3)
                except Exception:
                    pass
                self._up(job_id, state="cancelled", stage="cancelled", progress_pct=0)
                return False

            try:
                resp = _req.get(f"{self._pi_url}/scan/status", timeout=3)
                status = resp.json()
            except Exception as exc:
                self._fail(job_id, f"Pi scanner lost during scan: {exc}")
                return False

            pi_state = status.get("state", "idle")
            pi_pct = float(status.get("progress", 0))

            if pi_state == "scanning":
                pct = 5 + int(pi_pct * 0.45)
                self._up(
                    job_id,
                    stage="scan",
                    progress_pct=pct,
                    scan={
                        "current_row": status.get("current_row", 0),
                        "current_col": status.get("current_col", 0),
                        "rows": status.get("total_rows", 0),
                        "cols": status.get("total_cols", 0),
                    },
                )
            elif pi_state == "stitching":
                self._up(job_id, stage="stitch", progress_pct=65)
            elif pi_state == "uploading":
                self._up(job_id, stage="transfer", progress_pct=70)
            elif pi_state == "returning_home":
                self._up(job_id, stage="transfer", progress_pct=72)
            elif pi_state == "idle":
                job = self.get_job(job_id)
                tile_state = job.get("tile_upload", {}) if job else {}
                received = int(tile_state.get("received") or 0)
                expected = tile_state.get("expected")
                if received > 0 and (not expected or received >= int(expected)):
                    self._up(job_id, stage="transfer", progress_pct=72)
                    return True
                if received > 0:
                    self._fail(job_id, f"Pi scan ended before all tile uploads arrived: {received}/{expected}")
                    return False
                self._up(job_id, stage="transfer", progress_pct=70)
                return self._wait_for_stitched_upload(job_id)
            elif pi_state in ("error", "cancelled"):
                self._fail(job_id, f"Pi: {status.get('error', pi_state)}")
                return False

            time.sleep(1)

    def _detect_pc_app_url(self) -> str:
        """Best-effort PC URL for the Pi upload callback."""
        port = int(os.getenv("PC_APP_PORT", "8000"))
        parsed = urlparse(self._pi_url)
        pi_host = parsed.hostname
        if not pi_host:
            return ""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((pi_host, 80))
                local_ip = sock.getsockname()[0]
        except OSError:
            return ""
        return f"http://{local_ip}:{port}"

    def _current_pc_app_url(self) -> str:
        """Return a Pi-reachable PC callback URL.

        The PC address can change when switching hotspot/Ethernet adapters, so
        avoid using a stale auto-detected URL from server startup. An explicit
        PC_APP_URL still wins for controlled deployments.
        """
        if self._configured_pc_app_url:
            self._pc_app_url = self._configured_pc_app_url
        else:
            detected = self._detect_pc_app_url()
            if detected:
                self._pc_app_url = detected
        return self._pc_app_url

    def _process(self, job_id: str):
        from processing_adapter import ProcessingAdapter

        tile_inputs = self._tile_processing_inputs(job_id)
        if tile_inputs:
            self._process_tile_set(job_id, tile_inputs, ProcessingAdapter())
            return

        canonical_source, preview_source = self._ensure_processing_inputs(job_id)
        self._up(job_id, stage="classify", progress_pct=78)

        def progress_cb(info: dict):
            processing_update: dict = {}
            for src_key, dst_key in (
                ("classification", "classification"),
                ("classifier_raw_label", "classifier_raw_label"),
                ("classifier_mode", "classifier_mode"),
                ("selected_branch", "selected_branch"),
                ("runner_used", "runner_used"),
                ("iteration", "current_iteration"),
                ("max_iter", "max_iterations"),
                ("score", "score"),
                ("calibration_skip_reason", "calibration_skip_reason"),
                ("missing_calibrations", "missing_calibrations"),
            ):
                if src_key in info:
                    processing_update[dst_key] = info[src_key]

            pct = 85
            if info.get("max_iter") and info.get("iteration") is not None:
                pct = 85 + int((info["iteration"] / info["max_iter"]) * 13)
            self._up(job_id, stage="process", progress_pct=min(pct, 98), processing=processing_update)

        result = ProcessingAdapter().run(
            canonical_source=canonical_source,
            preview_source=preview_source,
            output_dir=self._job_dir(job_id),
            progress_cb=progress_cb,
        )

        job_dir = self._job_dir(job_id)
        artifacts: dict = {
            "raw_preview_url": f"/api/jobs/{job_id}/artifacts/raw",
        }
        if canonical_source and canonical_source.exists():
            artifacts["stitched_raw_dng_url"] = f"/api/jobs/{job_id}/artifacts/stitched_raw_dng"

        final_output_path = result.get("final_output_path")
        if final_output_path:
            final_path = Path(final_output_path)
            if final_path.exists():
                dest = job_dir / f"final{final_path.suffix.lower()}"
                if final_path != dest:
                    shutil.copy2(final_path, dest)
                artifacts["final_preview_url"] = f"/api/jobs/{job_id}/artifacts/final"
                artifacts["final_download_url"] = f"/api/jobs/{job_id}/artifacts/final"

        metadata_path = result.get("metadata_path")
        if metadata_path:
            meta = Path(metadata_path)
            if meta.exists():
                dest = job_dir / "metadata.json"
                if meta != dest:
                    shutil.copy2(meta, dest)
                artifacts["metadata_url"] = f"/api/jobs/{job_id}/artifacts/metadata"

        self._up(
            job_id,
            artifacts=artifacts,
            processing={
                "classification": result.get("classification"),
                "classifier_raw_label": result.get("classifier_raw_label"),
                "classifier_mode": result.get("classifier_mode"),
                "selected_branch": result.get("selected_branch"),
                "runner_used": result.get("runner_used"),
                "score": result.get("score"),
                "calibration_skip_reason": result.get("calibration_skip_reason"),
                "missing_calibrations": result.get("missing_calibrations"),
            },
        )

    def _process_tile_set(
        self,
        job_id: str,
        tile_inputs: list[tuple[int, int, Path, Path | None]],
        adapter,
    ) -> None:
        from processing_adapter import (
            _ClassifierResult,
            _detect_preview_bboxes,
            _map_preview_bbox_to_raw,
        )

        job_dir = self._job_dir(job_id)
        processed_dir = job_dir / "processed_tiles"
        tile_results: list[dict] = []
        total = len(tile_inputs)
        self._up(job_id, stage="classify", progress_pct=78)

        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and len(job.get("_tile_arrival_order", [])) < 3:
                job["_tile_arrival_order"] = [(int(row), int(col)) for row, col, _raw, _preview in tile_inputs[:3]]

        context = self._bootstrap_tile_context(
            job_id,
            adapter,
            _detect_preview_bboxes,
            _map_preview_bbox_to_raw,
            _ClassifierResult,
        )
        classifier_result = context["classifier_result"]
        reference_layout_path = context["reference_layout_path"]
        global_params_path = self._build_global_negative_params_from_bootstrap(
            job_id,
            context["bootstrap_metadata"],
        )
        global_params = None
        if global_params_path and global_params_path.exists():
            try:
                global_params = json.loads(global_params_path.read_text(encoding="utf-8"))
            except Exception:
                global_params = None

        for index, (row, col, raw_path, preview_path) in enumerate(tile_inputs, start=1):
            if self._cancelled(job_id):
                self._up(job_id, state="cancelled", stage="cancelled", progress_pct=0)
                return
            result = self._tile_processing_job(
                job_id,
                row,
                col,
                raw_path,
                preview_path,
                adapter,
                classifier_result,
                reference_layout_path,
                global_params_path,
                context.get("bootstrap_preview_map"),
            )
            tile_results.append(result)
            self._up(
                job_id,
                stage="process",
                progress_pct=min(80 + int((index / max(total, 1)) * 16), 96),
                tile_upload={
                    "mode": "per_tile_raw",
                    "received": total,
                    "expected": total,
                    "complete": True,
                    "processing_index": index,
                },
            )

        final_path = self._stitch_processed_tiles(tile_results, job_dir / "final.png", job_id=job_id)
        metadata_path = job_dir / "metadata.json"
        metadata = {
            "processing_mode": "per_tile_raw_then_processed_mosaic_first_three_bootstrap",
            "tile_count": total,
            "bootstrap": context["bootstrap_metadata"],
            "global_negative_params_path": str(global_params_path) if global_params_path else None,
            "global_negative_params_url": f"/job-files/{job_id}/global_negative_params.json" if global_params_path else None,
            "global_negative_params": global_params,
            "tiles": tile_results,
            "final_output_path": str(final_path) if final_path else None,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        artifacts = {
            "raw_preview_url": f"/api/jobs/{job_id}/artifacts/raw",
            "metadata_url": f"/api/jobs/{job_id}/artifacts/metadata",
            "tile_set_url": f"/api/jobs/{job_id}/tiles",
        }
        if final_path:
            artifacts["final_preview_url"] = f"/api/jobs/{job_id}/artifacts/final"
            artifacts["final_download_url"] = f"/api/jobs/{job_id}/artifacts/final"
        first = tile_results[0] if tile_results else {}
        self._up(
            job_id,
            stage="process",
            progress_pct=98,
            artifacts=artifacts,
            processing={
                "classification": first.get("classification"),
                "classifier_raw_label": first.get("classifier_raw_label"),
                "classifier_mode": first.get("classifier_mode"),
                "selected_branch": first.get("selected_branch"),
                "runner_used": first.get("runner_used"),
                "score": first.get("score"),
            },
        )
