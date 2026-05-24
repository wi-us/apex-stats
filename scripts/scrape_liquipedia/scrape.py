#!/usr/bin/env python3
"""
Liquipedia Apex Legends tournament scraper.

Шаги:
  1) Грузит индекс турниров (по умолчанию A-Tier 2025).
  2) Для каждого турнира:
     - открывает страницу на широком viewport (1400x1080) и собирает
       логотип + полное имя команды из таблицы `.standings-ffa`;
     - открывает её же на узком viewport (700x1080) и собирает team-tag
       (Liquipedia адаптивно подменяет полное имя на сокращение);
     - открывает каждую вкладку игры из `ul.panel-tabs__list`
       (кроме `#Overall_standings`) и собирает участников.
  3) Сохраняет всё в JSON-кэш под `--out`.

После скрейпа используй `upload.py` для заливки в Lovable Cloud.

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
    # If wide & short variants are identical, the team simply has no abbreviation.
    if tag and tag == name:
        tag = None

    # Place from first cell
    tds = tr.find_all("td")
    place: int | None = None
    if tds:
        m = re.match(r"(\d+)", tds[0].get_text(" ", strip=True))
        if m:
            place = int(m.group(1))

    return {
        "place": place,
        "slug": slug,
        "name": name,
        "tag": tag,
        "logo_url": logo_url,
        "url": team_href,
    }


def extract_teams_and_games(html: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse the standings-ffa table once.

    Liquipedia stores per-game placements as separate <tr>s tagged with
    `data-toggle-area-content="<game_no>"`. Tabs in `ul.panel-tabs__list`
    label them (item 0 = "Overall standings", item N = "Game N").
    """
    soup = BeautifulSoup(html, "html.parser")
    cont = soup.select_one("div.standings-ffa")
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

    merged = {**t}
    if not merged.get("name") and page_name:
        merged["name"] = page_name
    merged["teams"] = teams
    merged["games"] = games
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