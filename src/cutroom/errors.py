"""Errors that carry a suggested fix, so failures are loud and actionable."""

from __future__ import annotations


class CutroomError(Exception):
    """An expected failure with a human-readable fix.

    Every error the pipeline raises on purpose should say what went wrong and
    what to do about it. The CLI prints both and exits with ``exit_code``.
    """

    exit_code = 1

    def __init__(self, message: str, fix: str | None = None, *, details: str | None = None):
        super().__init__(message)
        self.message = message
        self.fix = fix
        self.details = details

    def to_dict(self) -> dict:
        return {
            "error": type(self).__name__,
            "message": self.message,
            "fix": self.fix,
            "details": self.details,
        }


class ToolMissingError(CutroomError):
    """A required external tool (FFmpeg, Node, a model) is not available."""

    exit_code = 3


class MediaError(CutroomError):
    """Input media is unreadable, unsupported or inconsistent."""

    exit_code = 4


class JobError(CutroomError):
    """A job workspace is missing, malformed or used incorrectly."""

    exit_code = 5


class RefusedError(CutroomError):
    """An operation was refused because it is destructive or invalid."""

    exit_code = 6
