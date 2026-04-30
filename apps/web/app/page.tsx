"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api, API_URL } from "../lib/api";
import { MapAdminConfig, MapEntry, Match, RingPoint, Team, TeamTrack, Tournament } from "../lib/types";
import { MapPlayer } from "../components/map-player";

function formatMmSs(secondsRaw: number): string {
  const total = Math.max(0, Math.floor(secondsRaw));
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

function formatMapLabel(map: MapEntry): string {
  const gameMatch = map.id.match(/_r(\d+)$/i);
  const gameNumber = gameMatch ? Number(gameMatch[1]) : null;
  if (!gameNumber || !Number.isFinite(gameNumber)) {
    return map.mapName;
  }
  return `${map.mapName} (game ${gameNumber})`;
}

const FRAME_STEP_SEC = 1 / 30;

function buildInterpolatedTrackPoints(
  track: TeamTrack,
  startSec: number,
  endSec: number,
  currentSec: number
) {
  const points = [...track.points].sort((a, b) => a.timestampSec - b.timestampSec);
  if (points.length === 0 || currentSec < startSec) {
    return [];
  }

  const targetTime = Math.min(currentSec, endSec);
  const result: Array<{ timestampSec: number; x: number; y: number; confidence: number }> = [];

  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i];
    const b = points[i + 1];

    if (b.timestampSec < startSec) continue;
    if (a.timestampSec > targetTime) break;

    const segmentStart = Math.max(a.timestampSec, startSec);
    const segmentEnd = Math.min(b.timestampSec, targetTime);
    if (segmentEnd < segmentStart) continue;

    const segmentDuration = Math.max(1e-6, b.timestampSec - a.timestampSec);
    const steps = Math.max(1, Math.floor((segmentEnd - segmentStart) / FRAME_STEP_SEC));

    for (let step = 0; step <= steps; step++) {
      const t = segmentStart + ((segmentEnd - segmentStart) * step) / steps;
      const ratio = (t - a.timestampSec) / segmentDuration;
      const x = a.x + (b.x - a.x) * ratio;
      const y = a.y + (b.y - a.y) * ratio;
      const confidence = a.confidence + (b.confidence - a.confidence) * ratio;

      if (result.length > 0) {
        const last = result[result.length - 1];
        if (Math.abs(last.timestampSec - t) < 1e-6) continue;
      }

      result.push({ timestampSec: t, x, y, confidence });
    }
  }

  const lastKnown = points[points.length - 1];
  if (lastKnown.timestampSec <= targetTime && lastKnown.timestampSec >= startSec) {
    const alreadyHasLast =
      result.length > 0 && Math.abs(result[result.length - 1].timestampSec - lastKnown.timestampSec) < 1e-6;
    if (!alreadyHasLast) {
      result.push(lastKnown);
    }
  }

  return result;
}

