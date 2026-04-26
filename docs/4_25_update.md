# 4/25 Update: Streaming Tile RAW Pipeline

## Direction Change

The pipeline is moving away from Pi-side stitched RAW processing.

Reason:
- Single captured DNG tiles can run through the negative post-processing pipeline correctly.
- Synthetic multi-tile `stitched_raw.dng` created compatibility problems with RAW metadata, black-level padding, flat-field dimensions, memory use, and color behavior.
- Full stitched RAW plus stitched backlight was too large for reliable processing and debugging.

New direction:
- Pi captures fixed-control RAW DNG + JPG preview per tile.
- Pi uploads each tile to the PC immediately after capture.
- PC processes each single DNG tile using the matching backlight tile.
- PC integrates the processed tile outputs after post-processing, not before.

This keeps RAW processing close to the known-good single-DNG path and reduces peak memory pressure.

## Current Implemented Logic

### Pi Scanner Server

Files:
- `software/src/scanner.py`
- `software/src/coordinator.py`
- `software/src/main.py`

Current behavior:
- `Scanner.capture(row, col)` now returns paths for the captured DNG and JPG.
- `/scan/start` accepts tile upload mode:
  - `upload_mode: "tiles"`
  - `tile_upload_url`
  - `calibration_kind`
- During a scan, after each tile capture, the Pi uploads:
  - `raw_file`: `row_X_col_Y.dng`
  - `preview_file`: `row_X_col_Y.jpg`
  - `row`, `col`, `total_rows`, `total_cols`
- If tile upload mode is active, Pi skips Pi-side `integrate_captures()` and no longer waits to build/upload `stitched_raw.dng`.
- Legacy stitched upload path remains available for compatibility.
- Pi server has been tested reachable at `http://rpi.local:5000/scan/status`.

### PC Server Tile Receive

Files:
- `software/server.py`
- `software/job_orchestrator.py`
- `software/calibration_store.py`

Current endpoints:
- `POST /internal/jobs/{job_id}/receive_tile`
- `POST /internal/calibration/{kind_name}/receive_tile`
- `GET /api/jobs/{job_id}/tiles`

Current behavior:
- Job tiles are stored under:
  - `software/web/jobs/{job_id}/tiles/row_X_col_Y.dng`
  - `software/web/jobs/{job_id}/tiles/row_X_col_Y.jpg`
- Backlight calibration tiles are stored under:
  - `software/web/calibration/backlight/tiles/row_X_col_Y.dng`
  - `software/web/calibration/backlight/tiles/row_X_col_Y.jpg`
- Backlight capture from the PC dev page now starts a full Pi scan in per-tile mode, not a stitched RAW scan.
- Old single stitched backlight DNG is cleared when starting a new backlight tile-set capture.

### Streaming Bootstrap And Processing

Files:
- `software/job_orchestrator.py`
- `software/processing_adapter.py`

Current behavior:
- The first three uploaded tiles are used as bootstrap tiles.
- PC uses the first three JPG previews, not middle tiles.
- Bootstrap does:
  - classification vote across the first three JPG previews
  - base bbox detection across the first three JPG previews
  - filters base bbox results by confidence threshold
  - maps accepted base bboxes from preview coordinates into RAW coordinates
  - averages accepted RAW-space base bboxes
  - writes job-local `reference_layout_streaming.json`
- Default base bbox confidence threshold:
  - `BASE_BBOX_CONFIDENCE_THRESHOLD=0.35`
- After bootstrap succeeds:
  - already uploaded tiles begin processing
  - later uploaded tiles are processed as soon as they arrive
  - classification is reused for all tiles
  - base reference layout is reused for all tiles
  - each tile uses its matching backlight tile when available
- Default tile processing concurrency:
  - `TILE_PROCESS_WORKERS=2`

### Processing Adapter

File:
- `software/processing_adapter.py`

Current behavior:
- `ProcessingAdapter.run(...)` can accept precomputed:
  - `classifier_result`
  - `bbox_metadata`
  - `reference_layout_path`
- Negative RAW processing prefers same-position backlight tiles:
  - film tile `row_X_col_Y.dng`
  - backlight tile `backlight/tiles/row_X_col_Y.dng`
- If a separate base frame is missing or mismatched, the tile itself can be used as the base reference source with the streamed reference layout.
- The current RAW branch stays close to the existing single-DNG negative pipeline.

## Important Current Assumptions

