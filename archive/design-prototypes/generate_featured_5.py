# -*- coding: utf-8 -*-
"""Generate 5 featured UX/UI multipage variants in mono-pro and dark-esports."""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "featured-5")

COLORS = {
    "dark-esports": {
        "bg": "#0b1020",
        "surface": "#121a30",
        "text": "#e7edf7",
        "muted": "#8ea0c0",
        "a1": "#00d4aa",
        "a2": "#ff4d6d",
    },
    "mono-pro": {
        "bg": "#111111",
        "surface": "#1a1a1a",
        "text": "#fafafa",
        "muted": "#a3a3a3",
        "a1": "#ffffff",
        "a2": "#737373",
    },
}

MAPS = ["Storm Point", "Olympus", "E-District", "World's Edge", "Broken Moon"]

VARIANTS = [
    {
        "slug": "01-mission-control-dark",
        "name": "Mission Control",
        "theme": "dark-esports",
        "concept": "Командный центр: много данных на одном экране",
        "layout": "three-column",
    },
    {
        "slug": "02-cinematic-review-mono",
        "name": "Cinematic Review",
        "theme": "mono-pro",
        "concept": "Кино-режим: карта и таймлайн во фокусе",
        "layout": "cinema",
    },
    {
        "slug": "03-wizard-setup-dark",
        "name": "Wizard Setup",
        "theme": "dark-esports",
        "concept": "Пошаговая настройка матч -> карта -> фильтры -> админ",
        "layout": "wizard",
    },
    {
        "slug": "04-kanban-operations-mono",
        "name": "Kanban Operations",
        "theme": "mono-pro",
        "concept": "Операционный канбан по стадиям обработки карт",
        "layout": "kanban",
    },
    {
        "slug": "05-split-admin-live-dark",
        "name": "Split Admin Live",
        "theme": "dark-esports",
        "concept": "Одновременный live-плеер + админка сбоку",
        "layout": "split-admin",
    },
]


def css(theme: dict, layout: str) -> str:
    base = f"""
:root {{
  --bg: {theme["bg"]};
  --surface: {theme["surface"]};
  --text: {theme["text"]};
  --muted: {theme["muted"]};
  --a1: {theme["a1"]};
  --a2: {theme["a2"]};
  --b: color-mix(in srgb, var(--text) 14%, transparent);
}}
* {{ box-sizing: border-box; margin: 0; }}
body {{ font-family: Inter, system-ui, sans-serif; background: var(--bg); color: var(--text); line-height: 1.45; }}
a {{ color: var(--a1); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.card {{ background: var(--surface); border: 1px solid var(--b); border-radius: 10px; padding: 12px; }}
.logo {{ color: var(--a1); font-weight: 700; letter-spacing: .02em; }}
.muted {{ color: var(--muted); font-size: 12px; }}
.timer {{ color: var(--a1); font-size: 24px; font-variant-numeric: tabular-nums; font-weight: 700; }}
.ring {{ font-size: 12px; border: 1px solid var(--b); border-radius: 999px; padding: 3px 10px; }}
.btn {{ border: 1px solid var(--b); color: var(--text); background: color-mix(in srgb, var(--surface) 78%, transparent); border-radius: 8px; padding: 6px 10px; cursor: pointer; }}
.btn.primary {{ background: var(--a1); border-color: transparent; color: #0a0a0a; font-weight: 600; }}
.map {{
  min-height: 280px;
  border-radius: 10px;
  border: 1px dashed color-mix(in srgb, var(--a1) 38%, var(--b));
  background:
    repeating-linear-gradient(0deg, transparent, transparent 19px, color-mix(in srgb, var(--text) 6%, transparent) 20px),
    repeating-linear-gradient(90deg, transparent, transparent 19px, color-mix(in srgb, var(--text) 6%, transparent) 20px);
  display: grid;
  place-items: center;
  color: var(--muted);
}}
select, input[type="range"] {{ width: 100%; }}
select {{ background: var(--surface); color: var(--text); border: 1px solid var(--b); border-radius: 8px; padding: 7px; }}
label {{ font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); display: block; margin-bottom: 4px; }}
"""
    layouts = {
        "three-column": """
.shell { min-height: 100vh; display: grid; grid-template-columns: 260px 1fr 300px; gap: 12px; padding: 12px; }
@media (max-width:1100px){ .shell { grid-template-columns: 1fr; } }
""",
        "cinema": """
.shell { min-height: 100vh; display: flex; flex-direction: column; gap: 12px; padding: 12px; }
.hud { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; align-items: center; }
.cinema-row { display: grid; grid-template-columns: 1fr 300px; gap: 12px; flex: 1; }
@media (max-width:1100px){ .cinema-row { grid-template-columns: 1fr; } }
""",
        "wizard": """
.shell { min-height: 100vh; display: grid; grid-template-columns: 280px 1fr; gap: 12px; padding: 12px; }
.steps { display: grid; gap: 8px; }
.step { border: 1px solid var(--b); border-radius: 8px; padding: 8px; color: var(--muted); }
.step.active { color: var(--text); border-color: var(--a1); background: color-mix(in srgb, var(--a1) 14%, transparent); }
@media (max-width:1100px){ .shell { grid-template-columns: 1fr; } }
""",
        "kanban": """
.shell { min-height: 100vh; display: flex; flex-direction: column; gap: 12px; padding: 12px; }
.board { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; }
.lane { min-height: 320px; }
.ticket { margin-top: 8px; border: 1px solid var(--b); border-radius: 8px; padding: 8px; }
@media (max-width:1100px){ .board { grid-template-columns: 1fr; } }
""",
        "split-admin": """
.shell { min-height: 100vh; display: grid; grid-template-columns: 1.3fr .9fr; gap: 12px; padding: 12px; }
.left, .right { display: flex; flex-direction: column; gap: 12px; }
@media (max-width:1100px){ .shell { grid-template-columns: 1fr; } }
""",
    }
    return base + layouts[layout]


