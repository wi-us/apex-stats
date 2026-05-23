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


def load(page: Page, url: str, wait: str = "networkidle") -> str:
    page.goto(url, wait_until=wait, timeout=60_000)
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
def extract_standings_rows(html: str) -> list[dict[str, Any]]:
    """Returns list of {place, team_text, logo_url} from the FIRST .standings-ffa table."""
    soup = BeautifulSoup(html, "html.parser")
    cont = soup.select_one("div.standings-ffa")
    if not cont:
        return []
    table = cont.select_one("table.table2__table")
    if not table:
        return []
    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody tr, tr"):
        # place: first cell often has rank
        tds = tr.find_all("td")
        if not tds:
            continue
        place_text = tds[0].get_text(" ", strip=True)
        place: int | None = None
        m = re.match(r"(\d+)", place_text)
        if m:
            place = int(m.group(1))
        # team cell: first <a> that points to /apexlegends/ team page
        team_a = None
        for a in tr.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/apexlegends/") and "Special:" not in href:
                team_a = a
                break
        if not team_a:
            continue
        text = team_a.get_text(" ", strip=True)
        # logo: <img> inside the row (team template icon)
        logo_url = None
        img = tr.find("img")
        if img:
            src = img.get("src") or img.get("data-src")
            if src:
                logo_url = urljoin(BASE, src)
        team_href = urljoin(BASE, team_a["href"])
        rows.append(
            {
                "place": place,
                "team_text": text,
                "team_href": team_href,
                "team_slug": slugify(team_a["href"].replace("/apexlegends/", "")),
                "logo_url": logo_url,
            }
        )
    return rows


def extract_game_tabs(html: str) -> list[dict[str, Any]]:
    """Returns [{tab_id, label}], skipping Overall_standings."""
    soup = BeautifulSoup(html, "html.parser")
    tabs: list[dict[str, Any]] = []
    for ul in soup.select("ul.panel-tabs__list"):
        for li in ul.select("li"):
            tab_id = li.get("id") or ""
            if tab_id == "Overall_standings" or not tab_id:
                continue
            label = li.get_text(" ", strip=True)
            tabs.append({"id": tab_id, "label": label})
    # dedupe
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for t in tabs:
        if t["id"] in seen:
            continue
        seen.add(t["id"])
        uniq.append(t)
    return uniq


def extract_game_participants(page: Page, tab_id: str) -> list[dict[str, Any]]:
    """
    After clicking a panel tab, the corresponding standings table becomes visible.
    We grab the .standings-ffa table that is currently visible / matches the tab.
    """
    # Click the tab
    try:
        page.locator(f"li#{tab_id}").first.click(timeout=5_000)
        time.sleep(0.4)
    except Exception:
        pass
    # After click, scrape the active panel
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    # Find the panel content for this tab: Liquipedia uses data-target or id linkage.
    # Fallback: take all .standings-ffa tables and pick the one whose ancestor panel is active.
    candidates = soup.select("div.standings-ffa")
    table = None
    for c in candidates:
        # active panels typically lose `is-hidden` / get `is-active`
        cls = " ".join(c.get("class", []))
        anc = c
        hidden = False
        for _ in range(6):
            if anc is None:
                break
            ancc = " ".join(anc.get("class", []) if hasattr(anc, "get") else [])
            if "is-hidden" in ancc or "panel-content--hidden" in ancc:
                hidden = True
                break
            anc = anc.parent
        if hidden:
            continue
        table = c.select_one("table.table2__table")
        if table is not None:
            break
    if table is None:
        return []
    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody tr, tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        m = re.match(r"(\d+)", tds[0].get_text(" ", strip=True))
        place = int(m.group(1)) if m else None
        team_a = None
        for a in tr.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/apexlegends/") and "Special:" not in href:
                team_a = a
                break
        if not team_a:
            continue
        rows.append(
            {
                "place": place,
                "team_slug": slugify(team_a["href"].replace("/apexlegends/", "")),
                "team_text": team_a.get_text(" ", strip=True),
            }
        )
    return rows


def scrape_tournament(browser: Browser, t: dict[str, Any]) -> dict[str, Any]:
    """Returns enriched tournament dict with teams + games."""
    # Pass A: wide viewport — logos + full names + game tabs
    ctx_wide = browser.new_context(viewport={"width": 1400, "height": 1080}, user_agent=UA)
    page = ctx_wide.new_page()
    html_wide = load(page, t["url"])
    teams_wide = extract_standings_rows(html_wide)
    tabs = extract_game_tabs(html_wide)

    games: list[dict[str, Any]] = []
    for i, tab in enumerate(tabs, start=1):
        participants = extract_game_participants(page, tab["id"])
        games.append(
            {
                "game_no": i,
                "tab_id": tab["id"],
                "label": tab["label"],
                "participants": participants,
            }
        )
    ctx_wide.close()

    # Pass B: narrow viewport — team tags
    ctx_narrow = browser.new_context(viewport={"width": 700, "height": 1080}, user_agent=UA)
    page2 = ctx_narrow.new_page()
    html_narrow = load(page2, t["url"])
    teams_narrow = extract_standings_rows(html_narrow)
    ctx_narrow.close()

    # Merge: tag = team_text from narrow viewport
    tag_by_slug = {r["team_slug"]: r["team_text"] for r in teams_narrow}
    teams: list[dict[str, Any]] = []
    for r in teams_wide:
        teams.append(
            {
                "place": r["place"],
                "slug": r["team_slug"],
                "name": r["team_text"],
                "tag": tag_by_slug.get(r["team_slug"]),
                "logo_url": r["logo_url"],
                "url": r["team_href"],
            }
        )
    return {**t, "teams": teams, "games": games}


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