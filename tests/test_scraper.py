"""Tests for yt_live_scraper.scraper."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
import requests

from yt_live_scraper.scraper import (
    StreamLiveStatus,
    UpcomingStream,
    _extract_actual_start,
    _extract_player_response,
    _extract_yt_initial_data,
    _find_streams_tab,
    _get_overlay_style,
    _parse_channel_info,
    _parse_stream,
    get_upcoming_streams,
    is_stream_live,
)

NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures: minimal YouTube-shaped data
# ---------------------------------------------------------------------------

def _make_video(
    video_id: str = "abc123",
    title: str = "Test Stream",
    overlay_style: str = "UPCOMING",
    start_time: str | None = "1772654400",  # 2026-03-04 20:00 UTC
    thumbnail_url: str = "https://i.ytimg.com/vi/abc123/hq.jpg",
) -> dict:
    video = {
        "videoId": video_id,
        "title": {"runs": [{"text": title}]},
        "thumbnail": {"thumbnails": [{"url": thumbnail_url}]},
        "thumbnailOverlays": [
            {"thumbnailOverlayTimeStatusRenderer": {"style": overlay_style}}
        ],
    }
    if start_time is not None:
        video["upcomingEventData"] = {"startTime": start_time}
    return video


def _make_yt_data(
    channel_name: str = "TestChannel",
    channel_thumbnail_url: str = "https://yt3.googleusercontent.com/abc123",
    tab_title: str = "Live",
    videos: list[dict] | None = None,
) -> dict:
    if videos is None:
        videos = [_make_video()]
    contents = [
        {"richItemRenderer": {"content": {"videoRenderer": v}}} for v in videos
    ]
    return {
        "metadata": {
            "channelMetadataRenderer": {
                "title": channel_name,
                "avatar": {
                    "thumbnails": [{"url": channel_thumbnail_url}],
                },
            },
        },
        "contents": {
            "twoColumnBrowseResultsRenderer": {
                "tabs": [
                    {
                        "tabRenderer": {
                            "title": tab_title,
                            "content": {
                                "richGridRenderer": {"contents": contents}
                            },
                        }
                    }
                ]
            }
        },
    }


def _wrap_as_html(data: dict) -> str:
    return f"<html><script>var ytInitialData = {json.dumps(data)};</script></html>"


def _wrap_player_response(data: dict) -> str:
    return f"<html><script>var ytInitialPlayerResponse = {json.dumps(data)};</script></html>"


# ---------------------------------------------------------------------------
# _extract_yt_initial_data
# ---------------------------------------------------------------------------

class TestExtractYtInitialData:
    def test_parses_json_from_html(self):
        data = {"foo": "bar", "nested": {"a": 1}}
        html = _wrap_as_html(data)
        assert _extract_yt_initial_data(html) == data

    def test_raises_when_not_found(self):
        with pytest.raises(ValueError, match="Could not find ytInitialData"):
            _extract_yt_initial_data("<html><body>nothing here</body></html>")


# ---------------------------------------------------------------------------
# _find_streams_tab
# ---------------------------------------------------------------------------

class TestFindStreamsTab:
    def test_finds_live_tab(self):
        data = _make_yt_data(tab_title="Live")
        tab = _find_streams_tab(data)
        assert tab is not None
        assert tab["title"] == "Live"

    def test_finds_streams_tab(self):
        data = _make_yt_data(tab_title="Streams")
        tab = _find_streams_tab(data)
        assert tab is not None

    def test_returns_none_when_missing(self):
        data = _make_yt_data(tab_title="Videos")
        assert _find_streams_tab(data) is None


# ---------------------------------------------------------------------------
# _parse_channel_info
# ---------------------------------------------------------------------------

class TestParseChannelInfo:
    def test_extracts_name_and_thumbnail(self):
        data = _make_yt_data(
            channel_name="My Channel",
            channel_thumbnail_url="https://yt3.googleusercontent.com/thumb",
        )
        name, thumb = _parse_channel_info(data)
        assert name == "My Channel"
        assert thumb == "https://yt3.googleusercontent.com/thumb"

    def test_returns_defaults_for_missing(self):
        name, thumb = _parse_channel_info({})
        assert name == "Unknown"
        assert thumb == ""


# ---------------------------------------------------------------------------
# _get_overlay_style
# ---------------------------------------------------------------------------

class TestGetOverlayStyle:
    def test_returns_style(self):
        video = _make_video(overlay_style="LIVE")
        assert _get_overlay_style(video) == "LIVE"

    def test_returns_none_when_no_overlays(self):
        assert _get_overlay_style({}) is None

    def test_returns_none_when_no_status_renderer(self):
        video = {"thumbnailOverlays": [{"other": {}}]}
        assert _get_overlay_style(video) is None


# ---------------------------------------------------------------------------
# _parse_stream
# ---------------------------------------------------------------------------

class TestParseStream:
    def test_upcoming_stream(self):
        video = _make_video(overlay_style="UPCOMING", start_time="1772654400")
        stream = _parse_stream(video, "Ch", "https://thumb.url", NOW)
        assert stream is not None
        assert stream.video_id == "abc123"
        assert stream.title == "Test Stream"
        assert stream.live is False
        assert stream.channel_thumbnail_url == "https://thumb.url"
        assert stream.scheduled_start == datetime(2026, 3, 4, 20, 0, tzinfo=timezone.utc)
        assert stream.url == "https://www.youtube.com/watch?v=abc123"

    def test_live_stream(self):
        video = _make_video(overlay_style="LIVE", start_time=None)
        stream = _parse_stream(video, "Ch", "", NOW)
        assert stream is not None
        assert stream.live is True
        assert stream.scheduled_start == NOW

    def test_live_stream_with_start_time(self):
        video = _make_video(overlay_style="LIVE", start_time="1772654400")
        stream = _parse_stream(video, "Ch", "", NOW)
        assert stream is not None
        assert stream.live is True
        assert stream.scheduled_start == datetime(2026, 3, 4, 20, 0, tzinfo=timezone.utc)

    def test_past_vod_returns_none(self):
        video = _make_video(overlay_style="DEFAULT", start_time=None)
        assert _parse_stream(video, "Ch", "", NOW) is None

    def test_upcoming_without_start_time_returns_none(self):
        video = _make_video(overlay_style="UPCOMING", start_time=None)
        # No upcomingEventData and not LIVE → None
        assert _parse_stream(video, "Ch", "", NOW) is None


# ---------------------------------------------------------------------------
# UpcomingStream.__str__
# ---------------------------------------------------------------------------

class TestUpcomingStreamStr:
    def test_upcoming_format(self):
        s = UpcomingStream(
            channel="Ch",
            channel_thumbnail_url="",
            video_id="x",
            title="My Stream",
            scheduled_start=datetime(2026, 3, 4, 20, 0, tzinfo=timezone.utc),
            url="https://www.youtube.com/watch?v=x",
            thumbnail_url="",
            live=False,
        )
        text = str(s)
        assert "Ch" in text
        assert "My Stream" in text
        assert "[LIVE]" not in text

    def test_live_format(self):
        s = UpcomingStream(
            channel="Ch",
            channel_thumbnail_url="",
            video_id="x",
            title="My Stream",
            scheduled_start=NOW,
            url="https://www.youtube.com/watch?v=x",
            thumbnail_url="",
            live=True,
        )
        assert "[LIVE]" in str(s)


# ---------------------------------------------------------------------------
# UpcomingStream.exists
# ---------------------------------------------------------------------------

class TestUpcomingStreamExists:
    def test_exists_returns_true_on_200(self):
        with patch("yt_live_scraper.scraper.requests.get") as mocked_get:
            mocked_get.return_value = _FakeResponse("", status_code=200)
            assert UpcomingStream.exists("anychannel") is True
            mocked_get.assert_called_once()
            args, kwargs = mocked_get.call_args
            assert "@anychannel" in args[0]

    def test_exists_returns_false_on_404(self):
        with patch("yt_live_scraper.scraper.requests.get") as mocked_get:
            mocked_get.return_value = _FakeResponse("", status_code=404)
            assert UpcomingStream.exists("nonexistent") is False

    def test_exists_returns_false_on_exception(self):
        with patch("yt_live_scraper.scraper.requests.get") as mocked_get:
            mocked_get.side_effect = requests.RequestException()
            assert UpcomingStream.exists("errorchannel") is False

    def test_exists_strips_at_prefix(self):
        with patch("yt_live_scraper.scraper.requests.get") as mocked_get:
            mocked_get.return_value = _FakeResponse("", status_code=200)
            UpcomingStream.exists("@mychannel")
            args, kwargs = mocked_get.call_args
            assert "https://www.youtube.com/@mychannel/streams" == args[0]


# ---------------------------------------------------------------------------
# get_upcoming_streams (integration with mocked HTTP)
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class TestGetUpcomingStreams:
    def _patch_get(self, yt_data: dict):
        html = _wrap_as_html(yt_data)
        return patch("yt_live_scraper.scraper.requests.get", return_value=_FakeResponse(html))

    def test_returns_upcoming(self):
        data = _make_yt_data(
            channel_name="TestCh",
            videos=[_make_video(start_time="1772654400")],
        )
        with self._patch_get(data):
            streams = get_upcoming_streams(
                ["testch"], from_date=date(2026, 1, 1),
            )
        assert len(streams) == 1
        assert streams[0].channel == "TestCh"
        assert streams[0].channel_thumbnail_url == "https://yt3.googleusercontent.com/abc123"
        assert streams[0].live is False

    def test_returns_live(self):
        data = _make_yt_data(
            videos=[_make_video(overlay_style="LIVE", start_time=None)],
        )
        with self._patch_get(data):
            streams = get_upcoming_streams(
                ["ch"], from_date=date(2026, 1, 1),
            )
        assert len(streams) == 1
        assert streams[0].live is True

    def test_filters_past_vods(self):
        data = _make_yt_data(
            videos=[
                _make_video(overlay_style="DEFAULT", start_time=None),
                _make_video(
                    video_id="upcoming1",
                    overlay_style="UPCOMING",
                    start_time="1772654400",
                ),
            ],
        )
        with self._patch_get(data):
            streams = get_upcoming_streams(
                ["ch"], from_date=date(2026, 1, 1),
            )
        assert len(streams) == 1
        assert streams[0].video_id == "upcoming1"

    def test_from_date_filters(self):
        data = _make_yt_data(
            videos=[
                _make_video(
                    video_id="early",
                    start_time="1740787200",  # 2025-03-01
                ),
                _make_video(
                    video_id="later",
                    start_time="1772654400",  # 2026-03-04
                ),
            ],
        )
        with self._patch_get(data):
            streams = get_upcoming_streams(
                ["ch"], from_date=date(2026, 1, 1),
            )
        assert len(streams) == 1
        assert streams[0].video_id == "later"

    def test_sorted_by_start_time(self):
        data = _make_yt_data(
            videos=[
                _make_video(video_id="later", start_time="1772654400"),
                _make_video(video_id="earlier", start_time="1772000000"),
            ],
        )
        with self._patch_get(data):
            streams = get_upcoming_streams(
                ["ch"], from_date=date(2026, 1, 1),
            )
        assert [s.video_id for s in streams] == ["earlier", "later"]

    def test_multiple_channels(self):
        data = _make_yt_data(
            videos=[_make_video(start_time="1772654400")],
        )
        html = _wrap_as_html(data)
        with patch(
            "yt_live_scraper.scraper.requests.get",
            return_value=_FakeResponse(html),
        ):
            streams = get_upcoming_streams(
                ["@ch1", "ch2"], from_date=date(2026, 1, 1),
            )
        assert len(streams) == 2

    def test_strips_at_from_handle(self):
        data = _make_yt_data(videos=[_make_video(start_time="1772654400")])
        html = _wrap_as_html(data)
        with patch(
            "yt_live_scraper.scraper.requests.get",
            return_value=_FakeResponse(html),
        ) as mock_get:
            get_upcoming_streams(["@myhandle"], from_date=date(2026, 1, 1))
        url = mock_get.call_args[0][0]
        assert "/@myhandle/streams" in url

    def test_http_error_skips_channel(self, capsys):
        with patch(
            "yt_live_scraper.scraper.requests.get",
            side_effect=requests.ConnectionError("fail"),
        ):
            streams = get_upcoming_streams(["badchannel"])
        assert streams == []
        assert "Warning" in capsys.readouterr().out

    def test_no_yt_initial_data_skips_channel(self, capsys):
        with patch(
            "yt_live_scraper.scraper.requests.get",
            return_value=_FakeResponse("<html>no data</html>"),
        ):
            streams = get_upcoming_streams(["ch"])
        assert streams == []
        assert "Warning" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _extract_player_response
# ---------------------------------------------------------------------------

class TestExtractPlayerResponse:
    def test_parses_json_from_html(self):
        data = {"videoDetails": {"isLive": True}}
        html = _wrap_player_response(data)
        assert _extract_player_response(html) == data

    def test_raises_when_not_found(self):
        with pytest.raises(ValueError, match="Could not find ytInitialPlayerResponse"):
            _extract_player_response("<html><body>nothing</body></html>")


# ---------------------------------------------------------------------------
# _extract_actual_start
# ---------------------------------------------------------------------------

class TestExtractActualStart:
    def test_extracts_start_timestamp(self):
        data = {
            "microformat": {
                "playerMicroformatRenderer": {
                    "liveBroadcastDetails": {
                        "startTimestamp": "2026-03-04T20:00:00+00:00",
                    }
                }
            }
        }
        result = _extract_actual_start(data)
        assert result == datetime(2026, 3, 4, 20, 0, tzinfo=timezone.utc)

    def test_returns_none_when_missing(self):
        assert _extract_actual_start({}) is None
        assert _extract_actual_start({"microformat": {}}) is None

    def test_returns_none_on_invalid_timestamp(self):
        data = {
            "microformat": {
                "playerMicroformatRenderer": {
                    "liveBroadcastDetails": {
                        "startTimestamp": "not-a-date",
                    }
                }
            }
        }
        assert _extract_actual_start(data) is None


# ---------------------------------------------------------------------------
# is_stream_live
# ---------------------------------------------------------------------------

class TestIsStreamLive:
    def _patch_get(self, html: str, status_code: int = 200):
        return patch(
            "yt_live_scraper.scraper.requests.get",
            return_value=_FakeResponse(html, status_code),
        )

    def test_returns_live_with_actual_start(self):
        data = {
            "videoDetails": {"isLive": True},
            "microformat": {
                "playerMicroformatRenderer": {
                    "liveBroadcastDetails": {
                        "startTimestamp": "2026-03-04T20:00:00+00:00",
                    }
                }
            },
        }
        html = _wrap_player_response(data)
        with self._patch_get(html):
            result = is_stream_live("abc123")
        assert result.is_live is True
        assert result.actual_start == datetime(2026, 3, 4, 20, 0, tzinfo=timezone.utc)

    def test_returns_live_without_actual_start(self):
        html = _wrap_player_response({"videoDetails": {"isLive": True}})
        with self._patch_get(html):
            result = is_stream_live("abc123")
        assert result.is_live is True
        assert result.actual_start is None

    def test_returns_not_live(self):
        html = _wrap_player_response({"videoDetails": {"isLive": False}})
        with self._patch_get(html):
            result = is_stream_live("abc123")
        assert result.is_live is False
        assert result.actual_start is None

    def test_returns_not_live_when_no_isLive_key(self):
        html = _wrap_player_response({"videoDetails": {"title": "Test"}})
        with self._patch_get(html):
            result = is_stream_live("abc123")
        assert result.is_live is False

    def test_returns_not_live_on_http_error(self):
        with patch(
            "yt_live_scraper.scraper.requests.get",
            side_effect=requests.ConnectionError("fail"),
        ):
            result = is_stream_live("abc123")
        assert result.is_live is False
        assert result.actual_start is None

    def test_returns_not_live_when_no_player_response(self):
        with self._patch_get("<html>no data</html>"):
            result = is_stream_live("abc123")
        assert result.is_live is False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCli:
    def test_text_output(self, capsys):
        data = _make_yt_data(
            channel_name="MyCh",
            videos=[_make_video(title="Cool Stream", start_time="1772654400")],
        )
        html = _wrap_as_html(data)
        with patch(
            "yt_live_scraper.scraper.requests.get",
            return_value=_FakeResponse(html),
        ):
            from yt_live_scraper.cli import main
            main(["ch", "--from", "2026-01-01"])
        out = capsys.readouterr().out
        assert "MyCh" in out
        assert "Cool Stream" in out

    def test_json_output(self, capsys):
        data = _make_yt_data(
            videos=[_make_video(start_time="1772654400")],
        )
        html = _wrap_as_html(data)
        with patch(
            "yt_live_scraper.scraper.requests.get",
            return_value=_FakeResponse(html),
        ):
            from yt_live_scraper.cli import main
            main(["ch", "--from", "2026-01-01", "--json"])
        parsed = json.loads(capsys.readouterr().out)
        assert len(parsed) == 1
        assert "video_id" in parsed[0]
        assert "channel_thumbnail_url" in parsed[0]
        assert "live" in parsed[0]
        assert parsed[0]["scheduled_start"] == "2026-03-04T20:00:00+00:00"

    def test_no_streams_exits_zero(self, capsys):
        data = _make_yt_data(videos=[_make_video(overlay_style="DEFAULT", start_time=None)])
        html = _wrap_as_html(data)
        with patch(
            "yt_live_scraper.scraper.requests.get",
            return_value=_FakeResponse(html),
        ):
            from yt_live_scraper.cli import main
            with pytest.raises(SystemExit) as exc_info:
                main(["ch", "--from", "2026-01-01"])
            assert exc_info.value.code == 0
        assert "No upcoming" in capsys.readouterr().out
