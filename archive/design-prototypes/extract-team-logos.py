"""Export first N team logos from output/teams_name_tag_logo.sqlite into designs/team-logos/.

After running, copy teams-manifest.json entries into TEAMS_MANIFEST in design-18-paper-cards.html
(or extend the page to fetch this JSON if you serve over HTTP).
"""
import json, os, re, sqlite3, sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
DB = os.path.join(ROOT, "output", "teams_name_tag_logo.sqlite")
OUT_DIR = os.path.join(os.path.dirname(__file__), "team-logos")
N = 20


def slug_tag(tag: str) -> str:
    t = re.sub(r"[^a-zA-Z0-9_-]+", "_", (tag or "").strip())[:32]
    return t or "team"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT name, tag, logo FROM "Teams" WHERE logo IS NOT NULL ORDER BY name COLLATE NOCASE',
    ).fetchall()
    manifest = []
    for i, r in enumerate(rows):
        if len(manifest) >= N:
            break
        name = r["name"] or ""
        tag = r["tag"] or ""
        blob = r["logo"]
        if not blob or not isinstance(blob, (bytes, memoryview)):
            continue
        b = bytes(blob)
        ext = ".png"
        if len(b) >= 3 and b[:3] == b"\xff\xd8\xff":
            ext = ".jpg"
        base = f"{len(manifest):02d}_{slug_tag(tag)}"
        path = os.path.join(OUT_DIR, base + ext)
        with open(path, "wb") as f:
            f.write(b)
        rel = "team-logos/" + base + ext
        manifest.append({"name": name, "tag": tag, "logo": rel})

    man_path = os.path.join(os.path.dirname(__file__), "teams-manifest.json")
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("wrote", len(manifest), "logos to", OUT_DIR, file=sys.stderr)
    print("manifest", man_path, file=sys.stderr)


if __name__ == "__main__":
    main()
