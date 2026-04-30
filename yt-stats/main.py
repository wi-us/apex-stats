from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

from dotenv import load_dotenv

from youtube_stats_collector import YouTubeStatsCollector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect deep YouTube analytics for your own channel."
    )
    parser.add_argument(
        "--start-date",
        help="Report start date (YYYY-MM-DD). Default: 30 days ago.",
    )
    parser.add_argument(
        "--end-date",
        help="Report end date (YYYY-MM-DD). Default: yesterday.",
    )
    parser.add_argument(
        "--top-videos-limit",
        type=int,
        default=None,
        help="Max number of rows in top videos report (default from env or 200).",
    )
    parser.add_argument(
        "--all-time",
        action="store_true",
        help="Use full channel history (channel creation date -> end date).",
    )
    return parser.parse_args()


def default_dates() -> tuple[str, str]:
    today = date.today()
    yesterday = today - timedelta(days=1)
    month_ago = today - timedelta(days=30)
    return month_ago.isoformat(), yesterday.isoformat()


def main() -> int:
    load_dotenv()
    args = parse_args()

    default_start, default_end = default_dates()
    start_date = (
        args.start_date
        or os.getenv("YT_DEFAULT_START_DATE")
        or default_start
    )
    end_date = (
        args.end_date
        or os.getenv("YT_DEFAULT_END_DATE")
        or default_end
    )

    top_videos_limit = args.top_videos_limit or int(os.getenv("YT_TOP_VIDEOS_LIMIT", "200"))

    client_secrets_file = os.getenv("YT_CLIENT_SECRETS_FILE", "./client_secret.json")
    token_file = os.getenv("YT_TOKEN_FILE", "./token.json")
    db_path = os.getenv("YT_DB_PATH", "./yt_stats.sqlite")

    collector = YouTubeStatsCollector(
        client_secrets_file=client_secrets_file,
        token_file=token_file,
        db_path=db_path,
    )

    print("Authenticating with Google OAuth...")
    collector.authenticate()
    if args.all_time:
        start_date = collector.get_channel_start_date()
    print(f"Channel: {collector.channel_title} ({collector.channel_id})")
    print(f"Date range: {start_date} -> {end_date}")

    reports = collector.collect_deep_analytics(
        start_date=start_date,
        end_date=end_date,
        top_videos_limit=top_videos_limit,
    )

    db_file, run_id = collector.save_reports_to_sqlite(
        reports=reports,
        start_date=start_date,
        end_date=end_date,
    )

    print(f"Done. SQLite updated: {db_file}")
    print(f"Run ID: {run_id}")
    for report_name, report in reports.items():
        print(f"- {report_name}: {len(report.rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
