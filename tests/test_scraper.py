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
    clear_caches,
    _extract_actual_start,
    _extract_player_response,
    _extract_scheduled_start,
    _extract_yt_initial_data,
    _fetch_scheduled_start,
    _find_streams_tab,
    _get_lockup_overlay_style,
    _get_overlay_style,
    _parse_channel_info,
    _parse_stream,
    get_channel,
    get_upcoming_streams,
    is_stream_live,
)

NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_start_time_cache():
    """Isolate the module-level start-time cache between tests."""
    clear_caches()
    yield
    clear_caches()


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


def _make_lockup(
    content_id: str = "lockup123",
    title: str = "Lockup Title",
    *,
    badge_style: str = "THUMBNAIL_OVERLAY_BADGE_STYLE_DEFAULT",
    badge_text: str | None = None,
    thumbnail_url: str = "https://thumb/1.jpg",
) -> dict:
    badge: dict = {"badgeStyle": badge_style}
    if badge_text is not None:
        badge["text"] = badge_text
    return {
        "contentId": content_id,
        "contentImage": {
            "thumbnailViewModel": {
                "image": {"sources": [{"url": thumbnail_url}]},
                "overlays": [
                    {
                        "thumbnailBottomOverlayViewModel": {
                            "badges": [{"thumbnailBadgeViewModel": badge}]
                        }
                    }
                ],
            }
        },
        "metadata": {"lockupMetadataViewModel": {"title": {"content": title}}},
    }


def _make_player_response(start_timestamp: str = "2026-06-16T21:00:00+00:00") -> dict:
    return {
        "videoDetails": {"isUpcoming": True},
        "microformat": {
            "playerMicroformatRenderer": {
                "liveBroadcastDetails": {"startTimestamp": start_timestamp}
            }
        },
    }


def _make_offline_slate_response(scheduled_start_time: str = "1781643600") -> dict:
    # An upcoming stream that hasn't begun: no liveBroadcastDetails, but a
    # Unix scheduledStartTime in the offline-slate renderer.
    return {
        "videoDetails": {"isUpcoming": True},
        "playabilityStatus": {
            "status": "LIVE_STREAM_OFFLINE",
            "liveStreamability": {
                "liveStreamabilityRenderer": {
                    "offlineSlate": {
                        "liveStreamOfflineSlateRenderer": {
                            "scheduledStartTime": scheduled_start_time
                        }
                    }
                }
            },
        },
    }


