"""URL parsing and transcript/metadata fetch logic.

All public functions in this module raise only the custom exceptions defined
in :mod:`youtube_transcript_mcp.errors` on failure -- library-specific
exceptions from ``youtube_transcript_api`` and ``yt_dlp`` are caught here and
translated into user-readable messages.
"""

from __future__ import annotations

import glob
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from youtube_transcript_api import (
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)
from youtube_transcript_api.proxies import GenericProxyConfig

from .errors import (
    InvalidURLError,
    LanguageNotFoundError,
    TranscriptUnavailableError,
    VideoUnavailableError,
    YoutubeTranscriptMCPError,
)

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_URL_PATTERNS = [
    re.compile(r"youtube(?:-nocookie)?\.com/watch\?(?:.*&)?v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube(?:-nocookie)?\.com/embed/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/shorts/([A-Za-z0-9_-]{11})"),
]

_RETRY_ATTEMPTS = 2
_RETRY_BACKOFF_SECONDS = 0.5

# Exceptions from youtube_transcript_api that indicate a definitive,
# non-transient outcome -- retrying them again would just fail the same way.
_NON_TRANSIENT_EXCEPTIONS = (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
)


@dataclass
class Config:
    default_languages: list[str]
    proxy_url: str | None
    timeout_seconds: float


@dataclass
class TranscriptInfo:
    language: str
    language_code: str
    is_generated: bool
    is_translatable: bool


@dataclass
class VideoMetadata:
    title: str
    channel: str
    duration_seconds: int | None
    upload_date: str | None


def load_config() -> Config:
    raw_languages = os.environ.get("YTT_DEFAULT_LANGUAGES", "en")
    proxy_url = os.environ.get("YTT_PROXY_URL") or None
    timeout_raw = os.environ.get("YTT_TIMEOUT_SECONDS", "15")
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError:
        timeout_seconds = 15.0
    languages = [lang.strip() for lang in raw_languages.split(",") if lang.strip()]
    return Config(
        default_languages=languages or ["en"],
        proxy_url=proxy_url,
        timeout_seconds=timeout_seconds,
    )


def extract_video_id(url: str) -> str:
    """Parse a YouTube video ID out of a URL or bare ID string."""
    if not url or not isinstance(url, str):
        raise InvalidURLError(f"Could not parse a video ID from: {url!r}")

    candidate = url.strip()
    if _VIDEO_ID_RE.match(candidate):
        return candidate

    for pattern in _URL_PATTERNS:
        match = pattern.search(candidate)
        if match:
            return match.group(1)

    raise InvalidURLError(f"Could not parse a YouTube video ID from: {url!r}")


def _build_api(config: Config) -> YouTubeTranscriptApi:
    proxy_config = None
    if config.proxy_url:
        proxy_config = GenericProxyConfig(
            http_url=config.proxy_url, https_url=config.proxy_url
        )
    return YouTubeTranscriptApi(proxy_config=proxy_config)


def _with_retry(func, *args, **kwargs):
    """Retry transient failures once; let non-transient library errors through immediately."""
    last_exc: Exception | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return func(*args, **kwargs)
        except _NON_TRANSIENT_EXCEPTIONS:
            raise
        except Exception as exc:  # noqa: BLE001 - broad on purpose, re-raised below
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    assert last_exc is not None
    raise last_exc


def _available_language_codes(video_id: str, config: Config) -> list[str]:
    try:
        api = _build_api(config)
        transcript_list = _with_retry(api.list, video_id)
        return [t.language_code for t in transcript_list]
    except Exception:  # noqa: BLE001 - best-effort, used only for error messages
        return []


def _fetch_via_api(
    video_id: str, languages: list[str], config: Config
) -> tuple[list[dict], str]:
    api = _build_api(config)
    fetched = _with_retry(api.fetch, video_id, languages=languages)
    segments = [
        {"text": s.text, "start": s.start, "duration": s.duration} for s in fetched
    ]
    return segments, fetched.language_code


