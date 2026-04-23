from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2

import config
from agents.classifier import ClassifierAgent
from agents.decision_model import DecisionAgent
from agents.evaluator import EvaluatorAgent
from agents.processor import PostProcessingAgent
from models import PipelineParams

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# File extensions treated as RAW inputs — routed to raw_pipeline.
_RAW_EXTENSIONS = {".dng", ".arw", ".nef", ".cr2", ".cr3", ".raf", ".rw2", ".orf", ".pef"}

# Names used when searching for a flat-field file alongside a frame.
_FLAT_NAMES = ("flat.dng", "flat_field.dng", "flatfield.dng", "flat.arw", "flat.nef")


def _agent_mode(agent: object) -> str:
    mode = getattr(agent, "last_mode", "unknown")
    return mode if isinstance(mode, str) else "unknown"


def _agent_metrics(agent: object) -> dict[str, float] | None:
    metrics = getattr(agent, "last_metrics", None)
    return metrics if isinstance(metrics, dict) else None


def _find_flat_for_frame(frame_path: Path) -> Path | None:
    """Search for a flat-field RAW alongside a frame file.

    Looks in the same directory for any of the known flat filenames.
    """
    parent = frame_path.parent
    for name in _FLAT_NAMES:
        candidate = parent / name
        if candidate.exists():
            return candidate
    return None


