"""Errors FastAPI maps to HTTP responses (docs/api_spec.md 4). Each carries the spec's error code."""
from datetime import datetime


class RmaAgentError(Exception):
    code = "INTERNAL_ERROR"
    http_status = 500

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class InvalidInput(RmaAgentError):
    code, http_status = "INVALID_INPUT", 400


class ClaimNotFound(RmaAgentError):
    code, http_status = "CLAIM_NOT_FOUND", 404


class AsOfInvalid(RmaAgentError):
    code, http_status = "AS_OF_INVALID", 422


class ServiceUnavailable(RmaAgentError):
    code, http_status = "SERVICE_UNAVAILABLE", 503


def check_as_of(as_of):
    """as_of must be ISO-8601 with a time zone, e.g. 2025-11-20T14:40:46+09:00. Returns it normalised."""
    if as_of is None:
        return None
    try:
        parsed = datetime.fromisoformat(as_of)
    except (TypeError, ValueError):
        raise AsOfInvalid(f"as_of 형식 오류: {as_of!r} (예: 2025-11-20T14:40:46+09:00)")
    if parsed.tzinfo is None:
        raise AsOfInvalid(f"as_of에 시간대가 없습니다: {as_of!r} (예: +09:00)")
    return parsed.isoformat(timespec="seconds")
