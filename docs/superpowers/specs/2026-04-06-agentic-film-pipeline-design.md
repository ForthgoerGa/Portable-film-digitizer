# Agentic Film Post-Processing Pipeline — Design Spec

**Date:** 2026-04-06  
**Status:** Approved  
**Location:** `Agentic_Post_Processing/`

---

## Overview

A sequential agentic pipeline that takes a single full-resolution film scan, classifies the film type using Gemini vision, routes it to the appropriate post-processing pipeline, and evaluates quality with an automated feedback loop.

---

## Supported Film Types

| FilmType enum         | Description                        |
|-----------------------|------------------------------------|
| `NEGATIVE_35MM`       | 35mm color negative (orange base)  |
| `NEGATIVE_120`        | 120 medium format color negative   |
| `POSITIVE_35MM`       | 35mm slide / transparency          |
| `POSITIVE_120`        | 120 medium format slide            |

---

## File Layout

```
Agentic_Post_Processing/
├── agents/
│   ├── __init__.py
│   ├── classifier.py       # ClassifierAgent — Gemini vision, returns FilmType
│   ├── processor.py        # PostProcessingAgent — routes to pipeline, returns image
│   └── evaluator.py        # EvaluatorAgent — Gemini vision, returns QualityResult
├── pipelines/
│   ├── __init__.py
│   ├── negative.py         # Negative pipeline: invert → WB → CLAHE → desat → unsharp
│   └── positive.py         # Positive pipeline: WB → shadow lift → CLAHE → unsharp
├── orchestrator.py         # Main loop: classify → process → evaluate → loop
├── config.py               # API keys, model names, thresholds (reads from .env)
├── models.py               # Shared dataclasses: FilmType, PipelineParams, QualityResult
├── .env                    # Real secrets — gitignored
├── .env.example            # Safe template to commit
└── data/                   # Input images
```

---

## Data Flow

```
input image path
  │
  ▼
ClassifierAgent (Gemini VLM)
  → FilmType
  │
  ▼
PostProcessingAgent
  → pipelines/negative.py  OR  pipelines/positive.py
  → accepts PipelineParams dataclass
  → returns np.ndarray
  │
  ▼
EvaluatorAgent (Gemini VLM)
  → QualityResult { score, passed, feedback, suggested_params }
  │
  ├─ passed OR iter >= MAX_ITER  →  save output, done
  └─ failed                      →  PostProcessingAgent(suggested_params), iter++
```

---

## Shared Models (`models.py`)

```python
from enum import Enum
from dataclasses import dataclass, field

class FilmType(Enum):
    NEGATIVE_35MM = "negative_35mm"
    NEGATIVE_120  = "negative_120"
    POSITIVE_35MM = "positive_35mm"
    POSITIVE_120  = "positive_120"

@dataclass
class PipelineParams:
    # Shared
    wb_clip_percent: float = 0.5
    black_point:     float = 0.8
    white_point:     float = 99.2
    clahe_clip:      float = 1.4
    unsharp_amount:  float = 0.35
    # Negative only
    desat_strength:  float = 0.20
    desat_sigma:     float = 7.0
    # Positive only
    shadow_lift:     float = 0.0   # additive lift, 0–30 typical

@dataclass
class QualityResult:
    score:             float          # 0.0–1.0
    passed:            bool
    feedback:          str
    suggested_params:  PipelineParams
```

---

## Agent Designs

### ClassifierAgent (`agents/classifier.py`)

- Encodes input image as base64, sends to Gemini VLM (`VLM_MODEL`)
- Prompt encodes classification rules:
  - Dark/black border → positive film
  - Orange overall tint + inverted luminance → negative film
  - Aspect ratio heuristic for 35mm vs 120 (35mm ≈ 3:2, 120 ≈ 1:1 or 6:7)
  - Gemini resolves ambiguous cases
- Returns `FilmType`
- Configurable model via `config.VLM_MODEL` — swap string to compare `gemini-2.5-flash` vs `gemini-2.5-pro`

### PostProcessingAgent (`agents/processor.py`)

- Pure OpenCV — no LLM
- Dispatches to `pipelines/negative.py` or `pipelines/positive.py` by `FilmType`
- Both 35mm and 120 use the same pipeline logic (format affects classification, not processing)
- Accepts `PipelineParams`, returns `np.ndarray`

### EvaluatorAgent (`agents/evaluator.py`)

- Encodes processed image as base64, sends to Gemini VLM
- Prompt asks model to score on: exposure (clipping), color balance, sharpness, artifacts
- Parses JSON response: `{ score, feedback, suggested_params }`
- Returns `QualityResult`
- If score >= `config.QUALITY_THRESHOLD` (default 0.75): `passed=True`
- `suggested_params` always populated — used only if `passed=False`

---

## Pipelines

### `pipelines/negative.py`

Stages (adapted from existing `negative_pipeline.py`, single-image interface):
1. Invert: `255 - image`
2. White balance (percentile-clipped)
3. Per-channel level stretch
4. Luminance CLAHE
5. Selective desaturation mask + application
6. Unsharp mask

### `pipelines/positive.py`

Stages:
1. White balance (percentile-clipped)
2. Shadow lift: `np.clip(image.astype(float) + shadow_lift, 0, 255)`
3. Luminance CLAHE (lower clip limit, ~1.0)
4. Unsharp mask

Both pipelines accept `PipelineParams` and return `np.ndarray`.

---

## Orchestrator (`orchestrator.py`)

```
for each image in data/:
    film_type = ClassifierAgent.classify(image_path)
    params = PipelineParams()   # defaults
    for i in range(MAX_EVAL_ITERATIONS):
        result_img = PostProcessingAgent.process(image, film_type, params)
        quality = EvaluatorAgent.evaluate(result_img)
        if quality.passed:
            break
        params = quality.suggested_params
    save result_img to output/<stem>_processed.png
    save metadata JSON: film_type, iterations, final_score, feedback
```

---

## Config (`config.py`)

Reads from `.env` via `python-dotenv`:

```python
GEMINI_API_KEY       = _env_first("GEMINI_API_KEY", "GOOGLE_API_KEY", default="")
VLM_MODEL            = _env_first("VLM_MODEL", "GEMINI_VLM_MODEL", default="gemini-2.5-flash")
LLM_MODEL            = _env_first("LLM_MODEL", "GEMINI_LLM_MODEL", default="gemini-2.5-flash")
MAX_EVAL_ITERATIONS  = 3
QUALITY_THRESHOLD    = 0.75
```

---

## Error Handling

- If Gemini returns unparseable JSON from evaluator: retain current params, log warning, count as iteration used
- If Gemini classification is ambiguous: default to `NEGATIVE_35MM`, log warning
- If pipeline raises exception: propagate with image path in message

---

## Dependencies

New additions to existing stack:
- `google-generativeai` — Gemini API client
- `python-dotenv` — `.env` loading

---

## Testing

- `data/` contains sample images covering at least: one negative, one positive
- Run `python orchestrator.py` to process all images in `data/`
- Each output written to `output/<stem>_processed.png` with sidecar `<stem>_meta.json`
- Compare Gemini models by changing `VLM_MODEL` in `.env`
