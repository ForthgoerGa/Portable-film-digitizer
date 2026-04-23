"""Reference layout parsing for future base/orange-mask sampling."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReferenceROI:
    """Normalized region-of-interest descriptor.

    ``bbox`` is ``[x0, y0, x1, y1]`` when a rectangular region can be derived.
    Unknown future formats keep the original payload and leave ``bbox`` as
    ``None`` so downstream code can add support without changing this interface.
    """

    name: str
    bbox: list[int] | None
    source_format: str
    raw_payload: Any


def load_reference_layout(path: str | Path | None) -> list[ReferenceROI]:
    if path is None:
        return []
    layout_path = Path(path)
    if not layout_path.exists():
        return []
    payload = json.loads(layout_path.read_text(encoding="utf-8"))
    return parse_reference_layout(payload)


def parse_reference_layout(payload: Any) -> list[ReferenceROI]:
    if isinstance(payload, list):
        return [_parse_list_region(item, index) for index, item in enumerate(payload)]
    if isinstance(payload, dict):
        return _parse_dict_layout(payload)
    return [ReferenceROI("unknown", None, "unknown", payload)]


def rois_to_dicts(rois: list[ReferenceROI]) -> list[dict[str, Any]]:
    return [asdict(roi) for roi in rois]


def _parse_dict_layout(payload: dict[str, Any]) -> list[ReferenceROI]:
    rois: list[ReferenceROI] = []
    y_range = _two_ints(payload.get("y"))

    for name, value in payload.items():
        if name == "y":
            rois.append(
                ReferenceROI(
                    name=name,
                    bbox=None,
                    source_format="axis_range",
                    raw_payload=value,
                )
            )
            continue

        bbox = _bbox_from_value(value, y_range)
        source_format = "range_with_shared_y" if bbox is not None and y_range else "dict_value"
        if isinstance(value, dict) and "bbox" in value:
            source_format = "bbox"
        rois.append(
            ReferenceROI(
                name=str(name),
                bbox=bbox,
                source_format=source_format,
                raw_payload=value,
            )
        )
    return rois


def _parse_list_region(item: Any, index: int) -> ReferenceROI:
    if isinstance(item, dict):
        name = str(item.get("name", f"region_{index}"))
        bbox = _bbox_from_value(item.get("bbox"), None)
        return ReferenceROI(name, bbox, "region_list", item)
    return ReferenceROI(f"region_{index}", None, "region_list_unknown", item)


def _bbox_from_value(value: Any, y_range: list[int] | None) -> list[int] | None:
    if isinstance(value, dict):
        return _bbox_from_value(value.get("bbox"), y_range)
    if isinstance(value, (list, tuple)) and len(value) == 4:
        ints = _int_list(value)
        if ints is not None:
            return ints
    x_range = _two_ints(value)
    if x_range is not None and y_range is not None:
        return [x_range[0], y_range[0], x_range[1], y_range[1]]
    return None


def _two_ints(value: Any) -> list[int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        ints = _int_list(value)
        if ints is not None:
            return ints
    return None


def _int_list(value: Any) -> list[int] | None:
    try:
        return [int(v) for v in value]
    except Exception:
        return None

