import argparse
import json
import re
import sqlite3
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def safe_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def normalize_text(s: str) -> str:
    s = (s or "").upper()
    return re.sub(r"[^A-Z0-9]+", "", s)


def scaled_zone(zone: dict, base_size, image_shape):
    base_w, base_h = base_size
    img_h, img_w = image_shape[:2]

    sx = img_w / base_w
    sy = img_h / base_h

    x = int(round(zone["x"] * sx))
    y = int(round(zone["y"] * sy))
    w = int(round(zone["w"] * sx))
    h = int(round(zone["h"] * sy))

    x = max(0, min(img_w - 1, x))
    y = max(0, min(img_h - 1, y))
    w = max(1, min(img_w - x, w))
    h = max(1, min(img_h - y, h))

    return x, y, w, h


def get_hud_name_zones(zones_data: dict):
    result = []

    for z in zones_data.get("zones", []):
        tag = str(z.get("tag", ""))
        name = str(z.get("name", ""))

        if tag.startswith("team_") and name == "name":
            m = re.search(r"team_(\d+)", tag)
            if not m:
                continue

            zz = dict(z)
            zz["team_index"] = int(m.group(1))
            result.append(zz)

    return sorted(result, key=lambda x: x["team_index"])


def load_series_teams_from_db(db_path: Path, series_id: str):
    """
    Важно: берём broadcast name / shortName из series_team_stats.raw_json.
    Там могут быть актуальные названия с трансляции: S2, STAL, BRGR и т.д.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT sts.team_id, sts.version_id, sts.position, sts.points, sts.kills, sts.raw_json,
               t.name AS db_name, t.short_name AS db_short_name
        FROM series_team_stats sts
        LEFT JOIN teams t ON t.id = sts.team_id
        WHERE sts.series_id=?
        ORDER BY sts.position
        """,
        (series_id,),
    )

    teams = []

    for r in cur.fetchall():
        raw = {}
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except Exception:
            raw = {}

        name = safe_text(raw.get("name") or r["db_name"] or "")
        tag = safe_text(raw.get("shortName") or r["db_short_name"] or "")
        db_name = safe_text(r["db_name"] or "")
        db_tag = safe_text(r["db_short_name"] or "")

        aliases = []
        for a in [
            name,
            tag,
            name.replace(" ", ""),
            db_name,
            db_tag,
            db_name.replace(" ", ""),
        ]:
            if a and a not in aliases:
                aliases.append(a)

        teams.append(
            {
                "team_id": r["team_id"],
                "version_id": r["version_id"],
                "name": name,
                "tag": tag,
                "db_name": db_name,
                "db_tag": db_tag,
                "series_position": r["position"],
                "points": r["points"],
                "kills": r["kills"],
                "aliases": aliases,
            }
        )

    conn.close()
    return teams


def render_text_template(text: str, font, canvas=(220, 60)):
    img = Image.new("L", canvas, 0)
    draw = ImageDraw.Draw(img)
    draw.text((4, 6), text, font=font, fill=255)
    arr = np.array(img)

    ys, xs = np.where(arr > 10)
    if len(xs) == 0:
        return None

    arr = arr[
        max(0, ys.min() - 2) : min(arr.shape[0], ys.max() + 3),
        max(0, xs.min() - 2) : min(arr.shape[1], xs.max() + 3),
    ]
    return arr


def crop_text_mask(crop):
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

    # Белый текст HUD.
    mask = ((gray > 130) & (hsv[:, :, 1] < 190)).astype(np.uint8) * 255

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

    return mask


