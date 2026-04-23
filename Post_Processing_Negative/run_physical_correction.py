from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from negative_physical import color_refinement as stage3_color_refinement
from negative_physical import perceptual_tone as stage2_perceptual_tone
from negative_physical.camera_color import (
    STAGE10_MATRIX_PRESETS,
    apply_stage10_soft_reference_mapping,
)
from negative_physical.color_refinement import apply_stage3_pseudo_lut_color_refinement
from negative_physical.evaluator_agent import FlatFieldEvaluatorAgent, load_env_file
from negative_physical.final_finish import apply_stage4_final_finish_and_export
from negative_physical.flat_field import (
    apply_flat_field,
    build_flat_model,
    diagnose_flat_correction,
)
from negative_physical.negative_inversion import (
    base_reference_to_dict,
    estimate_base_reference,
    process_negative_stages,
    save_base_roi_overlay,
)
from negative_physical.perceptual_tone import apply_stage2_perceptual_tone_base
from negative_physical.raw_io import load_raw_bayer
from negative_physical.reference_layout import load_reference_layout, rois_to_dicts
from negative_physical.render_preview import (
    demosaic_to_rgb,
    save_display_rgb_png,
    save_illumination_preview,
    save_linear_rgb16_png,
    save_preview_png,
)


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = SCRIPT_DIR / "rpi_captures_white"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output_physical"
RAW_EXTENSIONS = {".dng", ".arw", ".nef", ".cr2", ".cr3", ".raf", ".rw2", ".orf", ".pef"}
BACKLIGHT_EXACT_STEMS = {
    "backlight",
    "backlight_frame",
    "blacklight",
    "blacklight_frame",
    "flat",
    "flat_field",
    "flatfield",
}
BACKLIGHT_NAME_HINTS = ("backlight", "blacklight", "flat", "illumination", "light")
load_env_file(SCRIPT_DIR / ".env")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bayer-domain physical correction for negative scan RAW files."
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument(
        "--backlight-frame",
        "--flat",
        dest="backlight_frame",
        type=Path,
        default=None,
        help=(
            "Explicit backlight/flat-field RAW path. The --flat spelling is kept "
            "as a compatibility alias."
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--reference-layout", type=Path, default=None)
    parser.add_argument("--base-frame", type=Path, default=None)
    parser.add_argument("--frame", type=Path, action="append", default=None)
    parser.add_argument(
        "--flat-strength",
        type=float,
        default=1.0,
        help=(
            "Flat-field correction strength. 1.0 is direct physical division, "
            ">1.0 strengthens residual light-spot/vignetting correction, 0 disables it."
        ),
    )
    parser.add_argument(
        "--min-flat-strength",
        type=float,
        default=0.70,
        help="Lower bound for per-frame evaluator strength search.",
    )
    parser.add_argument(
        "--max-flat-strength",
        type=float,
        default=1.80,
        help="Upper bound for per-frame evaluator strength search.",
    )
    parser.add_argument(
        "--flat-sigma-frac",
        type=float,
        default=0.02,
        help=(
            "Gaussian sigma as a fraction of the flat-map working diagonal. "
            "Smaller values keep more local light-spot detail."
        ),
    )
    parser.add_argument(
        "--flat-max-side",
        type=int,
        default=1024,
        help="Working resolution for flat-map estimation; larger keeps finer detail.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=1,
        help=(
            "Maximum per-frame strength adjustment attempts. The default 1 is a "
            "single fine-heuristic flat-field pass with no evaluator loop; set >1 "
            "to enable automatic strength search."
        ),
    )
    parser.add_argument(
        "--quality-threshold",
        type=float,
        default=float(os.getenv("QUALITY_THRESHOLD", "0.75")),
        help="Evaluator score required to accept a frame.",
    )
    parser.add_argument(
        "--agent-evaluator",
        action="store_true",
        help="Enable VLM evaluation from .env during strength search.",
    )
    parser.add_argument(
        "--no-agent-evaluator",
        action="store_true",
        help="Deprecated compatibility flag; VLM evaluator is off by default.",
    )
    parser.add_argument("--preview-gamma", type=float, default=2.2)
    parser.add_argument("--density-percentile", type=float, default=99.0)
    parser.add_argument(
        "--inversion-curve-strength",
        type=float,
        default=2.2,
        help="Deprecated; the upgraded stage [9] uses expm1 channel gains instead.",
    )
    parser.add_argument(
        "--inversion-channel-gains",
        type=float,
        nargs=3,
        default=[1.35, 1.20, 1.10],
        metavar=("R_GAIN", "G_GAIN", "B_GAIN"),
        help="Per-channel gains for stage [9] expm1 density inversion.",
    )
    parser.add_argument(
        "--inversion-output-percentile",
        type=float,
        default=99.5,
        help="Per-channel percentile used to normalize stage [9] expm1 inversion.",
    )
    parser.add_argument(
        "--color-unmix-strength",
        type=float,
        default=1.0,
        help="Blend strength for the density-domain 3x3 dye-channel color unmixing pass.",
    )
    parser.add_argument(
        "--color-unmix-matrix",
        type=float,
        nargs=9,
        default=None,
        metavar="M",
        help="Optional row-major 3x3 color unmix matrix. Defaults to a conservative matrix.",
    )
    parser.add_argument(
        "--no-color-neutral-balance",
        action="store_true",
        help="Disable the mid-tone neutral balance applied after density-domain color unmixing.",
    )
    parser.add_argument(
        "--stage10-matrix-preset",
        choices=sorted(STAGE10_MATRIX_PRESETS),
        default="medium",
        help="Empirical matrix preset for stage10_soft_reference_mapping.",
    )
    parser.add_argument(
        "--stage10-empirical-matrix",
        type=float,
        nargs=9,
        default=None,
        metavar="M",
        help="Optional row-major 3x3 matrix overriding --stage10-matrix-preset.",
    )
    parser.add_argument(
        "--disable-stage10-gray-anchor",
        action="store_true",
        help="Disable stage [10a] weak gray anchor.",
    )
    parser.add_argument("--stage10-gray-anchor-percentile", type=float, default=30.0)
    parser.add_argument("--stage10-gray-anchor-strength", type=float, default=0.35)
    parser.add_argument("--stage10-gray-anchor-eps", type=float, default=1e-6)
    parser.add_argument(
        "--disable-stage10-soft-matrix",
        action="store_true",
        help="Disable stage [10b] soft empirical matrix.",
    )
    parser.add_argument("--stage10-soft-matrix-strength", type=float, default=0.15)
    parser.add_argument(
        "--disable-stage10-neutral-damp",
        action="store_true",
        help="Disable stage [10c] neutral damp.",
    )
    parser.add_argument("--stage10-neutral-damp-strength", type=float, default=0.25)
    parser.add_argument("--stage10-neutral-damp-sigma", type=float, default=0.10)
    parser.add_argument(
        "--disable-stage10-red-guard",
        action="store_true",
        help="Disable stage [10d] red guard.",
    )
    parser.add_argument("--stage10-red-guard-threshold", type=float, default=0.05)
    parser.add_argument("--stage10-red-guard-strength", type=float, default=0.30)
    parser.add_argument(
        "--disable-stage2-gray-norm",
        action="store_true",
        help="Disable stage [2a] gray anchor normalization.",
    )
    parser.add_argument("--stage2-gray-norm-percentile", type=float, default=30.0)
    parser.add_argument(
        "--stage2-gray-norm-method",
        choices=("mean_luma", "rgb_mean"),
        default="mean_luma",
    )
    parser.add_argument("--stage2-gray-norm-strength", type=float, default=0.20)
    parser.add_argument("--stage2-gray-norm-eps", type=float, default=1e-6)
    parser.add_argument(
        "--disable-stage2-perceptual-space",
        action="store_true",
        help="Disable stage [2b] Lab conversion and skip later Stage 2 tone steps.",
    )
    parser.add_argument(
        "--stage2-lab-input-mode",
        choices=("srgb_encoded", "linear_direct"),
        default="srgb_encoded",
        help="Default srgb_encoded uses percentile normalization plus gamma before Lab.",
    )
    parser.add_argument("--stage2-lab-input-percentile", type=float, default=99.5)
    parser.add_argument("--stage2-lab-input-gamma", type=float, default=2.2)
    parser.add_argument("--stage2-lab-input-eps", type=float, default=1e-6)
    parser.add_argument(
        "--disable-stage2-zoned-tone",
        action="store_true",
        help="Disable stage [2c] zoned luminance tone mapping.",
    )
    parser.add_argument("--stage2-shadow-threshold", type=float, default=0.28)
    parser.add_argument("--stage2-highlight-threshold", type=float, default=0.72)
    parser.add_argument("--stage2-shadow-gamma", type=float, default=0.90)
    parser.add_argument("--stage2-mid-sigmoid-k", type=float, default=3.5)
    parser.add_argument("--stage2-mid-sigmoid-x0", type=float, default=0.50)
    parser.add_argument("--stage2-highlight-local-strength", type=float, default=1.0)
    parser.add_argument(
        "--disable-stage2-highlight-rolloff",
        action="store_true",
        help="Disable stage [2d] global luminance highlight roll-off.",
    )
    parser.add_argument("--stage2-highlight-rolloff-alpha", type=float, default=0.20)
    parser.add_argument("--stage2-highlight-rolloff-power", type=float, default=1.5)
    parser.add_argument(
        "--disable-stage2-preview",
        action="store_true",
        help="Disable dedicated Stage 2 preview generation and save clipped linear RGB instead.",
    )
    parser.add_argument("--stage2-preview-percentile", type=float, default=99.5)
    parser.add_argument("--stage2-preview-gamma", type=float, default=2.2)
    parser.add_argument("--stage2-preview-eps", type=float, default=1e-6)
    parser.add_argument(
        "--skip-stage2-debug-png",
        action="store_true",
        help="Skip Stage 2 L-channel debug PNGs and save only the RGB Stage 2 output.",
    )
    parser.add_argument("--stage3-lab-input-percentile", type=float, default=99.5)
    parser.add_argument("--stage3-lab-input-gamma", type=float, default=2.2)
    parser.add_argument("--stage3-lab-input-eps", type=float, default=1e-6)
    parser.add_argument(
        "--disable-stage3-luma-chroma",
        action="store_true",
        help="Disable stage [3b] luminance-driven chroma scaling.",
    )
    parser.add_argument("--stage3-sat-base", type=float, default=0.92)
    parser.add_argument("--stage3-sat-mid-gain", type=float, default=0.34)
    parser.add_argument("--stage3-sat-mid-center", type=float, default=0.48)
    parser.add_argument("--stage3-sat-mid-sigma", type=float, default=0.18)
    parser.add_argument("--stage3-sat-highlight-decay", type=float, default=0.18)
    parser.add_argument("--stage3-sat-scale-min", type=float, default=0.75)
    parser.add_argument("--stage3-sat-scale-max", type=float, default=1.25)
    parser.add_argument(
        "--disable-stage3-hue-adjust",
        action="store_true",
        help="Disable stage [3c] hue-dependent pseudo-LUT adjustment.",
    )
    parser.add_argument("--stage3-red-hue-min", type=float, default=-0.45)
    parser.add_argument("--stage3-red-hue-max", type=float, default=0.55)
    parser.add_argument("--stage3-red-a-scale", type=float, default=1.05)
    parser.add_argument("--stage3-red-b-scale", type=float, default=0.94)
    parser.add_argument("--stage3-blue-abs-hue-threshold", type=float, default=2.1)
    parser.add_argument("--stage3-blue-a-scale", type=float, default=0.97)
    parser.add_argument("--stage3-blue-b-scale", type=float, default=1.08)
    parser.add_argument("--stage3-green-hue-min", type=float, default=0.9)
    parser.add_argument("--stage3-green-hue-max", type=float, default=1.8)
    parser.add_argument("--stage3-green-a-scale", type=float, default=0.96)
    parser.add_argument("--stage3-green-b-scale", type=float, default=1.03)
    parser.add_argument(
        "--disable-stage3-neutral-protect",
        action="store_true",
        help="Disable stage [3d] neutral region protection.",
    )
    parser.add_argument("--stage3-neutral-percentile", type=float, default=20.0)
    parser.add_argument("--stage3-neutral-shrink", type=float, default=0.45)
    parser.add_argument(
        "--stage3-neutral-soft-enabled",
        action="store_true",
        help="Use soft neutral weighting within the neutral mask.",
    )
    parser.add_argument("--stage3-neutral-sigma", type=float, default=8.0)
    parser.add_argument("--stage3-neutral-strength", type=float, default=0.55)
    parser.add_argument(
        "--disable-stage3-preview",
        action="store_true",
        help="Disable dedicated Stage 3 preview generation and save clipped linear RGB instead.",
    )
    parser.add_argument("--stage3-preview-percentile", type=float, default=99.5)
    parser.add_argument("--stage3-preview-gamma", type=float, default=2.2)
    parser.add_argument("--stage3-preview-eps", type=float, default=1e-6)
    parser.add_argument(
        "--skip-stage3-debug-png",
        action="store_true",
        help="Skip Stage 3 debug maps and save only the RGB Stage 3 output.",
    )
    parser.add_argument(
        "--disable-stage4-display-mapping",
        action="store_true",
        help="Disable stage [4a] final display mapping.",
    )
    parser.add_argument(
        "--stage4-display-mapping-mode",
        choices=("simple_gamma", "srgb_oetf"),
        default="simple_gamma",
    )
    parser.add_argument("--stage4-display-percentile", type=float, default=99.5)
    parser.add_argument("--stage4-display-gamma", type=float, default=2.2)
    parser.add_argument("--stage4-display-eps", type=float, default=1e-6)
    parser.add_argument(
        "--disable-stage4-final-trim",
        action="store_true",
        help="Disable stage [4b] final global trim.",
    )
    parser.add_argument("--stage4-final-exposure-ev", type=float, default=0.0)
    parser.add_argument("--stage4-final-black-point", type=float, default=0.0)
    parser.add_argument("--stage4-final-white-point", type=float, default=1.0)
    parser.add_argument("--stage4-final-contrast", type=float, default=1.02)
    parser.add_argument("--stage4-final-temp-shift", type=float, default=0.0)
    parser.add_argument("--stage4-final-tint-shift", type=float, default=0.0)
    parser.add_argument("--stage4-highlight-desat-enabled", action="store_true")
    parser.add_argument("--stage4-highlight-desat-threshold", type=float, default=0.80)
    parser.add_argument("--stage4-highlight-desat-strength", type=float, default=0.15)
    parser.add_argument("--stage4-shadow-neutralize-enabled", action="store_true")
    parser.add_argument("--stage4-shadow-neutralize-threshold", type=float, default=0.20)
    parser.add_argument("--stage4-shadow-neutralize-strength", type=float, default=0.10)
    parser.add_argument("--stage4-grain-enabled", action="store_true")
    parser.add_argument("--stage4-grain-strength", type=float, default=0.02)
    parser.add_argument("--stage4-grain-size", type=float, default=1.0)
    parser.add_argument("--stage4-bloom-enabled", action="store_true")
    parser.add_argument("--stage4-bloom-threshold", type=float, default=0.85)
    parser.add_argument("--stage4-bloom-strength", type=float, default=0.05)
    parser.add_argument("--stage4-bloom-sigma", type=float, default=3.0)
    parser.add_argument("--stage4-final-softness-enabled", action="store_true")
    parser.add_argument("--stage4-final-softness-sigma", type=float, default=0.6)
    parser.add_argument("--stage4-final-sharpen-enabled", action="store_true")
    parser.add_argument("--stage4-final-sharpen-amount", type=float, default=0.10)
    parser.add_argument(
        "--stage4-master-output-mode",
        choices=("display_mapped_float", "linear_float"),
        default="display_mapped_float",
    )
    parser.add_argument(
        "--skip-stage4-debug-png",
        action="store_true",
        help="Skip Stage 4 step previews and save only final preview/master outputs.",
    )
    parser.add_argument(
        "--skip-npy",
        action="store_true",
        help="Write PNG previews only and skip large .npy intermediate arrays.",
    )
    parser.add_argument(
        "--skip-linear16",
        action="store_true",
        help="Skip large 16-bit PNG intermediates and write visual-check PNGs only.",
    )
    parser.add_argument(
        "--verify-stage23-contract-only",
        action="store_true",
        help=(
            "Run a synthetic Stage 2/3 color-space contract smoke test and exit. "
            "This does not process RAW captures."
        ),
    )
    args = parser.parse_args()
    if args.flat_strength < 0.0:
        parser.error("--flat-strength must be >= 0")
    if args.min_flat_strength < 0.0 or args.max_flat_strength <= args.min_flat_strength:
        parser.error("--min-flat-strength/--max-flat-strength define an invalid range")
    if args.flat_sigma_frac <= 0.0:
        parser.error("--flat-sigma-frac must be > 0")
    if args.flat_max_side < 64:
        parser.error("--flat-max-side must be >= 64")
    if args.max_iterations < 1:
        parser.error("--max-iterations must be >= 1")
    if args.color_unmix_strength < 0.0:
        parser.error("--color-unmix-strength must be >= 0")
    if args.inversion_output_percentile <= 0.0 or args.inversion_output_percentile > 100.0:
        parser.error("--inversion-output-percentile must be in (0, 100]")
    if args.stage10_gray_anchor_eps <= 0.0:
        parser.error("--stage10-gray-anchor-eps must be > 0")
    if args.stage10_neutral_damp_sigma <= 0.0:
        parser.error("--stage10-neutral-damp-sigma must be > 0")
    if args.stage2_gray_norm_eps <= 0.0:
        parser.error("--stage2-gray-norm-eps must be > 0")
    if args.stage2_gray_norm_strength < 0.0 or args.stage2_gray_norm_strength > 1.0:
        parser.error("--stage2-gray-norm-strength must be in [0, 1]")
    if args.stage2_gray_norm_percentile < 0.0 or args.stage2_gray_norm_percentile > 100.0:
        parser.error("--stage2-gray-norm-percentile must be in [0, 100]")
    if args.stage2_lab_input_percentile <= 0.0 or args.stage2_lab_input_percentile > 100.0:
        parser.error("--stage2-lab-input-percentile must be in (0, 100]")
    if args.stage2_lab_input_gamma <= 0.0:
        parser.error("--stage2-lab-input-gamma must be > 0")
    if args.stage2_lab_input_eps <= 0.0:
        parser.error("--stage2-lab-input-eps must be > 0")
    if not 0.0 < args.stage2_shadow_threshold < args.stage2_highlight_threshold:
        parser.error("--stage2-shadow-threshold must be > 0 and below --stage2-highlight-threshold")
    if args.stage2_highlight_threshold > 1.0:
        parser.error("--stage2-highlight-threshold must be <= 1")
    if args.stage2_shadow_gamma <= 0.0:
        parser.error("--stage2-shadow-gamma must be > 0")
    if args.stage2_highlight_local_strength < 0.0:
        parser.error("--stage2-highlight-local-strength must be >= 0")
    if args.stage2_highlight_rolloff_alpha < 0.0:
        parser.error("--stage2-highlight-rolloff-alpha must be >= 0")
    if args.stage2_highlight_rolloff_power <= 0.0:
        parser.error("--stage2-highlight-rolloff-power must be > 0")
    if args.stage2_preview_percentile <= 0.0 or args.stage2_preview_percentile > 100.0:
        parser.error("--stage2-preview-percentile must be in (0, 100]")
    if args.stage2_preview_gamma <= 0.0:
        parser.error("--stage2-preview-gamma must be > 0")
    if args.stage2_preview_eps <= 0.0:
        parser.error("--stage2-preview-eps must be > 0")
    if args.stage3_lab_input_percentile <= 0.0 or args.stage3_lab_input_percentile > 100.0:
        parser.error("--stage3-lab-input-percentile must be in (0, 100]")
    if args.stage3_lab_input_gamma <= 0.0:
        parser.error("--stage3-lab-input-gamma must be > 0")
    if args.stage3_lab_input_eps <= 0.0:
        parser.error("--stage3-lab-input-eps must be > 0")
    if args.stage3_sat_mid_sigma <= 0.0:
        parser.error("--stage3-sat-mid-sigma must be > 0")
    if args.stage3_sat_scale_min <= 0.0 or args.stage3_sat_scale_max < args.stage3_sat_scale_min:
        parser.error("--stage3-sat-scale-min/--stage3-sat-scale-max define an invalid range")
    if args.stage3_neutral_percentile < 0.0 or args.stage3_neutral_percentile > 100.0:
        parser.error("--stage3-neutral-percentile must be in [0, 100]")
    if args.stage3_neutral_shrink < 0.0 or args.stage3_neutral_shrink > 1.0:
        parser.error("--stage3-neutral-shrink must be in [0, 1]")
    if args.stage3_neutral_sigma <= 0.0:
        parser.error("--stage3-neutral-sigma must be > 0")
    if args.stage3_neutral_strength < 0.0 or args.stage3_neutral_strength > 1.0:
        parser.error("--stage3-neutral-strength must be in [0, 1]")
    if args.stage3_preview_percentile <= 0.0 or args.stage3_preview_percentile > 100.0:
        parser.error("--stage3-preview-percentile must be in (0, 100]")
    if args.stage3_preview_gamma <= 0.0:
        parser.error("--stage3-preview-gamma must be > 0")
    if args.stage3_preview_eps <= 0.0:
        parser.error("--stage3-preview-eps must be > 0")
    if args.stage4_display_percentile <= 0.0 or args.stage4_display_percentile > 100.0:
        parser.error("--stage4-display-percentile must be in (0, 100]")
    if args.stage4_display_gamma <= 0.0:
        parser.error("--stage4-display-gamma must be > 0")
    if args.stage4_display_eps <= 0.0:
        parser.error("--stage4-display-eps must be > 0")
    if args.stage4_final_black_point < 0.0 or args.stage4_final_black_point >= 1.0:
        parser.error("--stage4-final-black-point must be in [0, 1)")
    if args.stage4_final_white_point <= 0.0:
        parser.error("--stage4-final-white-point must be > 0")
    if args.stage4_final_contrast <= 0.0:
        parser.error("--stage4-final-contrast must be > 0")
    if args.stage4_highlight_desat_strength < 0.0 or args.stage4_highlight_desat_strength > 1.0:
        parser.error("--stage4-highlight-desat-strength must be in [0, 1]")
    if args.stage4_shadow_neutralize_strength < 0.0 or args.stage4_shadow_neutralize_strength > 1.0:
        parser.error("--stage4-shadow-neutralize-strength must be in [0, 1]")
    if args.stage4_grain_strength < 0.0:
        parser.error("--stage4-grain-strength must be >= 0")
    if args.stage4_grain_size <= 0.0:
        parser.error("--stage4-grain-size must be > 0")
    if args.stage4_bloom_strength < 0.0:
        parser.error("--stage4-bloom-strength must be >= 0")
    if args.stage4_bloom_sigma <= 0.0:
        parser.error("--stage4-bloom-sigma must be > 0")
    if args.stage4_final_softness_sigma <= 0.0:
        parser.error("--stage4-final-softness-sigma must be > 0")
    if args.stage4_final_sharpen_amount < 0.0:
        parser.error("--stage4-final-sharpen-amount must be >= 0")
    if args.verify_stage23_contract_only:
        verification = _run_stage23_contract_verification()
        print(json.dumps(verification, indent=2))
        if not verification["all_passed"]:
            raise SystemExit(1)
        return

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    backlight_path = _resolve_backlight_path(input_dir, args.backlight_frame)
    base_frame_path = _resolve_base_frame_path(input_dir, args.base_frame)
    reference_layout = (
        args.reference_layout or (input_dir / "reference_layout.json")
    ).resolve()
    reference_rois = rois_to_dicts(load_reference_layout(reference_layout))
    frame_paths = _resolve_frame_paths(input_dir, args.frame, base_frame_path)
    agent_evaluator_enabled = bool(args.agent_evaluator and not args.no_agent_evaluator)
    strength_search_enabled = bool(agent_evaluator_enabled or args.max_iterations > 1)

    output_dir.mkdir(parents=True, exist_ok=True)
    stage3_params: dict[str, Any] = {
        "lab_input_percentile": float(args.stage3_lab_input_percentile),
        "lab_input_gamma": float(args.stage3_lab_input_gamma),
        "lab_input_eps": float(args.stage3_lab_input_eps),
        "luma_chroma_enabled": not bool(args.disable_stage3_luma_chroma),
        "sat_base": float(args.stage3_sat_base),
        "sat_mid_gain": float(args.stage3_sat_mid_gain),
        "sat_mid_center": float(args.stage3_sat_mid_center),
        "sat_mid_sigma": float(args.stage3_sat_mid_sigma),
        "sat_highlight_decay": float(args.stage3_sat_highlight_decay),
        "sat_scale_min": float(args.stage3_sat_scale_min),
        "sat_scale_max": float(args.stage3_sat_scale_max),
        "hue_adjust_enabled": not bool(args.disable_stage3_hue_adjust),
        "red_hue_min": float(args.stage3_red_hue_min),
        "red_hue_max": float(args.stage3_red_hue_max),
        "red_a_scale": float(args.stage3_red_a_scale),
        "red_b_scale": float(args.stage3_red_b_scale),
        "blue_abs_hue_threshold": float(args.stage3_blue_abs_hue_threshold),
        "blue_a_scale": float(args.stage3_blue_a_scale),
        "blue_b_scale": float(args.stage3_blue_b_scale),
        "green_hue_min": float(args.stage3_green_hue_min),
        "green_hue_max": float(args.stage3_green_hue_max),
        "green_a_scale": float(args.stage3_green_a_scale),
        "green_b_scale": float(args.stage3_green_b_scale),
        "neutral_protect_enabled": not bool(args.disable_stage3_neutral_protect),
        "neutral_percentile": float(args.stage3_neutral_percentile),
        "neutral_shrink": float(args.stage3_neutral_shrink),
        "neutral_soft_enabled": bool(args.stage3_neutral_soft_enabled),
        "neutral_sigma": float(args.stage3_neutral_sigma),
        "neutral_strength": float(args.stage3_neutral_strength),
        "preview_enabled": not bool(args.disable_stage3_preview),
        "preview_percentile": float(args.stage3_preview_percentile),
        "preview_gamma": float(args.stage3_preview_gamma),
        "preview_eps": float(args.stage3_preview_eps),
    }
    stage3_report_params = dict(stage3_params)
    stage3_report_params["debug_png_enabled"] = not bool(args.skip_stage3_debug_png)
    stage4_params: dict[str, Any] = {
        "display_mapping_enabled": not bool(args.disable_stage4_display_mapping),
        "display_mapping_mode": args.stage4_display_mapping_mode,
        "display_percentile": float(args.stage4_display_percentile),
        "display_gamma": float(args.stage4_display_gamma),
        "display_eps": float(args.stage4_display_eps),
        "final_trim_enabled": not bool(args.disable_stage4_final_trim),
        "final_exposure_ev": float(args.stage4_final_exposure_ev),
        "final_black_point": float(args.stage4_final_black_point),
        "final_white_point": float(args.stage4_final_white_point),
        "final_contrast": float(args.stage4_final_contrast),
        "final_temp_shift": float(args.stage4_final_temp_shift),
        "final_tint_shift": float(args.stage4_final_tint_shift),
        "highlight_desat_enabled": bool(args.stage4_highlight_desat_enabled),
        "highlight_desat_threshold": float(args.stage4_highlight_desat_threshold),
        "highlight_desat_strength": float(args.stage4_highlight_desat_strength),
        "shadow_neutralize_enabled": bool(args.stage4_shadow_neutralize_enabled),
        "shadow_neutralize_threshold": float(args.stage4_shadow_neutralize_threshold),
        "shadow_neutralize_strength": float(args.stage4_shadow_neutralize_strength),
        "grain_enabled": bool(args.stage4_grain_enabled),
        "grain_strength": float(args.stage4_grain_strength),
        "grain_size": float(args.stage4_grain_size),
        "bloom_enabled": bool(args.stage4_bloom_enabled),
        "bloom_threshold": float(args.stage4_bloom_threshold),
        "bloom_strength": float(args.stage4_bloom_strength),
        "bloom_sigma": float(args.stage4_bloom_sigma),
        "final_softness_enabled": bool(args.stage4_final_softness_enabled),
        "final_softness_sigma": float(args.stage4_final_softness_sigma),
        "final_sharpen_enabled": bool(args.stage4_final_sharpen_enabled),
        "final_sharpen_amount": float(args.stage4_final_sharpen_amount),
        "master_output_mode": args.stage4_master_output_mode,
    }
    stage4_report_params = dict(stage4_params)
    stage4_report_params["debug_png_enabled"] = not bool(args.skip_stage4_debug_png)

    report: dict[str, Any] = {
        "status": "ok",
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "backlight_frame_path": str(backlight_path),
        "base_frame_path": str(base_frame_path),
        "flat_path": str(backlight_path),
        "flat_field_strength": float(args.flat_strength),
        "flat_sigma_frac": float(args.flat_sigma_frac),
        "flat_max_side": int(args.flat_max_side),
        "max_iterations": int(args.max_iterations),
        "strength_search_enabled": strength_search_enabled,
        "agent_evaluator_enabled": agent_evaluator_enabled,
        "quality_threshold": float(args.quality_threshold),
        "density_percentile": float(args.density_percentile),
        "inversion_curve_strength_deprecated": float(args.inversion_curve_strength),
        "inversion_channel_gains": [float(v) for v in args.inversion_channel_gains],
        "inversion_output_percentile": float(args.inversion_output_percentile),
        "color_unmix_strength": float(args.color_unmix_strength),
        "color_unmix_matrix": args.color_unmix_matrix,
        "color_neutral_balance": not bool(args.no_color_neutral_balance),
        "stage10_soft_reference_mapping": {
            "matrix_preset": args.stage10_matrix_preset,
            "empirical_matrix": args.stage10_empirical_matrix,
            "gray_anchor_enabled": not bool(args.disable_stage10_gray_anchor),
            "gray_anchor_percentile": float(args.stage10_gray_anchor_percentile),
            "gray_anchor_strength": float(args.stage10_gray_anchor_strength),
            "gray_anchor_eps": float(args.stage10_gray_anchor_eps),
            "soft_matrix_enabled": not bool(args.disable_stage10_soft_matrix),
            "soft_matrix_strength": float(args.stage10_soft_matrix_strength),
            "neutral_damp_enabled": not bool(args.disable_stage10_neutral_damp),
            "neutral_damp_strength": float(args.stage10_neutral_damp_strength),
            "neutral_damp_sigma": float(args.stage10_neutral_damp_sigma),
            "red_guard_enabled": not bool(args.disable_stage10_red_guard),
            "red_guard_threshold": float(args.stage10_red_guard_threshold),
            "red_guard_strength": float(args.stage10_red_guard_strength),
        },
        "stage2_perceptual_tone_base": {
            "gray_norm_enabled": not bool(args.disable_stage2_gray_norm),
            "gray_norm_percentile": float(args.stage2_gray_norm_percentile),
            "gray_norm_method": args.stage2_gray_norm_method,
            "gray_norm_strength": float(args.stage2_gray_norm_strength),
            "gray_norm_eps": float(args.stage2_gray_norm_eps),
            "perceptual_space_enabled": not bool(args.disable_stage2_perceptual_space),
            "lab_input_mode": args.stage2_lab_input_mode,
            "lab_input_percentile": float(args.stage2_lab_input_percentile),
            "lab_input_gamma": float(args.stage2_lab_input_gamma),
            "lab_input_eps": float(args.stage2_lab_input_eps),
            "zoned_tone_enabled": not bool(args.disable_stage2_zoned_tone),
            "shadow_threshold": float(args.stage2_shadow_threshold),
            "highlight_threshold": float(args.stage2_highlight_threshold),
            "shadow_gamma": float(args.stage2_shadow_gamma),
            "mid_sigmoid_k": float(args.stage2_mid_sigmoid_k),
            "mid_sigmoid_x0": float(args.stage2_mid_sigmoid_x0),
            "highlight_local_strength": float(args.stage2_highlight_local_strength),
            "highlight_rolloff_enabled": not bool(args.disable_stage2_highlight_rolloff),
            "highlight_rolloff_alpha": float(args.stage2_highlight_rolloff_alpha),
            "highlight_rolloff_power": float(args.stage2_highlight_rolloff_power),
            "preview_enabled": not bool(args.disable_stage2_preview),
            "preview_percentile": float(args.stage2_preview_percentile),
            "preview_gamma": float(args.stage2_preview_gamma),
            "preview_eps": float(args.stage2_preview_eps),
            "debug_png_enabled": not bool(args.skip_stage2_debug_png),
        },
        "stage3_pseudo_lut_color_refinement": stage3_report_params,
        "stage4_final_finish_and_export": stage4_report_params,
        "stop_stage": "stage4_final_finish_and_export",
        "tone_mapping_enabled": True,
        "color_refinement_enabled": True,
        "final_gamma_enabled": True,
        "final_export_enabled": True,
        "save_npy": not bool(args.skip_npy),
        "save_linear16": not bool(args.skip_linear16),
        "backlight_selection": {
            "explicit_path": str(args.backlight_frame.resolve()) if args.backlight_frame else None,
            "method": "explicit" if args.backlight_frame else "auto_discovered",
        },
        "reference_layout": str(reference_layout),
        "reference_rois": reference_rois,
        "base_reference": None,
        "base_frame_result": None,
        "frames": [],
        "warnings": [],
    }

    print(f"Loading backlight flat-field frame: {backlight_path}")
    flat_frame = load_raw_bayer(backlight_path)
    flat_model = build_flat_model(
        flat_frame,
        sigma_frac=args.flat_sigma_frac,
        max_side=args.flat_max_side,
    )
    report["flat_metadata"] = flat_frame.metadata
    report["flat_model"] = flat_model.diagnostics

    if not args.skip_npy:
        np.save(output_dir / "flat_illumination_map.npy", flat_model.illumination_map)
    save_illumination_preview(
        flat_model.illumination_map,
        flat_model.cfa_pattern,
        output_dir / "flat_illumination_preview.png",
    )

    evaluator = None
    if strength_search_enabled:
        evaluator = FlatFieldEvaluatorAgent(
            env_path=SCRIPT_DIR / ".env",
            threshold=args.quality_threshold,
            min_strength=args.min_flat_strength,
            max_strength=args.max_flat_strength,
            enabled=agent_evaluator_enabled,
        )

    print(f"Processing base reference frame: {base_frame_path.name}")
    base_frame = load_raw_bayer(base_frame_path)
    base_frame_result, base_rgb_linear = _process_frame_with_evaluator(
        base_frame,
        flat_model,
        evaluator,
        output_dir,
        reference_rois,
        initial_strength=args.flat_strength,
        max_iterations=args.max_iterations,
        min_strength=args.min_flat_strength,
        max_strength=args.max_flat_strength,
        preview_gamma=args.preview_gamma,
        strength_search_enabled=strength_search_enabled,
        save_npy=not args.skip_npy,
        save_linear16=not args.skip_linear16,
    )
    base_reference = estimate_base_reference(
        base_rgb_linear,
        reference_rois,
        source_frame=str(base_frame_path),
        roi_name="base",
    )
    save_base_roi_overlay(
        base_rgb_linear,
        base_reference,
        output_dir / "base_reference_roi_overlay.png",
    )
    _save_negative_stage_outputs(
        frame=base_frame,
        flat_model=flat_model,
        frame_result=base_frame_result,
        base_reference=base_reference,
        rgb_linear=base_rgb_linear,
        output_dir=output_dir,
        preview_gamma=args.preview_gamma,
        density_percentile=args.density_percentile,
        inversion_curve_strength=args.inversion_curve_strength,
        color_unmix_matrix=args.color_unmix_matrix,
        color_unmix_strength=args.color_unmix_strength,
        color_neutral_balance=not args.no_color_neutral_balance,
        inversion_channel_gains=args.inversion_channel_gains,
        inversion_output_percentile=args.inversion_output_percentile,
        stage10_matrix_preset=args.stage10_matrix_preset,
        stage10_empirical_matrix=args.stage10_empirical_matrix,
        stage10_gray_anchor_enabled=not args.disable_stage10_gray_anchor,
        stage10_gray_anchor_percentile=args.stage10_gray_anchor_percentile,
        stage10_gray_anchor_strength=args.stage10_gray_anchor_strength,
        stage10_gray_anchor_eps=args.stage10_gray_anchor_eps,
        stage10_soft_matrix_enabled=not args.disable_stage10_soft_matrix,
        stage10_soft_matrix_strength=args.stage10_soft_matrix_strength,
        stage10_neutral_damp_enabled=not args.disable_stage10_neutral_damp,
        stage10_neutral_damp_strength=args.stage10_neutral_damp_strength,
        stage10_neutral_damp_sigma=args.stage10_neutral_damp_sigma,
        stage10_red_guard_enabled=not args.disable_stage10_red_guard,
        stage10_red_guard_threshold=args.stage10_red_guard_threshold,
        stage10_red_guard_strength=args.stage10_red_guard_strength,
        stage2_gray_norm_enabled=not args.disable_stage2_gray_norm,
        stage2_gray_norm_percentile=args.stage2_gray_norm_percentile,
        stage2_gray_norm_method=args.stage2_gray_norm_method,
        stage2_gray_norm_strength=args.stage2_gray_norm_strength,
        stage2_gray_norm_eps=args.stage2_gray_norm_eps,
        stage2_perceptual_space_enabled=not args.disable_stage2_perceptual_space,
        stage2_lab_input_mode=args.stage2_lab_input_mode,
        stage2_lab_input_percentile=args.stage2_lab_input_percentile,
        stage2_lab_input_gamma=args.stage2_lab_input_gamma,
        stage2_lab_input_eps=args.stage2_lab_input_eps,
        stage2_zoned_tone_enabled=not args.disable_stage2_zoned_tone,
        stage2_shadow_threshold=args.stage2_shadow_threshold,
        stage2_highlight_threshold=args.stage2_highlight_threshold,
        stage2_shadow_gamma=args.stage2_shadow_gamma,
        stage2_mid_sigmoid_k=args.stage2_mid_sigmoid_k,
        stage2_mid_sigmoid_x0=args.stage2_mid_sigmoid_x0,
        stage2_highlight_local_strength=args.stage2_highlight_local_strength,
        stage2_highlight_rolloff_enabled=not args.disable_stage2_highlight_rolloff,
        stage2_highlight_rolloff_alpha=args.stage2_highlight_rolloff_alpha,
        stage2_highlight_rolloff_power=args.stage2_highlight_rolloff_power,
        stage2_preview_enabled=not args.disable_stage2_preview,
        stage2_preview_percentile=args.stage2_preview_percentile,
        stage2_preview_gamma=args.stage2_preview_gamma,
        stage2_preview_eps=args.stage2_preview_eps,
        save_stage2_debug_png=not args.skip_stage2_debug_png,
        stage3_params=stage3_params,
        save_stage3_debug_png=not args.skip_stage3_debug_png,
        stage4_params=stage4_params,
        save_stage4_debug_png=not args.skip_stage4_debug_png,
        save_npy=not args.skip_npy,
        save_linear16=not args.skip_linear16,
    )
    report["base_reference"] = base_reference_to_dict(base_reference)
    report["base_frame_result"] = base_frame_result
    report["warnings"].extend(base_frame_result.get("warnings", []))

    for frame_path in frame_paths:
        print(f"Processing frame: {frame_path.name}")
        frame = load_raw_bayer(frame_path)
        frame_result, frame_rgb_linear = _process_frame_with_evaluator(
            frame,
            flat_model,
            evaluator,
            output_dir,
            reference_rois,
            initial_strength=args.flat_strength,
            max_iterations=args.max_iterations,
            min_strength=args.min_flat_strength,
            max_strength=args.max_flat_strength,
            preview_gamma=args.preview_gamma,
            strength_search_enabled=strength_search_enabled,
            save_npy=not args.skip_npy,
            save_linear16=not args.skip_linear16,
        )
        _save_negative_stage_outputs(
            frame=frame,
            flat_model=flat_model,
            frame_result=frame_result,
            base_reference=base_reference,
            rgb_linear=frame_rgb_linear,
            output_dir=output_dir,
            preview_gamma=args.preview_gamma,
            density_percentile=args.density_percentile,
            inversion_curve_strength=args.inversion_curve_strength,
            color_unmix_matrix=args.color_unmix_matrix,
            color_unmix_strength=args.color_unmix_strength,
            color_neutral_balance=not args.no_color_neutral_balance,
            inversion_channel_gains=args.inversion_channel_gains,
            inversion_output_percentile=args.inversion_output_percentile,
            stage10_matrix_preset=args.stage10_matrix_preset,
            stage10_empirical_matrix=args.stage10_empirical_matrix,
            stage10_gray_anchor_enabled=not args.disable_stage10_gray_anchor,
            stage10_gray_anchor_percentile=args.stage10_gray_anchor_percentile,
            stage10_gray_anchor_strength=args.stage10_gray_anchor_strength,
            stage10_gray_anchor_eps=args.stage10_gray_anchor_eps,
            stage10_soft_matrix_enabled=not args.disable_stage10_soft_matrix,
            stage10_soft_matrix_strength=args.stage10_soft_matrix_strength,
            stage10_neutral_damp_enabled=not args.disable_stage10_neutral_damp,
            stage10_neutral_damp_strength=args.stage10_neutral_damp_strength,
            stage10_neutral_damp_sigma=args.stage10_neutral_damp_sigma,
            stage10_red_guard_enabled=not args.disable_stage10_red_guard,
            stage10_red_guard_threshold=args.stage10_red_guard_threshold,
            stage10_red_guard_strength=args.stage10_red_guard_strength,
            stage2_gray_norm_enabled=not args.disable_stage2_gray_norm,
            stage2_gray_norm_percentile=args.stage2_gray_norm_percentile,
            stage2_gray_norm_method=args.stage2_gray_norm_method,
            stage2_gray_norm_strength=args.stage2_gray_norm_strength,
            stage2_gray_norm_eps=args.stage2_gray_norm_eps,
            stage2_perceptual_space_enabled=not args.disable_stage2_perceptual_space,
            stage2_lab_input_mode=args.stage2_lab_input_mode,
            stage2_lab_input_percentile=args.stage2_lab_input_percentile,
            stage2_lab_input_gamma=args.stage2_lab_input_gamma,
            stage2_lab_input_eps=args.stage2_lab_input_eps,
            stage2_zoned_tone_enabled=not args.disable_stage2_zoned_tone,
            stage2_shadow_threshold=args.stage2_shadow_threshold,
            stage2_highlight_threshold=args.stage2_highlight_threshold,
            stage2_shadow_gamma=args.stage2_shadow_gamma,
            stage2_mid_sigmoid_k=args.stage2_mid_sigmoid_k,
            stage2_mid_sigmoid_x0=args.stage2_mid_sigmoid_x0,
            stage2_highlight_local_strength=args.stage2_highlight_local_strength,
            stage2_highlight_rolloff_enabled=not args.disable_stage2_highlight_rolloff,
            stage2_highlight_rolloff_alpha=args.stage2_highlight_rolloff_alpha,
            stage2_highlight_rolloff_power=args.stage2_highlight_rolloff_power,
            stage2_preview_enabled=not args.disable_stage2_preview,
            stage2_preview_percentile=args.stage2_preview_percentile,
            stage2_preview_gamma=args.stage2_preview_gamma,
            stage2_preview_eps=args.stage2_preview_eps,
            save_stage2_debug_png=not args.skip_stage2_debug_png,
            stage3_params=stage3_params,
            save_stage3_debug_png=not args.skip_stage3_debug_png,
            stage4_params=stage4_params,
            save_stage4_debug_png=not args.skip_stage4_debug_png,
            save_npy=not args.skip_npy,
            save_linear16=not args.skip_linear16,
        )
        report["frames"].append(frame_result)
        report["warnings"].extend(frame_result.get("warnings", []))

    report_path = output_dir / "physical_correction_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote report: {report_path}")


def _process_frame_with_evaluator(
    frame,
    flat_model,
    evaluator: FlatFieldEvaluatorAgent | None,
    output_dir: Path,
    reference_rois: list[dict[str, Any]],
    initial_strength: float,
    max_iterations: int,
    min_strength: float,
    max_strength: float,
    preview_gamma: float,
    strength_search_enabled: bool,
    save_npy: bool,
    save_linear16: bool,
) -> tuple[dict[str, Any], np.ndarray]:
    """Returns (diagnostics_dict, rgb_linear) to avoid recomputing flat+demosaic downstream."""
    stem = Path(frame.path).stem
    strength = _clamp_strength(initial_strength, min_strength, max_strength)
    if not strength_search_enabled:
        corrected = apply_flat_field(frame, flat_model, strength=strength)
        rgb_linear = demosaic_to_rgb(corrected, frame.cfa_pattern)
        final_diagnostics = diagnose_flat_correction(
            frame,
            corrected,
            flat_model,
            strength=strength,
        )
        _save_flat_corrected_outputs(
            output_dir=output_dir,
            stem=stem,
            corrected=corrected,
            rgb_linear=rgb_linear,
            preview_gamma=preview_gamma,
            diagnostics=final_diagnostics,
            save_npy=save_npy,
            save_linear16=save_linear16,
        )
        final_diagnostics["strength_search_enabled"] = False
        final_diagnostics["selected_iteration"] = None
        final_diagnostics["selected_score"] = None
        final_diagnostics["passed"] = None
        final_diagnostics["attempts"] = []
        print(f"  flat strength={strength:.3f} single-pass")
        return final_diagnostics, rgb_linear

    if evaluator is None:
        raise RuntimeError("Strength search requires an evaluator instance.")

    attempt_dir = output_dir / "attempts" / stem
    attempt_dir.mkdir(parents=True, exist_ok=True)

    attempts: list[dict[str, Any]] = []
    tried_strengths: list[float] = []
    best_attempt: dict[str, Any] | None = None

    for iteration in range(1, max_iterations + 1):
        strength = _avoid_repeated_strength(
            strength=strength,
            tried_strengths=tried_strengths,
            min_strength=min_strength,
            max_strength=max_strength,
        )
        tried_strengths.append(strength)

        corrected = apply_flat_field(frame, flat_model, strength=strength)
        rgb_linear = demosaic_to_rgb(corrected, frame.cfa_pattern)
        attempt_preview = attempt_dir / (
            f"{stem}_iter_{iteration:02d}_strength_{_strength_token(strength)}_preview.png"
        )
        save_preview_png(rgb_linear, attempt_preview, gamma=preview_gamma)

        diagnostics = diagnose_flat_correction(frame, corrected, flat_model, strength=strength)
        evaluation = evaluator.evaluate(
            image_path=attempt_preview,
            rgb_linear=rgb_linear,
            current_strength=strength,
            rois=reference_rois,
        )
        attempt = {
            "iteration": iteration,
            "flat_field_strength": strength,
            "diagnostics": diagnostics,
            "evaluation": evaluation.__dict__,
            "preview_path": str(attempt_preview),
        }
        attempts.append(attempt)

        if best_attempt is None or evaluation.score > best_attempt["evaluation"]["score"]:
            best_attempt = attempt

        print(
            "  iter "
            f"{iteration}: strength={strength:.3f} score={evaluation.score:.3f} "
            f"mode={evaluation.mode} pass={evaluation.passed}"
        )
        if evaluation.passed:
            break

        strength = _choose_next_strength(
            attempts=attempts,
            suggested=evaluation.suggested_flat_strength,
            min_strength=min_strength,
            max_strength=max_strength,
        )

    if best_attempt is None:
        raise RuntimeError(f"No attempts were produced for {frame.path}")

    final_strength = float(best_attempt["flat_field_strength"])
    corrected = apply_flat_field(frame, flat_model, strength=final_strength)
    rgb_linear = demosaic_to_rgb(corrected, frame.cfa_pattern)
    final_diagnostics = diagnose_flat_correction(
        frame,
        corrected,
        flat_model,
        strength=final_strength,
    )

    _save_flat_corrected_outputs(
        output_dir=output_dir,
        stem=stem,
        corrected=corrected,
        rgb_linear=rgb_linear,
        preview_gamma=preview_gamma,
        diagnostics=final_diagnostics,
        save_npy=save_npy,
        save_linear16=save_linear16,
    )
    final_diagnostics["strength_search_enabled"] = True
    final_diagnostics["selected_iteration"] = int(best_attempt["iteration"])
    final_diagnostics["selected_score"] = float(best_attempt["evaluation"]["score"])
    final_diagnostics["passed"] = bool(best_attempt["evaluation"]["passed"])
    final_diagnostics["attempts"] = attempts
    return final_diagnostics, rgb_linear


def _save_flat_corrected_outputs(
    output_dir: Path,
    stem: str,
    corrected: np.ndarray,
    rgb_linear: np.ndarray,
    preview_gamma: float,
    diagnostics: dict[str, Any],
    save_npy: bool,
    save_linear16: bool,
) -> None:
    corrected_npy = output_dir / f"{stem}_flat_corrected_linear.npy"
    preview_png = output_dir / f"{stem}_flat_corrected_preview.png"
    linear16_png = output_dir / f"{stem}_flat_corrected_linear16.png"
    if save_npy:
        np.save(corrected_npy, corrected.astype(np.float32))
    save_preview_png(rgb_linear, preview_png, gamma=preview_gamma)
    if save_linear16:
        save_linear_rgb16_png(rgb_linear, linear16_png)

    diagnostics["output_paths"] = {
        "flat_corrected_preview_png": str(preview_png),
    }
    if save_npy:
        diagnostics["output_paths"]["flat_corrected_linear_npy"] = str(corrected_npy)
    if save_linear16:
        diagnostics["output_paths"]["flat_corrected_linear16_png"] = str(linear16_png)


def _rgb_for_selected_flat_result(frame, flat_model, frame_result: dict[str, Any]) -> np.ndarray:
    strength = float(frame_result["flat_field_strength"])
    corrected = apply_flat_field(frame, flat_model, strength=strength)
    return demosaic_to_rgb(corrected, frame.cfa_pattern)


def _save_negative_stage_outputs(
    frame,
    flat_model,
    frame_result: dict[str, Any],
    base_reference,
    rgb_linear: np.ndarray,
    output_dir: Path,
    preview_gamma: float,
    density_percentile: float,
    inversion_curve_strength: float,
    color_unmix_matrix: list[float] | None,
    color_unmix_strength: float,
    color_neutral_balance: bool,
    inversion_channel_gains: list[float],
    inversion_output_percentile: float,
    stage10_matrix_preset: str,
    stage10_empirical_matrix: list[float] | None,
    stage10_gray_anchor_enabled: bool,
    stage10_gray_anchor_percentile: float,
    stage10_gray_anchor_strength: float,
    stage10_gray_anchor_eps: float,
    stage10_soft_matrix_enabled: bool,
    stage10_soft_matrix_strength: float,
    stage10_neutral_damp_enabled: bool,
    stage10_neutral_damp_strength: float,
    stage10_neutral_damp_sigma: float,
    stage10_red_guard_enabled: bool,
    stage10_red_guard_threshold: float,
    stage10_red_guard_strength: float,
    stage2_gray_norm_enabled: bool,
    stage2_gray_norm_percentile: float,
    stage2_gray_norm_method: str,
    stage2_gray_norm_strength: float,
    stage2_gray_norm_eps: float,
    stage2_perceptual_space_enabled: bool,
    stage2_lab_input_mode: str,
    stage2_lab_input_percentile: float,
    stage2_lab_input_gamma: float,
    stage2_lab_input_eps: float,
    stage2_zoned_tone_enabled: bool,
    stage2_shadow_threshold: float,
    stage2_highlight_threshold: float,
    stage2_shadow_gamma: float,
    stage2_mid_sigmoid_k: float,
    stage2_mid_sigmoid_x0: float,
    stage2_highlight_local_strength: float,
    stage2_highlight_rolloff_enabled: bool,
    stage2_highlight_rolloff_alpha: float,
    stage2_highlight_rolloff_power: float,
    stage2_preview_enabled: bool,
    stage2_preview_percentile: float,
    stage2_preview_gamma: float,
    stage2_preview_eps: float,
    save_stage2_debug_png: bool,
    stage3_params: dict[str, Any],
    save_stage3_debug_png: bool,
    stage4_params: dict[str, Any],
    save_stage4_debug_png: bool,
    save_npy: bool,
    save_linear16: bool,
) -> None:
    """Save [5]-[10], Stage 2, Stage 3, and Stage 4 images for visual inspection."""

    stem = Path(frame.path).stem
    # rgb_linear is passed in — already computed by _process_frame_with_evaluator.
    negative = process_negative_stages(
        rgb_linear,
        base_reference,
        density_percentile=density_percentile,
        curve_strength=inversion_curve_strength,
        color_unmix_matrix=color_unmix_matrix,
        color_unmix_strength=color_unmix_strength,
        neutral_balance=color_neutral_balance,
        inversion_channel_gains=inversion_channel_gains,
        inversion_output_percentile=inversion_output_percentile,
    )
    stage10_matrix = (
        stage10_empirical_matrix
        if stage10_empirical_matrix is not None
        else STAGE10_MATRIX_PRESETS[stage10_matrix_preset]
    )
    stage10 = apply_stage10_soft_reference_mapping(
        negative.inverted_rgb,
        empirical_matrix=stage10_matrix,
        gray_anchor_enabled=stage10_gray_anchor_enabled,
        gray_anchor_percentile=stage10_gray_anchor_percentile,
        gray_anchor_strength=stage10_gray_anchor_strength,
        gray_anchor_eps=stage10_gray_anchor_eps,
        soft_matrix_enabled=stage10_soft_matrix_enabled,
        soft_matrix_strength=stage10_soft_matrix_strength,
        neutral_damp_enabled=stage10_neutral_damp_enabled,
        neutral_damp_strength=stage10_neutral_damp_strength,
        neutral_damp_sigma=stage10_neutral_damp_sigma,
        red_guard_enabled=stage10_red_guard_enabled,
        red_guard_threshold=stage10_red_guard_threshold,
        red_guard_strength=stage10_red_guard_strength,
    )
    stage2 = apply_stage2_perceptual_tone_base(
        stage10.mapped_rgb,
        gray_norm_enabled=stage2_gray_norm_enabled,
        gray_norm_percentile=stage2_gray_norm_percentile,
        gray_norm_method=stage2_gray_norm_method,
        gray_norm_strength=stage2_gray_norm_strength,
        gray_norm_eps=stage2_gray_norm_eps,
        perceptual_space_enabled=stage2_perceptual_space_enabled,
        lab_input_mode=stage2_lab_input_mode,
        lab_input_percentile=stage2_lab_input_percentile,
        lab_input_gamma=stage2_lab_input_gamma,
        lab_input_eps=stage2_lab_input_eps,
        zoned_tone_enabled=stage2_zoned_tone_enabled,
        shadow_threshold=stage2_shadow_threshold,
        highlight_threshold=stage2_highlight_threshold,
        shadow_gamma=stage2_shadow_gamma,
        mid_sigmoid_k=stage2_mid_sigmoid_k,
        mid_sigmoid_x0=stage2_mid_sigmoid_x0,
        highlight_local_strength=stage2_highlight_local_strength,
        highlight_rolloff_enabled=stage2_highlight_rolloff_enabled,
        highlight_rolloff_alpha=stage2_highlight_rolloff_alpha,
        highlight_rolloff_power=stage2_highlight_rolloff_power,
        preview_enabled=stage2_preview_enabled,
        preview_percentile=stage2_preview_percentile,
        preview_gamma=stage2_preview_gamma,
        preview_eps=stage2_preview_eps,
    )
    stage3 = apply_stage3_pseudo_lut_color_refinement(
        stage2.stage2_linear_output,
        include_debug=save_stage3_debug_png,
        **stage3_params,
    )
    stage4 = apply_stage4_final_finish_and_export(
        stage3.stage3_linear_output,
        include_debug=save_stage4_debug_png,
        **stage4_params,
    )

    paths = {
        "05_demosaic_rgb_preview": output_dir / f"{stem}_05_demosaic_rgb.png",
        "05_demosaic_rgb_linear16": output_dir / f"{stem}_05_demosaic_rgb_linear16.png",
        "05_demosaic_rgb_npy": output_dir / f"{stem}_05_demosaic_rgb.npy",
        "06_base_corrected_preview": output_dir / f"{stem}_06_base_corrected.png",
        "06_base_corrected_npy": output_dir / f"{stem}_06_base_corrected.npy",
        "07_density_preview": output_dir / f"{stem}_07_density.png",
        "07_density_npy": output_dir / f"{stem}_07_density.npy",
        "08_color_unmixed_density_preview": output_dir / f"{stem}_08_color_unmixed_density.png",
        "08_color_unmixed_density_npy": output_dir / f"{stem}_08_color_unmixed_density.npy",
        "09_inverted_preview": output_dir / f"{stem}_09_inverted_positive.png",
        "09_inverted_linear16": output_dir / f"{stem}_09_inverted_positive_linear16.png",
        "09_inverted_npy": output_dir / f"{stem}_09_inverted_positive.npy",
        "10_stage10_soft_reference_mapping_preview": output_dir / f"{stem}_10_stage10_soft_reference_mapping.png",
        "10_stage10_soft_reference_mapping_linear16": output_dir / f"{stem}_10_stage10_soft_reference_mapping_linear16.png",
        "10_stage10_soft_reference_mapping_npy": output_dir / f"{stem}_10_stage10_soft_reference_mapping.npy",
        "stage2_perceptual_tone_base_preview": output_dir / f"{stem}_stage2_perceptual_tone_base.png",
        "stage2_perceptual_tone_base_linear16": output_dir / f"{stem}_stage2_perceptual_tone_base_linear16.png",
        "stage2_perceptual_tone_base_npy": output_dir / f"{stem}_stage2_perceptual_tone_base.npy",
        "stage2_L_before_preview": output_dir / f"{stem}_stage2_L_before.png",
        "stage2_L_after_zoned_preview": output_dir / f"{stem}_stage2_L_after_zoned.png",
        "stage2_L_after_rolloff_preview": output_dir / f"{stem}_stage2_L_after_rolloff.png",
        "stage3_pseudo_lut_color_refinement_preview": output_dir / f"{stem}_stage3_pseudo_lut_color_refinement.png",
        "stage3_pseudo_lut_color_refinement_linear16": output_dir / f"{stem}_stage3_pseudo_lut_color_refinement_linear16.png",
        "stage3_pseudo_lut_color_refinement_npy": output_dir / f"{stem}_stage3_pseudo_lut_color_refinement.npy",
        "stage3_sat_scale_preview": output_dir / f"{stem}_stage3_sat_scale.png",
        "stage3_hue_map_preview": output_dir / f"{stem}_stage3_hue_map.png",
        "stage3_red_mask_preview": output_dir / f"{stem}_stage3_red_mask.png",
        "stage3_blue_mask_preview": output_dir / f"{stem}_stage3_blue_mask.png",
        "stage3_green_mask_preview": output_dir / f"{stem}_stage3_green_mask.png",
        "stage3_C_before_preview": output_dir / f"{stem}_stage3_C_before.png",
        "stage3_C_after_preview": output_dir / f"{stem}_stage3_C_after.png",
        "stage3_neutral_mask_preview": output_dir / f"{stem}_stage3_neutral_mask.png",
        "stage4_final_preview": output_dir / f"{stem}_stage4_final_finish.png",
        "stage4_final_master_linear16": output_dir / f"{stem}_stage4_final_master_linear16.png",
        "stage4_final_master_npy": output_dir / f"{stem}_stage4_final_master.npy",
        "stage4_display_base_preview": output_dir / f"{stem}_stage4_display_base.png",
        "stage4_after_trim_preview": output_dir / f"{stem}_stage4_after_trim.png",
        "stage4_after_local_refinement_preview": output_dir / f"{stem}_stage4_after_local_refinement.png",
        "stage4_after_film_finish_preview": output_dir / f"{stem}_stage4_after_film_finish.png",
    }

    save_preview_png(rgb_linear, paths["05_demosaic_rgb_preview"], gamma=preview_gamma)
    if save_linear16:
        save_linear_rgb16_png(rgb_linear, paths["05_demosaic_rgb_linear16"])
    if save_npy:
        np.save(paths["05_demosaic_rgb_npy"], rgb_linear.astype(np.float32))

    save_preview_png(
        negative.base_corrected_rgb,
        paths["06_base_corrected_preview"],
        gamma=preview_gamma,
    )
    if save_npy:
        np.save(paths["06_base_corrected_npy"], negative.base_corrected_rgb.astype(np.float32))

    save_preview_png(
        negative.density_norm_rgb,
        paths["07_density_preview"],
        gamma=1.0,
        low_percentile=0.0,
        high_percentile=100.0,
    )
    if save_npy:
        np.save(paths["07_density_npy"], negative.density_rgb.astype(np.float32))

    save_preview_png(
        negative.density_unmixed_norm_rgb,
        paths["08_color_unmixed_density_preview"],
        gamma=1.0,
        low_percentile=0.0,
        high_percentile=100.0,
    )
    if save_npy:
        np.save(paths["08_color_unmixed_density_npy"], negative.density_unmixed_rgb.astype(np.float32))

    save_preview_png(
        negative.inverted_rgb,
        paths["09_inverted_preview"],
        gamma=preview_gamma,
        low_percentile=0.0,
        high_percentile=100.0,
    )
    if save_linear16:
        save_linear_rgb16_png(negative.inverted_rgb, paths["09_inverted_linear16"])
    if save_npy:
        np.save(paths["09_inverted_npy"], negative.inverted_rgb.astype(np.float32))

    save_preview_png(
        stage10.mapped_rgb,
        paths["10_stage10_soft_reference_mapping_preview"],
        gamma=preview_gamma,
        low_percentile=0.0,
        high_percentile=100.0,
    )
    if save_linear16:
        save_linear_rgb16_png(stage10.mapped_rgb, paths["10_stage10_soft_reference_mapping_linear16"])
    if save_npy:
        np.save(paths["10_stage10_soft_reference_mapping_npy"], stage10.mapped_rgb.astype(np.float32))

    save_display_rgb_png(stage2.stage2_preview_output, paths["stage2_perceptual_tone_base_preview"])
    if save_linear16:
        save_linear_rgb16_png(stage2.stage2_linear_output, paths["stage2_perceptual_tone_base_linear16"])
    if save_npy:
        np.save(paths["stage2_perceptual_tone_base_npy"], stage2.stage2_linear_output.astype(np.float32))
    if save_stage2_debug_png:
        _save_luma_debug_preview(stage2.debug.get("L_before"), paths["stage2_L_before_preview"])
        _save_luma_debug_preview(stage2.debug.get("L_after_zoned"), paths["stage2_L_after_zoned_preview"])
        _save_luma_debug_preview(stage2.debug.get("L_after_rolloff"), paths["stage2_L_after_rolloff_preview"])

    save_display_rgb_png(stage3.stage3_preview_output, paths["stage3_pseudo_lut_color_refinement_preview"])
    if save_linear16:
        save_linear_rgb16_png(stage3.stage3_linear_output, paths["stage3_pseudo_lut_color_refinement_linear16"])
    if save_npy:
        np.save(paths["stage3_pseudo_lut_color_refinement_npy"], stage3.stage3_linear_output.astype(np.float32))
    if save_stage3_debug_png:
        _save_scalar_debug_preview(stage3.debug.get("sat_scale"), paths["stage3_sat_scale_preview"])
        _save_hue_debug_preview(stage3.debug.get("hue_map"), paths["stage3_hue_map_preview"])
        _save_mask_debug_preview(stage3.debug.get("red_mask"), paths["stage3_red_mask_preview"])
        _save_mask_debug_preview(stage3.debug.get("blue_mask"), paths["stage3_blue_mask_preview"])
        _save_mask_debug_preview(stage3.debug.get("green_mask"), paths["stage3_green_mask_preview"])
        _save_scalar_debug_preview(stage3.debug.get("C_before"), paths["stage3_C_before_preview"])
        _save_scalar_debug_preview(stage3.debug.get("C_after"), paths["stage3_C_after_preview"])
        _save_mask_debug_preview(stage3.debug.get("neutral_mask"), paths["stage3_neutral_mask_preview"])

    save_display_rgb_png(stage4.final_preview_output, paths["stage4_final_preview"])
    if save_linear16:
        save_linear_rgb16_png(stage4.final_master_output, paths["stage4_final_master_linear16"])
    if save_npy:
        np.save(paths["stage4_final_master_npy"], stage4.final_master_output.astype(np.float32))
    if save_stage4_debug_png and stage4.debug:
        _save_display_debug_preview(stage4.debug.get("img_display_base"), paths["stage4_display_base_preview"])
        _save_display_debug_preview(stage4.debug.get("img_after_trim"), paths["stage4_after_trim_preview"])
        _save_display_debug_preview(stage4.debug.get("img_after_local_refinement"), paths["stage4_after_local_refinement_preview"])
        _save_display_debug_preview(stage4.debug.get("img_after_film_finish"), paths["stage4_after_film_finish_preview"])

    frame_result["negative_stage_diagnostics"] = negative.diagnostics
    frame_result["stage10_soft_reference_mapping_diagnostics"] = stage10.diagnostics
    frame_result["stage2_perceptual_tone_base_diagnostics"] = stage2.diagnostics
    frame_result["stage3_pseudo_lut_color_refinement_diagnostics"] = stage3.diagnostics
    frame_result["stage4_final_finish_and_export_diagnostics"] = stage4.diagnostics
    stage2_debug_keys = set()
    if save_stage2_debug_png:
        if stage2.debug.get("L_before") is not None:
            stage2_debug_keys.add("stage2_L_before_preview")
        if stage2.debug.get("L_after_zoned") is not None:
            stage2_debug_keys.add("stage2_L_after_zoned_preview")
        if stage2.debug.get("L_after_rolloff") is not None:
            stage2_debug_keys.add("stage2_L_after_rolloff_preview")
    stage3_debug_keys = set()
    if save_stage3_debug_png and stage3.debug:
        for key in (
            "stage3_sat_scale_preview",
            "stage3_hue_map_preview",
            "stage3_red_mask_preview",
            "stage3_blue_mask_preview",
            "stage3_green_mask_preview",
            "stage3_C_before_preview",
            "stage3_C_after_preview",
            "stage3_neutral_mask_preview",
        ):
            stage3_debug_keys.add(key)
    stage4_debug_keys = set()
    if save_stage4_debug_png and stage4.debug:
        for key in (
            "stage4_display_base_preview",
            "stage4_after_trim_preview",
            "stage4_after_local_refinement_preview",
            "stage4_after_film_finish_preview",
        ):
            stage4_debug_keys.add(key)
    saved_paths = {
        k: str(v)
        for k, v in paths.items()
        if (save_npy or not k.endswith("_npy"))
        and (save_linear16 or not k.endswith("_linear16"))
        and (not k.startswith("stage2_L_") or k in stage2_debug_keys)
        and (not _is_stage3_debug_path_key(k) or k in stage3_debug_keys)
        and (not _is_stage4_debug_path_key(k) or k in stage4_debug_keys)
    }
    frame_result.setdefault("output_paths", {}).update(saved_paths)


def _save_luma_debug_preview(luma: np.ndarray | None, path: Path) -> None:
    if luma is None:
        return
    luma_rgb = np.repeat(np.asarray(luma, dtype=np.float32)[..., np.newaxis], 3, axis=2)
    save_preview_png(
        luma_rgb,
        path,
        gamma=1.0,
        low_percentile=0.0,
        high_percentile=100.0,
    )


def _save_scalar_debug_preview(values: np.ndarray | None, path: Path) -> None:
    if values is None:
        return
    arr = np.asarray(values, dtype=np.float32)
    rgb = np.repeat(arr[..., np.newaxis], 3, axis=2)
    save_preview_png(rgb, path, gamma=1.0, low_percentile=0.5, high_percentile=99.5)


def _save_hue_debug_preview(hue_map: np.ndarray | None, path: Path) -> None:
    if hue_map is None:
        return
    hue = np.asarray(hue_map, dtype=np.float32)
    display = np.clip((hue + np.pi) / (2.0 * np.pi), 0.0, 1.0)
    rgb = np.repeat(display[..., np.newaxis], 3, axis=2)
    save_display_rgb_png(rgb, path)


def _save_mask_debug_preview(mask: np.ndarray | None, path: Path) -> None:
    if mask is None:
        return
    arr = np.asarray(mask, dtype=np.float32)
    rgb = np.repeat(arr[..., np.newaxis], 3, axis=2)
    save_display_rgb_png(rgb, path)


def _is_stage3_debug_path_key(key: str) -> bool:
    return key.startswith("stage3_") and key not in {
        "stage3_pseudo_lut_color_refinement_preview",
        "stage3_pseudo_lut_color_refinement_linear16",
        "stage3_pseudo_lut_color_refinement_npy",
    }


def _is_stage4_debug_path_key(key: str) -> bool:
    return key in {
        "stage4_display_base_preview",
        "stage4_after_trim_preview",
        "stage4_after_local_refinement_preview",
        "stage4_after_film_finish_preview",
    }


def _save_display_debug_preview(img: np.ndarray | None, path: Path) -> None:
    if img is None:
        return
    save_display_rgb_png(np.asarray(img, dtype=np.float32), path)


def _run_stage23_contract_verification() -> dict[str, Any]:
    """Synthetic smoke checks for Stage 2/3 Lab path contracts.

    This validates the current OpenCV Lab path's single encode/decode contract.
    It is not a golden-image regression proof against the previous explicit
    RGB<->Lab implementation.

    Checks:
    1) Neutral inputs remain near neutral in Lab and after round-trip.
    2) Round-trip in encoded Lab path is numerically stable.
    3) Stage previews remain separate from linear outputs.
    """

    gamma = 2.2
    eps = 1e-6
    percentile = 99.5

    neutral_lin = np.linspace(0.02, 0.95, 64, dtype=np.float32).reshape(8, 8, 1)
    neutral_lin = np.repeat(neutral_lin, 3, axis=2)

    y = np.linspace(0.0, 1.0, 32, dtype=np.float32)
    x = np.linspace(0.0, 1.0, 32, dtype=np.float32)
    x_grid, y_grid = np.meshgrid(x, y)
    hdr_linear = np.stack(
        [
            0.05 + 1.60 * x_grid,
            0.03 + 1.20 * y_grid,
            0.02 + 1.40 * (0.65 * x_grid + 0.35 * y_grid),
        ],
        axis=2,
    ).astype(np.float32)

    # Stage 2 conversion contract: _prepare_lab_input(srgb_encoded) returns
    # encoded RGB that goes directly into RGB2Lab; Lab2RGB returns encoded RGB;
    # decode to linear exactly once.
    s2_prepared, s2_prepare_diag = stage2_perceptual_tone._prepare_lab_input(
        neutral_lin,
        mode="srgb_encoded",
        percentile=percentile,
        gamma=gamma,
        eps=eps,
    )
    s2_lab = stage2_perceptual_tone._rgb_to_lab_for_mode(
        s2_prepared,
        mode="srgb_encoded",
        gamma=gamma,
    )
    s2_encoded_rt = stage2_perceptual_tone._lab_to_rgb_for_mode(
        s2_lab,
        mode="srgb_encoded",
        gamma=gamma,
    )
    s2_linear_rt = np.power(np.clip(s2_encoded_rt, 0.0, 1.0), gamma).astype(np.float32)
    s2_expected_linear = np.power(np.clip(s2_prepared, 0.0, 1.0), gamma).astype(np.float32)
    s2_expected_from_original = np.clip(
        neutral_lin / (float(s2_prepare_diag["scale"]) + eps),
        0.0,
        1.0,
    ).astype(np.float32)

    s2_ab_abs_max = float(np.max(np.abs(s2_lab[..., 1:3])))
    s2_roundtrip_max_err = float(np.max(np.abs(s2_linear_rt - s2_expected_linear)))
    s2_roundtrip_mean_err = float(np.mean(np.abs(s2_linear_rt - s2_expected_linear)))
    s2_original_linear_max_err = float(np.max(np.abs(s2_linear_rt - s2_expected_from_original)))
    s2_original_linear_mean_err = float(np.mean(np.abs(s2_linear_rt - s2_expected_from_original)))

    # Stage 3 conversion contract mirrors Stage 2 but always uses encoded Lab input.
    s3_prepared, s3_prepare_diag = stage3_color_refinement._prepare_lab_input(
        neutral_lin,
        percentile=percentile,
        gamma=gamma,
        eps=eps,
    )
    s3_lab = stage3_color_refinement._srgb_like_to_lab(s3_prepared, gamma=gamma)
    s3_encoded_rt = stage3_color_refinement._lab_to_srgb_like(s3_lab, gamma=gamma)
    s3_linear_rt = np.power(np.clip(s3_encoded_rt, 0.0, 1.0), gamma).astype(np.float32)
    s3_expected_linear = np.power(np.clip(s3_prepared, 0.0, 1.0), gamma).astype(np.float32)
    s3_expected_from_original = np.clip(
        neutral_lin / (float(s3_prepare_diag["scale"]) + eps),
        0.0,
        1.0,
    ).astype(np.float32)

    s3_ab_abs_max = float(np.max(np.abs(s3_lab[..., 1:3])))
    s3_roundtrip_max_err = float(np.max(np.abs(s3_linear_rt - s3_expected_linear)))
    s3_roundtrip_mean_err = float(np.mean(np.abs(s3_linear_rt - s3_expected_linear)))
    s3_original_linear_max_err = float(np.max(np.abs(s3_linear_rt - s3_expected_from_original)))
    s3_original_linear_mean_err = float(np.mean(np.abs(s3_linear_rt - s3_expected_from_original)))

    # Full Stage 2/3 run with edits disabled validates preview-vs-linear separation.
    s2_result = apply_stage2_perceptual_tone_base(
        hdr_linear,
        gray_norm_enabled=False,
        perceptual_space_enabled=True,
        lab_input_mode="srgb_encoded",
        lab_input_percentile=percentile,
        lab_input_gamma=gamma,
        lab_input_eps=eps,
        zoned_tone_enabled=False,
        highlight_rolloff_enabled=False,
        preview_enabled=True,
        preview_percentile=99.5,
        preview_gamma=2.2,
        preview_eps=1e-6,
    )
    s3_result = apply_stage3_pseudo_lut_color_refinement(
        s2_result.stage2_linear_output,
        include_debug=False,
        lab_input_percentile=percentile,
        lab_input_gamma=gamma,
        lab_input_eps=eps,
        luma_chroma_enabled=False,
        hue_adjust_enabled=False,
        neutral_protect_enabled=False,
        preview_enabled=True,
        preview_percentile=99.5,
        preview_gamma=2.2,
        preview_eps=1e-6,
    )

    s2_preview_sep = float(
        np.mean(
            np.abs(
                s2_result.stage2_preview_output
                - np.clip(s2_result.stage2_linear_output, 0.0, 1.0)
            )
        )
    )
    s3_preview_sep = float(
        np.mean(
            np.abs(
                s3_result.stage3_preview_output
                - np.clip(s3_result.stage3_linear_output, 0.0, 1.0)
            )
        )
    )

    checks = {
        "stage2_neutral_lab": {
            "passed": s2_ab_abs_max <= 1.5,
            "metric": s2_ab_abs_max,
            "threshold": 1.5,
            "description": "Neutral gray stays near Lab neutral for Stage 2 path.",
        },
        "stage2_roundtrip_stability": {
            "passed": s2_roundtrip_max_err <= 5e-3,
            "max_abs_error": s2_roundtrip_max_err,
            "mean_abs_error": s2_roundtrip_mean_err,
            "threshold": 5e-3,
            "description": "Stage 2 encoded Lab round-trip remains stable.",
        },
        "stage2_original_linear_contract": {
            "passed": s2_original_linear_max_err <= 5e-3,
            "max_abs_error": s2_original_linear_max_err,
            "mean_abs_error": s2_original_linear_mean_err,
            "threshold": 5e-3,
            "description": (
                "Stage 2 round-trip returns to the normalized original linear input "
                "after exactly one decode."
            ),
        },
        "stage3_neutral_lab": {
            "passed": s3_ab_abs_max <= 1.5,
            "metric": s3_ab_abs_max,
            "threshold": 1.5,
            "description": "Neutral gray stays near Lab neutral for Stage 3 path.",
        },
        "stage3_roundtrip_stability": {
            "passed": s3_roundtrip_max_err <= 5e-3,
            "max_abs_error": s3_roundtrip_max_err,
            "mean_abs_error": s3_roundtrip_mean_err,
            "threshold": 5e-3,
            "description": "Stage 3 encoded Lab round-trip remains stable.",
        },
        "stage3_original_linear_contract": {
            "passed": s3_original_linear_max_err <= 5e-3,
            "max_abs_error": s3_original_linear_max_err,
            "mean_abs_error": s3_original_linear_mean_err,
            "threshold": 5e-3,
            "description": (
                "Stage 3 round-trip returns to the normalized original linear input "
                "after exactly one decode."
            ),
        },
        "preview_is_separate_from_linear": {
            "passed": (s2_preview_sep > 1e-4) and (s3_preview_sep > 1e-4),
            "stage2_mean_abs_diff": s2_preview_sep,
            "stage3_mean_abs_diff": s3_preview_sep,
            "threshold": 1e-4,
            "description": "Stage 2/3 preview buffers are not the linear pipeline outputs.",
        },
    }
    all_passed = all(bool(item["passed"]) for item in checks.values())
    return {
        "stage": "stage2_stage3_contract_smoke_test",
        "all_passed": all_passed,
        "checks": checks,
        "limitations": (
            "This validates the current encoded OpenCV Lab contract only. "
            "It does not prove golden-image equivalence to the previous explicit "
            "RGB<->Lab implementation."
        ),
        "contract": {
            "stage2_prepare": "srgb_encoded returns encoded RGB for Lab conversion",
            "stage2_lab2rgb": "srgb_encoded output treated as encoded RGB",
            "stage2_decode": "exactly one encoded->linear decode after Lab2RGB",
            "stage3_prepare": "returns encoded RGB for Lab conversion",
            "stage3_lab2rgb": "output treated as encoded RGB",
            "stage3_decode": "exactly one encoded->linear decode after Lab2RGB",
        },
    }


def _resolve_backlight_path(input_dir: Path, requested: Path | None) -> Path:
    """Resolve the backlight/flat-field RAW file without baking in one filename."""

    if requested is not None:
        path = requested.resolve()
        if not path.exists():
            raise FileNotFoundError(f"Backlight frame does not exist: {path}")
        return path

    candidates = [
        path
        for path in input_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in RAW_EXTENSIONS
        and _looks_like_backlight_frame(path)
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No backlight/flat-field RAW found in {input_dir}. "
            "Pass one explicitly with --backlight-frame."
        )

    candidates.sort(key=_backlight_candidate_sort_key)
    return candidates[0].resolve()


def _resolve_base_frame_path(input_dir: Path, requested: Path | None) -> Path:
    if requested is not None:
        path = requested.resolve()
        if not path.exists():
            raise FileNotFoundError(f"Base frame does not exist: {path}")
        return path
    path = input_dir / "base_frame.dng"
    if not path.exists():
        raise FileNotFoundError(
            f"No base frame found at {path}. Pass one explicitly with --base-frame."
        )
    return path.resolve()


def _looks_like_backlight_frame(path: Path) -> bool:
    stem = path.stem.lower()
    return stem in BACKLIGHT_EXACT_STEMS or any(hint in stem for hint in BACKLIGHT_NAME_HINTS)


def _backlight_candidate_sort_key(path: Path) -> tuple[int, float, str]:
    stem = path.stem.lower()
    exact_rank = 0 if stem in BACKLIGHT_EXACT_STEMS else 1
    # If multiple future backlight captures are present, prefer the newest one.
    return (exact_rank, -path.stat().st_mtime, path.name.lower())


def _clamp_strength(value: float, min_strength: float, max_strength: float) -> float:
    return float(max(min_strength, min(max_strength, value)))


def _avoid_repeated_strength(
    strength: float,
    tried_strengths: list[float],
    min_strength: float,
    max_strength: float,
) -> float:
    rounded = {round(v, 4) for v in tried_strengths}
    if round(strength, 4) not in rounded:
        return strength

    candidates = []
    for step in (0.05, 0.10, 0.15, 0.20, 0.30):
        candidates.append(strength + step)
        candidates.append(strength - step)
    for candidate in candidates:
        candidate = _clamp_strength(candidate, min_strength, max_strength)
        if round(candidate, 4) not in rounded:
            return candidate
    return strength


def _choose_next_strength(
    attempts: list[dict[str, Any]],
    suggested: float,
    min_strength: float,
    max_strength: float,
) -> float:
    tried = [float(a["flat_field_strength"]) for a in attempts]
    rounded = {round(v, 4) for v in tried}
    suggested = _clamp_strength(suggested, min_strength, max_strength)

    if len(attempts) >= 2:
        previous = attempts[-2]
        current = attempts[-1]
        previous_score = float(previous["evaluation"]["score"])
        current_score = float(current["evaluation"]["score"])
        if current_score < previous_score - 0.015:
            previous_strength = float(previous["flat_field_strength"])
            current_strength = float(current["flat_field_strength"])
            direction = 1.0 if current_strength > previous_strength else -1.0
            best = max(attempts, key=lambda a: float(a["evaluation"]["score"]))
            best_strength = float(best["flat_field_strength"])
            candidate = _clamp_strength(best_strength - direction * 0.10, min_strength, max_strength)
            if round(candidate, 4) not in rounded:
                return candidate

    if round(suggested, 4) not in rounded:
        return suggested

    best = max(attempts, key=lambda a: float(a["evaluation"]["score"]))
    best_strength = float(best["flat_field_strength"])
    for step in (0.05, -0.05, 0.10, -0.10, 0.20, -0.20, 0.30, -0.30):
        candidate = _clamp_strength(best_strength + step, min_strength, max_strength)
        if round(candidate, 4) not in rounded:
            return candidate
    return suggested


def _strength_token(strength: float) -> str:
    return f"{strength:.3f}".replace(".", "p")


def _resolve_frame_paths(
    input_dir: Path,
    requested: list[Path] | None,
    base_frame_path: Path,
) -> list[Path]:
    if requested:
        return [
            path.resolve()
            for path in requested
            if path.resolve() != base_frame_path.resolve()
        ]

    paths = [path.resolve() for path in sorted(input_dir.glob("capture_*.dng"))]
    if not paths:
        raise FileNotFoundError(f"No default DNG frames found in {input_dir}")
    return paths


if __name__ == "__main__":
    main()
