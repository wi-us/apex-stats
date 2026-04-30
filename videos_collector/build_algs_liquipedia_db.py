import json
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Tag

LIQUIPEDIA_URL = "https://liquipedia.net/apexlegends/Apex_Legends_Global_Series"
OUTPUT_DB = Path(__file__).with_name("algs_tournaments.sqlite")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ApexStatsParser/1.0"

SPLIT_PATTERNS: List[Tuple[str, str]] = [
    ("split 1", "Split 1"),
    ("split 2", "Split 2"),
    ("open", "Open"),
    ("championship", "Championship"),
    ("autumn", "Autumn"),
    ("winter", "Winter"),
    ("summer", "Summer"),
]

REGION_PATTERNS: List[Tuple[str, str]] = [
    ("apac south", "APAC_South"),
    ("apac north", "APAC_North"),
    ("americas", "Americas"),
    ("emea", "EMEA"),
    ("north america", "North_America"),
    ("south america", "South_America"),
]


def clean_text(value: str) -> str:
    normalized = value.replace("\xa0", " ").replace("‑", "-").replace("–", "-")
    return re.sub(r"\s+", " ", normalized).strip()


def parse_year(tournament_name: str) -> Optional[int]:
    match = re.search(r"(20\d{2})", tournament_name)
    return int(match.group(1)) if match else None


def detect_split(tournament_name: str) -> Optional[str]:
    lowered = tournament_name.lower()
    for needle, split_name in SPLIT_PATTERNS:
        if needle in lowered:
            return split_name
    return None


def detect_region(tournament_name: str, location_text: str) -> Optional[str]:
    name_haystack = tournament_name.lower()
    for needle, region_name in REGION_PATTERNS:
        if needle in name_haystack:
            return region_name

    haystack = location_text.lower()
    for needle, region_name in REGION_PATTERNS:
        if needle in haystack:
            return region_name
    return None


def normalize_logo(src: Optional[str]) -> Optional[str]:
    if not src:
        return None
    if src.startswith("//"):
        return f"https:{src}"
    if src.startswith("/"):
        return f"https://liquipedia.net{src}"
    return src


def download_logo_bytes(session: requests.Session, logo_url: Optional[str]) -> Optional[bytes]:
    if not logo_url:
        return None

    try:
        response = session.get(logo_url, timeout=45)
        response.raise_for_status()
    except requests.RequestException:
        return None

    content_type = (response.headers.get("Content-Type") or "").lower()
    if not content_type.startswith("image/"):
        return None
    return response.content or None


def extract_team_from_cell(cell: Tag) -> Optional[Tuple[str, Optional[str]]]:
    anchors = cell.select("a[href]")
    for anchor in anchors:
        href = anchor.get("href", "")
        if not href.startswith("/apexlegends/"):
            continue
        if "/Category:" in href or "/File:" in href:
            continue

        team_name = clean_text(anchor.get("title") or anchor.get_text(" ", strip=True))
        if not team_name or team_name in {"TBD", "-", "None"}:
            continue

        logo_img = cell.select_one("img")
        logo_url = normalize_logo(logo_img.get("src") if logo_img else None)
        return team_name, logo_url
    return None


def extract_teams_from_block_teams(container: Tag) -> List[Tuple[str, Optional[str]]]:
    teams: List[Tuple[str, Optional[str]]] = []
    seen: set[str] = set()

    for card in container.select(".block-team"):
        anchors = card.select("a[href]")
        team_name: Optional[str] = None
        for anchor in anchors:
            href = anchor.get("href", "")
            if not href.startswith("/apexlegends/"):
                continue
            if "/Category:" in href or "/File:" in href:
                continue

            candidate = clean_text(anchor.get("title") or anchor.get_text(" ", strip=True))
            if not candidate or candidate in {"TBD", "-", "None"}:
                continue
            team_name = candidate
            break

        if not team_name or team_name in seen:
            continue

        logo_img = card.select_one("img")
        logo_url = normalize_logo(logo_img.get("src") if logo_img else None)
        teams.append((team_name, logo_url))
        seen.add(team_name)

    return teams


def extract_participants_for_tournament(session: requests.Session, tournament_url: Optional[str]) -> List[Tuple[str, Optional[str]]]:
    if not tournament_url:
        return []

    try:
        response = session.get(tournament_url, timeout=60)
        response.raise_for_status()
    except requests.RequestException:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    participants_h2 = soup.find("h2", id="Participants")

    if participants_h2:
        section_nodes: List[Tag] = []
        current = participants_h2.find_next_sibling()
        while current:
            if isinstance(current, Tag) and current.name == "h2":
                break
            if isinstance(current, Tag):
                section_nodes.append(current)
            current = current.find_next_sibling()

        section_wrapper = soup.new_tag("div")
        for node in section_nodes:
            section_wrapper.append(node)

        section_teams = extract_teams_from_block_teams(section_wrapper)
        if section_teams:
            return section_teams

    return extract_teams_from_block_teams(soup)


