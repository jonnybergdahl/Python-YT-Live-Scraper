"""CLI entry point for yt-live-scraper."""

from __future__ import annotations

import argparse
import sys
from datetime import date

from yt_live_scraper.scraper import get_upcoming_streams


def main(argv: list[str] | None = None) -> None:
    """Run the yt-live-scraper command-line interface.

    :param argv: Argument list to parse. Defaults to ``sys.argv[1:]``.
    """
    parser = argparse.ArgumentParser(
        description="Show upcoming YouTube live streams for given channels.",
    )
    parser.add_argument(
        "channels",
        nargs="+",
        help="YouTube channel handles (e.g. @superhousetv @home_assistant)",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="Only show streams on or after this date (default: today)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    args = parser.parse_args(argv)

    streams = get_upcoming_streams(args.channels, from_date=args.from_date)

    if not streams:
        print("No upcoming live streams found.")
        sys.exit(0)

    if args.json:
        import json

        print(
            json.dumps(
                [
                    {
                        "channel": s.channel,
                        "channel_thumbnail_url": s.channel_thumbnail_url,
                        "title": s.title,
                        "video_id": s.video_id,
                        "scheduled_start": s.scheduled_start.isoformat(),
                        "url": s.url,
                        "thumbnail_url": s.thumbnail_url,
                        "live": s.live,
                    }
                    for s in streams
                ],
                indent=2,
            )
        )
    else:
        for stream in streams:
            print(stream)


if __name__ == "__main__":
    main()
