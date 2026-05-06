# Yuhong Chen Worklog

[[_TOC_]]


Note: Entries from February and March are reconstructed from the project kickoff notebook, design documents, repository history, and engineering work completed during the semester. They are written as session-level records where exact daily notes were sparse.

# 2026-02-10 - Project Kickoff and Initial Requirements

I started the project by reviewing the initial goal for Team 23: a portable film digitizer that could scan common film formats and return a processed digital image through an automated workflow. The kickoff notes defined the first version of the system around an IMX477 camera, macro lens stack, controlled illumination, motion control, and cloud/PC-side image processing.

- Session goal: Understand the project objective and translate the initial design idea into software-relevant requirements.
- Work completed: I reviewed the kickoff requirements for resolution, portability, automation, motion control, illumination, and processing. I identified the software responsibilities that would later become the scanner control server, browser interface, image upload path, and negative post-processing pipeline.
- Debugging observations: The initial goal of a 15 second capture-to-result workflow was aggressive because it included motor movement, multi-frame capture, wireless transfer, processing, and user feedback. I recorded this as a latency risk for later verification.
- Design decisions: I treated 4K output, automated user flow, and reliable RAW processing as the core software requirements. Multi-format support remained a goal, but the software path needed to work first on a controlled film sample.
- Verification/test evidence: The kickoff notebook recorded the initial high-level requirements and subsystem requirements, including IMX477 imaging, macro optics, dual-mode illumination, and automated capture.
- Next steps: Draft a block diagram and map the scanner, processing, and web interface responsibilities.

[PLACEHOLDER: Insert screenshot/photo of the 2026-02-10 kickoff notes or initial requirement list]

# 2026-02-17 - System Architecture and Software Boundary Planning

I used the early project documents to separate the system into hardware control, capture, networking, processing, and user-interface blocks. My focus was to make sure the software architecture could eventually support a one-click scan rather than a collection of disconnected scripts.

- Session goal: Convert the high-level project concept into an initial software architecture.
- Work completed: I reviewed the block-level flow: user inserts film, controller configures motion and illumination, camera captures RAW data, the image is uploaded, the processing pipeline runs, and the result is returned to the user. I marked the web interface and image processing pipeline as areas I would own or coordinate closely.
- Debugging observations: The early design assumed an STM32 master/slave style hardware controller, but the final scanner control path was still uncertain. I kept the software interface abstract enough that the low-level motor implementation could change later.
- Design decisions: I planned for a server boundary between scanner control and image processing. This allowed the scanner side to focus on hardware timing while the PC/cloud side focused on job state and image transformation.
- Verification/test evidence: The project overview and system architecture documents listed the intended software subsystems: firmware state machine, API receiver/storage, image processing pipeline, and optional web client.
- Next steps: Define a small web workflow and early API shape that could be tested with mocked scanner and processing components.

[PLACEHOLDER: Insert system block diagram showing hardware control, capture, upload, processing, and browser result display]

# 2026-02-24 - Resolution, Latency, and Processing Requirement Review

I reviewed the requirement and verification targets from a software perspective. The most important risk was that image quality and latency compete with each other: RAW processing gives better results, but RAW files are larger and slower to transfer and process.

- Session goal: Identify which requirements directly constrain the web and processing implementation.
- Work completed: I mapped the 3840 x 2160 output requirement to the later stitching/integration design, and mapped the cloud processing requirement to an HTTP upload/result API. I also listed the expected verification evidence: image dimensions, timing measurements, API request success, and visible scan output.
- Debugging observations: The original 15 second target was not realistic for a full multi-tile RAW scan, but the per-image negative processing target of under 30 seconds was a more useful software verification point.
- Design decisions: I decided that the final software should report progress by stage so that a longer full scan could still be usable and debuggable. This later became the unified progress model in the PC web interface.
- Verification/test evidence: The requirements document identified resolution, dynamic range, latency, multi-format support, and user feedback as the primary verification categories.
- Next steps: Build an early prototype that could move data from capture to processing and show a user-visible result.

