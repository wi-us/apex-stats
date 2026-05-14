# -*- coding: utf-8 -*-
"""Generate 20 multipage placeholder site variants."""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(ROOT, "multipage")

MAPS = ["Storm Point", "Olympus", "E-District", "World's Edge", "Broken Moon"]

THEMES = [
    {"name": "dark-esports", "bg": "#0b1020", "surface": "#121a30", "text": "#e7edf7", "muted": "#8ea0c0", "a1": "#00d4aa", "a2": "#ff4d6d", "r": "8px"},
    {"name": "light-ops", "bg": "#f5f7fb", "surface": "#ffffff", "text": "#111827", "muted": "#4b5563", "a1": "#2563eb", "a2": "#f97316", "r": "8px"},
    {"name": "neon-grid", "bg": "#140b1f", "surface": "#201033", "text": "#f5eefe", "muted": "#b088d8", "a1": "#e879f9", "a2": "#22d3ee", "r": "12px"},
    {"name": "forest-hud", "bg": "#0c1f16", "surface": "#153126", "text": "#d8f8e1", "muted": "#75b68a", "a1": "#4ade80", "a2": "#eab308", "r": "6px"},
    {"name": "mono-pro", "bg": "#111111", "surface": "#1a1a1a", "text": "#fafafa", "muted": "#a3a3a3", "a1": "#ffffff", "a2": "#737373", "r": "4px"},
]

STRUCTURES = [
    "sidebar-dashboard",
    "topnav-analytics",
    "dual-column-split",
    "focus-map-center",
    "kanban-steps",
]

SHAPES = [
    {"name": "soft", "card": "16px", "btn": "999px"},
    {"name": "sharp", "card": "2px", "btn": "2px"},
    {"name": "angled", "card": "12px 2px 12px 2px", "btn": "12px 2px"},
    {"name": "terminal", "card": "0px", "btn": "0px"},
]


def css_for(theme: dict, structure: str, shape: dict) -> str:
    layout = {
        "sidebar-dashboard": """
  .shell { display:grid; grid-template-columns: 270px 1fr; min-height:100vh; }
  .nav { border-right:1px solid var(--b); padding:14px; }
  .main { padding:14px; display:flex; flex-direction:column; gap:12px; }
  @media (max-width: 980px){ .shell{ grid-template-columns: 1fr; } .nav{ border-right:none; border-bottom:1px solid var(--b);} }
""",
        "topnav-analytics": """
  .shell { min-height:100vh; display:flex; flex-direction:column; }
  .nav { border-bottom:1px solid var(--b); padding:12px 16px; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .main { padding:14px; display:grid; grid-template-columns: 2fr 1fr; gap:12px; }
  .main > .full { grid-column: 1 / -1; }
  @media (max-width: 980px){ .main{ grid-template-columns: 1fr; } }
""",
        "dual-column-split": """
  .shell { min-height:100vh; padding:14px; display:grid; grid-template-columns: 1.4fr 1fr; gap:12px; }
  .nav { grid-column: 1 / -1; display:flex; gap:10px; flex-wrap:wrap; }
  .main { display:contents; }
  @media (max-width: 980px){ .shell{ grid-template-columns: 1fr; } }
""",
        "focus-map-center": """
  .shell { min-height:100vh; padding:18px; display:flex; flex-direction:column; gap:12px; }
  .nav { display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; }
  .main { display:grid; grid-template-columns: 220px 1fr 260px; gap:12px; flex:1; }
  @media (max-width: 1080px){ .main{ grid-template-columns: 1fr; } }
""",
        "kanban-steps": """
  .shell { min-height:100vh; padding:14px; display:flex; flex-direction:column; gap:12px; }
  .nav { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .main { display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:12px; }
  .main > .full { grid-column: 1 / -1; }
  @media (max-width: 980px){ .main{ grid-template-columns: 1fr; } }
""",
    }[structure]

    return f"""
:root {{
  --bg: {theme["bg"]};
  --surface: {theme["surface"]};
  --text: {theme["text"]};
  --muted: {theme["muted"]};
  --a1: {theme["a1"]};
  --a2: {theme["a2"]};
  --card-r: {shape["card"]};
  --btn-r: {shape["btn"]};
  --b: color-mix(in srgb, var(--text) 14%, transparent);
}}
* {{ box-sizing:border-box; margin:0; }}
body {{ font-family: Inter, system-ui, sans-serif; background:var(--bg); color:var(--text); line-height:1.4; }}
a {{ color:var(--a1); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
{layout}
.card {{ background:var(--surface); border:1px solid var(--b); border-radius:var(--card-r); padding:12px; }}
.logo {{ font-weight:700; color:var(--a1); letter-spacing:.02em; }}
.muted {{ color:var(--muted); font-size:12px; }}
.tree {{ display:grid; gap:6px; margin-top:10px; }}
.item {{ padding:6px 8px; border:1px solid var(--b); border-radius:calc(var(--card-r) / 1.5); color:var(--muted); }}
.item.active {{ color:var(--text); background:color-mix(in srgb, var(--a1) 20%, transparent); border-color:var(--a1); }}
.map {{ min-height:300px; border:1px dashed color-mix(in srgb, var(--a1) 40%, var(--b)); border-radius:var(--card-r); display:grid; place-items:center;
  background:repeating-linear-gradient(0deg, transparent, transparent 19px, color-mix(in srgb, var(--text) 6%, transparent) 20px),
             repeating-linear-gradient(90deg, transparent, transparent 19px, color-mix(in srgb, var(--text) 6%, transparent) 20px);
}}
.hud {{ display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; align-items:center; }}
.timer {{ font-size:26px; color:var(--a1); font-weight:700; font-variant-numeric: tabular-nums; }}
.ring {{ font-size:12px; padding:4px 10px; border:1px solid var(--b); border-radius:999px; }}
.btn {{ border:1px solid var(--b); border-radius:var(--btn-r); padding:6px 10px; background:color-mix(in srgb, var(--surface) 75%, transparent); color:var(--text); cursor:pointer; }}
.btn.primary {{ background:var(--a1); border-color:transparent; color:#0a0a0a; font-weight:600; }}
.controls {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
.filters {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:10px; }}
label {{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; display:block; margin-bottom:4px; }}
select, input[type="range"] {{ width:100%; }}
select {{ background:var(--surface); color:var(--text); border:1px solid var(--b); border-radius:calc(var(--btn-r)); padding:7px; }}
.tabs {{ display:flex; gap:6px; flex-wrap:wrap; margin:8px 0; }}
.tab {{ border:1px solid var(--b); border-radius:calc(var(--btn-r)); padding:5px 8px; font-size:12px; color:var(--muted); }}
.tab.on {{ color:var(--text); border-color:var(--a2); background: color-mix(in srgb, var(--a2) 22%, transparent); }}
"""


