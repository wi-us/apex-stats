import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAdminStore, updateTeam, updateMatch } from "@/lib/admin-store";
import { maps as allMaps, type MatchFull } from "@/lib/mock-match";
import type { Team } from "@/lib/mock-match";
import { TeamLogo } from "@/components/admin/TeamLogo";
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover";
import {
  fetchTeamDetail,
  type TeamDetail,
  type TeamMatchStat,
  type TeamPoiPick,
  type TeamPlayer,
  type TeamRosterMember,
  type TeamWeaponStat,
} from "@/lib/algs-team-fetchers";

export const Route = createFileRoute("/admin/teams/$teamId")({ component: TeamDetail });

type Mode = "all" | "year" | "tournaments" | "range";
type RangeKey = "7d" | "30d" | "90d" | "180d" | "365d";
const RANGE_DAYS: Record<RangeKey, number> = { "7d": 7, "30d": 30, "90d": 90, "180d": 180, "365d": 365 };
const RANGE_LABEL: Record<RangeKey, string> = { "7d": "Week", "30d": "Month", "90d": "3 mo", "180d": "6 mo", "365d": "12 mo" };

/** Deterministic per-match date derived from tournament window + match index. */
function matchDateTime(match: MatchFull, tourStart?: string, tourEnd?: string, indexInTour = 0) {
  if (!tourStart) return null;
  const start = new Date(tourStart + "T18:00:00Z").getTime();
  const end = tourEnd ? new Date(tourEnd + "T22:00:00Z").getTime() : start + 86400000;
  const span = Math.max(86400000, end - start);
  // 6 games per day stagger; spread across tournament window deterministically.
  const offset = (indexInTour * 75 * 60_000) % span; // 75 min between games
  return new Date(start + offset);
}
function fmtDate(d: Date | null) {
  if (!d) return "—";
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "2-digit" });
}
function fmtTime(d: Date | null) {
  if (!d) return "";
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function TeamDetail() {
  const { teamId } = Route.useParams();
  const { teams, matches, tournaments } = useAdminStore();
  const navigate = useNavigate();
  const team = teams.find((t) => t.id === teamId);
  const [editing, setEditing] = useState<Team | null>(null);
  const detailQuery = useQuery<TeamDetail>({
    queryKey: ["algs-team-detail", teamId],
    queryFn: () => fetchTeamDetail(teamId),
    staleTime: 5 * 60 * 1000,
  });
  const detail = detailQuery.data;
  if (!team) {
    return (
      <div className="p-6 text-sm">
        Team not found. <Link to="/admin/teams" className="text-primary underline">Back to teams</Link>
      </div>
    );
  }
  const tourIndex = new Map(tournaments.map((t) => [t.id, t]));
  const today = Date.now();

  // Real ALGS match-team stats keyed by match_id. Empty for mock seed teams.
  const realStatByMatchId = useMemo(() => {
    const m = new Map<string, TeamMatchStat>();
    for (const s of detail?.matches ?? []) m.set(s.matchId, s);
    return m;
  }, [detail]);

  // Annotate matches with derived datetime and per-tournament index.
  type Row = {
    match: MatchFull;
    tour: ReturnType<typeof tourIndex.get>;
    date: Date | null;
    stat?: TeamMatchStat;
  };
  const tourCounters = new Map<string, number>();
  const allRows: Row[] = matches.map((m) => {
    const idx = tourCounters.get(m.tournamentId) ?? 0;
    tourCounters.set(m.tournamentId, idx + 1);
    const tour = tourIndex.get(m.tournamentId);
    // Prefer real started_at from ALGS for any of the games (series→match in store
    // represents an ALGS *series*; pick the earliest real start among its games).
    const realStarts = (detail?.matches ?? [])
      .filter((x) => x.seriesId === m.id && x.startedAt)
      .map((x) => Date.parse(x.startedAt!))
      .sort((a, b) => a - b);
    const realDate = realStarts.length > 0 ? new Date(realStarts[0]) : null;
    return {
      match: m,
      tour,
      date: realDate ?? matchDateTime(m, tour?.startDate, tour?.endDate, idx),
      stat: undefined,
    };
  });
  // A "team row" is any stored series that has at least one real per-game stat
  // for this team, OR (fallback for mock seed) a series whose teamIds includes us.
  const teamRows = allRows.filter((r) => {
    const hasReal = (detail?.matches ?? []).some((x) => x.seriesId === r.match.id);
    return hasReal || r.match.teamIds?.includes(team.id);
  });
  const teamTournaments = useMemo(() => {
    const ids = Array.from(new Set(teamRows.map((r) => r.match.tournamentId)));
    return ids.map((id) => tourIndex.get(id)).filter(Boolean) as typeof tournaments;
  }, [teamRows]);

  const nextRows = teamRows
    .filter((r) => r.date && r.date.getTime() >= today)
    .sort((a, b) => (a.date!.getTime() - b.date!.getTime()));

  const allYears = Array.from(new Set(tournaments.map((t) => t.year))).sort((a, b) => b - a);
  const [mode, setMode] = useState<Mode>("all");
  const [year, setYear] = useState<number>(allYears[0] ?? 6);
  const [selectedTours, setSelectedTours] = useState<string[]>([]);
  const [range, setRange] = useState<RangeKey>("30d");
  const [view, setView] = useState<"overview" | "maps" | "weapons">("overview");

  const filteredRows = useMemo(() => {
    if (mode === "year") return teamRows.filter((r) => r.tour?.year === year);
    if (mode === "tournaments") return teamRows.filter((r) => selectedTours.includes(r.match.tournamentId));
    if (mode === "range") {
      const cutoff = today - RANGE_DAYS[range] * 86400000;
      return teamRows.filter((r) => r.date && r.date.getTime() >= cutoff && r.date.getTime() <= today);
    }
    return teamRows;
  }, [teamRows, mode, year, selectedTours, range]);

  const filteredTournaments = useMemo(() => {
    if (mode === "year") return teamTournaments.filter((t) => t.year === year);
    if (mode === "tournaments") return teamTournaments.filter((t) => selectedTours.includes(t.id));
    if (mode === "range") {
      const ids = new Set(filteredRows.map((r) => r.match.tournamentId));
      return teamTournaments.filter((t) => ids.has(t.id));
    }
    return teamTournaments;
  }, [teamTournaments, mode, year, selectedTours, filteredRows]);

  // Real per-game placement / kills from ALGS, with deterministic fallback for
  // mock seed series that have no ALGS rows. ALGS "matches" are individual map
  // drops, so a stored series → 1..N ALGS rows joined by series_id.
  function pseudoPlacement(mapId: string, matchId: string): number {
    // Find first real stat for this (series=matchId, map=mapId)
    const real = (detail?.matches ?? []).find(
      (x) => x.seriesId === matchId && (x.mapIdUlid === mapId || x.mapIdUlid?.replace(/_/g, "-") === mapId),
    );
    if (real && real.placement > 0) return real.placement;
    let h = team!.placement * 7;
    const s = mapId + matchId + team!.id;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return 1 + (h % 20);
  }
  function pseudoKills(matchId: string): number {
    // Sum real kills across all ALGS rows in this series for this team
    const rows = (detail?.matches ?? []).filter((x) => x.seriesId === matchId);
    if (rows.length > 0) {
      return Math.round(rows.reduce((s, r) => s + r.kills, 0) / rows.length);
    }
    let h = (team!.kills + 3) * 17;
    const s = matchId + team!.id + "k";
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return h % 25;
  }

  // Per-map stats over filtered window
  type MapStat = { id: string; count: number; avg: number; top1: number; top5: number };
  // Map tier list ignores the period filter — it represents all-time profile.
  const mapStats = useMemo<MapStat[]>(() => {
    const acc = new Map<string, { sum: number; count: number; top1: number; top5: number }>();
    teamRows.forEach((r) => {
      const ids = r.match.mapIds ?? [r.match.mapId];
      ids.forEach((id) => {
        const p = pseudoPlacement(id, r.match.id);
        const cur = acc.get(id) ?? { sum: 0, count: 0, top1: 0, top5: 0 };
        cur.sum += p;
        cur.count += 1;
        if (p === 1) cur.top1 += 1;
        if (p <= 5) cur.top5 += 1;
        acc.set(id, cur);
      });
    });
    return Array.from(acc.entries())
      .map(([id, v]) => ({ id, count: v.count, avg: v.sum / v.count, top1: v.top1, top5: v.top5 }))
      .sort((a, b) => a.avg - b.avg);
  }, [teamRows]);

  // Tier from average placement (lower is better).
  const tierOf = (avg: number): "S" | "A" | "B" | "C" | "D" | "F" => {
    if (avg <= 3) return "S";
    if (avg <= 6) return "A";
    if (avg <= 9) return "B";
    if (avg <= 12) return "C";
    if (avg <= 16) return "D";
    return "F";
  };
  const tierColor = (t: string) =>
    t === "S" ? "bg-destructive/20 text-destructive border-destructive/40"
    : t === "A" ? "bg-primary/20 text-primary border-primary/40"
    : t === "B" ? "bg-emerald-500/25 text-emerald-300 border-emerald-500/50"
    : t === "C" ? "bg-amber-500/15 text-amber-400 border-amber-500/30"
    : t === "D" ? "bg-muted text-foreground/80 border-border"
    : "bg-surface-2 text-muted-foreground border-border";

  const tourStatus = (t: { startDate: string; endDate: string }) => {
    const s = new Date(t.startDate + "T00:00:00Z").getTime();
    const e = new Date(t.endDate + "T23:59:59Z").getTime();
    if (today < s) return { label: "FUTURE", cls: "bg-success/20 text-success border-success/40" };
    if (today > e) return { label: "PAST", cls: "bg-surface-2 text-muted-foreground border-border" };
    return { label: "LIVE", cls: "bg-destructive/20 text-destructive border-destructive/40 animate-pulse" };
  };

  // ---- Form dynamics: per-match placement / kills / top5 over time --------
  type FormPoint = { matchId: string; name: string; date: Date | null; placement: number; kills: number; top5: number };
  const formPoints: FormPoint[] = useMemo(() => {
    return filteredRows
      .filter((r) => r.date)
      .sort((a, b) => a.date!.getTime() - b.date!.getTime())
      .slice(-30)
      .map((r) => {
        const ids = r.match.mapIds ?? [r.match.mapId];
        const placements = ids.map((id) => pseudoPlacement(id, r.match.id));
        const avgPlace = placements.reduce((s, v) => s + v, 0) / placements.length;
        const kills = pseudoKills(r.match.id);
        const top5 = avgPlace <= 5 ? 1 : 0;
        return { matchId: r.match.id, name: r.match.name, date: r.date, placement: avgPlace, kills, top5 };
      });
  }, [filteredRows]);

  // Rolling top-5 rate over last 5 games for a smoother series.
  const top5Rate = useMemo(() => {
    const win = 5;
    return formPoints.map((_, i) => {
      const slice = formPoints.slice(Math.max(0, i - win + 1), i + 1);
      const hits = slice.reduce((s, p) => s + p.top5, 0);
      return (hits / slice.length) * 100;
    });
  }, [formPoints]);

  // ---- Latest match for "Open in Match Viewer" ----------------------------
  const latestMatch = useMemo(() => {
    const past = teamRows
      .filter((r) => r.date && r.date.getTime() <= today)
      .sort((a, b) => b.date!.getTime() - a.date!.getTime())[0];
    return past?.match ?? teamRows[0]?.match;
  }, [teamRows]);

  // ---- Tournaments the team is NOT in (Add to tournament action) ----------
  const missingTournaments = useMemo(() => {
    const inIds = new Set(teamTournaments.map((t) => t.id));
    return tournaments.filter((t) => !inIds.has(t.id));
  }, [tournaments, teamTournaments]);

  function addToTournament(tournamentId: string) {
    matches
      .filter((m) => m.tournamentId === tournamentId)
      .forEach((m) => {
        if (!m.teamIds?.includes(team!.id)) {
          updateMatch(m.id, { teamIds: [...(m.teamIds ?? []), team!.id] });
        }
      });
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-surface px-6">
        <button onClick={() => navigate({ to: "/admin/teams" })} className="text-mono rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted">← Teams</button>
        <TeamLogo team={team} size={28} />
        <h1 className="text-sm font-bold uppercase tracking-wider">{team.tag} · {team.name}</h1>
        {detail?.currentSeason && (
          <span
            className="ml-2 inline-flex items-center gap-1.5 rounded-sm border border-primary/40 bg-primary/10 px-2 py-1 text-xs uppercase tracking-wider text-primary"
            title="Current season standings"
          >
            <span className="label-eyebrow text-xs opacity-70">Season</span>
            <span className="font-semibold normal-case">{detail.currentSeason.seasonName ?? "Current"}</span>
            <span className="text-mono">·</span>
            <span className="text-mono">
              {detail.currentSeason.totalPoints != null ? `${detail.currentSeason.totalPoints} pts` : "— pts"}
            </span>
          </span>
        )}
        <a
          href={
            team.liquipediaUrl ||
            `https://liquipedia.net/apexlegends/index.php?search=${encodeURIComponent(team.name)}`
          }
          target="_blank"
          rel="noreferrer"
          title={team.liquipediaUrl ? "Open Liquipedia page" : "Search team on Liquipedia"}
          className="ml-2 inline-flex items-center gap-1 rounded-sm border border-primary/40 bg-primary/10 px-2 py-1 text-xs uppercase tracking-wider text-primary hover:bg-primary/20"
        >
          Liquipedia
        </a>
      </header>
      <div className="flex-1 overflow-auto p-6">
        {/* ---- Sticky filters + view switcher ---- */}
        <div className="sticky top-0 z-30 -mx-6 -mt-6 mb-4 border-b border-border bg-background/95 px-6 pt-6 pb-3 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="hud-panel p-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="label-eyebrow text-xs">Period</div>
            <button
              onClick={() => setMode("all")}
              className={`rounded-sm border px-2 py-1 text-xs uppercase tracking-wider ${mode === "all" ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-surface hover:bg-muted"}`}
            >
              All time
            </button>
            <Popover>
              <PopoverTrigger asChild>
                <button
                  className={`rounded-sm border px-2 py-1 text-xs uppercase tracking-wider ${mode === "year" ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-surface hover:bg-muted"}`}
                >
                  By year{mode === "year" ? ` · ${year}` : ""} ▾
                </button>
              </PopoverTrigger>
              <PopoverContent align="start" className="w-40 p-1">
                {allYears.map((y) => (
                  <button
                    key={y}
                    onClick={() => { setMode("year"); setYear(y); }}
                    className={`block w-full rounded-sm px-2 py-1.5 text-left text-xs hover:bg-muted ${mode === "year" && year === y ? "bg-primary/10 text-primary" : ""}`}
                  >
                    Year {y}
                  </button>
                ))}
              </PopoverContent>
            </Popover>
            <Popover>
              <PopoverTrigger asChild>
                <button
                  className={`rounded-sm border px-2 py-1 text-xs uppercase tracking-wider ${mode === "tournaments" ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-surface hover:bg-muted"}`}
                >
                  By tournaments{mode === "tournaments" && selectedTours.length ? ` · ${selectedTours.length}` : ""} ▾
                </button>
              </PopoverTrigger>
              <PopoverContent align="start" className="max-h-80 w-72 overflow-auto p-1">
                <div className="flex items-center justify-between px-2 py-1 text-xs text-muted-foreground">
                  <span>{selectedTours.length} selected</span>
                  <button onClick={() => setSelectedTours([])} className="hover:text-foreground">Clear</button>
                </div>
                {teamTournaments.map((t) => {
                  const on = selectedTours.includes(t.id);
                  return (
                    <button
                      key={t.id}
                      onClick={() => {
                        setMode("tournaments");
                        setSelectedTours(on ? selectedTours.filter((x) => x !== t.id) : [...selectedTours, t.id]);
                      }}
                      className={`flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-xs hover:bg-muted ${on ? "bg-primary/10 text-primary" : ""}`}
                    >
                      <span className={`inline-block h-3 w-3 shrink-0 rounded-sm border ${on ? "border-primary bg-primary" : "border-border"}`} />
                      <span className="truncate">{t.name}</span>
                    </button>
                  );
                })}
                {teamTournaments.length === 0 && (
                  <div className="px-2 py-3 text-center text-xs text-muted-foreground">No tournaments</div>
                )}
              </PopoverContent>
            </Popover>
            <div className="flex items-center gap-1 border-l border-border pl-3 ml-1">
              {(Object.keys(RANGE_LABEL) as RangeKey[]).map((k) => (
                <button
                  key={k}
                  onClick={() => { setMode("range"); setRange(k); }}
                  className={`rounded-sm border px-2 py-1 text-xs uppercase tracking-wider ${mode === "range" && range === k ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-surface hover:bg-muted"}`}
                >
                  {RANGE_LABEL[k]}
                </button>
              ))}
            </div>
            <span className="text-xs text-muted-foreground">{filteredRows.length} matches</span>
            <div className="ml-auto flex items-center gap-1.5">
              <button
                onClick={() => setEditing({ ...team })}
                className="rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted"
              >
                Edit team
              </button>
              <Popover>
                <PopoverTrigger asChild>
                  <button className="rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted">
                    Add to tournament ▾
                  </button>
                </PopoverTrigger>
                <PopoverContent align="end" className="max-h-80 w-72 overflow-auto p-1">
                  {missingTournaments.length === 0 ? (
                    <div className="px-2 py-3 text-center text-xs text-muted-foreground">In every tournament</div>
                  ) : (
                    missingTournaments.map((t) => (
                      <button
                        key={t.id}
                        onClick={() => addToTournament(t.id)}
                        className="block w-full rounded-sm px-2 py-1.5 text-left text-xs hover:bg-muted"
                      >
                        <div className="truncate font-semibold">{t.name}</div>
                        <div className="text-mono text-xs text-muted-foreground">{t.startDate} → {t.endDate}</div>
                      </button>
                    ))
                  )}
                </PopoverContent>
              </Popover>
            </div>
          </div>
        </div>

        {/* ---- View switcher ---- */}
        <div className="mt-3 flex items-center gap-1.5">
          {([
            ["overview", "Overview"],
            ["maps", "Maps"],
            ["weapons", "Weapons"],
          ] as const).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setView(k)}
              className={`rounded-sm border px-3 py-1.5 text-xs uppercase tracking-wider ${view === k ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-surface hover:bg-muted"}`}
            >
              {label}
            </button>
          ))}
        </div>
        </div>

        {/* ---- Active roster + Next matches side-by-side ---- */}
        <div className="mt-4 grid gap-4 md:grid-cols-[auto_minmax(280px,1fr)] items-start">
          <RosterPanel
            roster={detail?.activeRoster ?? []}
            players={detail?.players ?? []}
            lastMatchPlayerIds={detail?.lastMatchPlayerIds ?? []}
            lastMatchAt={detail?.lastMatchAt ?? null}
            isLoading={detailQuery.isLoading}
          />
          <Panel title={`Next matches (${nextRows.length})`}>
            {nextRows.length === 0 ? <Empty /> : (
              <ScrollList>
                {nextRows.map((r) => (
                  <li key={r.match.id}>
                    <Link
                      to={"/admin/matches/$matchId" as "/admin/matches"}
                      params={{ matchId: r.match.id } as never}
                      className="block rounded-sm border border-border bg-surface px-2 py-1.5 text-xs hover:bg-muted"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate font-semibold">{r.match.name}</span>
                        <span className="text-mono text-xs text-muted-foreground whitespace-nowrap">
                          {fmtDate(r.date)} · {fmtTime(r.date)}
                        </span>
                      </div>
                      <div className="truncate text-xs text-muted-foreground">{r.tour?.name}</div>
                    </Link>
                  </li>
                ))}
              </ScrollList>
            )}
          </Panel>
        </div>

        {view === "overview" && (
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <Panel title={`Tournaments (${filteredTournaments.length})`}>
            {filteredTournaments.length === 0 ? <Empty /> : (
              <ScrollList>
                {[...filteredTournaments].sort((a, b) => (b.startDate ?? "").localeCompare(a.startDate ?? "")).map((t) => {
                  const st = tourStatus(t);
                  return (
                    <li key={t.id}>
                      <Link
                        to="/admin/tournaments"
                        className="block rounded-sm border border-border bg-surface px-2 py-1.5 text-xs hover:bg-muted"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate font-semibold">{t.name}</span>
                          <span className={`rounded-sm border px-1.5 py-[1px] text-xs font-bold uppercase tracking-wider ${st.cls}`}>{st.label}</span>
                        </div>
                        <div className="text-mono text-xs text-muted-foreground">
                          {t.startDate} → {t.endDate} · {t.region}
                        </div>
                      </Link>
                    </li>
                  );
                })}
              </ScrollList>
            )}
          </Panel>

          <Panel title={`Matches (${filteredRows.length})`}>
            {filteredRows.length === 0 ? <Empty /> : (
              <ScrollList>
                {[...filteredRows].sort((a, b) => (b.date?.getTime() ?? 0) - (a.date?.getTime() ?? 0)).map((r) => {
                  const map = allMaps.find((mp) => mp.id === r.match.mapId);
                  const ids = r.match.mapIds ?? [r.match.mapId];
                  const places = ids.map((id) => pseudoPlacement(id, r.match.id));
                  const avgPlace = places.reduce((s, v) => s + v, 0) / places.length;
                  const kills = pseudoKills(r.match.id);
                  return (
                    <li key={r.match.id}>
                      <Link
                        to={"/admin/matches/$matchId" as "/admin/matches"}
                        params={{ matchId: r.match.id } as never}
                        className="block rounded-sm border border-border bg-surface px-2 py-1.5 text-xs hover:bg-muted"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate font-semibold">{r.match.name}</span>
                          <span className="flex items-center gap-2 whitespace-nowrap text-xs">
                            <span className="text-mono rounded-sm border border-primary/40 bg-primary/10 px-1.5 py-[1px] font-semibold text-primary">#{avgPlace.toFixed(0)}</span>
                            <span className="text-mono rounded-sm border border-warning/40 bg-warning/10 px-1.5 py-[1px] font-semibold text-warning">{kills} K</span>
                            <span className="text-muted-foreground">{map?.name}</span>
                          </span>
                        </div>
                        <div className="text-mono text-xs text-muted-foreground">{fmtDate(r.date)} · {fmtTime(r.date)}</div>
                      </Link>
                    </li>
                  );
                })}
              </ScrollList>
            )}
          </Panel>
        </div>
        )}

        {view === "overview" && (
        <>
        {/* ---- Form dynamics ---- */}
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <Sparkline
            title="Placement over time"
            hint={["lower is better", "inverted axis"]}
            values={formPoints.map((p) => p.placement)}
            dates={formPoints.map((p) => p.date)}
            invert
            min={1}
            max={20}
            color="var(--primary)"
            formatVal={(v) => `#${v.toFixed(1)}`}
          />
          <Sparkline
            title="Kills over time"
            hint={["per match", "season aggregate"]}
            values={formPoints.map((p) => p.kills)}
            dates={formPoints.map((p) => p.date)}
            min={0}
            color="var(--primary)"
            formatVal={(v) => `${v.toFixed(0)}`}
          />
          <Sparkline
            title="Top 5 rate"
            hint={["rolling 5-game", "window · %"]}
            values={top5Rate}
            dates={formPoints.map((p) => p.date)}
            min={0}
            max={100}
            color="rgb(16 185 129)"
            formatVal={(v) => `${v.toFixed(0)}%`}
          />
        </div>
        </>
        )}

        {view === "maps" && (
        <div className="hud-panel mt-4 p-3">
          <div className="label-eyebrow mb-3 text-xs">Map tier list · avg placement, top 1 & top 5</div>
          {mapStats.length === 0 ? <Empty /> : (
            <div className="space-y-2">
              {(["S", "A", "B", "C", "D", "F"] as const).map((row) => {
                const items = mapStats.filter((s) => tierOf(s.avg) === row);
                if (items.length === 0) return null;
                return (
                  <div key={row} className="flex items-stretch gap-2">
                    <div className={`flex w-14 shrink-0 items-center justify-center rounded-sm border text-2xl font-bold ${tierColor(row)}`}>{row}</div>
                    <div className="flex flex-1 flex-wrap gap-2 rounded-sm border border-border bg-surface p-2">
                      {items.map((s) => {
                        const map = allMaps.find((mp) => mp.id === s.id);
                        return (
                          <Link
                            key={s.id}
                            to={"/admin/maps/$mapId" as "/admin/maps"}
                            params={{ mapId: s.id } as never}
                            search={{ team: team.id } as never}
                            className="flex w-[320px] items-center gap-3 rounded-sm border border-border bg-background p-2 hover:bg-muted"
                          >
                            {map && <img src={map.image} alt={map.name} className="h-20 w-32 rounded-sm object-cover" />}
                            <div className="min-w-0 flex-1">
                              <div className="truncate text-base font-semibold">{map?.name ?? s.id}</div>
                              <div className="text-mono text-xs text-muted-foreground">{s.count} games · avg <span className="text-foreground font-semibold">#{s.avg.toFixed(1)}</span></div>
                              <div className="mt-1.5 flex items-stretch gap-1.5 text-xs">
                                <div className="flex flex-1 flex-col items-center rounded-sm border border-warning/40 bg-warning/10 px-1.5 py-1 text-warning" title="Победы">
                                  <span className="label-eyebrow text-xs leading-none">TOP 1</span>
                                  <span className="text-mono text-sm font-bold leading-tight">{s.top1}</span>
                                </div>
                                <div className="flex flex-1 flex-col items-center rounded-sm border border-primary/40 bg-primary/10 px-1.5 py-1 text-primary" title="Топ-5 финиши">
                                  <span className="label-eyebrow text-xs leading-none">TOP 5</span>
                                  <span className="text-mono text-sm font-bold leading-tight">{s.top5}</span>
                                </div>
                              </div>
                            </div>
                          </Link>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
        )}

        {view === "maps" && (
        <div className="mt-4">
          <PoiMapPanel picks={detail?.poiPicks ?? []} />
        </div>
        )}

        {view === "weapons" && (
          <WeaponTierPanel weapons={detail?.weapons ?? []} isLoading={detailQuery.isLoading} />
        )}
      </div>
      {/* ---- TEST INTERFACE blocks moved inside main scroll area below ---- */}
      {editing && (
        <EditTeamModal
          row={editing}
          onChange={setEditing}
          onCancel={() => setEditing(null)}
          onSave={() => { updateTeam(editing.id, editing); setEditing(null); }}
        />
      )}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="hud-panel p-3">
      <div className="label-eyebrow mb-2 text-xs">{title}</div>
      {children}
    </div>
  );
}
function ScrollList({ children }: { children: React.ReactNode }) {
  return <ul className="max-h-[320px] space-y-1 overflow-y-auto pr-1">{children}</ul>;
}
function Empty() {
  return <div className="rounded-sm border border-dashed border-border px-2 py-4 text-center text-xs text-muted-foreground">No data</div>;
}

function TestPanel({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="rounded-sm border border-warning/30 bg-surface p-3">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <div className="label-eyebrow text-xs">{title}</div>
        {subtitle && <div className="text-xs text-muted-foreground">{subtitle}</div>}
      </div>
      <div className="max-h-[320px] overflow-y-auto">{children}</div>
    </div>
  );
}

/** Convert ALGS canonical id ("storm_point") → UI map id ("storm-point"). */
function canonicalToUiMapId(canonical: string | null): string | null {
  return canonical ? canonical.replace(/_/g, "-") : null;
}

function PoiMapPanel({ picks }: { picks: TeamPoiPick[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  // Group picks by map
  const byMap = useMemo(() => {
    const m = new Map<string, { mapName: string; mapImage: string | null; picks: TeamPoiPick[]; total: number }>();
    for (const p of picks) {
      const uiId = canonicalToUiMapId(p.mapCanonicalId) ?? "unknown";
      const map = allMaps.find((mp) => mp.id === uiId);
      const cur = m.get(uiId) ?? {
        mapName: map?.name ?? p.mapName ?? uiId,
        mapImage: map?.image ?? null,
        picks: [],
        total: 0,
      };
      cur.picks.push(p);
      cur.total += p.count;
      m.set(uiId, cur);
    }
    return Array.from(m.entries())
      .map(([id, v]) => ({ id, ...v, picks: v.picks.slice().sort((a, b) => b.count - a.count) }))
      .sort((a, b) => b.total - a.total);
  }, [picks]);

  if (picks.length === 0) {
    return (
      <div className="hud-panel p-3">
        <div className="label-eyebrow mb-2 text-xs">POI picks on map</div>
        <Empty />
      </div>
    );
  }

  return (
    <>
      <div className="hud-panel p-3">
        <div className="mb-2 flex items-baseline justify-between gap-2">
          <div className="label-eyebrow text-xs">POI picks on map</div>
          <div className="text-xs text-muted-foreground">{byMap.length} maps · {picks.length} unique POIs · click to expand</div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {byMap.map((m) => (
            <button
              key={m.id}
              onClick={() => setExpanded(m.id)}
              className="group rounded-sm border border-border bg-surface text-left hover:border-primary/40"
            >
              <PoiMapImage mapImage={m.mapImage} picks={m.picks} />
              <div className="flex items-center justify-between gap-2 px-2 py-1.5">
                <span className="truncate text-xs font-semibold">{m.mapName}</span>
                <span className="text-mono text-xs text-muted-foreground">{m.picks.length} POIs · {m.total} picks</span>
              </div>
            </button>
          ))}
        </div>
      </div>
      {expanded && (() => {
        const m = byMap.find((x) => x.id === expanded);
        if (!m) return null;
        const maxCount = Math.max(1, ...m.picks.map((p) => p.count));
        return (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
            onClick={() => setExpanded(null)}
          >
            <div
              className="relative max-h-[92vh] w-full max-w-5xl overflow-hidden rounded-sm border border-border bg-surface shadow-xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between border-b border-border px-4 py-2">
                <div className="text-sm font-bold uppercase tracking-wider">{m.mapName} · POI picks</div>
                <button
                  onClick={() => setExpanded(null)}
                  className="rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted"
                >
                  Close
                </button>
              </div>
              <div className="grid gap-4 p-4 md:grid-cols-[1fr_280px]">
                <PoiMapImage mapImage={m.mapImage} picks={m.picks} large />
                <div className="max-h-[70vh] overflow-y-auto">
                  <ul className="space-y-1">
                    {m.picks.map((p) => (
                      <li key={p.spawnLocationId} className="flex items-center justify-between rounded-sm border border-border bg-surface px-2 py-1.5 text-xs">
                        <div className="min-w-0">
                          <div className="truncate font-semibold">{p.spawnName}</div>
                          <div className="text-mono text-xs text-muted-foreground">avg pick #{p.avgPickNumber.toFixed(1)}</div>
                        </div>
                        <div className="text-mono">
                          <div className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
                            <div className="h-full bg-primary" style={{ width: `${(p.count / maxCount) * 100}%` }} />
                          </div>
                          <div className="text-right">{p.count}×</div>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          </div>
        );
      })()}
    </>
  );
}

function PoiMapImage({ mapImage, picks, large }: { mapImage: string | null; picks: TeamPoiPick[]; large?: boolean }) {
  const maxCount = Math.max(1, ...picks.map((p) => p.count));
  return (
    <div className={`relative w-full overflow-hidden ${large ? "" : "rounded-t-sm"} bg-surface-2`} style={{ aspectRatio: "1 / 1" }}>
      {mapImage ? (
        <img src={mapImage} alt="" className="absolute inset-0 h-full w-full object-cover" />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">no map image</div>
      )}
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full">
        {picks.map((p) => {
          if (p.xNorm == null || p.yNorm == null) return null;
          const r = 1 + (p.count / maxCount) * (large ? 3 : 2);
          return (
            <g key={p.spawnLocationId}>
              <circle cx={p.xNorm * 100} cy={p.yNorm * 100} r={r} fill="var(--primary)" fillOpacity={0.7} stroke="white" strokeWidth={0.3} />
              {large && (
                <text x={p.xNorm * 100 + r + 0.5} y={p.yNorm * 100 + 0.5} fill="white" fontSize={1.6} style={{ paintOrder: "stroke" }} stroke="black" strokeWidth={0.3}>
                  {p.spawnName} ({p.count})
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Sparkline — minimal inline SVG line chart                                  */
/* -------------------------------------------------------------------------- */

/* -------------------------------------------------------------------------- */
/* Active roster — photo-card grid                                            */
/* -------------------------------------------------------------------------- */
function RosterPanel({
  roster,
  players,
  lastMatchPlayerIds,
  lastMatchAt,
  isLoading,
}: {
  roster: TeamRosterMember[];
  players: TeamPlayer[];
  lastMatchPlayerIds: string[];
  lastMatchAt: string | null;
  isLoading: boolean;
}) {
  const lastSet = new Set(lastMatchPlayerIds);
  const lastDate = lastMatchAt ? new Date(lastMatchAt).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "2-digit" }) : null;
  return (
    <div className="hud-panel p-3">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <div className="label-eyebrow text-xs">Active roster · {roster.length} players</div>
        <div className="text-xs text-muted-foreground">
          latest event team version · role=player{lastDate ? ` · last match ${lastDate}` : ""}
          {isLoading && " · loading…"}
        </div>
      </div>
      {roster.length === 0 ? <Empty /> : (
        <div className="flex flex-wrap gap-3">
          {roster.map((p) => {
            const agg = players.find((x) => x.id === p.id);
            const playedLast = lastSet.has(p.id);
            return (
              <div key={p.id} className="w-[225px] overflow-hidden rounded-sm border border-border bg-surface">
                <div className="relative h-[300px] w-full bg-surface-2">
                  {p.image ? (
                    <img src={p.image} alt={p.name} className="absolute inset-0 h-full w-full object-cover" />
                  ) : (
                    <div className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">no photo</div>
                  )}
                  {playedLast && (
                    <div className="absolute left-1.5 top-1.5 rounded-sm border border-primary/60 bg-primary/90 px-1.5 py-0.5 text-xs font-bold uppercase tracking-wider text-primary-foreground" title={lastDate ? `Играл в последнем матче (${lastDate})` : "Играл в последнем матче"}>
                      Last match
                    </div>
                  )}
                  <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent px-2 py-1.5">
                    <div className="truncate text-sm font-bold uppercase tracking-wider text-white">{p.name}</div>
                  </div>
                </div>
                <div className="grid grid-cols-2 divide-x divide-border border-t border-border text-center">
                  <Stat label="Matches" value={agg?.matchesPlayed ?? 0} />
                  <Stat label="Kills" value={agg?.kills ?? 0} />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, title }: { label: string; value: number; title?: string }) {
  return (
    <div className="px-2 py-1.5" title={title}>
      <div className="text-mono text-sm font-bold">{value}</div>
      <div className="label-eyebrow text-xs text-muted-foreground">{label}</div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Weapon tier list — kill-share grouped, all weapons                         */
/* -------------------------------------------------------------------------- */
const AMMO_COLOR: Record<string, string> = {
  light: "bg-amber-500/20 text-amber-300 border-amber-500/40",
  heavy: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
  energy: "bg-red-500/20 text-red-300 border-red-500/40",
  shotgun: "bg-rose-500/20 text-rose-300 border-rose-500/40",
  sniper: "bg-sky-500/20 text-sky-300 border-sky-500/40",
  arrow: "bg-violet-500/20 text-violet-300 border-violet-500/40",
  grenade: "bg-orange-500/20 text-orange-300 border-orange-500/40",
  "mythic-light": "bg-amber-500/30 text-amber-200 border-amber-400/60",
  "mythic-sniper": "bg-sky-500/30 text-sky-200 border-sky-400/60",
  "mythic-arrow": "bg-violet-500/30 text-violet-200 border-violet-400/60",
};

/** ALGS weapon name → apexlegends.wiki.gg image filename (svg). */
const WEAPON_ICON: Record<string, string> = {
  "P2020": "P2020_Icon.svg",
  "RE-45": "RE-45_Auto_Icon.svg",
  "RE-45 Auto": "RE-45_Auto_Icon.svg",
  "Wingman": "Wingman_Icon.svg",
  "Mozambique": "Mozambique_Shotgun_Icon.svg",
  "EVA-8": "EVA-8_Auto_Icon.svg",
  "Peacekeeper": "Peacekeeper_Icon.svg",
  "Mastiff": "Mastiff_Shotgun_Icon.svg",
  "R-99": "R-99_SMG_Icon.svg",
  "Alternator": "Alternator_SMG_Icon.svg",
  "Volt": "Volt_SMG_Icon.svg",
  "Prowler": "Prowler_Burst_PDW_Icon.svg",
  "C.A.R. SMG": "C.A.R._SMG_Icon.svg",
  "C.A.R.": "C.A.R._SMG_Icon.svg",
  "R-301 Carbine": "R-301_Carbine_Icon.svg",
  "R-301": "R-301_Carbine_Icon.svg",
  "Flatline": "VK-47_Flatline_Icon.svg",
  "VK-47 Flatline": "VK-47_Flatline_Icon.svg",
  "Hemlok": "Hemlok_Burst_AR_Icon.svg",
  "Havoc": "HAVOC_Rifle_Icon.svg",
  "HAVOC": "HAVOC_Rifle_Icon.svg",
  "Nemesis": "Nemesis_Burst_AR_Icon.svg",
  "Devotion": "Devotion_LMG_Icon.svg",
  "L-STAR": "L-STAR_EMG_Icon.svg",
  "Spitfire": "M600_Spitfire_Icon.svg",
  "M600 Spitfire": "M600_Spitfire_Icon.svg",
  "Rampage": "Rampage_LMG_Icon.svg",
  "G7 Scout": "G7_Scout_Icon.svg",
  "30-30": "30-30_Repeater_Icon.svg",
  "30-30 Repeater": "30-30_Repeater_Icon.svg",
  "Triple Take": "Triple_Take_Icon.svg",
  "Bocek": "Bocek_Compound_Bow_Icon.svg",
  "Longbow": "Longbow_DMR_Icon.svg",
  "Charge Rifle": "Charge_Rifle_Icon.svg",
  "Sentinel": "Sentinel_ESR_Icon.svg",
  "Kraber": "Kraber_.50-Cal_Sniper_Icon.svg",
  "Frag Grenade": "Frag_Grenade_White.svg",
  "Arc Star": "Arc_Star_white.svg",
  "Thermite Grenade": "Thermite_Grenade_white.svg",
  "A-13 Sentry": "A-13_Sentry_Icon.svg",
  "EPG-1": "EPG-1_Launcher_Icon.svg",
};
function weaponIconUrl(name: string): string | null {
  const file = WEAPON_ICON[name];
  return file ? `https://apexlegends.wiki.gg/images/${file}` : null;
}

function WeaponTierPanel({ weapons, isLoading }: { weapons: TeamWeaponStat[]; isLoading: boolean }) {
  const max = Math.max(1, ...weapons.map((w) => w.kills));
  const tierOf = (kills: number): "S" | "A" | "B" | "C" | "D" | "F" => {
    const share = kills / max;
    if (share >= 0.6) return "S";
    if (share >= 0.35) return "A";
    if (share >= 0.18) return "B";
    if (share >= 0.08) return "C";
    if (share >= 0.03) return "D";
    return "F";
  };
  const tierColor = (t: string) =>
    t === "S" ? "bg-destructive/20 text-destructive border-destructive/40"
    : t === "A" ? "bg-primary/20 text-primary border-primary/40"
    : t === "B" ? "bg-emerald-500/25 text-emerald-300 border-emerald-500/50"
    : t === "C" ? "bg-amber-500/15 text-amber-400 border-amber-500/30"
    : t === "D" ? "bg-muted text-foreground/80 border-border"
    : "bg-surface-2 text-muted-foreground border-border";

  return (
    <div className="hud-panel mt-4 p-3">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <div className="label-eyebrow text-xs">Weapon meta · {weapons.length} weapons</div>
        <div className="text-xs text-muted-foreground">
          tier by team kill share · series-aggregated
          {isLoading && " · loading…"}
        </div>
      </div>
      {weapons.length === 0 ? <Empty /> : (
        <div className="space-y-2">
          {(["S", "A", "B", "C", "D", "F"] as const).map((row) => {
            const items = weapons.filter((w) => tierOf(w.kills) === row);
            if (items.length === 0) return null;
            return (
              <div key={row} className="flex items-stretch gap-2">
                <div className={`flex w-14 shrink-0 items-center justify-center rounded-sm border text-2xl font-bold ${tierColor(row)}`}>{row}</div>
                <div className="flex flex-1 flex-wrap gap-2 rounded-sm border border-border bg-surface p-2">
                  {items.map((w) => {
                    const ammoCls = (w.ammoType && AMMO_COLOR[w.ammoType]) || "bg-muted text-muted-foreground border-border";
                    const icon = weaponIconUrl(w.weapon);
                    return (
                      <div
                        key={w.weapon}
                        className={`flex w-[240px] flex-col gap-1.5 rounded-sm border p-2 ${ammoCls}`}
                        title={`${w.weapon} · ${w.gunType ?? "—"} · ${w.ammoType ?? "—"} · ${w.kills} kills in ${w.series} series`}
                      >
                        <div className="flex h-14 w-full items-center justify-center px-2">
                          {icon ? (
                            <img
                              src={icon}
                              alt={w.weapon}
                              loading="lazy"
                              className="h-full max-h-14 w-auto max-w-full object-contain [filter:brightness(0)_invert(1)]"
                            />
                          ) : (
                            <span className="text-xs uppercase tracking-wider opacity-70">no icon</span>
                          )}
                        </div>
                        <div className="truncate text-sm font-semibold">{w.weapon}</div>
                        <div className="flex items-center justify-between text-xs opacity-90">
                          <span className="text-mono font-semibold">{w.kills} kills</span>
                          <span className="text-mono">{w.series} series</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Sparkline — minimal inline SVG line chart                                  */
/* -------------------------------------------------------------------------- */
function Sparkline({
  title,
  hint,
  values,
  dates,
  min,
  max,
  invert,
  color,
  formatVal,
}: {
  title: string;
  hint?: string[];
  values: number[];
  dates?: (Date | null)[];
  min?: number;
  max?: number;
  invert?: boolean;
  color: string;
  formatVal: (v: number) => string;
}) {
  const w = 300;
  const h = 100;
  const padX = 0;
  const padTop = 12;
  const padBottom = 16;
  const lo = min ?? Math.min(...values, 0);
  const hi = max ?? Math.max(...values, 1);
  const span = Math.max(1e-6, hi - lo);
  const innerH = h - padTop - padBottom;

  const last = values[values.length - 1];
  const first = values[0];
  const delta = values.length >= 2 ? last - first : 0;
  const goodDir = invert ? delta < 0 : delta > 0;
  const deltaUp = invert ? delta <= 0 : delta >= 0;

  const yFor = (v: number) => {
    const norm = (v - lo) / span;
    return invert ? padTop + norm * innerH : padTop + innerH - norm * innerH;
  };
  const points = values.map((v, i) => {
    const x = padX + (i / Math.max(1, values.length - 1)) * (w - padX * 2);
    return [x, yFor(v)] as const;
  });
  const path = points.length
    ? points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ")
    : "";

  // Stats
  const minV = values.length ? Math.min(...values) : 0;
  const maxV = values.length ? Math.max(...values) : 0;
  const avgV = values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
  const bestV = invert ? minV : maxV;
  const worstV = invert ? maxV : minV;
  const yAvg = values.length ? yFor(avgV) : 0;

  // Peak (best) index
  let peakIdx = -1;
  if (values.length >= 1) {
    let best = values[0];
    peakIdx = 0;
    for (let i = 1; i < values.length; i++) {
      if (invert ? values[i] < best : values[i] > best) { best = values[i]; peakIdx = i; }
    }
  }

  const fmtShort = (d: Date | null | undefined) =>
    d ? d.toLocaleDateString(undefined, { day: "2-digit", month: "short" }) : "";
  const labelStep = Math.max(1, Math.ceil(values.length / 8));

  return (
    <div className="flex h-[260px] flex-col rounded-sm border border-border bg-surface-2/40">
      <div className="flex shrink-0 items-start justify-between gap-2 p-3 pb-2">
        <div>
          <div className="label-eyebrow text-xs tracking-wider">{title}</div>
          {values.length > 0 && (
            <div className="flex items-baseline gap-2">
              <span className="text-mono text-xl font-bold text-foreground">{formatVal(last)}</span>
              {values.length >= 2 && (
                <span className={`text-mono text-xs font-medium ${goodDir ? "text-emerald-400" : "text-destructive"}`}>
                  {deltaUp ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}
                </span>
              )}
            </div>
          )}
        </div>
        {hint && (
          <div className="text-right text-[9px] uppercase leading-tight tracking-wider text-muted-foreground">
            {hint.map((line, i) => <div key={i}>{line}</div>)}
          </div>
        )}
      </div>

      <div className="relative min-h-0 flex-1 px-4">
        {values.length === 0 ? (
          <div className="flex h-full items-center justify-center text-xs text-muted-foreground">No data</div>
        ) : (
          <>
            <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-full w-full overflow-visible">
              {/* Gridlines */}
              {[0.2, 0.5, 0.8].map((f) => (
                <line
                  key={f}
                  x1={0}
                  x2={w}
                  y1={padTop + innerH * f}
                  y2={padTop + innerH * f}
                  stroke="currentColor"
                  className="text-foreground"
                  strokeOpacity={0.08}
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                  strokeDasharray="2 4"
                />
              ))}
              {/* Avg ref line */}
              <line x1={0} x2={w} y1={yAvg} y2={yAvg} stroke={color} strokeWidth={1} strokeDasharray="4 4" strokeOpacity={0.35} vectorEffect="non-scaling-stroke" />
              {/* Path */}
              <path d={path} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
              {/* Dots */}
              {points.map(([x, y], i) => {
                const isPeak = i === peakIdx;
                const isLast = i === points.length - 1;
                return (
                  <circle
                    key={i}
                    cx={x}
                    cy={y}
                    r={isPeak || isLast ? 3 : 2}
                    fill={isPeak ? color : "hsl(var(--background))"}
                    stroke={color}
                    strokeWidth={1.5}
                    vectorEffect="non-scaling-stroke"
                  />
                );
              })}
              {/* Per-point value labels (above each point) */}
              {points.map(([x, y], i) => {
                if (i % labelStep !== 0 && i !== points.length - 1 && i !== peakIdx) return null;
                const isPeak = i === peakIdx;
                const anchor = i === 0 ? "start" : i === points.length - 1 ? "end" : "middle";
                return (
                  <text
                    key={`v${i}`}
                    x={x}
                    y={Math.max(7, y - 5)}
                    textAnchor={anchor}
                    fontSize="7"
                    fill="currentColor"
                    className={isPeak ? "text-foreground font-bold" : "text-muted-foreground"}
                  >
                    {formatVal(values[i])}
                  </text>
                );
              })}
              {/* Per-point date labels (below chart) */}
              {points.map(([x], i) => {
                if (i % labelStep !== 0 && i !== points.length - 1) return null;
                const d = dates?.[i];
                if (!d) return null;
                const anchor = i === 0 ? "start" : i === points.length - 1 ? "end" : "middle";
                return (
                  <text
                    key={`d${i}`}
                    x={x}
                    y={h - 4}
                    textAnchor={anchor}
                    fontSize="7"
                    fill="currentColor"
                    className="text-muted-foreground"
                  >
                    {fmtShort(d)}
                  </text>
                );
              })}
            </svg>
          </>
        )}
      </div>

      <div className="grid shrink-0 grid-cols-4 divide-x divide-border border-t border-border bg-background/60 py-2">
        {[
          ["Best", values.length ? formatVal(bestV) : "—"],
          ["Worst", values.length ? formatVal(worstV) : "—"],
          ["Avg", values.length ? formatVal(avgV) : "—"],
          ["Current", values.length ? formatVal(last) : "—"],
        ].map(([label, val]) => (
          <div key={label} className="flex flex-col items-center">
            <span className="text-[9px] uppercase tracking-wider text-muted-foreground">{label}</span>
            <span className="text-mono text-xs font-semibold text-foreground">{val}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Inline team color swatch (Assign color)                                    */
/* -------------------------------------------------------------------------- */
function ColorSwatch({ team }: { team: Team }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <div className="relative flex items-center">
      <button
        onClick={() => ref.current?.click()}
        title="Assign team color"
        className="flex items-center gap-1.5 rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted"
      >
        <span className="inline-block h-3.5 w-3.5 rounded-sm border border-border" style={{ background: team.color }} />
        Color
      </button>
      <input
        ref={ref}
        type="color"
        value={team.color}
        onChange={(e) => updateTeam(team.id, { color: e.target.value })}
        className="absolute inset-0 h-0 w-0 opacity-0"
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Compact Edit Team modal — Tag / Name / Color / Status / Logo / Liquipedia  */
/* -------------------------------------------------------------------------- */
function EditTeamModal({
  row, onChange, onCancel, onSave,
}: {
  row: Team;
  onChange: (r: Team) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const set = <K extends keyof Team>(k: K, v: Team[K]) => onChange({ ...row, [k]: v });
  const base = "mt-1 w-full rounded-sm border border-border bg-background px-2 py-1.5 text-sm";
  const status = row.status ?? "active";
  const logoInput = useRef<HTMLInputElement>(null);
  const onLogoFile = (file?: File) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => set("logo", String(reader.result));
    reader.readAsDataURL(file);
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onCancel}>
      <div className="hud-panel w-full max-w-lg bg-surface" onClick={(e) => e.stopPropagation()}>
        <div className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-bold uppercase tracking-wider">Edit team</h2>
        </div>
        <div className="max-h-[70vh] space-y-4 overflow-y-auto p-4">
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="label-eyebrow text-xs">Tag</label>
              <input className={base} value={row.tag} onChange={(e) => set("tag", e.target.value.toUpperCase())} />
            </div>
            <div className="col-span-2">
              <label className="label-eyebrow text-xs">Name</label>
              <input className={base} value={row.name} onChange={(e) => set("name", e.target.value)} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label-eyebrow text-xs">Color</label>
              <div className="mt-1 flex items-center gap-2">
                <input
                  type="color"
                  value={row.color}
                  onChange={(e) => set("color", e.target.value)}
                  className="h-8 w-12 cursor-pointer rounded-sm border border-border bg-background"
                />
                <input className={base + " text-mono"} value={row.color} onChange={(e) => set("color", e.target.value)} />
              </div>
            </div>
            <div>
              <label className="label-eyebrow text-xs">Status</label>
              <div className="mt-1 flex items-center gap-1 rounded-sm border border-border bg-background p-0.5 w-fit">
                {(["active", "archived"] as const).map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => set("status", s)}
                    className={
                      "rounded-sm px-3 py-1 text-xs font-semibold uppercase tracking-wider " +
                      (status === s
                        ? s === "active"
                          ? "bg-primary/15 text-primary"
                          : "border border-border bg-muted text-foreground"
                        : "bg-surface text-muted-foreground hover:text-foreground")
                    }
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div>
            <label className="label-eyebrow text-xs">Default logo</label>
            <div className="mt-1 flex items-center gap-3 rounded-sm border border-border bg-surface-2/40 p-3">
              <TeamLogo team={row} size={48} />
              <div className="flex-1">
                <button
                  type="button"
                  onClick={() => logoInput.current?.click()}
                  className="rounded-sm border border-border bg-surface px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted"
                >
                  Upload logo
                </button>
                <input
                  ref={logoInput}
                  type="file"
                  accept="image/*"
                  className="hidden"
                  onChange={(e) => onLogoFile(e.target.files?.[0])}
                />
                {row.logo && (
                  <button
                    type="button"
                    onClick={() => set("logo", "")}
                    className="ml-2 text-xs text-muted-foreground hover:text-destructive"
                  >
                    Remove
                  </button>
                )}
              </div>
            </div>
          </div>
          <div>
            <label className="label-eyebrow text-xs">Liquipedia page</label>
            <input
              className={base + " text-mono text-xs"}
              placeholder="https://liquipedia.net/apexlegends/Team_Name"
              value={row.liquipediaUrl ?? ""}
              onChange={(e) => set("liquipediaUrl", e.target.value || undefined)}
            />
          </div>
        </div>
        <div className="flex justify-end gap-2 border-t border-border bg-surface-2 px-4 py-3">
          <button onClick={onCancel} className="rounded-sm border border-border bg-surface px-3 py-1.5 text-xs uppercase tracking-wider hover:bg-muted">Cancel</button>
          <button onClick={onSave} className="rounded-sm bg-primary px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110">Save</button>
        </div>
      </div>
    </div>
  );
}
