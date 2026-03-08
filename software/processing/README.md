# Image Processing Demo Pipeline

This demo script stitches overlapping negative-film tiles, then restores film color with:

1. `negative -> positive` color inversion
2. percentile-clipped white balance (`SimpleWB`)
3. percentile auto-level stretch
4. luminance CLAHE contrast enhancement
5. selective cast-suppression mask + mild desaturation
6. light unsharp detail recovery

## Run on provided sample

From repo root:

```powershell
python software/processing/negative_pipeline.py
```

Outputs are written to:

`Image_integration&Post_Processing/output_demo`

Files:
- `stitched_raw.png`
- `inverted_positive.png`
- `white_balanced.png`
- `contrast_restored.png`
- `desaturation_mask.png`
- `inverted_desaturated.png`
- `restored_color.png` (same as `inverted_desaturated.png`)

## Optional arguments

```powershell
python software/processing/negative_pipeline.py `
  --tiles-dir "Image_integration&Post_Processing/Negative_example/norway_split_40_overlap" `
  --output-dir "Image_integration&Post_Processing/output_demo" `
  --wb-clip-percent 0.5 `
  --desat-strength 0.20 `
  --desat-sigma 7 `
  --black-point 0.8 `
  --white-point 99.2 `
  --clahe-clip 1.4 `
  --unsharp-amount 0.35
```

Use `--overlap-x` and `--overlap-y` if your dataset has no metadata/manifest overlap fields.

Notes:
- `white_balanced.png` is an intermediate output before full contrast/detail restoration.
- If output is still too bright, increase `--wb-clip-percent` (for example `0.8` to `1.2`).
