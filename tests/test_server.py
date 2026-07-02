"""Tests for MCP tool registration and error surfacing in youtube_transcript_mcp.server."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from youtube_transcript_mcp import server
from youtube_transcript_mcp.errors import InvalidURLError


def test_all_tools_are_registered():
    tools = server.mcp._tool_manager.list_tools()
    names = {t.name for t in tools}
    assert names == {"get_transcript", "list_available_transcripts", "get_video_metadata"}


def test_get_transcript_calls_youtube_module(monkeypatch):
    monkeypatch.setattr(
        server.youtube, "get_transcript_text", lambda url, languages, include_timestamps: "hi"
    )
    assert server.get_transcript("some-url") == "hi"


def test_get_transcript_raises_clean_error_not_traceback(monkeypatch):
    def fake_get_transcript_text(url, languages, include_timestamps):
        raise InvalidURLError(f"Could not parse a YouTube video ID from: {url!r}")

    monkeypatch.setattr(server.youtube, "get_transcript_text", fake_get_transcript_text)

    with pytest.raises(InvalidURLError) as exc_info:
        server.get_transcript("not-a-url")
    assert "Could not parse" in str(exc_info.value)
    assert "Traceback" not in str(exc_info.value)


def test_list_available_transcripts_shapes_output(monkeypatch):
    fake_track = MagicMock(
        language="English", language_code="en", is_generated=False, is_translatable=True
    )
    monkeypatch.setattr(
        server.youtube, "list_available_transcripts", lambda url: [fake_track]
    )
    result = server.list_available_transcripts("some-url")
    assert result == [
        {
            "language": "English",
            "language_code": "en",
            "is_generated": False,
            "is_translatable": True,
        }
    ]


def test_get_video_metadata_shapes_output(monkeypatch):
    fake_meta = MagicMock(
        title="Title", channel="Channel", duration_seconds=120, upload_date="2020-01-01"
    )
    monkeypatch.setattr(server.youtube, "get_video_metadata", lambda url: fake_meta)
    result = server.get_video_metadata("some-url")
    assert result == {
        "title": "Title",
        "channel": "Channel",
        "duration_seconds": 120,
        "upload_date": "2020-01-01",
    }
