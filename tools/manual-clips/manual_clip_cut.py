from __future__ import annotations

import argparse
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cut manual clips by start timestamp with auto start detection.")
    parser.add_argument(
        "--db-path",
        default="output/manual_clip_jobs.sqlite",
        help="Path to SQLite file with manual_clip_jobs table.",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Create manual_clip_jobs table before processing.",
    )
    parser.add_argument(
        "--job-id",
        type=int,
        default=None,
        help="Process only one job by id.",
    )
    parser.add_argument(
        "--include-status",
        action="append",
        default=["pending"],
        help="Statuses to process (repeatable). Default: pending",
    )
    parser.add_argument(
        "--default-duration-sec",
        type=int,
        default=1200,
        help="Fallback duration when DB value is invalid (default: 1200).",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS manual_clip_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            user_start_sec INTEGER NOT NULL,
            detected_start_sec INTEGER,
            final_start_sec INTEGER,
            duration_sec INTEGER NOT NULL DEFAULT 1200,
            tournament_id TEXT NOT NULL,
            output_file TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_manual_clip_jobs_status
            ON manual_clip_jobs(status);

        CREATE INDEX IF NOT EXISTS idx_manual_clip_jobs_tournament
            ON manual_clip_jobs(tournament_id);
        """
    )


def run_capture(command: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def detect_video_start_sec(source_file: Path) -> int:
    """
    Detect beginning of actual footage by scanning initial black frames.
    We use black_end from the first black segment that starts near t=0.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(source_file),
        "-t",
        "180",
        "-vf",
        "blackdetect=d=0.30:pix_th=0.10",
        "-an",
        "-f",
        "null",
        "-",
    ]
    _, stdout, stderr = run_capture(cmd)
    log_text = f"{stdout}\n{stderr}"

    pattern = re.compile(r"black_start:(?P<start>\d+(\.\d+)?)\s+black_end:(?P<end>\d+(\.\d+)?)")
    for match in pattern.finditer(log_text):
        start_val = float(match.group("start"))
        end_val = float(match.group("end"))
        if start_val <= 2.0:
            return max(0, int(round(end_val)))
    return 0


def cut_clip(source_file: Path, output_file: Path, start_sec: int, duration_sec: int) -> tuple[bool, str]:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(max(0, int(start_sec))),
        "-t",
        str(max(1, int(duration_sec))),
        "-i",
        str(source_file),
        "-c",
        "copy",
        str(output_file),
    ]
    code, out, err = run_capture(cmd)
    if code == 0 and output_file.exists():
        return True, ""
    details = err or out or f"ffmpeg exit code={code}"
    return False, details


def fetch_jobs(conn: sqlite3.Connection, job_id: int | None, statuses: list[str]) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    statuses = [s.strip() for s in statuses if s.strip()]
    if not statuses:
        statuses = ["pending"]

    if job_id is not None:
        row = conn.execute("SELECT * FROM manual_clip_jobs WHERE id = ?", (job_id,)).fetchone()
        return [row] if row else []

    placeholders = ",".join("?" for _ in statuses)
    query = f"SELECT * FROM manual_clip_jobs WHERE status IN ({placeholders}) ORDER BY source_file, id"
    return conn.execute(query, tuple(statuses)).fetchall()


def resolve_detected_start_sec(
    conn: sqlite3.Connection,
    source_file: Path,
    detected_cache: dict[str, int],
) -> int:
    key = str(source_file)
    if key in detected_cache:
        return detected_cache[key]

    row = conn.execute(
        """
        SELECT detected_start_sec
          FROM manual_clip_jobs
         WHERE source_file = ?
           AND detected_start_sec IS NOT NULL
         ORDER BY id DESC
         LIMIT 1
        """,
        (key,),
    ).fetchone()
    if row and row[0] is not None:
        detected_cache[key] = int(row[0])
        return detected_cache[key]

    detected = detect_video_start_sec(source_file)
    detected_cache[key] = int(detected)
    return detected_cache[key]


def process_one(
    conn: sqlite3.Connection,
    job: sqlite3.Row,
    default_duration_sec: int,
    detected_cache: dict[str, int],
) -> None:
    job_id = int(job["id"])
    source_file = Path(str(job["source_file"]))
    output_file = Path(str(job["output_file"]))
    user_start_sec = int(job["user_start_sec"])
    duration_sec = int(job["duration_sec"]) if int(job["duration_sec"]) > 0 else int(default_duration_sec)

    try:
        if not source_file.exists():
            raise RuntimeError(f"source file not found: {source_file}")

        detected_start_sec = resolve_detected_start_sec(
            conn=conn,
            source_file=source_file,
            detected_cache=detected_cache,
        )
        final_start_sec = max(0, detected_start_sec + user_start_sec)
        ok, details = cut_clip(
            source_file=source_file,
            output_file=output_file,
            start_sec=final_start_sec,
            duration_sec=duration_sec,
        )
        if not ok:
            raise RuntimeError(details)

        conn.execute(
            """
            UPDATE manual_clip_jobs
               SET detected_start_sec = ?,
                   final_start_sec = ?,
                   status = 'done',
                   error_message = NULL,
                   updated_at = ?
             WHERE id = ?
            """,
            (detected_start_sec, final_start_sec, utc_now(), job_id),
        )
        conn.commit()
        print(
            f"[done] id={job_id} "
            f"detected_start={detected_start_sec}s user_start={user_start_sec}s "
            f"final_start={final_start_sec}s output={output_file.name}"
        )
    except Exception as exc:
        conn.execute(
            """
            UPDATE manual_clip_jobs
               SET status = 'failed',
                   error_message = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (str(exc), utc_now(), job_id),
        )
        conn.commit()
        print(f"[failed] id={job_id} error={exc}")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        if args.init_db:
            init_db(conn)
            conn.commit()
            print(f"[init] table ready in {db_path}")

        jobs = fetch_jobs(conn, job_id=args.job_id, statuses=args.include_status)
        if not jobs:
            print("[info] no jobs to process")
            return 0

        print(f"[info] jobs to process: {len(jobs)}")
        detected_cache: dict[str, int] = {}
        for job in jobs:
            process_one(
                conn,
                job,
                default_duration_sec=args.default_duration_sec,
                detected_cache=detected_cache,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
