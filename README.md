# yt-transcript-mcp-tool

A local MCP (Model Context Protocol) server that fetches YouTube video
transcripts and metadata, so Claude (Desktop or Code) can pull captions
directly into context.

## Tools

### `get_transcript`

Fetch a video's transcript as plain text.

| Input | Type | Default | Description |
|---|---|---|---|
| `url` | string | required | Full YouTube URL or bare 11-char video ID |
| `languages` | list[string] | `["en"]` (or `YTT_DEFAULT_LANGUAGES`) | Preferred language codes, in priority order |
| `include_timestamps` | bool | `true` | Prefix each line with `[MM:SS]` |

Supported URL formats: `youtube.com/watch?v=...`, `youtu.be/...`,
`youtube.com/embed/...`, `youtube.com/shorts/...`, and bare video IDs.

Tries `youtube-transcript-api` first. If captions are disabled or the
requested language isn't found there, it falls back to `yt-dlp`'s
auto-caption download before giving up with a clean error message.

### `list_available_transcripts`

Lists the transcript tracks available for a video (language, language code,
whether it's auto-generated vs. manually created, and whether it's
translatable) without fetching the full text.

### `get_video_metadata`

Returns title, channel, duration, and upload date for a video via `yt-dlp`
(no download).

## Setup

Requires Python 3.10+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
```

Run the server directly to sanity-check it starts:

```bash
uv run youtube-transcript-mcp
```

## Configuration

Set these as environment variables (or in a `.env` file in the project root):

| Variable | Default | Description |
|---|---|---|
| `YTT_DEFAULT_LANGUAGES` | `en` | Comma-separated default language codes, e.g. `en,es,fr` |
| `YTT_PROXY_URL` | unset | Proxy URL used for both transcript fetches and `yt-dlp` calls |
| `YTT_TIMEOUT_SECONDS` | `15` | Timeout (seconds) per fetch |

### About `YTT_PROXY_URL`

`youtube-transcript-api` calls YouTube's internal endpoints directly from
your machine's IP, and cloud hosts (CI runners, VPS, etc.) are frequently
rate-limited or blocked by YouTube as a result. If you see errors mentioning
IP blocking or rate limiting, set `YTT_PROXY_URL` to an HTTP/HTTPS proxy
(e.g. a residential or datacenter proxy), for example:

```bash
export YTT_PROXY_URL="http://user:pass@proxy.example.com:8080"
```

## Registering with Claude

Add to Claude Desktop's `claude_desktop_config.json` or Claude Code's MCP
config:

```json
{
  "mcpServers": {
    "youtube-transcript": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/yt-transcript-mcp-tool", "run", "youtube-transcript-mcp"]
    }
  }
}
```

## Example tool calls

```
get_transcript(url="https://youtu.be/dQw4w9WgXcQ")
get_transcript(url="dQw4w9WgXcQ", languages=["es", "en"], include_timestamps=false)
list_available_transcripts(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
get_video_metadata(url="https://www.youtube.com/shorts/dQw4w9WgXcQ")
```

## Testing

```bash
uv run pytest                       # unit tests only
uv run pytest -m integration        # also run the integration test (hits real YouTube)
```

## Out of scope (v1)

- Transcribing audio (e.g. via Whisper) when no captions exist at all
- Playlist/channel-wide batch transcript fetching
- Transcript translation beyond YouTube's native translatable tracks
