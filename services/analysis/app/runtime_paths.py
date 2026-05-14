from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _resolve(project_root: Path, raw: str) -> Path:
    value = Path(raw)
    return value if value.is_absolute() else (project_root / value)


def load_runtime_paths(project_root: Path) -> dict[str, Any]:
    cfg_path = project_root / "config" / "runtime_paths.json"
    payload: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            payload = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}

    db_cfg = payload.get("databases", {}) if isinstance(payload, dict) else {}
    art_cfg = payload.get("artifacts", {}) if isinstance(payload, dict) else {}
    media_cfg = payload.get("media", {}) if isinstance(payload, dict) else {}

    tournaments_raw = db_cfg.get("tournaments")
    if not isinstance(tournaments_raw, list) or not tournaments_raw:
        tournaments_raw = ["output/tournaments.sqlite", "output/youtube_ingest/tournaments.sqlite"]

    resolved_tournaments = [_resolve(project_root, str(item)) for item in tournaments_raw]
    preferred_tournaments = next((p for p in resolved_tournaments if p.exists()), resolved_tournaments[0])

    return {
        "databases": {
            "tournaments": resolved_tournaments,
            "preferred_tournaments": preferred_tournaments,
            "map_start_detection": _resolve(project_root, str(db_cfg.get("mapStartDetection", "output/map_start_detection.sqlite"))),
        },
        "artifacts": {
            "jobs_store": _resolve(project_root, str(art_cfg.get("jobsStore", "output/jobs.json"))),
            "tracks_dir": _resolve(project_root, str(art_cfg.get("tracksDir", "output/tracks"))),
            "tracks_file": _resolve(project_root, str(art_cfg.get("tracksFile", "output/tracks.json"))),
            "map_admin_settings": _resolve(project_root, str(art_cfg.get("mapAdminSettings", "output/admin_map_settings.json"))),
            "zones_dir": _resolve(project_root, str(art_cfg.get("zonesDir", "output/zones"))),
            "text_zones_dir": _resolve(project_root, str(art_cfg.get("textZonesDir", "output/text_zones"))),
        },
        "media": {
            "records_dir": _resolve(project_root, str(media_cfg.get("recordsDir", "ffmpeg_downloader/records"))),
            "maps_dir": _resolve(project_root, str(media_cfg.get("mapsDir", "maps"))),
        },
    }
