# Agentic Film Post-Processing Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a sequential agentic pipeline in `Agentic_Post_Processing/` that classifies scanned film (negative/positive, 35mm/120), routes to the appropriate OpenCV pipeline, then uses Gemini to evaluate quality and re-run with tuned parameters if needed.

**Architecture:** `ClassifierAgent` (Gemini VLM) → `PostProcessingAgent` (OpenCV) → `EvaluatorAgent` (Gemini VLM) with a feedback loop up to `MAX_EVAL_ITERATIONS`. All agents are Python classes; pipelines are pure functions accepting `PipelineParams`.

**Tech Stack:** Python 3.10+, `google-generativeai`, `opencv-python`, `numpy`, `python-dotenv`, `pytest`

> **Note:** Per user instruction — do NOT commit until the full pipeline is complete and end-to-end tested (Task 9). Individual tasks end with test verification, not commits.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `Agentic_Post_Processing/models.py` | Create | `FilmType`, `PipelineParams`, `QualityResult` dataclasses |
| `Agentic_Post_Processing/config.py` | Create | API keys, model names, thresholds from `.env` |
| `Agentic_Post_Processing/pipelines/__init__.py` | Create | Empty |
| `Agentic_Post_Processing/pipelines/negative.py` | Create | Negative film pipeline (invert→WB→CLAHE→desat→unsharp) |
| `Agentic_Post_Processing/pipelines/positive.py` | Create | Positive film pipeline (WB→shadow lift→CLAHE→unsharp) |
| `Agentic_Post_Processing/agents/__init__.py` | Create | Empty |
| `Agentic_Post_Processing/agents/classifier.py` | Create | `ClassifierAgent` — Gemini VLM → `FilmType` |
| `Agentic_Post_Processing/agents/processor.py` | Create | `PostProcessingAgent` — routes to pipeline |
| `Agentic_Post_Processing/agents/evaluator.py` | Create | `EvaluatorAgent` — Gemini VLM → `QualityResult` |
| `Agentic_Post_Processing/orchestrator.py` | Create | Main loop: classify→process→evaluate→loop |
| `Agentic_Post_Processing/tests/__init__.py` | Create | Empty |
| `Agentic_Post_Processing/tests/conftest.py` | Create | Shared pytest fixtures (sample images, mock Gemini) |
| `Agentic_Post_Processing/tests/test_models.py` | Create | Dataclass construction and defaults |
| `Agentic_Post_Processing/tests/test_pipelines.py` | Create | Pipeline stage correctness on real pixels |
| `Agentic_Post_Processing/tests/test_classifier.py` | Create | ClassifierAgent with mocked Gemini |
| `Agentic_Post_Processing/tests/test_processor.py` | Create | PostProcessingAgent routing |
| `Agentic_Post_Processing/tests/test_evaluator.py` | Create | EvaluatorAgent with mocked Gemini |
| `Agentic_Post_Processing/tests/test_orchestrator.py` | Create | Full loop with all agents mocked |

---

## Task 1: Scaffold + `models.py`

**Files:**
- Create: `Agentic_Post_Processing/models.py`
- Create: `Agentic_Post_Processing/__init__.py`
- Create: `Agentic_Post_Processing/pipelines/__init__.py`
- Create: `Agentic_Post_Processing/agents/__init__.py`
- Create: `Agentic_Post_Processing/tests/__init__.py`
- Create: `Agentic_Post_Processing/tests/test_models.py`

- [ ] **Step 1: Create empty `__init__.py` files**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
touch __init__.py pipelines/__init__.py agents/__init__.py tests/__init__.py
mkdir -p pipelines agents tests output
```

- [ ] **Step 2: Write the failing test**

Create `Agentic_Post_Processing/tests/test_models.py`:

```python
from models import FilmType, PipelineParams, QualityResult


def test_film_type_values():
    assert FilmType.NEGATIVE_35MM.value == "negative_35mm"
    assert FilmType.NEGATIVE_120.value == "negative_120"
    assert FilmType.POSITIVE_35MM.value == "positive_35mm"
    assert FilmType.POSITIVE_120.value == "positive_120"


def test_pipeline_params_defaults():
    p = PipelineParams()
    assert p.wb_clip_percent == 0.5
    assert p.black_point == 0.8
    assert p.white_point == 99.2
    assert p.clahe_clip == 1.4
    assert p.unsharp_amount == 0.35
    assert p.desat_strength == 0.20
    assert p.desat_sigma == 7.0
    assert p.shadow_lift == 0.0


def test_pipeline_params_custom():
    p = PipelineParams(clahe_clip=2.0, shadow_lift=15.0)
    assert p.clahe_clip == 2.0
    assert p.shadow_lift == 15.0
    assert p.wb_clip_percent == 0.5  # other defaults unchanged


def test_quality_result_construction():
    params = PipelineParams(clahe_clip=1.8)
    qr = QualityResult(score=0.82, passed=True, feedback="Good exposure", suggested_params=params)
    assert qr.score == 0.82
    assert qr.passed is True
    assert qr.suggested_params.clahe_clip == 1.8
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -m pytest tests/test_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'models'`

- [ ] **Step 4: Create `Agentic_Post_Processing/models.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


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
    # Positive only (additive shadow lift, 0–30 typical)
    shadow_lift:     float = 0.0


