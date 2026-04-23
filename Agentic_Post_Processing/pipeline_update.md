# IMX447 RAW Film Pipeline Update Design

## 1. Purpose

This document defines an implementation-facing redesign of the film post-processing pipeline for `Agentic_Post_Processing`.

The new pipeline targets Raspberry Pi HQ Camera IMX447 RAW input and separates:
- technical inversion, which should be physically grounded and diagnostically stable
- aesthetic rendering, which should improve appearance without hiding inversion failures

The first-class workflow assumes these inputs:
1. one flat-field RAW scan containing only the light source
2. one frame RAW scan containing film border / clear base and full image content

Optional future support for a dedicated leader scan is included, but the pipeline must work without it.

## 2. Design goals

### Goals

- Support RAW-aware processing for IMX447 captures
- Use flat-field correction to remove illumination and optical bias before inversion
- Automatically detect film border from the frame scan
- Extract border pixels and estimate film base / orange mask automatically
- Support inversion without a dedicated leader scan
- Optionally improve calibration with a leader scan later
- Keep stage outputs inspectable for diagnostics and agentic orchestration
- Separate technical inversion from final rendering
- Keep module boundaries explicit so future agent-based orchestration can reason about failures by stage

### Non-goals

- Perfect film-stock-specific color reproduction in the first implementation
- Replacing all downstream editing workflows
- Training a learned model in the first version
- Implementing a full GUI redesign in this document
- Requiring a leader scan for the baseline workflow

## 3. Input protocol

### Required inputs

#### A. Flat-field RAW
A RAW image captured with the same hardware and scan configuration, but without film in the optical path.

Requirements:
- same camera
- same lens / magnification
- same ISO
- same exposure time
- same light source intensity and geometry
- same holder / optical stack when possible

Purpose:
- estimate illumination field
- correct shading and color bias from the capture system

#### B. Frame RAW
A RAW image of a real negative frame that includes:
- full photo content
- visible film border or clear base region
- preferably some non-image border area

Purpose:
- estimate per-frame film base / orange mask
- perform inversion of the actual image content

### Optional inputs

#### C. Leader RAW
A dedicated calibration frame with both clear base and heavily exposed dark negative region.

Purpose:
- estimate density endpoints more reliably
- improve D-min / D-max calibration consistency across frames

## 4. High-level end-to-end pipeline

The proposed end-to-end pipeline is:

1. RAW ingest
2. Sensor normalization
3. Flat-field model generation
4. Flat-field correction of the frame
5. Border detection and border confidence estimation
6. Film characterization
7. Technical density inversion
8. Positive rendering
9. Diagnostics and evaluator output

The pipeline should explicitly preserve intermediate outputs for inspection.

## 5. Proposed module breakdown

Recommended new module group:

```text
raw_pipeline/
  raw_ingest.py
  sensor_normalization.py
  flat_field.py
  border_detection.py
  film_characterization.py
  density_inversion.py
  rendering.py
  diagnostics.py
  models.py
```

Existing orchestration can later call these modules through the current `agents/` and `orchestrator.py` flow.

## 6. Core data models / dataclasses

Suggested dataclasses in `raw_pipeline/models.py`.

### RawFrame

```python
@dataclass
class RawFrame:
    path: str
    linear_rgb: np.ndarray
    metadata: dict[str, Any]
    black_level: float | np.ndarray | None
    white_level: float | np.ndarray | None
    wb_multipliers: tuple[float, float, float] | None
    sensor_name: str | None
    width: int
    height: int
```

### FlatFieldModel

```python
@dataclass
class FlatFieldModel:
    flat_linear_rgb: np.ndarray
    normalized_flat: np.ndarray
    illumination_map: np.ndarray
    channel_means: tuple[float, float, float]
    valid_mask: np.ndarray | None
```

### BorderDetectionResult

```python
@dataclass
class BorderDetectionResult:
    frame_bbox: tuple[int, int, int, int] | None
    border_mask: np.ndarray
    content_mask: np.ndarray
    clearbase_candidate_mask: np.ndarray
    method_votes: dict[str, float]
    confidence: float
```

### FilmCharacterization

```python
@dataclass
class FilmCharacterization:
    base_rgb: np.ndarray
    base_density_rgb: np.ndarray | None
    upper_density_rgb: np.ndarray | None
    dmin_rgb: np.ndarray | None
    dmax_rgb: np.ndarray | None
    border_confidence: float
    upper_ref_confidence: float
    calibration_mode: str
    notes: list[str]
```

### InversionResult

