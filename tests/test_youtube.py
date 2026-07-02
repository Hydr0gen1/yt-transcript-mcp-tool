"""Unit tests for URL parsing and error mapping in youtube_transcript_mcp.youtube."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from youtube_transcript_api import (
    IpBlocked,
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from youtube_transcript_mcp import youtube
from youtube_transcript_mcp.errors import (
    InvalidURLError,
    LanguageNotFoundError,
    TranscriptUnavailableError,
    VideoUnavailableError,
)

VIDEO_ID = "dQw4w9WgXcQ"


class TestExtractVideoId:
    @pytest.mark.parametrize(
        "url",
        [
            f"https://www.youtube.com/watch?v={VIDEO_ID}",
            f"https://youtube.com/watch?v={VIDEO_ID}&t=10s",
            f"http://m.youtube.com/watch?v={VIDEO_ID}",
            f"https://youtu.be/{VIDEO_ID}",
            f"https://youtu.be/{VIDEO_ID}?t=5",
            f"https://www.youtube.com/embed/{VIDEO_ID}",
            f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}",
            f"https://www.youtube.com/shorts/{VIDEO_ID}",
            VIDEO_ID,
            f"  {VIDEO_ID}  ",
        ],
    )
    def test_valid_formats(self, url):
        assert youtube.extract_video_id(url) == VIDEO_ID

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "not a url",
            "https://example.com/watch?v=dQw4w9WgXcQ",
            "short",
            "https://www.youtube.com/watch?v=tooshort",
            "https://www.youtube.com/channel/UC1234567890",
        ],
    )
    def test_invalid_formats_raise(self, url):
        with pytest.raises(InvalidURLError):
            youtube.extract_video_id(url)

    def test_none_input_raises(self):
        with pytest.raises(InvalidURLError):
            youtube.extract_video_id(None)  # type: ignore[arg-type]


class TestLoadConfig:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("YTT_DEFAULT_LANGUAGES", raising=False)
        monkeypatch.delenv("YTT_PROXY_URL", raising=False)
        monkeypatch.delenv("YTT_TIMEOUT_SECONDS", raising=False)
        config = youtube.load_config()
        assert config.default_languages == ["en"]
        assert config.proxy_url is None
        assert config.timeout_seconds == 15.0

    def test_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("YTT_DEFAULT_LANGUAGES", "es, fr ,de")
        monkeypatch.setenv("YTT_PROXY_URL", "http://proxy.example:8080")
        monkeypatch.setenv("YTT_TIMEOUT_SECONDS", "30")
        config = youtube.load_config()
        assert config.default_languages == ["es", "fr", "de"]
        assert config.proxy_url == "http://proxy.example:8080"
        assert config.timeout_seconds == 30.0


class TestExpandLanguageCodes:
    """Both fetch paths require an exact code match, so regional tags need a base-language fallback."""

    def test_inserts_base_language_after_regional_tag(self):
        assert youtube._expand_language_codes(["en-US"]) == ["en-US", "en"]

    def test_leaves_plain_codes_unchanged(self):
        assert youtube._expand_language_codes(["en", "es"]) == ["en", "es"]

    def test_preserves_priority_order_across_multiple_regional_tags(self):
        assert youtube._expand_language_codes(["en-US", "es"]) == ["en-US", "en", "es"]

    def test_does_not_duplicate_an_already_present_base_language(self):
        assert youtube._expand_language_codes(["en-US", "en"]) == ["en-US", "en"]

    def test_get_transcript_text_passes_expanded_languages_to_fallback(self, monkeypatch):
        fake_api = MagicMock()
        fake_api.fetch.side_effect = TranscriptsDisabled(VIDEO_ID)
        monkeypatch.setattr(youtube, "_build_api", lambda config: fake_api)

        captured = {}

        def fake_fetch_via_ytdlp(video_id, languages, config):
            captured["languages"] = list(languages)
            return [{"text": "hi", "start": 0.0, "duration": 1.0}], "en"

        monkeypatch.setattr(youtube, "_fetch_via_ytdlp", fake_fetch_via_ytdlp)

        youtube.get_transcript_text(VIDEO_ID, languages=["en-US"])
        assert captured["languages"] == ["en-US", "en"]


class TestSubtitleLangPatterns:
    """yt-dlp treats subtitleslangs entries as regex fullmatches against available codes."""

    def test_builds_wildcard_suffixed_pattern_per_language(self):
        assert youtube._subtitle_lang_patterns(["en"]) == ["en.*"]

    def test_pattern_matches_orig_suffixed_and_random_suffixed_codes(self):
        pattern = youtube._subtitle_lang_patterns(["en"])[0]
        regex = re.compile(pattern)
        assert regex.fullmatch("en")
        assert regex.fullmatch("en-orig")
        assert regex.fullmatch("en-uYU-mmqFLq8")
        assert not regex.fullmatch("es")
        assert not regex.fullmatch("de-en")

    def test_escapes_regex_metacharacters_in_language_codes(self):
        # A language code is never attacker-controlled regex, but must still
        # be treated as a literal string, not interpreted as a pattern.
        pattern = youtube._subtitle_lang_patterns(["en.US"])[0]
        regex = re.compile(pattern)
        assert regex.fullmatch("en.US")
        assert not regex.fullmatch("enXUS")


class TestTimeoutSession:
    """_TimeoutSession is what makes YTT_TIMEOUT_SECONDS apply to the primary fetch path."""

    def test_applies_default_timeout(self, monkeypatch):
        session = youtube._TimeoutSession(7.5)
        captured = {}

        def fake_request(self, method, url, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        monkeypatch.setattr(requests.Session, "request", fake_request)
        session.get("http://example.com")
        assert captured["timeout"] == 7.5

    def test_does_not_override_explicit_timeout(self, monkeypatch):
        session = youtube._TimeoutSession(7.5)
        captured = {}

        def fake_request(self, method, url, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        monkeypatch.setattr(requests.Session, "request", fake_request)
        session.get("http://example.com", timeout=1.0)
        assert captured["timeout"] == 1.0

    def test_build_api_uses_timeout_session_with_configured_timeout(self):
        config = youtube.Config(default_languages=["en"], proxy_url=None, timeout_seconds=42.0)
        api = youtube._build_api(config)
        http_client = api._fetcher._http_client
        assert isinstance(http_client, youtube._TimeoutSession)
        assert http_client._timeout_seconds == 42.0


class TestParseVtt:
    """The yt-dlp fallback path parses raw WebVTT; entities must come out decoded."""

    def _write_vtt(self, tmp_path, content: str) -> str:
        path = Path(tmp_path) / "captions.vtt"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_decodes_html_entities(self, tmp_path):
        vtt = (
            "WEBVTT\n\n"
            "00:00:00.000 --> 00:00:02.000\n"
            "Rock &amp; Roll and it&#39;s &lt;great&gt;\n"
        )
        path = self._write_vtt(tmp_path, vtt)
        segments = youtube._parse_vtt(path)
        assert segments[0]["text"] == "Rock & Roll and it's <great>"

    def test_strips_inline_tags_without_reintroducing_them_via_entities(self, tmp_path):
        vtt = (
            "WEBVTT\n\n"
            "00:00:00.000 --> 00:00:02.000\n"
            "<00:00:00.500><c> karaoke</c> word &amp; more\n"
        )
        path = self._write_vtt(tmp_path, vtt)
        segments = youtube._parse_vtt(path)
        assert segments[0]["text"] == "karaoke word & more"


class TestFormatTranscript:
    def test_with_timestamps(self):
        segments = [
            {"text": "hello", "start": 0.0, "duration": 1.0},
            {"text": "world", "start": 65.4, "duration": 1.0},
        ]
        result = youtube.format_transcript(segments, include_timestamps=True)
        assert result == "[00:00] hello\n[01:05] world"

    def test_without_timestamps(self):
        segments = [
            {"text": "hello", "start": 0.0, "duration": 1.0},
            {"text": "world", "start": 65.4, "duration": 1.0},
        ]
        result = youtube.format_transcript(segments, include_timestamps=False)
        assert result == "hello\nworld"


class TestErrorMapping:
    """Mock youtube_transcript_api to verify exceptions are translated cleanly."""

    def test_transcripts_disabled_with_failed_fallback_raises_transcript_unavailable(
        self, monkeypatch
    ):
        fake_api = MagicMock()
        fake_api.fetch.side_effect = TranscriptsDisabled(VIDEO_ID)
        monkeypatch.setattr(youtube, "_build_api", lambda config: fake_api)
        monkeypatch.setattr(
            youtube,
            "_fetch_via_ytdlp",
            MagicMock(side_effect=TranscriptUnavailableError("no captions available")),
        )

        with pytest.raises(TranscriptUnavailableError):
            youtube.get_transcript_text(VIDEO_ID)

    def test_no_transcript_found_with_failed_fallback_raises_language_not_found(
        self, monkeypatch
    ):
        fake_api = MagicMock()
        fake_api.fetch.side_effect = NoTranscriptFound(VIDEO_ID, ["en"], MagicMock())
        monkeypatch.setattr(youtube, "_build_api", lambda config: fake_api)
        monkeypatch.setattr(
            youtube,
            "_fetch_via_ytdlp",
            MagicMock(side_effect=TranscriptUnavailableError("no captions available")),
        )
        monkeypatch.setattr(
            youtube, "_available_language_codes", lambda video_id, config: ["es", "fr"]
        )

        with pytest.raises(LanguageNotFoundError) as exc_info:
            youtube.get_transcript_text(VIDEO_ID, languages=["en"])
        assert exc_info.value.available == ["es", "fr"]
        assert exc_info.value.requested == ["en"]

    def test_transcripts_disabled_falls_back_to_ytdlp_success(self, monkeypatch):
        fake_api = MagicMock()
        fake_api.fetch.side_effect = TranscriptsDisabled(VIDEO_ID)
        monkeypatch.setattr(youtube, "_build_api", lambda config: fake_api)
        monkeypatch.setattr(
            youtube,
            "_fetch_via_ytdlp",
            lambda video_id, langs, config: (
                [{"text": "hello from auto captions", "start": 0.0, "duration": 1.0}],
                "en",
            ),
        )

        result = youtube.get_transcript_text(VIDEO_ID, include_timestamps=False)
        assert result == "hello from auto captions"

    def test_video_unavailable_raises_video_unavailable_error(self, monkeypatch):
        fake_api = MagicMock()
        fake_api.fetch.side_effect = VideoUnavailable(VIDEO_ID)
        monkeypatch.setattr(youtube, "_build_api", lambda config: fake_api)

        with pytest.raises(VideoUnavailableError):
            youtube.get_transcript_text(VIDEO_ID)

    def test_ip_blocked_raises_transcript_unavailable_error(self, monkeypatch):
        fake_api = MagicMock()
        fake_api.fetch.side_effect = IpBlocked(VIDEO_ID)
        monkeypatch.setattr(youtube, "_build_api", lambda config: fake_api)

        with pytest.raises(TranscriptUnavailableError):
            youtube.get_transcript_text(VIDEO_ID)

    def test_list_available_transcripts_maps_transcripts_disabled(self, monkeypatch):
        fake_api = MagicMock()
        fake_api.list.side_effect = TranscriptsDisabled(VIDEO_ID)
        monkeypatch.setattr(youtube, "_build_api", lambda config: fake_api)

        with pytest.raises(TranscriptUnavailableError):
            youtube.list_available_transcripts(VIDEO_ID)

    def test_list_available_transcripts_maps_video_unavailable(self, monkeypatch):
        fake_api = MagicMock()
        fake_api.list.side_effect = VideoUnavailable(VIDEO_ID)
        monkeypatch.setattr(youtube, "_build_api", lambda config: fake_api)

        with pytest.raises(VideoUnavailableError):
            youtube.list_available_transcripts(VIDEO_ID)

    def test_list_available_transcripts_returns_track_metadata(self, monkeypatch):
        manual_track = MagicMock(
            language="English",
            language_code="en",
            is_generated=False,
            is_translatable=True,
        )
        auto_track = MagicMock(
            language="Spanish (auto-generated)",
            language_code="es",
            is_generated=True,
            is_translatable=False,
        )
        fake_api = MagicMock()
        fake_api.list.return_value = [manual_track, auto_track]
        monkeypatch.setattr(youtube, "_build_api", lambda config: fake_api)

        result = youtube.list_available_transcripts(VIDEO_ID)
        assert len(result) == 2
        assert result[0].language_code == "en"
        assert result[0].is_generated is False
        assert result[1].language_code == "es"
        assert result[1].is_generated is True


@pytest.mark.integration
class TestIntegration:
    """Hits the real YouTube API. Skipped by default; run with -m integration."""

    STABLE_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo"

    def test_get_transcript_end_to_end(self):
        result = youtube.get_transcript_text(self.STABLE_VIDEO_URL)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_list_available_transcripts_end_to_end(self):
        result = youtube.list_available_transcripts(self.STABLE_VIDEO_URL)
        assert len(result) > 0
