FROM python:3.12-slim

# ffmpeg: yt-dlp uses it for merging/remuxing on some caption/media extraction
# paths. ca-certificates: TLS trust store for outbound HTTPS calls to YouTube.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv for fast, lockfile-reproducible dependency installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Install dependencies first, in their own layer, so source-only changes
# don't invalidate the (slow) dependency install cache.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Now add the actual application code and install it.
COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Documents the conventional default; Render assigns the real port at
# runtime via $PORT, which the CMD below binds to.
EXPOSE 8000

# Shell form so $PORT is expanded by the shell at container start (Render
# sets PORT dynamically; it is not known at build time).
CMD uvicorn youtube_transcript_mcp.server:app --host 0.0.0.0 --port ${PORT:-8000}
