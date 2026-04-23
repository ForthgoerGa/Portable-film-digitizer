# Web Integration Framework

Status: draft for integration planning
Date: 2026-04-23
Owner branch at time of writing: `feature/pipeline-proxy`

## 1. Purpose

This document defines the target user-facing web workflow, system boundaries,
network interfaces, repository ownership, and phased integration plan for the
film digitizer software stack.

The goal is to stop treating the repository as several unrelated demos and
instead define one canonical product flow:

1. The user operates from a PC.
2. The PC is connected to the Raspberry Pi through a phone hotspot.
3. The user presses one button to start the job.
4. The Raspberry Pi performs scan capture and stitch/integration.
5. The Raspberry Pi sends `stitched_raw.dng` to the PC.
6. The PC performs classification, routing, and post-processing.
7. The user sees:
   - the integrated raw scan,
   - the final processed image,
   - a single end-to-end progress indicator.

## 2. Final Product Definition

### 2.1 User story

The user loads film, confirms alignment, and presses `One-Click Scan`.

After that point, the system should run the full chain automatically:

`scan -> stitch -> classify -> post-process -> present result`

### 2.2 Final user-visible screens

The final PC web app should be a single primary workflow, not multiple debug
tabs for different subsystems.

Required UI areas:

1. Job setup
   - one-click start button
   - cancel button

2. Progress
   - one unified progress bar
   - current stage label
   - optional sub-status text

3. Raw scan result
   - full integrated raw scan preview from the Raspberry Pi scan/stitch stage

4. Final processed result
   - final post-processed image preview from the PC pipeline stage

5. Optional secondary information
   - job history
   - warnings
   - advanced debug information hidden behind a details panel

### 2.3 Developer-only controls

The final product should still preserve a small developer/calibration surface.

Required dev-only action:

- `Scan Backlight Frame`
  - triggers a dedicated scan/capture action for flat-field correction
  - should remain outside the primary user flow
  - should live behind a developer/details panel in the PC UI

### 2.4 Progress stages

The final progress bar should represent the whole job, not only the currently
running subsystem.

Recommended normalized stages:

| Stage | Label | Owned by | Suggested progress range |
| --- | --- | --- | --- |
| 0 | idle | PC | 0% |
| 1 | dispatching job to Pi | PC -> Pi | 1-5% |
| 2 | scanning frames | Pi | 5-50% |
| 3 | stitching `stitched_raw.dng` | Pi | 50-70% |
| 4 | transferring stitched raw to PC | Pi -> PC | 70-78% |
| 5 | classifying film / selecting pipeline | PC | 78-85% |
| 6 | post-processing | PC | 85-98% |
| 7 | complete | PC | 100% |
| 8 | failed / cancelled | PC | terminal |

## 3. Canonical System Boundaries

### 3.1 Product-facing architecture

The browser should only talk to the PC application server.

Recommended final topology:

`Browser on PC -> PC App Server -> Raspberry Pi Scanner Worker`

`Browser on PC -> PC App Server -> Local Processing Adapter`

This keeps the browser simple and avoids direct browser dependencies on Pi IPs,
cross-origin setup, or processing implementation details.

### 3.2 Service roles

#### A. PC App Server

Canonical role:

- hosts the final user-facing web app
- owns the job lifecycle shown to the user
- starts scan jobs on the Pi
- receives stitched raw artifacts from the Pi
- invokes classification and post-processing locally
- exposes raw/final artifact URLs to the browser

This should become the primary product entry point.

#### B. Raspberry Pi Scanner Worker

Canonical role:

- owns scan hardware
- moves the scanner
- captures source images
- integrates/stitches `stitched_raw.dng`
- reports scan progress
- transfers the stitched artifact to the PC
- supports developer calibration actions such as backlight-frame capture

This should be treated as a worker service, not the final user-facing app.

#### C. Processing Adapter on the PC

Canonical role:

- chooses the active post-processing path
- runs the selected pipeline
- returns status, metadata, and final artifact paths

