
from pathlib import Path
import json
from integrator import integrate_captures
root = Path('captures')
print('Before integrate:')
for name in ('stitched_raw.dng', 'stitched_preview.jpg', 'capture_manifest.json'):
    p = root / name
    print(name, p.exists(), p.stat().st_size if p.exists() else 0)
print('DNG tiles', len(list(root.glob('row_*.dng'))))
print('JPG tiles', len(list(root.glob('row_*.jpg'))))
result = integrate_captures(root)
print('result_raw', result.get('stitched_raw_path'))
print('result_preview', result.get('stitched_preview_path'))
manifest = json.loads((root / 'capture_manifest.json').read_text())
print('raw_mode', manifest.get('raw_integration_mode'))
print('pending', manifest.get('true_raw_mosaic_pending'))
print('placement', json.dumps(manifest.get('placement'), sort_keys=True))
for name in ('stitched_raw.dng', 'stitched_preview.jpg', 'capture_manifest.json'):
    p = root / name
    print(name, p.exists(), p.stat().st_size if p.exists() else 0)
