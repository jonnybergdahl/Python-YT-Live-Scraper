"""Scrape YouTube channel pages for live and upcoming stream events."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

import requests

_YTINITIALDDATA_RE = re.compile(r"var\s+ytInitialData\s*=\s*")
_YTPLAYERRESPONSE_RE = re.compile(r"var\s+ytInitialPlayerResponse\s*=\s*")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Bypass YouTube's GDPR/cookie consent page.
_COOKIES = {
    "SOCS": "CAESEwgDEgk2NTc0MjcyNjQaAmVuIAEaBgiA_JCXBQ",
    "CONSENT": "PENDING+987",
}


@dataclass
class UpcomingStream:
    """A YouTube live or upcoming stream.

    :param channel: Channel display name.
    :param channel_id: YouTube channel ID.
    :param channel_thumbnail_url: URL of the channel's avatar image.
    :param video_id: YouTube video ID.
    :param title: Stream title.
    :param scheduled_start: Scheduled start time in UTC.
    :param url: Full YouTube watch URL.
    :param thumbnail_url: URL of the stream's thumbnail image.
    :param live: ``True`` if the stream is currently live.
    """

    channel: str
    channel_id: str
    channel_thumbnail_url: str
    video_id: str
    title: str
    scheduled_start: datetime
    url: str
    thumbnail_url: str
    live: bool

    def __str__(self) -> str:
        """Return a human-readable summary of the stream.

        :returns: Formatted string with channel, title, local time and live status.
        """
        local = self.scheduled_start.astimezone()
        status = " [LIVE]" if self.live else ""
        stream_id = f" ({self.video_id})" if self.live else ""
        return f"{self.channel}: {self.title} — {local:%Y-%m-%d %H:%M %Z}{status}{stream_id}"

    @staticmethod
    def exists(channel_handle: str, *, timeout: float = 10) -> bool:
        """Check if a YouTube channel handle exists.

        :param channel_handle: Channel handle (with or without leading ``@``).
        :param timeout: HTTP request timeout in seconds.
        :returns: ``True`` if the channel exists, ``False`` otherwise.
        """
        handle = channel_handle.lstrip("@")
        url = f"https://www.youtube.com/@{handle}"
        try:
            resp = requests.get(
                url, headers=_HEADERS, cookies=_COOKIES, timeout=timeout,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False


@dataclass
class ChannelInfo:
    """Basic metadata for a YouTube channel.

    :param name: Channel display name.
    :param channel_id: YouTube channel ID.
    :param thumbnail_url: URL of the channel's avatar image.
    """

    name: str
    channel_id: str
    thumbnail_url: str


def get_channel(channel_handle: str, *, timeout: float = 10) -> str | None:
    """Get the display name of a YouTube channel from its handle.

    :param channel_handle: Channel handle (with or without leading ``@``).
    :param timeout: HTTP request timeout in seconds.
    :returns: The channel's display name, or ``None`` if it does not exist.
    """
    info = get_channel_info(channel_handle, timeout=timeout)
    return info.name if info else None


def get_channel_info(
    channel_handle: str, *, timeout: float = 10,
) -> ChannelInfo | None:
    """Get basic metadata for a YouTube channel from its handle.

    :param channel_handle: Channel handle (with or without leading ``@``).
    :param timeout: HTTP request timeout in seconds.
    :returns: A :class:`ChannelInfo` with the channel's name, ID and avatar
              thumbnail URL, or ``None`` if the channel does not exist.
    """
    handle = channel_handle.lstrip("@")
    url = f"https://www.youtube.com/@{handle}"
    try:
        resp = requests.get(
            url, headers=_HEADERS, cookies=_COOKIES, timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        data = _extract_yt_initial_data(resp.text)
        name, channel_id, thumbnail_url = _parse_channel_info(data)
        return ChannelInfo(
            name=name,
            channel_id=channel_id,
            thumbnail_url=thumbnail_url,
        )
    except (requests.RequestException, ValueError):
        return None


def _extract_yt_initial_data(html: str) -> dict:
    """Extract the ``ytInitialData`` JSON object from a YouTube page.

    :param html: Raw HTML source of a YouTube page.
    :returns: Parsed ``ytInitialData`` dictionary.
    :raises ValueError: If the ``ytInitialData`` variable is not found.
    """
    m = _YTINITIALDDATA_RE.search(html)
    if not m:
        raise ValueError("Could not find ytInitialData in page source")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(html, m.end())
    return obj


def _find_streams_tab(data: dict) -> dict | None:
    """Locate the "Live" or "Streams" tab in ``ytInitialData``.

    :param data: Parsed ``ytInitialData`` dictionary.
    :returns: The tab renderer dictionary, or ``None`` if not found.
    """
    tabs = (
        data.get("contents", {})
        .get("twoColumnBrowseResultsRenderer", {})
        .get("tabs", [])
    )
    for tab in tabs:
        renderer = tab.get("tabRenderer", {})
        if renderer.get("title", "").lower() in ("live", "streams"):
            return renderer
    return None


def _parse_channel_info(data: dict) -> tuple[str, str, str]:
    """Extract the channel name, ID and avatar thumbnail URL.

    :param data: Parsed ``ytInitialData`` dictionary.
    :returns: A tuple of ``(channel_name, channel_id, channel_thumbnail_url)``.
              Defaults to ``("Unknown", "", "")`` when metadata is missing.
    """
    metadata = data.get("metadata", {}).get("channelMetadataRenderer", {})
    name = metadata.get("title", "Unknown")
    channel_id = metadata.get("externalId", "")
    avatars = metadata.get("avatar", {}).get("thumbnails", [])
    thumbnail_url = avatars[-1]["url"] if avatars else ""
    return name, channel_id, thumbnail_url


def _get_overlay_style(video: dict) -> str | None:
    """Return the thumbnail overlay style for a video renderer.

    :param video: A ``videoRenderer`` dictionary from YouTube's data.
    :returns: The overlay style string (e.g. ``"LIVE"``, ``"UPCOMING"``,
              ``"DEFAULT"``), or ``None`` if no overlay is present.
    """
    for o in video.get("thumbnailOverlays", []):
        style = o.get("thumbnailOverlayTimeStatusRenderer", {}).get("style")
        if style:
            return style
    return None


def _get_lockup_overlay_style(vm: dict) -> str | None:
    """Return the overlay style for a lockup view model.

    :param vm: A ``lockupViewModel`` dictionary.
    :returns: The overlay style string (e.g. ``"LIVE"``, ``"UPCOMING"``),
              or ``None`` if no recognizable overlay is present.
    """
    img = vm.get("contentImage", {}).get("thumbnailViewModel", {})
    for o in img.get("overlays", []):
        badges = o.get("thumbnailBottomOverlayViewModel", {}).get("badges", [])
        for b in badges:
            badge = b.get("thumbnailBadgeViewModel", {})
            style = badge.get("badgeStyle", "")
            if "STYLE_LIVE" in style:
                return "LIVE"
            if "STYLE_UPCOMING" in style:
                return "UPCOMING"
            # Newer YouTube markup uses a generic ``..._STYLE_DEFAULT`` badge
            # and carries the status in the badge ``text`` instead.
            text = (badge.get("text") or "").strip().lower()
            if text == "live":
                return "LIVE"
            if text == "upcoming":
                return "UPCOMING"
    return None


def _parse_lockup(
    vm: dict,
    channel: str,
    channel_id: str,
    channel_thumbnail_url: str,
    now: datetime,
    *,
    timeout: float = 15,
) -> UpcomingStream | None:
    """Parse a ``lockupViewModel`` into an :class:`UpcomingStream`.

    :param vm: A ``lockupViewModel`` dictionary.
    :param channel: The channel display name.
    :param channel_id: The YouTube channel ID.
    :param channel_thumbnail_url: URL of the channel's avatar image.
    :param now: Current UTC time.
    :param timeout: HTTP request timeout for the start-time lookup.
    :returns: An :class:`UpcomingStream` instance, or ``None``.
    """
    style = _get_lockup_overlay_style(vm)
    if style not in ("LIVE", "UPCOMING"):
        return None

    video_id = vm.get("contentId", "")
    metadata = vm.get("metadata", {}).get("lockupMetadataViewModel", {})
    title = metadata.get("title", {}).get("content", "")

    # Try to extract a higher-res thumbnail.
    img = vm.get("contentImage", {}).get("thumbnailViewModel", {}).get("image", {})
    sources = img.get("sources", [])
    thumbnail_url = sources[-1]["url"] if sources else ""

    # The lockup grid carries no machine-readable start time. For LIVE we use
    # now; for UPCOMING we look it up from the watch page.
    if style == "LIVE":
        start_time = now
    else:
        start_time = (
            _fetch_scheduled_start(video_id, timeout=timeout) if video_id else None
        )
        if start_time is None:
            return None

    return UpcomingStream(
        channel=channel,
        channel_id=channel_id,
        channel_thumbnail_url=channel_thumbnail_url,
        video_id=video_id,
        title=title,
        scheduled_start=start_time,
        url=f"https://www.youtube.com/watch?v={video_id}",
        thumbnail_url=thumbnail_url,
        live=(style == "LIVE"),
    )


def _parse_stream(
    video: dict, channel: str, channel_id: str, channel_thumbnail_url: str, now: datetime,
) -> UpcomingStream | None:
    """Parse a single ``videoRenderer`` into an :class:`UpcomingStream`.

    :param video: A ``videoRenderer`` dictionary from YouTube's data.
    :param channel: The channel display name.
    :param channel_id: The YouTube channel ID.
    :param channel_thumbnail_url: URL of the channel's avatar image.
    :param now: Current UTC time, used as the start time for live streams
                that lack ``upcomingEventData``.
    :returns: An :class:`UpcomingStream` instance, or ``None`` if the video
              is neither live nor upcoming.
    """
    overlay_style = _get_overlay_style(video)
    upcoming = video.get("upcomingEventData")
    is_live = overlay_style == "LIVE"
    is_upcoming = overlay_style == "UPCOMING" or upcoming is not None

    if not is_live and not is_upcoming:
        return None

    video_id = video.get("videoId", "")
    title_runs = video.get("title", {}).get("runs", [])
    title = title_runs[0]["text"] if title_runs else ""

    start_time = None
    if upcoming:
        ts = upcoming.get("startTime", "")
        if ts:
            start_time = datetime.fromtimestamp(int(ts), tz=timezone.utc)

    # For live streams without upcomingEventData, use "now" as the start time.
    if start_time is None:
        if is_live:
            start_time = now
        else:
            return None

    thumbnails = video.get("thumbnail", {}).get("thumbnails", [])
    thumbnail_url = thumbnails[-1]["url"] if thumbnails else ""

    return UpcomingStream(
        channel=channel,
        channel_id=channel_id,
        channel_thumbnail_url=channel_thumbnail_url,
        video_id=video_id,
        title=title,
        scheduled_start=start_time,
        url=f"https://www.youtube.com/watch?v={video_id}",
        thumbnail_url=thumbnail_url,
        live=is_live,
    )


@dataclass
class StreamLiveStatus:
    """Result of checking whether a stream is live.

    :param is_live: ``True`` if the stream is currently live.
    :param actual_start: The actual broadcast start time in UTC, extracted from
                         ``liveBroadcastDetails.startTimestamp``.  ``None`` when
                         the timestamp is unavailable or the stream is not live.
    """

    is_live: bool
    actual_start: datetime | None = None


def _extract_actual_start(data: dict) -> datetime | None:
    """Extract the broadcast start timestamp from a player response.

    :param data: Parsed ``ytInitialPlayerResponse`` dictionary.
    :returns: The start time as a UTC :class:`datetime`, or ``None`` if
              the field is missing or cannot be parsed.
    """
    try:
        ts = (
            data.get("microformat", {})
            .get("playerMicroformatRenderer", {})
            .get("liveBroadcastDetails", {})
            .get("startTimestamp", "")
        )
        if ts:
            return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        pass
    return None


def _extract_scheduled_start(data: dict) -> datetime | None:
    """Extract the scheduled start time of an upcoming stream.

    Upcoming streams that have not begun yet often lack
    ``liveBroadcastDetails.startTimestamp`` and instead expose a Unix
    timestamp in the offline-slate renderer of ``playabilityStatus``.

    :param data: Parsed ``ytInitialPlayerResponse`` dictionary.
    :returns: The scheduled start time as a UTC :class:`datetime`, or
              ``None`` if the field is missing or cannot be parsed.
    """
    try:
        ts = (
            data.get("playabilityStatus", {})
            .get("liveStreamability", {})
            .get("liveStreamabilityRenderer", {})
            .get("offlineSlate", {})
            .get("liveStreamOfflineSlateRenderer", {})
            .get("scheduledStartTime", "")
        )
        if ts:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (ValueError, TypeError):
        pass
    return None


def _extract_player_response(html: str) -> dict:
    """Extract the ``ytInitialPlayerResponse`` JSON object from a YouTube page.

    :param html: Raw HTML source of a YouTube video page.
    :returns: Parsed ``ytInitialPlayerResponse`` dictionary.
    :raises ValueError: If the ``ytInitialPlayerResponse`` variable is not found.
    """
    m = _YTPLAYERRESPONSE_RE.search(html)
    if not m:
        raise ValueError("Could not find ytInitialPlayerResponse in page source")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(html, m.end())
    return obj


def _fetch_scheduled_start(
    video_id: str, *, timeout: float = 10,
) -> datetime | None:
    """Fetch the scheduled start time for an upcoming stream.

    The channel grid's ``lockupViewModel`` no longer carries a machine-readable
    start time, so the value is read from the video's watch page
    (``liveBroadcastDetails.startTimestamp``).

    :param video_id: YouTube video ID to look up.
    :param timeout: HTTP request timeout in seconds.
    :returns: The scheduled start time in UTC, or ``None`` on network/parse
              errors or when the timestamp is unavailable.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        resp = requests.get(
            url, headers=_HEADERS, cookies=_COOKIES, timeout=timeout,
        )
        resp.raise_for_status()
        data = _extract_player_response(resp.text)
    except (requests.RequestException, ValueError):
        return None
    return _extract_actual_start(data) or _extract_scheduled_start(data)