This does not need to be a separate HTTP service. It can remain a local module
or subprocess layer behind the PC App Server.

### 3.3 Non-canonical but still useful tools

Some code in the repo remains valuable, but should not be treated as the final
product shell:

- `software/rpi_camera_server.py`
  - useful as a Raspberry Pi capture and developer UI tool
  - not the final PC user product shell

- `software/legacy/*`
  - historical reference only
  - should not be extended further unless a file must be copied out

## 4. Current Repository Resource Inventory

The repository currently contains several partially overlapping flows.

### 4.1 PC web and API layer

| Path | Current role | Status |
| --- | --- | --- |
| `software/server.py` | PC FastAPI server, camera proxy, process orchestration | active, best current base for final product shell |
| `software/web/*` | PC browser UI with camera capture, AI processing, scan debug views | active, closest current basis for final user-facing web |

Current strengths:

- already PC-centric
- already displays raw-ish and processed artifacts
- already has progress logic for the agentic path
- already proxies Pi camera capture to the browser

Current gaps:

- still split between camera tab and scan debug tab
- still models Pi capture and processing as separate manual actions
- not yet the final one-button scan-to-result flow

### 4.2 Raspberry Pi scan control layer

| Path | Current role | Status |
| --- | --- | --- |
| `software/src/main.py` | scanner REST API | active, in progress |
| `software/src/web/*` | scanner-side scan UI / file explorer | active, in progress |
| `software/src/coordinator.py` | scan state machine | active, in progress |
| `software/src/scanner.py` | low-level scanner hardware abstraction | active, in progress |

Current strengths:

- has a distinct scan worker API
- has scan progress concepts
- has file browsing for captures
- recent work on file viewer and scanner CLI continues in `rpi-serial`
- current Pi runtime evidence shows:
  - `python3 main.py` starts a FastAPI worker on port 5000
  - `/motor/move` works from both browser UI and CLI
  - `python3 cli.py` can move motors interactively

Current gaps:

- not yet integrated into the PC product shell
- not yet clearly scoped as a worker-only service
- scan loop / camera capture path is still incomplete
- current runtime logs show `Camera not available, skipping capture`
- `stitched_raw.dng` is now the intended canonical output boundary, but is not
  yet fully implemented end-to-end

### 4.3 Existing scan and negative demo path

| Path | Current role | Status |
| --- | --- | --- |
| `software/coordinator.py` | older mock scan orchestrator | usable reference |
| `software/stitcher.py` | tile stitching and negative demo output | functional demo/reference |
| `software/processing/negative_pipeline.py` | deterministic negative processing pipeline | usable now |

Current strengths:

- already has stitched raw output and final output concepts
- useful as the first integrated end-to-end processing path

Current gaps:

- older ownership model
- tied to mock/demo scan flow
- not aligned with the new Pi scanner worker structure

### 4.4 Agentic post-processing path

| Path | Current role | Status |
| --- | --- | --- |
| `Agentic_Post_Processing/*` | classification, routing, evaluation, and refinement framework | draft completed, partially integrated |

Current strengths:

- good code and tests exist
- already wrapped by `_run_single.py`
- already wired into `software/server.py` via `/process_capture`
- fits the future role of classifier/router before branch-specific processing

Current gaps:

- not yet the final canonical processing adapter
- not yet driven by a stitched raw scan from the Pi worker
- not yet productized into the final one-button flow

### 4.5 Physical raw negative path

| Path | Current role | Status |
| --- | --- | --- |
| `Post_Processing_Negative/*` | negative-film branch implementation for raw film processing | advanced R and D, not integrated |

Current strengths:

- strongest raw-aware negative path in the repo
- already used by the Pi developer server for calibration/dev workflows
- matches the intended negative-film processing branch in the final pipeline

Current gaps:

- not yet integrated into the PC product orchestration flow
- still needs to be wrapped behind the classification/router layer

### 4.6 Legacy archive

| Path | Current role | Status |
| --- | --- | --- |
| `software/legacy/*` | preserved older software tree | archive only |