@dataclass
class QualityResult:
    score:            float
    passed:           bool
    feedback:         str
    suggested_params: PipelineParams
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -m pytest tests/test_models.py -v
```

Expected: `4 passed`

---

## Task 2: `config.py`

**Files:**
- Create: `Agentic_Post_Processing/config.py`

- [ ] **Step 1: Install dependencies**

```bash
pip install google-generativeai python-dotenv
```

- [ ] **Step 2: Create `Agentic_Post_Processing/config.py`**

```python
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _env_first(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.getenv(k)
        if v:
            return v
    return default


GEMINI_API_KEY: str = _env_first(
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "VLM_API_KEY",
    "LLM_API_KEY",
    default="",
)
VLM_MODEL: str = _env_first("VLM_MODEL", "GEMINI_VLM_MODEL", default="gemini-2.5-flash")
LLM_MODEL: str = _env_first("LLM_MODEL", "GEMINI_LLM_MODEL", default="gemini-2.5-flash")

MAX_EVAL_ITERATIONS: int = 3
QUALITY_THRESHOLD:   float = 0.75
```

- [ ] **Step 3: Verify config loads without error**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -c "import config; print(config.VLM_MODEL, config.MAX_EVAL_ITERATIONS)"
```

Expected: `gemini-2.5-flash 3`

---

## Task 3: `pipelines/negative.py`

**Files:**
- Create: `Agentic_Post_Processing/pipelines/negative.py`
- Create: `Agentic_Post_Processing/tests/test_pipelines.py` (negative section)

- [ ] **Step 1: Write the failing test**

Create `Agentic_Post_Processing/tests/test_pipelines.py`:

```python
import numpy as np
import pytest

from models import PipelineParams
from pipelines.negative import run_negative_pipeline
from pipelines.positive import run_positive_pipeline


@pytest.fixture
def orange_negative_image():
    """Synthetic orange-tinted 'negative' image: 100x150 BGR."""
    img = np.zeros((100, 150, 3), dtype=np.uint8)
    img[:, :, 0] = 50   # B
    img[:, :, 1] = 100  # G
    img[:, :, 2] = 200  # R (strong red/orange cast = typical neg base)
    return img


@pytest.fixture
def natural_positive_image():
    """Synthetic natural-color 'positive' image: 100x150 BGR."""
    img = np.full((100, 150, 3), 128, dtype=np.uint8)
    return img


def test_negative_pipeline_inverts(orange_negative_image):
    params = PipelineParams()
    result = run_negative_pipeline(orange_negative_image, params)
    assert result.shape == orange_negative_image.shape
    assert result.dtype == np.uint8


def test_negative_pipeline_output_is_not_orange(orange_negative_image):
    """After inversion, the dominant channel should no longer be R."""
    params = PipelineParams()
    result = run_negative_pipeline(orange_negative_image, params)
    mean_b = result[:, :, 0].mean()
    mean_r = result[:, :, 2].mean()
    # Inverted image: was high-R, now high-B
    assert mean_b > mean_r


def test_negative_pipeline_respects_params(orange_negative_image):
    """Higher CLAHE clip should produce different output than lower."""
    low = run_negative_pipeline(orange_negative_image, PipelineParams(clahe_clip=0.5))
    high = run_negative_pipeline(orange_negative_image, PipelineParams(clahe_clip=4.0))
    assert not np.array_equal(low, high)


def test_positive_pipeline_returns_same_shape(natural_positive_image):
    params = PipelineParams()
    result = run_positive_pipeline(natural_positive_image, params)
    assert result.shape == natural_positive_image.shape
    assert result.dtype == np.uint8


def test_positive_pipeline_shadow_lift_brightens(natural_positive_image):
    no_lift = run_positive_pipeline(natural_positive_image, PipelineParams(shadow_lift=0.0))
    with_lift = run_positive_pipeline(natural_positive_image, PipelineParams(shadow_lift=20.0))
    assert with_lift.mean() >= no_lift.mean()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -m pytest tests/test_pipelines.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipelines.negative'`

- [ ] **Step 3: Create `Agentic_Post_Processing/pipelines/negative.py`**

```python
from __future__ import annotations

import cv2
import numpy as np

from models import PipelineParams


def run_negative_pipeline(image_bgr: np.ndarray, params: PipelineParams) -> np.ndarray:
    """Full negative film restoration pipeline on a single pre-stitched image."""
    inverted   = _invert(image_bgr)
    balanced   = _white_balance(inverted, params.wb_clip_percent)
    leveled    = _stretch_percentiles(balanced, params.black_point, params.white_point)
    contrasted = _luminance_clahe(leveled, params.clahe_clip)
    mask       = _cast_suppression_mask(contrasted, params.desat_sigma)
    desatured  = _selective_desaturation(contrasted, mask, params.desat_strength)
    return _unsharp_mask(desatured, params.unsharp_amount)


# --- private stages ---

def _invert(image: np.ndarray) -> np.ndarray:
    return 255 - image


def _white_balance(image: np.ndarray, clip_percent: float) -> np.ndarray:
    if hasattr(cv2, "xphoto") and hasattr(cv2.xphoto, "createSimpleWB"):
        wb = cv2.xphoto.createSimpleWB()
        wb.setP(float(clip_percent))
        return wb.balanceWhite(image)
    return _stretch_percentiles(image, clip_percent, 100.0 - clip_percent)


def _stretch_percentiles(image: np.ndarray, black: float, white: float) -> np.ndarray:
    src = image.astype(np.float32)
    out = np.empty_like(src)
    for c in range(3):
        ch = src[..., c]
        lo, hi = np.percentile(ch, [black, white])
        if hi - lo < 1.0:
            out[..., c] = ch
            continue
        out[..., c] = (ch - lo) * (255.0 / (hi - lo))
    return np.clip(out, 0, 255).astype(np.uint8)


def _luminance_clahe(image: np.ndarray, clip_limit: float, tile_grid: int = 8) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_grid, tile_grid))
    return cv2.cvtColor(cv2.merge((clahe.apply(l), a, b)), cv2.COLOR_LAB2BGR)


def _cast_suppression_mask(
    image: np.ndarray,
    sigma: float,
    sat_threshold: float = 0.22,
    val_threshold: float = 0.55,
) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    sat = hsv[..., 1] / 255.0
    val = hsv[..., 2] / 255.0
    sat_term = np.clip((sat - sat_threshold) / max(1.0 - sat_threshold, 1e-6), 0.0, 1.0)
    val_term = np.clip((val - val_threshold) / max(1.0 - val_threshold, 1e-6), 0.0, 1.0)
    mask = cv2.GaussianBlur(sat_term * val_term, (0, 0), sigmaX=sigma, sigmaY=sigma)
    scale = np.percentile(mask, 99.5)
    if scale > 1e-6:
        mask = np.clip(mask / scale, 0.0, 1.0)
    return mask


def _selective_desaturation(image: np.ndarray, mask: np.ndarray, strength: float) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    alpha = np.clip(mask * strength, 0.0, 1.0)
    hsv[..., 1] = np.clip(hsv[..., 1] * (1.0 - alpha), 0.0, 255.0)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _unsharp_mask(image: np.ndarray, amount: float, sigma: float = 1.2) -> np.ndarray:
    src = image.astype(np.float32)
    blur = cv2.GaussianBlur(src, (0, 0), sigmaX=sigma, sigmaY=sigma)
    return np.clip(src + amount * (src - blur), 0, 255).astype(np.uint8)
```

- [ ] **Step 4: Run negative pipeline tests**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -m pytest tests/test_pipelines.py::test_negative_pipeline_inverts \
  tests/test_pipelines.py::test_negative_pipeline_output_is_not_orange \
  tests/test_pipelines.py::test_negative_pipeline_respects_params -v
```

Expected: `3 passed`

---

## Task 4: `pipelines/positive.py`

**Files:**
- Create: `Agentic_Post_Processing/pipelines/positive.py`

- [ ] **Step 1: Create `Agentic_Post_Processing/pipelines/positive.py`**

```python
from __future__ import annotations

import cv2
import numpy as np

from models import PipelineParams


def run_positive_pipeline(image_bgr: np.ndarray, params: PipelineParams) -> np.ndarray:
    """Positive/slide film restoration pipeline."""
    balanced   = _white_balance(image_bgr, params.wb_clip_percent)
    lifted     = _shadow_lift(balanced, params.shadow_lift)
    contrasted = _luminance_clahe(lifted, clip_limit=min(params.clahe_clip, 1.0))
    return _unsharp_mask(contrasted, params.unsharp_amount)


# --- private stages ---

def _white_balance(image: np.ndarray, clip_percent: float) -> np.ndarray:
    if hasattr(cv2, "xphoto") and hasattr(cv2.xphoto, "createSimpleWB"):
        wb = cv2.xphoto.createSimpleWB()
        wb.setP(float(clip_percent))
        return wb.balanceWhite(image)
    src = image.astype(np.float32)
    out = np.empty_like(src)
    for c in range(3):
        ch = src[..., c]
        lo, hi = np.percentile(ch, [clip_percent, 100.0 - clip_percent])
        if hi - lo < 1.0:
            out[..., c] = ch
            continue
        out[..., c] = (ch - lo) * (255.0 / (hi - lo))
    return np.clip(out, 0, 255).astype(np.uint8)


def _shadow_lift(image: np.ndarray, lift: float) -> np.ndarray:
    if lift == 0.0:
        return image
    return np.clip(image.astype(np.float32) + lift, 0, 255).astype(np.uint8)


def _luminance_clahe(image: np.ndarray, clip_limit: float, tile_grid: int = 8) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_grid, tile_grid))
    return cv2.cvtColor(cv2.merge((clahe.apply(l), a, b)), cv2.COLOR_LAB2BGR)


