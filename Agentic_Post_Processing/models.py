from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FilmType(Enum):
    NEGATIVE_35MM = "negative_35mm"
    NEGATIVE_120 = "negative_120"
    POSITIVE_35MM = "positive_35mm"
    POSITIVE_120 = "positive_120"


@dataclass(slots=True)
class PipelineParams:
    # ── Existing params ───────────────────────────────────────────────────────
    wb_clip_percent: float = 0.5       # percentile clip for per-channel white balance (0.2–1.5)
    black_point: float = 0.8           # percentile for black-level stretch (0.5–2.0)
    white_point: float = 99.2          # percentile for white-level stretch (97.0–99.5)
    clahe_clip: float = 1.4            # CLAHE clip limit (0.5–4.0)
    unsharp_amount: float = 0.35       # unsharp mask strength (0.0–1.0)
    desat_strength: float = 0.20       # selective desaturation on cast regions (0.0–0.6)
    desat_sigma: float = 7.0           # spatial blur sigma for cast mask (2.0–20.0)
    shadow_lift: float = 0.0           # additive shadow lift in 0–255 space (0.0–40.0)

    # ── New params giving the agent wider autonomy ────────────────────────────
    gamma: float = 1.0                 # power-law gamma — <1 brightens, >1 darkens (0.4–2.2)
    lab_strength: float = 0.85         # LAB a/b cast neutralization strength (0.0–1.5)
    clahe_grid: int = 8                # CLAHE tile-grid size: 4, 8, or 16
    highlight_compression: float = 0.0 # soft highlight rolloff to reduce blown highlights (0.0–0.5)
    vibrance: float = 0.0              # selective saturation boost on muted colours (−0.3–0.6)
    unsharp_sigma: float = 1.2         # unsharp mask blur radius — larger = coarser detail (0.5–3.0)


@dataclass(slots=True)
class QualityResult:
    score: float
    passed: bool
    feedback: str
    suggested_params: PipelineParams
