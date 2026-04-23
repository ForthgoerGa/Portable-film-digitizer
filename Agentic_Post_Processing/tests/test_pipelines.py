from __future__ import annotations

import numpy as np
import pytest

from models import PipelineParams
from pipelines.negative import run_negative_pipeline
from pipelines.positive import run_positive_pipeline


@pytest.fixture
def orange_negative_image() -> np.ndarray:
    img = np.zeros((100, 150, 3), dtype=np.uint8)
    img[:, :, 0] = 50
    img[:, :, 1] = 100
    img[:, :, 2] = 200
    return img


@pytest.fixture
def natural_positive_image() -> np.ndarray:
    return np.full((100, 150, 3), 128, dtype=np.uint8)


@pytest.fixture
def masked_negative_scan() -> np.ndarray:
    positive = np.zeros((120, 180, 3), dtype=np.uint8)
    positive[:, :60] = (70, 70, 70)
    positive[:, 60:120] = (120, 170, 210)
    positive[:, 120:] = (200, 140, 90)
    negative = 255 - positive
    orange_mask = np.array([0, 35, 90], dtype=np.uint8)
    return np.clip(negative.astype(np.int16) + orange_mask[None, None, :], 0, 255).astype(np.uint8)


def test_negative_pipeline_inverts(orange_negative_image: np.ndarray):
    result = run_negative_pipeline(orange_negative_image, PipelineParams())
    assert result.shape == orange_negative_image.shape
    assert result.dtype == np.uint8


def test_negative_pipeline_reduces_blue_cast(masked_negative_scan: np.ndarray):
    naive = 255 - masked_negative_scan
    result = run_negative_pipeline(masked_negative_scan, PipelineParams())
    naive_blue_cast = abs(float(naive[:, :, 0].mean() - naive[:, :, 2].mean()))
    result_blue_cast = abs(float(result[:, :, 0].mean() - result[:, :, 2].mean()))
    assert result_blue_cast < naive_blue_cast


def test_negative_pipeline_respects_params(orange_negative_image: np.ndarray):
    low = run_negative_pipeline(orange_negative_image, PipelineParams(clahe_clip=0.5))
    high = run_negative_pipeline(orange_negative_image, PipelineParams(clahe_clip=4.0))
    assert not np.array_equal(low, high)


def test_positive_pipeline_returns_same_shape(natural_positive_image: np.ndarray):
    result = run_positive_pipeline(natural_positive_image, PipelineParams())
    assert result.shape == natural_positive_image.shape
    assert result.dtype == np.uint8


def test_positive_pipeline_shadow_lift_brightens(natural_positive_image: np.ndarray):
    no_lift = run_positive_pipeline(natural_positive_image, PipelineParams(shadow_lift=0.0))
    with_lift = run_positive_pipeline(natural_positive_image, PipelineParams(shadow_lift=20.0))
    assert with_lift.mean() >= no_lift.mean()