def _unsharp_mask(image: np.ndarray, amount: float, sigma: float = 1.2) -> np.ndarray:
    src = image.astype(np.float32)
    blur = cv2.GaussianBlur(src, (0, 0), sigmaX=sigma, sigmaY=sigma)
    return np.clip(src + amount * (src - blur), 0, 255).astype(np.uint8)
```

- [ ] **Step 2: Run all pipeline tests**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -m pytest tests/test_pipelines.py -v
```

Expected: `5 passed`

---

## Task 5: `agents/classifier.py`

**Files:**
- Create: `Agentic_Post_Processing/agents/classifier.py`
- Create: `Agentic_Post_Processing/tests/conftest.py`
- Create: `Agentic_Post_Processing/tests/test_classifier.py`

- [ ] **Step 1: Create `Agentic_Post_Processing/tests/conftest.py`**

```python
import numpy as np
import pytest
import cv2
from pathlib import Path
import tempfile


@pytest.fixture
def sample_negative_image_path(tmp_path):
    """Write a synthetic orange-tinted negative scan to disk."""
    img = np.zeros((200, 300, 3), dtype=np.uint8)
    img[:, :, 0] = 50
    img[:, :, 1] = 100
    img[:, :, 2] = 200
    path = tmp_path / "negative.jpg"
    cv2.imwrite(str(path), img)
    return path


@pytest.fixture
def sample_positive_image_path(tmp_path):
    """Write a synthetic natural-color positive scan with dark borders to disk."""
    img = np.full((200, 300, 3), 128, dtype=np.uint8)
    img[:10, :] = 10   # dark top border
    img[-10:, :] = 10  # dark bottom border
    path = tmp_path / "positive.jpg"
    cv2.imwrite(str(path), img)
    return path


@pytest.fixture
def sample_bgr_image():
    img = np.full((100, 150, 3), 128, dtype=np.uint8)
    return img
```