```python
@dataclass
class InversionResult:
    positive_linear_rgb: np.ndarray
    density_rgb: np.ndarray
    density_norm_rgb: np.ndarray | None
    channel_spread: float
    clipping_ratio: float
    diagnostics: dict[str, float]
```

### RenderResult

```python
@dataclass
class RenderResult:
    rendered_rgb: np.ndarray
    technical_positive_rgb: np.ndarray
    render_metrics: dict[str, float]
```

### PipelineResult

```python
@dataclass
class PipelineResult:
    flat_model: FlatFieldModel
    border_result: BorderDetectionResult
    film_characterization: FilmCharacterization
    inversion_result: InversionResult
    render_result: RenderResult
    evaluator_output: dict[str, Any]
```

## 7. Detailed module interfaces

## 7.1 raw_ingest.py

Purpose:
- read IMX447 RAW files into linear float32 RGB
- preserve metadata needed for reproducibility

Suggested functions:

```python
def load_raw_frame(path: str) -> RawFrame:
    ...
```

Responsibilities:
- use a RAW-capable loader such as `rawpy`
- disable auto-brightening
- keep gamma linear
- preserve metadata fields relevant to calibration

Notes:
- this module should not perform aesthetic color correction
- this module should not apply final white balance for look rendering

## 7.2 sensor_normalization.py

Purpose:
- normalize sensor-domain information before flat-field or film analysis

Suggested functions:

```python
def subtract_black_level(raw: RawFrame) -> np.ndarray:
    ...


def normalize_white_level(image: np.ndarray, raw: RawFrame) -> np.ndarray:
    ...


def apply_bad_pixel_correction(image: np.ndarray) -> np.ndarray:
    ...


def prepare_linear_rgb(raw: RawFrame) -> np.ndarray:
    ...
```

Responsibilities:
- black level subtraction
- white level normalization
- optional bad-pixel correction
- return linear float32 RGB in [0,1] or similarly documented linear range

## 7.3 flat_field.py

Purpose:
- build an illumination correction model from the flat-field RAW
- apply that correction to the frame RAW

Suggested functions:

```python
def build_flat_field_model(flat_rgb: np.ndarray) -> FlatFieldModel:
    ...


def apply_flat_field(frame_rgb: np.ndarray, flat_model: FlatFieldModel) -> np.ndarray:
    ...
```

Responsibilities:
- normalize flat image so global mean is approximately 1
- estimate low-frequency illumination field
- suppress lighting color imbalance and falloff

Expected behavior:
- if `flat_rgb` is spatially uneven, `illumination_map` captures that
- corrected frame should preserve relative scene transmission while reducing system bias

## 7.4 border_detection.py

Purpose:
- automatically find film border / clear base candidates from the frame scan

Suggested functions:

```python
def detect_border_regions(frame_rgb: np.ndarray) -> BorderDetectionResult:
    ...


def projection_based_border_candidate(frame_rgb: np.ndarray) -> tuple[np.ndarray, float]:
    ...


def edge_based_border_candidate(frame_rgb: np.ndarray) -> tuple[np.ndarray, float]:
    ...


def low_variance_border_candidate(frame_rgb: np.ndarray) -> tuple[np.ndarray, float]:
    ...


def merge_border_candidates(candidates: list[tuple[np.ndarray, float]]) -> BorderDetectionResult:
    ...
```

Responsibilities:
- detect likely border area from geometry and image statistics
- avoid relying on an agent for low-level pixel segmentation
- produce a usable border mask and a confidence score

## 7.5 film_characterization.py

Purpose:
- estimate film base and density references from border and scene content

Suggested functions:

```python
def estimate_base_from_border(frame_rgb: np.ndarray, border: BorderDetectionResult) -> np.ndarray:
    ...


def estimate_base_density(frame_rgb: np.ndarray, base_rgb: np.ndarray) -> np.ndarray:
    ...


def estimate_scene_upper_density(frame_rgb: np.ndarray, base_rgb: np.ndarray) -> tuple[np.ndarray, float]:
    ...


def characterize_film(
    frame_rgb: np.ndarray,
    border: BorderDetectionResult,
    leader_rgb: np.ndarray | None = None,
    flat_model: FlatFieldModel | None = None,
) -> FilmCharacterization:
    ...
```

Responsibilities:
- estimate `base_rgb` from stable border pixels
- derive a border-based base density estimate
- if no leader exists, estimate an upper-density reference conservatively from scene data
- if leader exists, prefer leader-based D-min / D-max calibration

## 7.6 density_inversion.py

Purpose:
- perform technically grounded negative inversion using density-domain operations

Suggested functions:

