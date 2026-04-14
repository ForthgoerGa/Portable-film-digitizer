# Agentic Post-Processing Pipeline

AI-driven film scan restoration pipeline for the ECE 445 portable film digitizer. Automatically classifies film type, applies a multi-stage OpenCV pipeline, and uses Gemini (or a heuristic fallback) to iteratively refine processing parameters until the output meets a quality threshold.

---

## Quick Start

### 1. Set up API key (optional — heuristic fallback works without it)

Create `Agentic_Post_Processing/.env`:

```
GEMINI_API_KEY=your_key_here
```

### 2. Process a directory of images

```bash
cd Agentic_Post_Processing
python orchestrator.py --input-dir data --output-dir output --max-iter 3
```

Each image produces two output files in `--output-dir`:
- `<stem>_processed.png` — the restored image
- `<stem>_meta.json` — processing metadata (film type, score, parameters, per-iteration log)

### 3. Process a single image

```bash
python _run_single.py path/to/capture.jpg path/to/output_dir/
```

Prints a JSON result to stdout and streams `PROGRESS:{json}` lines to stderr after each iteration (used by the web server for live progress display).

### 4. Run tests

```bash
python -m pytest tests -v
```

---

## Web UI Integration

The web frontend at `http://localhost:8000` has an **AI Processing** button on the Camera tab. After capturing an image:

1. Click **Process with AI** — the image is sent to the PC and the pipeline starts.
2. A live **AI Pipeline** panel appears showing:
   - Film type + agent modes (Gemini or heuristic)
   - Iteration progress bar
   - Score bar (green ≥ 70%, amber ≥ 45%, red below)
   - Per-iteration log with score, pass/fail verdict, and feedback
   - Current parameter values (expandable)
3. On completion, a **before/after comparison** is shown with final metrics.

To run the web server:

```bash
cd software
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

---

## Architecture

```
orchestrator.py
  └── process_image()
        ├── ClassifierAgent   → film type (negative_35mm | positive_35mm | medium_format)
        ├── PostProcessingAgent → OpenCV pipeline (negative.py or positive.py)
        └── EvaluatorAgent    → quality score + suggested params
              └── loop up to MAX_EVAL_ITERATIONS times