_VTT_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})"
)
_VTT_TAG_RE = re.compile(r"<[^>]+>")


def _vtt_timestamp_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _parse_vtt(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    segments: list[dict] = []
    i = 0
    while i < len(lines):
        match = _VTT_TIME_RE.search(lines[i])
        if not match:
            i += 1
            continue

        start = _vtt_timestamp_to_seconds(*match.groups()[0:4])
        end = _vtt_timestamp_to_seconds(*match.groups()[4:8])
        i += 1

        text_lines = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(_VTT_TAG_RE.sub("", lines[i]).strip())
            i += 1

        text = " ".join(t for t in text_lines if t).strip()
        # YouTube's auto-generated VTT repeats rolling caption text across
        # consecutive cues; drop exact repeats of the previous line.
        if text and (not segments or segments[-1]["text"] != text):
            segments.append({"text": text, "start": start, "duration": max(end - start, 0.0)})

    return segments


def _lang_code_from_filename(path: str) -> str:
    parts = Path(path).stem.split(".")
    return parts[-1] if len(parts) > 1 else "unknown"


def _pick_best_vtt(vtt_files: list[str], languages: list[str]) -> str:
    by_lang = {_lang_code_from_filename(path): path for path in vtt_files}
    for lang in languages:
        if lang in by_lang:
            return by_lang[lang]
        base = lang.split("-")[0]
        for candidate_lang, path in by_lang.items():
            if candidate_lang.split("-")[0] == base:
                return path
    return vtt_files[0]


class _YtdlpSilentLogger:
    """Routes yt-dlp's log messages to stderr, never stdout.

    This server communicates over stdio (JSON-RPC on stdout); any stray
    yt-dlp output on stdout would corrupt the MCP protocol stream.
    """

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        print(msg, file=sys.stderr)

    def error(self, msg: str) -> None:
        print(msg, file=sys.stderr)


def _ydl_opts(config: Config, **overrides) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": config.timeout_seconds,
        "logger": _YtdlpSilentLogger(),
    }
    if config.proxy_url:
        opts["proxy"] = config.proxy_url
    opts.update(overrides)
    return opts


def _fetch_via_ytdlp(
    video_id: str, languages: list[str], config: Config
) -> tuple[list[dict], str]:
    """Fallback transcript fetch: ask yt-dlp to download subtitle/auto-caption tracks."""
    import yt_dlp  # lazy: keeps this out of server startup/cold-start path

    video_url = f"https://www.youtube.com/watch?v={video_id}"

    with tempfile.TemporaryDirectory() as tmpdir:
        outtmpl = os.path.join(tmpdir, "%(id)s.%(ext)s")
        ydl_opts = _ydl_opts(
            config,
            skip_download=True,
            writesubtitles=True,
            writeautomaticsub=True,
            subtitleslangs=languages,
            subtitlesformat="vtt",
            outtmpl=outtmpl,
        )

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
        except yt_dlp.utils.DownloadError as exc:
            raise TranscriptUnavailableError(
                f"yt-dlp fallback failed for video '{video_id}': {exc}"
            ) from exc

        vtt_files = sorted(glob.glob(os.path.join(tmpdir, f"{video_id}*.vtt")))
        if not vtt_files:
            raise TranscriptUnavailableError(
                f"No captions (manual or auto-generated) could be found for video '{video_id}'."
            )

        chosen = _pick_best_vtt(vtt_files, languages)
        lang_code = _lang_code_from_filename(chosen)
        segments = _parse_vtt(chosen)
        if not segments:
            raise TranscriptUnavailableError(
                f"Captions for video '{video_id}' were found but could not be parsed."
            )
        return segments, lang_code


def format_transcript(segments: list[dict], include_timestamps: bool) -> str:
    lines = []
    for seg in segments:
        if include_timestamps:
            minutes, seconds = divmod(int(seg["start"]), 60)
            lines.append(f"[{minutes:02d}:{seconds:02d}] {seg['text']}")
        else:
            lines.append(seg["text"])
    return "\n".join(lines)