[PLACEHOLDER: Insert table mapping software requirements to planned verification evidence]

# 2026-03-03 - Block Diagram and Prototype Workflow Planning

I worked through the first practical version of the scan-to-result workflow. The goal was not yet final image quality; it was to prove that data could flow through the system from a scanner-side capture concept to a processed output visible to the user.

- Session goal: Plan the prototype workflow for capture, upload, processing, and result display.
- Work completed: I reviewed the repository scaffold and the block diagram. I outlined the first prototype modules: scanner coordinator, serial/control abstraction, camera capture placeholder, uploader, processing function, and web UI.
- Debugging observations: The hardware side was not stable yet, so the software needed mockable interfaces. This made it possible to test the web and cloud pieces before final scanner hardware was available.
- Design decisions: I planned to keep camera capture, motion control, stitching, and upload as separate modules. That separation later made it easier to replace stitched RAW processing with per-tile DNG streaming.
- Verification/test evidence: The repository history shows early project scaffolding and documentation on 2026-02-10, followed by the first integrated prototype work in early March.
- Next steps: Implement a web scan flow that can call a coordinator and display tiled/processed images.

[PLACEHOLDER: Insert early repository or prototype workflow screenshot]

# 2026-03-08 - Web Scan Flow and Cloud Processing Prototype

I worked on the first meaningful software prototype that connected a browser scan flow to backend processing and a result path. This was still a mock/early implementation, but it established the shape of the final system.

- Session goal: Build and test a prototype web flow for scanner coordination, film processing, and result upload/display.
- Work completed: I integrated a film processing pipeline into the web scan flow, added RPi-style control loop placeholders, connected serial/cloud endpoint abstractions, and displayed tiled images/results through the browser. I also reviewed cloud upload behavior and result URL handling.
- Debugging observations: Early cloud/serverless processing was useful for proving the API concept, but it was not enough for final RAW negative quality. The later system needed a stronger local PC-side processing path for full-resolution DNG files.
- Design decisions: I kept the API-style processing boundary because it matched the requirement that the scanner could upload raw camera data and receive a result. Even after moving heavy work to the PC, the same HTTP contract remained useful.
- Verification/test evidence: Repository history from 2026-03-08 records the RPI control loop prototype, web scan flow integration, tiled image display, and processed result upload path.
- Next steps: Improve the negative processing logic and start planning a more serious RAW-aware pipeline.

[PLACEHOLDER: Insert screenshot of early web scan flow showing tiled images or uploaded processed result]

# 2026-03-15 - Negative Processing Prototype Review

I focused on the image-processing side and reviewed how a color negative should be transformed into a usable positive image. The early processing path used simpler raster operations, while the later work moved toward RAW-based flat-field correction and physical negative processing.

- Session goal: Understand the negative inversion pipeline and identify gaps between a demo filter and a reliable film pipeline.
- Work completed: I reviewed the early negative processing stages: inversion, white balance, level stretch, contrast enhancement, selective desaturation, and sharpening. I compared this with the expected need for RAW input, base/reference estimation, and illumination correction.
- Debugging observations: Simple inversion could produce an image quickly, but color quality was unstable because film base, illumination nonuniformity, and camera color response were not handled robustly.
- Design decisions: I treated the early OpenCV pipeline as a useful baseline, not the final processing architecture. The final system would need flat-field correction and reference-base logic before color/tone decisions.
- Verification/test evidence: Early processing outputs demonstrated that software inversion could run quickly enough, but later tests were needed on real Pi DNG captures.
- Next steps: Design a classifier and processing router so negative and positive film could eventually share the same capture workflow but use different processing branches.

[PLACEHOLDER: Insert before/after example from early negative inversion prototype]

# 2026-03-22 - Classifier and Processing Routing Planning

I planned the agentic processing structure that would later classify the scan and choose the correct processing path. This was important because the final user workflow should not require the user to understand film type or manually choose a pipeline.

