"""ALGS API HTTP client with rate limiting, retry/backoff and on-disk cache.

Stdlib only (urllib). Designed for batch importer use - synchronous,
single-process, polite. Avoids hammering the public API which can lead
to a temporary block.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


# ----------------------------- configuration ---------------------------------

API_BASE = os.environ.get("ALGS_API_BASE", "https://prod-api.algstools.com").rstrip("/")
API_RPS = float(os.environ.get("ALGS_API_RPS", "2.0"))
API_BURST = int(os.environ.get("ALGS_API_BURST", "5"))
CACHE_TTL = int(os.environ.get("ALGS_API_CACHE_TTL", "86400"))
CACHE_DIR = Path(os.environ.get(
    "ALGS_API_CACHE_DIR",
    str(Path(__file__).resolve().parent / "data" / "cache"),
))
USER_AGENT = os.environ.get(
    "ALGS_API_UA",
    "apex-tracer-insight/0.1 (+https://github.com/lovable; contact: dev)",
)


# ------------------------------ token bucket ---------------------------------

class _TokenBucket:
    def __init__(self, rps: float, burst: int) -> None:
        self.rps = max(0.1, float(rps))
        self.capacity = max(1, int(burst))
        self._tokens = float(self.capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def take(self, n: float = 1.0) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity,
                    self._tokens + (now - self._last) * self.rps,
                )
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                wait = (n - self._tokens) / self.rps
            time.sleep(wait + random.uniform(0.05, 0.25))


_BUCKET = _TokenBucket(API_RPS, API_BURST)


# --------------------------------- cache -------------------------------------

def _cache_path(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / digest[:2] / f"{digest}.json"


def _cache_load(url: str, max_age: int) -> Any | None:
    p = _cache_path(url)
    if not p.exists():
        return None
    if max_age >= 0:
        age = time.time() - p.stat().st_mtime
        if age > max_age:
            return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _cache_store(url: str, data: Any) -> None:
    p = _cache_path(url)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


# ----------------------------- low-level fetch -------------------------------

class AlgsApiError(RuntimeError):
    pass


class AlgsNotFound(AlgsApiError):
    """Raised for 404/400 'not applicable' responses. Callers usually skip."""
    pass


def _request(url: str, *, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        try:
            return json.loads(raw) if raw else None
        except Exception as e:  # noqa: BLE001
            raise AlgsApiError(f"non-JSON response from {url}") from e


def get(path: str, *, params: dict[str, Any] | None = None,
        cache_ttl: int | None = None, force: bool = False) -> Any:
    """GET a JSON endpoint with throttle + cache + retry/backoff.

    `cache_ttl` overrides the global `ALGS_API_CACHE_TTL` when set
    (use a small number for live data, 0 to bypass on read but still write).
    `force=True` bypasses the cache for reads.
    """
    if not path.startswith("/"):
        path = "/" + path
    if params:
        flat = {k: v for k, v in params.items() if v is not None}
        if flat:
            path = f"{path}?{urllib.parse.urlencode(flat, doseq=True)}"
    url = API_BASE + path
    ttl = CACHE_TTL if cache_ttl is None else cache_ttl

    if not force and ttl > 0:
        cached = _cache_load(url, ttl)
        if cached is not None:
            if isinstance(cached, dict) and cached.get("__error__"):
                raise AlgsNotFound(f"{cached.get('status')} (cached): {url}")
            return cached

    delay = 1.0
    last_err: Exception | None = None
    for attempt in range(6):
        _BUCKET.take(1.0)
        time.sleep(random.uniform(0.05, 0.25))
        try:
            data = _request(url)
            _cache_store(url, data)
            return data
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504):
                ra = e.headers.get("Retry-After") if e.headers else None
                wait = float(ra) if ra and ra.isdigit() else delay
                wait += random.uniform(0.2, 0.8)
                print(f"[algs] {e.code} on {url}; sleep {wait:.1f}s "
                      f"(attempt {attempt + 1}/6)", file=sys.stderr)
                time.sleep(wait)
                delay = min(delay * 2.0, 30.0)
                continue
            if e.code in (400, 404):
                _cache_store(url, {"__error__": "not_available",
                                    "status": e.code})
                raise AlgsNotFound(f"{e.code} not available: {url}") from e
            raise AlgsApiError(f"HTTP {e.code} for {url}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            wait = delay + random.uniform(0.2, 0.8)
            print(f"[algs] network error on {url}: {e}; sleep {wait:.1f}s "
                  f"(attempt {attempt + 1}/6)", file=sys.stderr)
            time.sleep(wait)
            delay = min(delay * 2.0, 30.0)
    raise AlgsApiError(f"giving up on {url}: {last_err}")


# ------------------------------ thin wrappers --------------------------------
# Only the read endpoints we use from the importer. Auth-protected endpoints
# (login, voting, observer) are intentionally not exposed.

# ---- reference ----
def maps() -> list[dict[str, Any]]:
    return get("/v1/maps")["maps"]

def characters() -> list[dict[str, Any]]:
    return get("/v1/characters")["characters"]

# ---- seasons ----
def seasons() -> list[dict[str, Any]]:
    data = get("/v1/seasons")
    return data if isinstance(data, list) else data.get("seasons", [])

def season_structure(season_id: str) -> dict[str, Any]:
    return get(f"/v1/seasons/{season_id}/structure")

def season_standings_teams(season_id: str, **kw) -> list[dict[str, Any]]:
    return get(f"/v1/seasons/{season_id}/standings/teams", **kw)["standings"]

def season_standings_players(season_id: str, **kw) -> list[dict[str, Any]]:
    return get(f"/v1/seasons/{season_id}/standings/players", **kw)["standings"]

# ---- events ----
def event(event_id: str) -> dict[str, Any]:
    return get(f"/v1/events/{event_id}")

def event_structure(event_id: str) -> dict[str, Any]:
    return get(f"/v1/events/{event_id}/structure")

def event_teams(event_id: str) -> list[dict[str, Any]]:
    return get(f"/v1/events/{event_id}/teams")["teams"]

def event_maps(event_id: str) -> list[dict[str, Any]]:
    return get(f"/v1/events/{event_id}/maps")["maps"]

def event_standings(event_id: str, **kw) -> list[dict[str, Any]]:
    return get(f"/v1/events/{event_id}/standings", **kw)["standings"]

def event_schedule(event_id: str, **kw) -> list[dict[str, Any]]:
    return get(f"/v1/events/{event_id}/schedule", **kw)["phases"]

def stats_event_first_squad_wipe(event_id: str) -> Any:
    return get(f"/v1/stats/events/{event_id}/first-squad-wipe")

# ---- phases ----
def phase_teams(phase_id: str) -> list[dict[str, Any]]:
    return get(f"/v1/phases/{phase_id}/teams")["teams"]

def stats_phase_standings(phase_id: str, **kw) -> list[dict[str, Any]]:
    return get(f"/v1/stats/phases/{phase_id}/standings", **kw)["standings"]

def stats_phase_first_squad_wipe(phase_id: str) -> Any:
    return get(f"/v1/stats/phases/{phase_id}/first-squad-wipe")

# ---- series ----
def series(series_id: str, **kw) -> dict[str, Any]:
    return get(f"/v1/series/{series_id}", **kw)

def series_matches(series_id: str, **kw) -> list[dict[str, Any]]:
    return get(f"/v1/series/{series_id}/matches", **kw)["matches"]

def series_teams(series_id: str, **kw) -> list[dict[str, Any]]:
    return get(f"/v1/series/{series_id}/teams", **kw)["teams"]

def series_banned_legends(series_id: str, **kw) -> list[dict[str, Any]]:
    return get(f"/v1/series/{series_id}/banned-legends", **kw)["legendBans"]

def series_upcoming(**kw) -> list[dict[str, Any]]:
    # short cache - this list changes constantly
    kw.setdefault("cache_ttl", 600)
    return get("/v1/series/upcoming", **kw)["series"]

# ---- stats ----
def stats_series(series_id: str, **kw) -> dict[str, Any]:
    return get(f"/v1/stats/series/{series_id}", **kw)

def stats_series_match(series_id: str, match_number: int, **kw) -> dict[str, Any]:
    return get(f"/v1/stats/series/{series_id}/match/number/{match_number}", **kw)

def stats_pois(**filters) -> dict[str, Any]:
    p = {k: filters.get(k) for k in
         ("sort", "sortOrder", "map", "seasonId", "eventId",
          "phaseId", "seriesId")}
    return get("/v1/stats/pois", params=p)

def stats_weapons(**filters) -> dict[str, Any]:
    p = {k: filters.get(k) for k in
         ("page", "mapId", "eventId", "phaseId", "seriesId", "matchNumber")}
    return get("/v1/stats/weapons", params=p)

def stats_characters(**filters) -> dict[str, Any]:
    p = {k: filters.get(k) for k in
         ("mapId", "eventId", "phaseId", "seriesId", "matchNumber")}
    return get("/v1/stats/characters", params=p)

def stats_characters_composition(**filters) -> dict[str, Any]:
    p = {k: filters.get(k) for k in
         ("mapId", "eventId", "phaseId", "seriesId", "matchNumber")}
    return get("/v1/stats/characters/composition", params=p)

def stats_players(**filters) -> dict[str, Any]:
    p = {k: filters.get(k) for k in
         ("sort", "sortOrder", "page", "mapId", "playerIds",
          "teamId", "phaseId", "eventId", "seriesId", "type")}
    return get("/v1/stats/players", params=p)

# ---- poi drafts ----
def poi_draft(draft_id: str) -> dict[str, Any]:
    return get(f"/v1/poi-drafts/{draft_id}")["poiDraft"]

def poi_draft_locations(draft_id: str) -> list[dict[str, Any]]:
    return get(f"/v1/poi-drafts/{draft_id}/locations")["spawnLocations"]

def poi_draft_picks(draft_id: str) -> list[dict[str, Any]]:
    return get(f"/v1/poi-drafts/{draft_id}/pick")["picks"]

# ---- teams ----
def team(team_id: str) -> dict[str, Any]:
    return get(f"/v1/teams/{team_id}")["team"]

# ---- streams ----
def streams_live(**kw) -> list[dict[str, Any]]:
    # very short cache - this is the live state of the tournament
    kw.setdefault("cache_ttl", 120)
    return get("/v1/streams/live", **kw)["series"]

# ---- leaderboards (CC = Challenger Circuit) ----
def cc_leaderboard_teams(season_id: str, event_id: str, **kw) -> dict[str, Any]:
    p = {k: kw.pop(k, None) for k in ("region", "limit", "offset", "search")}
    return get(f"/v1/leaderboards/teams/cc-leaderboard/{season_id}/{event_id}",
               params=p, **kw)

def cc_leaderboard_players(season_id: str, event_id: str, **kw) -> dict[str, Any]:
    p = {k: kw.pop(k, None) for k in ("region", "limit", "offset", "search")}
    return get(f"/v1/leaderboards/players/cc-leaderboard/{season_id}/{event_id}",
               params=p, **kw)
