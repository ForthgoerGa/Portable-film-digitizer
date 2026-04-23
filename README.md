# Portable Multi-Format Film Digitization System

ECE 445 coursework design project repository for hardware/firmware/software development and lab notebook tracking.

## System Block Diagram
This block diagram shows the general system design and how the power subsystem, control unit, and imaging subsystem are connected.

<img width="2816" height="1504" alt="Gemini_Generated_Image_8ml5k58ml5k58ml5" src="https://github.com/user-attachments/assets/f618c00b-75cb-43d7-91d4-eb39339cfe66" />

## Project Goal
Build a portable, battery-powered film digitizer that supports:
- 35mm negatives/slides (transmissive)
- 120 medium format film (transmissive)
- Instax Mini prints (reflective)

The system captures a raw frame, uploads via Wi-Fi, performs cloud processing (negative inversion + enhancement), and returns a social-media-ready image.

## Problem
Current film digitization workflows are either:
- expensive and slow (professional scanners),
- fast but complex and non-portable (DSLR scanning rigs),
- or outsourced with long turnaround times (lab services).

This project targets a one-click workflow with fast turnaround and practical portability.

## Solution Overview
An STM32-based control system coordinates:
- dual-mode illumination (backlight for transmissive film, angled front light for reflective media),
- high-resolution sensor capture,
- network upload to cloud backend,
- return of processed output to a mobile device.

## Major Subsystems
1. **Power Supply and Management (PCB)**  
   Li-Ion battery pack, charging/protection (BMS), regulated rails for logic and LEDs.
2. **MCU and Control (STM32F4/H7)**  
   UI handling, capture sequencing, sensor interface, and network orchestration.
3. **Dual-Mode Illumination**  
   High-CRI transmissive array + glare-controlled reflective array, PWM-controlled.
4. **Cloud Processing Pipeline (Python)**  
   Negative inversion (orange mask handling), denoising, sharpening, and API response.

## Optical Stack (Current Plan)
- Sensor: Raspberry Pi HQ Camera (IMX477)
- Lens: 25mm f/1.4 C-mount lens
- Extension: 10mm C-mount tube for macro working distance (~8.5 cm)

## Success Criteria
- Resolution: at least `3840 x 2160` output.
- Speed: under `15 seconds` from capture press to phone delivery (stable network).
- Versatility: works with 35mm, 120, and Instax Mini.
- Portability: under `30 cm x 20 cm x 15 cm`, under `1.5 kg`.
- Battery: at least `360 shots` (~10 rolls) per charge.

## Repository Layout
```text
hardware/              PCB + mechanical design files
firmware/              STM32 firmware and drivers
software/              Cloud/image-processing code and APIs
docs/                  Specs, architecture, plans, reports
lab-notebook/          Dated engineering logs
```

## Lab Notebook Workflow
1. Add one file per workday in `lab-notebook/` using `YYYY-MM-DD.md`.
2. Record decisions, measurements, failures, and next steps.
3. Include links to commits, issues, and test data where possible.
4. Use `lab-notebook/TEMPLATE.md` for consistency.

## Quick Start (Local)
```powershell
git init -b main
git add .
git commit -m "Initial project scaffold"
```

## Running the Software

### Web Inspection Server (PC)
Runs the FastAPI server with the web UI for scan control and image review:
```bash
cd software && python server.py
# FastAPI at http://localhost:8000, web UI at http://localhost:8000/web/
```

### Raspberry Pi Camera Server
Streams a live MJPEG preview, triggers captures, and forwards to the PC pipeline:
```bash
# On the Raspberry Pi:
python3 software/rpi_camera_server.py [--port 8080] [--button-pin 17] [--pc-host <PC_IP>]
# Web UI at http://<pi-ip>:8080/
```

### Agentic Post-Processing Pipeline
Runs the Gemini-powered agent loop (classify → process → evaluate → refine):
```bash
cd Agentic_Post_Processing
python orchestrator.py --input-dir data --output-dir output --max-iter 2
```
Requires a `.env` file with `GEMINI_API_KEY` (see `.env.example`). Falls back to heuristics automatically if the key is absent.

### Raw Negative Pipeline (direct)
```bash
python software/processing/negative_pipeline.py \
  --tiles-dir "Image_integration&Post_Processing/Negative_example/norway_split_40_overlap" \
  --output-dir "Image_integration&Post_Processing/output_demo" \
  --wb-clip-percent 0.5
```

## GitHub Setup
See `docs/github-setup.md` for:
- creating the remote repo,
- pushing `main`,
- inviting collaborator `Allannn-sudo`.
