from __future__ import annotations

import json
from unittest.mock import MagicMock

import numpy as np
import pytest

from models import FilmType, PipelineParams, QualityResult
from orchestrator import process_image


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
        score=0.90,
        passed=True,
        feedback="Great",
        suggested_params=PipelineParams(),
    )
    return agent


@pytest.fixture
def failing_then_passing_evaluator():
    agent = MagicMock()
    agent.evaluate.side_effect = [
        QualityResult(
            score=0.50,
            passed=False,
            feedback="Too dark",
            suggested_params=PipelineParams(shadow_lift=15.0),
        ),
        QualityResult(
            score=0.85,
            passed=True,
            feedback="Good now",
            suggested_params=PipelineParams(),
        ),
    ]
    return agent


def test_process_image_passes_first_iteration(
    local_artifact_dir,
    sample_negative_image_path,
    mock_classifier,
    mock_processor,
    passing_evaluator,
):
    output_dir = local_artifact_dir / "output"
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
    local_artifact_dir,
    sample_negative_image_path,
    mock_classifier,
    mock_processor,
    failing_then_passing_evaluator,
):
    output_dir = local_artifact_dir / "output"
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
    second_call_params = mock_processor.process.call_args_list[1][0][2]
    assert second_call_params.shadow_lift == 15.0


def test_process_image_stops_at_max_iterations(
    local_artifact_dir,
    sample_negative_image_path,
    mock_classifier,
    mock_processor,
):
    always_failing = MagicMock()
    always_failing.evaluate.return_value = QualityResult(
        score=0.40,
        passed=False,
        feedback="Bad",
        suggested_params=PipelineParams(),
    )
    output_dir = local_artifact_dir / "output"
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
    local_artifact_dir,
    sample_negative_image_path,
    mock_classifier,
    mock_processor,
    passing_evaluator,
):
    output_dir = local_artifact_dir / "output"
    output_dir.mkdir()
    process_image(
        image_path=sample_negative_image_path,
        output_dir=output_dir,
        classifier=mock_classifier,
        processor=mock_processor,
        evaluator=passing_evaluator,
        max_iterations=3,
    )
    meta = json.loads((output_dir / f"{sample_negative_image_path.stem}_meta.json").read_text())
    assert meta["film_type"] == "negative_35mm"
    assert "iterations" in meta
    assert "final_score" in meta
    assert "feedback" in meta
