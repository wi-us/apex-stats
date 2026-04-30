from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync clipped records from VPS to local PC.")
    parser.add_argument("--host", required=True, help="SSH host, e.g. root@31.13.248.102")
    parser.add_argument(
        "--remote-dir",
        default="~/www/wi-us.ru/apex-stats/ffmpeg_downloader/records",
        help="Remote directory with clipped mp4 files.",
    )
    parser.add_argument(
        "--local-dir",
        default="ffmpeg_downloader/records",
        help="Local destination directory.",
    )
    parser.add_argument(
        "--delete-remote",
        action="store_true",
        help="Delete remote file after successful transfer.",
    )
    parser.add_argument(
        "--cleanup-sources",
        action="store_true",
        help="Delete remote _sources video when all its clips are sent/removed.",
    )
    parser.add_argument(
        "--remote-db",
        default="~/www/wi-us.ru/apex-stats/output/youtube_ingest/tournaments.sqlite",
        help="Remote SQLite DB path created by ingest.",
    )
    parser.add_argument(
        "--remote-sources-dir",
        default="~/www/wi-us.ru/apex-stats/ffmpeg_downloader/records/_sources",
        help="Remote source videos directory to clean.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep syncing in a loop.",
    )
    parser.add_argument(
        "--interval-sec",
        type=int,
        default=30,
        help="Polling interval for --watch mode.",
    )
    return parser.parse_args()


def run_capture(command: list[str]) -> tuple[int, str]:
    completed = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return completed.returncode, completed.stdout.strip()