def page_template(variant_name: str, title: str, css: str, nav_links: str, content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title} — {variant_name}</title>
  <style>{css}</style>
</head>
<body>
  <div class="shell">
    <nav class="nav card">
      <div class="logo">Apex Stats · {variant_name}</div>
      <p class="muted">Турнир → матч → карта · 5 карт · пресеты · сохранить</p>
      {nav_links}
    </nav>
    <main class="main">{content}</main>
  </div>
</body>
</html>
"""


def nav_block() -> str:
    return """
<div class="tree">
  <div class="item active">ALGS Split 2</div>
  <div class="item">Day 3 — Match 1</div>
  <div class="item">Map 2 / 6</div>
  <div class="item"><a href="home.html">Home</a></div>
  <div class="item"><a href="match.html">Match Room</a></div>
  <div class="item"><a href="admin.html">Admin Panel</a></div>
</div>
"""


def home_content() -> str:
    return """
<section class="card full">
  <div class="hud">
    <div><div class="timer">14:09</div><div class="ring">Кольцо: R3 · closing</div></div>
    <div class="controls">
      <button class="btn">Play</button><button class="btn">Stop</button>
      <label class="muted">Скорость <input type="range" min="0.25" max="2" step="0.25" value="1" /></label>
      <button class="btn primary">Полный экран</button>
    </div>
  </div>
</section>
<section class="card"><div class="map">Интерактивная карта (пустышка)</div></section>
<section class="card">
  <div class="filters">
    <div><label>Команды</label><select><option>Все</option><option>TSM</option><option>NRG</option></select></div>
    <div><label>Остановки: длительность</label><input type="range" /></div>
    <div><label>Остановки: сглаживание</label><input type="range" /></div>
  </div>
</section>
"""


def match_content() -> str:
    return """
<section class="card">
  <h3>Матч как сценарий</h3>
  <p class="muted">Отдельная страница матча: список карт, быстрые переходы, состояние каждой карты.</p>
  <div class="tree" style="margin-top:8px">
    <div class="item active">Карта 1 — завершена</div>
    <div class="item">Карта 2 — сейчас в работе</div>
    <div class="item">Карта 3 — ожидание</div>
    <div class="item">Карта 4 — ожидание</div>
    <div class="item">Карта 5 — ожидание</div>
  </div>