## 5. Current Completion Snapshot

This snapshot is based on repository inspection on 2026-04-23.

| Subsystem | Completion level | Notes |
| --- | --- | --- |
| PC product shell | medium | strongest current base is `software/server.py` + `software/web/*` |
| Pi scan worker API | medium | `software/src/*` exists and is still being actively developed |
| Pi full stitched raw output contract | medium | frozen in this document as `stitched_raw.dng`, not yet implemented end-to-end |
| Pi motor control and worker boot path | medium to high | current Pi runtime shows worker startup and motor movement are usable |
| Pi scan loop and capture integration | low to medium | scan start path exists, but camera/loop reliability is still in progress |
| Deterministic negative pipeline | medium to high | available now in `software/processing/*` |
| Classification/routing layer | medium | draft largely done in `Agentic_Post_Processing`, partial server integration exists |
| Negative-film branch implementation | medium | advanced `Post_Processing_Negative` code exists but is not yet integrated |
| Unified job orchestration | low | still spread across multiple demos and APIs |
| Final one-button UX | low | defined here, not implemented yet |

## 6. Current Network Interface Inventory

### 6.1 Existing PC server interfaces

Current PC server: `software/server.py`

Existing useful endpoints:

- `POST /scan`
- `GET /status`
- `POST /reset`
- `POST /cancel`
- `GET /serial/ports`
- `POST /serial/connect`
- `POST /serial/disconnect`
- `GET /serial/status`
- `POST /receive_capture`
- `GET /pi_captures`
- `GET /camera/stream`
- `POST /camera/capture`
- `GET /camera/captures`
- `GET /camera/captures/{filename}`
- `POST /camera/send-to-pc/{filename}`
- `GET /camera/status`
- `POST /process_capture`
- `GET /process_status/{job_id}`

Observation:

This server already combines product-like UI concerns and processing concerns.
It is the strongest current candidate for the final browser-facing app server.

### 6.2 Existing Pi camera/dev interfaces

Current Pi camera server: `software/rpi_camera_server.py`

Existing useful endpoints:

- `GET /stream`
- `POST /capture`
- `GET /captures`
- `GET /captures/{filename}`
- `POST /send/{filename}`
- `GET /status`
- `GET /dev`
- `GET /dev/state`
- `POST /dev/set-role`
- `POST /dev/set-roi`
- `POST /dev/run-pipeline`
- `POST /dev/stop-pipeline`
- `GET /dev/images/{filename}`

Observation:

This file is a powerful developer tool and calibration/capture surface, but it
should not be the final user-facing orchestration shell.

### 6.3 Existing Pi scan worker interfaces

Current scanner worker: `software/src/main.py`

Existing useful endpoints:

- `POST /scan/start`
- `POST /scan/cancel`
- `GET /scan/status`
- `POST /motor/move`
- `POST /motor/home`
- `POST /motor/set_home`
- `GET /motor/position`
- `GET /captures/tree`
- `GET /captures/list`
- `GET /captures/file`
- `GET /captures/meta`

Observation:

This service should evolve into the canonical Pi worker API, but its current
surface is scan-worker-shaped, not product-shell-shaped.

Current real-world status from the latest team test:

- the worker boots on the Pi and serves `/web/`
- browser status polling and motor position polling work
- browser and CLI motor movement work
- `POST /scan/start` enters scan flow, but the scan loop is not yet reliable
- capture integration is still incomplete on the tested setup

## 7. Recommended Final Interface Model

### 7.1 Browser contract

The browser should only call the PC App Server.

Canonical browser-facing endpoints should be grouped under `/api/*`.

This prefix exists only so the PC browser has one stable contract to call.
The browser should not need to know any Raspberry Pi path names.

Recommended external API:

- `POST /api/jobs`
  - create a new one-click scan job
  - request body:
    ```json
    {
      "job_kind": "one_click_scan"
    }
    ```
- `GET /api/jobs/{job_id}`
  - get unified job status, progress, and artifact URLs
