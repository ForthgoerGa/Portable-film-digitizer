from __future__ import annotations

import numpy as np
import pytest

from agents.processor import PostProcessingAgent
from models import FilmType, PipelineParams


@pytest.fixture
def agent() -> PostProcessingAgent:
    return PostProcessingAgent()


@pytest.fixture
def bgr_image() -> np.ndarray:
    return np.full((100, 150, 3), 128, dtype=np.uint8)


def test_processes_negative_35mm(agent: PostProcessingAgent, bgr_image: np.ndarray):
    result = agent.process(bgr_image, FilmType.NEGATIVE_35MM, PipelineParams())
    assert result.shape == bgr_image.shape
    assert result.dtype == np.uint8


def test_processes_negative_120(agent: PostProcessingAgent, bgr_image: np.ndarray):
    result = agent.process(bgr_image, FilmType.NEGATIVE_120, PipelineParams())
    assert result.shape == bgr_image.shape
    assert result.dtype == np.uint8


def test_processes_positive_35mm(agent: PostProcessingAgent, bgr_image: np.ndarray):
    result = agent.process(bgr_image, FilmType.POSITIVE_35MM, PipelineParams())
    assert result.shape == bgr_image.shape
    assert result.dtype == np.uint8


def test_processes_positive_120(agent: PostProcessingAgent, bgr_image: np.ndarray):
    result = agent.process(bgr_image, FilmType.POSITIVE_120, PipelineParams())
    assert result.shape == bgr_image.shape
    assert result.dtype == np.uint8


def test_negative_routes_to_negative_pipeline(agent: PostProcessingAgent, bgr_image: np.ndarray):
    neg_result = agent.process(bgr_image, FilmType.NEGATIVE_35MM, PipelineParams())
    pos_result = agent.process(bgr_image, FilmType.POSITIVE_35MM, PipelineParams())
    assert not np.array_equal(neg_result, pos_result)

