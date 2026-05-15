from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from map_vod_ingest import (
    build_match_id,
    infer_mp_id,
    CatalogMatch,
    build_yt_dlp_auth_args,
    build_yt_dlp_ejs_args,
    compute_file_stem,
    extract_video_item,
    get_video_mark,
    init_db,
    is_known_non_map_video,
    list_channel_video_ids,
    parse_best_effort_meta,
    upsert_game,
    upsert_map,
    upsert_match,
    upsert_tournament,
    upsert_video,
    upsert_video_mark,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Soft metadata check for ALGS Map VODs: fill SQLite only, no media download."
    )
    parser.add_argument(
        "--channel-url",
        default="https://www.youtube.com/@algs_vods/videos",
        help="YouTube channel videos URL.",
    )
    parser.add_argument(
        "--db-path",
        default="output/youtube_ingest/tournaments.sqlite",
        help="SQLite DB path.",
    )
    parser.add_argument(
        "--output-dir",
        default="ffmpeg_downloader/records",
        help="Virtual output folder used to populate games.output_path (files are not created).",
    )
    parser.add_argument(
        "--duration-sec",
        type=int,
        default=1200,
        help="Planned clip duration in seconds.",
    )
    parser.add_argument(
        "--limit-videos",
        type=int,
        default=None,
        help="Optional cap for amount of channel videos to inspect.",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=1,
        help="1-based start index in listed videos.",
    )
    parser.add_argument(
        "--end-index",
        type=int,
        default=None,
        help="1-based inclusive end index in listed videos.",
    )
    parser.add_argument(
        "--cookies-file",
        default=None,
        help="Path to Netscape cookies.txt for yt-dlp.",
    )
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="Browser profile name for yt-dlp cookie extraction.",
    )
    parser.add_argument(
        "--js-runtimes",
        default="deno",
        help="yt-dlp JS runtime selector (default: deno).",
    )
    parser.add_argument(
        "--remote-components",
        default="ejs:npm",
        help="yt-dlp remote components source (default: ejs:npm).",
    )
    parser.add_argument(
        "--disable-ejs",
        action="store_true",
        help="Disable EJS runtime arguments.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Recheck videos even if they are marked viewed/completed.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    auth_args = build_yt_dlp_auth_args(args.cookies_file, args.cookies_from_browser)
    ejs_args = build_yt_dlp_ejs_args(args.js_runtimes, args.remote_components, args.disable_ejs)

    db_path = Path(args.db_path)
    output_dir = Path(args.output_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[soft-check] channel: {args.channel_url}")
    print(f"[soft-check] db: {db_path}")
    print(f"[soft-check] output template dir: {output_dir}")

    with sqlite3.connect(db_path) as conn:
        init_db(conn)
        video_ids = list_channel_video_ids(args.channel_url, args.limit_videos, auth_args, ejs_args)
        total_ids = len(video_ids)

        start = max(1, int(args.start_index))
        end = total_ids if args.end_index is None else max(start, min(total_ids, int(args.end_index)))
        scoped_video_ids = video_ids[start - 1 : end]

        print(f"[soft-check] listed videos: {total_ids}")
        print(f"[soft-check] processing range: {start}..{end} ({len(scoped_video_ids)} videos)")

        parsed_videos = 0
        skipped_cached = 0
        failed_videos = 0
        planned_games = 0

        for offset, video_id in enumerate(scoped_video_ids, start=start):
            if not args.force_refresh:
                mark = get_video_mark(conn, video_id)
                if mark in ("viewed", "completed"):
                    skipped_cached += 1
                    print(f"[video {offset}/{total_ids}] skip cached {mark} video_id={video_id}")
                    conn.commit()
                    continue
                if is_known_non_map_video(conn, video_id):
                    skipped_cached += 1
                    print(f"[video {offset}/{total_ids}] skip cached non-map video_id={video_id}")
                    conn.commit()
                    continue

            print(f"[video {offset}/{total_ids}] loading metadata for video_id={video_id}...")
            try:
                item = extract_video_item(video_id, auth_args, ejs_args)
            except RuntimeError as exc:
                failed_videos += 1
                print(f"  -> metadata failed: {exc}")
                conn.commit()
                continue

            has_map = "map" in item.title.lower()
            upsert_video(conn, item, has_map_keyword=has_map)
            if not has_map:
                upsert_video_mark(conn, video_id, "viewed", "soft_check_non_map_title")
                print("  -> skipped non-map title")
                conn.commit()
                continue

            parsed = parse_best_effort_meta(item)
            if not parsed:
                upsert_video_mark(conn, video_id, "viewed", "soft_check_missing_games")
                print("  -> skipped: no Game timestamps found")
                conn.commit()
                continue

            match_info = CatalogMatch(catalog_tournament_id=None, catalog_tournament_name=None, confidence=0.0)
            tournament_id = upsert_tournament(conn, item.youtube_video_id, parsed, item.published_at, match_info)
            match_id = build_match_id(item.youtube_video_id)
            mp_id = infer_mp_id(item)
            parsed_videos += 1
            print(
                "  -> parsed: "
                f"region={parsed.region}, split={parsed.split_number}, day={parsed.day_number}, "
                f"year=Y{parsed.year_number}, games={len(parsed.games)}"
            )
            if parsed.missing_fields:
                print(f"  -> partial metadata: missing={','.join(parsed.missing_fields)}")
            upsert_match(conn, item, tournament_id, parsed, match_info, "viewed")

            for game in parsed.games:
                file_stem = compute_file_stem(
                    year_number=parsed.year_number,
                    split_number=parsed.split_number,
                    region=parsed.region,
                    day_number=parsed.day_number,
                    game_number=game.game_number,
                    youtube_video_id=item.youtube_video_id,
                )
                output_file = output_dir / f"{file_stem}.mp4"
                game_id = f"{item.youtube_video_id}_g{game.game_number}"
                upsert_game(
                    conn=conn,
                    game_id=game_id,
                    tournament_id=tournament_id,
                    video_id=item.youtube_video_id,
                    game_number=game.game_number,
                    start_sec=game.start_sec,
                    duration_sec=args.duration_sec,
                    output_filename=output_file.name,
                    output_path=str(output_file),
                    download_status="planned",
                    error_message=None,
                )
                upsert_map(conn, match_id, mp_id, game.game_number, teams_json="[]")
                planned_games += 1

            upsert_video_mark(conn, item.youtube_video_id, "viewed", "soft_check_planned_only")
            conn.commit()

        print("========== soft-check summary ==========")
        print(f"videos parsed: {parsed_videos}")
        print(f"videos failed on metadata: {failed_videos}")
        print(f"videos skipped from cache: {skipped_cached}")
        print(f"games planned in DB: {planned_games}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

