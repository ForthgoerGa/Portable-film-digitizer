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