- Session goal: Define how film classification, processing, and quality evaluation should fit into the overall pipeline.
- Work completed: I outlined a classifier stage, a processing stage, and an optional evaluator stage. I considered negative film, positive film, and instant film routing, while keeping the first working target focused on color negative film.
- Debugging observations: Relying on the browser to send a film-format selection would be fragile. A preview-based classifier was more consistent with the one-click goal, but it required a fast preview image that looked normal enough for a vision model.
- Design decisions: I planned to normalize classifier labels into product-level categories such as `negative_film`, `positive_film`, and `instax_instant_film`. This avoided leaking implementation-specific labels into the browser workflow.
- Verification/test evidence: The later agentic pipeline design document formalized this classifier -> processor -> evaluator structure.
- Next steps: Build the agentic pipeline scaffold and connect it to sample images before integrating it into the scanner server.

[PLACEHOLDER: Insert diagram of classifier, processor, evaluator, and branch routing]

# 2026-03-29 - RAW Capture and Backlight Calibration Planning

I prepared the software assumptions needed for real camera data. At this point the key issue was that a film scanner cannot rely only on display-ready JPGs; the post-processing pipeline needs RAW data and a calibration reference for uneven backlight.

- Session goal: Plan the capture data model for RAW tiles, preview images, and calibration frames.
- Work completed: I listed the files that a real scan should produce: DNG for processing, JPG/PNG preview for debugging and classification, and backlight reference frames for flat-field correction. I also planned a job directory structure so scan data could be inspected after a failed test.
- Debugging observations: A single full-frame scan would be simpler, but the macro lens and 4K output requirement pushed the design toward multiple overlapping tiles. That meant naming, row/column metadata, and per-tile references would matter.
- Design decisions: I planned to preserve raw capture files instead of overwriting intermediate data. This made later debugging possible when stitched RAW and per-tile RAW behaved differently.
- Verification/test evidence: The final project later used 4608 x 2592 DNG tiles and job folders containing tile captures, processed outputs, metadata, and final integrated images.
- Next steps: Implement the agentic processing pipeline and prepare it for real DNG inputs.

[PLACEHOLDER: Insert planned job folder tree for scan tiles, backlight tiles, metadata, and final output]

# 2026-04-06 - Agentic Film Pipeline Design

I reviewed and organized the agentic post-processing design. The goal was to make classification and processing modular enough that the scanner could later support multiple film types without rewriting the full web/scanner path.

- Session goal: Formalize the agentic film pipeline design and implementation plan.
- Work completed: I reviewed the design spec for `Agentic_Post_Processing`, including `ClassifierAgent`, `PostProcessingAgent`, `EvaluatorAgent`, shared film type models, pipeline parameters, and separate negative/positive processing functions.
- Debugging observations: The design was useful, but it assumed a single full-resolution scan image. Later hardware tests showed that the more reliable path was per-tile RAW processing followed by RGB integration.
- Design decisions: I kept classification as a separate boundary from processing. That decision survived later architecture changes because the scanner still needed one global film classification result before running all tiles.
- Verification/test evidence: The agentic pipeline design document defined the expected file layout, supported film types, configuration values, and tests for model/dataclass construction, routing, and orchestrator behavior.
- Next steps: Validate the processing pipeline on real negative samples and compare simple preview processing against RAW-based physical correction.

[PLACEHOLDER: Insert screenshot of agentic film pipeline design spec or implementation plan]

# 2026-04-14 - Decision Model and RAW Pipeline Diagnostics

I worked on making the processing pipeline more configurable and easier to debug. This was the bridge between the early agentic design and the later full integration work with Pi DNG tiles.

- Session goal: Improve decision routing and RAW pipeline diagnostics before hardware integration became the main bottleneck.
- Work completed: I reviewed updates for model configuration, decision-tier defaults, optional escalation, metadata logging, and RAW pipeline characterization. I paid attention to metadata because later debugging required knowing which classifier, model, and processing path generated a result.
- Debugging observations: Without explicit metadata, it was hard to tell whether a bad output came from capture, preview conversion, classification, base detection, flat-field correction, or the final processing branch.
- Design decisions: I kept `runner_used`, classification labels, confidence, base reference values, and processing outputs as explicit metadata fields in later server work.
- Verification/test evidence: Repository history from 2026-04-14 records decision-tier configuration updates, pipeline metadata logging, and RAW pipeline diagnostic improvements.
- Next steps: Move into full software integration with the Pi scanner branch, PC web framework, and negative post-processing branch.

