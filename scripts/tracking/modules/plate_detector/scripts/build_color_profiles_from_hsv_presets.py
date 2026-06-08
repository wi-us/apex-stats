import argparse
import json
import re
from pathlib import Path


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def safe_tag(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "", (s or "").upper())


def get_hud_order(config: dict) -> dict[int, str]:
    order = config.get("hud_team_order") or {}
    out = {}
    for k, v in order.items():
        try:
            out[int(k)] = safe_tag(str(v))
        except Exception:
            pass
    return out


def get_team_meta(config: dict) -> dict[str, dict]:
    aliases_cfg = config.get("broadcast_tag_aliases") or {}

    alias_to_tag = {}
    for tag, aliases in aliases_cfg.items():
        tag_u = safe_tag(tag)
        alias_to_tag[tag_u] = tag_u
        for a in aliases:
            alias_to_tag[safe_tag(str(a))] = tag_u

    meta = {}

    for t in config.get("teams", []):
        candidates = [
            t.get("tag"),
            t.get("name"),
            t.get("db_tag"),
            t.get("db_name"),
            t.get("broadcast_tag"),
            t.get("short_name"),
        ] + (t.get("aliases") or [])

        broadcast_tag = None
        for c in candidates:
            c_norm = safe_tag(str(c or ""))
            if c_norm in alias_to_tag:
                broadcast_tag = alias_to_tag[c_norm]
                break

        if not broadcast_tag:
            broadcast_tag = safe_tag(str(t.get("tag") or t.get("name") or ""))

        if not broadcast_tag:
            continue

        aliases = set()
        for c in candidates:
            if c:
                aliases.add(str(c))
                aliases.add(safe_tag(str(c)))
        for a in aliases_cfg.get(broadcast_tag, []):
            aliases.add(str(a))
            aliases.add(safe_tag(str(a)))

        meta[broadcast_tag] = {
            "team_id": t.get("team_id") or t.get("id"),
            "team_name": t.get("name") or t.get("db_name") or broadcast_tag,
            "team_tag": t.get("tag") or t.get("db_tag") or broadcast_tag,
            "aliases": sorted(a for a in aliases if a),
        }

    # Add alias-only tags too.
    for tag, aliases in aliases_cfg.items():
        tag_u = safe_tag(tag)
        if tag_u not in meta:
            meta[tag_u] = {
                "team_id": None,
                "team_name": tag_u,
                "team_tag": tag_u,
                "aliases": [tag_u] + list(aliases),
            }
        else:
            for a in aliases:
                if a not in meta[tag_u]["aliases"]:
                    meta[tag_u]["aliases"].append(a)

    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Build match color profiles from HSV slot presets and hud_team_order")
    parser.add_argument("--config", required=True)
    parser.add_argument("--hsv-presets", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--update-config", action="store_true")
    args = parser.parse_args()

    config_path = Path(args.config)
    preset_path = Path(args.hsv_presets)
    out_path = Path(args.out)

    config = load_json(config_path)
    presets = load_json(preset_path)

    hud_order = get_hud_order(config)
    meta_by_tag = get_team_meta(config)

    if not hud_order:
        raise RuntimeError("config does not contain hud_team_order. Add slot -> broadcast tag mapping first.")

    profiles = []
    for item in presets.get("teams", []):
        slot = int(item["slot"])
        tag = hud_order.get(slot)
        if not tag:
            print(f"SKIP slot {slot}: no hud_team_order entry")
            continue

        meta = meta_by_tag.get(tag, {})
        aliases = set(meta.get("aliases") or [])
        aliases.add(tag)
        aliases.add(meta.get("team_tag") or tag)
        aliases.add(meta.get("team_name") or tag)

        # Known conflict aliases.
        if tag == "BB":
            aliases.update(["BB", "Buckle Boys", "BuckleBoys", "BUCKLEBOYS"])
        if tag == "THUG":
            aliases.update(["THUG", "THUGGETS"])

        profiles.append({
            "hud_index": slot,
            "broadcast_tag": tag,
            "team_id": meta.get("team_id"),
            "team_name": meta.get("team_name") or tag,
            "team_tag": meta.get("team_tag") or tag,
            "hex": item.get("hex"),
            "h": item.get("h"),
            "s": item.get("s"),
            "v": item.get("v"),
            "aliases": sorted(a for a in aliases if a),
            "source": f"hsv_preset:{preset_path.name}",
        })

    result = {
        "source": str(preset_path),
        "mode": "hsv_range_profiles",
        "team_color_profiles": profiles,
        "color_conflicts": {
            "BB": ["THUG"],
            "THUG": ["BB"]
        }
    }

    save_json(out_path, result)
    print(f"Saved: {out_path.resolve()}")
    print(f"Profiles: {len(profiles)}")

    for p in profiles:
        if p["broadcast_tag"] in {"BB", "THUG"}:
            print(f"{p['broadcast_tag']}: slot={p['hud_index']} h={p['h']} s={p['s']} v={p['v']} hex={p['hex']}")

    if args.update_config:
        config["team_color_profiles"] = profiles

        # Keep/extend conflicts in config.
        conflicts = config.get("color_conflicts") or {}
        conflicts.setdefault("BB", [])
        conflicts.setdefault("THUG", [])
        if "THUG" not in conflicts["BB"]:
            conflicts["BB"].append("THUG")
        if "BB" not in conflicts["THUG"]:
            conflicts["THUG"].append("BB")
        config["color_conflicts"] = conflicts

        save_json(config_path, config)
        print(f"Updated config: {config_path.resolve()}")


if __name__ == "__main__":
    main()
