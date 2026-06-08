import argparse
import json
from pathlib import Path


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def deep_merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge ALGS conflict/tracking patch into match config JSON")
    parser.add_argument("--config", required=True, help="Path to configs\\01KH...json")
    parser.add_argument("--patch", required=True, help="Path to algs_conflict_patch.json")
    parser.add_argument("--out", default=None, help="If omitted, config is updated in-place after making .bak")
    args = parser.parse_args()

    config_path = Path(args.config)
    patch_path = Path(args.patch)
    config = load_json(config_path)
    patch = load_json(patch_path)

    merged = deep_merge(config, patch)

    if args.out:
        out = Path(args.out)
    else:
        backup = config_path.with_suffix(config_path.suffix + ".bak")
        backup.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        out = config_path

    save_json(out, merged)
    print(f"Patched config saved: {out.resolve()}")
