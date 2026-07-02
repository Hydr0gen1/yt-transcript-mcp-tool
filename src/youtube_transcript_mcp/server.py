"""MCP server exposing YouTube transcript tools via FastMCP."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import youtube

mcp = FastMCP("youtube-transcript")


@mcp.tool()
def get_transcript(
    url: str,
    languages: list[str] | None = None,
    include_timestamps: bool = True,
) -> str:
    """Fetch a YouTube video's transcript as plain text.

    Args:
        url: Full YouTube URL (watch, youtu.be, embed, or shorts) or a bare
            11-character video ID.
        languages: Preferred language codes in priority order, e.g.
            ["en", "es"]. Defaults to the YTT_DEFAULT_LANGUAGES env var,
            falling back to ["en"].
        include_timestamps: If true, prefix each line with a [MM:SS] timestamp.

    Returns:
        The transcript text, one line per caption segment.
    """
    return youtube.get_transcript_text(
        url, languages=languages, include_timestamps=include_timestamps
    )


@mcp.tool()
def list_available_transcripts(url: str) -> list[dict]:
    """List available transcript tracks for a YouTube video.

    Args:
        url: Full YouTube URL or a bare 11-character video ID.

    Returns:
        A list of transcript tracks, each with language, language_code,
        is_generated (auto vs. manual), and is_translatable.
    """
    transcripts = youtube.list_available_transcripts(url)
    return [
        {
            "language": t.language,
            "language_code": t.language_code,
            "is_generated": t.is_generated,
            "is_translatable": t.is_translatable,
        }
        for t in transcripts
    ]


@mcp.tool()
def get_video_metadata(url: str) -> dict:
    """Get title, channel, duration, and upload date for a YouTube video (no download).

    Args:
        url: Full YouTube URL or a bare 11-character video ID.

    Returns:
        A dict with title, channel, duration_seconds, and upload_date (YYYY-MM-DD).
    """
    meta = youtube.get_video_metadata(url)
    return {
        "title": meta.title,
        "channel": meta.channel,
        "duration_seconds": meta.duration_seconds,
        "upload_date": meta.upload_date,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
