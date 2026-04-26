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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np

import calibration_store

try:
    import requests as _req

    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

_TRANSFER_TIMEOUT_S = 180.0
_WAIT_SLICE_S = 0.25
_JPEG_QUALITY = 92
_REPO_ROOT = Path(__file__).resolve().parent.parent

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
            for key, value in updates.items():
                if key in ("scan", "processing", "artifacts", "tile_upload") and isinstance(value, dict):
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
        from Agentic_Post_Processing.raw_pipeline.raw_ingest import load_raw_frame

        raw_frame = load_raw_frame(str(canonical_path))
        # load_raw_frame has already let rawpy subtract black, normalize white,
        # demosaic, and produce an RGB buffer. Do not call prepare_linear_rgb()
        # here; that would subtract native black level a second time and can
        # create the false-color preview seen on stitched multi-tile DNGs.
        linear_rgb = np.clip(raw_frame.linear_rgb.astype(np.float32), 0.0, 1.0)

        luma = 0.2126 * linear_rgb[..., 0] + 0.7152 * linear_rgb[..., 1] + 0.0722 * linear_rgb[..., 2]
        valid = luma[(luma > 0.002) & (luma < 0.995)]
        stretch_source = valid if valid.size > 100 else luma.reshape(-1)
        lo = float(np.percentile(stretch_source, 0.5))
        hi = float(np.percentile(stretch_source, 99.5))
        scale = max(hi - lo, 1e-6)
        preview = np.clip((linear_rgb - lo) / scale, 0.0, 1.0)
        preview = np.power(preview, 1.0 / 2.2)
        self._save_jpeg_preview(preview, preview_path)

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
        if preview_path is not None and preview_path.exists():
            return preview_path
        self._derive_preview_from_dng(raw_path, output_path)
        return output_path

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
        bbox_results = []
        accepted_base_bboxes = []
        threshold = float(os.getenv("BASE_BBOX_CONFIDENCE_THRESHOLD", "0.35"))
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
            bbox_dir = bootstrap_dir / f"row_{row}_col_{col}"
            bbox_dir.mkdir(parents=True, exist_ok=True)
            bbox = detect_preview_bboxes(usable_preview, bbox_dir)
            bbox_results.append({"row": row, "col": col, "bbox_detection": bbox})
            base_bbox = (bbox or {}).get("base_candidate_bbox")
            confidence = float((bbox or {}).get("confidence") or 0.0)
            if base_bbox and confidence >= threshold:
                mapped = map_preview_bbox_to_raw(
                    base_bbox,
                    preview_source=usable_preview,
                    raw_source=raw_path,
                )
                if mapped:
                    accepted_base_bboxes.append([float(v) for v in mapped])

        classifier_result = self._vote_classifier(classifier_results, classifier_cls)
        if not accepted_base_bboxes:
            raise RuntimeError(
                "Could not bootstrap base reference: first three tiles produced no "
                f"base_candidate_bbox above confidence {threshold}"
            )
        averaged = np.asarray(accepted_base_bboxes, dtype=np.float32).mean(axis=0)
        base_bbox = [int(round(v)) for v in averaged.tolist()]
        # Preserve Bayer phase for downstream RAW crops.
        base_bbox[0] -= base_bbox[0] % 2
        base_bbox[1] -= base_bbox[1] % 2
        base_bbox[2] -= base_bbox[2] % 2
        base_bbox[3] -= base_bbox[3] % 2

        reference_layout_path = job_dir / "reference_layout_streaming.json"
        reference_layout_path.write_text(
            json.dumps({"base": {"bbox": base_bbox}}, indent=2),
            encoding="utf-8",
        )
        metadata = {
            "mode": "first_three_tile_bootstrap",
            "arrival_order": [{"row": r, "col": c} for r, c in arrival],
            "classification": classifier_result.classification,
            "classifier_raw_label": classifier_result.raw_label,
            "classifier_mode": classifier_result.mode,
            "base_confidence_threshold": threshold,
            "accepted_base_bbox_count": len(accepted_base_bboxes),
            "base_bbox": base_bbox,
            "bbox_results": bbox_results,
            "reference_layout_path": str(reference_layout_path),
        }
        (job_dir / "tile_bootstrap.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        self._up(
            job_id,
            stage="classify",
            progress_pct=79,
            processing={
                "classification": classifier_result.classification,
                "classifier_raw_label": classifier_result.raw_label,
                "classifier_mode": classifier_result.mode,
            },
        )
        return {
            "classifier_result": classifier_result,
            "reference_layout_path": reference_layout_path,
            "bootstrap_metadata": metadata,
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
        reference_layout_path: Path,
    ) -> dict:
        job_dir = self._job_dir(job_id)
        tile_dir = job_dir / "processed_tiles" / raw_path.stem
        tile_dir.mkdir(parents=True, exist_ok=True)
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

    _STITCH_MAX_PX = 4096  # longest axis target for the integrated output

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
        placements = []
        for item in images:
            row, col, img = item["row"], item["col"], item["image"]
            small = cv2.resize(img, (tile_w, tile_h), interpolation=cv2.INTER_AREA)
            y = row_index[row] * tile_h
            x = col_index[col] * tile_w
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
                "placements": [
                    {key: value for key, value in p.items() if key != "image"}
                    for p in placements
                ],
            },
        )
        return out_jpg

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
        job_dir = self._job_dir(job_id)
        processed_dir = job_dir / "processed_tiles"
        tile_results: list[dict] = []
        total = len(tile_inputs)
        self._up(job_id, stage="classify", progress_pct=78)

        for index, (row, col, raw_path, preview_path) in enumerate(tile_inputs, start=1):
            if self._cancelled(job_id):
                self._up(job_id, state="cancelled", stage="cancelled", progress_pct=0)
                return
            tile_dir = processed_dir / raw_path.stem
            tile_dir.mkdir(parents=True, exist_ok=True)
            usable_preview = self._ensure_tile_preview(
                raw_path,
                preview_path,
                tile_dir / f"{raw_path.stem}_preview.jpg",
            )
            if index == 1 and not (job_dir / "raw_preview.jpg").exists():
                shutil.copy2(usable_preview, job_dir / "raw_preview.jpg")
                self._set_raw_preview(job_id)

            def progress_cb(info: dict, tile_index: int = index):
                processing_update = {
                    key: info[key]
                    for key in (
                        "classification",
                        "classifier_raw_label",
                        "classifier_mode",
                        "selected_branch",
                        "runner_used",
                        "score",
                        "calibration_skip_reason",
                        "missing_calibrations",
                    )
                    if key in info
                }
                base = 80 + int(((tile_index - 1) / max(total, 1)) * 16)
                self._up(
                    job_id,
                    stage="process",
                    progress_pct=min(base, 97),
                    processing=processing_update,
                    tile_upload={
                        "mode": "per_tile_raw",
                        "received": total,
                        "expected": total,
                        "complete": True,
                        "processing_index": tile_index,
                    },
                )

            result = adapter.run(
                canonical_source=raw_path,
                preview_source=usable_preview,
                output_dir=tile_dir,
                progress_cb=progress_cb,
            )
            result.update({"row": row, "col": col, "raw_tile_path": str(raw_path)})
            tile_results.append(result)

        final_path = self._stitch_processed_tiles(tile_results, job_dir / "final.png", job_id=job_id)
        metadata_path = job_dir / "metadata.json"
        metadata = {
            "processing_mode": "per_tile_raw_then_processed_mosaic",
            "tile_count": total,
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