- [ ] **Step 2: Write the failing classifier test**

Create `Agentic_Post_Processing/tests/test_classifier.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from agents.classifier import ClassifierAgent
from models import FilmType


def _make_mock_model(response_text: str) -> MagicMock:
    mock_response = MagicMock()
    mock_response.text = response_text
    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response
    return mock_model


def test_classifier_returns_negative_35mm(sample_negative_image_path):
    mock_model = _make_mock_model('{"film_type": "negative_35mm"}')
    agent = ClassifierAgent(model=mock_model)
    result = agent.classify(sample_negative_image_path)
    assert result == FilmType.NEGATIVE_35MM


def test_classifier_returns_positive_35mm(sample_positive_image_path):
    mock_model = _make_mock_model('{"film_type": "positive_35mm"}')
    agent = ClassifierAgent(model=mock_model)
    result = agent.classify(sample_positive_image_path)
    assert result == FilmType.POSITIVE_35MM


def test_classifier_returns_negative_120(sample_negative_image_path):
    mock_model = _make_mock_model('{"film_type": "negative_120"}')
    agent = ClassifierAgent(model=mock_model)
    result = agent.classify(sample_negative_image_path)
    assert result == FilmType.NEGATIVE_120


def test_classifier_defaults_on_bad_json(sample_negative_image_path):
    """Unparseable Gemini response falls back to NEGATIVE_35MM."""
    mock_model = _make_mock_model("sorry, I cannot determine this")
    agent = ClassifierAgent(model=mock_model)
    result = agent.classify(sample_negative_image_path)
    assert result == FilmType.NEGATIVE_35MM


def test_classifier_calls_model_once(sample_negative_image_path):
    mock_model = _make_mock_model('{"film_type": "positive_120"}')
    agent = ClassifierAgent(model=mock_model)
    agent.classify(sample_negative_image_path)
    assert mock_model.generate_content.call_count == 1
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -m pytest tests/test_classifier.py -v
```

Expected: `ModuleNotFoundError: No module named 'agents.classifier'`

- [ ] **Step 4: Create `Agentic_Post_Processing/agents/classifier.py`**

```python
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import google.generativeai as genai

import config
from models import FilmType

logger = logging.getLogger(__name__)

_PROMPT = """\
You are a film scanner assistant. Examine this scanned film image and classify it.

Classification rules:
- If the overall image has an orange/brownish cast and colors appear inverted (sky looks dark, shadows light) → NEGATIVE film
- If the borders/edges of the frame are dark or black and colors appear natural → POSITIVE film (slide/transparency)
- Aspect ratio close to 1:1 (0.8–1.2 ratio) → 120 medium format; otherwise → 35mm

Respond with ONLY a valid JSON object, no markdown, no explanation:
{"film_type": "negative_35mm" | "negative_120" | "positive_35mm" | "positive_120"}
"""

_VALID_TYPES = {ft.value for ft in FilmType}
_DEFAULT = FilmType.NEGATIVE_35MM


class ClassifierAgent:
    def __init__(self, model=None) -> None:
        if model is None:
            genai.configure(api_key=config.GEMINI_API_KEY)
            model = genai.GenerativeModel(config.VLM_MODEL)
        self._model = model

    def classify(self, image_path: Path) -> FilmType:
        image_data = _encode_image(image_path)
        try:
            response = self._model.generate_content([image_data, _PROMPT])
            raw = response.text.strip()
            payload = json.loads(raw)
            value = payload.get("film_type", "")
            if value in _VALID_TYPES:
                return FilmType(value)
            logger.warning("Unexpected film_type value %r, defaulting to %s", value, _DEFAULT)
        except (json.JSONDecodeError, KeyError, Exception) as exc:
            logger.warning("Classifier parse error (%s), defaulting to %s", exc, _DEFAULT)
        return _DEFAULT


def _encode_image(image_path: Path) -> dict:
    suffix = image_path.suffix.lower().lstrip(".")
    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
    mime = mime_map.get(suffix, "image/jpeg")
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return {"inline_data": {"mime_type": mime, "data": data}}
```