def nav(v: dict) -> str:
    return f"""
<div class="card">
  <div class="logo">Apex Stats · {v["name"]}</div>
  <p class="muted">{v["concept"]}</p>
  <p class="muted" style="margin-top:8px"><a href="./home.html">Home</a> · <a href="./match.html">Match</a> · <a href="./admin.html">Admin</a> · <a href="../index.html">Все 5</a></p>
</div>
"""


def common_controls() -> str:
    return """
<div class="card">
  <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center">
    <div><div class="timer">12:47</div><div class="ring">R3 · closing</div></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <button class="btn">Play</button>
      <button class="btn">Stop</button>
      <label class="muted">Speed <input type="range" min="0.25" max="2" step="0.25" value="1" /></label>
      <button class="btn primary">Полный экран</button>
    </div>
  </div>
</div>
"""


def filters() -> str:
    return """
<div class="card">
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px">
    <div><label>Команды</label><select><option>Все</option><option>TSM</option><option>NRG</option></select></div>
    <div><label>Остановки: длительность</label><input type="range" /></div>
    <div><label>Остановки: сглаживание</label><input type="range" /></div>
  </div>
</div>
"""


def admin_form() -> str:
    map_opts = "".join(f"<option>{m}</option>" for m in MAPS)
    return f"""
<div class="card">
  <h3>Админ панель</h3>
  <p class="muted">HSV / Zones / Polygons, карта + пресеты + сохранить.</p>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-top:8px">
    <div><label>Карта (1 из 5)</label><select>{map_opts}</select></div>
    <div><label>Пресет</label><select><option>Default</option><option>Broadcast</option><option>Training</option></select></div>
    <div><label>Режим</label><select><option>Auto</option><option>Manual</option></select></div>
  </div>
  <div style="display:flex;gap:6px;margin:10px 0;flex-wrap:wrap">
    <span class="btn">HSV</span><span class="btn">Zones</span><span class="btn">Polygons</span>
  </div>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px">
    <div><label>HSV lower</label><input type="range" /></div>
    <div><label>HSV upper</label><input type="range" /></div>
    <div><label>Сглаживание зон</label><input type="range" /></div>
  </div>
  <div style="margin-top:10px"><button class="btn primary">Сохранить</button></div>
</div>
"""


def page_html(v: dict, title: str, body: str) -> str:
    c = COLORS[v["theme"]]
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{v["name"]} · {title}</title>
  <style>{css(c, v["layout"])}</style>
</head>
<body>{body}</body>
</html>
"""


def home_body(v: dict) -> str:
    if v["layout"] == "three-column":
        return f"""
<div class="shell">
  <aside>{nav(v)}<div class="card"><p class="muted">Турнир -> Матч -> Карта</p></div></aside>
  <main>{common_controls()}<div class="card"><div class="map">Интерактивная карта</div></div>{filters()}</main>
  <section>{admin_form()}</section>
