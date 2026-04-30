import argparse
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from runtime_paths import load_runtime_paths

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_PATHS = load_runtime_paths(PROJECT_ROOT)
DEFAULT_TOURNAMENTS_DB = RUNTIME_PATHS["databases"]["preferred_tournaments"]
DEFAULT_RECORDS_DIR = RUNTIME_PATHS["media"]["records_dir"]
DEFAULT_MAP_START_DB = RUNTIME_PATHS["databases"]["map_start_detection"]
ANALYZE_SCRIPT = PROJECT_ROOT / "services" / "analysis" / "app" / "batch_analyze.py"
TRACKS_OUTPUT_DIR = RUNTIME_PATHS["artifacts"]["tracks_dir"]


def resolve_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def build_plan_rows(db_path: Path) -> list[dict[str, object]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            g.output_filename AS output_filename,
            m.id AS match_id,
            m.tournament_id AS tournament_id,
            g.game_number AS map_number,
            mp.id AS map_id
        FROM games g
        JOIN matches m
          ON m.youtube_video_id = g.youtube_video_id
        LEFT JOIN maps mp
          ON mp.match_id = m.id
         AND mp.round_number = g.game_number
        WHERE g.output_filename IS NOT NULL
          AND TRIM(g.output_filename) != ''
        ORDER BY m.id ASC, g.game_number ASC
        """
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def run_one_video(cmd: list[str], cwd: Path) -> int:
    # Inherit subprocess stdout/stderr so batch_analyze sees a real TTY: single-line \\r redraw + ANSI colors.
    return int(subprocess.call(cmd, cwd=str(cwd)))


def is_already_analyzed(match_id: str, map_number: int) -> bool:
    if not TRACKS_OUTPUT_DIR.exists():
        return False
    pattern = f"{match_id}_{map_number}_*.json"
    return any(TRACKS_OUTPUT_DIR.glob(pattern))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run team analysis for all tournament game videos.")
    parser.add_argument("--tournaments-db-path", default=str(DEFAULT_TOURNAMENTS_DB))
    parser.add_argument("--records-dir", default=str(DEFAULT_RECORDS_DIR))
    parser.add_argument("--map-start-db-path", default=str(DEFAULT_MAP_START_DB))
    parser.add_argument("--workers", type=int, default=6, help="Workers per video analysis run")
    parser.add_argument(
        "--status-interval-sec",
        type=float,
        default=1.0,
        help="How often to print batch_analyze live status lines when piped (default 1.0)",
    )
    parser.add_argument("--frame-skip", type=int, default=None)
    parser.add_argument("--round", choices=["1", "2", "all"], default="all")
    parser.add_argument("--selection-strategy", choices=["nearest", "rightmost", "label_arrow"], default="rightmost")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N videos")
    parser.add_argument(
        "--skip-analyzed",
        action="store_true",
        help="Skip videos that already have output/tracks/<match>_<game>_*.json",
    )
    parser.add_argument("--continue-on-error", action="store_true", help="Continue with next video if one fails")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without execution")
    args = parser.parse_args()

    tournaments_db_path = resolve_path(args.tournaments_db_path)
    records_dir = resolve_path(args.records_dir)
    map_start_db_path = resolve_path(args.map_start_db_path)

    if not tournaments_db_path.exists():
        raise FileNotFoundError(f"tournaments DB not found: {tournaments_db_path}")
    if not records_dir.exists():
        raise FileNotFoundError(f"records dir not found: {records_dir}")
    if not ANALYZE_SCRIPT.exists():
        raise FileNotFoundError(f"batch_analyze.py not found: {ANALYZE_SCRIPT}")

    rows = build_plan_rows(tournaments_db_path)
    if args.limit is not None and args.limit > 0:
        rows = rows[: args.limit]
    total = len(rows)
    if total == 0:
        print("No videos found in tournaments DB plan.")
        return

    print(f"Planned videos: {total}")
    started = time.time()
    ok_count = 0
    fail_count = 0
    skip_count = 0

    for index, row in enumerate(rows, start=1):
        output_filename = str(row.get("output_filename") or "").strip()
        match_id = str(row.get("match_id") or "test").strip()
        tournament_id = str(row.get("tournament_id") or "test").strip()
        map_number = int(row.get("map_number") or 1)
        map_id_raw = row.get("map_id")
        map_id: Optional[str] = str(map_id_raw).strip() if map_id_raw is not None else None

        video_path = records_dir / output_filename
        print(f"[video {index}/{total}] {output_filename} match={match_id} game={map_number}")
        if not video_path.exists():
            print(f"  SKIP missing file: {video_path}")
            skip_count += 1
            continue
        if args.skip_analyzed and is_already_analyzed(match_id, map_number):
            print(f"  SKIP already analyzed: output/tracks/{match_id}_{map_number}_*.json")
            skip_count += 1
            continue

        command = [
            sys.executable,
            "-u",
            str(ANALYZE_SCRIPT),
            "--video",
            str(video_path),
            "--use-map-start-db",
            "--map-start-db-path",
            str(map_start_db_path),
            "--match-id",
            match_id,
            "--tournament-id",
            tournament_id,
            "--map-number",
            str(map_number),
            "--workers",
            str(max(1, int(args.workers))),
            "--status-interval-sec",
            str(float(args.status_interval_sec)),
            "--round",
            args.round,
            "--selection-strategy",
            args.selection_strategy,
        ]
        if map_id:
            command.extend(["--map-id", map_id])
        if args.frame_skip is not None:
            command.extend(["--frame-skip", str(int(args.frame_skip))])
        if args.dry_run:
            print("  DRY RUN:", " ".join(command))
            continue

        exit_code = run_one_video(command, PROJECT_ROOT)
        if exit_code == 0:
            ok_count += 1
            print(f"  OK {output_filename}")
        else:
            fail_count += 1
            print(f"  FAIL {output_filename} exit={exit_code}")
            if not args.continue_on_error:
                break

    elapsed = time.time() - started
    print(
        f"Done in {elapsed:.1f}s | ok={ok_count} fail={fail_count} skip={skip_count} total={total}"
    )


if __name__ == "__main__":
    main()