- `POST /api/jobs/{job_id}/cancel`
  - cancel the whole job
- `GET /api/jobs/{job_id}/artifacts/raw`
  - fetch a browser-safe raw preview derived from `stitched_raw.dng`
- `GET /api/jobs/{job_id}/artifacts/final`
  - fetch the processed final artifact
- `GET /api/system/status`
  - high-level system health for UI bootstrapping

Recommended developer-only PC endpoints:

- `POST /api/dev/calibration/backlight`
  - request a backlight frame capture / scan for flat-field correction
- `GET /api/dev/calibration/backlight/latest`
  - fetch metadata or preview for the latest backlight artifact

The browser should not call Pi worker endpoints directly in the final product.
All Pi-specific endpoint naming stays behind the PC server boundary.

### 7.2 PC App Server to Pi Scanner Worker

This should be an internal network contract between the PC server and the Pi.

Recommended worker API:

- `POST /worker/scan-jobs`
  - body:
    ```json
    {
      "job_id": "pc-generated-id",
      "scan_kind": "full_scan"
    }
    ```

- `GET /worker/scan-jobs/{job_id}`
  - returns Pi-side scan status, scan stage, scan progress, and artifact metadata

- `POST /worker/scan-jobs/{job_id}/cancel`

- `POST /worker/scan-jobs/{job_id}/upload-stitched-raw`
  - Pi sends `stitched_raw.dng` to the PC once ready

- `POST /worker/calibration/backlight`
  - runs the developer calibration path for flat-field reference capture

Pi worker output contract for normal user jobs:

- primary artifact: `stitched_raw.dng`
- fixed image dimensions for the current scanner configuration
- image contains both film and non-film regions
- Pi does not perform final segmentation/crop for product processing
- Pi may additionally expose or upload a lightweight preview image for UI use

If the team wants to move incrementally, the current `software/src/main.py`
surface can be adapted behind this contract without rewriting all internals at
once.

### 7.3 PC processing adapter contract

This should remain local to the PC server at first.

Recommended adapter contract:

- input:
  - `stitched_raw.dng`
  - stitched preview image derived from the DNG when needed
  - optional processing mode override

- output:
  - `classification`
  - `selected_branch`
  - `raw_preview_path`
  - `final_output_path`
  - `metadata_path`
  - progress callbacks

Recommended final processing policy:

1. build or derive a stitched preview from `stitched_raw.dng`
2. run classification on the stitched preview
3. classify into exactly one of:
   - `positive_film`
   - `instax_instant_film`
   - `negative_film`
4. route to the corresponding processing branch
5. run segmentation/cropping/base-region analysis as required by that branch

Recommended architectural interpretation of existing repo assets:

- `Agentic_Post_Processing/*`
  - owns classification, routing, evaluation, and refinement logic
- `Post_Processing_Negative/*`
  - is the negative-film branch implementation
- positive-film and instax branches
  - still need implementation or integration work

Practical near-term policy:

1. Phase 1:
   - prove the end-to-end path with one stable branch
   - negative branch is the most mature candidate today

2. Phase 2:
   - wire the classifier/router in front of branch selection

3. Phase 3:
   - complete positive and instax branches

## 8. Unified Job Model

The PC App Server should own one canonical job record.

Recommended fields:

```json
{
  "job_id": "string",
  "state": "idle|running|completed|failed|cancelled",
  "stage": "dispatch|scan|stitch|transfer|classify|segment|process|complete",
  "progress_pct": 0,
  "started_at": "timestamp",
  "updated_at": "timestamp",
  "error": null,
  "scan": {
    "current_row": 0,
    "current_col": 0,
    "rows": 0,
    "cols": 0
  },
  "processing": {
    "classification": "positive_film|instax_instant_film|negative_film|null",
    "selected_branch": "positive|instax|negative|null",
    "current_iteration": null,
    "max_iterations": null,
    "score": null
  },
  "artifacts": {
    "stitched_raw_dng_url": null,
    "raw_preview_url": null,
    "final_preview_url": null,
    "final_download_url": null,
    "metadata_url": null,
    "backlight_reference_url": null
  }
}
```

