"""Unified scan job orchestrator - PC App Server side."""

from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
import uuid
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
        },
        # Internal fields stripped from the public job model.
        "_cancel": False,
        "_source_path": None,
        "_stitched_upload_event": threading.Event(),
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
                if key in ("scan", "processing", "artifacts") and isinstance(value, dict):
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

        raise RuntimeError("No browser-safe raw preview or stitched raw artifact available")

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
            payload["upload_url"] = f"{pc_app_url}/internal/jobs/{job_id}/receive_stitched_raw"
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
