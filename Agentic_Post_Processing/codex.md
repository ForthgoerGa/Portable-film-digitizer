# raw_pipeline update summary

Date: 2026-04-14
Project: `C:\ece_445\Agentic_Post_Processing`

## What was implemented / improved

This round focused on turning `raw_pipeline/` from a broad first-pass scaffold into a more internally consistent RAW-aware negative pipeline prototype.

### 1. Density / reference semantics were corrected

Files updated:
- `raw_pipeline/models.py`
- `raw_pipeline/film_characterization.py`
- `raw_pipeline/density_inversion.py`

Main fixes:
- Removed the earlier logical mismatch where the pipeline computed relative transmission as `frame / base`, converted it to density with `-log(frame / base)`, and then effectively handled base offset a second time.
- Unified calibration semantics so downstream normalization works with density-space references rather than mixing linear-RGB and density representations.
- `normalize_density()` now accepts density-space references directly:
  - `upper_density_rgb`
  - `dmin_density_rgb`
  - `dmax_density_rgb`

Effect:
- The inversion path is now mathematically more self-consistent.
- Clear base is treated as the zero/reference point in the intended relative-density space.

### 2. Border detection was pushed toward film-specific geometry

File updated:
- `raw_pipeline/border_detection.py`

Main fixes:
- Kept the original three-cue candidate generation:
  - projection-based
  - edge-based
  - low-variance-based
- Added a refinement stage after generic voting:
  - keep only edge-connected connected-components
  - convert resulting candidates into top / bottom / left / right border strips
  - compute confidence partly from side-strip coverage rather than only generic vote agreement

Effect:
- Final `border_mask` is now closer to film-border geometry instead of an arbitrary fragmented pixel mask.
- Internal false-positive regions are less likely to survive into characterization.

### 3. Base estimation was upgraded from simple percentile to stable clear-base selection

File updated:
- `raw_pipeline/film_characterization.py`

Main fixes:
- Replaced the simple “take border pixels and compute a percentile” approach with a more robust selection process:
  - start from `clearbase_candidate_mask`, fallback to `border_mask`
  - keep brighter border candidates
  - reject pixels with larger inter-channel spread
  - choose a more stable bright cluster
  - use median of the selected cluster as `base_rgb`
- Added side-wise estimation:
  - split usable border candidates into `top`, `bottom`, `left`, `right`
  - estimate a base RGB for each usable side
  - fuse only mutually consistent sides
  - drop inconsistent side estimates when needed

New characterization fields:
- `base_sample_count`
- `base_rel_spread`
- `side_consistency_score`
- `side_base_rgbs`

Effect:
- `base_rgb` is less likely to be dominated by a contaminated border region, local leak, or content intrusion.
- The pipeline now surfaces whether different sides of the frame agree on the base estimate.

### 4. Diagnostics were made more physically useful

Files updated:
- `raw_pipeline/diagnostics.py`
- `raw_pipeline/pipeline.py`
- `raw_pipeline/density_inversion.py`

Main additions:
- Characterization-side metrics:
  - `base_sample_count`
  - `base_rel_spread`
  - `side_consistency_score`
  - side estimate count
  - density span
- Inversion-side density distribution metrics:
  - `density_p50_mean`
  - `density_p95_mean`
  - `density_p99_mean`
  - `density_p99_spread`
  - `density_headroom`
  - `headroom_score`
- `pipeline.py` stage metrics now serialize the new characterization outputs, including `side_base_rgbs`.

Effect:
- The pipeline is now better at exposing whether failure is due to border detection, base estimation, or density normalization.
- This is especially important before real IMX447 sample intake.

## Smoke test run status

A full end-to-end smoke test was run through the public entrypoint:
- function: `process_raw_negative(...)`
- frame input: `data\sample1.jpg`
- flat input: `data\sample3.png`
- output dir: `output\raw_pipeline_smoke`

Important note:
- This was **not** a real RAW validation run.
- The project did not contain ready-to-use RAW samples, so the test used the JPEG/PNG fallback path in `raw_ingest.py`.
- Therefore this confirms **entry-flow executability and diagnostics behavior**, not final physical correctness for IMX447.

### Smoke test result

Status:
- pipeline completed successfully
- returned `status: ok`
- saved intermediates:
  - `01_flat_corrected.png`
  - `02_border_mask.png`
  - `03_technical_positive.png`
  - `04_density_norm.png`
  - `05_rendered.png`