[PLACEHOLDER: Insert metadata example showing classifier output, runner used, and RAW diagnostic values]

# 2026-04-18 - Project Software Integration Baseline

I started this phase by organizing my work around the full software path for the Team 23 portable automated macro-stitching film digitizer. My goal was to make the browser interface, PC server, Raspberry Pi scanner server, camera capture, and processing code behave as one system instead of separate demos.

- Session goal: Define the integration baseline and identify which software pieces had to be connected before the final pipeline test.
- Work completed: I reviewed the current scanner control code, the PC processing scripts, and the browser workflow. I mapped the intended architecture as Browser -> PC FastAPI server on port 8000 -> Pi scanner server on port 5000 -> per-tile RAW DNG capture/upload -> PC processing/integration.
- Debugging observations: The biggest risk was that the PC and Pi code had been evolving independently. Some controls existed on one side but did not have a stable API contract on the other side.
- Design decisions: I decided to treat the PC server as the owner of user-facing job state and image processing, while the Pi server stayed responsible for hardware movement, capture, and tile upload.
- Verification/test evidence: I used basic endpoint checks and manual UI traces to confirm that the browser could talk to the PC server and that the PC could issue scanner requests toward the Pi server.
- Next steps: Stabilize the API boundary, then move on to scan configuration, motor control, and tile capture tests.

[PLACEHOLDER: Insert screenshot/photo of full Browser PC Pi software architecture diagram]

# 2026-04-20 - Integration Branch and Merge Conflict Resolution

I spent this session moving the separate software branches into a shared integration branch. My focus was to preserve the working scanner controls while bringing in the newer PC-side processing and web framework changes.

- Session goal: Resolve branch conflicts without losing working scanner commands or processing updates.
- Work completed: I merged the web UI, PC FastAPI server work, and Pi scanner server updates into the integration branch. I manually resolved conflicts around job state, route names, capture configuration, and shared scan metadata.
- Debugging observations: Several conflicts were not simple text conflicts. Some code paths assumed the PC generated all scan state locally, while the Pi-side branch assumed the scanner server controlled more of the scan lifecycle.
- Design decisions: I kept the PC job model as the source of truth for browser-visible progress and made the Pi scanner report tile events back to the PC. This made later debugging easier because the PC logs contained the full scan and processing sequence.
- Verification/test evidence: After the merge, I verified that the server could start, the scanner routes were still reachable, and the UI could still create a scan job.
- Next steps: Write the web framework contract clearly enough that later work would not reintroduce PC/Pi ownership confusion.

[PLACEHOLDER: Insert screenshot/photo of integration branch merge conflict resolution notes]

# 2026-04-21 - Web Framework Definition and PC/Pi API Boundary

I defined the software boundary between the browser, PC server, and Pi scanner server. This became the contract I used for the rest of the final integration work.

- Session goal: Make the browser workflow talk only to the PC FastAPI server and make the PC server coordinate all scanner requests through the Pi server.
- Work completed: I documented and implemented the expected route flow: browser commands go to the PC server on port 8000, and the PC server sends scan, cancel, home, debug, and calibration commands to the Pi scanner server on port 5000.
- Debugging observations: Direct browser-to-Pi control would have been simpler for manual tests, but it would have split job state across two machines. That would make progress reporting, cancel behavior, and processing startup unreliable.
- Design decisions: I kept upload endpoints on the PC side. Normal DNG and JPG tiles are named with the `row_X_col_Y` pattern and upload through `/internal/jobs/{job_id}/receive_tile`. Backlight calibration captures upload through `/internal/calibration/backlight/receive_tile`.
- Verification/test evidence: I traced a scan setup request from the browser to the PC server and then to the Pi server. I also checked that the PC job stayed active while waiting for the Pi to send tile uploads.
- Next steps: Connect the scanner configuration panel to actual Pi scanner commands and test movement on hardware.