Artifact semantics:

- `stitched_raw_dng_url`
  - canonical full-fidelity scan artifact used for processing
- `raw_preview_url`
  - browser-safe preview rendered from the DNG for UI display
- `final_preview_url`
  - browser-safe processed result preview

This model should drive the final progress bar and both image panels.

## 9. Recommended Repository Ownership Going Forward

### 9.1 Ownership table

| Path | Final role | Disposition | Notes |
| --- | --- | --- | --- |
| `software/server.py` | PC product orchestration shell | keep and extend | final browser-facing `/api/*` should terminate here |
| `software/web/*` | PC product UI | keep and extend | final one-click UX belongs here |
| `software/src/*` | Pi scanner worker | keep and extend | scan hardware, scan loop, stitch/export, worker API |
| `software/processing/*` | older deterministic processing helpers | keep as adapter/reference | useful for early integration and fallback logic |
| `Agentic_Post_Processing/*` | classification and routing layer | integrate and extend | should sit in front of branch-specific processing |
| `Post_Processing_Negative/*` | negative-film branch implementation | integrate and extend | strongest current negative branch |
| `software/rpi_camera_server.py` | Pi developer/calibration tool | dev-only | useful for calibration and experiments, not final product shell |
| `software/legacy/*` | historical archive | reference-only | do not extend as active product code |

### 9.2 Immediate clean separation

Before major code movement, the team should adopt this ownership rule:

- browser/product UX belongs to `software/web/*`
- PC orchestration and browser-facing `/api/*` belong to `software/server.py`
- Pi worker behavior belongs to `software/src/*`
- classification/routing belongs behind the PC processing adapter
- branch-specific image pipelines stay behind adapter boundaries and should not
  directly own product UI

## 10. Phased Implementation Plan

### Phase 0: Freeze the integration contract

Deliverables:

- this document
- agreement that the browser talks only to the PC server
- agreement that the Pi scanner is a worker, not the product shell
- agreement that the Pi output contract is `stitched_raw.dng`
- agreement that post-processing owns segmentation/crop/base analysis

### Phase 1: Add orchestration boundaries only

Deliverables:

- scanner-side pipeline proxy contract defined
- PC-side job model defined
- developer backlight/calibration path defined
- no major UI rewrite yet

Notes:

- this phase is about interface shape, not polishing features

### Phase 2: Make the one-click path work with one stable processing path

Deliverables:

- one browser button starts one PC job
- PC server starts one Pi scan job
- Pi returns `stitched_raw.dng`
- PC runs one stable branch through the processing adapter
- browser shows raw + final + progress

Recommended processing path for this phase:

- use the negative branch first if a temporary single-branch integration is
  needed to validate the end-to-end path

### Phase 3: Integrate classification and richer processing selection

Deliverables:

- classification-driven processing mode selection
- agentic classification/routing integration behind the processing adapter
- negative branch integrated through `Post_Processing_Negative`
- placeholders or initial implementations for positive and instax branches
- better metadata in the progress UI

### Phase 4: Replace debug UI with final product UI

Deliverables:

- single streamlined page
- no separate debug-first tabs in the primary product flow
- advanced diagnostics moved behind optional controls

## 11. Current Branch Note

At the time of writing:

- `integration` already contains the latest `origin/rpi-serial` merge
- `feature/pipeline-proxy` has already been updated from `integration`
- this document reflects the current planning baseline for integration work

## 12. Immediate Next Actions

1. Approve this boundary:
   - PC server is the product shell
   - Pi scanner is a worker

2. Freeze the browser-facing `/api/*` contract on the PC server

3. Freeze the Pi worker contract for `stitched_raw.dng` and backlight capture

4. Add the scanner-side pipeline proxy layer without changing scanner UI logic

5. Add the PC-side unified job API skeleton

6. Rework the PC web UI around one canonical scan job flow
