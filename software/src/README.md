# Film Digitizer - New Architecture

This directory contains the refactored scanner architecture with proper separation of concerns:

## Architecture Overview

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   REST API      │    │   Coordinator    │    │   Scanner       │
│   (main.py)     │────│   (State Machine)│────│   (Motors)      │
│                 │    │   (coordinator.py)│    │   (scanner.py)  │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │
         ▼
┌─────────────────┐
│   Web UI        │
│   (web/)        │
│   index.html    │
│   app.js        │
│   style.css     │
└─────────────────┘
```

## Components

- **`scanner.py`**: Low-level motor control and scanning APIs
  - `StepperMotor`: Individual motor control with GPIO
  - `Scanner`: High-level scanning interface (move_x, move_y, capture)

- **`coordinator.py`**: State machine and scan lifecycle management
  - `ScannerCoordinator`: Manages scan state and progress
  - Background thread execution for non-blocking operations

- **`main.py`**: FastAPI REST API server
  - `/scan/*` endpoints for scan operations
  - `/motor/*` endpoints for manual motor control
  - Serves web UI from `src/web/`

- **`config.py`**: Configuration constants (GPIO pins, timing, etc.)

- **`web/`**: Web UI files
  - `index.html`: Full control panel UI
  - `app.js`: REST API interactions and status polling
  - `style.css`: Modern, responsive styling

## API Endpoints

### Scan Operations
- `POST /scan/start` - Start scanning operation
- `POST /scan/cancel` - Cancel current scan
- `GET /scan/status` - Get scan progress and state

### Motor Control
- `POST /motor/move` - Move motors by steps
- `POST /motor/home` - Return to origin

## Web UI Features

The web interface provides:

**Scan Control:**
- Film format selection (35mm, 120, 4x5)
- Start/Cancel scan buttons
- Real-time progress bar and percentage
- Current scan position (row, column)
- Status indicators (idle, scanning, error)

**Motor Control:**
- Manual X/Y axis movement
- Configurable step sizes
- Return-to-home functionality
- Status feedback for all operations

## Usage

Run the server:
```bash
cd src/
python main.py
```

The web UI will be available at `http://localhost:5000/`

## Development

For testing without hardware, the code uses `Mock.GPIO` when `RPi.GPIO` is unavailable.

Run tests:
```bash
cd src/
python test.py
```