def find_events_table(page: BeautifulSoup) -> Tag:
    for table in page.select("table"):
        header_cells = [clean_text(th.get_text(" ", strip=True)) for th in table.select("tr th")]
        header_set = {h.lower() for h in header_cells}
        if {"tournament", "date", "location", "winner", "runner-up"}.issubset(header_set):
            return table
    raise RuntimeError("Could not find ALGS events table with Tournament/Winner/Runner-up columns.")


def fetch_events() -> Tuple[List[Dict], Dict[str, Optional[str]]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    response = session.get(LIQUIPEDIA_URL, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    table = find_events_table(soup)
    header_row = table.select_one("tr")
    if not header_row:
        raise RuntimeError("Events table does not have a header row.")

    headers = [clean_text(th.get_text(" ", strip=True)) for th in header_row.select("th")]
    col_idx = {name: idx for idx, name in enumerate(headers)}
    required = ["Tournament", "Date", "Location", "Winner", "Runner-up"]
    missing = [name for name in required if name not in col_idx]
    if missing:
        raise RuntimeError(f"Events table misses required columns: {missing}")

    tournaments: List[Dict] = []
    teams: Dict[str, Optional[str]] = {}

    rows = table.select("tr")[1:]
    for row in rows:
        cells = row.select("td")
        if not cells:
            continue

        shift = max(0, len(cells) - len(headers))
        max_index = max(col_idx[name] + shift for name in required)
        if len(cells) <= max_index:
            continue

        tournament_cell = cells[col_idx["Tournament"] + shift]
        date_cell = cells[col_idx["Date"] + shift]
        location_cell = cells[col_idx["Location"] + shift]
        winner_cell = cells[col_idx["Winner"] + shift]
        runner_up_cell = cells[col_idx["Runner-up"] + shift]

        tournament_anchor = tournament_cell.select_one("a[href]")
        if tournament_anchor:
            tournament_name = clean_text(
                tournament_anchor.get_text(" ", strip=True) or tournament_anchor.get("title") or tournament_cell.get_text(" ", strip=True)
            )
            href = tournament_anchor.get("href")
            tournament_url = f"https://liquipedia.net{href}" if href and href.startswith("/") else href
        else:
            tournament_name = clean_text(tournament_cell.get_text(" ", strip=True))
            tournament_url = None
        if not tournament_name:
            continue

        date_text = clean_text(date_cell.get_text(" ", strip=True))
        location_text = clean_text(location_cell.get_text(" ", strip=True))

        extracted_teams: List[str] = []
        for team_cell in (winner_cell, runner_up_cell):
            extracted = extract_team_from_cell(team_cell)
            if not extracted:
                continue
            team_name, logo_url = extracted
            if team_name not in extracted_teams:
                extracted_teams.append(team_name)
            if team_name not in teams or (teams[team_name] is None and logo_url is not None):
                teams[team_name] = logo_url

        participant_teams = extract_participants_for_tournament(session, tournament_url)
        for team_name, logo_url in participant_teams:
            if team_name not in extracted_teams:
                extracted_teams.append(team_name)
            if team_name not in teams or (teams[team_name] is None and logo_url is not None):
                teams[team_name] = logo_url

        tournaments.append(
            {
                "name": tournament_name,
                "year": parse_year(tournament_name),
                "date_text": date_text,
                "split": detect_split(tournament_name),
                "region": detect_region(tournament_name, location_text) or "WORLD",
                "teams_json": json.dumps(extracted_teams, ensure_ascii=False),
            }
        )

    return tournaments, teams


def build_db(db_path: Path, tournaments: List[Dict], teams: Dict[str, Optional[str]]) -> None:
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE tournaments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                year INTEGER,
                date_text TEXT NOT NULL,
                split TEXT,
                region TEXT,
                teams_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                logo BLOB
            )
            """
        )

        cur.executemany(
            """
            INSERT INTO tournaments (name, year, date_text, split, region, teams_json)
            VALUES (:name, :year, :date_text, :split, :region, :teams_json)
            """,
            tournaments,
        )

        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})

        team_rows = []
        for name, logo_url in sorted(teams.items(), key=lambda item: item[0].lower()):
            team_rows.append({"name": name, "logo": download_logo_bytes(session, logo_url)})

        cur.executemany(
            """
            INSERT INTO teams (name, logo)
            VALUES (:name, :logo)
            """,
            team_rows,
        )

        conn.commit()
    finally:
        conn.close()


def main() -> None:
    tournaments, teams = fetch_events()
    build_db(OUTPUT_DB, tournaments, teams)
    print(f"Database created: {OUTPUT_DB}")
    print(f"Tournaments: {len(tournaments)}")
    print(f"Teams: {len(teams)}")


if __name__ == "__main__":
    main()
