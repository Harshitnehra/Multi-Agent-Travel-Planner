"""Formatting helpers for safe, readable itinerary output."""

from __future__ import annotations

import json
import re
from typing import Any


READABLE_KEYS = ("rendered_itinerary", "itinerary", "answer")


def _find_readable_value(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in READABLE_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        for candidate in value.values():
            found = _find_readable_value(candidate)
            if found:
                return found
    elif isinstance(value, list):
        for candidate in value:
            found = _find_readable_value(candidate)
            if found:
                return found
    return None


def extract_embedded_itinerary(value: str) -> str | None:
    """Extract readable prose from a JSON/tool wrapper, even if its outer JSON is damaged."""
    text = str(value or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    candidate = fenced.group(1) if fenced else text

    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    found = _find_readable_value(parsed)
    if found:
        return found

    # Older saved plans may contain a valid JSON string inside an outer object
    # that became invalid after null values were humanized. Decode just that field.
    decoder = json.JSONDecoder()
    for key in READABLE_KEYS:
        match = re.search(rf'"{key}"\s*:\s*', candidate)
        if not match:
            continue
        try:
            decoded, _ = decoder.raw_decode(candidate[match.end() :])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(decoded, str) and decoded.strip():
            return decoded
    return None


def strip_structured_plan_section(markdown: str) -> str:
    """Return human-facing Markdown without schema dumps or internal labels."""
    original = str(markdown or "")
    embedded = extract_embedded_itinerary(original)
    if embedded:
        original = embedded
    elif re.match(r'^\s*[\[{]', original) and re.search(
        r'"(?:trip_plan|structured_plan|flights|hotels)"\s*:', original
    ):
        return "# Travel plan\n\nA readable itinerary is not available yet."

    marker = re.search(
        r"(?im)^\s{0,3}(?:#{1,6}\s*)?(?:\*{1,2}|_{1,2})?\s*"
        r"structured\s+trip\s+plan\b.*$",
        original,
    )
    readable = original[: marker.start()] if marker else original
    readable = re.sub(r"(?is)```\s*json\s*.*?```", "", readable)
    readable = re.sub(r"(?im)\bsource_verified\b", "Source checked", readable)
    readable = re.sub(r"(?im)\bnull\b", "To be confirmed", readable)
    readable = re.sub(r"(?i)\|\s*true\s*\|", "| Yes |", readable)
    readable = re.sub(r"(?i)\|\s*false\s*\|", "| No |", readable)
    readable = re.sub(
        r"(?im)^[ \t]*(?:#{1,6}[ \t]*)?(?:\*{1,2})?(?:(?:10|[1-9])[.)][ \t]*)?"
        r"(trip summary|flights|hotels|day[-\u2010-\u2015]by[-\u2010-\u2015]day[ \t]+itinerary|"
        r"best places to visit|activities(?:[ \t]+and|[ \t]*&)[ \t]+experiences|"
        r"best time to visit|local food|budget(?:[ \t]+and|[ \t]*&)[ \t]+total cost|budget|"
        r"recommendations(?:[ \t]+and|[ \t]*&)[ \t]+practical notes|practical notes)"
        r"(?:\*{1,2})?[ \t]*:?[ \t]*$",
        lambda match: f"## {match.group(1).translate(str.maketrans('‐‑‒–—', '-----')).title()}",
        readable,
    )
    readable = re.sub(r"(?m)^[ \t]*[•·][ \t]+", "- ", readable)
    readable = re.sub(
        r"(?im)^[ \t]*(?:\*{1,2})?(day[ \t]+\d+\b[^\n]*?)(?:\*{1,2})?[ \t]*$",
        lambda match: f"### {match.group(1)}",
        readable,
    )
    readable = re.sub(r"\n\s*(?:---|\*\*\*|___)\s*$", "", readable).rstrip()
    return readable or "# Travel plan\n\nA readable itinerary is not available yet."