def _make_yt_data(
    channel_name: str = "TestChannel",
    channel_id: str = "UC1234567890",
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
                "externalId": channel_id,
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
    def test_extracts_name_id_and_thumbnail(self):
        data = _make_yt_data(
            channel_name="My Channel",
            channel_id="UC_CHANNEL_ID",
            channel_thumbnail_url="https://yt3.googleusercontent.com/thumb",
        )
        name, channel_id, thumb = _parse_channel_info(data)
        assert name == "My Channel"
        assert channel_id == "UC_CHANNEL_ID"
        assert thumb == "https://yt3.googleusercontent.com/thumb"

    def test_returns_defaults_for_missing(self):
        name, channel_id, thumb = _parse_channel_info({})
        assert name == "Unknown"
        assert channel_id == ""
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
        stream = _parse_stream(video, "Ch", "UC123", "https://thumb.url", NOW)
        assert stream is not None
        assert stream.video_id == "abc123"
        assert stream.channel_id == "UC123"
        assert stream.title == "Test Stream"
        assert stream.live is False
        assert stream.channel_thumbnail_url == "https://thumb.url"
        assert stream.scheduled_start == datetime(2026, 3, 4, 20, 0, tzinfo=timezone.utc)
        assert stream.url == "https://www.youtube.com/watch?v=abc123"

    def test_live_stream(self):
        video = _make_video(overlay_style="LIVE", start_time=None)
        stream = _parse_stream(video, "Ch", "UC123", "", NOW)
        assert stream is not None
        assert stream.channel_id == "UC123"
        assert stream.live is True
        assert stream.scheduled_start == NOW

    def test_live_stream_with_start_time(self):
        video = _make_video(overlay_style="LIVE", start_time="1772654400")
        stream = _parse_stream(video, "Ch", "UC123", "", NOW)
        assert stream is not None
        assert stream.channel_id == "UC123"
        assert stream.live is True
        assert stream.scheduled_start == datetime(2026, 3, 4, 20, 0, tzinfo=timezone.utc)

    def test_past_vod_returns_none(self):
        video = _make_video(overlay_style="DEFAULT", start_time=None)
        assert _parse_stream(video, "Ch", "UC123", "", NOW) is None

    def test_upcoming_without_start_time_returns_none(self):
        video = _make_video(overlay_style="UPCOMING", start_time=None)
        # No upcomingEventData and not LIVE → None
        assert _parse_stream(video, "Ch", "UC123", "", NOW) is None


# ---------------------------------------------------------------------------
# UpcomingStream.__str__
# ---------------------------------------------------------------------------

class TestUpcomingStreamStr:
    def test_upcoming_format(self):
        s = UpcomingStream(
            channel="Ch",
            channel_id="UC123",
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
            channel_id="UC123",
            channel_thumbnail_url="",
            video_id="x",
            title="My Stream",
            scheduled_start=NOW,
            url="https://www.youtube.com/watch?v=x",
            thumbnail_url="",
            live=True,
        )
        out = str(s)
        assert "[LIVE]" in out
        assert " (x)" in out


# ---------------------------------------------------------------------------
# UpcomingStream.exists
# ---------------------------------------------------------------------------

class TestUpcomingStreamExists:
    def test_exists_returns_true_on_200(self):
        with patch("yt_live_scraper.scraper._http_get") as mocked_get:
            mocked_get.return_value = _FakeResponse("", status_code=200)
            assert UpcomingStream.exists("anychannel") is True
            mocked_get.assert_called_once()
            args, kwargs = mocked_get.call_args
            assert "@anychannel" in args[0]

    def test_exists_returns_false_on_404(self):
        with patch("yt_live_scraper.scraper._http_get") as mocked_get:
            mocked_get.return_value = _FakeResponse("", status_code=404)
            assert UpcomingStream.exists("nonexistent") is False

    def test_exists_returns_false_on_exception(self):
        with patch("yt_live_scraper.scraper._http_get") as mocked_get:
            mocked_get.side_effect = requests.RequestException()
            assert UpcomingStream.exists("errorchannel") is False

    def test_exists_strips_at_prefix(self):
        with patch("yt_live_scraper.scraper._http_get") as mocked_get:
            mocked_get.return_value = _FakeResponse("", status_code=200)
            UpcomingStream.exists("@mychannel")
            args, kwargs = mocked_get.call_args
            assert "https://www.youtube.com/@mychannel" == args[0]


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
        return patch("yt_live_scraper.scraper._http_get", return_value=_FakeResponse(html))

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

    def test_live_stream_started_before_cutoff_is_included(self):
        # 2026-03-01 12:00:00 UTC (NOW in tests)
        # Yesterday: 1772582400 is not correct for NOW.
        # NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        # 2026-02-28 20:00:00 UTC
        yesterday_ts = str(int(datetime(2026, 2, 28, 20, 0, 0, tzinfo=timezone.utc).timestamp()))
        data = _make_yt_data(
            videos=[
                _make_video(
                    video_id="live_yesterday",
                    overlay_style="LIVE",
                    start_time=yesterday_ts,
                ),
            ],
        )
        with self._patch_get(data):
            streams = get_upcoming_streams(
                ["ch"], from_date=date(2026, 3, 1),
            )
        assert len(streams) == 1
        assert streams[0].video_id == "live_yesterday"
        assert streams[0].live is True

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
            "yt_live_scraper.scraper._http_get",
            return_value=_FakeResponse(html),
        ):
            streams = get_upcoming_streams(
                ["@ch1", "ch2"], from_date=date(2026, 1, 1),
            )
        assert len(streams) == 2

    def test_lockup_view_model_live(self):
        # Sample lockupViewModel structure
        lockup = {
            "contentId": "lockup123",
            "contentImage": {
                "thumbnailViewModel": {
                    "image": {
                        "sources": [{"url": "https://thumb/1.jpg"}]
                    },
                    "overlays": [
                        {
                            "thumbnailBottomOverlayViewModel": {
                                "badges": [
                                    {
                                        "thumbnailBadgeViewModel": {
                                            "badgeStyle": "THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE",
                                            "text": "LIVE"
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }
            },
            "metadata": {
                "lockupMetadataViewModel": {
                    "title": {"content": "Lockup Title"}
                }
            }
        }
        data = {
            "metadata": {"channelMetadataRenderer": {"title": "Ch", "externalId": "UC123"}},
            "contents": {
                "twoColumnBrowseResultsRenderer": {
                    "tabs": [
                        {
                            "tabRenderer": {
                                "title": "Live",
                                "content": {
                                    "richGridRenderer": {
                                        "contents": [
                                            {"richItemRenderer": {"content": {"lockupViewModel": lockup}}}
                                        ]
                                    }
                                }
                            }
                        }
                    ]
                }
            }
        }
        html = _wrap_as_html(data)
        with patch("yt_live_scraper.scraper._http_get", return_value=_FakeResponse(html)):
            streams = get_upcoming_streams(["ch"], from_date=date(2026, 1, 1))
        
        assert len(streams) == 1
        assert streams[0].video_id == "lockup123"
        assert streams[0].title == "Lockup Title"
        assert streams[0].live is True
        assert streams[0].thumbnail_url == "https://thumb/1.jpg"

    def _lockup_yt_data(self, lockup: dict) -> dict:
        return {
            "metadata": {
                "channelMetadataRenderer": {"title": "Ch", "externalId": "UC123"}
            },
            "contents": {
                "twoColumnBrowseResultsRenderer": {
                    "tabs": [
                        {
                            "tabRenderer": {
                                "title": "Live",
                                "content": {
                                    "richGridRenderer": {
                                        "contents": [
                                            {
                                                "richItemRenderer": {
                                                    "content": {
                                                        "lockupViewModel": lockup
                                                    }
                                                }
                                            }
                                        ]
                                    }
                                },
                            }
                        }
                    ]
                }
            },
        }

    def test_lockup_view_model_upcoming_fetches_start_time(self):
        # New markup: upcoming lockup with no inline time; the start time is
        # looked up from the watch page.
        lockup = _make_lockup(content_id="up123", badge_text="Upcoming")
        channel_html = _wrap_as_html(self._lockup_yt_data(lockup))
        watch_html = _wrap_player_response(_make_player_response())
        with patch(
            "yt_live_scraper.scraper._http_get",
            side_effect=[_FakeResponse(channel_html), _FakeResponse(watch_html)],
        ):
            streams = get_upcoming_streams(["ch"], from_date=date(2026, 1, 1))

        assert len(streams) == 1
        assert streams[0].video_id == "up123"
        assert streams[0].live is False
        assert streams[0].scheduled_start == datetime(
            2026, 6, 16, 21, 0, tzinfo=timezone.utc
        )

    def test_lockup_view_model_upcoming_dropped_when_no_start_time(self):
        lockup = _make_lockup(content_id="up123", badge_text="Upcoming")
        channel_html = _wrap_as_html(self._lockup_yt_data(lockup))
        with patch(
            "yt_live_scraper.scraper._http_get",
            side_effect=[
                _FakeResponse(channel_html),
                _FakeResponse("<html>no player response</html>"),
            ],
        ):
            streams = get_upcoming_streams(["ch"], from_date=date(2026, 1, 1))
        assert streams == []

    def test_strips_at_from_handle(self):
        data = _make_yt_data(videos=[_make_video(start_time="1772654400")])
        html = _wrap_as_html(data)
        with patch(
            "yt_live_scraper.scraper._http_get",
            return_value=_FakeResponse(html),
        ) as mock_get:
            get_upcoming_streams(["@myhandle"], from_date=date(2026, 1, 1))
        url = mock_get.call_args[0][0]
        assert "/@myhandle" in url

    def test_http_error_skips_channel(self, capsys):
        with patch(
            "yt_live_scraper.scraper._http_get",
            side_effect=requests.ConnectionError("fail"),
        ):
            streams = get_upcoming_streams(["badchannel"])
        assert streams == []
        assert "Warning" in capsys.readouterr().out

    def test_no_yt_initial_data_skips_channel(self, capsys):
        with patch(
            "yt_live_scraper.scraper._http_get",
            return_value=_FakeResponse("<html>no data</html>"),
        ):
            streams = get_upcoming_streams(["ch"])
        assert streams == []
        assert "Warning" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _get_lockup_overlay_style
# ---------------------------------------------------------------------------

class TestGetLockupOverlayStyle:
    def test_legacy_badge_style_live(self):
        vm = _make_lockup(badge_style="THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE")
        assert _get_lockup_overlay_style(vm) == "LIVE"

    def test_legacy_badge_style_upcoming(self):
        vm = _make_lockup(badge_style="THUMBNAIL_OVERLAY_BADGE_STYLE_UPCOMING")
        assert _get_lockup_overlay_style(vm) == "UPCOMING"

    def test_text_based_upcoming(self):
        # New markup: generic badgeStyle, status carried in the text field.
        vm = _make_lockup(badge_text="Upcoming")
        assert _get_lockup_overlay_style(vm) == "UPCOMING"

    def test_text_based_live(self):
        vm = _make_lockup(badge_text="LIVE")
        assert _get_lockup_overlay_style(vm) == "LIVE"

    def test_unrecognized_returns_none(self):
        vm = _make_lockup(badge_text="4K")
        assert _get_lockup_overlay_style(vm) is None

    def test_no_overlays_returns_none(self):
        assert _get_lockup_overlay_style({}) is None


# ---------------------------------------------------------------------------
# _fetch_scheduled_start
# ---------------------------------------------------------------------------

class TestFetchScheduledStart:
    def test_returns_start_from_watch_page(self):
        html = _wrap_player_response(_make_player_response())
        with patch(
            "yt_live_scraper.scraper._http_get",
            return_value=_FakeResponse(html),
        ):
            result = _fetch_scheduled_start("vid")
        assert result == datetime(2026, 6, 16, 21, 0, tzinfo=timezone.utc)

    def test_returns_none_on_http_error(self):
        with patch(
            "yt_live_scraper.scraper._http_get",
            side_effect=requests.ConnectionError("fail"),
        ):
            assert _fetch_scheduled_start("vid") is None

    def test_returns_none_when_no_player_response(self):
        with patch(
            "yt_live_scraper.scraper._http_get",
            return_value=_FakeResponse("<html>nothing</html>"),
        ):
            assert _fetch_scheduled_start("vid") is None

    def test_falls_back_to_offline_slate_scheduled_start(self):
        # No liveBroadcastDetails — start time only in the offline slate.
        html = _wrap_player_response(_make_offline_slate_response("1781643600"))
        with patch(
            "yt_live_scraper.scraper._http_get",
            return_value=_FakeResponse(html),
        ):
            result = _fetch_scheduled_start("vid")
        assert result == datetime.fromtimestamp(1781643600, tz=timezone.utc)


# ---------------------------------------------------------------------------
# _extract_scheduled_start
# ---------------------------------------------------------------------------

class TestExtractScheduledStart:
    def test_extracts_offline_slate_timestamp(self):
        result = _extract_scheduled_start(_make_offline_slate_response("1781643600"))
        assert result == datetime.fromtimestamp(1781643600, tz=timezone.utc)

    def test_returns_none_when_missing(self):
        assert _extract_scheduled_start({}) is None
        assert _extract_scheduled_start({"playabilityStatus": {}}) is None


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
            "yt_live_scraper.scraper._http_get",
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
            "yt_live_scraper.scraper._http_get",
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
# get_channel
# ---------------------------------------------------------------------------

class TestGetChannel:
    def test_returns_name_on_200(self):
        data = _make_yt_data(channel_name="My Awesome Channel")
        html = _wrap_as_html(data)
        with patch("yt_live_scraper.scraper._http_get") as mocked_get:
            mocked_get.return_value = _FakeResponse(html, status_code=200)
            assert get_channel("anychannel") == "My Awesome Channel"

    def test_returns_none_on_404(self):
        with patch("yt_live_scraper.scraper._http_get") as mocked_get:
            mocked_get.return_value = _FakeResponse("", status_code=404)
            assert get_channel("nonexistent") is None

    def test_returns_none_on_exception(self):
        with patch("yt_live_scraper.scraper._http_get") as mocked_get:
            mocked_get.side_effect = requests.RequestException()
            assert get_channel("errorchannel") is None

    def test_returns_none_on_invalid_html(self):
        with patch("yt_live_scraper.scraper._http_get") as mocked_get:
            mocked_get.return_value = _FakeResponse("<html>no data</html>", status_code=200)
            assert get_channel("nodatachannel") is None


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
            "yt_live_scraper.scraper._http_get",
            return_value=_FakeResponse(html),
        ):
            from yt_live_scraper.cli import main
            main(["ch", "--from", "2026-01-01"])
        out = capsys.readouterr().out
        assert "MyCh" in out
        assert "Cool Stream" in out

    def test_json_output(self, capsys):
        data = _make_yt_data(
            channel_id="UC123",
            videos=[_make_video(start_time="1772654400")],
        )
        html = _wrap_as_html(data)
        with patch(
            "yt_live_scraper.scraper._http_get",
            return_value=_FakeResponse(html),
        ):
            from yt_live_scraper.cli import main
            main(["ch", "--from", "2026-01-01", "--json"])
        parsed = json.loads(capsys.readouterr().out)
        assert len(parsed) == 1
        assert "video_id" in parsed[0]
        assert parsed[0]["channel_id"] == "UC123"
        assert "channel_thumbnail_url" in parsed[0]
        assert "live" in parsed[0]
        assert parsed[0]["scheduled_start"] == "2026-03-04T20:00:00+00:00"
        # We don't check stream_id here because it's only for live streams in this test case
        # Wait, the test case says s.live is False because overlay_style is not LIVE
        assert parsed[0]["live"] is False
        assert parsed[0]["stream_id"] is None

    def test_json_output_live(self, capsys):
        data = _make_yt_data(
            channel_id="UC123",
            videos=[_make_video(overlay_style="LIVE", start_time=None)],
        )
        html = _wrap_as_html(data)
        with patch(
            "yt_live_scraper.scraper._http_get",
            return_value=_FakeResponse(html),
        ):
            from yt_live_scraper.cli import main
            main(["ch", "--from", "2026-01-01", "--json"])
        parsed = json.loads(capsys.readouterr().out)
        assert len(parsed) == 1
        assert parsed[0]["live"] is True
        assert parsed[0]["stream_id"] == "abc123"

    def test_no_streams_exits_zero(self, capsys):
        data = _make_yt_data(videos=[_make_video(overlay_style="DEFAULT", start_time=None)])
        html = _wrap_as_html(data)
        with patch(
            "yt_live_scraper.scraper._http_get",
            return_value=_FakeResponse(html),
        ):
            from yt_live_scraper.cli import main
            with pytest.raises(SystemExit) as exc_info:
                main(["ch", "--from", "2026-01-01"])
            assert exc_info.value.code == 0
        assert "No upcoming" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# HTTP session: rotated User-Agent, connection reuse, retry/backoff
# ---------------------------------------------------------------------------

class TestHttpGet:
    def test_uses_a_pooled_user_agent(self):
        from yt_live_scraper.scraper import _USER_AGENTS, _http_get

        with patch("yt_live_scraper.scraper._SESSION.get") as mock_get:
            mock_get.return_value = _FakeResponse("", status_code=200)
            _http_get("https://example.com", timeout=5)

        _args, kwargs = mock_get.call_args
        assert kwargs["headers"]["User-Agent"] in _USER_AGENTS
        assert kwargs["headers"]["Accept-Language"] == "en-US,en;q=0.9"
        assert "SOCS" in kwargs["cookies"]
        assert kwargs["timeout"] == 5

    def test_rotates_user_agent_between_calls(self):
        from yt_live_scraper.scraper import _USER_AGENTS, _http_get

        ua_a, ua_b = _USER_AGENTS[0], _USER_AGENTS[1]
        with (
            patch("yt_live_scraper.scraper._SESSION.get") as mock_get,
            patch("yt_live_scraper.scraper.random.choice", side_effect=[ua_a, ua_b]),
        ):
            mock_get.return_value = _FakeResponse("", status_code=200)
            _http_get("https://example.com", timeout=5)
            _http_get("https://example.com", timeout=5)

        seen = [c.kwargs["headers"]["User-Agent"] for c in mock_get.call_args_list]
        assert seen == [ua_a, ua_b]

    def test_session_is_shared_with_retry_adapter(self):
        import requests as _requests

        from yt_live_scraper.scraper import _SESSION

        assert isinstance(_SESSION, _requests.Session)
        retry = _SESSION.get_adapter("https://www.youtube.com").max_retries
        assert retry.total == 3
        assert 429 in retry.status_forcelist
        assert 503 in retry.status_forcelist
        assert retry.respect_retry_after_header is True


# ---------------------------------------------------------------------------
# Start-time cache
# ---------------------------------------------------------------------------

class TestStartTimeCache:
    def test_second_lookup_served_from_cache(self):
        html = _wrap_player_response(_make_player_response())
        with patch(
            "yt_live_scraper.scraper._http_get",
            return_value=_FakeResponse(html),
        ) as mock_get:
            first = _fetch_scheduled_start("vidcache")
            second = _fetch_scheduled_start("vidcache")

        assert first == second
        assert mock_get.call_count == 1

    def test_failures_are_not_cached(self):
        with patch(
            "yt_live_scraper.scraper._http_get",
            side_effect=requests.ConnectionError("fail"),
        ) as mock_get:
            assert _fetch_scheduled_start("vidfail") is None
            assert _fetch_scheduled_start("vidfail") is None

        assert mock_get.call_count == 2

    def test_cache_expires_after_ttl(self):
        html = _wrap_player_response(_make_player_response())
        with patch(
            "yt_live_scraper.scraper._http_get",
            return_value=_FakeResponse(html),
        ) as mock_get:
            with patch("yt_live_scraper.scraper.time.monotonic", return_value=1000.0):
                _fetch_scheduled_start("vidttl")
            with patch(
                "yt_live_scraper.scraper.time.monotonic", return_value=1000.0 + 901.0
            ):
                _fetch_scheduled_start("vidttl")

        assert mock_get.call_count == 2
