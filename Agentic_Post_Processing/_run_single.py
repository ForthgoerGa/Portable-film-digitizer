#!/usr/bin/env python3
"""Subprocess entry point: process one image through the agentic pipeline.
Usage: python _run_single.py <source_image_path> <output_dir>
Emits PROGRESS:<json> lines to stderr after each iteration.
Prints final JSON result to stdout."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from orchestrator import process_image


def _progress(data: dict) -> None:
    print(f"PROGRESS:{json.dumps(data)}", file=sys.stderr, flush=True)


source = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
result = process_image(source, output_dir, progress_callback=_progress)
print(json.dumps(result))
