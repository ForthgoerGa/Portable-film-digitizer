# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Portable multi-format film digitization system (ECE 445). Captures 35mm negatives/slides, 120 medium format film, and Instax Mini prints; processes them via an on-device pipeline and returns social-media-ready images. Hardware: STM32 MCU + Raspberry Pi HQ Camera (IMX477) + dual-mode LED arrays. Cloud: AWS Lambda + S3 (Terraform-managed). Active development is split across `software/` (FastAPI server + image pipeline) and `Agentic_Post_Processing/` (Gemini-powered agent loop).

## Commands

No `requirements.txt` — install dependencies manually:
```bash
pip install fastapi uvicorn opencv-python numpy pyserial requests boto3 google-genai python-dotenv pytest
```

**Web server** (from repo root):
```bash
cd software && python main.py
# FastAPI at http://localhost:8000, web UI at http://localhost:8000/web/, hot reload enabled
```

**Negative pipeline directly** (from repo root):
```bash
python software/processing/negative_pipeline.py \
  --tiles-dir "Image_integration&Post_Processing/Negative_example/norway_split_40_overlap" \
  --output-dir "Image_integration&Post_Processing/output_demo" \
  --wb-clip-percent 0.5
```

**Agentic pipeline** (from repo root):
```bash
cd Agentic_Post_Processing
python orchestrator.py --input-dir data --output-dir output --max-iter 2
```

**Tests** (29 tests in `Agentic_Post_Processing/tests/`):
```bash
cd Agentic_Post_Processing && python -m pytest tests -v
```

**API key setup** (required for Gemini agents):
```bash
cd Agentic_Post_Processing && cp .env.example .env  # then fill in GEMINI_API_KEY
```

## Architecture

### Post_Processing_Negative pipeline (`Post_Processing_Negative/`) — NegativeLab-Pro style

RAW-first Bayer-domain pipeline for 35mm color negative film. Entry point: `run_physical_correction.py`. Requires a backlight flat-field capture and a `base_frame.dng` (unexposed clear-base reference). Real outputs are in `output_full_run_s1_s4/`.

```
DNG frame + backlight DNG + base_frame.dng + reference_layout.json
→ [1] raw_io.load_raw_bayer          Bayer float32 with black/white level metadata
→ [2] flat_field.build_flat_model    Low-freq illumination map (σ-frac Gaussian); optional per-frame strength search
→ [3] flat_field.apply_flat_field    Divide Bayer by illumination map (parameterised strength)
→ [4] render_preview.demosaic_to_rgb Simple bilinear Bayer→RGB, linear float32
→ [5] negative_inversion.estimate_base_reference  Median of ROI from base_frame; clips luma extremes (p10–p90)
→ [6] process_negative_stages        Base correction (frame / base_rgb)
→ [7]   density = -log(transmission)
→ [8]   color_unmix (3×3 density-domain dye unmixing, default conservative matrix, blended by strength)
→ [9]   invert_density_expm1  (expm1 curve, per-channel gains [1.35,1.20,1.10], percentile normalise)
→ [10] camera_color.apply_stage10_soft_reference_mapping
         [10a] gray anchor (weak luma normalisation, strength 0.35)
         [10b] soft 3×3 empirical matrix (3 presets: conservative/medium/aggressive)
         [10c] neutral damp (suppress color casts in neutral pixels)
         [10d] red guard (cap excess red)
→ Stage 2: perceptual_tone.apply_stage2_perceptual_tone_base
         [2a] gray norm (mean_luma, strength 0.20)
         [2b] Lab conversion for perceptual tone
         [2c] zoned luminance tone mapping (shadow γ + midtone sigmoid + highlight local)
         [2d] highlight roll-off (power curve)
→ Stage 3: color_refinement.apply_stage3_pseudo_lut_color_refinement
         [3a] luma-driven chroma scaling (Gaussian bell, highlight decay)
         [3b] hue-dependent pseudo-LUT (red/blue/green hue adjustments in Lab a/b)
         [3c] neutral region protection (low-chroma pixels shielded)
→ Stage 4: final_finish.apply_stage4_final_finish_and_export
         [4a] display mapping (simple gamma 2.2 or sRGB OETF)
         [4b] global trim (exposure EV, black/white point, contrast, temp/tint)
         [4c] optional: highlight desat, shadow neutralize, grain, bloom, softness, sharpen
→ final_finish.png  +  physical_correction_report.json
```

**Key modules (all in `negative_physical/`):**

| Module | Role |
|--------|------|
| `raw_io.py` | rawpy Bayer ingest, black/white level normalisation |
| `flat_field.py` | Build illumination map from backlight DNG; apply with tunable strength |
| `negative_inversion.py` | Base correction, density, 3×3 dye unmixing, expm1 inversion |
| `camera_color.py` | Stage [10] soft reference mapping (gray anchor + empirical matrix + neutral damp + red guard) |
| `perceptual_tone.py` | Stage 2 Lab-space zoned tone curve |
| `color_refinement.py` | Stage 3 pseudo-LUT color refinement (luma-chroma + hue adjust + neutral protect) |
| `final_finish.py` | Stage 4 display mapping and global trim export |
| `evaluator_agent.py` | Optional VLM / heuristic evaluator for flat-field strength search |
| `reference_layout.py` | Loads `reference_layout.json` ROI definitions for base estimation |
| `render_preview.py` | Demosaic, preview PNG save helpers |
| `tone_mapping.py` | Standalone filmic tone map (exposure align → sigmoid → roll-off → sRGB) |