[PLACEHOLDER: Insert screenshot/photo of PC FastAPI route list and Pi scanner route list]

# 2026-04-22 - Pi Scanner Testing and Scan Controls

I moved from route-level testing to actual scanner behavior. The main goal was to make the web controls useful for real hardware testing instead of only server testing.

- Session goal: Verify motor movement, scan configuration, and scanner utility controls through the PC-to-Pi path.
- Work completed: I tested scan configuration values for rows, columns, overlap, capture delay, and movement direction. I also wired or verified cancel, home, and debug controls so I could stop a bad scan, return the stage to a known position, and inspect scanner state without restarting the whole stack.
- Debugging observations: Scanner behavior was sensitive to stale state. If a previous run was canceled or interrupted, the next run could start from the wrong assumption about position unless I homed or reset the scanner state first.
- Design decisions: I treated home as the recovery action before important tests and kept cancel separate from home. Cancel should stop a scan, while home should intentionally move the stage back to the reference position.
- Verification/test evidence: I ran Pi scanner tests that confirmed individual motor movement, multi-step movement, scan-grid stepping, cancel behavior, and home behavior. These tests gave me confidence that the software control loop could support a 4x4 tile scan.
- Next steps: Use the movement controls to build a manual alignment workflow and calibrate usable offsets.

[PLACEHOLDER: Insert screenshot/photo of Pi scanner controls showing scan configuration cancel home and debug buttons]

# 2026-04-23 - Manual Alignment Workflow and Offset Calibration

I worked on the practical alignment process needed before taking useful film scans. The project needed a repeatable way to line up the film and choose offsets that produced enough overlap for stitching.

- Session goal: Create a manual alignment workflow that let us position the frame, test offsets, and refine overlap before a full scan.
- Work completed: I used manual movement controls to align the film area under the camera, then tested row and column stepping to estimate the offset between tiles. I recorded settings that produced useful overlap without wasting too much scan time.
- Debugging observations: Small mechanical and positioning errors showed up clearly in the tile grid. The software could command a clean pattern, but the final integration still depended on film placement, stage motion repeatability, and overlap margin.
- Design decisions: I kept manual alignment as part of the normal workflow instead of trying to fully automate it at the end of the semester. The time cost was acceptable, and it made the final system more reliable for demos.
- Verification/test evidence: I checked adjacent tile content and confirmed that the overlap was sufficient for later manual alignment and overlap refinement. This helped produce usable stitched and integrated outputs even when automatic registration was imperfect.
- Next steps: Start debugging DNG capture so the scanner could produce RAW inputs for the negative processing pipeline.

[PLACEHOLDER: Insert screenshot/photo of manual alignment workflow with row and column offset calibration]

# 2026-04-24 - DNG Capture Debugging and Fixed Capture Settings

I focused on making the Pi capture consistent DNG and JPG tiles. This was important because the negative film pipeline depended on RAW data, while JPG previews were useful for fast visual debugging.

- Session goal: Capture reliable DNG + JPG tile pairs using fixed settings and include backlight calibration captures.
- Work completed: I tested RAW/JPG capture from the Pi camera and verified that tiles were named using `row_X_col_Y`. I adjusted the capture path so each tile included the DNG for processing and the JPG for preview/debugging. I also tested the separate backlight calibration capture upload path.
- Debugging observations: Auto exposure and auto white balance made tile-to-tile processing inconsistent. The same film area could shift brightness or color when the camera adapted between captures.
- Design decisions: I moved toward fixed capture settings for scan tiles so that flat-field correction and base-reference logic had stable inputs. Backlight captures were kept separate because they represent illumination correction data, not image content.
- Verification/test evidence: I confirmed that DNG tiles were captured at 4608 x 2592 px and uploaded to the PC. I also confirmed that JPG previews arrived with the corresponding row and column identifiers.
- Next steps: Try stitched RAW processing and determine whether the PC pipeline could process a synthetic full-frame DNG.

