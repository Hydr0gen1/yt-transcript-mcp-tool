"""Custom exceptions for the YouTube transcript MCP server.

Tool functions catch library-specific exceptions (from youtube_transcript_api
and yt-dlp) and re-raise as one of these, so the MCP client only ever sees a
clean, user-readable message instead of a raw traceback.
"""

from __future__ import annotations


class YoutubeTranscriptMCPError(Exception):
    """Base class for all errors raised by this server."""


class InvalidURLError(YoutubeTranscriptMCPError):
    """Raised when a video ID could not be parsed from the given input."""


class TranscriptUnavailableError(YoutubeTranscriptMCPError):
    """Raised when captions are disabled or no transcript exists for a video."""


class LanguageNotFoundError(YoutubeTranscriptMCPError):
    """Raised when none of the requested languages are available.

    The message includes the list of languages that *are* available so the
    caller can retry with a valid code.
    """

    def __init__(self, requested: list[str], available: list[str]):
        self.requested = requested
        self.available = available
        available_str = ", ".join(available) if available else "none"
        super().__init__(
            f"None of the requested languages {requested} are available. "
            f"Available languages: {available_str}."
        )


class VideoUnavailableError(YoutubeTranscriptMCPError):
    """Raised when a video is private, deleted, region-locked, or otherwise unplayable."""
