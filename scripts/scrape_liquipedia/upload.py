#!/usr/bin/env python3
"""
Заливает JSON-кэш (созданный scrape.py) в Lovable Cloud (Supabase).

Требует переменные окружения:
  SUPABASE_DB_URL   — postgres connection string (берётся из Lovable Cloud secrets)

Usage:
  python upload.py --in data
  python upload.py --in data --only als-pro-league-year-5-split-1-playoffs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import execute_values


def get_conn():
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        print(
            "ERROR: SUPABASE_DB_URL env var not set.\n"
            "Возьми его из Lovable Cloud → Secrets → SUPABASE_DB_URL.",
            file=sys.stderr,
        )
        sys.exit(2)
    return psycopg2.connect(url)


def upsert_tournament(cur, t: dict[str, Any]) -> str:
    cur.execute(
        """
        INSERT INTO public.lp_tournaments (slug, url, name, dates_text, location)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (slug) DO UPDATE SET
          url = EXCLUDED.url,
          name = EXCLUDED.name,
          dates_text = EXCLUDED.dates_text,
          location = EXCLUDED.location,
          scraped_at = now()
        RETURNING id
        """,
        (t["slug"], t["url"], t["name"], t.get("dates_text"), t.get("location")),
    )
    return cur.fetchone()[0]


def upsert_teams(cur, teams: list[dict[str, Any]]) -> dict[str, str]:
    if not teams:
        return {}
    # one upsert per team to also handle tag/logo refresh
    slug_to_id: dict[str, str] = {}
    for tm in teams:
        cur.execute(
            """
            INSERT INTO public.lp_teams (slug, name, tag, logo_url)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (slug) DO UPDATE SET
              name = EXCLUDED.name,
              tag = COALESCE(EXCLUDED.tag, public.lp_teams.tag),
              logo_url = COALESCE(EXCLUDED.logo_url, public.lp_teams.logo_url),
              scraped_at = now()
            RETURNING id
            """,
            (tm["slug"], tm["name"], tm.get("tag"), tm.get("logo_url")),
        )
        slug_to_id[tm["slug"]] = cur.fetchone()[0]
    return slug_to_id


def replace_tournament_teams(cur, tournament_id: str, teams: list[dict[str, Any]], slug_to_id: dict[str, str]) -> None:
    cur.execute("DELETE FROM public.lp_tournament_teams WHERE tournament_id = %s", (tournament_id,))
    rows = [
        (tournament_id, slug_to_id[t["slug"]], t.get("place"))
        for t in teams if t["slug"] in slug_to_id
    ]
    if rows:
        execute_values(
            cur,
            "INSERT INTO public.lp_tournament_teams (tournament_id, team_id, place) VALUES %s",
            rows,
        )


def replace_games(cur, tournament_id: str, games: list[dict[str, Any]], slug_to_id: dict[str, str]) -> None:
    cur.execute("DELETE FROM public.lp_games WHERE tournament_id = %s", (tournament_id,))
    for g in games:
        cur.execute(
            """
            INSERT INTO public.lp_games (tournament_id, game_no, label)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (tournament_id, g["game_no"], g.get("label")),
        )
        game_id = cur.fetchone()[0]
        rows = [
            (game_id, slug_to_id[p["team_slug"]], p.get("place"))
            for p in g.get("participants", [])
            if p.get("team_slug") in slug_to_id
        ]
        if rows:
            execute_values(
                cur,
                "INSERT INTO public.lp_game_participants (game_id, team_id, place) VALUES %s",
                rows,
            )


def upload_one(cur, data: dict[str, Any]) -> None:
    tid = upsert_tournament(cur, data)
    slug_to_id = upsert_teams(cur, data.get("teams", []))
    replace_tournament_teams(cur, tid, data.get("teams", []), slug_to_id)
    replace_games(cur, tid, data.get("games", []), slug_to_id)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="indir", type=Path, default=Path(__file__).parent / "data")
    ap.add_argument("--only", default=None, help="comma-separated tournament slugs")
    args = ap.parse_args()

    files = sorted((args.indir / "tournaments").glob("*.json"))
    if args.only:
        keep = {s.strip() for s in args.only.split(",")}
        files = [f for f in files if f.stem in keep]
    if not files:
        print("No tournament JSON files to upload.", file=sys.stderr)
        return

    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            for i, f in enumerate(files, start=1):
                data = json.loads(f.read_text(encoding="utf-8"))
                print(
                    f"[{i}/{len(files)}] {data['slug']}  teams={len(data.get('teams', []))} games={len(data.get('games', []))}",
                    file=sys.stderr,
                )
                upload_one(cur, data)
                conn.commit()
    finally:
        conn.close()
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()