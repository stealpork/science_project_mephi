from typing import Any


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def require(condition: object, code: str, message: str, status: int = 409) -> None:
    if not condition:
        raise APIError(status, code, message)
