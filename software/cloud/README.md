# Cloud Pipeline

Python backend target for image post-processing.

## MVP flow
1. Receive raw image upload.
2. Detect film mode metadata.
3. Apply inversion + mask compensation.
4. Denoise/sharpen.
5. Return output URL.
