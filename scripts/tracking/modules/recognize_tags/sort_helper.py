#!/usr/bin/env python3
"""
sort_helper.py — раскладывает raw-кропы по TAG-папкам на основе slot-to-tag.json.

Источник:  dataset/raw/{match_id}/{slot_id}/*.png
Назначение: dataset/labeled/{TAG}/{match_id}__{slot_id}__{filename}

Маппинг slot_id -> TAG берётся из src/data/{match_id}/slot-to-tag.json
(ключи — числа "1".."20" или строки "slot_01"; и то, и другое поддерживается).

Кропы, для которых нет записи в slot-to-tag.json (или TAG=null/""),
уходят в  dataset/_review/{match_id}__{slot_id}/  — разберёшь руками.

По умолчанию файлы КОПИРУЮТСЯ (raw/ остаётся неприкосновенным).
Флаг --move перемещает вместо копирования.
Флаг --link создаёт hardlink (мгновенно, экономит диск; работает в пределах одного тома).

Пример:
  python sort_helper.py \
      --raw   dataset/raw \
      --out   dataset/labeled \
      --review dataset/_review \
      --maps  src/data \
      --match-id m-test-g1

  # сразу по всем матчам, у которых есть slot-to-tag.json:
  python sort_helper.py --raw dataset/raw --out dataset/labeled \
                        --review dataset/_review --maps src/data --all
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Dict, Optional

SLOT_RE = re.compile(r"(?:slot[_-]?)?(\d+)", re.IGNORECASE)


def _slot_key(raw: str) -> Optional[str]:
    m = SLOT_RE.match(raw.strip())
    if not m:
        return None
    return str(int(m.group(1)))


def load_slot_map(maps_root: Path, match_id: str) -> Dict[str, str]:
    """Возвращает {normalized_slot_num: TAG}. TAG=="" если null/пусто."""
    f = maps_root / match_id / "slot-to-tag.json"
    if not f.exists():
        raise FileNotFoundError(f"slot-to-tag.json not found: {f}")
    data = json.loads(f.read_text(encoding="utf-8"))
    out: Dict[str, str] = {}
    for k, v in data.items():
        key = _slot_key(str(k))
        if not key:
            continue
        tag = (v or "").strip() if isinstance(v, str) else ""
        out[key] = tag
    return out


def transfer(src: Path, dst: Path, mode: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    if mode == "move":
        shutil.move(str(src), str(dst))
    elif mode == "link":
        try:
            dst.hardlink_to(src)  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            shutil.copy2(src, dst)
    else:
        shutil.copy2(src, dst)


def process_match(
    raw_root: Path,
    out_root: Path,
    review_root: Path,
    maps_root: Path,
    match_id: str,
    mode: str,
) -> None:
    match_dir = raw_root / match_id
    if not match_dir.is_dir():
        print(f"[skip] {match_id}: нет {match_dir}")
        return
    try:
        slot_map = load_slot_map(maps_root, match_id)
    except FileNotFoundError as e:
        print(f"[skip] {e}")
        return

    n_ok = n_review = n_slots = 0
    for slot_dir in sorted(p for p in match_dir.iterdir() if p.is_dir()):
        n_slots += 1
        slot_raw = slot_dir.name
        slot_key = _slot_key(slot_raw)
        tag = slot_map.get(slot_key or "", "") if slot_key else ""

        for png in slot_dir.glob("*.png"):
            new_name = f"{match_id}__slot_{int(slot_key):02d}__{png.name}" \
                if slot_key else f"{match_id}__{slot_raw}__{png.name}"
            if tag:
                dst = out_root / tag / new_name
                transfer(png, dst, mode)
                n_ok += 1
            else:
                dst = review_root / f"{match_id}__{slot_raw}" / png.name
                transfer(png, dst, mode)
                n_review += 1

    print(f"[ok] {match_id}: slots={n_slots}, labeled={n_ok}, review={n_review}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw",     required=True, type=Path)
    ap.add_argument("--out",     required=True, type=Path)
    ap.add_argument("--review",  required=True, type=Path)
    ap.add_argument("--maps",    required=True, type=Path,
                    help="Корень с {match_id}/slot-to-tag.json (обычно src/data)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--match-id", help="Один матч")
    g.add_argument("--all", action="store_true",
                   help="Все подкаталоги raw/, у которых есть slot-to-tag.json")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--move", action="store_true", help="Перемещать вместо копирования")
    mode.add_argument("--link", action="store_true", help="Hardlink (быстро, экономно)")
    args = ap.parse_args()

    transfer_mode = "move" if args.move else "link" if args.link else "copy"

    args.out.mkdir(parents=True, exist_ok=True)
    args.review.mkdir(parents=True, exist_ok=True)

    if args.all:
        matches = sorted(p.name for p in args.raw.iterdir() if p.is_dir())
    else:
        matches = [args.match_id]

    for mid in matches:
        process_match(args.raw, args.out, args.review, args.maps, mid, transfer_mode)


if __name__ == "__main__":
    main()