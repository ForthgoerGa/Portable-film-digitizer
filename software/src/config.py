"""
Configuration constants for the film digitizer scanner.

This module centralizes all hardware and scanning parameters.
"""

from pathlib import Path

# GPIO pin assignments
STEP_X = 21
STEP_Y = 20
DIR_X = 26
DIR_Y = 19

# Motor configuration
RPM = 200  # realistic speed
SPR = 200 * 16  # steps per revolution (microstepping)

# Timing configuration
STEP_DELAY = 0.00001  # ~2 kHz stepping (stable in Python)

# Ramp profile for smooth acceleration/deceleration
RAMP_START = 0.002
RAMP_FACTOR = 0.90

# Standard full-size scan parameters, calibrated from manual checkpoints.
# Manual max travel:
#   X: 130000 steps
#   Y: 120000 steps
# Approximate single-frame visual crop/FOV:
#   X: 60000 motor steps ~= 1.85 cm ~= 4056 px
#   Y: 45000 motor steps ~= 1.55 cm ~= 3040 px
SCAN_MAX_X_POSITION = 130000
SCAN_MAX_Y_POSITION = 120000
CAPTURE_FOV_X_STEPS = 60000
CAPTURE_FOV_Y_STEPS = 45000

# 4x4 is the smallest grid that covers the measured travel in both axes
# while preserving overlap between adjacent camera frames.
X_SEGMENTS = 4
Y_SEGMENTS = 4
X_STEPS_PER_SEG = 43333
Y_STEPS_PER_SEG = 38000

# Scanner integration calibration. Conservative defaults preserve current
# behavior: tiles are stitched edge-to-edge unless these values are calibrated.
STITCH_OVERLAP_X_PX = 0
STITCH_OVERLAP_Y_PX = 0

# Optional explicit pixel stride overrides. These are the physical model's
# initial stitch estimates; phase-correlation refinement below handles small
# mechanical drift between neighboring frames.
STITCH_TILE_STRIDE_X_PX = 2800
STITCH_TILE_STRIDE_Y_PX = 2568

# Preview-based local refinement around the physical model. RAW placement still
# uses integer/even-pixel offsets to preserve the 2x2 Bayer phase.
STITCH_REFINE_DOWNSAMPLE = 6
STITCH_REFINE_MAX_SHIFT_PX = 360
STITCH_REFINE_MAX_CORRECTION_PX = 520
STITCH_REFINE_MIN_OVERLAP_PX = 240
STITCH_REFINE_MIN_SNR = 1.8

# Optional full vector placement from manual alignment. Keep disabled until
# it is calibrated against the active motor scan step sizes.
STITCH_STEP_X_DX_PX = None
STITCH_STEP_X_DY_PX = None
STITCH_STEP_Y_DX_PX = None
STITCH_STEP_Y_DY_PX = None

# NEMA 17 1.8deg motors provide 200 full steps per revolution. Pixel overlap
# also depends on microstepping, transmission pitch, optical magnification, and
# sensor pixel scale, so these placeholders must be calibrated on the scanner.
MOTOR_FULL_STEPS_PER_REV = 200
MOTOR_MICROSTEPS_PER_FULL_STEP = 16
TRANSMISSION_PITCH_MM_PER_REV = None
OPTICAL_MAGNIFICATION = None
SENSOR_PIXEL_SIZE_UM = None

# Camera capture parameters. Picamera2/libcamera exposes ISO behavior through
# exposure time and analogue gain; keep these fixed for post-processing.
RAW_CAPTURE_ENABLED = True
CAPTURE_EXTENSION = ".dng"
PREVIEW_EXTENSION = ".jpg"
CAPTURE_EXPOSURE_US = 50000
CAPTURE_ANALOGUE_GAIN = 1.0
CAPTURE_COLOUR_GAINS = (1.0, 1.0)
CAPTURE_AWB_ENABLE = False
CAPTURE_DENOISE_MODE = "Off"

# Integration outputs produced after a segmented scan.
STITCHED_RAW_FILENAME = "stitched_raw.dng"
STITCHED_PREVIEW_FILENAME = "stitched_preview.jpg"
CAPTURE_MANIFEST_FILENAME = "capture_manifest.json"

# Capture output directory
CAPTURES_DIR = Path(__file__).parent / "captures"