def template_score(crop_mask, template):
    if crop_mask.size == 0 or template is None or template.size == 0:
        return 0.0

    h, w = crop_mask.shape[:2]
    th, tw = template.shape[:2]

    best = 0.0

    for scale in [0.65, 0.8, 0.95, 1.1, 1.25, 1.4]:
        new_h = max(4, int(h * 0.72 * scale))
        new_w = max(4, int(tw * (new_h / max(th, 1))))

        if new_w > w * 1.8:
            continue

        templ = cv2.resize(template, (new_w, new_h), interpolation=cv2.INTER_AREA)

        canvas = np.zeros((h, w), dtype=np.uint8)
        y = max(0, (h - new_h) // 2)
        x = max(0, (w - new_w) // 2)

        hh = min(new_h, h - y)
        ww = min(new_w, w - x)

        if hh <= 0 or ww <= 0:
            continue

        canvas[y : y + hh, x : x + ww] = templ[:hh, :ww]

        a = crop_mask.astype(np.float32) / 255.0
        b = canvas.astype(np.float32) / 255.0

        inter = float(np.sum(np.minimum(a, b)))
        denom = float(np.sum(b) + 1e-6)

        score = inter / denom
        best = max(best, score)

    return best


def build_templates(teams, font):
    templates = []

    for team in teams:
        for alias in team["aliases"]:
            norm = normalize_text(alias)
            if not norm:
                continue

            templ = render_text_template(norm, font)
            if templ is not None:
                templates.append((team, alias, templ))

    return templates


def match_crop(crop, templates):
    mask = crop_text_mask(crop)

    best = {
        "team": None,
        "alias": None,
        "score": 0.0,
    }

    for team, alias, templ in templates:
        score = template_score(mask, templ)

        if score > best["score"]:
            best = {
                "team": team,
                "alias": alias,
                "score": score,
            }

    return best


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--zones", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--series-id", required=True)
    parser.add_argument("--font", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--font-size", type=int, default=24)
    parser.add_argument("--min-score", type=float, default=0.32)
    args = parser.parse_args()

    image_path = Path(args.image)
    zones_path = Path(args.zones)
    db_path = Path(args.db)
    font_path = Path(args.font)
    out_dir = Path(args.out)

    out_dir.mkdir(parents=True, exist_ok=True)
    crops_dir = out_dir / "hud_crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"Cannot read image: {image_path}")

    zones = load_json(zones_path)
    base = zones.get("base", [1920, 1080])
    hud_zones = get_hud_name_zones(zones)

    teams = load_series_teams_from_db(db_path, args.series_id)

    font = ImageFont.truetype(str(font_path), args.font_size)
    templates = build_templates(teams, font)

    debug = image.copy()
    results = []

    for z in hud_zones:
        x, y, w, h = scaled_zone(z, base, image.shape)
        crop = image[y : y + h, x : x + w]

        match = match_crop(crop, templates)
        team = match["team"]
        score = float(match["score"])

        status = "ok" if team and score >= args.min_score else "low_score"

        crop_name = f"hud_team_{z['team_index']:02d}_{status}_{team['tag'] if team else 'UNK'}_{score:.2f}.jpg"
        cv2.imwrite(str(crops_dir / crop_name), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])

        color = (0, 255, 0) if status == "ok" else (0, 0, 255)
        label = f"{z['team_index']}:{team['tag'] if team else '?'} {score:.2f}"

        cv2.rectangle(debug, (x, y), (x + w, y + h), color, 2)
        cv2.putText(
            debug,
            label,
            (x, max(0, y - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )

        results.append(
            {
                "hud_team_index": z["team_index"],
                "zone_id": z.get("id"),
                "zone": {"x": x, "y": y, "w": w, "h": h},
                "matched_team_id": team["team_id"] if team else None,
                "matched_team_name": team["name"] if team else None,
                "matched_team_tag": team["tag"] if team else None,
                "matched_alias": match["alias"],
                "score": score,
                "status": status,
            }
        )

    cv2.imwrite(str(out_dir / "hud_debug.jpg"), debug, [cv2.IMWRITE_JPEG_QUALITY, 92])
    save_json(out_dir / "hud_teams_detected.json", {"source_image": str(image_path), "results": results})

    print(f"Saved: {out_dir / 'hud_teams_detected.json'}")
    print(f"Saved: {out_dir / 'hud_debug.jpg'}")

    for r in results:
        print(
            f"HUD {r['hud_team_index']:02d}: "
            f"{(r['matched_team_tag'] or '?'):<6} "
            f"{(r['matched_team_name'] or '?'):<24} "
            f"score={r['score']:.3f} "
            f"{r['status']}"
        )


if __name__ == "__main__":
    main()