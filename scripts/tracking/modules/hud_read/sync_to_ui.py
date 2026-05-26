#!/usr/bin/env python3
"""Копирует свежие отчёты hud_read в src/data/m-test-g1/
чтобы фронтенд (MatchViewer) подцепил реальные данные.

Создаёт:
  src/data/m-test-g1/eliminations.json
  src/data/m-test-g1/rings.json            (если есть)
  src/data/m-test-g1/hud_timeline.json
  src/data/m-test-g1/slot-to-tag.json      (slot → team tag, из hud_timeline)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# Windows PowerShell defaults to cp1251/cp866 and crashes on Unicode (e.g. '→').
# Force UTF-8 on stdout/stderr so prints never raise UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parents[3]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", type=Path,
                    default=MODULE_DIR / "reports")
    ap.add_argument("--out", type=Path,
                    default=REPO_ROOT / "src" / "data" / "m-test-g1")
    ap.add_argument("--ring-geometry", type=Path, default=None,
                    help="Опц. ring_geometry.json из ring_locator — "
                         "будет вмёржен в rings.json как поле 'geometry'.")
    ap.add_argument("--tracks-reports", type=Path, default=Path("auto"),
                    help="Путь к scripts/tracking/modules/track_teams/reports — "
                         "копирует tracks.json и tracks.slots.json в --out. "
                         "По умолчанию 'auto' → стандартное расположение в репо. "
                         "Передай 'skip' (или пустую строку), чтобы отключить.")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for name in ("eliminations.json", "rings.json", "ring_geometry_v2.json", "hud_timeline.json"):
        src = args.reports / name
        if not src.exists():
            print(f"[sync_to_ui] пропускаю {name} — не найден в {args.reports}")
            continue
        dst = args.out / name
        shutil.copy2(src, dst)
        copied.append(name)
        print(f"[sync_to_ui] {src} → {dst}")

    # Встраиваем eliminations прямо в hud_timeline.json — единый источник
    # HUD-смертей для фронта. eliminations.json остаётся как debug-artifact
    # в out/, но UI его больше не использует.
    elim_src = args.reports / "eliminations.json"
    tl_dst = args.out / "hud_timeline.json"
    if elim_src.exists() and tl_dst.exists():
        try:
            elim = json.loads(elim_src.read_text(encoding="utf-8"))
            tl = json.loads(tl_dst.read_text(encoding="utf-8"))
            tl["eliminations"] = {
                "source": "scout",
                "fps": elim.get("fps"),
                "mode": elim.get("mode"),
                "teams": elim.get("teams", {}),
            }
            tl_dst.write_text(
                json.dumps(tl, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            teams_e = elim.get("teams", {})
            dead = sum(1 for v in teams_e.values() if v.get("t_first_dead") is not None)
            alive = len(teams_e) - dead
            print(f"[sync_to_ui] embedded eliminations → hud_timeline.json "
                  f"({len(teams_e)} slots / {dead} dead / {alive} alive)")
        except Exception as e:
            print(f"[sync_to_ui] не смог встроить eliminations: {e}")

    # tracks.json / tracks.slots.json из track_teams (опц.)
    tracks_dir = args.tracks_reports
    if tracks_dir is not None and str(tracks_dir).lower() not in ("skip", ""):
        if str(tracks_dir).lower() == "auto":
            tracks_dir = REPO_ROOT / "scripts" / "tracking" / "modules" / "track_teams" / "reports"
        for name in ("tracks.json", "tracks.slots.json"):
            src = tracks_dir / name
            if not src.exists():
                print(f"[sync_to_ui] пропускаю {name} — не найден в {tracks_dir}")
                continue
            dst = args.out / name
            shutil.copy2(src, dst)
            copied.append(name)
            print(f"[sync_to_ui] {src} → {dst}")

    # Мердж геометрии колец в rings.json (если есть).
    if args.ring_geometry and args.ring_geometry.exists():
        rings_dst = args.out / "rings.json"
        if rings_dst.exists():
            try:
                rings = json.loads(rings_dst.read_text(encoding="utf-8"))
                geom = json.loads(args.ring_geometry.read_text(encoding="utf-8"))
                rings["geometry"] = {
                    "minimap": geom.get("minimap"),
                    "phases": geom.get("phases") or [],
                }
                geom_dst = args.out / "ring_geometry_v2.json"
                shutil.copy2(args.ring_geometry, geom_dst)
                rings_dst.write_text(
                    json.dumps(rings, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"[sync_to_ui] merged ring geometry "
                      f"({len(rings['geometry']['phases'])} phases) → {rings_dst} + {geom_dst}")
            except Exception as e:
                print(f"[sync_to_ui] не смог вмёржить ring_geometry: {e}")
        else:
            print(f"[sync_to_ui] нет {rings_dst} — пропускаю ring-geometry merge")

    # slot-to-tag из hud_timeline
    tl_path = args.reports / "hud_timeline.json"
    if tl_path.exists():
        try:
            tl = json.loads(tl_path.read_text(encoding="utf-8"))
            slot_to_tag: dict[int, str] = {}
            for entry in tl.get("timeline", []):
                for t in entry.get("teams", []) or []:
                    slot = t.get("slot")
                    name = t.get("name")
                    if slot is None or not name:
                        continue
                    slot_to_tag.setdefault(int(slot), str(name))
                if len(slot_to_tag) >= 20:
                    break
            (args.out / "slot-to-tag.json").write_text(
                json.dumps(slot_to_tag, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[sync_to_ui] slot-to-tag.json ({len(slot_to_tag)} teams)")
        except Exception as e:
            print(f"[sync_to_ui] не смог собрать slot-to-tag.json: {e}")

    print(f"[sync_to_ui] готово ({len(copied)} файлов скопировано)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())