</div>
"""
    if v["layout"] == "cinema":
        return f"""
<div class="shell">
  {nav(v)}
  {common_controls()}
  <div class="cinema-row">
    <section><div class="card"><div class="map" style="min-height:420px">Карта в центре внимания</div></div>{filters()}</section>
    <aside>{admin_form()}</aside>
  </div>
</div>
"""
    if v["layout"] == "wizard":
        return f"""
<div class="shell">
  <aside>{nav(v)}
    <div class="card" style="margin-top:12px">
      <div class="steps">
        <div class="step">1. Турнир</div>
        <div class="step">2. Матч</div>
        <div class="step active">3. Карта</div>
        <div class="step">4. Фильтры</div>
        <div class="step">5. Админка</div>
      </div>
    </div>
  </aside>
  <main>{common_controls()}<div class="card"><div class="map">Step 3: проверка интерактивной карты</div></div>{filters()}</main>
</div>
"""
    if v["layout"] == "kanban":
        return f"""
<div class="shell">
  {nav(v)}
  <div class="board">
    <div class="card lane"><h4>Backlog</h4><div class="ticket">Map 1: Storm Point</div><div class="ticket">Map 2: Olympus</div></div>
    <div class="card lane"><h4>In Progress</h4><div class="ticket">Map 3: E-District</div></div>
    <div class="card lane"><h4>Review</h4><div class="ticket">Map 4: World's Edge</div></div>
    <div class="card lane"><h4>Done</h4><div class="ticket">Map 5: Broken Moon</div></div>
  </div>
  {common_controls()}
</div>
"""
    return f"""
<div class="shell">
  <div class="left">{nav(v)}{common_controls()}<div class="card"><div class="map">Live карта</div></div>{filters()}</div>
  <div class="right">{admin_form()}<div class="card"><div class="map">Preview overlays</div></div></div>
</div>
"""


def match_body(v: dict) -> str:
    return f"""
<div class="shell">
  <aside>{nav(v)}</aside>
  <main class="card">
    <h3>Страница матча</h3>
    <p class="muted">Матч состоит из карт. Быстрый переход между картами и режимами анализа.</p>
    <div style="display:grid;gap:8px;margin-top:10px">
      <div class="card">Карта 1 — завершена</div>
      <div class="card">Карта 2 — активна сейчас</div>
      <div class="card">Карта 3 — ожидание</div>
      <div class="card">Карта 4 — ожидание</div>
      <div class="card">Карта 5 — ожидание</div>
    </div>
  </main>
  <section>{filters()}</section>
</div>
"""


def admin_body(v: dict) -> str:
    return f"""
<div class="shell">
  <aside>{nav(v)}</aside>
  <main>{admin_form()}</main>
  <section><div class="card"><div class="map">Область редактирования HSV / Zones / Polygons</div></div></section>
</div>
"""


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for v in VARIANTS:
        folder = os.path.join(OUT, v["slug"])
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "home.html"), "w", encoding="utf-8") as f:
            f.write(page_html(v, "Home", home_body(v)))
        with open(os.path.join(folder, "match.html"), "w", encoding="utf-8") as f:
            f.write(page_html(v, "Match", match_body(v)))
        with open(os.path.join(folder, "admin.html"), "w", encoding="utf-8") as f:
            f.write(page_html(v, "Admin", admin_body(v)))
        rows.append(
            f'<li><strong>{v["name"]}</strong> ({v["theme"]}) — {v["concept"]} · '
            f'<a href="./{v["slug"]}/home.html">home</a> / '
            f'<a href="./{v["slug"]}/match.html">match</a> / '
            f'<a href="./{v["slug"]}/admin.html">admin</a></li>'
        )

    idx = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Featured 5 — UX/UI</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 1000px; margin: 36px auto; padding: 0 16px; line-height: 1.45; }}
    li {{ margin: 8px 0; }}
    .muted {{ color: #555; }}
  </style>
</head>
<body>
  <h1>5 разных UX/UI дизайнов</h1>
  <p class="muted">Палитры: mono-pro и dark-esports. Отличаются не только цветом, но и информационной архитектурой и layout-паттернами.</p>
  <ul>{''.join(rows)}</ul>
</body>
</html>"""
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(idx)

    print(f"Wrote {len(VARIANTS)} featured variants into {OUT}")


if __name__ == "__main__":
    main()
