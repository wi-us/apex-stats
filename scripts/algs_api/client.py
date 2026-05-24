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
    """Simple thread-safe token bucket. ~rps tokens/sec, capped at burst."""

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


def _request(url: str, *, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ct = resp.headers.get("Content-Type", "")
        raw = resp.read()
        if "json" not in ct:
            try:
                return json.loads(raw)
            except Exception as e:  # noqa: BLE001
                raise AlgsApiError(f"non-JSON response from {url}: {ct}") from e
        return json.loads(raw)


def get(path: str, *, params: dict[str, Any] | None = None,
        cache_ttl: int | None = None, force: bool = False) -> Any:
    """GET a JSON endpoint with throttle + cache + retry/backoff.

    `cache_ttl` overrides the global `ALGS_API_CACHE_TTL` when set.
    `force=True` bypasses the cache (still writes the fresh result back).
    """
    if not path.startswith("/"):
        path = "/" + path
    if params:
        path = f"{path}?{urllib.parse.urlencode(params, doseq=True)}"
    url = API_BASE + path
    ttl = CACHE_TTL if cache_ttl is None else cache_ttl

    if not force:
        cached = _cache_load(url, ttl)
        if cached is not None:
            return cached

    delay = 1.0
    last_err: Exception | None = None
    for attempt in range(6):
        _BUCKET.take(1.0)
        time.sleep(random.uniform(0.05, 0.25))  # extra jitter
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
            if e.code == 404:
                # Cache 404 briefly so we don't re-hammer.
                _cache_store(url, {"__error__": "not_found", "status": 404})
                raise AlgsApiError(f"404 not found: {url}") from e
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

def maps() -> list[dict[str, Any]]:
    return get("/v1/maps")["maps"]


def characters() -> list[dict[str, Any]]:
    return get("/v1/characters")["characters"]


def seasons() -> list[dict[str, Any]]:
    data = get("/v1/seasons")
    return data if isinstance(data, list) else data.get("seasons", [])


def season_structure(season_id: str) -> dict[str, Any]:
    return get(f"/v1/seasons/{season_id}/structure")


def event(event_id: str) -> dict[str, Any]:
    return get(f"/v1/events/{event_id}")


def event_structure(event_id: str) -> dict[str, Any]:
    return get(f"/v1/events/{event_id}/structure")


def event_teams(event_id: str) -> dict[str, Any]:
    return get(f"/v1/events/{event_id}/teams")


def event_maps(event_id: str) -> list[dict[str, Any]]:
    return get(f"/v1/events/{event_id}/maps")["maps"]


def event_standings(event_id: str) -> dict[str, Any]:
    return get(f"/v1/events/{event_id}/standings")


def series(series_id: str) -> dict[str, Any]:
    return get(f"/v1/series/{series_id}")


def series_matches(series_id: str) -> list[dict[str, Any]]:
    return get(f"/v1/series/{series_id}/matches")["matches"]


def series_teams(series_id: str) -> list[dict[str, Any]]:
    return get(f"/v1/series/{series_id}/teams")["teams"]


def series_banned_legends(series_id: str) -> list[dict[str, Any]]:
    return get(f"/v1/series/{series_id}/banned-legends")["legendBans"]


def stats_series(series_id: str) -> dict[str, Any]:
    return get(f"/v1/stats/series/{series_id}")


def stats_series_match(series_id: str, match_number: int) -> dict[str, Any]:
    return get(f"/v1/stats/series/{series_id}/match/number/{match_number}")


def stats_pois(*, series_id: str | None = None,
               event_id: str | None = None) -> Any:
    params: dict[str, Any] = {}
    if series_id: params["seriesId"] = series_id
    if event_id:  params["eventId"]  = event_id
    return get("/v1/stats/pois", params=params or None)


def poi_draft(draft_id: str) -> dict[str, Any]:
    return get(f"/v1/poi-drafts/{draft_id}")["poiDraft"]


def poi_draft_locations(draft_id: str) -> list[dict[str, Any]]:
    return get(f"/v1/poi-drafts/{draft_id}/locations")["spawnLocations"]


def poi_draft_picks(draft_id: str) -> list[dict[str, Any]]:
    return get(f"/v1/poi-drafts/{draft_id}/pick")["picks"]


def team(team_id: str) -> dict[str, Any]:
    return get(f"/v1/teams/{team_id}")