def list_remote_files(host: str, remote_dir: str) -> list[tuple[str, int]]:
    cmd = [
        "ssh",
        host,
        f"find {remote_dir} -maxdepth 1 -type f -name '*.mp4' -printf '%f\\t%s\\n' | sort",
    ]
    code, out = run_capture(cmd)
    if code != 0 or not out:
        return []
    rows: list[tuple[str, int]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        name = parts[0].strip()
        try:
            size = int(parts[1].strip())
        except ValueError:
            continue
        rows.append((name, size))
    return rows


def transfer_one(host: str, remote_dir: str, local_dir: Path, file_name: str, expected_size: int) -> bool:
    local_dir.mkdir(parents=True, exist_ok=True)
    remote_path = f"{host}:{remote_dir}/{file_name}"
    print(f"[sync] downloading {file_name} ({expected_size / (1024 * 1024):.1f} MB)")
    code = subprocess.run(["scp", remote_path, str(local_dir)], check=False).returncode
    if code != 0:
        print(f"[sync] failed transfer: {file_name} (scp code={code})")
        return False
    local_file = local_dir / file_name
    if not local_file.exists():
        print(f"[sync] failed verification: local file missing {file_name}")
        return False
    local_size = local_file.stat().st_size
    if expected_size > 0 and local_size < expected_size:
        print(
            f"[sync] failed verification: {file_name} partial size "
            f"{local_size / (1024 * 1024):.1f} MB < {expected_size / (1024 * 1024):.1f} MB"
        )
        return False
    print(f"[sync] done {file_name} ({local_size / (1024 * 1024):.1f} MB)")
    return True


def remove_remote(host: str, remote_dir: str, file_name: str) -> None:
    subprocess.run(["ssh", host, f"rm -f {remote_dir}/{file_name}"], check=False)


def cleanup_remote_sources(host: str, remote_db: str, remote_records_dir: str, remote_sources_dir: str) -> int:
    script = f"""python3 - <<'PY'
import json
import sqlite3
from pathlib import Path

db_path = Path("{remote_db}").expanduser()
records_dir = Path("{remote_records_dir}").expanduser()
sources_dir = Path("{remote_sources_dir}").expanduser()
removed = 0

if not db_path.exists() or not records_dir.exists() or not sources_dir.exists():
    print(0)
    raise SystemExit(0)

remote_files = {{p.name for p in records_dir.glob("*.mp4")}}
conn = sqlite3.connect(db_path)
rows = conn.execute(
    "SELECT youtube_video_id, output_filename, download_status FROM games"
).fetchall()
conn.close()

by_video = {{}}
for video_id, output_name, status in rows:
    video = by_video.setdefault(video_id, {{"all_ok": True, "outputs": []}})
    video["outputs"].append(output_name)
    if status not in ("downloaded", "completed"):
        video["all_ok"] = False

for video_id, info in by_video.items():
    if not info["all_ok"] or not info["outputs"]:
        continue
    if any(name in remote_files for name in info["outputs"]):
        continue
    src = sources_dir / f"{{video_id}}.mp4"
    if src.exists():
        src.unlink()
        removed += 1

print(removed)
PY"""
    code, out = run_capture(["ssh", host, script])
    if code != 0:
        return 0
    try:
        return int((out or "0").splitlines()[-1].strip())
    except ValueError:
        return 0


def mark_remote_games_completed(host: str, remote_db: str, output_filenames: list[str]) -> int:
    if not output_filenames:
        return 0
    payload = json.dumps(sorted(set(output_filenames)))
    script = f"""python3 - <<'PY'
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

db_path = Path("{remote_db}").expanduser()
names = json.loads('''{payload}''')
if not db_path.exists() or not names:
    print(0)
    raise SystemExit(0)
conn = sqlite3.connect(db_path)
updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
changed = 0
for name in names:
    cur = conn.execute(
        '''
        UPDATE games
           SET download_status = 'completed',
               error_message = NULL,
               updated_at = ?
         WHERE output_filename = ?
           AND download_status IN ('downloaded', 'completed')
        ''',
        (updated_at, name),
    )
    changed += int(cur.rowcount or 0)
conn.commit()
conn.close()
print(changed)
PY"""
    code, out = run_capture(["ssh", host, script])
    if code != 0:
        return 0
    try:
        return int((out or "0").splitlines()[-1].strip())
    except ValueError:
        return 0


def sync_once(args: argparse.Namespace) -> tuple[int, int]:
    local_dir = Path(args.local_dir)
    existing_sizes = {p.name: p.stat().st_size for p in local_dir.glob("*.mp4")} if local_dir.exists() else {}
    remote_files = list_remote_files(args.host, args.remote_dir)
    if not remote_files:
        print("[sync] no remote mp4 files found")
        return 0, 0

    transferred = 0
    skipped = 0
    deleted_remote = 0
    completed_names: list[str] = []
    for file_name, size in remote_files:
        if file_name in existing_sizes:
            local_size = int(existing_sizes[file_name])
            if local_size >= size:
                skipped += 1
                completed_names.append(file_name)
                if args.delete_remote:
                    remove_remote(args.host, args.remote_dir, file_name)
                    deleted_remote += 1
                continue
        ok = transfer_one(args.host, args.remote_dir, local_dir, file_name, size)
        if ok:
            transferred += 1
            completed_names.append(file_name)
            if args.delete_remote:
                remove_remote(args.host, args.remote_dir, file_name)
                deleted_remote += 1
        time.sleep(0.3)
    marked_completed = mark_remote_games_completed(args.host, args.remote_db, completed_names)
    cleaned_sources = 0
    if args.delete_remote and args.cleanup_sources:
        cleaned_sources = cleanup_remote_sources(args.host, args.remote_db, args.remote_dir, args.remote_sources_dir)
    print(
        f"[sync] summary transferred={transferred} skipped_existing={skipped} "
        f"remote_deleted={deleted_remote} db_completed_updates={marked_completed} "
        f"sources_cleaned={cleaned_sources} remote_total={len(remote_files)}"
    )
    return transferred, skipped


def main() -> int:
    args = parse_args()
    while True:
        sync_once(args)
        if not args.watch:
            break
        time.sleep(max(5, int(args.interval_sec)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

