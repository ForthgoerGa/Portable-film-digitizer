"""raw_pipeline — experimental RAW-aware film negative inversion pipeline.

This package is additive / work-in-progress and does not modify the existing
agentic pipeline. See pipeline_update.md for the design rationale.
"""

from .models import (
    RawFrame,
    FlatFieldModel,
    BorderDetectionResult,
    FilmCharacterization,
    InversionResult,
    RenderResult,
    PipelineResult,
)
from .pipeline import process_raw_negative

__all__ = [
    "RawFrame",
    "FlatFieldModel",
    "BorderDetectionResult",
    "FilmCharacterization",
    "InversionResult",
    "RenderResult",
    "PipelineResult",
    "process_raw_negative",
]
