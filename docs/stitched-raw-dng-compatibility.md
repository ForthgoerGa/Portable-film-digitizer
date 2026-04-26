# Stitched RAW DNG Compatibility Notes

Date: 2026-04-25

## Context

The negative post-processing pipeline was originally validated on single Pi
camera DNG tiles from `Post_Processing_Negative/rpi_captures_white/`. The full
scanner now produces a synthetic stitched DNG on the Pi and sends it to the PC
as the canonical scan artifact.

This note records the compatibility issues found while comparing those two DNG
sources. The goal is to keep the PC processing contract stable while making the
Pi stitched DNG semantically match a real RAW input closely enough for flat
field correction and negative inversion.

## Compared Inputs

Single tile test DNG:

- Example: `Post_Processing_Negative/rpi_captures_white/capture_20260403_131943_968456.dng`
- Size: `4056 x 3040`
- CFA/raw pattern: same Bayer pattern reported by `rawpy`
- Black level: `256`
- White level: `4095`
- Zero-valued pixel fraction: approximately `0`
- Saturated-at-white fraction: approximately `0`
- Camera metadata includes non-trivial camera white balance values.

Current stitched scan DNG:

- Example: `software/web/jobs/f0b7c5e9da6c/stitched_raw.dng`
- Size: `13496 x 11784`
- CFA/raw pattern: readable by `rawpy`, same Bayer pattern family as tiles
- Black level: `256`
- White level: `4095`
- Zero-valued pixel fraction: approximately `26.7%`
- Saturated-at-white fraction: approximately `4.1%`
- Written by the Pi integrator as a synthetic 16-bit DNG.
- Camera white balance metadata is effectively neutral/defaulted.

Current stitched backlight DNG:

- Example: `software/web/calibration/backlight/backlight_frame.dng`
- Size matches the stitched scan.
- Zero-valued pixel fraction: approximately `26.7%`
- Saturated-at-white fraction: approximately `12.2%`

## Main Compatibility Problem

The stitched DNG is readable as RAW, but it is not equivalent to a real camera
DNG. The Pi integrator currently builds a larger stitched Bayer canvas and
leaves uncovered areas as numeric zero. Because the DNG declares a black level
of `256`, those zero regions are below black level and become invalid clipped
black data during RAW normalization.

That invalid padding affects:

- Flat-field model estimation.
- Per-frame flat-field division.
- Base/reference density statistics.
- Automatic exposure and percentile-based normalization.
- Any future model that assumes all pixels in the RAW frame are real capture
  data.

The full-scan backlight DNG has the same invalid zero padding and also contains
substantial saturation. That means the current backlight can corrupt the flat
field model even when its dimensions match the stitched scan.

## Secondary Issues

- The synthetic stitched DNG does not fully preserve source DNG metadata such as
  original white balance, exposure, ISO, color matrices, and related RAW tags.
- Hard tile seams remain visible. These are primarily stitching/integration
  quality issues, but they also affect downstream percentile and local contrast
  operations.
- Bayer phase must remain aligned. Any crop or placement adjustment needs even
  pixel boundaries so the CFA pattern is not shifted.

## Required Pi-Side Direction

The Pi stitched RAW output should keep the browser and PC contract unchanged:

- Canonical artifact: `stitched_raw.dng`
- Browser preview: derived JPEG/PNG preview
- Optional sidecars are allowed for masks/metadata

Recommended changes on the Pi side:

1. Do not leave uncovered canonical RAW canvas regions as numeric zero.
   At minimum, fill invalid regions with the DNG black level. This prevents
   below-black values, but it does not make those pixels valid image data.

2. Add a valid-coverage sidecar for every stitched RAW.
   Recommended names:
   - `stitched_raw_valid_mask.png`
   - `stitched_raw_metadata.json`

   The mask should mark which pixels came from real tile data. The PC pipeline
   can then ignore invalid regions during flat-field modeling, base detection,
   and final cropping.

3. Prefer cropping the canonical stitched DNG to the largest valid covered
   rectangle when that does not break the fixed-size calibration contract.
   Crops must use even x/y boundaries to preserve Bayer phase. The same crop
   policy must be applied to scan frames and backlight frames.

4. Preserve relevant source RAW metadata from the tile DNGs when writing the
   stitched DNG:
   - BlackLevel / WhiteLevel
   - CFA pattern tags
   - AsShotNeutral / camera white balance
   - Exposure time
   - ISO
   - Color matrices and calibration tags when available

5. Capture the stitched backlight with the same grid, placement, crop/mask, and
   Bayer phase as the scan frame. Avoid saturation in the backlight; saturated
   backlight pixels are not usable for physical flat-field correction.

6. Keep tile placement and any final crop CFA-safe:
   - x/y placement offsets should be even where possible.
   - final DNG width/height should preserve the expected Bayer pattern.

## PC-Side Handling Until Pi Fixes Land

For now, the PC side should treat stitched DNG support as provisional:

- Single-tile DNG validation remains the compatibility baseline.
- Stitched DNG processing should require a matching full-scan backlight.
- Any invalid canvas padding should be masked or cropped before using the frame
  as input to negative physical correction.
- If no reliable mask exists, processing should fail clearly rather than
  silently using zero-padded regions as real film data.

## Current Next Step

The negative pipeline has been rolled back to the pre-integration baseline.
The immediate integration path is:

1. Reconnect the PC adapter to the baseline pipeline using:
   - frame DNG
   - matching backlight DNG
   - matching base-frame DNG
   - `reference_layout.json`
2. Validate the adapter with a single known-good DNG tile.
3. Only after that validation, reintroduce output cleanup and bbox-assisted
   base/film region selection as separate, controlled changes.
