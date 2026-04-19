# Software

Cloud and processing software.

## Responsibilities
- Receive uploaded raw captures
- Invert color negatives and remove orange mask
- Denoise/sharpen and apply color look
- Return processed image URL/API response

## Suggested structure
- `cloud/`
- `api/`
- `processing/`
- `tests/`

## Current demo
- `processing/negative_pipeline.py`: stitches overlapping film tiles, then restores color with white balance, contrast/tone recovery, and selective cast suppression.
- `server.py` + `web/`: FastAPI + web UI flow where `Start Scan` processes sample fractions, shows tile grid, stitched raw image, and white-balanced output.

## Hardware Communication Protocol

The software communicates with the embedded STM32 controller via UART (USART1, PB6/PB7) at 115200 baud. Commands are ASCII-based and fire-and-forget (no responses expected).

### Single-Byte Commands
- `w` or `d`: Start jogging forward (ramped acceleration)
- `s` or `a`: Start jogging reverse (ramped acceleration)
- `q`: Stop all movement immediately

### API Commands
Format: `$<command>;<CR>` (newline-terminated)

- `$MX:<steps>;`: Move X-axis by `<steps>` (signed integer; positive = forward, negative = reverse)
- `$MY:<steps>;`: Move Y-axis by `<steps>` (signed integer; for future embedded support)

### Notes
- Ramping: Starts at 800µs delay, ramps to 100µs minimum over steps.
- Error Handling: UART errors are handled on the embedded side; Python assumes success.
- Extensions: Illumination and other features can be added as the embedded code evolves.