[PLACEHOLDER: Insert screenshot/photo of DNG and JPG tile files named row_X_col_Y]

# 2026-04-25 - Stitched RAW Compatibility Failure and Per-Tile DNG Pivot

This was the major software design pivot. I tested the idea of stitching the RAW tiles into a synthetic larger DNG and then passing that stitched RAW file through the existing processing path. It was not reliable enough to keep.

- Session goal: Determine whether a stitched RAW DNG could be used as the main processing input.
- Work completed: I generated stitched RAW attempts from tile captures and tested them against the flat-field and negative conversion code. I compared metadata, padding behavior, and downstream color behavior against single camera DNG files.
- Debugging observations: The stitched RAW DNG path failed in several ways. Padding and metadata did not behave like a real camera DNG, flat-field correction became harder to reason about, and color errors were amplified after stitching.
- Design decisions: I abandoned synthetic stitched RAW DNG processing. The new design processes each real camera DNG tile individually, then integrates the processed RGB tiles afterward. This keeps the RAW processing code closer to normal camera input and avoids invalid stitched-DNG metadata.
- Verification/test evidence: Single-tile DNG processing was more stable than the stitched RAW attempt. The failure evidence supported switching to per-tile DNG streaming before spending more time on synthetic DNG repair.
- Next steps: Update the PC server so uploaded tiles can be processed as they arrive and then integrated at the RGB stage.

[PLACEHOLDER: Insert screenshot/photo of stitched RAW DNG failure or metadata debugging output]

# 2026-04-26 - PC-Side Per-Tile Processing and Bootstrap Logic

I implemented the PC-side flow for processing tiles individually as they upload from the Pi. The important part was making early decisions once and then reusing them consistently across the scan.

- Session goal: Process per-tile DNG uploads on the PC and avoid inconsistent classification or base-reference choices across the tile grid.
- Work completed: I connected `/internal/jobs/{job_id}/receive_tile` to the per-tile processing path. The PC receives DNG + JPG tiles, stores them by row and column, and starts processing each real DNG tile instead of waiting for a synthetic stitched RAW image.
- Debugging observations: If every tile independently classified the film or selected a base reference, the result could vary across the scan because some tiles contained more film border, dense negative content, or low-detail regions.
- Design decisions: I used the first three uploaded tiles for a classification vote and for negative base-reference bootstrap. Later tiles reuse the global classification and global base reference so the scan stays visually consistent.
- Verification/test evidence: I tested early tile uploads and confirmed that the first three tiles could initialize the job-level classification/base-reference state, while later tiles reused those values.
- Next steps: Tune flat-field correction and preview behavior so the processed RGB tiles are easier to inspect during integration.

[PLACEHOLDER: Insert screenshot/photo of per-tile processing logs showing first three tile bootstrap]

# 2026-04-27 - Flat-Field Tuning and Preview Color Debugging

I spent this session tuning the image correction path and debugging why previews sometimes looked different from the final processed output. The focus was on negative film because that was the main final pipeline.

- Session goal: Improve RAW-first flat-field correction and make preview/color behavior easier to trust.
- Work completed: I tested backlight calibration inputs uploaded through `/internal/calibration/backlight/receive_tile` and compared processed negative tiles with and without the calibration data. I inspected color shifts after density inversion and adjusted the order of correction steps.
- Debugging observations: Applying correction after too much color transformation made the preview harder to interpret. The negative path behaved better when flat-field correction happened early on RAW-derived data before base-reference, density inversion, tone, and color refinement.
- Design decisions: I kept the negative pipeline as RAW-first flat-field correction, base reference, density inversion, tone/color refinement, and final output. JPG previews stayed useful for scanner/debug feedback, but the DNG remained the processing source.
- Verification/test evidence: I compared processed tile previews against the original JPG previews and backlight captures. The tuned path reduced illumination artifacts and produced more consistent tile color for integration.
- Next steps: Integrate the negative pipeline with the RGB tile integration path and leave positive-film color tuning clearly marked as future work.

