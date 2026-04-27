"""Classification-routed processing adapter for the PC app server.

Phase 3 introduces an upfront classifier step. Based on the classification and
the state of the calibration store we dispatch to one of:

- ``negative_raw``    — ``Post_Processing_Negative/run_physical_correction.py``
                         subprocess on ``stitched_raw.dng`` when the
                         backlight/base-frame DNGs and reference layout are present.
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
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

import calibration_store
import flat_field_config as _flat_field_config
import flat_field_maps as _flat_field_maps

logger = logging.getLogger(__name__)

_SOFTWARE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SOFTWARE_DIR.parent
_AGENTIC_DIR = _REPO_ROOT / "Agentic_Post_Processing"
_NEGATIVE_DIR = _REPO_ROOT / "Post_Processing_Negative"

_PREVIEW_RUNNER = _AGENTIC_DIR / "_run_single.py"
_NEGATIVE_ENTRY = _NEGATIVE_DIR / "run_physical_correction.py"

_PREVIEW_RUNNER_NAME = "agentic_preview_runner"
_NEGATIVE_RAW_RUNNER_NAME = "post_processing_negative"
_POSITIVE_RAW_RUNNER_NAME = "post_processing_positive"
_INSTAX_PASSTHROUGH_RUNNER_NAME = "instax_direct_integration"
_POSITIVE_RUNNER_NAME = "positive_preview_pipeline"
_INSTAX_RUNNER_NAME = "instax_preview_pipeline"
_NEGATIVE_PREVIEW_RUNNER_NAME = "negative_preview_pipeline"

_NEGATIVE_RAW_MODE = "phase2_negative_raw"
_POSITIVE_RAW_MODE = "phase2_positive_raw"
_INSTAX_PASSTHROUGH_MODE = "phase2_instax_direct"
_NEGATIVE_PREVIEW_MODE = "phase3_negative_preview"
_POSITIVE_MODE = "phase3_positive_preview"
_INSTAX_MODE = "phase3_instax_preview"
_PREVIEW_FALLBACK_MODE = "phase1_preview_only"

_NEGATIVE_FINAL_SUFFIX = "_stage4_final_finish.png"
_POSITIVE_FINAL_SUFFIX = "_positive_final.png"

_POSITIVE_ENTRY = _NEGATIVE_DIR / "run_positive_correction.py"

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
        # Instax is intentionally disabled while flat-field-first routing is
        # being validated. Treat it as the positive branch so every scan goes
        # through the shared RAW flat-field correction boundary first.
        return "positive_film"
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


def _bbox_from_mask(mask: np.ndarray) -> list[int] | None:
    if mask is None or mask.size == 0:
        return None
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any() or not cols.any():
        return None
    y0 = int(np.argmax(rows))
    y1 = int(len(rows) - np.argmax(rows[::-1]))
    x0 = int(np.argmax(cols))
    x1 = int(len(cols) - np.argmax(cols[::-1]))
    return [x0, y0, x1, y1]


def _expand_bbox(
    bbox: list[int],
    width: int,
    height: int,
    margin_frac: float = 0.03,
) -> list[int]:
    x0, y0, x1, y1 = [int(v) for v in bbox]
    margin = int(round(max(x1 - x0, y1 - y0) * margin_frac))
    return [
        max(0, x0 - margin),
        max(0, y0 - margin),
        min(width, x1 + margin),
        min(height, y1 + margin),
    ]


def _detect_middle_film_bbox(preview_source: Path) -> dict[str, Any] | None:
    work = cv2.imread(str(preview_source), cv2.IMREAD_REDUCED_COLOR_8)
    if work is None:
        work = cv2.imread(str(preview_source), cv2.IMREAD_REDUCED_COLOR_4)
    if work is None:
        logger.warning("Could not load preview thumbnail for bbox detection: %s", preview_source)
        return None
    try:
        from PIL import Image  # type: ignore

        with Image.open(preview_source) as image:
            width, height = image.size
    except Exception:
        height, width = work.shape[0] * 8, work.shape[1] * 8

    if min(height, width) < 100:
        return None
    wh, ww = work.shape[:2]
    scale_x = ww / float(width)
    scale_y = wh / float(height)
    hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
    sat = hsv[..., 1]
    val = hsv[..., 2]
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    local_mean = cv2.blur(gray, (31, 31))
    texture = cv2.absdiff(gray, local_mean)

    mask = (((sat > 38) & (val > 20) & (val < 245)) | (texture > 18)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8), iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((13, 13), np.uint8), iterations=1)

    candidates: list[dict[str, Any]] = []
    image_area = float(ww * wh)
    center = np.array([ww / 2.0, wh / 2.0], dtype=np.float32)
    num_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    for label in range(1, num_labels):
        x, y, w, h, area = [int(v) for v in stats[label]]
        area_frac = (w * h) / image_area
        if area_frac < 0.025 or area_frac > 0.65:
            continue
        aspect = w / max(h, 1)
        if aspect < 0.35 or aspect > 4.0:
            continue
        candidate_center = np.array([x + w / 2.0, y + h / 2.0], dtype=np.float32)
        center_dist = float(np.linalg.norm((candidate_center - center) / np.array([ww, wh])))
        fill = float(area / max(w * h, 1))
        score = (1.0 - min(center_dist * 2.0, 1.0)) * 0.55 + min(area_frac / 0.18, 1.0) * 0.30 + fill * 0.15
        candidates.append(
            {
                "bbox_work": [int(x), int(y), int(x + w), int(y + h)],
                "score": float(score),
                "area_frac": float(area_frac),
                "center_distance": center_dist,
                "fill": fill,
            }
        )
    if not candidates:
        fallback = [
            int(round(width * 0.18)),
            int(round(height * 0.12)),
            int(round(width * 0.63)),
            int(round(height * 0.62)),
        ]
        return {
            "bbox": _expand_bbox(fallback, width, height, margin_frac=0.015),
            "score": 0.35,
            "mode": "middle_fixed_geometry_fallback",
            "candidate_count": 0,
            "coordinate_space": "preview_source_pixels",
            "width": int(width),
            "height": int(height),
        }

    chosen = max(candidates, key=lambda item: item["score"])
    x0, y0, x1, y1 = chosen["bbox_work"]
    bbox = [
        int(round(x0 / scale_x)),
        int(round(y0 / scale_y)),
        int(round(x1 / scale_x)),
        int(round(y1 / scale_y)),
    ]
    bbox = _expand_bbox(bbox, width, height)
    return {
        "bbox": bbox,
        "score": chosen["score"],
        "mode": "middle_preview_contour",
        "candidate_count": len(candidates),
        "coordinate_space": "preview_source_pixels",
        "width": int(width),
        "height": int(height),
    }


def _load_bbox_sidecar(preview_source: Path, output_dir: Path) -> dict[str, Any] | None:
    candidates = [
        output_dir / "bbox_detection.json",
        preview_source.parent / "bbox_detection.json",
        preview_source.parent / "detected_bboxes.json",
        preview_source.parent / "film_bbox.json",
        preview_source.with_name(f"{preview_source.stem}_bbox_detection.json"),
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read bbox sidecar %s: %s", candidate, exc)
            continue
        if isinstance(payload, dict):
            if not payload.get("base_candidate_bbox"):
                continue
            payload["film_content_bbox"] = None
            payload.setdefault("source", str(candidate))
            payload.setdefault("mode", "sidecar")
            return payload
    return None


_BASE_BBOX_PROMPT = """You are selecting a clean negative-film clear-base reference area from a scanned film tile preview.
Return ONLY valid JSON:
{"base_candidate_bbox":[x0,y0,x1,y1] or null, "confidence":0.0-1.0, "reason":"short"}

