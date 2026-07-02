"""MCP server exposing YouTube transcript tools via FastMCP.

Two transports are exposed from this one module:

- stdio, via `main()` / the `youtube-transcript-mcp` console script, for local
  Claude Desktop/Code usage (`uv run youtube-transcript-mcp`).
- SSE, via the module-level `app` ASGI object, for remote deployment (e.g.
  Render) served by an external ASGI server such as uvicorn
  (`uvicorn youtube_transcript_mcp.server:app`).

Tool definitions and business logic are transport-agnostic and unchanged
between the two.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import youtube

# The MCP SDK only auto-enables its Host-header allowlist (DNS-rebinding
# protection) when FastMCP's `host` is left at its localhost default, and in
# that case the allowlist only contains localhost/127.0.0.1/::1. A remote
# deployment (Render, etc.) is reached with a public Host header, so with the
# SDK default every SSE request gets rejected with 421 before the MCP
# handshake -- `/health` still passes since it bypasses this SSE-specific
# check entirely. We keep the protection on but extend the allowlist to the
# hostname(s) this service is actually reachable at.
_LOCALHOST_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_LOCALHOST_ALLOWED_ORIGINS = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
]


def _build_transport_security() -> TransportSecuritySettings:
    extra_hosts = [
        host.strip()
        for host in os.environ.get("YTT_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    ]
    # Render sets this automatically for every web service (e.g.
    # "my-app.onrender.com"); YTT_ALLOWED_HOSTS covers custom domains or
    # other hosts on top of that.
    render_hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if render_hostname:
        extra_hosts.append(render_hostname)

    allowed_hosts = list(_LOCALHOST_ALLOWED_HOSTS)
    allowed_origins = list(_LOCALHOST_ALLOWED_ORIGINS)
    for host in extra_hosts:
        allowed_hosts.append(host)
        allowed_hosts.append(f"{host}:*")
        allowed_origins.append(f"https://{host}")
        allowed_origins.append(f"http://{host}")

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


mcp = FastMCP("youtube-transcript", transport_security=_build_transport_security())


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


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> Response:
    """Liveness probe for Render's health check and cold-start wake-ups.

    Deliberately does no YouTube/yt-dlp work -- it must return fast so Render
    (and anything polling to warm up a spun-down free-tier instance) gets a
    quick, cheap signal that the process is up.
    """
    return JSONResponse({"status": "ok"})


# ASGI app for the SSE transport. Served remotely via
# `uvicorn youtube_transcript_mcp.server:app` (see Dockerfile). Routes:
#   GET  /sse        - SSE connection endpoint MCP clients connect to
#   POST /messages/   - message-send endpoint used by the SSE transport
#   GET  /health      - plain liveness check, not part of the MCP protocol
app = mcp.sse_app()


def main() -> None:
    """Entry point for local stdio usage (Claude Desktop/Code `uv run youtube-transcript-mcp`)."""
    mcp.run()


if __name__ == "__main__":
    main()