</section>
<section class="card">
  <div class="map">Мини-карта матча (таймлайн + обзор)</div>
</section>
<section class="card">
  <div class="filters">
    <div><label>Режим просмотра</label><select><option>Live</option><option>Review</option></select></div>
    <div><label>Скорость пакетной прокрутки</label><input type="range" /></div>
    <div><label>Фокус команды</label><select><option>Все</option><option>Alliance</option><option>FNC</option></select></div>
  </div>
</section>
"""


def admin_content() -> str:
    map_options = "".join(f"<option>{m}</option>" for m in MAPS)
    return f"""
<section class="card">
  <h3>Админ панель</h3>
  <p class="muted">Отдельная страница конфигурации для HSV / Zones / Polygons с пресетами.</p>
  <div class="filters" style="margin-top:8px">
    <div><label>Карта (1 из 5)</label><select>{map_options}</select></div>
    <div><label>Пресет</label><select><option>Default</option><option>Broadcast</option><option>Training</option></select></div>
    <div><label>Источник данных</label><select><option>Auto</option><option>Manual</option></select></div>
  </div>
  <div class="tabs"><span class="tab on">HSV</span><span class="tab">Zones</span><span class="tab">Polygons</span></div>
  <div class="filters">
    <div><label>HSV lower</label><input type="range" /></div>
    <div><label>HSV upper</label><input type="range" /></div>
    <div><label>Сглаживание зон</label><input type="range" /></div>
  </div>
  <div style="margin-top:10px"><button class="btn primary">Сохранить</button></div>
</section>
<section class="card"><div class="map">Canvas предпросмотра админ-настроек</div></section>
"""


def build_variant(idx: int, theme: dict, structure: str, shape: dict) -> dict:
    num = f"{idx:02d}"
    slug = f"{num}-{theme['name']}-{structure}-{shape['name']}"
    title = f"Вариант {num}"
    css = css_for(theme, structure, shape)
    nav = nav_block()
    return {
        "slug": slug,
        "title": title,
        "theme": theme["name"],
        "structure": structure,
        "shape": shape["name"],
        "home": page_template(slug, "Home", css, nav, home_content()),
        "match": page_template(slug, "Match", css, nav, match_content()),
        "admin": page_template(slug, "Admin", css, nav, admin_content()),
    }


def index_page(variants: list[dict]) -> str:
    rows = []
    for v in variants:
        rows.append(
            f'<li><strong>{v["title"]}</strong> · {v["theme"]} · {v["structure"]} · форма: {v["shape"]} '
            f'— <a href="./{v["slug"]}/home.html">home</a> / <a href="./{v["slug"]}/match.html">match</a> / '
            f'<a href="./{v["slug"]}/admin.html">admin</a></li>'
        )
    body = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Multipage макеты</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 980px; margin: 36px auto; padding: 0 16px; line-height: 1.45; }}
    ul {{ padding-left: 1.2rem; }}
    li {{ margin: 8px 0; }}
    .muted {{ color:#555; }}
  </style>
</head>
<body>
  <h1>20 multipage-вариантов</h1>
  <p class="muted">Здесь отличаются не только цвета, но и структура экрана, плотность, формы карточек и кнопок. Каждый вариант имеет 3 страницы.</p>
  <ul>{body}</ul>
</body>
</html>"""


def main() -> None:
    os.makedirs(OUT_ROOT, exist_ok=True)
    variants = []
    idx = 1
    for shape in SHAPES:
        for structure in STRUCTURES:
            theme = THEMES[(idx - 1) % len(THEMES)]
            variants.append(build_variant(idx, theme, structure, shape))
            idx += 1
            if idx > 20:
                break
        if idx > 20:
            break

    for v in variants:
        variant_dir = os.path.join(OUT_ROOT, v["slug"])
        os.makedirs(variant_dir, exist_ok=True)
        with open(os.path.join(variant_dir, "home.html"), "w", encoding="utf-8") as f:
            f.write(v["home"])
        with open(os.path.join(variant_dir, "match.html"), "w", encoding="utf-8") as f:
            f.write(v["match"])
        with open(os.path.join(variant_dir, "admin.html"), "w", encoding="utf-8") as f:
            f.write(v["admin"])

    with open(os.path.join(OUT_ROOT, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_page(variants))

    print(f"Wrote {len(variants)} variants into {OUT_ROOT}")


if __name__ == "__main__":
    main()
