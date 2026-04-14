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


def _agent_mode(agent: object) -> str:
    mode = getattr(agent, "last_mode", "unknown")
    return mode if isinstance(mode, str) else "unknown"


def _agent_metrics(agent: object) -> dict[str, float] | None:
    metrics = getattr(agent, "last_metrics", None)
    return metrics if isinstance(metrics, dict) else None


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
    classifier = classifier or ClassifierAgent()
    processor = processor or PostProcessingAgent()
    evaluator = evaluator or EvaluatorAgent()
    decision_agent = decision_agent or DecisionAgent()
    max_iters = max_iterations if max_iterations is not None else config.MAX_EVAL_ITERATIONS

    image_path = Path(image_path)
    output_dir = Path(output_dir)

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
    input_dir = Path(input_dir)
    classifier = classifier or ClassifierAgent()
    processor = processor or PostProcessingAgent()
    evaluator = evaluator or EvaluatorAgent()
    decision_agent = decision_agent or DecisionAgent()
    extensions = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
    images = sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in extensions)
    results: list[dict[str, Any]] = []

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
    args = parser.parse_args()

    results = process_directory(args.input_dir, args.output_dir, max_iterations=args.max_iter)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
