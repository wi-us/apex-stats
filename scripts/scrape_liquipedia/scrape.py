#!/usr/bin/env python3
"""
Liquipedia Apex Legends tournament scraper.

Шаги:
  1) Грузит индекс турниров (по умолчанию A-Tier 2025).
  2) Для каждого турнира:
     - открывает страницу на широком viewport (1400x1080);
     - сначала ищет финальный battle-royale блок внутри
       `.mw-content-ltr.mw-parser-output` и собирает ровно его команды/игры;
     - если такого блока нет, использует fallback на `.standings-ffa`.
  3) Сохраняет всё в JSON-кэш под `--out`.

После скрейпа работаем с JSON-кэшем.

Usage:
  python scrape.py --out data --tier A --year 2025
  python scrape.py --out data --index-url https://liquipedia.net/apexlegends/S-Tier_Tournaments/2025
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import Browser, Page, sync_playwright

BASE = "https://liquipedia.net"
UA = (
    "Mozilla/5.0 (LovableScraper/1.0; +contact: project-owner) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0"
)
SLEEP = 2.5  # be polite, Liquipedia ToS asks for >=2s between requests


def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "unknown"


# --------------------------------------------------------------------------- #
# Map name normalization
# --------------------------------------------------------------------------- #
# Keys match files in scripts/tracking/shared/canonical_maps/*.json
_MAP_ALIASES: dict[str, str] = {
    "storm point": "storm_point",
    "world's edge": "worlds_edge",
    "worlds edge": "worlds_edge",
    "e-district": "e_district",
    "edistrict": "e_district",
    "broken moon": "broken_moon",
    "olympus": "olympus",
    "kings canyon": "kings_canyon",
}


def normalize_map(name: str | None) -> str | None:
    if not name:
        return None
    key = re.sub(r"\s+", " ", name).strip().lower()
    return _MAP_ALIASES.get(key)


def load(page: Page, url: str, wait: str = "domcontentloaded") -> str:
    page.goto(url, wait_until=wait, timeout=60_000)
    # Wait for the main content table to render; fall back to a short sleep.
    try:
        page.wait_for_selector("table.table2__table, div.tournaments-listing, div.standings-ffa", timeout=15_000)
    except Exception:
        pass
    time.sleep(SLEEP)
    return page.content()


# --------------------------------------------------------------------------- #
# Index: A-Tier_Tournaments/2025
# --------------------------------------------------------------------------- #
def parse_index(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    containers = soup.select("div.tournaments-listing")
    out: list[dict[str, Any]] = []
    for cont in containers:
        table = cont.select_one("table.table2__table")
        if not table:
            continue
        # Heuristic column detection from header
        header_cells = [
            (th.get_text(" ", strip=True) or "").lower()
            for th in table.select("thead th, tr:first-child th")
        ]

        def col(*keys: str) -> int | None:
            for k in keys:
                for i, h in enumerate(header_cells):
                    if k in h:
                        return i
            return None

        c_name = col("tournament", "name")
        c_date = col("date")
        c_loc = col("location")

        for tr in table.select("tbody tr, tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            name_cell = tds[c_name] if c_name is not None and c_name < len(tds) else tds[0]
            a = name_cell.find("a", href=True)
            if not a:
                continue
            href = a["href"]
            if not href.startswith("/apexlegends/"):
                continue
            name = a.get_text(" ", strip=True)
            dates = (
                tds[c_date].get_text(" ", strip=True)
                if c_date is not None and c_date < len(tds)
                else ""
            )
            location = (
                tds[c_loc].get_text(" ", strip=True)
                if c_loc is not None and c_loc < len(tds)
                else ""
            )
            out.append(
                {
                    "name": name,
                    "url": urljoin(BASE, href),
                    "slug": slugify(href.replace("/apexlegends/", "")),
                    "dates_text": dates,
                    "location": location,
                }
            )
    # dedupe by slug, keep first
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for t in out:
        if t["slug"] in seen:
            continue
        seen.add(t["slug"])
        uniq.append(t)
    return uniq


# --------------------------------------------------------------------------- #
# Tournament page: standings-ffa + game tabs
# --------------------------------------------------------------------------- #
def _is_team_href(href: str) -> bool:
    """True if href looks like a real Liquipedia team article."""
    if not href.startswith("/apexlegends/"):
        return False
    if "Special:" in href or "index.php" in href or "?" in href:
        return False
    return True


def _parse_place(text: str) -> int | None:
    m = re.match(r"\s*(\d+)", text or "")
    return int(m.group(1)) if m else None


def _parse_int(text: str) -> int | None:
    m = re.search(r"-?\d+", text or "")
    return int(m.group(0)) if m else None


def _looks_like_tag(text: str | None) -> bool:
    if not text:
        return False
    compact = re.sub(r"[^A-Za-z0-9]", "", text)
    return 1 <= len(compact) <= 6 and compact.upper() == compact


def _team_from_row(tr) -> dict[str, Any] | None:
    """Extract team info from one standings <tr>.

    Liquipedia renders the team cell as a `div.block-team` with:
      - `span.team-template-image-icon` → icon links (no text)
      - `span.name.hidden-xs` → full team name
      - `span.name.visible-xs` → abbreviation / tag
    All three are present in the DOM regardless of viewport; CSS hides them.
    """
    bt = tr.select_one("div.block-team") or tr
    # Logo (lightmode preferred, then any image in the icon span)
    logo_url: str | None = None
    icon = bt.select_one(
        "span.team-template-image-icon.team-template-lightmode img, "
        "span.team-template-image-icon img"
    )
    if icon:
        src = icon.get("src") or icon.get("data-src")
        if src:
            logo_url = urljoin(BASE, src)

    # Real team link (skip redlinks / icon-only links that point to /index.php?...)
    team_a = None
    for a in bt.find_all("a", href=True):
        if _is_team_href(a["href"]):
            team_a = a
            break
    if team_a is None:
        return None
    team_href = urljoin(BASE, team_a["href"])
    slug = slugify(team_a["href"].replace("/apexlegends/", ""))

    name_span = bt.select_one("span.name.hidden-xs") or bt.select_one("span.name")
    tag_span = bt.select_one("span.name.visible-xs")
    name = name_span.get_text(" ", strip=True) if name_span else team_a.get_text(" ", strip=True)
    tag = tag_span.get_text(" ", strip=True) if tag_span else None
    # Teams that are already short (TSM/CIMJ/DGAP/etc.) often render as one
    # `span.name`; keep that value as the tag instead of dropping it.
    if not tag and _looks_like_tag(name):
        tag = name
    elif tag and tag == name and not _looks_like_tag(tag):
        tag = None

    # Place from first table cell or battle-royale rank cell.
    tds = tr.find_all("td")
    place: int | None = None
    if tds:
        place = _parse_place(tds[0].get_text(" ", strip=True))
    if place is None:
        rank_cell = tr.select_one(".cell--rank")
        if rank_cell:
            place = _parse_place(rank_cell.get_text(" ", strip=True))

    return {
        "place": place,
        "slug": slug,
        "name": name,
        "tag": tag,
        "logo_url": logo_url,
        "url": team_href,
    }


def _extract_battle_royale(br) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract Final battle-royale standings from Liquipedia's panel table.

    This is the block under `.mw-content-ltr.mw-parser-output` with rows like
    `div.panel-table__row[data-js-battle-royale="row"]`. It contains exactly
    the final lobby teams plus one nested `.cell--game` per played game.
    """
    rows = br.select('div.panel-table__row[data-js-battle-royale="row"]')
    if not rows:
        return [], []

    header = br.select_one("div.panel-table__row.row--header")
    game_labels: list[str] = []
    if header:
        for cell in header.select(":scope > div.cell--game-container div.cell--game"):
            label = cell.select_one(".panel-table__cell-text")
            game_labels.append(label.get_text(" ", strip=True) if label else f"Game {len(game_labels) + 1}")

    teams: list[dict[str, Any]] = []
    games: list[dict[str, Any]] = [
        {"game_no": i + 1, "tab_id": str(i + 1), "label": label or f"Game {i + 1}", "participants": []}
        for i, label in enumerate(game_labels)
    ]
    for row in rows:
        info = _team_from_row(row)
        if info is None:
            continue
        teams.append({k: info[k] for k in ("slug", "name", "tag", "logo_url", "url")})
        for i, game_cell in enumerate(row.select(":scope > div.cell--game-container > div.cell--game")):
            while i >= len(games):
                games.append({"game_no": i + 1, "tab_id": str(i + 1), "label": f"Game {i + 1}", "participants": []})
            placement = game_cell.select_one(".panel-table__cell__game-placement")
            kills = game_cell.select_one(".panel-table__cell__game-kills")
            games[i]["participants"].append(
                {
                    "place": _parse_place(placement.get_text(" ", strip=True) if placement else ""),
                    "team_slug": info["slug"],
                    "team_text": info["name"],
                    "kills": _parse_int(kills.get_text(" ", strip=True) if kills else ""),
                }
            )

    for game in games:
        game["participants"].sort(key=lambda p: p["place"] if p["place"] is not None else 9999)
    return teams, games


