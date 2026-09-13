import re
from dataclasses import asdict, dataclass


MAX_REQUEST_CHARS = 2_000

TRAVEL_TERMS = {
    "airport",
    "book",
    "budget",
    "destination",
    "day",
    "days",
    "flight",
    "holiday",
    "hotel",
    "itinerary",
    "journey",
    "sightseeing",
    "tour",
    "train",
    "travel",
    "trip",
    "vacation",
    "visa",
    "weekend",
}

INJECTION_PATTERNS = (
    r"\bignore\s+(all|any|the|your)?\s*(previous|prior|system)\s+instructions?\b",
    r"\b(reveal|show|print|return)\s+(the\s+)?(system prompt|api key|secret|environment variables?)\b",
    r"\b(read|open|dump)\s+[^.\n]*(\.env|credentials?|secrets?)\b",
    r"\b(run|execute)\s+(a\s+)?(shell|terminal|powershell|command)\b",
)


@dataclass(frozen=True)
class GuardrailResult:
    allowed: bool
    code: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def validate_travel_request(value: str) -> GuardrailResult:
    """Apply inexpensive, deterministic checks before APIs or models are called."""
    query = " ".join((value or "").split())
    if not query:
        return GuardrailResult(False, "empty_request", "Enter a travel request.")

    if len(query) > MAX_REQUEST_CHARS:
        return GuardrailResult(
            False,
            "request_too_long",
            f"Keep the request under {MAX_REQUEST_CHARS:,} characters.",
        )

    lowered = query.casefold()
    if any(re.search(pattern, lowered) for pattern in INJECTION_PATTERNS):
        return GuardrailResult(
            False,
            "unsafe_instruction",
            "The request contains instructions unrelated to travel planning.",
        )

    words = set(re.findall(r"[a-z]+", lowered))
    if not words.intersection(TRAVEL_TERMS):
        return GuardrailResult(
            False,
            "out_of_scope",
            "TripMate only accepts travel-planning requests.",
        )

    return GuardrailResult(True, "allowed", "Request accepted.")