def process_raw_image(
    frame_path: Path,
    flat_path: Path,
    output_dir: Path,
    leader_path: Path | None = None,
    max_iterations: int | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    """Process a RAW negative frame through the raw-aware pipeline with a bounded
    agent loop driven by stage-aware diagnostics.

    The technical inversion pipeline (flat-field → border → film characterisation
    → density inversion) is deterministic.  The agent loop adjusts only the
    *render_params* fed to the aesthetic rendering stage based on the evaluator
    output from ``raw_pipeline.diagnostics``.

    Parameters
    ----------
    frame_path:
        Path to the negative frame RAW file (DNG / ARW / etc.).
    flat_path:
        Path to the flat-field RAW file (same setup, no film).
    output_dir:
        Directory where intermediate and final images will be saved.
    leader_path:
        Optional path to a leader/calibration RAW frame.
    max_iterations:
        Maximum render-refinement iterations (default: config.MAX_EVAL_ITERATIONS).
    progress_callback:
        Optional callable that receives a progress dict after each iteration.

    Returns
    -------
    dict with the same top-level keys as ``process_image`` plus raw-pipeline
    specific keys (``calibration_mode``, ``border_confidence``, ``stage_metrics``).
    """
    from raw_pipeline.pipeline import process_raw_negative

    frame_path = Path(frame_path)
    flat_path = Path(flat_path)
    output_dir = Path(output_dir)
    max_iters = max_iterations if max_iterations is not None else config.MAX_EVAL_ITERATIONS

    render_params: dict[str, Any] | None = None
    best_result: dict[str, Any] | None = None
    best_score: float = -1.0

    for iteration in range(1, max_iters + 1):
        logger.info(
            "RAW pipeline iteration %d/%d: %s", iteration, max_iters, frame_path.name
        )
        iter_output_dir = str(output_dir / f"iter_{iteration:02d}")
        result = process_raw_negative(
            frame_raw_path=str(frame_path),
            flat_raw_path=str(flat_path),
            leader_raw_path=str(leader_path) if leader_path else None,
            output_dir=iter_output_dir,
            render_params=render_params,
        )

        if result["status"] == "error":
            logger.error("Raw pipeline error on iteration %d: %s", iteration, result.get("error", ""))
            # Return partial result on hard error.
            return {
                "source": frame_path.name,
                "pipeline": "raw",
                "status": "error",
                "error": result.get("error", "unknown"),
                "iterations": iteration,
                "stage_metrics": result.get("stage_metrics", {}),
                "evaluator_output": result.get("evaluator_output", {}),
                "output_paths": result.get("output_paths", {}),
            }

        ev = result.get("evaluator_output", {})
        score = float(ev.get("overall_score", 0.0))
        suspected_stage = ev.get("suspected_failure_stage")
        strategy = ev.get("recommended_strategy")
        logger.info(
            "Iteration %d: overall_score=%.3f  suspected_failure=%s  strategy=%s",
            iteration, score, suspected_stage, strategy,
        )

        if progress_callback is not None:
            try:
                progress_callback({
                    "iteration": iteration,
                    "max_iter": max_iters,
                    "pipeline": "raw",
                    "score": round(score, 4),
                    "passed": score >= config.QUALITY_THRESHOLD,
                    "suspected_failure_stage": suspected_stage,
                    "recommended_strategy": strategy,
                    "calibration_mode": result.get("calibration_mode", ""),
                    "border_confidence": result.get("border_confidence", 0.0),
                })
            except Exception:
                pass

        if score > best_score:
            best_score = score
            best_result = result
            best_result["_best_iteration"] = iteration

        if score >= config.QUALITY_THRESHOLD:
            logger.info("Quality threshold met at iteration %d (score=%.3f).", iteration, score)
            break

        if iteration < max_iters:
            # Use the diagnostics-recommended render params for next iteration.
            next_render = dict(ev.get("recommended_params", {}))
            if next_render:
                render_params = next_render
                logger.info("Applying recommended render params for iteration %d: %s", iteration + 1, render_params)

    if best_result is None:
        raise RuntimeError(f"Raw pipeline produced no output for {frame_path}")

    # Copy the best rendered output to the final output directory.
    output_dir.mkdir(parents=True, exist_ok=True)
    best_iter = best_result.get("_best_iteration", 1)
    best_paths = best_result.get("output_paths", {})
    rendered_src = best_paths.get("05_rendered") or best_paths.get("05_rendered".replace("05_rendered", "rendered"))
    # Try to find any rendered output path.
    rendered_src = rendered_src or next(
        (v for k, v in best_paths.items() if "render" in k.lower() or "05" in k), None
    )

    final_image_path = output_dir / f"{frame_path.stem}_processed.png"
    if rendered_src and Path(rendered_src).exists():
        import shutil
        shutil.copy2(rendered_src, final_image_path)
    else:
        # Fallback: copy first available output image.
        for p in best_paths.values():
            if Path(p).exists():
                import shutil
                shutil.copy2(p, final_image_path)
                break

    meta = {
        "source": frame_path.name,
        "flat_source": flat_path.name,
        "leader_source": leader_path.name if leader_path else None,
        "pipeline": "raw",
        "calibration_mode": best_result.get("calibration_mode", ""),
        "border_confidence": best_result.get("border_confidence", 0.0),
        "iterations": iteration,
        "selected_iteration": best_iter,
        "final_score": round(best_score, 4),
        "passed": best_score >= config.QUALITY_THRESHOLD,
        "stage_metrics": best_result.get("stage_metrics", {}),
        "evaluator_output": best_result.get("evaluator_output", {}),
        "output_image": final_image_path.name,
        "intermediate_paths": best_paths,
    }
    meta_path = output_dir / f"{frame_path.stem}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return meta


def process_image(
    image_path: Path,
    output_dir: Path,
    classifier: ClassifierAgent | None = None,
    processor: PostProcessingAgent | None = None,
    evaluator: EvaluatorAgent | None = None,
    decision_agent: DecisionAgent | None = None,
    max_iterations: int | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    image_path = Path(image_path)
    output_dir = Path(output_dir)

    # Route RAW inputs to the raw-aware pipeline instead of the legacy agent loop.
    if image_path.suffix.lower() in _RAW_EXTENSIONS:
        flat_path = _find_flat_for_frame(image_path)
        if flat_path is None:
            raise FileNotFoundError(
                f"No flat-field file found alongside {image_path.name}. "
                f"Place one of {_FLAT_NAMES} in the same directory, or call "
                f"process_raw_image() directly with an explicit flat_path."
            )
        return process_raw_image(
            frame_path=image_path,
            flat_path=flat_path,
            output_dir=output_dir,
            max_iterations=max_iterations,
            progress_callback=progress_callback,
        )

    classifier = classifier or ClassifierAgent()
    processor = processor or PostProcessingAgent()
    evaluator = evaluator or EvaluatorAgent()
    decision_agent = decision_agent or DecisionAgent()
    max_iters = max_iterations if max_iterations is not None else config.MAX_EVAL_ITERATIONS

    image_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image_bgr is None:
        raise ValueError(f"Cannot read image: {image_path}")

    film_type = classifier.classify(image_path)
    params = PipelineParams()
    result_img = None
    quality = None
    iteration = 0
    best_result_img = None
    best_quality = None
    best_params = params
    best_iteration = 0
    decision_events: list[dict[str, Any]] = []

    for iteration in range(1, max_iters + 1):
        logger.info(
            "Processing %s iteration %d/%d as %s",
            image_path.name,
            iteration,
            max_iters,
            film_type.value,
        )
        result_img = processor.process(image_bgr, film_type, params)
        quality = evaluator.evaluate(result_img)
        logger.info("Score %.3f for %s: %s", quality.score, image_path.name, quality.feedback)
        if progress_callback is not None:
            try:
                progress_callback({
                    "iteration": iteration,
                    "max_iter": max_iters,
                    "film_type": film_type.value,
                    "score": round(quality.score, 4),
                    "passed": quality.passed,
                    "feedback": quality.feedback,
                    "evaluator_mode": _agent_mode(evaluator),
                    "classifier_mode": _agent_mode(classifier),
                    "decision_tier": getattr(decision_agent, "last_tier", "disabled"),
                    "decision_reason": getattr(decision_agent, "last_reason", ""),
                    "params": asdict(params),
                })
            except Exception:
                pass
        if best_quality is None or quality.score > best_quality.score:
            best_quality = quality
            best_result_img = result_img.copy()
            best_params = PipelineParams(**asdict(params))
            best_iteration = iteration
        if quality.passed:
            break
        if iteration < max_iters:
            next_params = quality.suggested_params
            if (
                decision_agent is not None
                and decision_agent.enabled()
                and iteration >= config.DECISION_MIN_ITERATION
                and quality.score <= config.DECISION_SCORE_TRIGGER
            ):
                decision = decision_agent.suggest(
                    film_type=film_type.value,
                    iteration=iteration,
                    score=quality.score,
                    feedback=quality.feedback,
                    current_params=asdict(params),
                    evaluation_metrics=_agent_metrics(evaluator) or {},
                )
                if decision is not None:
                    reason, suggested = decision
                    tier = getattr(decision_agent, "last_tier", "unknown")
                    logger.info("Decision model override for %s via %s: %s", image_path.name, tier, reason)
                    decision_events.append({
                        "iteration": iteration,
                        "score": quality.score,
                        "tier": tier,
                        "reason": reason,
                        "suggested_params": asdict(suggested),
                    })
                    next_params = suggested
            params = next_params

    if result_img is None or quality is None or best_result_img is None or best_quality is None:
        raise RuntimeError(f"Pipeline did not produce an output for {image_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_image_path = output_dir / f"{image_path.stem}_processed.png"
    meta_path = output_dir / f"{image_path.stem}_meta.json"

    cv2.imwrite(str(out_image_path), best_result_img)
    meta = {
        "source": image_path.name,
        "film_type": film_type.value,
        "classifier_mode": _agent_mode(classifier),
        "evaluator_mode": _agent_mode(evaluator),
        "decision_model_enabled": bool(decision_agent is not None and decision_agent.enabled()),
        "decision_mode": getattr(decision_agent, "last_mode", "disabled"),
        "decision_events": decision_events,
        "iterations": iteration,
        "selected_iteration": best_iteration,
        "final_score": best_quality.score,
        "passed": best_quality.passed,
        "feedback": best_quality.feedback,
        "final_params": asdict(best_params),
        "output_image": out_image_path.name,
    }
    metrics = _agent_metrics(evaluator)
    if metrics is not None:
        meta["evaluation_metrics"] = metrics
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def process_directory(
    input_dir: Path,
    output_dir: Path,
    classifier: ClassifierAgent | None = None,
    processor: PostProcessingAgent | None = None,
    evaluator: EvaluatorAgent | None = None,
    decision_agent: DecisionAgent | None = None,
    max_iterations: int | None = None,
) -> list[dict[str, Any]]:
    """Process all images in a directory.

    For directories containing RAW files (DNG, ARW, etc.):
    - Looks for a flat-field file (``flat.dng`` or similar) in the directory.
    - Treats all other RAW files in the directory as frame inputs.
    - Routes each frame through ``process_raw_image``.

    For directories containing only standard images (JPEG/PNG):
    - Routes each image through the legacy ``process_image`` agent loop.
    """
    input_dir = Path(input_dir)
    results: list[dict[str, Any]] = []

    all_files = sorted(p for p in input_dir.iterdir() if p.is_file())

    # Check if directory contains RAW files.
    raw_files = [p for p in all_files if p.suffix.lower() in _RAW_EXTENSIONS]
    if raw_files:
        # Separate flat from frame files.
        flat_candidates = [p for p in raw_files if p.name.lower() in _FLAT_NAMES]
        flat_path: Path | None = flat_candidates[0] if flat_candidates else None
        frame_files = [p for p in raw_files if p not in flat_candidates]

        if flat_path is None:
            logger.warning(
                "RAW files found in %s but no flat-field file detected "
                "(expected one of %s). Skipping RAW processing.",
                input_dir, _FLAT_NAMES,
            )
        else:
            logger.info(
                "RAW batch: flat=%s, %d frame(s) to process.", flat_path.name, len(frame_files)
            )
            for frame_path in frame_files:
                try:
                    results.append(
                        process_raw_image(
                            frame_path=frame_path,
                            flat_path=flat_path,
                            output_dir=output_dir,
                            max_iterations=max_iterations,
                        )
                    )
                except Exception as exc:
                    logger.exception("Failed to process RAW frame %s", frame_path.name)
                    results.append({"source": frame_path.name, "error": str(exc)})
        return results

    # Legacy path: standard image files.
    classifier = classifier or ClassifierAgent()
    processor = processor or PostProcessingAgent()
    evaluator = evaluator or EvaluatorAgent()
    decision_agent = decision_agent or DecisionAgent()
    extensions = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
    images = sorted(p for p in all_files if p.suffix.lower() in extensions)

    for image_path in images:
        try:
            results.append(
                process_image(
                    image_path=image_path,
                    output_dir=output_dir,
                    classifier=classifier,
                    processor=processor,
                    evaluator=evaluator,
                    decision_agent=decision_agent,
                    max_iterations=max_iterations,
                )
            )
        except Exception as exc:
            logger.exception("Failed to process %s", image_path.name)
            results.append({"source": image_path.name, "error": str(exc)})

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Agentic film post-processing pipeline")
    parser.add_argument("--input-dir", type=Path, default=config.DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=config.OUTPUT_DIR)
    parser.add_argument("--max-iter", type=int, default=config.MAX_EVAL_ITERATIONS)
    # RAW-specific arguments.
    parser.add_argument(
        "--flat",
        type=Path,
        default=None,
        help="Explicit flat-field RAW file path (overrides auto-detection).",
    )
    parser.add_argument(
        "--leader",
        type=Path,
        default=None,
        help="Optional leader/calibration RAW frame for D-min/D-max calibration.",
    )
    parser.add_argument(
        "--frame",
        type=Path,
        default=None,
        help="Process a single RAW frame file (requires --flat).",
    )
    args = parser.parse_args()

    if args.frame is not None:
        # Single-frame RAW mode.
        if args.flat is None:
            flat_path = _find_flat_for_frame(args.frame)
            if flat_path is None:
                parser.error(
                    f"No flat-field file found for {args.frame.name}. "
                    f"Provide --flat explicitly."
                )
        else:
            flat_path = args.flat
        result = process_raw_image(
            frame_path=args.frame,
            flat_path=flat_path,
            output_dir=args.output_dir,
            leader_path=args.leader,
            max_iterations=args.max_iter,
        )
        print(json.dumps(result, indent=2))
    else:
        results = process_directory(args.input_dir, args.output_dir, max_iterations=args.max_iter)
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
