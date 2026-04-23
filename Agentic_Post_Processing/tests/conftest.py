from __future__ import annotations

from pathlib import Path
import shutil
import uuid

import cv2
import numpy as np
import pytest


@pytest.fixture
def local_artifact_dir() -> Path:
    base = Path(__file__).resolve().parents[1] / "test_artifacts"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"run_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def sample_negative_image_path(local_artifact_dir: Path) -> Path:
    img = np.zeros((200, 300, 3), dtype=np.uint8)
    img[:, :, 0] = 50
    img[:, :, 1] = 100
    img[:, :, 2] = 200
    path = local_artifact_dir / "negative.jpg"
    assert cv2.imwrite(str(path), img)
    return path


@pytest.fixture
def sample_positive_image_path(local_artifact_dir: Path) -> Path:
    img = np.full((200, 300, 3), 128, dtype=np.uint8)
    img[:12, :] = 10
    img[-12:, :] = 10
    path = local_artifact_dir / "positive.jpg"
    assert cv2.imwrite(str(path), img)
    return path


@pytest.fixture
def sample_bgr_image() -> np.ndarray:
    return np.full((100, 150, 3), 128, dtype=np.uint8)
