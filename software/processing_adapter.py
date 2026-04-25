"""Classification-routed processing adapter for the PC app server.

Phase 3 introduces an upfront classifier step. Based on the classification and
the state of the calibration store we dispatch to one of:

- ``negative_raw``    — ``Post_Processing_Negative/run_physical_correction.py``
                         subprocess on ``stitched_raw.dng`` when both the
                         backlight and base-frame calibration DNGs are present.
- ``negative_preview``— in-process ``pipelines/negative.py`` on the browser
                         preview when calibration is missing. Provides a useful
                         result even without RAW calibration.
- ``positive``        — in-process ``pipelines/positive.py``.
- ``instax``          — in-process ``pipelines/positive.py`` with
                         instant-print tuned defaults.
- ``preview_fallback``— ``Agentic_Post_Processing/_run_single.py`` subprocess.
                         Only reached when the classifier fails hard.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

import calibration_store

logger = logging.getLogger(__name__)

_SOFTWARE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SOFTWARE_DIR.parent
_AGENTIC_DIR = _REPO_ROOT / "Agentic_Post_Processing"
_NEGATIVE_DIR = _REPO_ROOT / "Post_Processing_Negative"

_PREVIEW_RUNNER = _AGENTIC_DIR / "_run_single.py"
_NEGATIVE_ENTRY = _NEGATIVE_DIR / "run_physical_correction.py"

_PREVIEW_RUNNER_NAME = "agentic_preview_runner"
_NEGATIVE_RAW_RUNNER_NAME = "post_processing_negative"
_POSITIVE_RUNNER_NAME = "positive_preview_pipeline"
_INSTAX_RUNNER_NAME = "instax_preview_pipeline"
_NEGATIVE_PREVIEW_RUNNER_NAME = "negative_preview_pipeline"

_NEGATIVE_RAW_MODE = "phase2_negative_raw"
_NEGATIVE_PREVIEW_MODE = "phase3_negative_preview"
_POSITIVE_MODE = "phase3_positive_preview"
_INSTAX_MODE = "phase3_instax_preview"
_PREVIEW_FALLBACK_MODE = "phase1_preview_only"

_NEGATIVE_FINAL_SUFFIX = "_stage4_final_finish.png"

_VALID_CLASSIFICATIONS = {"negative_film", "positive_film", "instax_instant_film"}


def _ensure_agentic_on_path() -> None:
    agentic = str(_AGENTIC_DIR)
    if agentic not in sys.path:
        sys.path.insert(0, agentic)


def _normalize_classification(raw_label: str | None) -> str | None:
    if not raw_label:
        return None
    label = raw_label.strip().lower()
    if label in _VALID_CLASSIFICATIONS:
        return label
    if label.startswith("instax") or label.startswith("instant"):
        return "instax_instant_film"
    if "negative" in label:
        return "negative_film"
    if "positive" in label or "slide" in label or "reversal" in label:
        return "positive_film"
    return None


def _resolve_preview_final_path(meta: dict, output_dir: Path, stem: str) -> Path | None:
    output_name = meta.get("output_image")
    if output_name:
        candidate = output_dir / str(output_name)
        if candidate.exists():
            return candidate

    for candidate in (
        output_dir / f"{stem}_processed.png",
        output_dir / f"{stem}_processed.jpg",
        output_dir / f"{stem}_processed.jpeg",
    ):
        if candidate.exists():
            return candidate
    return None


def _drain_stderr(
    proc: subprocess.Popen,
    sink: list[str],
    line_cb: Callable[[str], None] | None = None,
) -> None:
    if proc.stderr is None:
        return
    try:
        for raw in proc.stderr:
            line = raw.strip()
            if not line:
                continue
            sink.append(line)
            if line_cb is not None:
                try:
                    line_cb(line)
                except Exception:
                    # Progress parsing must not interrupt subprocess draining.
                    pass
    except ValueError:
        return


def _looks_like_instax(image_bgr: np.ndarray) -> bool:
    """Lightweight Instax heuristic: bright uniform frame around a central image.

    Checks whether the outer ~8% of the image is noticeably brighter and far
    less saturated than the central region, and that the central region is
    large enough to be the image window.
    """
    if image_bgr is None or image_bgr.size == 0:
        return False
    height, width = image_bgr.shape[:2]
    if min(height, width) < 120:
        return False

    border_frac = 0.08
    border_px = max(int(min(height, width) * border_frac), 8)

    border_mask = np.zeros((height, width), dtype=bool)
    border_mask[:border_px, :] = True
    border_mask[-border_px:, :] = True
    border_mask[:, :border_px] = True
    border_mask[:, -border_px:] = True
    inner_mask = ~border_mask

    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    value = hsv[..., 2].astype(np.float32)
    sat = hsv[..., 1].astype(np.float32)

    border_value = float(value[border_mask].mean())
    inner_value = float(value[inner_mask].mean())
    border_sat = float(sat[border_mask].mean())

    bright_border = border_value >= 225.0
    desaturated_border = border_sat <= 25.0
    brighter_than_inner = border_value - inner_value >= 40.0

    return bright_border and desaturated_border and brighter_than_inner


class _ClassifierResult:
    __slots__ = ("classification", "raw_label", "mode", "error")

    def __init__(
        self,
        classification: str | None,
        raw_label: str | None,
        mode: str,
        error: str | None = None,
    ) -> None:
        self.classification = classification
        self.raw_label = raw_label
        self.mode = mode
        self.error = error


class ProcessingAdapter:
    """Classification-first router for post-scan processing."""

    def run(
        self,
        canonical_source: Path | None,
        preview_source: Path,
        output_dir: Path,
        progress_cb: Callable[[dict], None] | None = None,
    ) -> dict:
        output_dir.mkdir(parents=True, exist_ok=True)

        if not preview_source.exists():
            raise RuntimeError(f"Preview source not found: {preview_source}")

        classifier_result = self._classify(preview_source)
        if progress_cb:
            progress_cb(
                {
                    "classification": classifier_result.classification,
                    "classifier_raw_label": classifier_result.raw_label,
                    "classifier_mode": classifier_result.mode,
                    "stage_hint": "classify_done",
                }
            )

        negative_raw_compatibility = (
            self._negative_raw_compatibility(canonical_source)
            if classifier_result.classification == "negative_film"
            else None
        )
        branch = self._select_branch(classifier_result, negative_raw_compatibility)

        if branch == "negative_raw":
            backlight_dng = calibration_store.get_dng_path_if_ready(calibration_store.BACKLIGHT)
            base_frame_dng = calibration_store.get_dng_path_if_ready(calibration_store.BASE_FRAME)
            assert canonical_source is not None and backlight_dng is not None and base_frame_dng is not None
            return self._run_negative_raw_branch(
                frame_dng=canonical_source,
                backlight_dng=backlight_dng,
                base_frame_dng=base_frame_dng,
                preview_source=preview_source,
                output_dir=output_dir,
                classifier_result=classifier_result,
                progress_cb=progress_cb,
            )

        if branch == "negative_preview":
            return self._run_preview_pipeline_branch(
                pipeline="negative",
                runner_name=_NEGATIVE_PREVIEW_RUNNER_NAME,
                processing_mode=_NEGATIVE_PREVIEW_MODE,
                selected_branch="negative",
                preview_source=preview_source,
                output_dir=output_dir,
                canonical_source=canonical_source,
                classifier_result=classifier_result,
                calibration_skip_reason=(
                    negative_raw_compatibility or {}
                ).get("reason"),
                missing_calibrations=(
                    negative_raw_compatibility or {}
                ).get("missing_calibrations"),
                progress_cb=progress_cb,
            )

        if branch == "positive":
            return self._run_preview_pipeline_branch(
                pipeline="positive",
                runner_name=_POSITIVE_RUNNER_NAME,
                processing_mode=_POSITIVE_MODE,
                selected_branch="positive",
                preview_source=preview_source,
                output_dir=output_dir,
                canonical_source=canonical_source,
                classifier_result=classifier_result,
                progress_cb=progress_cb,
            )

        if branch == "instax":
            return self._run_preview_pipeline_branch(
                pipeline="instax",
                runner_name=_INSTAX_RUNNER_NAME,
                processing_mode=_INSTAX_MODE,
                selected_branch="instax",
                preview_source=preview_source,
                output_dir=output_dir,
                canonical_source=canonical_source,
                classifier_result=classifier_result,
                progress_cb=progress_cb,
            )

        return self._run_preview_fallback(
            canonical_source=canonical_source,
            preview_source=preview_source,
            output_dir=output_dir,
            classifier_result=classifier_result,
            progress_cb=progress_cb,
        )

    # Classification --------------------------------------------------------

    def _classify(self, preview_source: Path) -> _ClassifierResult:
        try:
            _ensure_agentic_on_path()
            from agents.classifier import ClassifierAgent  # type: ignore
        except Exception as exc:
            logger.warning("Classifier unavailable: %s", exc)
            return _ClassifierResult(None, None, "unavailable", str(exc))

        try:
            agent = ClassifierAgent()
            film_type = agent.classify(preview_source)
        except Exception as exc:
            logger.warning("Classifier error: %s", exc)
            return _ClassifierResult(None, None, "error", str(exc))

        raw_label = getattr(film_type, "value", None) or str(film_type)
        normalized = _normalize_classification(raw_label)
        if normalized == "positive_film":
            try:
                image_bgr = cv2.imread(str(preview_source), cv2.IMREAD_COLOR)
                if image_bgr is not None and _looks_like_instax(image_bgr):
                    normalized = "instax_instant_film"
            except Exception as exc:
                logger.debug("Instax heuristic skipped: %s", exc)

        return _ClassifierResult(normalized, raw_label, getattr(agent, "last_mode", "unknown"))

    # Branch selection ------------------------------------------------------

    def _select_branch(
        self,
        classifier_result: _ClassifierResult,
        negative_raw_compatibility: dict | None,
    ) -> str:
        classification = classifier_result.classification
        if classification == "negative_film":
            if negative_raw_compatibility and negative_raw_compatibility.get("ok"):
                return "negative_raw"
            return "negative_preview"
        if classification == "positive_film":
            return "positive"
        if classification == "instax_instant_film":
            return "instax"
        return "fallback"

    def _negative_raw_compatibility(self, canonical_source: Path | None) -> dict:
        if canonical_source is None or not canonical_source.exists():
            return {
                "ok": False,
                "reason": "missing_canonical_dng",
                "missing_calibrations": [],
            }
        if canonical_source.suffix.lower() != ".dng":
            return {
                "ok": False,
                "reason": "canonical_source_is_not_dng",
                "missing_calibrations": [],
            }

        backlight = calibration_store.get_dng_path_if_ready(calibration_store.BACKLIGHT)
        base_frame = calibration_store.get_dng_path_if_ready(calibration_store.BASE_FRAME)
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
            }

        roi_path = calibration_store.dng_path(calibration_store.BASE_FRAME).parent / "reference_layout.json"
        if not roi_path.exists():
            return {
                "ok": False,
                "reason": "base_frame_roi_missing",
                "missing_calibrations": [],
            }

        frame_sig = calibration_store.dng_signature(canonical_source)
        backlight_sig = calibration_store.dng_signature(backlight)
        base_frame_sig = calibration_store.dng_signature(base_frame)
        ok, reason = calibration_store.signatures_compatible(
            frame_sig,
            backlight_sig,
        )
        return {
            "ok": ok,
            "reason": reason,
            "missing_calibrations": [],
            "frame_signature": frame_sig,
            "backlight_signature": backlight_sig,
            "base_frame_signature": base_frame_sig,
            "base_frame_usage": "roi_reference_only",
            "base_frame_roi_path": str(roi_path),
        }

    # Negative RAW branch (subprocess) --------------------------------------

    def _run_negative_raw_branch(
        self,
        frame_dng: Path,
        backlight_dng: Path,
        base_frame_dng: Path,
        preview_source: Path,
        output_dir: Path,
        classifier_result: _ClassifierResult,
        progress_cb: Callable[[dict], None] | None = None,
    ) -> dict:
        if not _NEGATIVE_ENTRY.exists():
            raise RuntimeError(f"Negative branch entry not found: {_NEGATIVE_ENTRY}")

        if progress_cb:
            progress_cb(
                {
                    "classification": "negative_film",
                    "classifier_raw_label": classifier_result.raw_label or "negative_film",
                    "classifier_mode": classifier_result.mode,
                    "selected_branch": "negative",
                    "runner_used": _NEGATIVE_RAW_RUNNER_NAME,
                    "stage_hint": "negative_branch_start",
                }
            )

        cmd = [
            sys.executable,
            str(_NEGATIVE_ENTRY),
            "--frame",
            str(frame_dng),
            "--backlight-frame",
            str(backlight_dng),
            "--base-frame",
            str(base_frame_dng),
            "--input-dir",
            str(frame_dng.parent),
            "--output-dir",
            str(output_dir),
            "--skip-npy",
            "--skip-linear16",
            "--skip-stage2-debug-png",
            "--skip-stage3-debug-png",
            "--skip-stage4-debug-png",
            "--skip-intermediate-previews",
            "--skip-flat-corrected-preview",
            "--max-processing-side",
            "3200",
        ]
        _roi_path = base_frame_dng.parent / "reference_layout.json"
        if _roi_path.exists():
            cmd += ["--reference-layout", str(_roi_path)]

        proc = subprocess.Popen(
            cmd,
            cwd=str(_NEGATIVE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        stderr_lines: list[str] = []
        stderr_thread = threading.Thread(
            target=_drain_stderr, args=(proc, stderr_lines), daemon=True
        )
        stderr_thread.start()

        stdout_data = ""
        try:
            if proc.stdout is not None:
                stdout_data = proc.stdout.read()
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
            proc.wait()
            stderr_thread.join(timeout=5)

        if proc.returncode != 0:
            tail = "\n".join(stderr_lines[-15:]) or "Negative branch subprocess failed"
            raise RuntimeError(f"Negative branch failed: {tail}")

        final_path = self._locate_negative_raw_final(frame_dng, output_dir)
        report_path = output_dir / "physical_correction_report.json"

        metadata = {
            "classification": "negative_film",
            "classifier_raw_label": classifier_result.raw_label or "negative_film",
            "classifier_mode": classifier_result.mode,
            "selected_branch": "negative",
            "runner_used": _NEGATIVE_RAW_RUNNER_NAME,
            "processing_mode": _NEGATIVE_RAW_MODE,
            "processing_input_kind": "stitched_raw_dng",
            "processing_input_path": str(frame_dng),
            "canonical_source": str(frame_dng),
            "canonical_source_available": True,
            "preview_source": str(preview_source),
            "backlight_dng_path": str(backlight_dng),
            "base_frame_dng_path": str(base_frame_dng),
            "final_output_path": str(final_path) if final_path else None,
            "negative_branch_report": report_path.name if report_path.exists() else None,
            "subprocess_stdout_tail": stdout_data.splitlines()[-10:] if stdout_data else [],
            "subprocess_stderr_tail": stderr_lines[-10:],
        }

        meta_file = output_dir / f"{frame_dng.stem}_meta.json"
        meta_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        if progress_cb:
            progress_cb(
                {
                    "classification": "negative_film",
                    "classifier_mode": classifier_result.mode,
                    "selected_branch": "negative",
                    "runner_used": _NEGATIVE_RAW_RUNNER_NAME,
                    "iteration": 1,
                    "max_iter": 1,
                    "score": 1.0,
                }
            )

        return {
            "classification": "negative_film",
            "classifier_raw_label": classifier_result.raw_label or "negative_film",
            "classifier_mode": classifier_result.mode,
            "selected_branch": "negative",
            "runner_used": _NEGATIVE_RAW_RUNNER_NAME,
            "score": 1.0,
            "raw_preview_path": str(preview_source),
            "final_output_path": str(final_path) if final_path else None,
            "metadata_path": str(meta_file),
        }

    def _locate_negative_raw_final(self, frame_dng: Path, output_dir: Path) -> Path | None:
        exact = output_dir / f"{frame_dng.stem}{_NEGATIVE_FINAL_SUFFIX}"
        if exact.exists():
            return exact
        matches = sorted(output_dir.glob(f"*{_NEGATIVE_FINAL_SUFFIX}"))
        return matches[-1] if matches else None

    # In-process preview branches ------------------------------------------

    def _run_preview_pipeline_branch(
        self,
        pipeline: str,
        runner_name: str,
        processing_mode: str,
        selected_branch: str,
        preview_source: Path,
        output_dir: Path,
        canonical_source: Path | None,
        classifier_result: _ClassifierResult,
        calibration_skip_reason: str | None = None,
        missing_calibrations: list[str] | None = None,
        progress_cb: Callable[[dict], None] | None = None,
    ) -> dict:
        _ensure_agentic_on_path()
        from models import PipelineParams  # type: ignore

        if pipeline == "negative":
            from pipelines.negative import run_negative_pipeline as run_pipeline  # type: ignore
            params = PipelineParams()
        elif pipeline == "positive":
            from pipelines.positive import run_positive_pipeline as run_pipeline  # type: ignore
            params = PipelineParams()
        elif pipeline == "instax":
            from pipelines.positive import run_positive_pipeline as run_pipeline  # type: ignore
            # Instant prints are already a positive image but typically lower contrast
            # and warmer. Slightly gentler stretch, more vibrance, modest CLAHE.
            params = PipelineParams(
                wb_clip_percent=0.3,
                clahe_clip=1.1,
                unsharp_amount=0.25,
                vibrance=0.15,
                gamma=1.0,
            )
        else:
            raise RuntimeError(f"Unknown preview pipeline: {pipeline}")

        if progress_cb:
            progress_cb(
                {
                    "classification": classifier_result.classification,
                    "classifier_raw_label": classifier_result.raw_label,
                    "classifier_mode": classifier_result.mode,
                    "selected_branch": selected_branch,
                    "runner_used": runner_name,
                    "calibration_skip_reason": calibration_skip_reason,
                    "missing_calibrations": missing_calibrations,
                    "stage_hint": f"{selected_branch}_preview_start",
                }
            )

        image_bgr = cv2.imread(str(preview_source), cv2.IMREAD_COLOR)
        if image_bgr is None:
            raise RuntimeError(f"Could not read preview: {preview_source}")

        processed = run_pipeline(image_bgr, params)
        stem = preview_source.stem
        final_path = output_dir / f"{stem}_processed.png"
        if not cv2.imwrite(str(final_path), processed):
            raise RuntimeError(f"Failed to write processed preview: {final_path}")

        metadata = {
            "source": preview_source.name,
            "classification": classifier_result.classification,
            "classifier_raw_label": classifier_result.raw_label,
            "classifier_mode": classifier_result.mode,
            "selected_branch": selected_branch,
            "runner_used": runner_name,
            "processing_mode": processing_mode,
            "processing_input_kind": "preview_raster",
            "processing_input_path": str(preview_source),
            "canonical_source": str(canonical_source) if canonical_source else None,
            "canonical_source_available": bool(canonical_source and canonical_source.exists()),
            "calibration_skip_reason": calibration_skip_reason,
            "missing_calibrations": missing_calibrations or [],
            "preview_source": str(preview_source),
            "final_output_path": str(final_path),
            "output_image": final_path.name,
        }

        meta_file = output_dir / f"{stem}_meta.json"
        meta_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        if progress_cb:
            progress_cb(
                {
                    "classification": classifier_result.classification,
                    "classifier_mode": classifier_result.mode,
                    "selected_branch": selected_branch,
                    "runner_used": runner_name,
                    "iteration": 1,
                    "max_iter": 1,
                    "score": 1.0,
                }
            )

        return {
            "classification": classifier_result.classification,
            "classifier_raw_label": classifier_result.raw_label,
            "classifier_mode": classifier_result.mode,
            "selected_branch": selected_branch,
            "runner_used": runner_name,
            "calibration_skip_reason": calibration_skip_reason,
            "missing_calibrations": missing_calibrations or [],
            "score": 1.0,
            "raw_preview_path": str(preview_source),
            "final_output_path": str(final_path),
            "metadata_path": str(meta_file),
        }

    # Preview fallback (classifier failure) --------------------------------

    def _run_preview_fallback(
        self,
        canonical_source: Path | None,
        preview_source: Path,
        output_dir: Path,
        classifier_result: _ClassifierResult,
        progress_cb: Callable[[dict], None] | None,
    ) -> dict:
        if not _PREVIEW_RUNNER.exists():
            raise RuntimeError(f"Preview runner not found: {_PREVIEW_RUNNER}")

        proc = subprocess.Popen(
            [sys.executable, str(_PREVIEW_RUNNER), str(preview_source), str(output_dir)],
            cwd=str(_AGENTIC_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        stderr_lines: list[str] = []

        def forward_progress(line: str) -> None:
            if not line.startswith("PROGRESS:") or progress_cb is None:
                return
            try:
                info = json.loads(line[len("PROGRESS:"):])
            except Exception:
                return
            progress_cb(
                {
                    "classification": _normalize_classification(info.get("film_type")),
                    "classifier_raw_label": info.get("film_type"),
                    "classifier_mode": info.get("classifier_mode") or classifier_result.mode,
                    "selected_branch": None,
                    "runner_used": _PREVIEW_RUNNER_NAME,
                    "iteration": info.get("iteration"),
                    "max_iter": info.get("max_iter"),
                    "score": info.get("score"),
                }
            )

        stderr_thread = threading.Thread(
            target=_drain_stderr, args=(proc, stderr_lines, forward_progress), daemon=True
        )
        stderr_thread.start()

        stdout_data = ""
        try:
            if proc.stdout is not None:
                stdout_data = proc.stdout.read()
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
            proc.wait()
            stderr_thread.join(timeout=5)

        if proc.returncode != 0:
            tail = "\n".join(stderr_lines[-10:]) or "Pipeline subprocess failed"
            raise RuntimeError(f"Processing failed: {tail}")

        try:
            meta = json.loads(stdout_data)
        except Exception as exc:
            raise RuntimeError(f"Bad pipeline output: {exc}")

        raw_label = meta.get("film_type")
        normalized = _normalize_classification(raw_label)
        stem = preview_source.stem
        final_score = meta.get("final_score")

        result: dict[str, Any] = {
            "classification": normalized,
            "classifier_raw_label": raw_label,
            "classifier_mode": meta.get("classifier_mode") or classifier_result.mode,
            "selected_branch": None,
            "runner_used": _PREVIEW_RUNNER_NAME,
            "score": final_score if final_score is not None else meta.get("score"),
            "raw_preview_path": str(preview_source),
            "final_output_path": None,
            "metadata_path": None,
        }

        processed = _resolve_preview_final_path(meta, output_dir, stem)
        if processed is not None and processed.exists():
            result["final_output_path"] = str(processed)

        metadata = {
            **meta,
            "classification": normalized,
            "classifier_raw_label": raw_label,
            "classifier_mode": meta.get("classifier_mode") or classifier_result.mode,
            "selected_branch": None,
            "runner_used": _PREVIEW_RUNNER_NAME,
            "canonical_source": str(canonical_source) if canonical_source else None,
            "preview_source": str(preview_source),
            "processing_mode": _PREVIEW_FALLBACK_MODE,
            "processing_input_kind": "preview_raster",
            "processing_input_path": str(preview_source),
            "canonical_source_available": bool(canonical_source and canonical_source.exists()),
            "upstream_classifier_error": classifier_result.error,
        }

        meta_file = output_dir / f"{stem}_meta.json"
        meta_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        result["metadata_path"] = str(meta_file)
        return result
