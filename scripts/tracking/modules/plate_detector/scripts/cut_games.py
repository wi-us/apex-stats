import argparse
import re
import subprocess
from pathlib import Path


DEFAULT_TIMINGS = """
00:01:19 - Game 1
00:26:46 - Game 2
00:53:32 - Game 3
01:37:18 - Game 4
02:04:42 - Game 5
02:33:38 - Game 6
"""


def parse_time_to_seconds(value: str) -> int:
    value = value.strip()
    parts = value.split(":")
    if len(parts) == 2:
        h = 0
        m, s = parts
    elif len(parts) == 3:
        h, m, s = parts
    else:
        raise ValueError(f"Bad time format: {value}")
    return int(h) * 3600 + int(m) * 60 + int(s)


def seconds_to_hms(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def safe_filename(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_\- ]+", "", text)
    text = re.sub(r"\s+", "_", text.strip())
    return text or "clip"


def parse_timings(text: str):
    games = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Supported:
        # 00:01:19 - Game 1
        # 00:01:19 Game 1
        m = re.match(r"^(\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2})\s*(?:-|—|–)?\s*(.+?)\s*$", line)
        if not m:
            raise ValueError(f"Cannot parse timing line: {line}")

        start = m.group(1)
        name = m.group(2)
        games.append({"start": start, "name": name, "start_sec": parse_time_to_seconds(start)})

    if not games:
        raise ValueError("No timings found")

    return games


def read_timings(path: str | None):
    if not path:
        return parse_timings(DEFAULT_TIMINGS)
    return parse_timings(Path(path).read_text(encoding="utf-8"))


def run_ffmpeg(cmd, dry_run: bool):
    print(" ".join(f'"{x}"' if " " in x else x for x in cmd))
    if not dry_run:
        subprocess.run(cmd, check=True)


def main():
    parser = argparse.ArgumentParser(description="Cut video into fixed-duration game clips by start timings")
    parser.add_argument("--video", required=True, help="Input video path")
    parser.add_argument("--out", default="clips", help="Output directory")
    parser.add_argument("--timings", default=None, help="Text file with lines like: 00:01:19 - Game 1")
    parser.add_argument("--duration", default="00:20:00", help="Clip duration, default 20 minutes")
    parser.add_argument("--reencode", action="store_true", help="Use precise cutting with re-encoding instead of stream copy")
    parser.add_argument("--dry-run", action="store_true", help="Print ffmpeg commands without running")
    args = parser.parse_args()

    video = Path(args.video)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not video.exists():
        raise FileNotFoundError(video)

    games = read_timings(args.timings)
    duration = seconds_to_hms(parse_time_to_seconds(args.duration))

    for idx, game in enumerate(games, start=1):
        start = seconds_to_hms(game["start_sec"])
        name = safe_filename(game["name"])
        output = out_dir / f"{idx:02d}_{name}_{start.replace(':', '-')}.mp4"

        if args.reencode:
            cmd = [
                "ffmpeg", "-y",
                "-ss", start,
                "-i", str(video),
                "-t", duration,
                "-map", "0",
                "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "192k",
                str(output),
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-ss", start,
                "-i", str(video),
                "-t", duration,
                "-map", "0",
                "-c", "copy",
                str(output),
            ]

        run_ffmpeg(cmd, dry_run=args.dry_run)

    print(f"Done. Clips saved to: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