- [ ] **Step 5: Run classifier tests**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -m pytest tests/test_classifier.py -v
```

Expected: `5 passed`

---

## Task 6: `agents/processor.py`

**Files:**
- Create: `Agentic_Post_Processing/agents/processor.py`
- Create: `Agentic_Post_Processing/tests/test_processor.py`

- [ ] **Step 1: Write the failing test**

Create `Agentic_Post_Processing/tests/test_processor.py`:

```python
import numpy as np
import pytest

from agents.processor import PostProcessingAgent
from models import FilmType, PipelineParams


@pytest.fixture
def agent():
    return PostProcessingAgent()


@pytest.fixture
def bgr_image():
    img = np.zeros((100, 150, 3), dtype=np.uint8)
    img[:, :, 0] = 50
    img[:, :, 1] = 100
    img[:, :, 2] = 200
    return img


def test_processes_negative_35mm(agent, bgr_image):
    result = agent.process(bgr_image, FilmType.NEGATIVE_35MM, PipelineParams())
    assert result.shape == bgr_image.shape
    assert result.dtype == np.uint8


def test_processes_negative_120(agent, bgr_image):
    result = agent.process(bgr_image, FilmType.NEGATIVE_120, PipelineParams())
    assert result.shape == bgr_image.shape
    assert result.dtype == np.uint8


def test_processes_positive_35mm(agent, bgr_image):
    result = agent.process(bgr_image, FilmType.POSITIVE_35MM, PipelineParams())
    assert result.shape == bgr_image.shape
    assert result.dtype == np.uint8


def test_processes_positive_120(agent, bgr_image):
    result = agent.process(bgr_image, FilmType.POSITIVE_120, PipelineParams())
    assert result.shape == bgr_image.shape
    assert result.dtype == np.uint8


def test_negative_routes_to_negative_pipeline(agent, bgr_image):
    """Negative output should differ from positive output (inversion distinguishes them)."""
    neg_result = agent.process(bgr_image, FilmType.NEGATIVE_35MM, PipelineParams())
    pos_result = agent.process(bgr_image, FilmType.POSITIVE_35MM, PipelineParams())
    assert not np.array_equal(neg_result, pos_result)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -m pytest tests/test_processor.py -v
```

Expected: `ModuleNotFoundError: No module named 'agents.processor'`

- [ ] **Step 3: Create `Agentic_Post_Processing/agents/processor.py`**

```python
from __future__ import annotations

import numpy as np

from models import FilmType, PipelineParams
from pipelines.negative import run_negative_pipeline
from pipelines.positive import run_positive_pipeline

_NEGATIVE_TYPES = {FilmType.NEGATIVE_35MM, FilmType.NEGATIVE_120}


class PostProcessingAgent:
    def process(
        self,
        image_bgr: np.ndarray,
        film_type: FilmType,
        params: PipelineParams,
    ) -> np.ndarray:
        if film_type in _NEGATIVE_TYPES:
            return run_negative_pipeline(image_bgr, params)
        return run_positive_pipeline(image_bgr, params)
```

- [ ] **Step 4: Run processor tests**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -m pytest tests/test_processor.py -v
```

Expected: `5 passed`

---

## Task 7: `agents/evaluator.py`

**Files:**
- Create: `Agentic_Post_Processing/agents/evaluator.py`
- Create: `Agentic_Post_Processing/tests/test_evaluator.py`

- [ ] **Step 1: Write the failing test**

Create `Agentic_Post_Processing/tests/test_evaluator.py`:

