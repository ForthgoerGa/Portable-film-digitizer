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