```python
def compute_transmission(frame_rgb: np.ndarray, base_rgb: np.ndarray | None = None) -> np.ndarray:
    ...


def compute_density(transmission_rgb: np.ndarray) -> np.ndarray:
    ...


def subtract_base_density(density_rgb: np.ndarray, base_density_rgb: np.ndarray) -> np.ndarray:
    ...


def normalize_density(
    density_rgb: np.ndarray,
    dmin_rgb: np.ndarray | None,
    dmax_rgb: np.ndarray | None,
    upper_density_rgb: np.ndarray | None,
) -> np.ndarray:
    ...


def invert_negative(
    frame_rgb: np.ndarray,
    characterization: FilmCharacterization,
) -> InversionResult:
    ...
```

Responsibilities:
- convert corrected linear intensities into density-domain representation
- remove base density contribution
- map negative density into normalized positive domain
- expose diagnostics instead of silently hiding unstable behavior

## 7.7 rendering.py

Purpose:
- apply aesthetic rendering after technical inversion is already complete

Suggested functions:

```python
def apply_residual_white_balance(image_rgb: np.ndarray, gains: tuple[float, float, float]) -> np.ndarray:
    ...


def apply_tone_curve(image_rgb: np.ndarray, params: dict[str, float]) -> np.ndarray:
    ...


def apply_color_rendering(image_rgb: np.ndarray, params: dict[str, float]) -> np.ndarray:
    ...


def apply_detail_rendering(image_rgb: np.ndarray, params: dict[str, float]) -> np.ndarray:
    ...


def render_positive(image_rgb: np.ndarray, params: dict[str, Any]) -> RenderResult:
    ...
```

Responsibilities:
- operate only on already-inverted positive data
- avoid hiding inversion-stage failure
- keep rendering parameters distinct from technical inversion parameters

## 7.8 diagnostics.py

Purpose:
- generate stage-aware diagnostics for agentic orchestration

Suggested functions:

```python
def evaluate_border_result(border: BorderDetectionResult) -> dict[str, float]:
    ...


def evaluate_characterization(characterization: FilmCharacterization) -> dict[str, float]:
    ...


def evaluate_inversion(inversion: InversionResult) -> dict[str, float]:
    ...


def evaluate_render(render: RenderResult) -> dict[str, float]:
    ...


def build_evaluator_output(
    border: BorderDetectionResult,
    characterization: FilmCharacterization,
    inversion: InversionResult,
    render: RenderResult,
) -> dict[str, Any]:
    ...
```

Responsibilities:
- produce stage-specific confidence and failure signals
- support agentic retry logic
- support strategy changes, not only slider tuning

## 8. Border detection and border confidence strategy

The border detector should be automatic.

The preferred approach is CV-first, not agent-first.

The initial implementation should combine at least three candidate methods:
- projection-based boundary estimation
- edge/line-based boundary estimation
- low-variance edge-region detection

The detector should produce:
- `border_mask`
- `content_mask`
- `clearbase_candidate_mask`
- `confidence`

### Confidence heuristics

Confidence should increase when:
- multiple methods agree on a similar border region
- border pixels have low variance
- border pixels have color consistency across multiple sides
- border region is spatially contiguous and located near image edges

Confidence should decrease when:
- different sides produce inconsistent base estimates
- estimated border contains strong texture or scene content
- candidate border region is too small
- candidate border colors vary strongly after flat-field correction

## 9. Film characterization without leader, and optional leader-enhanced mode

### Why border-only estimation is useful

Border / clear base estimation provides a strong estimate of:
- film base color
- orange mask contribution
- D-min-like lower reference behavior

This is already much better than estimating film base from scene highlights.

### Why border-only estimation is not equivalent to full leader calibration

Border-only estimation typically gives one reliable endpoint: the base side.

It does not reliably provide the upper density endpoint of the negative. Scene content may not contain a stable or representative darkest-density region, so estimating the upper bound from image content can drift between frames.

Therefore:
- border-only mode is useful and should be first-class
- border-only mode is not equivalent to explicit D-min / D-max calibration

### Baseline mode: no leader

In the default workflow without a leader scan:
- estimate `base_rgb` from border / clear base pixels
- derive `base_density_rgb`
- estimate `upper_density_rgb` conservatively from scene content using robust density statistics
- set `calibration_mode = "border_plus_scene_estimate"`

### Enhanced mode: leader available

If a dedicated leader scan is provided:
- estimate clear base from leader clear area
- estimate heavily exposed density from leader dark area
- derive `dmin_rgb` and `dmax_rgb`
- set `calibration_mode = "border_plus_leader"`

Leader-enhanced mode should override unstable scene-based upper-density estimation.