```python
from unittest.mock import MagicMock
import json

import numpy as np
import pytest

from agents.evaluator import EvaluatorAgent
from models import PipelineParams, QualityResult


def _make_mock_model(response_text: str) -> MagicMock:
    mock_response = MagicMock()
    mock_response.text = response_text
    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response
    return mock_model


@pytest.fixture
def processed_image():
    return np.full((100, 150, 3), 128, dtype=np.uint8)


_GOOD_RESPONSE = json.dumps({
    "score": 0.88,
    "feedback": "Good exposure and color balance.",
    "suggested_params": {
        "wb_clip_percent": 0.5,
        "black_point": 0.8,
        "white_point": 99.2,
        "clahe_clip": 1.4,
        "unsharp_amount": 0.35,
        "desat_strength": 0.20,
        "desat_sigma": 7.0,
        "shadow_lift": 0.0,
    }
})

_TUNE_RESPONSE = json.dumps({
    "score": 0.55,
    "feedback": "Shadows too dark, needs lift.",
    "suggested_params": {
        "wb_clip_percent": 0.6,
        "black_point": 1.0,
        "white_point": 98.5,
        "clahe_clip": 1.8,
        "unsharp_amount": 0.4,
        "desat_strength": 0.25,
        "desat_sigma": 8.0,
        "shadow_lift": 12.0,
    }
})


def test_evaluator_passes_high_score(processed_image):
    agent = EvaluatorAgent(model=_make_mock_model(_GOOD_RESPONSE), threshold=0.75)
    result = agent.evaluate(processed_image)
    assert isinstance(result, QualityResult)
    assert result.score == 0.88
    assert result.passed is True
    assert result.feedback == "Good exposure and color balance."


def test_evaluator_fails_low_score(processed_image):
    agent = EvaluatorAgent(model=_make_mock_model(_TUNE_RESPONSE), threshold=0.75)
    result = agent.evaluate(processed_image)
    assert result.passed is False
    assert result.score == 0.55


def test_evaluator_suggested_params_populated(processed_image):
    agent = EvaluatorAgent(model=_make_mock_model(_TUNE_RESPONSE), threshold=0.75)
    result = agent.evaluate(processed_image)
    assert result.suggested_params.shadow_lift == 12.0
    assert result.suggested_params.clahe_clip == 1.8


def test_evaluator_fallback_on_bad_json(processed_image):
    """Bad JSON returns a failing result with default params."""
    agent = EvaluatorAgent(model=_make_mock_model("not json at all"), threshold=0.75)
    result = agent.evaluate(processed_image)
    assert result.passed is False
    assert result.score == 0.0
    assert isinstance(result.suggested_params, PipelineParams)


def test_evaluator_calls_model_once(processed_image):
    mock_model = _make_mock_model(_GOOD_RESPONSE)
    agent = EvaluatorAgent(model=mock_model, threshold=0.75)
    agent.evaluate(processed_image)
    assert mock_model.generate_content.call_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -m pytest tests/test_evaluator.py -v
```

Expected: `ModuleNotFoundError: No module named 'agents.evaluator'`

- [ ] **Step 3: Create `Agentic_Post_Processing/agents/evaluator.py`**

```python
from __future__ import annotations

import base64
import json
import logging
import tempfile
from pathlib import Path

import cv2
import numpy as np
import google.generativeai as genai

import config
from models import PipelineParams, QualityResult

logger = logging.getLogger(__name__)

_PROMPT = """\
You are a film scan quality evaluator. Analyze this processed film scan image.

Score it from 0.0 (unusable) to 1.0 (excellent) on:
1. Exposure: highlights clipped to pure white or shadows crushed to pure black? (-0.2 each)
2. Color balance: strong unnatural color cast present? (-0.2)
3. Sharpness: appropriately sharp without excessive edge halos? (-0.15)
4. Artifacts: visible banding, noise amplification, or unnatural gradients? (-0.15)

Also suggest improved pipeline parameters if the score is below 0.75.

Respond with ONLY valid JSON, no markdown fences:
{
  "score": <float 0.0-1.0>,
  "feedback": "<one sentence>",
  "suggested_params": {
    "wb_clip_percent": <float 0.2-1.5>,
    "black_point": <float 0.5-2.0>,
    "white_point": <float 97.0-99.5>,
    "clahe_clip": <float 0.5-3.0>,
    "unsharp_amount": <float 0.1-0.8>,
    "desat_strength": <float 0.0-0.5>,
    "desat_sigma": <float 3.0-15.0>,
    "shadow_lift": <float 0.0-30.0>
  }
}
"""


class EvaluatorAgent:
    def __init__(self, model=None, threshold: float | None = None) -> None:
        if model is None:
            genai.configure(api_key=config.GEMINI_API_KEY)
            model = genai.GenerativeModel(config.VLM_MODEL)
        self._model = model
        self._threshold = threshold if threshold is not None else config.QUALITY_THRESHOLD

    def evaluate(self, image_bgr: np.ndarray) -> QualityResult:
        image_data = _encode_array(image_bgr)
        try:
            response = self._model.generate_content([image_data, _PROMPT])
            payload = json.loads(response.text.strip())
            score = float(payload["score"])
            feedback = str(payload.get("feedback", ""))
            sp = payload.get("suggested_params", {})
            suggested = PipelineParams(
                wb_clip_percent=float(sp.get("wb_clip_percent", 0.5)),
                black_point=float(sp.get("black_point", 0.8)),
                white_point=float(sp.get("white_point", 99.2)),
                clahe_clip=float(sp.get("clahe_clip", 1.4)),
                unsharp_amount=float(sp.get("unsharp_amount", 0.35)),
                desat_strength=float(sp.get("desat_strength", 0.20)),
                desat_sigma=float(sp.get("desat_sigma", 7.0)),
                shadow_lift=float(sp.get("shadow_lift", 0.0)),
            )
            return QualityResult(
                score=score,
                passed=score >= self._threshold,
                feedback=feedback,
                suggested_params=suggested,
            )
        except (json.JSONDecodeError, KeyError, ValueError, Exception) as exc:
            logger.warning("Evaluator parse error (%s), returning failure result", exc)
            return QualityResult(
                score=0.0,
                passed=False,
                feedback=f"Parse error: {exc}",
                suggested_params=PipelineParams(),
            )


def _encode_array(image_bgr: np.ndarray) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        tmp_path = Path(f.name)
    cv2.imwrite(str(tmp_path), image_bgr)
    with open(tmp_path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    tmp_path.unlink(missing_ok=True)
    return {"inline_data": {"mime_type": "image/jpeg", "data": data}}
```

