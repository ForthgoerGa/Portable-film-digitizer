from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib import request

import cv2
import numpy as np

import config


class GatewayModelAdapter:
    def __init__(self, model_name: str, group: str = "") -> None:
        if not config.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not configured")
        if not config.OPENAI_BASE_URL:
            raise ValueError("OPENAI_BASE_URL is not configured")
        self.model_name = model_name
        self.group = group or config.MODEL_GROUP
        self.base_url = config.OPENAI_BASE_URL.rstrip("/")

    def generate_content(self, contents: list[Any]) -> Any:
        prompt_parts: list[dict[str, Any]] = []
        for item in contents:
            if isinstance(item, str):
                prompt_parts.append({"type": "text", "text": item})
            else:
                image_url = _to_data_url(item)
                prompt_parts.append({"type": "image_url", "image_url": {"url": image_url}})

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt_parts}],
            "temperature": 0.1,
            "max_tokens": 768,
        }
        if self.group:
            payload["group"] = self.group

        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                "User-Agent": "Agentic-Post-Processing/1.0",
            },
        )
        with request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8", "ignore"))
        text = body["choices"][0]["message"].get("content", "")
        return _SimpleResponse(text=text)


class _SimpleResponse:
    def __init__(self, text: str) -> None:
        self.text = text


def _to_data_url(item: Any) -> str:
    if isinstance(item, Path):
        mime = _mime_from_suffix(item.suffix.lower())
        data = item.read_bytes()
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    if isinstance(item, np.ndarray):
        ok, encoded = cv2.imencode('.jpg', item)
        if not ok:
            raise ValueError('Could not encode ndarray image')
        data = encoded.tobytes()
        return f"data:image/jpeg;base64,{base64.b64encode(data).decode('ascii')}"

    raise TypeError(f"Unsupported image content type: {type(item)!r}")


def _mime_from_suffix(suffix: str) -> str:
    return {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.webp': 'image/webp',
        '.tif': 'image/tiff',
        '.tiff': 'image/tiff',
    }.get(suffix, 'image/jpeg')