def get_transcript_text(
    url: str,
    languages: list[str] | None = None,
    include_timestamps: bool = True,
) -> str:
    config = load_config()
    langs = list(languages) if languages else list(config.default_languages)
    video_id = extract_video_id(url)

    try:
        segments, _lang_code = _fetch_via_api(video_id, langs, config)
        return format_transcript(segments, include_timestamps)
    except (TranscriptsDisabled, NoTranscriptFound) as primary_exc:
        try:
            segments, _lang_code = _fetch_via_ytdlp(video_id, langs, config)
            return format_transcript(segments, include_timestamps)
        except YoutubeTranscriptMCPError:
            if isinstance(primary_exc, TranscriptsDisabled):
                raise TranscriptUnavailableError(
                    f"Captions are disabled for video '{video_id}' and no "
                    "auto-generated captions could be retrieved either."
                ) from primary_exc
            available = _available_language_codes(video_id, config)
            raise LanguageNotFoundError(langs, available) from primary_exc
    except VideoUnavailable as exc:
        raise VideoUnavailableError(
            f"Video '{video_id}' is unavailable (it may be private, deleted, or region-locked)."
        ) from exc
    except (IpBlocked, RequestBlocked) as exc:
        raise TranscriptUnavailableError(
            f"YouTube blocked the transcript request for video '{video_id}' "
            "(likely rate-limited or IP-blocked). Consider configuring YTT_PROXY_URL."
        ) from exc
    except YoutubeTranscriptMCPError:
        raise
    except Exception as exc:  # noqa: BLE001 - final safety net, never leak a traceback
        raise TranscriptUnavailableError(
            f"Failed to fetch transcript for video '{video_id}': {exc}"
        ) from exc


def list_available_transcripts(url: str) -> list[TranscriptInfo]:
    config = load_config()
    video_id = extract_video_id(url)
    api = _build_api(config)

    try:
        transcript_list = _with_retry(api.list, video_id)
    except TranscriptsDisabled as exc:
        raise TranscriptUnavailableError(
            f"Captions are disabled for video '{video_id}'."
        ) from exc
    except VideoUnavailable as exc:
        raise VideoUnavailableError(
            f"Video '{video_id}' is unavailable (it may be private, deleted, or region-locked)."
        ) from exc
    except (IpBlocked, RequestBlocked) as exc:
        raise TranscriptUnavailableError(
            f"YouTube blocked the request for video '{video_id}' "
            "(likely rate-limited or IP-blocked). Consider configuring YTT_PROXY_URL."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise TranscriptUnavailableError(
            f"Failed to list transcripts for video '{video_id}': {exc}"
        ) from exc

    return [
        TranscriptInfo(
            language=t.language,
            language_code=t.language_code,
            is_generated=t.is_generated,
            is_translatable=t.is_translatable,
        )
        for t in transcript_list
    ]


def get_video_metadata(url: str) -> VideoMetadata:
    import yt_dlp  # lazy: keeps this out of server startup/cold-start path

    config = load_config()
    video_id = extract_video_id(url)
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = _ydl_opts(config, skip_download=True)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise VideoUnavailableError(
            f"Could not retrieve metadata for video '{video_id}' "
            f"(it may be private, deleted, or region-locked): {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise TranscriptUnavailableError(
            f"Failed to fetch metadata for video '{video_id}': {exc}"
        ) from exc

    if info is None:
        raise VideoUnavailableError(f"Could not retrieve metadata for video '{video_id}'.")

    upload_date = info.get("upload_date")
    if upload_date and len(upload_date) == 8:
        upload_date = f"{upload_date[0:4]}-{upload_date[4:6]}-{upload_date[6:8]}"

    return VideoMetadata(
        title=info.get("title") or "Unknown title",
        channel=info.get("channel") or info.get("uploader") or "Unknown channel",
        duration_seconds=info.get("duration"),
        upload_date=upload_date,
    )
