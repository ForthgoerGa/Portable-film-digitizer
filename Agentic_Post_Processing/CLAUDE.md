# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Portable multi-format film digitization system (ECE 445). The system captures 35mm negatives/slides, 120 medium format film, and Instax Mini prints, processes them locally, and returns processed images. The `Agentic_Post_Processing` directory is the active development working directory; the broader repo lives at `c:\ece_445\`.

## Running the Server

```bash
python software/main.py
```

Starts the FastAPI server at `http://localhost:8000` with hot reload. The web UI is served from `software/web/`.

## Running the Image Processing Pipeline Directly

```bash
python software/processing/negative_pipeline.py \
  --tiles-dir "Image_integration&Post_Processing/Negative_example/norway_split_40_overlap" \
  --output-dir "Image_integration&Post_Processing/output_demo" \
  --wb-clip-percent 0.5
```

## Agentic Post-Processing Pipeline (`Agentic_Post_Processing/`)

A complete agentic image restoration pipeline powered by Gemini (with heuristic fallback).

### Running the Agentic Pipeline

```bash
cd Agentic_Post_Processing
python orchestrator.py --input-dir data --output-dir output --max-iter 2
```

### Architecture

| Module | Role |
|--------|------|
| `orchestrator.py` | Top-level loop: classifies → processes → evaluates → refines up to `--max-iter` times |
| `agents/classifier.py` | Classifies image as `positive_35mm`, `negative_35mm`, or `medium_format`; uses Gemini or heuristic |
| `agents/processor.py` | Routes to `pipelines/negative.py` or `pipelines/positive.py` based on classification |
| `agents/evaluator.py` | Scores output (0–1) using Gemini or heuristic metrics; emits parameter adjustment suggestions |
| `pipelines/negative.py` | Film-base demasking in density space → midtone gray-world → LAB cast neutralization → CLAHE → unsharp mask |
| `pipelines/positive.py` | White balance (percentile stretch) → CLAHE → unsharp mask |
| `models.py` | Shared dataclasses (`ProcessingResult`, `EvaluationResult`, etc.) |
| `config.py` | Reads `.env` for `GEMINI_API_KEY`; exposes pipeline defaults |

### Key Details

- **Gemini integration**: uses `google.genai` with `response_mime_type="application/json"` and `thinking_budget=0` to get stable JSON responses
- **Fallback**: classifier and evaluator automatically fall back to heuristics when Gemini is unavailable; `classifier_mode` / `evaluator_mode` fields in output metadata record which path was used
- **Negative demasking**: estimates film base from brightest pixels and border candidates; inverts in optical-density space to remove the orange mask, then applies gray-world and LAB neutralization
- **Evaluator metrics** written to output metadata: `gray_mean`, `gray_std`, `clipped_black_ratio`, `clipped_white_ratio`, `blue_red_delta`, `green_magenta_delta`, `channel_spread`, `mean_saturation`, `laplacian_variance`
- **Tests**: `python -m pytest tests -v` → 29 passed
- **Output**: processed images + JSON sidecar metadata written to `--output-dir`

## Key Dependencies (no requirements.txt — install manually)

- `fastapi`, `uvicorn` — web server
- `opencv-python` (cv2), `numpy` — image processing
- `pyserial` — STM32 serial communication (optional; mocks available)
- `requests` — HTTP client
- `boto3` — AWS S3 integration
- `google-genai` — Gemini API client (agentic pipeline)
- `python-dotenv` — `.env` loading for API keys

## Architecture

**Request flow**: Browser → FastAPI (`server.py`) → `ScanJobCoordinator` (`coordinator.py`) → `Stitcher` (`stitcher.py`) → `negative_pipeline.py` → `software/web/generated/`

### Core Modules

| Module | Role |
|--------|------|
| `software/server.py` | FastAPI routes (`/scan`, `/status`, `/serial/*`), static file serving |
| `software/coordinator.py` | Scan state machine (Ready → Working → Ready/Error), thread management, cancel flag |
| `software/stitcher.py` | Loads overlapping tiles from manifest, feather-blends stitching, applies white balance |
| `software/processing/negative_pipeline.py` | Full restoration pipeline: stitch → invert → white balance → CLAHE → desaturation mask → unsharp mask |
| `software/serial_comm.py` | STM32 communication; falls back to `MockSerialInterface` automatically when no device connected |
| `software/camera.py` | Mock camera capture (real capture not yet integrated) |
| `software/uploader.py` | POSTs images to remote cloud API |
| `cloud/main.py` | AWS Lambda handler for S3 upload/retrieval |

### Image Processing Pipeline Stages (`negative_pipeline.py`)

1. Tile loading with overlap-aware feather weights
2. Negative inversion (`255 - pixel`)
3. White balance (percentile-clipped or OpenCV SimpleWB)
4. Luminance CLAHE contrast enhancement
5. Selective desaturation mask for color cast suppression
6. Unsharp mask for detail recovery

### Important Details

- **Paths**: Stitcher resolves tile paths relative to repo root; outputs go to `software/web/generated/`
- **Mock mode**: `serial_comm.py` uses `MockSerialInterface` when no STM32 is connected — safe for development
- **Coordinator gap**: The coordinator currently only applies white balance, not the full `negative_pipeline.py` pipeline — these are not yet wired together
- **Demo data**: `Image_integration&Post_Processing/Negative_example/norway_split_40_overlap/` contains 40-tile overlapping negative sample dataset
- **Frontend**: Vanilla JS with cache-busting timestamps for image refresh; no framework or build step needed