def is_stream_live(video_id: str, *, timeout: float = 10) -> StreamLiveStatus:
    """Check whether a YouTube stream is currently live.

    Fetches the video page and inspects the player response for the
    ``isLive`` flag inside ``videoDetails``.  When the stream is live,
    the actual broadcast start time is extracted from
    ``liveBroadcastDetails.startTimestamp`` if available.

    :param video_id: YouTube video ID to check.
    :param timeout: HTTP request timeout in seconds.
    :returns: A :class:`StreamLiveStatus` with the live flag and, when
              available, the actual start time.  On network or parse errors
              the status defaults to not-live with no start time.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        resp = requests.get(
            url, headers=_HEADERS, cookies=_COOKIES, timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return StreamLiveStatus(is_live=False)

    try:
        data = _extract_player_response(resp.text)
    except ValueError:
        return StreamLiveStatus(is_live=False)

    is_live = data.get("videoDetails", {}).get("isLive") is True
    actual_start = _extract_actual_start(data) if is_live else None
    return StreamLiveStatus(is_live=is_live, actual_start=actual_start)


def get_upcoming_streams(
    channels: list[str],
    *,
    from_date: date | None = None,
    timeout: float = 15,
) -> list[UpcomingStream]:
    """Fetch upcoming live streams for a list of YouTube channel handles.

    :param channels: Channel handles (with or without leading ``@``).
    :param from_date: Only include streams on or after this date.
                      Defaults to today (UTC).
    :param timeout: HTTP request timeout in seconds.
    :returns: List of :class:`UpcomingStream` objects sorted by scheduled
              start time.
    """
    now = datetime.now(tz=timezone.utc)
    if from_date is None:
        from_date = now.date()
    cutoff = datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc)
    results: list[UpcomingStream] = []

    for handle in channels:
        handle = handle.lstrip("@")
        url = f"https://www.youtube.com/@{handle}/streams"

        try:
            resp = requests.get(
                url, headers=_HEADERS, cookies=_COOKIES, timeout=timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"Warning: failed to fetch {url}: {exc}")
            continue

        try:
            data = _extract_yt_initial_data(resp.text)
        except ValueError:
            print(f"Warning: no ytInitialData found for @{handle}")
            continue

        channel_name, channel_id, channel_thumb = _parse_channel_info(data)
        tab = _find_streams_tab(data)
        if tab is None:
            continue

        contents = (
            tab.get("content", {})
            .get("richGridRenderer", {})
            .get("contents", [])
        )

        for item in contents:
            rir = item.get("richItemRenderer", {})
            content = rir.get("content", {})
            video = content.get("videoRenderer")
            lockup = content.get("lockupViewModel")

            stream = None
            if video:
                stream = _parse_stream(video, channel_name, channel_id, channel_thumb, now)
            elif lockup:
                stream = _parse_lockup(
                    lockup, channel_name, channel_id, channel_thumb, now,
                    timeout=timeout,
                )

            if stream and (stream.live or stream.scheduled_start >= cutoff):
                results.append(stream)

    results.sort(key=lambda s: s.scheduled_start)
    return results