```

| File | Role |
|---|---|
| `orchestrator.py` | Entry point; classify → process → evaluate → refine loop |
| `agents/classifier.py` | Gemini vision or heuristic film type classification |
| `agents/processor.py` | Routes to the correct pipeline based on film type |
| `agents/evaluator.py` | Scores output 0–1; emits suggested parameters for next iteration |
| `pipelines/negative.py` | 13-stage negative restoration pipeline |
| `pipelines/positive.py` | 7-stage positive/slide pipeline |
| `models.py` | Shared dataclasses (`PipelineParams`, `QualityResult`) |
| `config.py` | Loads `.env`; exposes `GEMINI_API_KEY`, `MAX_EVAL_ITERATIONS`, `QUALITY_THRESHOLD` |
| `_run_single.py` | Subprocess wrapper used by the web server |

---

## Pipeline Stages

### Negative Film (`pipelines/negative.py`)

1. **Film base estimation** — samples brightest pixels and image borders to estimate the orange mask
2. **Optical-density inversion** — converts to density space, subtracts the base, inverts back
3. **Gray-world white balance** — per-channel gain normalisation (clamped to [0.5, 1.8])
4. **Highlight compression** — soft-clips blown highlights before further processing
5. **Gamma correction** — `output = input ^ gamma` (gamma < 1 brightens, gamma > 1 darkens)
6. **Shadow lift** — additive floor lift for crushed shadows
7. **LAB cast neutralisation** — suppresses residual colour cast in LAB a/b channels
8. **CLAHE** — contrast-limited adaptive histogram equalisation on the L channel
9. **Vibrance boost** — weighted saturation increase (low-saturation pixels boosted most)
10. **Final gray-world pass** — tightens residual per-channel imbalance (clamped to [0.75, 1.35])
11. **Second LAB neutralisation** — removes any cast reintroduced by CLAHE
12. **Selective desaturation** — suppresses specific colour cast hues via HSV-space mask
13. **Unsharp mask** — recovers fine grain and edge detail

### Positive / Slide Film (`pipelines/positive.py`)

1. White balance (percentile stretch per channel)
2. Shadow lift
3. Gamma correction
4. Highlight compression
5. CLAHE
6. Vibrance boost
7. Unsharp mask

---

## Pipeline Parameters

All 14 parameters are tuned automatically by the evaluator. Ranges and defaults:

| Parameter | Range | Default | Effect |
|---|---|---|---|
| `wb_clip_percent` | 0.2 – 1.5 | 0.5 | Percentile used for white-balance stretch; higher clips more |
| `black_point` | 0.5 – 2.0 | 0.8 | Black-point percentile for tone mapping |
| `white_point` | 97.0 – 99.5 | 99.2 | White-point percentile for tone mapping |
| `clahe_clip` | 0.5 – 4.0 | 1.4 | CLAHE clip limit; higher = more contrast |
| `clahe_grid` | 4, 8, or 16 | 8 | CLAHE tile grid size; smaller = more localised |
| `unsharp_amount` | 0.0 – 1.0 | 0.35 | Unsharp mask strength |
| `unsharp_sigma` | 0.5 – 3.0 | 1.2 | Gaussian blur radius for unsharp mask |
| `desat_strength` | 0.0 – 0.6 | 0.20 | Selective desaturation mask strength |
| `desat_sigma` | 2.0 – 20.0 | 7.0 | Hue width for selective desaturation |
| `shadow_lift` | 0.0 – 40.0 | 0.0 | Additive lift applied to all pixels before CLAHE |
| `gamma` | 0.4 – 2.2 | 1.0 | Power-law gamma: < 1 brightens, > 1 darkens, 1 = no-op |
| `lab_strength` | 0.0 – 1.5 | 0.85 | LAB cast neutralisation strength; > 1 is aggressive |
| `highlight_compression` | 0.0 – 0.5 | 0.0 | Soft highlight rolloff; use 0.2–0.4 if white is clipping |
| `vibrance` | −0.3 – 0.6 | 0.0 | Weighted saturation boost; affects unsaturated pixels most |

---

## Evaluator Metrics

Written to `<stem>_meta.json` under `evaluation_metrics`:

| Metric | Ideal range | What it measures |
|---|---|---|
| `gray_mean` | 100 – 165 | Overall exposure (0 = black, 255 = white) |
| `gray_std` | — | Tonal contrast |
| `clipped_black_ratio` | < 0.005 | Fraction of pixels crushed to near-black (≤ 2) |
| `clipped_white_ratio` | < 0.005 | Fraction of pixels blown to near-white (≥ 253) |
| `blue_red_delta` | ≈ 0 | Blue–red channel imbalance (positive = blue/cyan cast) |
| `green_magenta_delta` | ≈ 0 | Green–magenta channel imbalance |
| `channel_spread` | < 0.10 | Max − min of channel means (lower = more balanced) |
| `mean_saturation` | 0.08 – 0.45 | Average HSV saturation (< 0.04 may indicate demasking failure) |
| `laplacian_variance` | > 150 | Sharpness proxy (< 80 = soft) |

---

## Configuration

Edit `config.py` or set environment variables in `.env`:

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | *(none)* | Existing stable Gemini key for the main fast path |
| `VLM_MODEL` | `gemini-2.5-flash` | Main classifier/evaluator model |
| `MAX_EVAL_ITERATIONS` | `3` | Maximum refinement iterations per image |
| `QUALITY_THRESHOLD` | `0.75` | Score ≥ this value → pipeline exits early (quality passed) |
| `DECISION_MODEL_ENABLED` | `0` | Enable optional high-level escalation model |
| `OPENAI_API_KEY` | *(none)* | Gateway key for escalation-only high-level decisions |
| `OPENAI_BASE_URL` | *(none)* | OpenAI-compatible gateway base URL |
| `DECISION_MODEL` | `gpt-5-mini` | Primary high-level strategy model, chosen for measured stability |
| `DECISION_MODEL_FALLBACK` | `gpt-5.1-chat` | Stronger but currently less stable fallback tier |
| `DECISION_MODEL_EXPERT` | `gpt-5-pro` | Expert tier reserved for the hardest cases |
| `DECISION_SCORE_TRIGGER` | `0.55` | Escalate when score is at or below this threshold |
| `DECISION_MIN_ITERATION` | `2` | Earliest iteration allowed to call the decision model |

### Decision-model escalation path

The stable Gemini-based agentic pipeline remains the default fast path.
An optional escalation-only strategy model can be enabled for difficult cases.
When enabled, the orchestrator may call a stronger gateway-backed model such as
`gpt-5.2` only when:
- the current iteration is at least `DECISION_MIN_ITERATION`
- the evaluator score is at or below `DECISION_SCORE_TRIGGER`
- the normal fast path has not already passed the quality threshold

This preserves Gemini as the main and rollback path while allowing stronger
high-level parameter decisions on hard examples.

Recommended escalation tiering:
- primary decision tier: `gpt-5-mini`
- secondary tier: `gpt-5.1-chat`
- expert tier: `gpt-5-pro`

---

## Output Metadata Example

```json
{
  "source": "capture_20240101_120000.jpg",
  "film_type": "negative_35mm",
  "classifier_mode": "gemini",
  "evaluator_mode": "gemini",
  "iterations": 2,
  "selected_iteration": 2,
  "final_score": 0.821,
  "passed": true,
  "feedback": "Good exposure, colour balance, and detail.",
  "final_params": {
    "gamma": 0.85,
    "lab_strength": 1.1,
    "clahe_clip": 1.6,
    ...
  },
  "evaluation_metrics": {
    "gray_mean": 134.7,
    "clipped_white_ratio": 0.002,
    "mean_saturation": 0.18,
    "laplacian_variance": 210.4,
    ...
  }
}
```

---

## Experimental: `raw_pipeline` package (work-in-progress)

`raw_pipeline/` is a new, additive package implementing a physically-grounded
RAW-aware negative inversion pipeline as described in `pipeline_update.md`.
It does **not** modify or replace the existing agentic pipeline.

### Design

The package separates two concerns that the current pipeline merges:

| Stage | Modules |
|---|---|
| **Technical inversion** | `raw_ingest`, `sensor_normalization`, `flat_field`, `border_detection`, `film_characterization`, `density_inversion` |
| **Aesthetic rendering** | `rendering` |
| **Stage-aware diagnostics** | `diagnostics` |

### Quick start

```bash
cd Agentic_Post_Processing
python - <<'EOF'
from raw_pipeline import process_raw_negative
result = process_raw_negative(
    frame_raw_path="path/to/frame.dng",
    flat_raw_path="path/to/flat.dng",
    output_dir="/tmp/raw_out",
)
print(result["evaluator_output"]["overall_score"])
EOF
```

JPEG/PNG inputs are accepted as a fallback for testing without actual RAW files.
A `rawpy` install is required for true RAW (DNG/ARW/RAF) input:
```bash
pip install rawpy
```

### Status

Phase 1–5 (ingest → flat-field → border detection → characterisation →
inversion → rendering → diagnostics) are implemented as first-pass working
code.  Leader-based calibration (`border_plus_leader` mode) is supported but
not yet validated on real IMX477 captures.  Results should be treated as
experimental until tested against real RAW scans.