Observed metrics of note:
- `border_confidence = 0.5921`
- `base_sample_count = 30083`
- `base_rel_spread = 1.29772`
- `side_consistency_score = 0.65485`
- fused side estimates from: `top`, `left`
- inconsistent side estimates were dropped
- `clipping_ratio = 0.82653`
- `clipped_white_ratio = 0.82147`

Interpretation:
- The pipeline runs end-to-end without crashing.
- The new diagnostics correctly expose that this fallback sample run is still heavily over-bright / upper-clipped.
- This is acceptable for now because these are non-RAW fallback images and not the intended calibration protocol.
- More importantly, the pipeline is now instrumented enough that when real IMX447 samples arrive, we should be able to tell whether the next issue is caused by:
  - bad border/base estimation
  - inconsistent side estimates
  - poor upper-density estimation
  - normalization / clipping behavior

## Gateway model integration update

The project previously assumed Gemini-only access for classifier/evaluator model calls.
This round added a practical OpenAI-compatible gateway path using the first tested key pattern.

Files updated:
- `config.py`
- `agents/gateway_model.py`
- `agents/classifier.py`
- `agents/evaluator.py`
- `.env.example`

### What changed

1. Added gateway-related config fields:
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `MODEL_PROVIDER`
- `MODEL_GROUP`

2. Added `agents/gateway_model.py`
- A small OpenAI-compatible adapter that sends multimodal requests to:
  - `<OPENAI_BASE_URL>/chat/completions`
- Supports text + image inputs by converting images into data URLs.
- Returns a simple `.text` response compatible with the existing classifier/evaluator code path.

3. Updated `ClassifierAgent` and `EvaluatorAgent`
- If `MODEL_PROVIDER=openai_compatible` and gateway env vars are present, they now use the gateway adapter.
- If not, they still fall back to the old Gemini path or heuristic mode.

4. Added `.env.example`
- Documents the recommended integration pattern.

### Model probing result for the first gateway key pattern

Tested successfully through:
- `https://yinli.one/v1/chat/completions`

Models that returned valid minimal completions:
- `gpt-4o-mini`
- `gpt-4.1-mini`
- `gpt-4.1`
- `deepseek-chat` (backend surfaced as `deepseek-v3`)

Model that responded less reliably in this quick probe:
- `gemini-2.5-flash`
  - returned HTTP 200, but the sampled response body was effectively unusable for this minimal check

### Recommended default for this project

Recommended starting point:
- `MODEL_PROVIDER=openai_compatible`
- `OPENAI_BASE_URL=https://yinli.one/v1`
- `VLM_MODEL=gpt-4.1-mini`
- `LLM_MODEL=gpt-4.1-mini`

Cheaper alternative to test later:
- `deepseek-chat`

### Where to place the formal key

Do **not** hardcode it into source files.
Place the real key in one of these:
- project `.env`
- machine/user environment variables
- OpenClaw secret/config environment if this project is run under OpenClaw

For this project specifically, the simplest path is:
- create `C:\ece_445\Agentic_Post_Processing\.env`
- populate it from `.env.example`

Example:

```env
MODEL_PROVIDER=openai_compatible
OPENAI_API_KEY=your_real_gateway_key
OPENAI_BASE_URL=https://yinli.one/v1
VLM_MODEL=gpt-4.1-mini
LLM_MODEL=gpt-4.1-mini
MODEL_GROUP=
MAX_EVAL_ITERATIONS=3
QUALITY_THRESHOLD=0.75
```

## Current assessment

The pipeline is now in a better “real sample ready” prototype state than before:
- architecture is additive and intact
- border logic is more film-aware
- characterization math is more consistent
- base estimation is more robust
- diagnostics are more actionable
- classifier/evaluator model access is no longer locked to Gemini-only setup

But it is still a prototype, not a finished production negative pipeline.

## Recommended next step

Next step should be intake of real Raspberry Pi HQ Camera IMX447 sample data using the agreed protocol:
- one flat-field RAW scan
- one frame RAW scan containing border / clear base / full image
- optional leader RAW scan

When real samples are provided, inspect first:
- `base_rgb`
- `side_base_rgbs`
- `side_consistency_score`
- `base_rel_spread`
- `upper_density_rgb`
- `clipping_ratio`
- saved intermediate outputs under the smoke/output directory