## 10. Density inversion strategy

The inversion core should operate in a density-aware way.

Suggested conceptual flow:

1. start from flat-field-corrected linear RGB
2. derive transmission proxy
3. convert to density using a log transform
4. subtract base density contribution
5. normalize density using border-derived or leader-derived references
6. map normalized density into positive linear RGB

The initial implementation should expose intermediate arrays for debugging:
- corrected frame
- border sample visualization
- density image
- normalized density image
- technical positive result

## 11. Technical inversion vs aesthetic rendering

This separation is mandatory.

### Technical inversion stage

Technical inversion is responsible for:
- raw-aware ingestion
- flat-field correction
- border extraction
- base estimation
- density transform
- density normalization
- initial positive reconstruction

This stage should aim for:
- physical plausibility
- consistency across frames
- diagnostic transparency

### Aesthetic rendering stage

Rendering is responsible for:
- residual white balance refinement
- tone shaping
- contrast
- saturation / vibrance
- sharpening
- optional look presets

This stage should not be used to hide systematic inversion failures.

If technical inversion fails, evaluator output should flag technical failure rather than compensating with aggressive rendering.

## 12. Diagnostics / evaluator outputs for agentic orchestration

The evaluator should move from a single final-image score toward stage-aware diagnostics.

Suggested output structure:

```python
{
  "overall_score": float,
  "stage_scores": {
    "border_detection": float,
    "film_characterization": float,
    "technical_inversion": float,
    "rendering": float,
  },
  "cast_type": str | None,
  "suspected_failure_stage": str | None,
  "recommended_strategy": str | None,
  "recommended_params": dict[str, Any],
  "confidence": float,
  "notes": list[str],
}
```

Suggested `suspected_failure_stage` values:
- `flat_field`
- `border_detection`
- `film_characterization`
- `density_inversion`
- `rendering`

Suggested `recommended_strategy` values:
- `retry_border_detection_projection`
- `retry_border_detection_low_variance`
- `use_conservative_upper_density`
- `use_leader_calibration`
- `reduce_render_strength`
- `increase_border_weight`

## 13. Suggested implementation phases and acceptance criteria

### Phase 1: RAW ingest + flat-field

Deliverables:
- `raw_ingest.py`
- `sensor_normalization.py`
- `flat_field.py`

Acceptance criteria:
- IMX447 RAW files load reproducibly
- flat-field correction reduces visible low-frequency illumination bias
- corrected frame is stable across repeated runs

### Phase 2: Border detection + base estimation

Deliverables:
- `border_detection.py`
- partial `film_characterization.py`

Acceptance criteria:
- border mask is extracted automatically on representative scans
- border confidence correlates with obvious success/failure cases
- base estimate is more stable than full-frame brightest-pixel estimation

### Phase 3: Technical inversion core

Deliverables:
- `density_inversion.py`
- remaining `film_characterization.py`

Acceptance criteria:
- technical positive output is visibly less biased than the current heuristic demasking pipeline
- blue cast frequency is reduced on representative test images
- intermediate density diagnostics are saved or inspectable

### Phase 4: Rendering stage

Deliverables:
- `rendering.py`

Acceptance criteria:
- rendering can improve appearance without changing technical inversion internals
- technical output and rendered output can be compared side by side

### Phase 5: Diagnostics and agentic orchestration

Deliverables:
- `diagnostics.py`
- orchestration integration

Acceptance criteria:
- evaluator can identify likely failure stage
- retries can choose between strategies, not just scalar parameter changes

## 14. Risks and open questions

### Risks

- Border may be partially missing or contaminated by scene content
- Scene-based upper-density estimation may drift on low-contrast frames
- RAW decoding choices may affect density calibration consistency
- IMX447-specific metadata availability may vary by capture path
- Flat-field reference may become invalid if scan geometry or lighting changes

### Open questions

- Should demosaic happen before or after some parts of normalization, or should a Bayer-domain correction path be preserved?
- Should the initial implementation support only DNG-compatible RAW files, or multiple raw formats?
- Should border detection work on full-resolution data or on a proxy with later mask upsampling?
- Should film characterization store per-side base estimates to diagnose asymmetry explicitly?
- At what point should stock-aware profiles or learned correction models be introduced?

## 15. Immediate implementation recommendation

For the first implementation pass, prioritize:
1. IMX447 RAW ingest
2. flat-field correction
3. automatic border detection
4. border-based base estimation
5. density inversion without leader
6. diagnostics for border confidence and blue-cast detection

Leader support should be added next as a calibration enhancement, not as a prerequisite for the baseline pipeline.
