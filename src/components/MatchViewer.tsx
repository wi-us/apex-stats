import { Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import {
  tournaments,
  maps,
  matches,
  matchSeedExtras,
  teams as defaultTeams,
  generateTrajectory,
  ringPhases as defaultRingPhases,
  events as defaultEvents,
  gameDataOverrides,
  getGames,
  parseGameId,
  type Team,
  type RingPhase,
  type GameEvent,
  type Game,
} from "@/lib/mock-match";
import { TeamLogo } from "@/components/admin/TeamLogo";
import { getSlotColor } from "@/lib/team-colors";
import { ThemeToggle } from "@/components/ThemeToggle";
import { DensityToggle } from "@/components/DensityToggle";
import { Users, Swords, Skull, ShieldAlert, Package, Circle, Flag } from "lucide-react";
import damageIcon from "@/assets/icons/damage.svg";

function formatTime(sec: number) {
  const m = Math.floor(sec / 60).toString().padStart(2, "0");
  const s = Math.floor(sec % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

type EventFilter = "all" | "fights" | "rings" | "eliminations" | "rotations" | "errors";

const EVENT_FILTERS: { id: EventFilter; label: string }[] = [
  { id: "all",          label: "Все" },
  { id: "fights",       label: "Бои" },
  { id: "rings",        label: "Кольца" },
  { id: "eliminations", label: "Выбывания" },
  { id: "rotations",    label: "Ротации" },
  { id: "errors",       label: "Спорные" },
];

function matchesFilter(e: GameEvent, f: EventFilter) {
  if (f === "all") return true;
  if (f === "fights")       return e.type === "kill" || e.type === "knock";
  if (f === "rings")        return e.type === "ring";
  if (f === "eliminations") return e.type === "wipe";
  if (f === "rotations")    return e.type === "care";
  if (f === "errors")       return false;
  return true;
}

/** Split each ring phase into CD (waiting) and Closing windows. */
const RING_CLOSE_FRACTION = 0.4;
type RingSegment = { phaseIndex: number; kind: "CD" | "Closing"; startSec: number; endSec: number };
function buildRingSegments(phases: RingPhase[]): RingSegment[] {
  return phases.flatMap((p, i) => {
    const dur = p.endSec - p.startSec;
    const closeStart = p.closingStartSec ?? (p.startSec + dur * (1 - RING_CLOSE_FRACTION));
    return [
      { phaseIndex: i, kind: "CD",      startSec: p.startSec, endSec: closeStart } as RingSegment,
      { phaseIndex: i, kind: "Closing", startSec: closeStart, endSec: p.endSec }    as RingSegment,
    ];
  });
}

export function MatchViewer({ initialGameId }: { initialGameId?: string }) {
  const initial = (() => {
    if (initialGameId) {
      const parsed = parseGameId(initialGameId);
      if (parsed) {
        const m = matches.find((x) => x.id === parsed.matchId);
        if (m) {
          const extras = matchSeedExtras[m.id];
          const games = getGames({ ...m, mapIds: extras?.mapIds, gameDurations: extras?.gameDurations });
          const idx = Math.max(0, Math.min(games.length - 1, parsed.index));
          return { match: m, gameIndex: idx };
        }
      }
    }
    return { match: matches[0], gameIndex: 0 };
  })();
  const [tournamentId, setTournamentId] = useState(initial.match.tournamentId);
  const [matchId, setMatchId] = useState(initial.match.id);
  const [gameIndex, setGameIndex] = useState(initial.gameIndex);
  const match = matches.find((m) => m.id === matchId) ?? matches[0];
  const matchEnriched = useMemo(() => {
    const extras = matchSeedExtras[match.id];
    return { ...match, mapIds: extras?.mapIds, gameDurations: extras?.gameDurations };
  }, [match]);
  const games = useMemo(() => getGames(matchEnriched), [matchEnriched]);
  const game = games[Math.min(gameIndex, games.length - 1)] ?? games[0];
  const apexMap = maps.find((m) => m.id === game.mapId)!;
  const _override = gameDataOverrides[game.id];
  const teams: Team[] = _override?.teams?.length ? _override.teams : defaultTeams;
  const ringPhases: RingPhase[] = _override?.ringPhases?.length ? _override.ringPhases : defaultRingPhases;
  const events: GameEvent[] = _override?.events?.length ? _override.events : defaultEvents;
  const durationSec = _override?.durationSec ?? game.durationSec;
  const ringSegments = useMemo(() => buildRingSegments(ringPhases), [ringPhases]);

  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const [selectedTeams, setSelectedTeams] = useState<Set<string>>(
    () => new Set(teams.map((t) => t.id)),
  );
  const [hoverTeam, setHoverTeam] = useState<string | null>(null);
  const [showTrails, setShowTrails] = useState(true);
  const [showRing, setShowRing] = useState(true);
  const [showLabels, setShowLabels] = useState(true);
  const [showConfig, setShowConfig] = useState(false);
  const [eventFilter, setEventFilter] = useState<EventFilter>("all");
  const [focusRequest, setFocusRequest] = useState<{ x: number; y: number; token: number } | null>(null);
  const [cfg, setCfg] = useState({
    trailWidth: 2,
    labelSize: 22,
    labelBg: 0.78,
    dwellWindow: 40,    // seconds
    dwellRadius: 0.04,  // normalized
  });

  const overrideTrajectories = _override?.trajectories;
  const trajectories = useMemo(
    () => {
      if (overrideTrajectories) {
        // Для команд без реальных треков — пустой массив, отрисуется только если есть точки.
        return Object.fromEntries(
          teams.map((t) => [t.id, overrideTrajectories[t.id] ?? []]),
        );
      }
      return Object.fromEntries(
        teams.map((t, i) => [t.id, generateTrajectory(i + 7, durationSec)]),
      );
    },
    [overrideTrajectories, teams, durationSec],
  );

  /** Detect dwell clusters per team: contiguous windows of >= dwellWindow seconds
   *  where all points stay within dwellRadius of the window's mean position. */
  const dwellsByTeam = useMemo(() => {
    const out: Record<string, { x: number; y: number; tStart: number; tEnd: number }[]> = {};
    for (const team of teams) {
      const pts = trajectories[team.id];
      if (!pts) { out[team.id] = []; continue; }
      const dwells: { x: number; y: number; tStart: number; tEnd: number }[] = [];
      let i = 0;
      while (i < pts.length) {
        let sumX = pts[i].x, sumY = pts[i].y;
        let j = i + 1;
        while (j < pts.length) {
          const n = j - i + 1;
          const mx = (sumX + pts[j].x) / n;
          const my = (sumY + pts[j].y) / n;
          let ok = true;
          for (let k = i; k <= j; k++) {
            const dx = pts[k].x - mx, dy = pts[k].y - my;
            if (dx * dx + dy * dy > cfg.dwellRadius * cfg.dwellRadius) { ok = false; break; }
          }
          if (!ok) break;
          sumX += pts[j].x; sumY += pts[j].y;
          j++;
        }
        const last = j - 1;
        const dur = pts[last].t - pts[i].t;
        if (dur >= cfg.dwellWindow) {
          const n = last - i + 1;
          let ax = 0, ay = 0;
          for (let k = i; k <= last; k++) { ax += pts[k].x; ay += pts[k].y; }
          dwells.push({ x: ax / n, y: ay / n, tStart: pts[i].t, tEnd: pts[last].t });
          i = last + 1;
        } else {
          i++;
        }
      }
      out[team.id] = dwells;
    }
    return out;
  }, [trajectories, cfg.dwellWindow, cfg.dwellRadius]);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      setTime((t) => {
        const nt = t + dt * speed;
        if (nt >= durationSec) { setPlaying(false); return durationSec; }
        return nt;
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, durationSec]);

  /**
   * The currently-visible safe area (the cyan boundary; everything outside is red).
   * Semantics, phase by phase:
   *  - R{N} CD       — zone is parked at the PREVIOUS ring (or the whole map for R1 CD).
   *  - R{N} Closing  — zone shrinks from the previous ring (or whole map) down to ring N.
   * A synthetic "whole map" ring uses r ≈ 1.5 so the danger overlay is empty
   * and the cyan boundary falls outside the canvas.
   */
  const ring = useMemo<RingPhase>(() => {
    const seg = ringSegments.find(s => time >= s.startSec && time <= s.endSec)
      ?? ringSegments[ringSegments.length - 1];
    const target = ringPhases[seg.phaseIndex];
    // For R1 there is no "previous" ring. Use a synthetic starting circle that
    // just inscribes the map corners (≈ √0.5) so the danger zone is invisible
    // at CD but starts shrinking from the corners the moment R1 Closing begins.
    const prev: RingPhase = seg.phaseIndex === 0
      ? { startSec: 0, endSec: 0, cx: 0.5, cy: 0.5, r: 0.72 }
      : ringPhases[seg.phaseIndex - 1];
    if (seg.kind === "CD") return prev;
    const k = Math.max(0, Math.min(1, (time - seg.startSec) / (seg.endSec - seg.startSec)));
    return {
      ...target,
      cx: prev.cx + (target.cx - prev.cx) * k,
      cy: prev.cy + (target.cy - prev.cy) * k,
      r:  prev.r  + (target.r  - prev.r ) * k,
    };
  }, [time]);

  const toggleTeam = (id: string) => {
    setSelectedTeams((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  };

  const totalKills = teams.reduce((acc, t) => acc + t.kills, 0);

  const teamByTag = useMemo(() => new Map(teams.map(t => [t.tag, t])), []);

  /** When each team becomes "out". Sourced from `wipe` events (victim parsed from label),
   *  and synthesized from `placement` for teams that are flagged dead but lack an event. */
  const deathTimes = useMemo<Record<string, number>>(() => {
    const out: Record<string, number> = {};
    for (const e of events) {
      if (e.type !== "wipe") continue;
      // Prefer structured fields (teamId / team tag) — the wipe event IS the
      // elimination of `e.team` itself (HUD-sourced). Fall back to label
      // parsing for legacy mock events shaped like "<KILLER> wipes <VICTIM>".
      let victimId: string | undefined;
      if (e.teamId) {
        victimId = e.teamId;
      } else if (e.team) {
        victimId = teamByTag.get(e.team)?.id;
      }
      if (!victimId) {
        const m = /wipes\s+([A-Za-z0-9]+)/i.exec(e.label);
        const victimTag = m?.[1];
        if (victimTag) victimId = teamByTag.get(victimTag)?.id;
      }
      if (!victimId) continue;
      if (out[victimId] === undefined || e.t < out[victimId]) out[victimId] = e.t;
    }
    // Fallback: teams marked dead in static data but with no wipe event get a
    // synthetic death time derived from placement (lower placement → later death).
    for (const t of teams) {
      if (t.alive) continue;
      if (out[t.id] !== undefined) continue;
      const k = (teams.length - t.placement + 1) / (teams.length + 1);
      out[t.id] = Math.round(durationSec * k);
    }
    return out;
  }, [teamByTag, durationSec, events, teams]);

  /** Per-team alive state at the current `time`. */
  const liveAlive = useMemo<Record<string, boolean>>(() => {
    const map: Record<string, boolean> = {};
    for (const t of teams) {
      const d = deathTimes[t.id];
      map[t.id] = d === undefined ? true : time < d;
    }
    return map;
  }, [deathTimes, time]);

  const aliveTeams = useMemo(() => teams.filter((t) => liveAlive[t.id]).length, [liveAlive]);

  /** Resolve an event's spatial position so we can plot it / focus the map. */
  const eventPoint = useCallback((e: GameEvent): { x: number; y: number } | null => {
    if (e.type === "ring") {
      const phase = ringPhases.find(p => e.t >= p.startSec && e.t <= p.endSec) ?? ringPhases[0];
      return { x: phase.cx, y: phase.cy };
    }
    if (e.team) {
      const team = teamByTag.get(e.team);
      if (!team) return null;
      const traj = trajectories[team.id];
      if (!traj || traj.length === 0) return null;
      let p = traj[0];
      for (const q of traj) { if (q.t <= e.t) p = q; else break; }
      return { x: p.x, y: p.y };
    }
    return null;
  }, [teamByTag, trajectories]);

  const filteredEvents = useMemo(() => events.filter(e => matchesFilter(e, eventFilter)), [eventFilter]);

  const handleEventClick = useCallback((e: GameEvent) => {
    setTime(e.t);
    setPlaying(false);
    const p = eventPoint(e);
    if (p) setFocusRequest({ x: p.x, y: p.y, token: Date.now() });
  }, [eventPoint]);

  // Resizable side panels (Teams left, Match feed right)
  const [leftWidth, setLeftWidth] = useState(260);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightWidth, setRightWidth] = useState(300);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const startResize = (side: "left" | "right") => (e: React.PointerEvent) => {
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    const startX = e.clientX;
    const startW = side === "left" ? leftWidth : rightWidth;
    const onMove = (ev: PointerEvent) => {
      const dx = ev.clientX - startX;
      const next = side === "left" ? startW + dx : startW - dx;
      if (side === "left") {
        if (next < 150) {
          setLeftCollapsed(true);
        } else {
          setLeftCollapsed(false);
          setLeftWidth(Math.max(160, Math.min(560, next)));
        }
      } else {
        if (next < 150) {
          setRightCollapsed(true);
        } else {
          setRightCollapsed(false);
          setRightWidth(Math.max(160, Math.min(560, next)));
        }
      }
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };
  // Scale team-row logo size with panel width.
  const teamCompact = leftWidth < 210;
  // In compact mode the row is 48px tall with ~28px reserved for slot color + alive dot on the sides.
  const teamLogoSize = teamCompact
    ? Math.max(24, Math.min(44, leftWidth - 40))
    : Math.round(Math.max(18, Math.min(44, leftWidth / 11)));

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
      <TopBar
        tournamentId={tournamentId}
        onTournamentChange={(id) => {
          setTournamentId(id);
          const firstMatch = matches.find((m) => m.tournamentId === id);
          if (firstMatch) { setMatchId(firstMatch.id); setGameIndex(0); setTime(0); setPlaying(false); }
        }}
        matchId={matchId}
        onMatchChange={(id) => { setMatchId(id); setGameIndex(0); setTime(0); setPlaying(false); }}
        gameId={game.id}
        games={games}
        onGameChange={(gid) => {
          const idx = games.findIndex((g) => g.id === gid);
          if (idx >= 0) { setGameIndex(idx); setTime(0); setPlaying(false); }
        }}
        mapName={apexMap.name}
        aliveTeams={aliveTeams}
        totalKills={totalKills}
      />

      <div className="relative flex min-h-0 flex-1">
        {leftCollapsed ? (
          <button
            onClick={() => setLeftCollapsed(false)}
            title="Show teams"
            className="absolute left-2 top-2 z-20 hidden h-10 w-10 items-center justify-center rounded-sm border border-border-strong bg-surface-2/90 text-foreground shadow-md backdrop-blur hover:bg-muted lg:flex"
          >
            <Users className="h-5 w-5" />
          </button>
        ) : (
        <aside
          className="relative hidden shrink-0 flex-col border-r border-border bg-surface lg:flex"
          style={{ width: leftWidth }}
        >
          <PanelHeader
            title="Teams"
            subtitle={
              <span className="flex items-center gap-2">
                <span className="flex items-center gap-1">
                  <span className="h-1.5 w-1.5 rounded-full bg-success" /> alive
                </span>
                <span className="flex items-center gap-1">
                  <span className="h-1.5 w-1.5 rounded-full bg-destructive/70" /> out
                </span>
              </span>
            }
          />
          <div className="min-h-0 flex-1 overflow-y-auto p-2 scrollbar-slim">
            {[...teams]
              .sort((a, b) => {
                // Сортировка по слоту = по цветовой палитре (как в HUD VOD),
                // а не по placement. Слот извлекаем из id `t-test-{slot}`.
                const sa = Number(a.id.split("-").pop()) || 0;
                const sb = Number(b.id.split("-").pop()) || 0;
                return sa - sb;
              })
              .map((t) => (
              <TeamRow key={t.id} team={t} slotIndex={teams.indexOf(t)} active={selectedTeams.has(t.id)} hovered={hoverTeam === t.id}
                onToggle={() => toggleTeam(t.id)}
                onHover={(v) => setHoverTeam(v ? t.id : null)}
                logoSize={teamLogoSize} compact={teamCompact}
                alive={liveAlive[t.id]} />
            ))}
          </div>
          <div className="border-t border-border p-3">
            <div className="flex gap-2">
              <button onClick={() => setSelectedTeams(new Set(teams.map((t) => t.id)))}
                className="flex-1 rounded-sm border border-border-strong bg-surface-2 px-2 py-1.5 text-xs font-medium hover:bg-muted">Show all</button>
              <button onClick={() => setSelectedTeams(new Set())}
                className="flex-1 rounded-sm border border-border bg-surface px-2 py-1.5 text-xs font-medium hover:bg-muted">Hide all</button>
            </div>
          </div>
          <div
            onPointerDown={startResize("left")}
            className="absolute top-0 right-0 z-10 h-full w-1.5 cursor-col-resize bg-transparent hover:bg-primary/40"
            title="Drag to resize"
          />
        </aside>
        )}

        <main className="flex min-w-0 flex-1 flex-col">
          <MapCanvas
            time={time}
            ring={showRing ? ring : null}
            trajectories={trajectories}
            dwellsByTeam={dwellsByTeam}
            cfg={cfg}
            onCfg={setCfg}
            showConfig={showConfig}
            setShowConfig={setShowConfig}
            selectedTeams={selectedTeams}
            hoverTeam={hoverTeam}
            showTrails={showTrails}
            showLabels={showLabels}
            mapImage={_override?.mapImage ?? apexMap.image}
            mapName={apexMap.name}
            aliveTeams={aliveTeams}
            totalKills={totalKills}
            duration={durationSec}
            deathTimes={deathTimes}
            ringIndex={ringPhases.findIndex((p) => time >= p.startSec && time <= p.endSec)}
            ringCount={ringPhases.length}
            controls={{ showTrails, setShowTrails, showRing, setShowRing, showLabels, setShowLabels }}
            focusRequest={focusRequest}
            onEventClick={handleEventClick}
            ringPhases={ringPhases}
            teams={teams}
            matchId={match.id}
          />

          <Timeline time={time} duration={durationSec} playing={playing} speed={speed}
            onSeek={setTime} onTogglePlay={() => setPlaying((p) => !p)} onSpeedChange={setSpeed}
            ringSegments={ringSegments} events={events} />
        </main>

        {rightCollapsed ? (
          <button
            onClick={() => setRightCollapsed(false)}
            title="Show match feed"
            className="absolute right-2 top-2 z-20 hidden h-10 w-10 items-center justify-center rounded-sm border border-border-strong bg-surface-2/90 text-foreground shadow-md backdrop-blur hover:bg-muted xl:flex"
          >
            <Swords className="h-5 w-5" />
          </button>
        ) : (
        <aside
          className="relative hidden shrink-0 flex-col border-l border-border bg-surface xl:flex"
          style={{ width: rightWidth }}
        >
          <div
            onPointerDown={startResize("right")}
            className="absolute top-0 left-0 z-10 h-full w-1.5 cursor-col-resize bg-transparent hover:bg-primary/40"
            title="Drag to resize"
          />
          <PanelHeader title="Match feed" subtitle={`${filteredEvents.length}/${events.length}`} />
          <div className="flex flex-wrap gap-1 border-b border-border px-2 py-2">
            {EVENT_FILTERS.map(f => {
              const count = f.id === "all" ? events.length : events.filter(e => matchesFilter(e, f.id)).length;
              const active = eventFilter === f.id;
              return (
                <button key={f.id} onClick={() => setEventFilter(f.id)}
                  className={`text-mono rounded-sm border px-1.5 py-0.5 text-xs uppercase tracking-wider transition-colors ${
                    active
                      ? "border-primary bg-primary/15 text-primary"
                      : "border-border bg-surface-2 text-muted-foreground hover:text-foreground"
                  } ${count === 0 ? "opacity-40" : ""}`}>
                  {f.label} <span className="ml-0.5 opacity-70">{count}</span>
                </button>
              );
            })}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto scrollbar-slim">
            {filteredEvents.length === 0 && (
              <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                Нет событий в этой категории
              </div>
            )}
            {filteredEvents.map((e, i) => {
              const active = time >= e.t - 4 && time <= e.t + 4;
              const past = time > e.t + 4;
              return (
                <button key={i} onClick={() => handleEventClick(e)}
                  className={`group flex w-full items-start gap-3 border-b border-border px-3 py-2.5 text-left transition-colors ${
                    active ? "bg-primary/10" : past ? "opacity-60 hover:bg-muted" : "hover:bg-muted"}`}>
                  <span className="text-mono mt-0.5 w-12 shrink-0 text-xs text-muted-foreground">{formatTime(e.t)}</span>
                  <span
                    className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center"
                    style={{ color: eventColor(e.type) }}
                  >
                    <EventIcon type={e.type} />
                  </span>
                  <span className="min-w-0 text-xs leading-snug">
                    <span className="label-eyebrow mr-1.5 text-xs">{e.type}</span>
                    <span className="text-foreground">{e.label}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </aside>
        )}
      </div>
    </div>
  );
}

function eventColor(type: string) {
  switch (type) {
    case "kill":
    case "wipe":  return "#ef4444"; // danger
    case "knock": return "#fbbf24"; // warning
    case "ring":  return "#22c4f5"; // info
    case "care":  return "#34d399"; // success
    case "endgame": return "#a78bfa"; // accent
    default:      return "#94a3b8"; // neutral
  }
}

function EventIcon({ type }: { type: string }) {
  const cls = "h-5 w-5";
  switch (type) {
    case "kill":  return (
      <img
        src={damageIcon}
        alt="kill"
        className={cls}
        style={{
          filter: "brightness(0) saturate(100%) invert(38%) sepia(93%) saturate(3000%) hue-rotate(346deg) brightness(96%) contrast(97%)",
        }}
      />
    );
    case "wipe":  return <Skull className={cls} strokeWidth={2.5} />;
    case "knock": return (
      <img
        src={damageIcon}
        alt="knock"
        className={cls}
        style={{
          filter: "brightness(0) saturate(100%) invert(72%) sepia(57%) saturate(728%) hue-rotate(359deg) brightness(101%) contrast(98%)",
        }}
      />
    );
    case "ring":  return <ShieldAlert className={cls} strokeWidth={2.5} />;
    case "care":  return <Package className={cls} strokeWidth={2.5} />;
    case "endgame": return <Flag className={cls} strokeWidth={2.5} />;
    default:      return <Circle className={cls} strokeWidth={2.5} />;
  }
}

/* ---------- TOP BAR ---------- */
function TopBar({
  tournamentId, onTournamentChange, matchId, onMatchChange, gameId, games, onGameChange, mapName, aliveTeams, totalKills,
}: {
  tournamentId: string; onTournamentChange: (id: string) => void;
  matchId: string; onMatchChange: (id: string) => void;
  gameId: string; games: Game[]; onGameChange: (id: string) => void;
  mapName: string; aliveTeams: number; totalKills: number;
}) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-border bg-surface px-4">
      <Link to="/" className="flex items-center gap-2.5">
        <div className="flex h-7 w-7 items-center justify-center rounded-sm bg-primary text-primary-foreground">
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2.5}>
            <path d="M12 3 L21 20 H3 Z" />
          </svg>
        </div>
        <div className="leading-tight">
          <div className="text-sm font-bold tracking-tight">APEX STATS</div>
          <div className="label-eyebrow text-xs">VOD analytics</div>
        </div>
      </Link>

      <div className="ml-2 h-6 w-px bg-border" />

      <Select label="Tournament" value={tournamentId} onChange={onTournamentChange}
        options={tournaments.map((t) => ({ value: t.id, label: t.name }))} />
      <Select label="Match" value={matchId} onChange={onMatchChange}
        options={matches.filter((m) => m.tournamentId === tournamentId).map((m) => ({ value: m.id, label: m.name }))} />
      <Select label="Game" value={gameId} onChange={onGameChange}
        options={games.map((g) => ({ value: g.id, label: `G${g.index + 1} · ${maps.find((mp) => mp.id === g.mapId)?.name ?? g.mapId}` }))} />
      <div className="hud-panel hidden items-center gap-2 px-3 py-1.5 text-xs md:flex">
        <span className="label-eyebrow text-xs">Map</span>
        <span className="text-mono font-semibold">{mapName}</span>
      </div>

      <div className="ml-auto flex items-center gap-2">
        <span className="flex items-center gap-1.5 rounded-sm border border-border bg-surface-2 px-2.5 py-1 text-xs">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-success" />
          <span className="label-eyebrow text-xs">Live</span>
        </span>
      </div>
    </header>
  );
}

function Select({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[];
}) {
  return (
    <label className="hud-panel flex items-center gap-2 px-2.5 py-1.5 text-xs">
      <span className="label-eyebrow text-xs">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="text-mono bg-transparent text-xs font-medium text-foreground outline-none">
        {options.map((o) => (
          <option key={o.value} value={o.value} className="bg-surface text-foreground">{o.label}</option>
        ))}
      </select>
    </label>
  );
}

function PanelHeader({ title, subtitle }: { title: string; subtitle?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
      <h2 className="text-xs font-bold uppercase tracking-wider">{title}</h2>
      {subtitle && <span className="text-mono text-xs text-muted-foreground">{subtitle}</span>}
    </div>
  );
}

function TeamRow({ team, slotIndex, active, hovered, onToggle, onHover, logoSize = 20, compact = false, alive }: {
  team: Team; slotIndex: number; active: boolean; hovered: boolean; onToggle: () => void; onHover: (v: boolean) => void;
  logoSize?: number; compact?: boolean;
  /** Live alive override; falls back to the static `team.alive` flag. */
  alive?: boolean;
}) {
  const slotColor = getSlotColor(slotIndex);
  const nameSize = Math.max(12, Math.min(18, Math.round(logoSize * 0.6)));
  const isAlive = alive ?? team.alive;
  if (compact) {
    return (
      <div onMouseEnter={() => onHover(true)} onMouseLeave={() => onHover(false)}
        onClick={onToggle}
        className={`group relative mb-1 flex h-12 cursor-pointer items-center rounded-sm border px-4 transition-colors ${
          active ? "border-border-strong bg-surface-2" : "border-transparent bg-transparent opacity-50"
        } ${hovered ? "ring-1 ring-primary/40" : ""}`}>
        <div className="pointer-events-none flex h-full w-full items-center justify-center">
          <TeamLogo team={team} size={logoSize} className="!rounded-none !border-0 !bg-transparent" />
        </div>
        <span className="absolute left-1.5 top-1.5 h-2.5 w-2.5 rounded-sm"
          style={{ backgroundColor: slotColor }} />
        <span className={`absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full ${isAlive ? "bg-success" : "bg-destructive/70"}`} />
      </div>
    );
  }
  return (
    <div onMouseEnter={() => onHover(true)} onMouseLeave={() => onHover(false)}
      className={`group relative mb-1 flex cursor-pointer items-center gap-2.5 rounded-sm border px-2 py-1.5 transition-colors ${
        active ? "border-border-strong bg-surface-2" : "border-transparent bg-transparent opacity-50"
      } ${hovered ? "ring-1 ring-primary/40" : ""}`} onClick={onToggle}>
      <span className="h-2.5 w-2.5 shrink-0 rounded-sm"
        style={{ backgroundColor: slotColor }} />
      <TeamLogo team={team} size={logoSize} />
      <span className="text-mono w-6 text-xs tabular-nums text-muted-foreground">#{team.placement}</span>
      <span className="min-w-0 flex-1 truncate font-semibold" style={{ fontSize: nameSize }}>{team.name}</span>
      <span className={`h-1.5 w-1.5 rounded-full ${isAlive ? "bg-success" : "bg-destructive/70"}`} />
    </div>
  );
}

function LayerToggle({ label, active, onChange }: { label: string; active: boolean; onChange: (v: boolean) => void }) {
  return (
    <button onClick={() => onChange(!active)}
      className={`flex items-center justify-between gap-3 rounded-sm px-2 py-1 text-xs transition-colors ${
        active ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted"}`}>
      <span className="label-eyebrow text-xs">{label}</span>
      <span className={`h-1.5 w-3 rounded-full ${active ? "bg-primary" : "bg-border-strong"}`} />
    </button>
  );
}

function CfgSlider({ label, value, min, max, step, onChange }: {
  label: string; value: number; min: number; max: number; step: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block">
      <div className="flex items-center justify-between mb-0.5">
        <span className="label-eyebrow text-xs">{label}</span>
        <span className="text-mono text-xs text-muted-foreground tabular-nums">
          {Number.isInteger(step) ? value : value.toFixed(2)}
        </span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full accent-primary" />
    </label>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="hud-panel-strong px-3 py-1.5">
      <div className="label-eyebrow text-xs">{label}</div>
      <div className={`text-mono text-sm font-bold tabular-nums ${accent ? "text-primary" : ""}`}>{value}</div>
    </div>
  );
}

/* ---------- RING PHASE STATUS chip (sits next to Stat row) ---------- */
function RingPhaseChip({ time, ringSegments }: { time: number; ringSegments: RingSegment[] }) {
  const seg = ringSegments.find(s => time >= s.startSec && time <= s.endSec)
    ?? ringSegments[ringSegments.length - 1];
  const isClosing = seg.kind === "Closing";
  const remaining = Math.max(0, Math.round(seg.endSec - time));
  const accent    = isClosing ? "text-destructive" : "text-cyan";
  const dot       = isClosing ? "bg-destructive"   : "bg-cyan";
  return (
    <div className="hud-panel-strong flex items-center gap-2.5 px-3 py-1.5">
      <span className={`h-2 w-2 shrink-0 rounded-full ${dot} ${isClosing ? "animate-pulse" : ""}`} />
      <div className="leading-tight">
        <div className="label-eyebrow text-xs">Phase</div>
        <div className={`text-mono text-sm font-bold uppercase tracking-wider ${accent}`}>
          R{seg.phaseIndex + 1} {seg.kind}
        </div>
      </div>
      <div className="text-mono ml-1 border-l border-border pl-2.5 text-xs tabular-nums text-muted-foreground">
        {formatTime(remaining)}
      </div>
    </div>
  );
}

type Cfg = { trailWidth: number; labelSize: number; labelBg: number; dwellWindow: number; dwellRadius: number };
type Dwell = { x: number; y: number; tStart: number; tEnd: number };

/* ---------- MAP with pan/zoom ---------- */
function MapCanvas({
  time, ring, trajectories, dwellsByTeam, cfg, onCfg, showConfig, setShowConfig,
  selectedTeams, hoverTeam, showTrails, showLabels,
  mapImage, mapName, aliveTeams, totalKills, duration, deathTimes, ringIndex, ringCount, controls,
  focusRequest, onEventClick, ringPhases, teams,
  matchId,
}: {
  time: number; ring: RingPhase | null;
  trajectories: Record<string, { t: number; x: number; y: number }[]>;
  dwellsByTeam: Record<string, Dwell[]>;
  cfg: Cfg;
  onCfg: (next: Cfg) => void;
  showConfig: boolean;
  setShowConfig: (v: boolean) => void;
  selectedTeams: Set<string>; hoverTeam: string | null;
  showTrails: boolean; showLabels: boolean;
  mapImage: string; mapName: string;
  aliveTeams: number; totalKills: number; duration: number;
  /** Per-team death timestamps (sec). Absent → still alive. */
  deathTimes: Record<string, number>;
  ringIndex: number; ringCount: number;
  controls: {
    showTrails: boolean; setShowTrails: (v: boolean) => void;
    showRing: boolean; setShowRing: (v: boolean) => void;
    showLabels: boolean; setShowLabels: (v: boolean) => void;
  };
  focusRequest: { x: number; y: number; token: number } | null;
  onEventClick: (e: GameEvent) => void;
  ringPhases: RingPhase[];
  teams: Team[];
  matchId: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const ringSegments = useMemo(() => buildRingSegments(ringPhases), [ringPhases]);
  const [view, setView] = useState({ scale: 1, tx: 0, ty: 0 });
  const drag = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);

  const clampScale = (s: number) => Math.max(1, Math.min(6, s));

  const onWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const rect = containerRef.current!.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    setView((v) => {
      const factor = Math.exp(-e.deltaY * 0.0015);
      const ns = clampScale(v.scale * factor);
      const k = ns / v.scale;
      // keep cursor point stable
      const ntx = cx - k * (cx - v.tx);
      const nty = cy - k * (cy - v.ty);
      return clampPan({ scale: ns, tx: ntx, ty: nty }, rect.width, rect.height);
    });
  }, []);

  const clampPan = (v: { scale: number; tx: number; ty: number }, w: number, h: number) => {
    const minX = w - w * v.scale;
    const minY = h - h * v.scale;
    return { scale: v.scale, tx: Math.min(0, Math.max(minX, v.tx)), ty: Math.min(0, Math.max(minY, v.ty)) };
  };

  const onMouseDown = (e: React.MouseEvent) => {
    drag.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty };
  };
  const onMouseMove = (e: React.MouseEvent) => {
    if (!drag.current) return;
    const rect = containerRef.current!.getBoundingClientRect();
    const nx = drag.current.tx + (e.clientX - drag.current.x);
    const ny = drag.current.ty + (e.clientY - drag.current.y);
    setView((v) => clampPan({ scale: v.scale, tx: nx, ty: ny }, rect.width, rect.height));
  };
  const onMouseUp = () => { drag.current = null; };

  const zoomBy = (factor: number) => {
    const rect = containerRef.current!.getBoundingClientRect();
    const cx = rect.width / 2, cy = rect.height / 2;
    setView((v) => {
      const ns = clampScale(v.scale * factor);
      const k = ns / v.scale;
      return clampPan({ scale: ns, tx: cx - k * (cx - v.tx), ty: cy - k * (cy - v.ty) }, rect.width, rect.height);
    });
  };
  const resetView = () => setView({ scale: 1, tx: 0, ty: 0 });

  // Center the map on an external focus request (event click in feed).
  useEffect(() => {
    if (!focusRequest || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const size = Math.min(rect.width, rect.height);
    const offsetX = (rect.width - size) / 2;
    const offsetY = (rect.height - size) / 2;
    const targetScale = 2.5;
    const px = offsetX + focusRequest.x * size;
    const py = offsetY + focusRequest.y * size;
    const tx = rect.width / 2 - targetScale * px;
    const ty = rect.height / 2 - targetScale * py;
    setView(clampPan({ scale: targetScale, tx, ty }, rect.width, rect.height));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusRequest?.token]);

  return (
    <div
      ref={containerRef}
      onWheel={onWheel}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      onMouseLeave={onMouseUp}
      className="relative min-h-0 flex-1 overflow-hidden bg-background hud-grid-bg select-none"
      style={{ cursor: drag.current ? "grabbing" : "grab" }}
    >
      <div
        className="absolute inset-0 origin-top-left"
        style={{ transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.scale})` }}
      >
        <div className="relative h-full w-full">
          <img src={mapImage} alt={mapName} draggable={false}
            className="absolute inset-0 h-full w-full object-contain opacity-95" />
          <svg viewBox="0 0 1000 1000" preserveAspectRatio="xMidYMid meet"
            className="absolute inset-0 h-full w-full pointer-events-none">
            <defs>
              <filter id="glow">
                <feGaussianBlur stdDeviation="2.5" result="b" />
                <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
              </filter>
              <clipPath id="mapBounds">
                <rect x="0" y="0" width="1000" height="1000" />
              </clipPath>
            </defs>

            {ring && (
              <g clipPath="url(#mapBounds)">
                {/* Red DANGER ZONE — everything outside the active safe area.
                    Rendered as a single path: full map rectangle minus a circle
                    at the safe area, using even-odd fill-rule. */}
                <path
                  d={`M0,0 H1000 V1000 H0 Z M ${ring.cx * 1000},${(ring.cy * 1000) - ring.r * 1000} a ${ring.r * 1000},${ring.r * 1000} 0 1,0 0,${ring.r * 2000} a ${ring.r * 1000},${ring.r * 1000} 0 1,0 0,${-ring.r * 2000} Z`}
                  fillRule="evenodd"
                  fill="rgba(239,68,68,0.28)"
                  stroke="none"
                />
                {/* Static preview of all 6 ring phases */}
                {ringPhases.map((p, i) => (
                  <circle key={`prev-${i}`} cx={p.cx * 1000} cy={p.cy * 1000} r={p.r * 1000}
                    fill="none" stroke="rgba(255,255,255,0.85)"
                    strokeWidth={1.6 / view.scale}
                    strokeDasharray={`${4 / view.scale} ${4 / view.scale}`} />
                ))}
                <circle cx={ring.cx * 1000} cy={ring.cy * 1000} r={ring.r * 1000}
                  fill="rgba(34,196,245,0.08)" stroke="#22c4f5" strokeWidth={3.5 / view.scale} strokeDasharray={`${10 / view.scale} ${5 / view.scale}`} />
                <circle cx={ring.cx * 1000} cy={ring.cy * 1000} r={3 / view.scale} fill="#22c4f5" />
              </g>
            )}

            {teams.map((t, slotIdx) => {
              if (!selectedTeams.has(t.id)) return null;
              const slotColor = getSlotColor(slotIdx);
              const path = trajectories[t.id];
              const deathT = deathTimes[t.id];
              const isDead = deathT !== undefined && time >= deathT;
              // Freeze trajectory at the moment of death.
              const effectiveTime = isDead ? deathT : time;
              const upTo = path.filter((p) => p.t <= effectiveTime);
              if (upTo.length === 0) return null;
              const head = upTo[upTo.length - 1];
              const dimOthers = hoverTeam && hoverTeam !== t.id;
              const opacity = dimOthers ? 0.15 : (isDead ? 0.55 : 1);
              const trail = upTo.slice(-60);
              const d = trail.map((p, i) => `${i === 0 ? "M" : "L"}${p.x * 1000} ${p.y * 1000}`).join(" ");
              const dwells = (dwellsByTeam[t.id] ?? []).filter((dw) => dw.tStart <= time);
              const labelW = t.tag.length * (cfg.labelSize * 0.64) + cfg.labelSize * 0.55;
              const labelH = cfg.labelSize * 1.28;

              return (
                <g key={t.id} opacity={opacity}>
                  {/* Dwell clusters */}
                  {dwells.map((dw, di) => {
                    const dur = Math.round(dw.tEnd - dw.tStart);
                    return (
                      <g key={`dw-${di}`} transform={`translate(${dw.x * 1000} ${dw.y * 1000})`}>
                        <circle r={cfg.dwellRadius * 1000} fill={slotColor} fillOpacity={0.1}
                          stroke={slotColor} strokeOpacity={0.6}
                          strokeWidth={1.2 / view.scale}
                          strokeDasharray={`${3 / view.scale} ${3 / view.scale}`} />
                        <circle r={5 / view.scale} fill={slotColor} stroke="#000" strokeWidth={0.8 / view.scale} />
                        <g transform={`translate(0 ${cfg.dwellRadius * 1000 + 14 / view.scale})`}>
                          <rect
                            x={-32 / view.scale} y={-9 / view.scale}
                            width={64 / view.scale} height={18 / view.scale}
                            rx={2 / view.scale} ry={2 / view.scale}
                            fill={`rgba(0,0,0,${cfg.labelBg})`}
                            stroke={slotColor} strokeWidth={1 / view.scale}
                          />
                          <text x={0} y={4 / view.scale} textAnchor="middle"
                            fontSize={11 / view.scale} fontWeight={700} fill="#fff"
                            fontFamily="Manrope, sans-serif">
                            {formatTime(dw.tStart)} · {dur}s
                          </text>
                        </g>
                      </g>
                    );
                  })}
                  {showTrails && (
                    <path d={d} fill="none"
                      stroke={isDead ? "#9ca3af" : slotColor}
                      strokeWidth={cfg.trailWidth / view.scale}
                      strokeOpacity={isDead ? 0.4 : 0.75}
                      strokeDasharray={isDead ? `${4 / view.scale} ${3 / view.scale}` : undefined}
                      strokeLinecap="round" strokeLinejoin="round" />
                  )}
                  <g transform={`translate(${head.x * 1000} ${head.y * 1000})`}>
                    {isDead ? (
                      // Frozen "tombstone": desaturated body + bright slot-color ring,
                      // so the team's identity is still readable but clearly out.
                      <g>
                        <circle r={9 / view.scale} fill="none"
                          stroke={slotColor} strokeWidth={2 / view.scale} opacity={0.9} />
                        <circle r={5.5 / view.scale} fill="#6b7280"
                          stroke="rgba(0,0,0,0.85)" strokeWidth={1 / view.scale} />
                        {/* X mark */}
                        <path d={`M${-3 / view.scale},${-3 / view.scale} L${3 / view.scale},${3 / view.scale} M${3 / view.scale},${-3 / view.scale} L${-3 / view.scale},${3 / view.scale}`}
                          stroke="#fff" strokeWidth={1.4 / view.scale} strokeLinecap="round" />
                      </g>
                    ) : (
                      <g filter="url(#glow)">
                        <circle r={11 / view.scale} fill="none" stroke={slotColor} strokeWidth={1 / view.scale} opacity={0.5} />
                        <circle r={6 / view.scale} fill={slotColor} stroke="rgba(0,0,0,0.8)" strokeWidth={1 / view.scale} />
                      </g>
                    )}
                    {showLabels && (
                      <g transform={`translate(${14 / view.scale} ${-(labelH / 2) / view.scale})`}>
                        <rect
                          x={0}
                          y={0}
                          rx={3 / view.scale}
                          ry={3 / view.scale}
                          width={labelW / view.scale}
                          height={labelH / view.scale}
                          fill={`rgba(0,0,0,${cfg.labelBg})`}
                          stroke={isDead ? "#9ca3af" : slotColor}
                          strokeWidth={2 / view.scale}
                          strokeDasharray={isDead ? `${3 / view.scale} ${2 / view.scale}` : undefined}
                        />
                        <text
                          x={(labelW / 2) / view.scale}
                          y={(labelH * 0.72) / view.scale}
                          textAnchor="middle"
                          fontSize={cfg.labelSize / view.scale}
                          fontWeight={800}
                          fill={isDead ? "#d1d5db" : "#fff"}
                          fontFamily="Manrope, sans-serif"
                          style={{ letterSpacing: `${0.6 / view.scale}px` }}
                        >
                          {t.tag}
                        </text>
                      </g>
                    )}
                  </g>
                </g>
              );
            })}

          </svg>
        </div>
      </div>


      {/* Single CONFIG button (top-right) */}
      <div className="pointer-events-auto absolute right-4 top-4">
        <button onClick={() => setShowConfig(!showConfig)}
          className={`hud-panel-strong flex items-center gap-2 px-3 py-2 text-xs transition-colors ${
            showConfig ? "text-primary" : "text-foreground hover:text-primary"}`}>
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2}>
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5h0a1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
          </svg>
          <span className="label-eyebrow text-xs">Config</span>
        </button>

        {showConfig && (
          <div className="mt-2 hud-panel-strong w-64 p-3 text-xs space-y-3">
            <div className="flex items-center justify-between">
              <span className="label-eyebrow">Layers</span>
              <button onClick={() => setShowConfig(false)} className="text-muted-foreground hover:text-foreground">×</button>
            </div>
            <div className="flex flex-col gap-1">
              <LayerToggle label="Trails" active={controls.showTrails} onChange={controls.setShowTrails} />
              <LayerToggle label="Ring" active={controls.showRing} onChange={controls.setShowRing} />
              <LayerToggle label="Labels" active={controls.showLabels} onChange={controls.setShowLabels} />
            </div>
            <div className="border-t border-border pt-2 space-y-3">
              <span className="label-eyebrow block">Map config</span>
              <CfgSlider label="Trail width" value={cfg.trailWidth} min={0.5} max={6} step={0.5}
                onChange={(v) => onCfg({ ...cfg, trailWidth: v })} />
              <CfgSlider label="Label size" value={cfg.labelSize} min={8} max={40} step={1}
                onChange={(v) => onCfg({ ...cfg, labelSize: v })} />
              <CfgSlider label="Label bg" value={cfg.labelBg} min={0} max={1} step={0.05}
                onChange={(v) => onCfg({ ...cfg, labelBg: v })} />
            </div>
            <div className="border-t border-border pt-2 space-y-3">
              <CfgSlider label="Dwell window (s)" value={cfg.dwellWindow} min={10} max={120} step={5}
                onChange={(v) => onCfg({ ...cfg, dwellWindow: v })} />
              <CfgSlider label="Dwell radius" value={cfg.dwellRadius} min={0.01} max={0.12} step={0.005}
                onChange={(v) => onCfg({ ...cfg, dwellRadius: v })} />
            </div>
          </div>
        )}
      </div>

      {/* Zoom controls */}
      <div className="pointer-events-auto absolute right-4 bottom-4 hud-panel-strong flex flex-col overflow-hidden text-xs">
        <button onClick={() => zoomBy(1.5)} className="flex h-8 w-8 items-center justify-center border-b border-border hover:bg-muted" aria-label="Zoom in">+</button>
        <button onClick={() => zoomBy(1 / 1.5)} className="flex h-8 w-8 items-center justify-center border-b border-border hover:bg-muted" aria-label="Zoom out">−</button>
        <button onClick={resetView} className="text-mono flex h-8 w-8 items-center justify-center text-xs hover:bg-muted" aria-label="Reset zoom">1:1</button>
      </div>
      <div className="pointer-events-none absolute bottom-4 right-16 hud-panel-strong px-2 py-1 text-mono text-xs text-muted-foreground">
        {(view.scale * 100).toFixed(0)}%
      </div>

      <div className="pointer-events-none absolute bottom-4 left-4 flex flex-wrap items-stretch gap-2">
        <Stat label="Alive" value={`${aliveTeams}/${teams.length}`} accent />
        <Stat label="Kills" value={totalKills.toString()} />
        <Stat label="Ring"  value={`${ringIndex + 1 || ringCount}/${ringCount}`} />
        <RingPhaseChip time={time} ringSegments={ringSegments} />
      </div>
    </div>
  );
}

/* ---------- TIMELINE ---------- */
function Timeline({
  time, duration, playing, speed, onSeek, onTogglePlay, onSpeedChange, ringSegments, events,
}: {
  time: number; duration: number; playing: boolean; speed: number;
  onSeek: (t: number) => void; onTogglePlay: () => void; onSpeedChange: (s: number) => void;
  ringSegments: RingSegment[]; events: GameEvent[];
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const seekFromClientX = (clientX: number) => {
    const r = trackRef.current!.getBoundingClientRect();
    const k = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
    onSeek(k * duration);
  };
  const onTrackPointerDown = (e: React.PointerEvent) => {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragging.current = true;
    seekFromClientX(e.clientX);
  };
  const onTrackPointerMove = (e: React.PointerEvent) => {
    if (!dragging.current) return;
    seekFromClientX(e.clientX);
  };
  const onTrackPointerUp = (e: React.PointerEvent) => {
    dragging.current = false;
    try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); } catch {}
  };
  const speeds = [1, 2, 4, 8];

  return (
    <div className="shrink-0 border-t border-border bg-surface">
      <div className="flex items-center gap-3 px-4 py-2.5">
        <button onClick={onTogglePlay}
          className="flex h-9 w-9 items-center justify-center rounded-sm bg-primary text-primary-foreground transition-colors hover:brightness-110"
          aria-label={playing ? "Pause" : "Play"}>
          {playing ? (
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" /><rect x="14" y="5" width="4" height="14" /></svg>
          ) : (
            <svg className="h-3.5 w-3.5 translate-x-[1px]" viewBox="0 0 24 24" fill="currentColor"><path d="M6 4 L20 12 L6 20 Z" /></svg>
          )}
        </button>
        <button onClick={() => onSeek(Math.max(0, time - 10))} className="text-mono rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs hover:bg-muted">−10s</button>
        <button onClick={() => onSeek(Math.min(duration, time + 10))} className="text-mono rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs hover:bg-muted">+10s</button>

        <div className="text-mono flex items-center gap-1 text-xs tabular-nums">
          <span className="font-semibold">{formatTime(time)}</span>
          <span className="text-muted-foreground">/ {formatTime(duration)}</span>
        </div>

        <div className="ml-auto flex items-center gap-1">
          <span className="label-eyebrow text-xs mr-1">Speed</span>
          {speeds.map((s) => (
            <button key={s} onClick={() => onSpeedChange(s)}
              className={`text-mono rounded-sm px-2 py-1 text-xs font-semibold ${
                speed === s ? "bg-primary text-primary-foreground" : "border border-border bg-surface-2 text-muted-foreground hover:text-foreground"}`}>{s}×</button>
          ))}
        </div>
      </div>

      <div className="px-4 pb-3">
        <div
          ref={trackRef}
          onPointerDown={onTrackPointerDown}
          onPointerMove={onTrackPointerMove}
          onPointerUp={onTrackPointerUp}
          onPointerCancel={onTrackPointerUp}
          className="relative h-9 cursor-pointer touch-none select-none rounded-sm border border-border bg-background"
        >
          {ringSegments.map((seg, i) => {
            const isClosing = seg.kind === "Closing";
            const intensity = 0.04 + seg.phaseIndex * 0.025;
            return (
              <div key={i}
                className={`absolute top-0 h-full ${isClosing ? "border-l border-r border-destructive/50" : "border-r border-border/60"}`}
                style={{
                  left: `${(seg.startSec / duration) * 100}%`,
                  width: `${((seg.endSec - seg.startSec) / duration) * 100}%`,
                  background: isClosing
                    ? `repeating-linear-gradient(45deg, rgba(239,68,68,${intensity + 0.18}) 0 4px, rgba(239,68,68,${intensity + 0.05}) 4px 8px)`
                    : `rgba(34,196,245,${intensity})`,
                }}>
                <div className={`text-mono absolute left-1 top-0.5 text-xs uppercase tracking-wider ${
                  isClosing ? "text-destructive font-bold" : "text-muted-foreground/80"}`}>
                  R{seg.phaseIndex + 1} {seg.kind}
                </div>
              </div>
            );
          })}
          {/* progress fill — subtle tint of elapsed time */}
          <div className="pointer-events-none absolute inset-y-0 left-0 bg-primary/10"
            style={{ width: `${(time / duration) * 100}%` }} />
          {events.map((e, i) => (
            <div key={i} className="absolute top-0 h-full w-px"
              style={{ left: `${(e.t / duration) * 100}%`, backgroundColor: eventColor(e.type), opacity: 0.7 }}
              title={`${formatTime(e.t)} — ${e.label}`} />
          ))}
          {/* playhead */}
          <div className="pointer-events-none absolute top-0 h-full w-0.5 bg-primary shadow-[0_0_10px_rgba(255,91,18,0.7)]"
            style={{ left: `${(time / duration) * 100}%` }}>
            {/* top handle */}
            <div className="absolute -left-2 -top-1.5 flex h-3 w-3 items-center justify-center rounded-sm bg-primary ring-2 ring-background">
              <span className="h-1 w-1 rounded-full bg-primary-foreground/90" />
            </div>
            {/* bottom triangle */}
            <div className="absolute -left-[5px] -bottom-1 h-0 w-0 border-l-[6px] border-r-[6px] border-t-[6px] border-l-transparent border-r-transparent border-t-primary" />
            {/* current time label */}
            <div className="text-mono absolute -top-5 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-sm bg-primary px-1.5 py-0.5 text-xs font-bold tabular-nums text-primary-foreground shadow-md">
              {formatTime(time)}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
