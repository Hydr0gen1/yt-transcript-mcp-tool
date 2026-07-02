# yt-transcript-mcp-tool

An MCP (Model Context Protocol) server that fetches YouTube video
transcripts and metadata, so Claude (Desktop, Code, or claude.ai) can pull
captions directly into context. It runs either way from the same codebase:

- **Locally over stdio** — spawned by Claude Desktop/Code via `uv run`.
- **Remotely over SSE** — a persistent HTTP service (e.g. deployed on
  Render) that Claude connects to over the network.

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

## Setup (local, stdio)

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
| `YTT_ALLOWED_HOSTS` | unset | Comma-separated Host headers to allow on the SSE endpoints, e.g. `my-app.onrender.com` (remote/SSE only, see below) |

### About `YTT_ALLOWED_HOSTS` (remote/SSE deployments)

The SSE transport keeps the MCP SDK's DNS-rebinding protection enabled,
which validates the incoming `Host` header against an allowlist.
`127.0.0.1`/`localhost`/`::1` are always allowed (so local `uv run` and
local Docker testing work out of the box), but a request reaching a remote
deployment carries a public Host header instead, so that hostname needs to
be added to the allowlist or every SSE connection gets rejected with `421
Misdirected Request`.

On Render this is handled automatically: Render sets `RENDER_EXTERNAL_HOSTNAME`
for every web service, and the server picks it up with no config needed. Set
`YTT_ALLOWED_HOSTS` yourself if you're deploying elsewhere, or if you've
mapped a custom domain on top of the `onrender.com` one, e.g.:

```bash
export YTT_ALLOWED_HOSTS="transcripts.example.com"
```

### About `YTT_PROXY_URL`

`youtube-transcript-api` calls YouTube's internal endpoints directly from
your machine's IP, and cloud hosts (CI runners, VPS, etc.) are frequently
rate-limited or blocked by YouTube as a result. If you see errors mentioning
IP blocking or rate limiting, set `YTT_PROXY_URL` to an HTTP/HTTPS proxy
(e.g. a residential or datacenter proxy), for example:

```bash
export YTT_PROXY_URL="http://user:pass@proxy.example.com:8080"
```

## Registering with Claude (local, stdio)

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

## Deploying to Render (remote, SSE)

The same tool logic is also served as a persistent HTTP service using the
MCP SDK's SSE transport, mounted as a Starlette ASGI app (`app` in
`src/youtube_transcript_mcp/server.py`) and run with `uvicorn` inside a
Docker container. This is what `Dockerfile` and `render.yaml` are for.

### Option A: one-click Blueprint deploy

1. Push this repo to your own GitHub/GitLab account (Render deploys from a
   connected repo).
2. In the Render dashboard: **New > Blueprint**, point it at your repo.
   Render will read `render.yaml` and create the web service automatically
   (Docker env, free plan, `/health` check already configured).
3. If you need a proxy for `youtube-transcript-api` (see below), set
   `YTT_PROXY_URL` in the service's Environment tab — it's intentionally
   left unset by the blueprint (`sync: false`) since it's optional and
   deployment-specific.
4. Deploy. Render builds the Docker image and starts the service.

### Option B: manual web service

1. In the Render dashboard: **New > Web Service**, connect your repo.
2. Environment: **Docker**. Render will detect and use the `Dockerfile`.
3. Plan: **Free**.
4. Health check path: `/health`.
5. Add environment variables as needed: `YTT_DEFAULT_LANGUAGES`,
   `YTT_TIMEOUT_SECONDS`, `YTT_PROXY_URL`. `YTT_ALLOWED_HOSTS` is normally
   not needed on Render since `RENDER_EXTERNAL_HOSTNAME` is auto-detected
   (see Configuration below) — only set it for a custom domain.

### Free tier constraints (read this before relying on it)

Render's free web service plan:

- **750 instance-hours/month**, no credit card required to start.
- **Spins down after 15 minutes of no inbound traffic.** The next request
  wakes it back up, but that first request pays a **~30-60 second cold
  start** while Render boots a fresh container. Claude will just see that
  request hang/time out longer than usual — it's not an error, it's the
  container waking up.
- The `/health` endpoint is intentionally cheap (no YouTube/yt-dlp work) so
  Render's own health checks, and anything else polling to keep the
  service warm, get a fast response instead of adding to cold-start time.
- `yt_dlp` is imported lazily (inside the functions that use it, not at
  module load) specifically so a freshly-woken container can answer
  `/health` and an MCP `initialize` handshake quickly, before paying that
  import cost on the first real tool call.

### Registering the deployed server with Claude (remote, SSE)

Once deployed, Render gives you a URL like
`https://youtube-transcript-mcp.onrender.com`. The SSE endpoint MCP clients
connect to is `<that URL>/sse`. Register it as a remote MCP server wherever
your Claude client supports remote/HTTP MCP servers (Claude Code, or
claude.ai's remote MCP connector settings), using that SSE URL instead of a
local `command`/`args` pair.

### Verifying the deployment

```bash
# 1. Liveness check (should return instantly once the container is up)
curl https://your-service.onrender.com/health
# => {"status":"ok"}

# 2. SSE endpoint should return an event-stream (Ctrl-C to stop; it's a
#    long-lived connection by design, so a hang here after the headers
#    print is expected, not a bug)
curl -N -H "Accept: text/event-stream" https://your-service.onrender.com/sse
```

If step 1 times out, the instance is likely still cold-starting — retry
after ~30-60s. If step 2 doesn't return `content-type: text/event-stream`
headers, something is wrong with the deploy (check Render's build/runtime
logs).

## Example tool calls

```
get_transcript(url="https://youtu.be/dQw4w9WgXcQ")
get_transcript(url="dQw4w9WgXcQ", languages=["es", "en"], include_timestamps=false)
list_available_transcripts(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
get_video_metadata(url="https://www.youtube.com/shorts/dQw4w9WgXcQ")
```

## Testing

### Unit tests

```bash
uv run pytest                       # unit tests only
uv run pytest -m integration        # also run the integration test (hits real YouTube)
```

### Local Docker test (verify the same image Render will run)

Build and run the container exactly as Render will, then confirm it behaves
like the stdio version functionally:

```bash
docker build -t youtube-transcript-mcp .
docker run --rm -p 8000:8000 -e PORT=8000 youtube-transcript-mcp
```

In another terminal:

```bash
# Liveness check
curl http://localhost:8000/health
# => {"status":"ok"}

# Confirm the SSE endpoint is live (Ctrl-C to stop — it's a long-lived
# connection by design)
curl -N -H "Accept: text/event-stream" http://localhost:8000/sse
```

For a full functional check (tool listing + a real tool call) against the
running container, point the MCP Python SDK's SSE client at it:

```bash
uv run python3 -c "
import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client

async def main():
    async with sse_client('http://localhost:8000/sse') as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print([t.name for t in tools.tools])

asyncio.run(main())
"
```

You should see `['get_transcript', 'get_video_metadata', 'list_available_transcripts']`.

## Out of scope (v1)

- Transcribing audio (e.g. via Whisper) when no captions exist at all
- Playlist/channel-wide batch transcript fetching
- Transcript translation beyond YouTube's native translatable tracks
