import json
import re
from typing import Any


def extract_text(response: dict[str, Any]) -> str:
    # Gemini wraps generated text inside candidate content parts.
    candidates = response.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned no candidates")

    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    text_parts = [part.get("text", "") for part in parts if part.get("text")]
    content = "".join(text_parts).strip()
    if not content:
        raise RuntimeError("Gemini returned empty content")
    return content


def parse_json_object(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        # Be tolerant of providers that wrap JSON with extra text.
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))

    if not isinstance(parsed, dict):
        raise ValueError("Gemini response must be a JSON object")
    return parsed