**Important details:**
- Requires actual DNG captures from the RPi HQ Camera (not stitched tiles)
- `reference_layout.json` must define a `"base"` ROI covering a clear-base region in `base_frame.dng`
- `--flat-strength` (default 1.0) and `--flat-sigma-frac` (default 0.02) are the most impactful flat-field tuning knobs
- Color unmix matrix default `[[1.55,-0.38,-0.17],[-0.22,1.44,-0.22],[-0.08,-0.45,1.53]]` — tunable via `--color-unmix-matrix`
- Stage 10 `medium` preset matrix is default; switch to `conservative` if reds are over-corrected
- `tone_mapping.py` is a standalone filmic operator — not currently wired into the main `run_physical_correction.py` stage chain

### Web server request flow

```
Browser → FastAPI server.py (/scan, /status, /serial/*)
        → ScanJobCoordinator (coordinator.py)   [state machine: Ready → Working → Ready/Error]
        → Stitcher (stitcher.py)                [loads manifest.csv, feather-blends 40-tile overlap]
        → negative_pipeline.py                  [invert → WB → CLAHE → desat mask → unsharp]
        → software/web/generated/               [stitched_raw.png, inverted_positive.png, white_balanced.png]
```

### Agentic pipeline flow (`Agentic_Post_Processing/`)

```
Input image
→ ClassifierAgent    [Gemini VLM or heuristic fallback → film type: negative_35mm / negative_120 / positive_*]
→ PostProcessingAgent [routes to pipelines/negative.py or pipelines/positive.py]
→ EvaluatorAgent     [Gemini LLM or heuristic → quality score 0–1, suggested PipelineParams]
→ loop if score < 0.75 and iteration < max_iter
→ <name>_processed.png + <name>_meta.json (classification, score, metrics, params used)
```

### Key module map

| Module | Role |
|--------|------|
| `software/server.py` | FastAPI routes, static file serving |
| `software/coordinator.py` | Scan state machine, thread management, cancel flag |
| `software/stitcher.py` | Tile loading from manifest, overlap feather-blending, white balance |
| `software/processing/negative_pipeline.py` | Full 10-stage negative restoration pipeline |
| `software/serial_comm.py` | STM32 serial comms; auto-falls back to `MockSerialInterface` |
| `Agentic_Post_Processing/orchestrator.py` | Top-level agent loop runner |
| `Agentic_Post_Processing/agents/classifier.py` | Film type classification |
| `Agentic_Post_Processing/agents/processor.py` | Routes images to correct pipeline |
| `Agentic_Post_Processing/agents/evaluator.py` | Quality scoring, emits parameter suggestions |
| `Agentic_Post_Processing/pipelines/negative.py` | Density-space demasking → gray-world → LAB neutralization → CLAHE → unsharp |
| `Agentic_Post_Processing/pipelines/positive.py` | Percentile WB → CLAHE → unsharp |
| `Agentic_Post_Processing/config.py` | Loads `.env`, exposes pipeline defaults |
| `Agentic_Post_Processing/models.py` | Dataclasses: `FilmType`, `PipelineParams`, `QualityResult` |
| `cloud/main.py` | AWS Lambda handler: receives base64 image, uploads to S3 |

### Negative pipeline stages (`negative_pipeline.py` and `pipelines/negative.py`)

1. Film-base estimation from brightest pixels/borders; inversion in optical-density space (removes orange mask)
2. Percentile-clipped white balance per channel
3. Gray-world midtone balance
4. LAB color space cast neutralization
5. Percentile auto-levels (black_point / white_point params)
6. Luminance CLAHE contrast enhancement
7. Spatial cast-suppression mask computation
8. Selective desaturation on cast regions
9. Unsharp mask for detail recovery
10. Final gray-world neutralization

## Important details

- **Coordinator gap**: `ScanJobCoordinator` currently only applies white balance via `Stitcher`; the full `negative_pipeline.py` stages are not yet wired into the coordinator path.
- **Mock mode**: `serial_comm.py` uses `MockSerialInterface` automatically when no STM32 is connected — no hardware required for development.
- **Gemini integration**: uses `google.genai` with `response_mime_type="application/json"` and `thinking_budget=0` for stable JSON responses. Both classifier and evaluator fall back to heuristics when Gemini is unavailable; `classifier_mode` / `evaluator_mode` in output metadata record which path ran.
- **Demo data**: `Image_integration&Post_Processing/Negative_example/norway_split_40_overlap/` — 40-tile overlapping negative sample with `manifest.csv` and `metadata.json`. Stitcher resolves tile paths relative to repo root.
- **Frontend**: Vanilla JS + HTML/CSS in `software/web/`; no build step. Cache-busting timestamps on image URLs force refresh after processing. Status polls every 500 ms.
- **Evaluator metrics** in output JSON: `gray_mean`, `gray_std`, `clipped_black_ratio`, `clipped_white_ratio`, `blue_red_delta`, `green_magenta_delta`, `channel_spread`, `mean_saturation`, `laplacian_variance`.