- Tile filenames encode scan position as `row_X_col_Y`.
- The matching backlight tile must have the same row/col stem as the film tile.
- The first three uploaded tiles are representative enough for:
  - classification
  - base bbox detection
- The averaged base bbox is reusable across tiles in the same scan.
- Final integration should happen on processed RGB outputs, not RAW Bayer mosaics.

## Known Gaps / Still Need To Change

### 1. Processed Tile Integration Still Uses Simple Grid Placement

Current final integration is a basic row/col mosaic of processed tile PNGs.

Needed:
- Reuse scan alignment data or manual alignment vectors for processed tile placement.
- Account for serpentine scan ordering and X-axis reversal consistently.
- Apply overlap-aware placement or seam handling on processed RGB outputs.
- Avoid black gaps and ragged edges with final crop logic.

### 2. Streaming Tile Processing Needs Real Test With Live Scan

Implemented but not fully validated with a real full scan.

Need to test:
- backlight tile-set capture
- film tile upload
- first-three bootstrap
- per-tile negative RAW processing
- processed tile mosaic output
- cancellation behavior while streaming processing is active

### 3. Bootstrap Confidence Policy Needs Calibration

Current threshold is conservative:
- `BASE_BBOX_CONFIDENCE_THRESHOLD=0.35`

Needed:
- Inspect actual first-three bootstrap metadata.
- Decide a stable threshold.
- Add fallback behavior if fewer than three base detections pass threshold.
- Possibly use median instead of mean if detections are noisy.

### 4. Classification Voting Is Basic

Current logic uses majority vote from the first three previews.

Needed:
- Record per-tile classifier raw labels and confidence/mode if available.
- Add fallback if classifier returns unknown on all three.
- Consider using only valid film-containing tiles if early scan tiles are mostly border/backlight.

### 5. Tile-Level Film Content Crop Is Not Fully Solved

Current streaming path uses job-level base reference, but tile-level film-content bbox is disabled during per-tile processing.

Needed:
- Decide whether each tile should be cropped to film content before processing.
- If yes, detect/derive per-tile film bbox separately from base bbox.
- Ensure backlight crop uses the same bbox as the film tile.

### 6. PC Dev UI Needs Tile-Set Awareness

Current backend supports tile sets, but UI still needs clearer status.

Needed:
- Show backlight tile count and completeness.
- Show current film job tile upload count.
- Show bootstrap state:
  - waiting for first 3 tiles
  - classifying
  - base bbox averaging
  - processing tiles
- Expose `tile_bootstrap.json` or summary in dev UI.

### 7. Calibration Compatibility Summary Still Has Legacy Concepts

The calibration summary still exposes single-DNG calibration state alongside tile-set state.

Needed:
- Make tile-set readiness explicit.
- Avoid confusing old stitched backlight DNG with new tile backlight set.
- Update docs/UI wording to say backlight is now a per-tile calibration set.

### 8. Failure Recovery Needs Hardening

Needed:
- If one tile upload fails, decide whether to retry, skip, or fail the job.
- If one tile processing subprocess fails, decide whether to continue remaining tiles.
- Add clearer job error messages for:
  - bootstrap failure
  - missing matching backlight tile
  - tile processing timeout
  - final integration failure

### 9. Performance Needs Measurement

Current concurrency is set to 2 workers by default.

Needed:
- Measure memory and processing time on the PC.
- Tune `TILE_PROCESS_WORKERS`.
- Avoid running too many RAW subprocesses at once.
- Consider queueing by scan order so final preview can update progressively.

### 10. Pi Workspace Cleanup Still Needed

Pi server code was synced and started, but the Pi workspace still contains:
- local captures
- calibration files
- backups
- possible old config backup files

Needed:
- Clean only safe generated data.
- Keep manual alignment calibration.
- Keep current `config.py`.
- Avoid deleting live captures needed for testing.

## Recommended Next Test Order

1. Capture backlight tile set from PC dev page.
2. Confirm `software/web/calibration/backlight/tiles` contains 16 DNG files for 4x4.
3. Start one full film scan.
4. Watch job state after first three tile uploads:
   - bootstrap metadata should appear
   - processing should start before scan ends
5. Confirm later tiles are processed as they arrive.
6. Inspect:
   - `tile_bootstrap.json`
   - per-tile processed outputs
   - final processed mosaic
7. Only after color looks correct, improve processed RGB tile integration.