[PLACEHOLDER: Insert screenshot/photo of flat-field tuning comparison before and after correction]

# 2026-04-28 - Negative Pipeline Integration and Positive Pipeline Status

I connected the per-tile DNG processing work to the final image integration path. This made the system closer to the intended full pipeline instead of a collection of separate processing tests.

- Session goal: Run negative-film tiles through the PC pipeline and integrate processed RGB tiles into a final output.
- Work completed: I integrated negative processing with the tile grid assembly. Processed RGB tiles were positioned by row and column, then refined with manual alignment and overlap adjustment when needed.
- Debugging observations: The negative branch was usable after the RAW-first correction and shared base-reference logic. The positive branch existed, but color tuning remained future work because the final project evidence was focused on the negative-film pipeline.
- Design decisions: I prioritized the negative pipeline for final verification and documented the positive pipeline honestly as implemented but not fully color tuned. This matched the project risk and the film samples available for the final test.
- Verification/test evidence: I produced integrated outputs from multi-tile scan data and checked that row/column placement matched the scanner configuration. Manual alignment and overlap refinement were still necessary to produce the most usable stitched result.
- Next steps: Run a full 4x4 scan through the Browser -> PC -> Pi -> capture/upload -> PC processing/integration flow.

[PLACEHOLDER: Insert screenshot/photo of negative pipeline integrated tile grid output]

# 2026-04-29 - Full Pipeline Test

I ran the final end-to-end pipeline test from the browser controls through the scanner and back into PC processing. This was the main proof that the software integration work functioned as a system.

- Session goal: Complete a full scan using the final architecture and verify that the data path worked end to end.
- Work completed: I started a scan from the browser, routed it through the PC FastAPI server on port 8000, commanded the Pi scanner server on port 5000, captured per-tile DNG + JPG files, uploaded tiles back to the PC, processed DNG tiles individually, and integrated the processed RGB results.
- Debugging observations: The long path made log ordering important. I had to check browser state, PC job state, Pi scanner state, capture output, upload requests, and processing output together to understand failures.
- Design decisions: I kept the final display/export target at 4K while retaining full-resolution 4608 x 2592 px DNG tile inputs for processing. This balanced final viewing requirements with the high-resolution capture data.
- Verification/test evidence: The full pipeline test completed end to end using 4x4 tile scans. The PC received tile uploads, initialized classification/base-reference logic from the first three tiles, reused that global state for later tiles, and generated a final integrated output.
- Next steps: Prepare figures and notebook documentation for the final report so the evidence is easy to review.

[PLACEHOLDER: Insert screenshot/photo of completed 4x4 full pipeline test and final 4K output]

# 2026-04-30 - Final Documentation and Verification Figure Preparation

I used the final sessions to prepare documentation and verification figures for the report. My goal was to make the software decisions understandable and connect the figures to actual test evidence.

- Session goal: Document the final software architecture, the major RAW processing pivot, and the verification evidence for the completed pipeline.
- Work completed: I prepared notes for the architecture diagram, PC/Pi API boundary, scanner control workflow, DNG upload flow, first-three-tile bootstrap logic, negative-film pipeline, and final 4x4 pipeline test.
- Debugging observations: The final report needed to explain not only what worked, but also why the stitched RAW DNG path was rejected. That pivot was one of the most important software integration lessons.
- Design decisions: I organized the documentation around the actual data path: browser command, PC job creation, Pi scan execution, DNG/JPG capture, PC tile receive endpoint, per-tile processing, and final RGB integration.
- Verification/test evidence: I selected figures showing scanner configuration, tile upload logs, DNG tile dimensions, flat-field correction, per-tile classification/bootstrap, and final integrated output.
- Next steps: Insert final screenshots/photos in the placeholders and make sure the final report text describes the positive pipeline status and negative pipeline verification accurately.

[PLACEHOLDER: Insert screenshot/photo of final report verification figures for scanner UI tile uploads and processed output]
