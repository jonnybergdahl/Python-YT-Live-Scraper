# yt-live-scraper

A Python library and CLI tool for scraping YouTube channels for live and upcoming stream events.

## Installation

```bash
pip install yt-live-scraper
```

## CLI Usage

```bash
# Show upcoming streams for one or more channels
yt-live-scraper @home_assistant @superhousetv

# Output as JSON
yt-live-scraper @home_assistant --json

# Only show streams from a specific date onwards
yt-live-scraper @home_assistant --from 2026-03-01
```

## Library Usage

### Get upcoming streams

```python
from yt_live_scraper import get_upcoming_streams

streams = get_upcoming_streams(["@home_assistant", "@superhousetv"])
for stream in streams:
    print(stream)
```

### Check if a stream is live

```python
from yt_live_scraper import is_stream_live

if is_stream_live("dQw4w9WgXcQ"):
    print("Stream is live!")
```

### UpcomingStream fields

Each `UpcomingStream` object contains:

| Field                   | Type       | Description                          |
| ----------------------- | ---------- | ------------------------------------ |
| `channel`               | `str`      | Channel display name                 |
| `channel_thumbnail_url` | `str`      | URL of the channel's avatar image    |
| `video_id`              | `str`      | YouTube video ID                     |
| `title`                 | `str`      | Stream title                         |
| `scheduled_start`       | `datetime` | Scheduled start time (UTC)           |
| `url`                   | `str`      | Full YouTube watch URL               |
| `thumbnail_url`         | `str`      | URL of the stream's thumbnail image  |
| `live`                  | `bool`     | `True` if the stream is currently live |

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest
```

## License

[MIT](LICENSE)