def extract_teams_and_games(html: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse the standings-ffa table once.

    Liquipedia stores per-game placements as separate <tr>s tagged with
    `data-toggle-area-content="<game_no>"`. Tabs in `ul.panel-tabs__list`
    label them (item 0 = "Overall standings", item N = "Game N").
    """
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("div.mw-content-ltr.mw-parser-output") or soup
    battle_blocks = content.select("div.battle-royale[data-js-battle-royale-id]")
    for br in reversed(battle_blocks):
        teams, games = _extract_battle_royale(br)
        if teams and games:
            return teams, games

    cont = content.select_one("div.standings-ffa")
    if not cont:
        return [], []
    table = cont.select_one("table.table2__table")
    if not table:
        return [], []

    # Build {tab_index_str: label} from the panel tabs.
    tab_labels: dict[str, str] = {}
    ul = cont.find_previous("ul", class_="panel-tabs__list") or soup.select_one("ul.panel-tabs__list")
    if ul:
        for idx, li in enumerate(ul.select("li.panel-tabs__list-item, li")):
            tab_labels[str(idx)] = li.get_text(" ", strip=True)

    # Walk all body rows, bucket by data-toggle-area-content.
    games_buckets: dict[str, list[dict[str, Any]]] = {}
    teams_by_slug: dict[str, dict[str, Any]] = {}
    for tr in table.select("tr"):
        if not tr.find("td"):
            continue
        info = _team_from_row(tr)
        if info is None:
            continue
        toggle = tr.get("data-toggle-area-content")
        if toggle:
            games_buckets.setdefault(toggle, []).append(info)
        # Aggregate unique team list.
        prev = teams_by_slug.get(info["slug"])
        if prev is None:
            teams_by_slug[info["slug"]] = {
                "slug": info["slug"],
                "name": info["name"],
                "tag": info["tag"],
                "logo_url": info["logo_url"],
                "url": info["url"],
            }
        else:
            # Fill in fields that were missing in earlier rows.
            for k in ("name", "tag", "logo_url"):
                if not prev.get(k) and info.get(k):
                    prev[k] = info[k]

    games: list[dict[str, Any]] = []
    for key in sorted(games_buckets.keys(), key=lambda x: int(x) if x.isdigit() else 9999):
        participants = [
            {"place": p["place"], "team_slug": p["slug"], "team_text": p["name"]}
            for p in games_buckets[key]
        ]
        label = tab_labels.get(key) or f"Game {key}"
        games.append(
            {
                "game_no": int(key) if key.isdigit() else None,
                "tab_id": key,
                "label": label,
                "participants": participants,
            }
        )

    teams = sorted(teams_by_slug.values(), key=lambda x: x["name"].lower())
    return teams, games


def extract_tournament_name(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.select_one("h1#firstHeading, h1.firstHeading")
    if h1:
        return h1.get_text(" ", strip=True) or None
    return None


# --------------------------------------------------------------------------- #
# Game schedule: date + map per game
# --------------------------------------------------------------------------- #
def _ts_to_iso(ts: str | int | None) -> str | None:
    if ts is None:
        return None
    try:
        n = int(str(ts).strip())
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _map_name_from_node(node) -> str | None:
    """Pick a map name out of a schedule row.

    Liquipedia variants: <a title="Storm Point">, <span class="map">…</span>,
    or a plain <a> linking to the map page.
    """
    if node is None:
        return None
    # Prefer explicit map elements first.
    for sel in (".panel-content__game-schedule__game-map", ".game-schedule__map", ".cell--map"):
        el = node.select_one(sel)
        if el:
            txt = el.get_text(" ", strip=True)
            if txt:
                return txt
    # Fall back to any link with a known map name in title/text.
    for a in node.find_all("a"):
        title = (a.get("title") or "").strip()
        if normalize_map(title):
            return title
        txt = a.get_text(" ", strip=True)
        if normalize_map(txt):
            return txt
    return None


def _extract_game_schedule(content) -> dict[int, dict[str, Any]]:
    """Return {game_no: {date, map, map_id}} from any panel-content__game-schedule
    block under `content`.
    """
    out: dict[int, dict[str, Any]] = {}
    blocks = content.select("div.panel-content__game-schedule")
    for block in blocks:
        rows = block.select(
            "div.panel-content__game-schedule__container, "
            "div.panel-content__game-schedule__game, "
            "tr"
        )
        for i, row in enumerate(rows, start=1):
            # Game number: look for "Game N" text, then fall back to row order.
            game_no: int | None = None
            m = re.search(r"\bGame\s*(\d+)\b", row.get_text(" ", strip=True), re.IGNORECASE)
            if m:
                game_no = int(m.group(1))
            else:
                game_no = i
            timer = row.select_one("span.timer-object[data-timestamp]")
            date = _ts_to_iso(timer.get("data-timestamp")) if timer else None
            map_name = _map_name_from_node(row)
            map_id = normalize_map(map_name)
            if date is None and map_name is None:
                continue
            out.setdefault(game_no, {"date": date, "map": map_name, "map_id": map_id})
    return out


# --------------------------------------------------------------------------- #
# POI Drafts (optional section: stage × map → list of picks)
# --------------------------------------------------------------------------- #
def _poi_section_root(soup) -> Any | None:
    """Locate the wrapper that contains the POI Drafts tabs + tables."""
    anchor = soup.select_one("#POI_Drafts, span#POI_Drafts")
    if not anchor:
        return None
    # Walk up to the heading, then take its next siblings until the next h2.
    h = anchor
    while h is not None and h.name not in ("h2", "h3"):
        h = h.parent
    if h is None:
        return h
    # Collect siblings until next h2/h3.
    frag = BeautifulSoup("<div></div>", "html.parser")
    holder = frag.div
    for sib in h.next_siblings:
        if getattr(sib, "name", None) in ("h2", "h3"):
            break
        holder.append(sib if isinstance(sib, str) else sib.__copy__())  # type: ignore[attr-defined]
    return holder


_STAGE_KEYS = {
    "regular season": "regular",
    "regular": "regular",
    "finals": "finals",
    "final": "finals",
    "playoffs": "playoffs",
    "playoff": "playoffs",
    "group stage": "groups",
    "groups": "groups",
}


def _stage_key(label: str) -> str:
    k = re.sub(r"\s+", " ", label or "").strip().lower()
    return _STAGE_KEYS.get(k, slugify(k))


def _parse_poi_table(table) -> list[dict[str, Any]]:
    """Parse the right-hand POI table (Rotation | Draft # | Team | Spot Picked)."""
    rows: list[dict[str, Any]] = []
    headers = [th.get_text(" ", strip=True).lower() for th in table.select("thead th, tr:first-child th")]

    def col(*keys: str) -> int | None:
        for k in keys:
            for i, h in enumerate(headers):
                if k in h:
                    return i
        return None

    c_rot = col("rotation")
    c_draft = col("draft")
    c_team = col("team")
    c_spot = col("spot")

    for tr in table.select("tbody tr, tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        team_cell = tds[c_team] if c_team is not None and c_team < len(tds) else None
        if team_cell is None:
            continue
        a = next(
            (a for a in team_cell.find_all("a", href=True) if _is_team_href(a["href"])),
            None,
        )
        if a is None:
            continue
        team_name = a.get_text(" ", strip=True)
        team_slug = slugify(a["href"].replace("/apexlegends/", ""))
        rotation_txt = tds[c_rot].get_text(" ", strip=True) if c_rot is not None and c_rot < len(tds) else ""
        draft_txt = tds[c_draft].get_text(" ", strip=True) if c_draft is not None and c_draft < len(tds) else ""
        spot = tds[c_spot].get_text(" ", strip=True) if c_spot is not None and c_spot < len(tds) else ""
        rows.append(
            {
                "rotation": _parse_int(rotation_txt),
                "draft_no": _parse_int(draft_txt),
                "team_slug": team_slug,
                "team_name": team_name,
                "spot": spot or None,
            }
        )
    return rows


def _extract_poi_drafts(soup) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Parse POI Drafts section. Returns {stage_key: {map_id: [picks]}}.

    Liquipedia renders the section as nested toggle areas:
      - outer toggle = stage (Regular Season / Finals)
      - inner toggle = map (Storm Point / World's Edge / E-District)
    Each map panel contains the POI map image on the left and the picks table
    on the right. We only care about the table.
    """
    root = _poi_section_root(soup)
    if root is None:
        return {}

    out: dict[str, dict[str, list[dict[str, Any]]]] = {}

    # Look for nested tabs structure. Each `.tabs-static` is one tab strip;
    # `[data-toggle-area-content]` rows / divs are the content panels.
    stage_tabs = root.select("ul.tabs-static")
    if not stage_tabs:
        # No tab strip — there might be a single stage + single map table.
        for table in root.select("table.wikitable, table.table2__table"):
            picks = _parse_poi_table(table)
            if picks:
                out.setdefault("default", {})["default"] = picks
        return out

    # Walk every toggle-area-content panel, then for each: find its containing
    # stage label + map label by walking up through enclosing `data-toggle-area`s.
    for panel in root.select("[data-toggle-area-content]"):
        # Identify stage + map by climbing parents that have `data-toggle-area`
        # along with the matching tab strip's <li>.
        labels: list[str] = []
        node = panel
        while node is not None and getattr(node, "name", None) is not None:
            ta_id = node.get("data-toggle-area-content") if hasattr(node, "get") else None
            if ta_id:
                area = node.find_parent(attrs={"data-toggle-area": True})
                if area is not None:
                    li_list = area.select("ul.tabs-static > li, ul.panel-tabs__list > li")
                    try:
                        idx = int(ta_id) - 1
                    except ValueError:
                        idx = -1
                    if 0 <= idx < len(li_list):
                        labels.append(li_list[idx].get_text(" ", strip=True))
            node = node.parent

        # Outer label = stage, inner = map (panels are reached innermost-first).
        stage_label = labels[-1] if labels else "default"
        map_label = labels[0] if labels else "default"

        stage = _stage_key(stage_label)
        map_id = normalize_map(map_label) or slugify(map_label)

        for table in panel.select("table.wikitable, table.table2__table"):
            picks = _parse_poi_table(table)
            if not picks:
                continue
            out.setdefault(stage, {}).setdefault(map_id, []).extend(picks)

    return out


def scrape_tournament(browser: Browser, t: dict[str, Any]) -> dict[str, Any]:
    """Returns enriched tournament dict with teams + games.

    Liquipedia exposes both the full team name (`span.name.hidden-xs`) and the
    short tag (`span.name.visible-xs`) in the DOM regardless of viewport, and
    splits per-game standings via `data-toggle-area-content` on each row, so
    one render + one parse is enough.
    """
    ctx = browser.new_context(viewport={"width": 1400, "height": 1080}, user_agent=UA)
    page = ctx.new_page()
    try:
        html = load(page, t["url"])
    finally:
        ctx.close()

    teams, games = extract_teams_and_games(html)
    page_name = extract_tournament_name(html)

    # Enrich each game with date + map from the schedule panel.
    soup_full = BeautifulSoup(html, "html.parser")
    content_root = soup_full.select_one("div.mw-content-ltr.mw-parser-output") or soup_full
    schedule = _extract_game_schedule(content_root)
    if schedule:
        for g in games:
            entry = schedule.get(g.get("game_no"))
            if entry:
                g["date"] = entry.get("date")
                g["map"] = entry.get("map")
                g["map_id"] = entry.get("map_id")

    # Optional POI Drafts section (priority info when available).
    poi_drafts = _extract_poi_drafts(soup_full)

    merged = {**t}
    if not merged.get("name") and page_name:
        merged["name"] = page_name
    merged["teams"] = teams
    merged["games"] = games
    merged["poi_drafts"] = poi_drafts
    merged["has_poi_drafts"] = bool(poi_drafts)
    return merged


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "data")
    ap.add_argument("--tier", default="A", help="A, S, B, ... (used if --index-url not given)")
    ap.add_argument("--year", default="2025")
    ap.add_argument("--index-url", default=None)
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--only", default=None, help="comma-separated tournament slugs to scrape")
    ap.add_argument("--force", action="store_true", help="re-scrape even if cache exists")
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--headed", dest="headless", action="store_false")
    args = ap.parse_args()

    index_url = args.index_url or f"{BASE}/apexlegends/{args.tier}-Tier_Tournaments/{args.year}"
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "tournaments").mkdir(parents=True, exist_ok=True)

    only = {s.strip() for s in args.only.split(",")} if args.only else None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        ctx = browser.new_context(viewport={"width": 1400, "height": 1080}, user_agent=UA)
        page = ctx.new_page()
        print(f"[index] {index_url}", file=sys.stderr)
        idx_html = load(page, index_url)
        ctx.close()

        tournaments = parse_index(idx_html)
        print(f"[index] found {len(tournaments)} tournaments", file=sys.stderr)
        (args.out / "index.json").write_text(
            json.dumps(tournaments, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        targets = tournaments
        if only:
            targets = [t for t in tournaments if t["slug"] in only]
        if args.limit > 0:
            targets = targets[: args.limit]

        for i, t in enumerate(targets, start=1):
            cache = args.out / "tournaments" / f"{t['slug']}.json"
            if cache.exists() and not args.force:
                print(f"[{i}/{len(targets)}] SKIP {t['slug']} (cached)", file=sys.stderr)
                continue
            print(f"[{i}/{len(targets)}] {t['slug']}  {t['url']}", file=sys.stderr)
            try:
                data = scrape_tournament(browser, t)
                cache.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    f"    teams={len(data['teams'])} games={len(data['games'])}",
                    file=sys.stderr,
                )
            except Exception as e:  # noqa: BLE001
                print(f"    ERROR: {e}", file=sys.stderr)

        browser.close()

    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()