export default function HomePage() {
  const ROUND_2_ENABLED = false;
  const [tournaments, setTournaments] = useState<Tournament[]>([]);
  const [matches, setMatches] = useState<Match[]>([]);
  const [maps, setMaps] = useState<MapEntry[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [roundTracks, setRoundTracks] = useState<TeamTrack[]>([]);
  const [roundRings, setRoundRings] = useState<RingPoint[]>([]);
  const [selectedTournamentId, setSelectedTournamentId] = useState("");
  const [selectedMatchId, setSelectedMatchId] = useState("");
  const [selectedMapId, setSelectedMapId] = useState("");
  const [selectedTeamIds, setSelectedTeamIds] = useState<string[]>([]);
  const [selectedRound, setSelectedRound] = useState<"round1" | "round2" | "all">("round1");
  const [timeCursor, setTimeCursor] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const [showControls, setShowControls] = useState(false);
  const [showSpeedMenu, setShowSpeedMenu] = useState(false);
  const [enableStopGrouping, setEnableStopGrouping] = useState(true);
  const [stopRadiusPx, setStopRadiusPx] = useState(40);
  const [stopMinDurationSec, setStopMinDurationSec] = useState(40);
  const [smoothWindow, setSmoothWindow] = useState(25);
  const [mapConfig, setMapConfig] = useState<MapAdminConfig | null>(null);
  const playerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    api.getTournaments().then((items) => {
      setTournaments(items);
      if (items.length > 0) setSelectedTournamentId(items[0].id);
    });
  }, []);

  useEffect(() => {
    if (!selectedMapId) {
      setTeams([]);
      setSelectedTeamIds([]);
      return;
    }
    api.getTeamsForMap(selectedMapId).then((items) => {
      setTeams(items);
      setSelectedTeamIds(items.map((team) => team.id));
    });
  }, [selectedMapId]);

  useEffect(() => {
    if (!selectedTournamentId) return;
    api.getMatches(selectedTournamentId).then((items) => {
      setMatches(items);
      setSelectedMatchId(items[0]?.id ?? "");
    });
  }, [selectedTournamentId]);

  useEffect(() => {
    if (!selectedMatchId) return;
    api.getMaps(selectedMatchId).then((items) => {
      setMaps(items);
      setSelectedMapId(items[0]?.id ?? "");
      if (items[0]) {
        setSelectedRound("round1");
        setTimeCursor(0);
        setIsPlaying(false);
      }
    });
  }, [selectedMatchId]);

  useEffect(() => {
    if (!selectedMapId) {
      setMapConfig(null);
      return;
    }
    api.getMapAdminConfig(selectedMapId).then(setMapConfig).catch(() => setMapConfig(null));
  }, [selectedMapId]);

  const roundWindows = useMemo(() => {
    const selectedMap = maps.find((entry) => entry.id === selectedMapId);
    const fallbackStart = selectedMap?.workFragmentStartSec ?? 0;
    const fallbackEnd = selectedMap?.workFragmentEndSec ?? 600;
    const runtimeWindows = mapConfig?.runtime?.roundWindows;
    if (runtimeWindows) {
      return runtimeWindows;
    }
    return {
      round1: { startSec: fallbackStart, endSec: selectedMap?.ring2StartSec ?? 375 },
      round2: { startSec: selectedMap?.ring2StartSec ?? 375, endSec: fallbackEnd },
    };
  }, [mapConfig, maps, selectedMapId]);

  const roundRange = useMemo(() => {
    const selectedMap = maps.find((entry) => entry.id === selectedMapId);
    if (selectedRound === "all") {
      return {
        startSec: selectedMap?.workFragmentStartSec ?? roundWindows.round1.startSec,
        endSec: selectedMap?.workFragmentEndSec ?? roundWindows.round2.endSec,
      };
    }
    if (ROUND_2_ENABLED && selectedRound === "round2") {
      return { startSec: roundWindows.round2.startSec, endSec: roundWindows.round2.endSec };
    }
    return { startSec: roundWindows.round1.startSec, endSec: roundWindows.round1.endSec };
  }, [ROUND_2_ENABLED, roundWindows, selectedRound, maps, selectedMapId]);

  useEffect(() => {
    setTimeCursor((prev) => {
      if (prev < roundRange.startSec || prev > roundRange.endSec) {
        return roundRange.startSec;
      }
      return prev;
    });
  }, [roundRange]);

  useEffect(() => {
    if (!selectedMapId || selectedTeamIds.length === 0) {
      setRoundTracks([]);
      return;
    }
    api.getTracks(selectedMapId, selectedTeamIds, roundRange.startSec, roundRange.endSec).then(setRoundTracks);
  }, [selectedMapId, selectedTeamIds, roundRange]);

  useEffect(() => {
    if (!selectedMapId) {
      setRoundRings([]);
      return;
    }
    api.getRings(selectedMapId, roundRange.startSec, roundRange.endSec).then(setRoundRings).catch(() => setRoundRings([]));
  }, [selectedMapId, roundRange]);

  const visibleTracks = useMemo(
    () =>
      roundTracks.map((track) => ({
        ...track,
        points: buildInterpolatedTrackPoints(track, roundRange.startSec, roundRange.endSec, timeCursor),
      })),
    [roundTracks, roundRange, timeCursor]
  );

  useEffect(() => {
    if (!isPlaying) return;
    let rafId = 0;
    let lastTs = performance.now();

    const tick = (now: number) => {
      const deltaSec = (now - lastTs) / 1000;
      lastTs = now;

      setTimeCursor((prev) => {
        const next = prev + deltaSec * playbackSpeed;
        if (next >= roundRange.endSec) {
          setIsPlaying(false);
          return roundRange.endSec;
        }
        return next;
      });

      rafId = requestAnimationFrame(tick);
    };

    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [isPlaying, playbackSpeed, roundRange.endSec]);

  const selectedMap = useMemo(
    () => maps.find((entry) => entry.id === selectedMapId),
    [maps, selectedMapId]
  );

  const toggleTeam = (teamId: string) => {
    setSelectedTeamIds((prev) =>
      prev.includes(teamId) ? prev.filter((id) => id !== teamId) : [...prev, teamId]
    );
  };

  const calibrationPayload = useMemo(
    () => ({
      player_smoothing: {
        enable_stop_grouping: enableStopGrouping,
        stop_radius_px: stopRadiusPx,
        stop_min_duration_sec: stopMinDurationSec,
        smooth_window: smoothWindow
      }
    }),
    [enableStopGrouping, stopMinDurationSec, stopRadiusPx, smoothWindow]
  );

  return (
    <main className="container">
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "10px" }}>
        <Link href="/admin" style={{ color: "#9cc0ff", fontSize: "13px" }}>
          Open admin panel
        </Link>
      </div>
      <div className="grid">
        <section className="panel">
          <label htmlFor="tournament">Турнир</label>
          <select
            id="tournament"
            value={selectedTournamentId}
            onChange={(event) => setSelectedTournamentId(event.target.value)}
          >
            {tournaments.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} ({item.season})
              </option>
            ))}
          </select>

          <label htmlFor="match">Матч</label>
          <select
            id="match"
            value={selectedMatchId}
            onChange={(event) => setSelectedMatchId(event.target.value)}
          >
            {matches.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>

          <label htmlFor="map">Карта</label>
          <select
            id="map"
            value={selectedMapId}
            onChange={(event) => setSelectedMapId(event.target.value)}
          >
            {maps.map((item) => (
              <option key={item.id} value={item.id}>
                {formatMapLabel(item)}
              </option>
            ))}
          </select>

          <label>Фильтр команд</label>
          <div className="teamFilterList">
            {teams.map((team) => (
              <label key={team.id} className="teamFilterItem">
                <input
                  type="checkbox"
                  checked={selectedTeamIds.includes(team.id)}
                  onChange={() => toggleTeam(team.id)}
                />
                <span>{team.name}</span>
              </label>
            ))}
          </div>

          <label>Круг</label>
          <div style={{ display: "flex", gap: "8px", marginBottom: "14px" }}>
            <button
              type="button"
              onClick={() => {
                setSelectedRound("round1");
                setTimeCursor(roundWindows.round1.startSec);
                setIsPlaying(false);
              }}
              style={{
                padding: "8px 12px",
                borderRadius: "8px",
                border: selectedRound === "round1" ? "1px solid #79a8ff" : "1px solid #2a3447",
                background: selectedRound === "round1" ? "#1d2a44" : "#141a26",
                color: "#f5f5f5",
                cursor: "pointer"
              }}
            >
              Круг 1
            </button>
            <button
              type="button"
              onClick={() => {
                const allStart = selectedMap?.workFragmentStartSec ?? roundWindows.round1.startSec;
                setSelectedRound("all");
                setTimeCursor(allStart);
                setIsPlaying(false);
              }}
              style={{
                padding: "8px 12px",
                borderRadius: "8px",
                border: selectedRound === "all" ? "1px solid #79a8ff" : "1px solid #2a3447",
                background: selectedRound === "all" ? "#1d2a44" : "#141a26",
                color: "#f5f5f5",
                cursor: "pointer"
              }}
            >
              All
            </button>
            <button
              type="button"
              onClick={() => {
                if (!ROUND_2_ENABLED) return;
                setSelectedRound("round2");
                setTimeCursor(roundWindows.round2.startSec);
                setIsPlaying(false);
              }}
              disabled={!ROUND_2_ENABLED}
              style={{
                padding: "8px 12px",
                borderRadius: "8px",
                border: selectedRound === "round2" ? "1px solid #79a8ff" : "1px solid #2a3447",
                background: selectedRound === "round2" ? "#1d2a44" : "#141a26",
                color: "#f5f5f5",
                cursor: ROUND_2_ENABLED ? "pointer" : "not-allowed",
                opacity: ROUND_2_ENABLED ? 1 : 0.55,
              }}
            >
              Круг 2
            </button>
          </div>

          <label style={{ marginTop: "12px" }}>Доп. функция: сглаживание узелков</label>
          <div className="calibrationBox">
            <label className="calibrationRow">
              <input
                type="checkbox"
                checked={enableStopGrouping}
                onChange={(event) => setEnableStopGrouping(event.target.checked)}
              />
              <span>Группировать близкие точки в stop-маркер</span>
            </label>

            <label htmlFor="stop-radius">Радиус stop (px): {stopRadiusPx}</label>
            <input
              id="stop-radius"
              type="range"
              min={4}
              max={120}
              step={1}
              value={stopRadiusPx}
              onChange={(event) => setStopRadiusPx(Number(event.target.value))}
            />

            <label htmlFor="stop-duration">Мин. длительность stop (sec): {stopMinDurationSec}</label>
            <input
              id="stop-duration"
              type="range"
              min={5}
              max={180}
              step={1}
              value={stopMinDurationSec}
              onChange={(event) => setStopMinDurationSec(Number(event.target.value))}
            />

            <label htmlFor="smooth-window">Окно сглаживания: {smoothWindow}</label>
            <input
              id="smooth-window"
              type="range"
              min={1}
              max={25}
              step={1}
              value={smoothWindow}
              onChange={(event) => setSmoothWindow(Number(event.target.value))}
            />

            <label>Передать мне эти значения:</label>
            <textarea
              readOnly
              className="calibrationOutput"
              value={JSON.stringify(calibrationPayload, null, 2)}
            />
          </div>

        </section>

        <section className="panel">
          <div
            className="mapPlayerShell"
            ref={playerRef}
            onMouseEnter={() => setShowControls(true)}
            onMouseLeave={() => {
              setShowControls(false);
              setShowSpeedMenu(false);
            }}
          >
            <div className="map">
            <MapPlayer
              tracks={visibleTracks}
              rings={roundRings}
              currentTimeSec={timeCursor}
              teams={teams}
              backgroundSrc={selectedMap?.backgroundUrl ? `${API_URL}${selectedMap.backgroundUrl}` : undefined}
              renderSettings={{
                enableStopGrouping,
                stopRadiusPx,
                stopMinDurationSec,
                smoothWindow
              }}
            />
          </div>
            <div className={`mapControls ${showControls || isPlaying ? "visible" : ""}`}>
              <button
                type="button"
                className="controlBtn controlBtnPrimary"
                onClick={() => setIsPlaying((prev) => !prev)}
                aria-label={isPlaying ? "Пауза" : "Пуск"}
              >
                {isPlaying ? "❚❚" : "▶"}
              </button>

              <div className="timelineBlock">
                <input
                  id="timeline-map"
                  type="range"
                  min={roundRange.startSec}
                  max={roundRange.endSec}
                  value={timeCursor}
                  onChange={(event) => setTimeCursor(Number(event.target.value))}
                />
                <div className="timelineMeta">
                  <span className="mapTimeBadge">{formatMmSs(timeCursor)}</span>
                  <span className="timelineTotal">/ {formatMmSs(roundRange.endSec)}</span>
                </div>
              </div>

              <div className="speedMenuWrap">
                <button
                  type="button"
                  className="controlBtn"
                  aria-label="Настройка скорости"
                  onClick={() => setShowSpeedMenu((prev) => !prev)}
                >
                  ⚙
                </button>
                {showSpeedMenu && (
                  <div className="speedMenu">
                    {[0.5, 1, 1.5, 2, 3].map((speed) => (
                      <button
                        key={speed}
                        type="button"
                        className={`speedOption ${playbackSpeed === speed ? "active" : ""}`}
                        onClick={() => {
                          setPlaybackSpeed(speed);
                          setShowSpeedMenu(false);
                        }}
                      >
                        {speed}x
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <button
                type="button"
                className="controlBtn"
                onClick={() => {
                  if (!playerRef.current) return;
                  if (document.fullscreenElement) {
                    void document.exitFullscreen();
                  } else {
                    void playerRef.current.requestFullscreen();
                  }
                }}
                aria-label="Полноэкранный режим"
              >
                ⛶
              </button>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