- [ ] **Step 4: Run evaluator tests**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -m pytest tests/test_evaluator.py -v
```

Expected: `5 passed`

---

## Task 8: `orchestrator.py`

**Files:**
- Create: `Agentic_Post_Processing/orchestrator.py`
- Create: `Agentic_Post_Processing/tests/test_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Create `Agentic_Post_Processing/tests/test_orchestrator.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch
import json

import numpy as np
import pytest

from models import FilmType, PipelineParams, QualityResult
from orchestrator import process_image, process_directory


@pytest.fixture
def mock_classifier():
    agent = MagicMock()
    agent.classify.return_value = FilmType.NEGATIVE_35MM
    return agent


@pytest.fixture
def mock_processor():
    agent = MagicMock()
    agent.process.return_value = np.full((100, 150, 3), 128, dtype=np.uint8)
    return agent


@pytest.fixture
def passing_evaluator():
    agent = MagicMock()
    agent.evaluate.return_value = QualityResult(
        score=0.90, passed=True, feedback="Great", suggested_params=PipelineParams()
    )
    return agent


@pytest.fixture
def failing_then_passing_evaluator():
    agent = MagicMock()
    agent.evaluate.side_effect = [
        QualityResult(score=0.50, passed=False, feedback="Too dark",
                      suggested_params=PipelineParams(shadow_lift=15.0)),
        QualityResult(score=0.85, passed=True, feedback="Good now",
                      suggested_params=PipelineParams()),
    ]
    return agent


def test_process_image_passes_first_iteration(
    tmp_path, sample_negative_image_path,
    mock_classifier, mock_processor, passing_evaluator
):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    result = process_image(
        image_path=sample_negative_image_path,
        output_dir=output_dir,
        classifier=mock_classifier,
        processor=mock_processor,
        evaluator=passing_evaluator,
        max_iterations=3,
    )
    assert result["passed"] is True
    assert result["iterations"] == 1
    assert result["final_score"] == 0.90
    mock_processor.process.assert_called_once()
    assert (output_dir / f"{sample_negative_image_path.stem}_processed.png").exists()
    assert (output_dir / f"{sample_negative_image_path.stem}_meta.json").exists()


def test_process_image_retries_on_failure(
    tmp_path, sample_negative_image_path,
    mock_classifier, mock_processor, failing_then_passing_evaluator
):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    result = process_image(
        image_path=sample_negative_image_path,
        output_dir=output_dir,
        classifier=mock_classifier,
        processor=mock_processor,
        evaluator=failing_then_passing_evaluator,
        max_iterations=3,
    )
    assert result["iterations"] == 2
    assert result["passed"] is True
    assert mock_processor.process.call_count == 2
    # Second call should use suggested params from first evaluation
    second_call_params = mock_processor.process.call_args_list[1][0][2]
    assert second_call_params.shadow_lift == 15.0


def test_process_image_stops_at_max_iterations(
    tmp_path, sample_negative_image_path,
    mock_classifier, mock_processor
):
    always_failing = MagicMock()
    always_failing.evaluate.return_value = QualityResult(
        score=0.40, passed=False, feedback="Bad", suggested_params=PipelineParams()
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    result = process_image(
        image_path=sample_negative_image_path,
        output_dir=output_dir,
        classifier=mock_classifier,
        processor=mock_processor,
        evaluator=always_failing,
        max_iterations=2,
    )
    assert result["iterations"] == 2
    assert mock_processor.process.call_count == 2


def test_meta_json_contains_film_type(
    tmp_path, sample_negative_image_path,
    mock_classifier, mock_processor, passing_evaluator
):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    process_image(
        image_path=sample_negative_image_path,
        output_dir=output_dir,
        classifier=mock_classifier,
        processor=mock_processor,
        evaluator=passing_evaluator,
        max_iterations=3,
    )
    meta_path = output_dir / f"{sample_negative_image_path.stem}_meta.json"
    meta = json.loads(meta_path.read_text())
    assert meta["film_type"] == "negative_35mm"
    assert "iterations" in meta
    assert "final_score" in meta
    assert "feedback" in meta
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -m pytest tests/test_orchestrator.py -v
```

Expected: `ModuleNotFoundError: No module named 'orchestrator'`

- [ ] **Step 3: Create `Agentic_Post_Processing/orchestrator.py`**