Task:
- Find a clean clear-base patch suitable for estimating the negative film base color.
- First locate the film frame edge: the boundary where the photographed image rectangle ends and the transparent film border/base begins.
- Then choose a small patch inside the film's white / pale / light translucent edge area, immediately outside the photographed image area but still inside the film material.
- Think of the target as the clear/tinted border around the image frame, not the scene/photo and not the outer sprocket/perforation strip.
- The best target often looks like a smooth pale gray, pale blue, pale cyan, or whitish translucent film edge next to the image frame.
- Prefer the smooth white/pale film edge strip over darker purple/pink areas near sprocket holes.
- The bbox must be inside transparent film material, not scanner background, not black border, and not the photographed image/content area.
- The clear-base patch is usually a translucent tinted strip at the edge of the film image, or a tinted gap between frames.
- Do NOT choose the bright white/cyan scanner backlight outside the physical film edge.
- Distinguish film white edge from scanner background: the film edge is attached to the film strip and lies between image content and perforations/border; scanner background is outside the film strip.
- Do NOT choose the black or very dark photographed scene area.
- Do NOT choose a patch containing printed marks such as "400D", frame numbers, edge numbers, or handwriting.
- Do NOT choose the sprocket/perforation strip or any patch adjacent to sprocket holes. Stay well away from holes and rounded perforation edges.
- Strong preference: smooth white/pale film-border strip directly beside the image frame edge, or a clean pale gap between adjacent image frames.
- The patch should be compact and local, roughly square or moderately rectangular. Do not return the whole strip or a long thin edge line.
- Avoid sprocket holes, perforations, frame holes, black border, dust, scratches, printed text/numbers, handwriting, dark image edges, trees/sky/objects, and strong glare spots.
- Do not cross boundaries: the box must not overlap holes, text, dark frame border, image content, or scanner background.
- If multiple possible patches exist, pick the cleanest, most uniform, lightest pale film-edge patch with no visible features.
- If only image content, holes, text, glare, or scanner background are visible, return null with low confidence.
- If unsure whether the candidate is film base or scanner background, return null.
- A good bbox should contain mostly smooth pale clear-base pixels and should be several hole-widths away from any sprocket/perforation hole.
- Coordinates must be in the input image pixel coordinate system.
Do not return a film/content bounding box; only return the clean base patch bbox.
"""


def _validate_bbox(
    bbox: Any,
    *,
    width: int,
    height: int,
    max_area_fraction: float = 0.25,
) -> list[int] | None:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        x0, y0, x1, y1 = [int(round(float(v))) for v in bbox]
    except Exception:
        return None
    x0 = max(0, min(x0, width - 2))
    y0 = max(0, min(y0, height - 2))
    x1 = max(x0 + 2, min(x1, width))
    y1 = max(y0 + 2, min(y1, height))
    bw = x1 - x0
    bh = y1 - y0
    area_fraction = (bw * bh) / float(max(width * height, 1))
    aspect = max(bw / max(bh, 1), bh / max(bw, 1))
    min_side = max(12, int(round(min(width, height) * 0.025)))
    if area_fraction <= 0.0002 or area_fraction > max_area_fraction:
        return None
    if bw < min_side or bh < min_side:
        return None
    if aspect > 6.0:
        return None
    return [x0, y0, x1, y1]


def _validate_base_patch_pixels(preview_source: Path, bbox: list[int] | None) -> tuple[list[int] | None, str | None]:
    if not bbox:
        return None, "missing_bbox"
    image_bgr = cv2.imread(str(preview_source), cv2.IMREAD_COLOR)
    if image_bgr is None:
        return bbox, None
    height, width = image_bgr.shape[:2]
    x0, y0, x1, y1 = [int(v) for v in bbox]
    warnings: list[str] = []
    # Clear-base strips can be at tile boundaries. Do not reject solely for
    # touching an edge; reject later only if the patch is white scanner
    # background, dark image content, or textured/printed content.
    edge_margin = max(8, int(round(min(width, height) * 0.015)))
    if x0 <= edge_margin or y0 <= edge_margin or x1 >= width - edge_margin or y1 >= height - edge_margin:
        warnings.append("bbox_touches_preview_boundary")

    patch = image_bgr[y0:y1, x0:x1]
    if patch.size == 0:
        return None, "empty_patch"
    pad = max(12, int(round(min(width, height) * 0.025)))
    nx0 = max(0, x0 - pad)
    ny0 = max(0, y0 - pad)
    nx1 = min(width, x1 + pad)
    ny1 = min(height, y1 + pad)
    neighborhood = image_bgr[ny0:ny1, nx0:nx1]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    value = hsv[..., 2].astype(np.float32)
    sat = hsv[..., 1].astype(np.float32)
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    mean_v = float(value.mean())
    mean_s = float(sat.mean())
    texture = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    dark_frac = float((value < 28).mean())
    bright_frac = float((value > 245).mean())
    if neighborhood.size:
        n_hsv = cv2.cvtColor(neighborhood, cv2.COLOR_BGR2HSV)
        n_value = n_hsv[..., 2].astype(np.float32)
        n_gray = cv2.cvtColor(neighborhood, cv2.COLOR_BGR2GRAY)
        n_dark_frac = float((n_value < 18).mean())
        n_bright_frac = float((n_value > 248).mean())
        n_texture = float(cv2.Laplacian(n_gray, cv2.CV_32F).var())
    else:
        n_dark_frac = n_bright_frac = n_texture = 0.0

    if dark_frac > 0.18 or mean_v < 35:
        return None, "patch_too_dark_for_clear_base"
    if bright_frac > 0.28 and mean_s < 45:
        return None, "patch_looks_like_white_scanner_background"
    if texture > 2600:
        return None, "patch_has_text_or_image_texture"
    if (n_dark_frac > 0.18 and n_bright_frac > 0.08) or n_texture > 6200:
        return None, "patch_too_close_to_hole_or_printed_edge"
    if mean_s < 8 and mean_v > 210:
        return None, "patch_too_neutral_bright_background"
    return bbox, ";".join(warnings) if warnings else None


def _detect_base_bbox_with_vlm(preview_source: Path, output_dir: Path) -> dict[str, Any] | None:
    try:
        from PIL import Image  # type: ignore

        with Image.open(preview_source) as image:
            width, height = image.size
    except Exception:
        image_bgr = cv2.imread(str(preview_source), cv2.IMREAD_COLOR)
        if image_bgr is None:
            return None
        height, width = image_bgr.shape[:2]

    try:
        _ensure_agentic_on_path()
        import config as agent_config  # type: ignore
        from agents.classifier import ClassifierAgent, _image_part, _response_text  # type: ignore

        agent = ClassifierAgent()
        if getattr(agent, "_model", None) is None:
            return None
        image_input = preview_source if agent_config.MODEL_PROVIDER == "openai_compatible" else _image_part(preview_source)
        response = agent._model.generate_content([_BASE_BBOX_PROMPT, image_input])
        payload = json.loads(_response_text(response).strip())
    except Exception as exc:
        logger.info("VLM base bbox detection unavailable for %s: %s", preview_source.name, exc)
        return None

    bbox = _validate_bbox(payload.get("base_candidate_bbox"), width=int(width), height=int(height))
    bbox, validation_reason = _validate_base_patch_pixels(preview_source, bbox)
    try:
        confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.0))))
    except Exception:
        confidence = 0.0
    result = {
        "mode": "vlm_base_bbox",
        "coordinate_space": "preview_source_pixels",
        "preview_source": str(preview_source),
        "preview_width": int(width),
        "preview_height": int(height),
        "film_content_bbox": None,
        "base_candidate_bbox": bbox,
        "confidence": confidence if bbox else 0.0,
        "reason": str(payload.get("reason", ""))[:500],
        "validation_rejection_reason": validation_reason,
        "raw_payload": payload,
    }
    (output_dir / "bbox_detection.json").write_text(
        json.dumps(result, indent=2),
        encoding="utf-8",
    )
    return result


def _detect_base_bbox_cv_edge_fallback(preview_source: Path, output_dir: Path, reason: str | None = None) -> dict[str, Any] | None:
    image_bgr = cv2.imread(str(preview_source), cv2.IMREAD_COLOR)
    if image_bgr is None:
        return None
    height, width = image_bgr.shape[:2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    value = hsv[..., 2].astype(np.float32)
    sat = hsv[..., 1].astype(np.float32)

    win = max(36, int(round(min(width, height) * 0.05)))
    stride = max(12, win // 3)
    candidates: list[tuple[float, list[int], dict[str, float]]] = []
    margin = max(24, int(round(min(width, height) * 0.045)))
    inner_x0 = int(round(width * 0.06))
    inner_x1 = int(round(width * 0.94))
    inner_y0 = int(round(height * 0.06))
    inner_y1 = int(round(height * 0.94))
    for y in range(margin, max(margin + 1, height - win - margin), stride):
        for x in range(margin, max(margin + 1, width - win - margin), stride):
            x1 = x + win
            y1 = y + win
            if x < inner_x0 or y < inner_y0 or x1 > inner_x1 or y1 > inner_y1:
                continue
            patch_v = value[y:y1, x:x1]
            patch_s = sat[y:y1, x:x1]
            patch_gray = gray[y:y1, x:x1]
            mean_v = float(patch_v.mean())
            mean_s = float(patch_s.mean())
            dark_frac = float((patch_v < 28).mean())
            bright_frac = float((patch_v > 245).mean())
            texture = float(cv2.Laplacian(patch_gray, cv2.CV_32F).var())
            if mean_v < 35 or dark_frac > 0.12:
                continue
            if bright_frac > 0.18 and mean_s < 55:
                continue
            if texture > 1800:
                continue
            # Prefer moderately saturated tinted base near image-frame edges,
            # not pure white scanner background and not highly textured image.
            if mean_s < 12 or mean_s > 130:
                continue
            # Nearby strong black/white alternation usually indicates holes or
            # printed marks. Keep this weaker than the VLM gate so it can rescue
            # otherwise usable edge patches.
            pad = win // 2
            nx0 = max(0, x - pad)
            ny0 = max(0, y - pad)
            nx1 = min(width, x1 + pad)
            ny1 = min(height, y1 + pad)
            n_v = value[ny0:ny1, nx0:nx1]
            n_dark = float((n_v < 18).mean())
            n_bright = float((n_v > 248).mean())
            if n_dark > 0.20 and n_bright > 0.10:
                continue
            # Avoid the extreme tile periphery: it is often scanner/background
            # edge instead of usable film-base edge.
            edge_distance = min(x / width, y / height, (width - x1) / width, (height - y1) / height)
            edge_penalty = max(0.0, 0.12 - edge_distance) * 2.5
            center_penalty = abs((x + win / 2) / width - 0.5) + abs((y + win / 2) / height - 0.5)
            score = (
                1.0 / (1.0 + texture / 400.0)
                + min(mean_s / 60.0, 1.0) * 0.35
                + min(mean_v / 180.0, 1.0) * 0.25
                - center_penalty * 0.06
                - edge_penalty
            )
            candidates.append(
                (
                    float(score),
                    [int(x), int(y), int(x1), int(y1)],
                    {
                        "mean_value": mean_v,
                        "mean_saturation": mean_s,
                        "texture": texture,
                        "dark_fraction": dark_frac,
                        "bright_fraction": bright_frac,
                    },
                )
            )
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _score, bbox, metrics = candidates[0]
    payload = {
        "mode": "cv_base_edge_fallback",
        "coordinate_space": "preview_source_pixels",
        "preview_source": str(preview_source),
        "preview_width": int(width),
        "preview_height": int(height),
        "film_content_bbox": None,
        "base_candidate_bbox": bbox,
        "confidence": 0.35,
        "reason": "VLM returned no clean base; selected lowest-texture tinted edge-like patch by CV fallback.",
        "vlm_rejection_reason": reason,
        "candidate_metrics": metrics,
    }
    (output_dir / "bbox_detection.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    return payload


def _detect_preview_bboxes(preview_source: Path, output_dir: Path) -> dict[str, Any] | None:
    """Attach clean base bbox metadata without changing physical RAW math."""
    sidecar = _load_bbox_sidecar(preview_source, output_dir)
    if sidecar is not None:
        return sidecar

    detector_mode = os.getenv("BASE_BBOX_DETECTOR", "cv_edge").strip().lower()
    if detector_mode in {"cv_edge", "cv_edge_only", "cv_fallback"}:
        fallback = _detect_base_bbox_cv_edge_fallback(preview_source, output_dir, reason="default_cv_edge")
        if fallback is not None:
            return fallback
        return {
            "mode": "cv_base_edge_fallback",
            "coordinate_space": "preview_source_pixels",
            "preview_source": str(preview_source),
            "film_content_bbox": None,
            "base_candidate_bbox": None,
            "confidence": 0.0,
            "reason": "No CV edge-base candidate passed validation.",
        }

    if detector_mode not in {"cv", "cv_only"}:
        vlm_payload = _detect_base_bbox_with_vlm(preview_source, output_dir)
        if vlm_payload is not None:
            if not vlm_payload.get("base_candidate_bbox") and os.getenv("BASE_BBOX_ENABLE_CV_FALLBACK", "1").strip().lower() not in {"0", "false", "off"}:
                fallback = _detect_base_bbox_cv_edge_fallback(
                    preview_source,
                    output_dir,
                    reason=vlm_payload.get("reason") or vlm_payload.get("validation_rejection_reason"),
                )
                if fallback is not None:
                    fallback["vlm_payload"] = vlm_payload
                    return fallback
            return vlm_payload

    try:
        _ensure_agentic_on_path()
        from raw_pipeline.border_detection import detect_border_regions  # type: ignore
    except Exception as exc:
        logger.debug("BBox detection unavailable: %s", exc)
        payload = {"mode": "unavailable", "error": str(exc), "film_content_bbox": None, "base_candidate_bbox": None}
        (output_dir / "bbox_detection.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        return payload

    image_bgr = cv2.imread(str(preview_source), cv2.IMREAD_COLOR)
    if image_bgr is None:
        return {"mode": "error", "error": f"Could not read preview: {preview_source}"}
    height, width = image_bgr.shape[:2]

    try:
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        detected = detect_border_regions(image_rgb)
        base_bbox = _bbox_from_mask(detected.clearbase_candidate_mask)
        base_bbox = _validate_bbox(base_bbox, width=width, height=height, max_area_fraction=0.45)
        payload = {
            "mode": "cv_preview_border_detection",
            "coordinate_space": "preview_source_pixels",
            "preview_source": str(preview_source),
            "preview_width": int(width),
            "preview_height": int(height),
            "film_content_bbox": None,
            "base_candidate_bbox": base_bbox,
            "confidence": float(detected.confidence),
            "method_votes": detected.method_votes,
        }
        (output_dir / "bbox_detection.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        return payload
    except Exception as exc:
        logger.warning("Preview bbox detection failed: %s", exc)
        return {"mode": "error", "error": str(exc)}


def _map_preview_bbox_to_raw(
    bbox: list[int] | None,
    *,
    preview_source: Path,
    raw_source: Path,
) -> list[int] | None:
    if not bbox:
        return None
    try:
        from PIL import Image  # type: ignore

        with Image.open(preview_source) as image:
            preview_w, preview_h = image.size
    except Exception as exc:
        logger.warning("Could not inspect preview dimensions for bbox mapping: %s", exc)
        return None
    raw_w, raw_h = _read_raw_dimensions(raw_source)
    if raw_w is None or raw_h is None:
        logger.warning("Could not inspect RAW dimensions for bbox mapping: %s", raw_source)
        return None

    sx = raw_w / max(preview_w, 1)
    sy = raw_h / max(preview_h, 1)
    x0, y0, x1, y1 = bbox
    mapped = [
        int(round(x0 * sx)),
        int(round(y0 * sy)),
        int(round(x1 * sx)),
        int(round(y1 * sy)),
    ]
    mapped[0] = max(0, min(mapped[0], raw_w - 2))
    mapped[1] = max(0, min(mapped[1], raw_h - 2))
    mapped[2] = max(mapped[0] + 2, min(mapped[2], raw_w))
    mapped[3] = max(mapped[1] + 2, min(mapped[3], raw_h))
    mapped[0] -= mapped[0] % 2
    mapped[1] -= mapped[1] % 2
    mapped[2] -= mapped[2] % 2
    mapped[3] -= mapped[3] % 2
    return mapped


def _read_raw_dimensions(raw_source: Path) -> tuple[int | None, int | None]:
    try:
        import rawpy  # type: ignore

        with rawpy.imread(str(raw_source)) as raw:
            raw_h, raw_w = raw.raw_image_visible.shape
            return int(raw_w), int(raw_h)
    except Exception:
        pass
    try:
        import tifffile  # type: ignore

        with tifffile.TiffFile(str(raw_source)) as tif:
            page = tif.pages[0]
            return int(page.imagewidth), int(page.imagelength)
    except Exception:
        return None, None


def _dng_dimensions(path: Path | None) -> tuple[int | None, int | None]:
    if path is None:
        return None, None
    return _read_raw_dimensions(path)


class _ClassifierResult:
    __slots__ = ("classification", "raw_label", "mode", "error", "confidence")

    def __init__(
        self,
        classification: str | None,
        raw_label: str | None,
        mode: str,
        error: str | None = None,
        confidence: float | None = None,
    ) -> None:
        self.classification = classification
        self.raw_label = raw_label
        self.mode = mode
        self.error = error
        self.confidence = confidence


class ProcessingAdapter:
    """Classification-first router for post-scan processing."""

    def run(
        self,
        canonical_source: Path | None,
        preview_source: Path,
        output_dir: Path,
        progress_cb: Callable[[dict], None] | None = None,
        classifier_result: _ClassifierResult | None = None,
        bbox_metadata: dict[str, Any] | None = None,
        reference_layout_path: Path | None = None,
        global_params_path: Path | None = None,
    ) -> dict:
        output_dir.mkdir(parents=True, exist_ok=True)

        if not preview_source.exists():
            raise RuntimeError(f"Preview source not found: {preview_source}")

        if classifier_result is None:
            classifier_result = self._classify(preview_source)

        # Bbox detection is only needed by the negative RAW branch (for film-content crop).
        # Positive film and instax require no detection, classification, or inversion.
        if bbox_metadata is None and classifier_result.classification == "negative_film":
            bbox_metadata = _detect_preview_bboxes(preview_source, output_dir)

        if progress_cb:
            progress_cb(
                {
                    "classification": classifier_result.classification,
                    "classifier_raw_label": classifier_result.raw_label,
                    "classifier_mode": classifier_result.mode,
                    "bbox_detection": bbox_metadata,
                    "stage_hint": "classify_done",
                }
            )

        negative_raw_compatibility = (
            self._negative_raw_compatibility(canonical_source, reference_layout_path, global_params_path)
            if classifier_result.classification == "negative_film"
            else None
        )
        positive_raw_compatibility = (
            self._positive_raw_compatibility(canonical_source)
            if classifier_result.classification == "positive_film"
            else None
        )
        branch = self._select_branch(classifier_result, negative_raw_compatibility, positive_raw_compatibility)

        if branch == "negative_raw":
            backlight_dng = self._select_backlight_for_raw_run(canonical_source)
            base_frame_dng = calibration_store.get_dng_path_if_ready(calibration_store.BASE_FRAME)
            assert canonical_source is not None and backlight_dng is not None
            base_frame_for_run, base_frame_usage = self._select_base_frame_for_raw_run(
                frame_dng=canonical_source,
                base_frame_dng=base_frame_dng,
            )
            return self._run_negative_raw_branch(
                frame_dng=canonical_source,
                backlight_dng=backlight_dng,
                base_frame_dng=base_frame_for_run,
                base_frame_usage=base_frame_usage,
                preview_source=preview_source,
                output_dir=output_dir,
                classifier_result=classifier_result,
                bbox_metadata=bbox_metadata,
                reference_layout_path=reference_layout_path,
                global_params_path=global_params_path,
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
                bbox_metadata=bbox_metadata,
                progress_cb=progress_cb,
            )

        if branch == "positive_raw":
            backlight_dng = self._select_backlight_for_raw_run(canonical_source)
            assert canonical_source is not None and backlight_dng is not None
            return self._run_positive_raw_branch(
                frame_dng=canonical_source,
                backlight_dng=backlight_dng,
                output_dir=output_dir,
                classifier_result=classifier_result,
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
                bbox_metadata=None,
                progress_cb=progress_cb,
            )

        if branch == "instax":
            return self._run_instax_passthrough(
                canonical_source=canonical_source,
                preview_source=preview_source,
                output_dir=output_dir,
                classifier_result=classifier_result,
                progress_cb=progress_cb,
            )

        return self._run_preview_fallback(
            canonical_source=canonical_source,
            preview_source=preview_source,
            output_dir=output_dir,
            classifier_result=classifier_result,
            bbox_metadata=bbox_metadata,
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
        if normalized == "positive_film" and os.getenv("ENABLE_INSTAX_CLASSIFICATION", "0").strip().lower() not in {"0", "false", "off"}:
            try:
                image_bgr = cv2.imread(str(preview_source), cv2.IMREAD_COLOR)
                if image_bgr is not None and _looks_like_instax(image_bgr):
                    normalized = "instax_instant_film"
            except Exception as exc:
                logger.debug("Instax heuristic skipped: %s", exc)

        return _ClassifierResult(
            normalized,
            raw_label,
            getattr(agent, "last_mode", "unknown"),
            confidence=getattr(agent, "last_confidence", None),
        )

    # Branch selection ------------------------------------------------------

    def _select_branch(
        self,
        classifier_result: _ClassifierResult,
        negative_raw_compatibility: dict | None,
        positive_raw_compatibility: dict | None = None,
    ) -> str:
        classification = classifier_result.classification
        if classification == "negative_film":
            if negative_raw_compatibility and negative_raw_compatibility.get("ok"):
                return "negative_raw"
            return "negative_preview"
        if classification == "positive_film":
            if positive_raw_compatibility and positive_raw_compatibility.get("ok"):
                return "positive_raw"
            return "positive"
        if classification == "instax_instant_film":
            return "instax"
        return "fallback"

    def _negative_raw_compatibility(
        self,
        canonical_source: Path | None,
        reference_layout_path: Path | None = None,
        global_params_path: Path | None = None,
    ) -> dict:
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

        backlight = self._select_backlight_for_raw_run(canonical_source)
        base_frame = calibration_store.get_dng_path_if_ready(calibration_store.BASE_FRAME)
        has_global_base = bool(global_params_path and global_params_path.exists())
        roi_path = reference_layout_path or (calibration_store.dng_path(calibration_store.BASE_FRAME).parent / "reference_layout.json")
        missing = []
        if backlight is None:
            missing.append("backlight")
        if not has_global_base and not roi_path.exists():
            missing.append("reference_layout")
        if missing:
            return {
                "ok": False,
                "reason": "missing_calibration_asset",
                "missing_calibrations": missing,
            }

        frame_sig = calibration_store.dng_signature(canonical_source)
        backlight_sig = calibration_store.dng_signature(backlight)
        base_frame_sig = calibration_store.dng_signature(base_frame or canonical_source)
        ok, reason = calibration_store.signatures_compatible(
            frame_sig,
            backlight_sig,
        )
        base_dims_match = (
            frame_sig.get("width") == base_frame_sig.get("width")
            and frame_sig.get("height") == base_frame_sig.get("height")
            and frame_sig.get("raw_pattern") == base_frame_sig.get("raw_pattern")
            and frame_sig.get("color_desc") == base_frame_sig.get("color_desc")
        )
        return {
            "ok": ok,
            "reason": reason,
            "missing_calibrations": [],
            "frame_signature": frame_sig,
            "backlight_signature": backlight_sig,
            "base_frame_signature": base_frame_sig,
            "base_frame_usage": (
                "calibration_base_frame"
                if base_dims_match
                else "current_scan_base_reference_due_base_size_mismatch"
            ),
            "base_frame_roi_path": str(roi_path) if not has_global_base else None,
            "base_detection": "global_base_rgb" if has_global_base else "reference_layout",
            "global_params_path": str(global_params_path) if has_global_base else None,
        }

    def _select_backlight_for_raw_run(self, frame_dng: Path | None) -> Path | None:
        """Prefer same-position backlight tile, then legacy single DNG.

        The current scanner strategy processes individual DNG tiles and only
        integrates after processing. In that mode the correct flat-field frame is
        the backlight tile with the same row/col stem.
        """
        if frame_dng is not None:
            tile_backlight = calibration_store.matching_tile_dng(calibration_store.BACKLIGHT, frame_dng)
            if tile_backlight is not None:
                return tile_backlight
        return calibration_store.get_dng_path_if_ready(calibration_store.BACKLIGHT)

    def _select_base_frame_for_raw_run(
        self,
        *,
        frame_dng: Path,
        base_frame_dng: Path | None,
    ) -> tuple[Path, str]:
        frame_dims = _dng_dimensions(frame_dng)
        base_dims = _dng_dimensions(base_frame_dng)
        if base_frame_dng is not None and frame_dims == base_dims:
            return base_frame_dng, "calibration_base_frame"
        return frame_dng, "current_scan_base_reference_due_base_size_mismatch"

    # Positive RAW compatibility check --------------------------------------

    def _positive_raw_compatibility(self, canonical_source: Path | None) -> dict:
        if canonical_source is None or not canonical_source.exists():
            return {"ok": False, "reason": "missing_canonical_dng"}
        if canonical_source.suffix.lower() != ".dng":
            return {"ok": False, "reason": "canonical_source_is_not_dng"}
        backlight = self._select_backlight_for_raw_run(canonical_source)
        if backlight is None:
            return {"ok": False, "reason": "missing_backlight", "missing_calibrations": ["backlight"]}
        frame_sig = calibration_store.dng_signature(canonical_source)
        backlight_sig = calibration_store.dng_signature(backlight)
        ok, reason = calibration_store.signatures_compatible(frame_sig, backlight_sig)
        return {
            "ok": ok,
            "reason": reason,
            "frame_signature": frame_sig,
            "backlight_signature": backlight_sig,
        }

    # Positive RAW branch (subprocess) --------------------------------------

    def _run_positive_raw_branch(
        self,
        frame_dng: Path,
        backlight_dng: Path,
        output_dir: Path,
        classifier_result: _ClassifierResult,
        progress_cb: Callable[[dict], None] | None = None,
    ) -> dict:
        if not _POSITIVE_ENTRY.exists():
            raise RuntimeError(f"Positive branch entry not found: {_POSITIVE_ENTRY}")

        if progress_cb:
            progress_cb(
                {
                    "classification": "positive_film",
                    "classifier_raw_label": classifier_result.raw_label or "positive_film",
                    "classifier_mode": classifier_result.mode,
                    "selected_branch": "positive",
                    "runner_used": _POSITIVE_RAW_RUNNER_NAME,
                    "stage_hint": "positive_branch_start",
                }
            )

        ff = _flat_field_config.load()
        cmd = [
            sys.executable,
            str(_POSITIVE_ENTRY),
            "--frame", str(frame_dng),
            "--backlight-frame", str(backlight_dng),
            "--input-dir", str(frame_dng.parent),
            "--output-dir", str(output_dir),
            "--skip-flat-corrected-preview",
            "--flat-strength", str(ff["strength"]),
            "--flat-sigma-frac", str(ff["sigma_frac"]),
            "--flat-max-side", str(ff["max_side"]),
        ]

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
            tail = "\n".join(stderr_lines[-15:]) or "Positive branch subprocess failed"
            raise RuntimeError(f"Positive branch failed: {tail}")

        final_path = self._locate_positive_raw_final(frame_dng, output_dir)
        report_path = output_dir / "positive_correction_report.json"

        metadata = {
            "classification": "positive_film",
            "classifier_raw_label": classifier_result.raw_label or "positive_film",
            "classifier_mode": classifier_result.mode,
            "selected_branch": "positive",
            "runner_used": _POSITIVE_RAW_RUNNER_NAME,
            "processing_mode": _POSITIVE_RAW_MODE,
            "processing_input_kind": "single_tile_dng",
            "processing_input_path": str(frame_dng),
            "canonical_source": str(frame_dng),
            "backlight_dng_path": str(backlight_dng),
            "final_output_path": str(final_path) if final_path else None,
            "positive_branch_report": report_path.name if report_path.exists() else None,
            "subprocess_stderr_tail": stderr_lines[-10:],
        }

        meta_file = output_dir / f"{frame_dng.stem}_meta.json"
        meta_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        if progress_cb:
            progress_cb(
                {
                    "classification": "positive_film",
                    "classifier_mode": classifier_result.mode,
                    "selected_branch": "positive",
                    "runner_used": _POSITIVE_RAW_RUNNER_NAME,
                    "iteration": 1,
                    "max_iter": 1,
                    "score": 1.0,
                }
            )

        return {
            "classification": "positive_film",
            "classifier_raw_label": classifier_result.raw_label or "positive_film",
            "classifier_mode": classifier_result.mode,
            "selected_branch": "positive",
            "runner_used": _POSITIVE_RAW_RUNNER_NAME,
            "score": 1.0,
            "final_output_path": str(final_path) if final_path else None,
            "metadata_path": str(meta_file),
        }

    def _locate_positive_raw_final(self, frame_dng: Path, output_dir: Path) -> Path | None:
        exact = output_dir / f"{frame_dng.stem}{_POSITIVE_FINAL_SUFFIX}"
        if exact.exists():
            return exact
        matches = sorted(output_dir.glob(f"*{_POSITIVE_FINAL_SUFFIX}"))
        return matches[-1] if matches else None

    # Instax passthrough (direct integration — no per-tile processing) ------

    def _run_instax_passthrough(
        self,
        canonical_source: Path | None,
        preview_source: Path,
        output_dir: Path,
        classifier_result: _ClassifierResult,
        progress_cb: Callable[[dict], None] | None = None,
    ) -> dict:
        import shutil

        output_dir.mkdir(parents=True, exist_ok=True)
        stem = preview_source.stem

        if progress_cb:
            progress_cb(
                {
                    "classification": "instax_instant_film",
                    "classifier_raw_label": classifier_result.raw_label,
                    "classifier_mode": classifier_result.mode,
                    "selected_branch": "instax",
                    "runner_used": _INSTAX_PASSTHROUGH_RUNNER_NAME,
                    "stage_hint": "instax_passthrough",
                }
            )

        final_path: Path | None = None
        input_kind = "preview_copy"

        # Prefer full-resolution DNG via rawpy's default postprocess.
        # rawpy applies the camera white balance and basic demosaic automatically.
        if (
            canonical_source is not None
            and canonical_source.exists()
            and canonical_source.suffix.lower() == ".dng"
        ):
            try:
                import rawpy  # type: ignore

                with rawpy.imread(str(canonical_source)) as raw:
                    rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
                bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                final_path = output_dir / f"{stem}_instax_direct.png"
                if not cv2.imwrite(str(final_path), bgr):
                    final_path = None
                else:
                    input_kind = "dng_rawpy_postprocess"
            except Exception as exc:
                logger.warning("Instax rawpy passthrough failed, falling back to preview copy: %s", exc)
                final_path = None

        if final_path is None or not final_path.exists():
            final_path = output_dir / f"{stem}_instax_direct.jpg"
            shutil.copy2(preview_source, final_path)
            input_kind = "preview_copy"

        metadata = {
            "classification": "instax_instant_film",
            "classifier_raw_label": classifier_result.raw_label,
            "classifier_mode": classifier_result.mode,
            "selected_branch": "instax",
            "runner_used": _INSTAX_PASSTHROUGH_RUNNER_NAME,
            "processing_mode": _INSTAX_PASSTHROUGH_MODE,
            "processing_input_kind": input_kind,
            "canonical_source": str(canonical_source) if canonical_source else None,
            "preview_source": str(preview_source),
            "final_output_path": str(final_path),
            "output_image": final_path.name,
        }

        meta_file = output_dir / f"{stem}_meta.json"
        meta_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        if progress_cb:
            progress_cb(
                {
                    "classification": "instax_instant_film",
                    "classifier_mode": classifier_result.mode,
                    "selected_branch": "instax",
                    "runner_used": _INSTAX_PASSTHROUGH_RUNNER_NAME,
                    "iteration": 1,
                    "max_iter": 1,
                    "score": 1.0,
                }
            )

        return {
            "classification": "instax_instant_film",
            "classifier_raw_label": classifier_result.raw_label,
            "classifier_mode": classifier_result.mode,
            "selected_branch": "instax",
            "runner_used": _INSTAX_PASSTHROUGH_RUNNER_NAME,
            "score": 1.0,
            "raw_preview_path": str(preview_source),
            "final_output_path": str(final_path),
            "metadata_path": str(meta_file),
        }

    # Negative RAW branch (subprocess) --------------------------------------

    def _run_negative_raw_branch(
        self,
        frame_dng: Path,
        backlight_dng: Path,
        base_frame_dng: Path,
        base_frame_usage: str,
        preview_source: Path,
        output_dir: Path,
        classifier_result: _ClassifierResult,
        bbox_metadata: dict[str, Any] | None,
        reference_layout_path: Path | None = None,
        global_params_path: Path | None = None,
        progress_cb: Callable[[dict], None] | None = None,
    ) -> dict:
        if not _NEGATIVE_ENTRY.exists():
            raise RuntimeError(f"Negative branch entry not found: {_NEGATIVE_ENTRY}")

        film_content_bbox = None
        if bbox_metadata:
            film_content_bbox = _map_preview_bbox_to_raw(
                bbox_metadata.get("film_content_bbox"),
                preview_source=preview_source,
                raw_source=frame_dng,
            )

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
            "--input-dir",
            str(frame_dng.parent),
            "--output-dir",
            str(output_dir),
            "--base-frame",
            str(base_frame_dng),
            "--reference-layout",
            str(reference_layout_path or (calibration_store.dng_path(calibration_store.BASE_FRAME).parent / "reference_layout.json")),
            "--skip-npy",
            "--skip-linear16",
            "--skip-stage2-debug-png",
            "--skip-stage3-debug-png",
            "--skip-stage4-debug-png",
            "--skip-intermediate-previews",
            "--skip-flat-corrected-preview",
        ]
        ff = _flat_field_config.load()
        cmd += [
            "--flat-strength", str(ff["strength"]),
            "--flat-sigma-frac", str(ff["sigma_frac"]),
            "--flat-max-side", str(ff["max_side"]),
        ]
        if global_params_path is not None:
            cmd += ["--global-params-json", str(global_params_path)]
        if film_content_bbox is not None:
            cmd += ["--film-content-bbox", ",".join(str(v) for v in film_content_bbox)]
        _flat_field_maps.ensure_current()
        flat_map_paths = _flat_field_maps.matching_map_paths(frame_dng)
        if flat_map_paths is not None:
            flat_map_path, flat_map_metadata_path = flat_map_paths
            cmd += ["--flat-map", str(flat_map_path), "--flat-map-metadata", str(flat_map_metadata_path)]
        else:
            flat_map_path = None
            flat_map_metadata_path = None

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
            "processing_input_kind": "single_tile_dng" if frame_dng.parent.name == "tiles" else "stitched_raw_dng",
            "processing_input_path": str(frame_dng),
            "canonical_source": str(frame_dng),
            "canonical_source_available": True,
            "preview_source": str(preview_source),
            "backlight_dng_path": str(backlight_dng),
            "flat_map_path": str(flat_map_path) if flat_map_path else None,
            "flat_map_metadata_path": str(flat_map_metadata_path) if flat_map_metadata_path else None,
            "base_frame_dng_path": str(base_frame_dng),
            "base_reference_mode": base_frame_usage,
            "reference_layout_path": str(reference_layout_path) if reference_layout_path else None,
            "global_params_path": str(global_params_path) if global_params_path else None,
            "bbox_detection": bbox_metadata,
            "film_content_bbox_raw": film_content_bbox,
            "backlight_crop_mode": "same_film_content_bbox" if film_content_bbox else None,
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
            "film_content_bbox_raw": film_content_bbox,
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
        bbox_metadata: dict[str, Any] | None = None,
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
            "bbox_detection": bbox_metadata,
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
        bbox_metadata: dict[str, Any] | None,
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
            "bbox_detection": bbox_metadata,
            "upstream_classifier_error": classifier_result.error,
        }

        meta_file = output_dir / f"{stem}_meta.json"
        meta_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        result["metadata_path"] = str(meta_file)
        return result
