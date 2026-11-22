"""Gateway errors carry a stable code so callers, audit records and alerts can rely on them."""

from __future__ import annotations

ERROR_CODES = frozenset(
    {
        "UNAUTHENTICATED",
        "AGENT_NOT_FOUND",
        "AGENT_NOT_APPROVED",
        "AGENT_EXPIRED",
        "CLIENT_NOT_PERMITTED",
        "VERSION_MISMATCH",
        "PAYLOAD_TOO_LARGE",
        "INVALID_REQUEST",
        "RATE_LIMITED",
        "TOOL_DENIED",
        "INVALID_TOOL_INPUT",
        "INVALID_TOOL_OUTPUT",
        "TOOL_TIMEOUT",
        "INVALID_AGENT_OUTPUT",
        "INTERNAL_ERROR",
    }
)


class GatewayError(Exception):
    def __init__(self, code: str, message: str) -> None:
        assert code in ERROR_CODES, code
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}