```python
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

import config
from agents.classifier import ClassifierAgent
from agents.evaluator import EvaluatorAgent
from agents.processor import PostProcessingAgent
from models import PipelineParams

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def process_image(
    image_path: Path,
    output_dir: Path,
    classifier: ClassifierAgent | None = None,
    processor: PostProcessingAgent | None = None,
    evaluator: EvaluatorAgent | None = None,
    max_iterations: int | None = None,
) -> dict[str, Any]:
    classifier    = classifier    or ClassifierAgent()
    processor     = processor     or PostProcessingAgent()
    evaluator     = evaluator     or EvaluatorAgent()
    max_iters     = max_iterations if max_iterations is not None else config.MAX_EVAL_ITERATIONS

    image_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image_bgr is None:
        raise ValueError(f"Cannot read image: {image_path}")

    film_type = classifier.classify(image_path)
    logger.info("Classified %s as %s", image_path.name, film_type.value)

    params = PipelineParams()
    result_img = None
    quality = None

    for iteration in range(1, max_iters + 1):
        logger.info("Processing iteration %d/%d with params: %s", iteration, max_iters, params)
        result_img = processor.process(image_bgr, film_type, params)
        quality = evaluator.evaluate(result_img)
        logger.info("Evaluation score: %.2f — %s", quality.score, quality.feedback)

        if quality.passed:
            logger.info("Quality passed on iteration %d", iteration)
            break

        if iteration < max_iters:
            params = quality.suggested_params
        else:
            logger.info("Max iterations reached, saving best result")

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = image_path.stem
    out_image_path = output_dir / f"{stem}_processed.png"
    meta_path      = output_dir / f"{stem}_meta.json"

    cv2.imwrite(str(out_image_path), result_img)

    meta = {
        "source":      image_path.name,
        "film_type":   film_type.value,
        "iterations":  iteration,
        "final_score": quality.score,
        "passed":      quality.passed,
        "feedback":    quality.feedback,
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    logger.info("Saved: %s", out_image_path)

    return meta


def process_directory(
    input_dir: Path,
    output_dir: Path,
    classifier: ClassifierAgent | None = None,
    processor: PostProcessingAgent | None = None,
    evaluator: EvaluatorAgent | None = None,
    max_iterations: int | None = None,
) -> list[dict[str, Any]]:
    extensions = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
    images = sorted(p for p in input_dir.iterdir() if p.suffix.lower() in extensions)
    if not images:
        logger.warning("No images found in %s", input_dir)
        return []

    results = []
    for image_path in images:
        logger.info("--- Processing %s ---", image_path.name)
        try:
            meta = process_image(
                image_path=image_path,
                output_dir=output_dir,
                classifier=classifier,
                processor=processor,
                evaluator=evaluator,
                max_iterations=max_iterations,
            )
            results.append(meta)
        except Exception as exc:
            logger.error("Failed to process %s: %s", image_path.name, exc)
            results.append({"source": image_path.name, "error": str(exc)})
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Agentic film post-processing pipeline")
    parser.add_argument("--input-dir",  type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--max-iter",   type=int,  default=config.MAX_EVAL_ITERATIONS)
    args = parser.parse_args()

    results = process_directory(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        max_iterations=args.max_iter,
    )
    print(json.dumps(results, indent=2))
```

- [ ] **Step 4: Run orchestrator tests**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -m pytest tests/test_orchestrator.py -v
```

Expected: `4 passed`

---

## Task 9: Full Test Suite + End-to-End Smoke Test + Commit

- [ ] **Step 1: Run the full test suite**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python -m pytest tests/ -v
```

Expected: all tests pass (models: 4, pipelines: 5, classifier: 5, processor: 5, evaluator: 5, orchestrator: 4 = **28 passed**)

- [ ] **Step 2: Run end-to-end smoke test on real data**

```bash
cd /mnt/c/ece_445/Agentic_Post_Processing
python orchestrator.py --input-dir data --output-dir output --max-iter 2
```

Expected: images from `data/` processed to `output/`, each with `_processed.png` and `_meta.json`. Check that `film_type` classification looks correct for each image.

- [ ] **Step 3: Inspect outputs**

```bash
ls output/
cat output/*_meta.json
```

Verify: `film_type` is reasonable (e.g., orange-tinted scans → `negative_*`), `final_score` is populated, output images exist.

- [ ] **Step 4: Commit everything**

```bash
cd /mnt/c/ece_445
git add \
  Agentic_Post_Processing/models.py \
  Agentic_Post_Processing/config.py \
  Agentic_Post_Processing/orchestrator.py \
  Agentic_Post_Processing/__init__.py \
  Agentic_Post_Processing/pipelines/ \
  Agentic_Post_Processing/agents/ \
  Agentic_Post_Processing/tests/ \
  Agentic_Post_Processing/.env.example \
  docs/superpowers/specs/2026-04-06-agentic-film-pipeline-design.md \
  docs/superpowers/plans/2026-04-06-agentic-film-pipeline.md
git commit -m "$(cat <<'EOF'
Add agentic film post-processing pipeline

Sequential ClassifierAgent → PostProcessingAgent → EvaluatorAgent
pipeline for 35mm/120 negative and positive film. Uses Gemini vision
API for classification and quality evaluation with parameter feedback loop.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```
