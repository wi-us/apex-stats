"""Build a local WebP cache for ALGS player portrait images.

The ALGS API stores player portraits as large transparent PNG files. The site
renders them as roster cards, so a 500x600 WebP is enough for crisp display and
keeps first page loads much lighter.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import sqlite3
from pathlib import Path
from typing import Iterable

import requests
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "scripts" / "algs_api" / "data" / "algs.sqlite"
DEFAULT_OUT = ROOT / "public" / "player-images"
DEFAULT_MANIFEST = ROOT / "src" / "data" / "player-images.manifest.json"


def image_name(url: str) -> str:
    return f"{hashlib.sha1(url.encode('utf-8')).hexdigest()[:20]}.webp"


def iter_player_image_urls(db_path: Path) -> list[str]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "select distinct front_image from players "
            "where front_image is not null and front_image != '' "
            "order by front_image"
        ).fetchall()
    finally:
        con.close()
    return [row[0] for row in rows]


def fit_image(img: Image.Image, max_width: int, max_height: int) -> Image.Image:
    img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    return img


def convert_one(
    url: str,
    out_dir: Path,
    *,
    max_width: int,
    max_height: int,
    quality: int,
    request_timeout: float,
    force: bool,
) -> tuple[str, str, int, int, str | None]:
    out_path = out_dir / image_name(url)
    public_path = f"/player-images/{out_path.name}"

    if out_path.exists() and not force:
        return url, public_path, 0, out_path.stat().st_size, None

    try:
        response = requests.get(url, timeout=(5, request_timeout))
        response.raise_for_status()
        original = response.content
        with Image.open(io.BytesIO(original)) as source:
            img = fit_image(source, max_width=max_width, max_height=max_height)
            tmp = out_path.with_suffix(".tmp")
            img.save(tmp, "WEBP", quality=quality, method=6)
            tmp.replace(out_path)
        return url, public_path, len(original), out_path.stat().st_size, None
    except Exception as exc:  # noqa: BLE001
        return url, public_path, 0, 0, str(exc)


def write_manifest(pairs: Iterable[tuple[str, str]], path: Path) -> None:
    manifest = {url: public_path for url, public_path in sorted(pairs)}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert ALGS player PNG portraits to local WebP files.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--max-width", type=int, default=500)
    parser.add_argument("--max-height", type=int, default=600)
    parser.add_argument("--quality", type=int, default=78)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--request-timeout", type=float, default=12.0)
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N missing images.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    all_urls = iter_player_image_urls(args.db)
    urls = all_urls
    if args.limit > 0 and not args.force:
        urls = [url for url in urls if not (args.out / image_name(url)).exists()][:args.limit]
    elif args.limit > 0:
        urls = urls[:args.limit]
    if not all_urls:
        write_manifest([], args.manifest)
        print("No player images found.")
        return 0

    converted: list[tuple[str, str]] = []
    failures: list[tuple[str, str]] = []
    original_total = 0
    webp_total = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        jobs = [
            pool.submit(
                convert_one,
                url,
                args.out,
                max_width=args.max_width,
                max_height=args.max_height,
                quality=args.quality,
                request_timeout=args.request_timeout,
                force=args.force,
            )
            for url in urls
        ]
        for idx, job in enumerate(concurrent.futures.as_completed(jobs), start=1):
            url, public_path, original_size, webp_size, error = job.result()
            if error:
                failures.append((url, error))
            else:
                converted.append((url, public_path))
                original_total += original_size
                webp_total += webp_size
            if idx % 25 == 0 or idx == len(jobs):
                print(f"{idx}/{len(jobs)} images processed")

    manifest_pairs = []
    seen = set()
    for url, public_path in converted:
        manifest_pairs.append((url, public_path))
        seen.add(url)
    for url in all_urls:
        if url in seen:
            continue
        out_path = args.out / image_name(url)
        if out_path.exists():
            manifest_pairs.append((url, f"/player-images/{out_path.name}"))
    write_manifest(manifest_pairs, args.manifest)

    saved = original_total - webp_total
    print(f"Manifest: {args.manifest}")
    print(f"WebP files: {args.out}")
    print(f"Converted: {len(converted)}; failed: {len(failures)}")
    if original_total:
        print(f"Downloaded originals: {original_total / 1024 / 1024:.1f} MB")
        print(f"Local WebP total: {webp_total / 1024 / 1024:.1f} MB")
        print(f"Saved on converted files: {saved / 1024 / 1024:.1f} MB")
    if failures:
        print("Failures:")
        for url, error in failures[:20]:
            print(f"- {url}: {error}")
    return 0 if converted else 1


if __name__ == "__main__":
    raise SystemExit(main())
