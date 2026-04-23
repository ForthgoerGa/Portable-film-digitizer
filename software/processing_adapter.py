"""Transitional processing adapter for the Phase 1 web framework."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

_SOFTWARE_DIR = Path(__file__).resolve().parent
_AGENTIC_DIR = _SOFTWARE_DIR.parent / "Agentic_Post_Processing"
_RUNNER = _AGENTIC_DIR / "_run_single.py"
_RUNNER_USED = "agentic_preview_runner"
_PROCESSING_MODE = "phase1_preview_only"
_PROCESSING_INPUT_KIND = "preview_raster"


def _normalize_classification(raw_label: str | None) -> str | None:
    if not raw_label:
        return None

    label = raw_label.strip().lower()
    if label in {"negative_film", "positive_film", "instax_instant_film"}:
        return label
    if label.startswith("instax") or label.startswith("instant"):
        return "instax_instant_film"
    if "negative" in label:
        return "negative_film"
    if "positive" in label or "slide" in label or "reversal" in label:
        return "positive_film"
    return None


def _resolve_final_output_path(meta: dict, output_dir: Path, stem: str) -> Path | None:
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


class ProcessingAdapter:
    """Classification boundary for Phase 1.

    This adapter is intentionally transitional. It preserves the canonical DNG for
    future routing work, but the actual processing runner in Phase 1 operates on
    the browser-safe raw preview raster.
    """

    def run(
        self,
        canonical_source: Path | None,
        preview_source: Path,
        output_dir: Path,
        progress_cb: Callable[[dict], None] | None = None,
    ) -> dict:
        output_dir.mkdir(parents=True, exist_ok=True)

        if not _RUNNER.exists():
            raise RuntimeError(f"Pipeline runner not found: {_RUNNER}")
        if not preview_source.exists():
            raise RuntimeError(f"Preview source not found: {preview_source}")

        proc = subprocess.Popen(
            [sys.executable, str(_RUNNER), str(preview_source), str(output_dir)],
            cwd=str(_AGENTIC_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        stderr_lines: list[str] = []

        def _read_stderr():
            if proc.stderr is None:
                return

            try:
                for raw in proc.stderr:
                    line = raw.strip()
                    if not line:
                        continue
                    stderr_lines.append(line)
                    if line.startswith("PROGRESS:") and progress_cb:
                        try:
                            info = json.loads(line[9:])
                        except Exception:
                            continue
                        progress_cb(
                            {
                                "classification": _normalize_classification(info.get("film_type")),
                                "classifier_raw_label": info.get("film_type"),
                                "selected_branch": None,
                                "runner_used": _RUNNER_USED,
                                "iteration": info.get("iteration"),
                                "max_iter": info.get("max_iter"),
                                "score": info.get("score"),
                            }
                        )
            except ValueError:
                # The pipe can be closed during teardown; preserve collected stderr.
                return

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
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
            err = "\n".join(stderr_lines[-10:]) or "Pipeline subprocess failed"
            raise RuntimeError(f"Processing failed: {err}")

        try:
            meta = json.loads(stdout_data)
        except Exception as exc:
            raise RuntimeError(f"Bad pipeline output: {exc}")

        raw_label = meta.get("film_type")
        normalized = _normalize_classification(raw_label)
        stem = preview_source.stem
        final_score = meta.get("final_score")

        result: dict = {
            "classification": normalized,
            "classifier_raw_label": raw_label,
            "selected_branch": None,
            "runner_used": _RUNNER_USED,
            "score": final_score if final_score is not None else meta.get("score"),
            "raw_preview_path": str(preview_source),
            "final_output_path": None,
            "metadata_path": None,
        }

        processed = _resolve_final_output_path(meta, output_dir, stem)
        if processed is not None and processed.exists():
            result["final_output_path"] = str(processed)

        metadata = {
            **meta,
            "classification": normalized,
            "classifier_raw_label": raw_label,
            "selected_branch": None,
            "runner_used": _RUNNER_USED,
            "canonical_source": str(canonical_source) if canonical_source else None,
            "preview_source": str(preview_source),
            "processing_mode": _PROCESSING_MODE,
            "processing_input_kind": _PROCESSING_INPUT_KIND,
            "processing_input_path": str(preview_source),
            "canonical_source_available": bool(canonical_source and canonical_source.exists()),
        }

        meta_file = output_dir / f"{stem}_meta.json"
        meta_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        result["metadata_path"] = str(meta_file)
        return result
