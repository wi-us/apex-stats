import { createFileRoute } from "@tanstack/react-router";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  useAdminStore,
  addProcess,
  updateProcess,
  removeProcess,
  addMatch,
  type AnalysisProcess,
  type ProcessPov,
  type MapTiming,
  type MapAnalysis,
  type ProcessKind,
} from "@/lib/admin-store";
import { maps as allMaps, type Team, type MatchFull } from "@/lib/mock-match";
import { Progress } from "@/components/ui/progress";
import { TeamLogo } from "@/components/admin/TeamLogo";

export const Route = createFileRoute("/admin/processes")({
  component: ProcessesAdmin,
  validateSearch: (s: Record<string, unknown>) => ({
    matchId: typeof s.matchId === "string" ? s.matchId : undefined,
  }),
});

const STATUS_COLORS: Record<AnalysisProcess["status"], string> = {
  draft: "bg-muted text-foreground/80",
  queued: "bg-primary/20 text-primary",
  running: "bg-warning/20 text-warning",
  done: "bg-success/20 text-success",
  failed: "bg-destructive/20 text-destructive",
};

const KIND_LABELS: Record<ProcessKind, string> = {
  minimap: "Minimap tracking",
  camera: "Camera tracking",
  full: "Full match analysis",
  hsv: "HSV validation",
  ring: "Ring detection",
  debug_export: "Debug export",
};
const KIND_OPTIONS: ProcessKind[] = ["minimap", "camera", "full", "hsv", "ring", "debug_export"];
const PRESET_OPTIONS = ["Default", "Step zoom", "Smooth observer", "Fast camera", "Low noise"];

/** Required analyses per match — used to compute "missing" for Suggested. */
const REQUIRED_KINDS: ProcessKind[] = ["minimap", "camera", "full"];
const KIND_SHORT: Record<ProcessKind, string> = {
  minimap: "minimap", camera: "camera", full: "trajectory", hsv: "hsv", ring: "ring", debug_export: "debug",
};

/** Derived progress + ETA for a process based on map analyses. */
function deriveProgress(p: AnalysisProcess): { pct: number; etaSec: number | null; framesDone: number; framesTotal: number } {
  const ma = p.mapAnalyses ?? [];
  const totalSec = (p.maps ?? []).reduce((s, m) => s + Math.max(0, m.endSec - m.startSec), 0);
  const fps = p.frameStep && p.frameStep > 0 ? Math.max(1, Math.round(30 / p.frameStep)) : 15;
  const framesTotal = Math.max(60, Math.round(totalSec * fps));
  if (!ma.length) {
    if (p.status === "done") return { pct: 100, etaSec: 0, framesDone: framesTotal, framesTotal };
    return { pct: 0, etaSec: null, framesDone: 0, framesTotal };
  }
  const avg = ma.reduce((s, a) => s + (a.ring + a.start + a.camera) / 3, 0) / ma.length;
  const pct = Math.max(0, Math.min(100, Math.round(avg)));
  const framesDone = Math.round((framesTotal * pct) / 100);
  let etaSec: number | null = null;
  if (p.status === "running" && p.startedAt && pct > 1 && pct < 100) {
    const elapsed = (Date.now() - p.startedAt) / 1000;
    const remaining = elapsed * ((100 - pct) / pct);
    etaSec = Math.max(1, Math.round(remaining));
  }
  if (p.status === "done") etaSec = 0;
  return { pct, etaSec, framesDone, framesTotal };
}

function relTime(ts?: number): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}
function durationLabel(start?: number, end?: number): string {
  if (!start) return "—";
  const e = end ?? Date.now();
  const s = Math.max(0, Math.round((e - start) / 1000));
  return mmss(s);
}

const mmss = (s: number) =>
  `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;

function FilterChip({
  label, count, active, onClick, tone,
}: {
  label: string; count: number; active: boolean; onClick: () => void;
  tone: "muted" | "primary" | "warning" | "success" | "destructive";
}) {
  const toneCls =
    tone === "primary" ? "border-primary/40 text-primary"
    : tone === "warning" ? "border-warning/40 text-warning"
    : tone === "success" ? "border-success/40 text-success"
    : tone === "destructive" ? "border-destructive/40 text-destructive"
    : "border-border text-foreground/80";
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded-sm border px-2 py-1 text-xs font-semibold uppercase tracking-wider transition-colors ${toneCls} ${
        active ? "bg-surface-2 brightness-125" : "bg-surface hover:bg-surface-2"
      }`}
    >
      <span>{label}</span>
      <span className="text-mono tabular-nums opacity-70">{count}</span>
    </button>
  );
}

const hhmmss = (s: number) => {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${h.toString().padStart(2, "0")}:${m.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
};
const parseHMS = (str: string): number | null => {
  const m = str.trim().match(/^(?:(\d+):)?(\d{1,2}):(\d{2})$/);
  if (!m) return null;
  const h = +(m[1] ?? 0), mm = +m[2], ss = +m[3];
  return h * 3600 + mm * 60 + ss;
};

/** Parse a typical ALGS-style stream title + description block. */
function parseAlgsTitle(text: string): {
  region?: string;
  tournamentName?: string;
  day?: string;
  matchup?: string;
  timings?: { label: string; sec: number }[];
} {
  const out: ReturnType<typeof parseAlgsTitle> = {};
  const region = text.match(/Region:\s*([^\n]+)/i)?.[1]?.trim();
  const tour = text.match(/Tournament:\s*([^\n]+)/i)?.[1]?.trim();
  const day = text.match(/Day:\s*([^\n]+)/i)?.[1]?.trim();
  const matchup = text.match(/Matchup:\s*([^\n]+)/i)?.[1]?.trim();
  if (region) out.region = region;
  if (tour) out.tournamentName = tour;
  if (day) out.day = day;
  if (matchup) out.matchup = matchup;

  // Fallback: parse from "ALGS Map POV - Americas - Split 1 - Americas Day 6 (Group B vs C) - May 3, 2026"
  if (!region || !tour) {
    const parts = text.split(/[-–]/).map((s) => s.trim());
    if (parts.length >= 4) {
      out.region ??= parts[1];
      out.tournamentName ??= `${parts[2]} - ${parts[1]}`;
      const dm = parts[3]?.match(/Day\s*(\d+)/i);
      if (dm) out.day ??= dm[1];
      const mm = text.match(/\(([^)]+)\)/);
      if (mm) out.matchup ??= mm[1];
    }
  }

  const timings: { label: string; sec: number }[] = [];
  const re = /(\d{1,2}:\d{2}:\d{2})\s*[-–]\s*([^\n]+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const sec = parseHMS(m[1]);
    if (sec != null) timings.push({ label: m[2].trim(), sec });
  }
  if (timings.length) out.timings = timings;
  return out;
}

/** Mock metadata fetch — derives info from URL deterministically. */
function fetchVideoMeta(url: string): {
  title: string;
  channel: string;
  durationSec: number;
  tournamentHint?: string;
  matchHint?: string;
  maps?: MapTiming[];
  rawDescription?: string;
  region?: string;
  day?: string;
  matchup?: string;
} | null {
  if (!/^https?:\/\//i.test(url)) return null;
  const lower = url.toLowerCase();
  const tournamentHint = lower.includes("algs")
    ? "algs-2026-split-1"
    : lower.includes("esl")
      ? "esl-pro-league-12"
      : "scrims-eu-week-4";
  // (matches are days, not games — matchHint below uses Day)
  const mockDescription = `ALGS Map POV - Americas - Split 1 - Americas Day 6 (Group B vs C) - May 3, 2026

Region: Americas
Tournament: Split 1 - Americas
Day: 6
Matchup: Group B vs C

Timestamps:
00:00:00 - Pregame
00:06:57 - Game 1
00:34:17 - Game 2
01:08:30 - Game 3
01:46:45 - Game 4
02:13:04 - Game 5
02:41:27 - Game 6`;
  const parsed = parseAlgsTitle(mockDescription);
  // Only Game 1..n become map timings — exclude Pregame, breaks, intros.
  const games = (parsed.timings ?? []).filter((t) => /^\s*game\s*\d+/i.test(t.label));
  const mapsParsed: MapTiming[] = games.map((g, i) => {
    const next = games[i + 1];
    const end = next ? next.sec : g.sec + 1500;
    // mapId left empty — operator chooses which map this game was on.
    return { mapId: "", startSec: g.sec, endSec: end };
  });
  return {
    title: mockDescription.split("\n")[0],
    channel: lower.includes("twitch") ? "Twitch · Official" : "YouTube · Caster",
    durationSec: 10800,
    tournamentHint,
    matchHint: parsed.day ? `Day ${parsed.day}` : undefined,
    maps: mapsParsed.length ? mapsParsed : undefined,
    rawDescription: mockDescription,
    region: parsed.region,
    day: parsed.day,
    matchup: parsed.matchup,
  };
}

function ProcessesAdmin() {
  const { processes, matches, tournaments, teams } = useAdminStore();
  const [editing, setEditing] = useState<AnalysisProcess | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  type FilterKey = "all" | "suggested" | "queued" | "running" | "done" | "failed" | "needs_review" | "draft";
  const [statusFilter, setStatusFilter] = useState<FilterKey>("all");
  const search = Route.useSearch();
  const handledMatchRef = useRef<string | null>(null);

  const statusCounts = useMemo(() => {
    const c: Record<AnalysisProcess["status"], number> = {
      draft: 0, queued: 0, running: 0, done: 0, failed: 0,
    };
    for (const p of processes) c[p.status]++;
    return c;
  }, [processes]);

  // Suggestions: matches whose tournament endDate is in the past and required analyses are missing.
  const suggestions = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    return matches
      .map((m) => {
        const t = tournaments.find((x) => x.id === m.tournamentId);
        if (!t || t.endDate >= today) return null;
        const done = new Set(
          processes.filter((p) => p.matchId === m.id && p.status === "done").map((p) => p.kind ?? "minimap"),
        );
        const missing = REQUIRED_KINDS.filter((k) => !done.has(k));
        if (missing.length === 0) return null;
        return { match: m, tournament: t, missing };
      })
      .filter(Boolean) as { match: typeof matches[number]; tournament: typeof tournaments[number]; missing: ProcessKind[] }[];
  }, [processes, matches, tournaments]);

  const needsReviewCount = useMemo(
    () => processes.filter((p) => p.needsReview || (p.status === "done" && (p.qualityScore ?? 100) < 60)).length,
    [processes],
  );

  const visibleProcesses = useMemo(() => {
    if (statusFilter === "all") return processes;
    if (statusFilter === "suggested") return [];
    if (statusFilter === "needs_review") {
      return processes.filter((p) => p.needsReview || (p.status === "done" && (p.qualityScore ?? 100) < 60));
    }
    return processes.filter((p) => p.status === statusFilter);
  }, [processes, statusFilter]);

  const selected = processes.find((p) => p.id === selectedId) ?? null;

  const draft = (preset?: Partial<AnalysisProcess>) => {
    const tId = preset?.tournamentId ?? tournaments[0]?.id ?? "";
    const mId = preset?.matchId ?? matches.find((m) => m.tournamentId === tId)?.id ?? matches[0]?.id ?? "";
    setEditing({
      id: `p-${Date.now()}`,
      pov: "map",
      kind: "minimap",
      live: false,
      streamUrl: "",
      tournamentId: tId,
      matchId: mId,
      teamId: undefined,
      mapCount: 0,
      maps: [],
      status: "draft",
      createdAt: Date.now(),
      preset: "Default",
      frameStep: 2,
      debugMode: false,
      ...preset,
    });
  };

  const duplicate = editing
    ? processes.some((p) => p.matchId === editing.matchId && p.id !== editing.id && p.pov === editing.pov)
    : false;

  useEffect(() => {
    const mid = search.matchId;
    if (!mid || handledMatchRef.current === mid) return;
    const match = matches.find((m) => m.id === mid);
    if (!match) return;
    handledMatchRef.current = mid;
    const existing = processes.find((p) => p.matchId === mid);
    if (existing) {
      setExpanded(existing.id);
      setStatusFilter("all");
      requestAnimationFrame(() => {
        document.getElementById(`process-${existing.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    } else {
      draft({ tournamentId: match.tournamentId, matchId: mid });
    }
  }, [search.matchId, processes, matches]);

  const save = (run: boolean) => {
    if (!editing) return;
    const exists = processes.some((p) => p.id === editing.id);
    const teamIds = matches.find((m) => m.id === editing.matchId)?.teamIds ?? [];
    const mapAnalyses: MapAnalysis[] | undefined = run
      ? editing.maps.map((_, mi) => ({
          mapIndex: mi,
          ring: 0,
          start: 0,
          camera: 0,
          teams: teamIds.slice(0, 20).map((tid) => ({ teamId: tid, progress: 0 })),
        }))
      : editing.mapAnalyses;
    const next: AnalysisProcess = {
      ...editing,
      status: run ? "queued" : editing.status,
      mapAnalyses,
      startedAt: run ? Date.now() : editing.startedAt,
    };
    if (exists) updateProcess(editing.id, next);
    else addProcess(next);
    if (run) {
      setTimeout(() => updateProcess(next.id, { status: "running", startedAt: Date.now() }), 600);
      // Each task is independent: separate random multipliers per map per task.
      const rng = (seed: number) => {
        let s = seed >>> 0;
        return () => {
          s = (s * 1664525 + 1013904223) >>> 0;
          return s / 0xffffffff;
        };
      };
      const tick = (pct: number) => updateProcess(next.id, {
        mapAnalyses: (mapAnalyses ?? []).map((ma) => {
          const r = rng(ma.mapIndex * 7 + 1);
          return {
            ...ma,
            ring: Math.min(100, Math.round(pct * (0.5 + r() * 0.7))),
            start: Math.min(100, Math.round(pct * (0.5 + r() * 0.7))),
            camera: Math.min(100, Math.round(pct * (0.5 + r() * 0.7))),
            teams: ma.teams.map((tp, ti) => {
              const rt = rng(ma.mapIndex * 31 + ti * 13 + 5);
              return { ...tp, progress: Math.min(100, Math.round(pct * (0.4 + rt() * 0.9))) };
            }),
          };
        }),
      });
      [15, 30, 50, 70, 90, 100].forEach((p, i) => setTimeout(() => tick(p), 800 + i * 600));
      setTimeout(() => {
        // synthesize a quality score and flag for review when low
        const q = 55 + Math.round(Math.random() * 40);
        updateProcess(next.id, {
          status: "done",
          finishedAt: Date.now(),
          qualityScore: q,
          needsReview: q < 65,
        });
      }, 5000);
    }
    setEditing(null);
  };

  const runDirect = (preset: Partial<AnalysisProcess>) => {
    // Create + immediately enqueue a process (used by Suggested "Analyze" buttons).
    draft(preset);
    requestAnimationFrame(() => {
      // nothing — operator confirms in modal
    });
  };

  const onAction = (p: AnalysisProcess, action: string) => {
    switch (action) {
      case "start":
      case "retry":
      case "rerun":
        updateProcess(p.id, { status: "queued", startedAt: Date.now(), finishedAt: undefined, errorMessage: undefined });
        setTimeout(() => updateProcess(p.id, { status: "running" }), 400);
        setTimeout(() => updateProcess(p.id, { status: "done", finishedAt: Date.now(), qualityScore: 70 + Math.round(Math.random() * 25), needsReview: false }), 3200);
        break;
      case "cancel":
      case "stop":
        updateProcess(p.id, { status: "failed", errorMessage: "Cancelled by operator", finishedAt: Date.now() });
        break;
      case "edit": setEditing({ ...p }); break;
      case "review": updateProcess(p.id, { needsReview: false }); break;
      case "delete":
        if (confirm("Delete process?")) {
          removeProcess(p.id);
          if (selectedId === p.id) setSelectedId(null);
        }
        break;
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface px-6">
        <div className="flex items-center gap-3">
          <h1 className="text-sm font-bold uppercase tracking-wider">Processes</h1>
          <span className="text-xs text-muted-foreground">· operator control center</span>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 overflow-auto p-6 space-y-6">
          {/* FILTERS */}
          <div className="flex flex-wrap items-center gap-1.5">
            <FilterChip label="All" count={processes.length} active={statusFilter === "all"} onClick={() => setStatusFilter("all")} tone="muted" />
            <FilterChip label="Suggested" count={suggestions.length} active={statusFilter === "suggested"} onClick={() => setStatusFilter("suggested")} tone="primary" />
            <FilterChip label="Queued"  count={statusCounts.queued}  active={statusFilter === "queued"}  onClick={() => setStatusFilter("queued")}  tone="primary" />
            <FilterChip label="Running" count={statusCounts.running} active={statusFilter === "running"} onClick={() => setStatusFilter("running")} tone="warning" />
            <FilterChip label="Done"    count={statusCounts.done}    active={statusFilter === "done"}    onClick={() => setStatusFilter("done")}    tone="success" />
            <FilterChip label="Failed"  count={statusCounts.failed}  active={statusFilter === "failed"}  onClick={() => setStatusFilter("failed")}  tone="destructive" />
            <FilterChip label="Needs review" count={needsReviewCount} active={statusFilter === "needs_review"} onClick={() => setStatusFilter("needs_review")} tone="warning" />
            <FilterChip label="Draft"   count={statusCounts.draft}   active={statusFilter === "draft"}   onClick={() => setStatusFilter("draft")}   tone="muted" />
          </div>

          {/* NEEDS ATTENTION */}
          <NeedsAttention
            suggestionsCount={suggestions.length}
            failedCount={statusCounts.failed}
            needsReviewCount={needsReviewCount}
            runningCount={statusCounts.running}
            onJump={(f) => setStatusFilter(f)}
          />

          {/* SUGGESTED with missing list */}
          {(statusFilter === "all" || statusFilter === "suggested") && suggestions.length > 0 && (
            <section className="hud-panel p-4">
              <div className="mb-2 flex items-center justify-between">
                <h2 className="label-eyebrow">Suggested · finished without analysis</h2>
                <span className="text-mono text-xs text-muted-foreground">{suggestions.length}</span>
              </div>
              <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
                {suggestions.slice(0, 9).map(({ match: m, tournament: t, missing }) => (
                  <div key={m.id} className="rounded-sm border border-border bg-surface-2 p-3">
                    <div className="text-xs font-semibold">{m.name}</div>
                    <div className="mb-2 text-xs text-muted-foreground">{t.name}</div>
                    <div className="mb-2 flex flex-wrap gap-1">
                      <span className="text-xs uppercase tracking-wider text-muted-foreground">Missing:</span>
                      {missing.map((k) => (
                        <span key={k} className="rounded-sm border border-warning/40 bg-warning/10 px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wider text-warning">
                          {KIND_SHORT[k]}
                        </span>
                      ))}
                    </div>
                    <button
                      onClick={() => draft({ tournamentId: m.tournamentId, matchId: m.id, kind: missing[0] ?? "minimap" })}
                      className="w-full rounded-sm bg-primary/15 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary hover:bg-primary/25"
                    >
                      Analyze →
                    </button>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* TABLE */}
          {statusFilter !== "suggested" && (
            <section className="hud-panel overflow-hidden">
              <table className="w-full text-sm">
                <thead className="border-b border-border bg-surface-2">
                  <tr className="label-eyebrow text-left text-xs">
                    <th className="px-3 py-2">Type</th>
                    <th className="px-3 py-2">Match</th>
                    <th className="px-3 py-2">Team/POV</th>
                    <th className="px-3 py-2">Map</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Progress</th>
                    <th className="px-3 py-2">Quality</th>
                    <th className="px-3 py-2">Started</th>
                    <th className="px-3 py-2 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleProcesses.length === 0 && (
                    <tr><td colSpan={9} className="px-3 py-10 text-center">
                      <div className="text-sm font-semibold text-foreground">No processes yet</div>
                      <div className="mt-1 text-xs text-muted-foreground">Create a new process or analyze suggested matches.</div>
                      <button onClick={() => draft()} className="mt-3 rounded-sm bg-primary px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110">+ New process</button>
                    </td></tr>
                  )}
                  {visibleProcesses.map((p) => {
                    const m = matches.find((x) => x.id === p.matchId);
                    const t = tournaments.find((x) => x.id === p.tournamentId);
                    const team = p.teamId ? teams.find((x) => x.id === p.teamId) : null;
                    const isOpen = expanded === p.id;
                    const prog = deriveProgress(p);
                    const firstMap = p.maps[0] ? allMaps.find((x) => x.id === p.maps[0].mapId) : null;
                    const isSelected = selectedId === p.id;
                    return (
                      <Fragment key={p.id}>
                        <tr
                          id={`process-${p.id}`}
                          onClick={() => setSelectedId(isSelected ? null : p.id)}
                          className={`cursor-pointer border-b border-border scroll-mt-20 ${isSelected ? "bg-surface-2" : "hover:bg-surface-2/40"}`}
                        >
                          <td className="px-3 py-2 text-xs">
                            <div className="font-semibold">{KIND_LABELS[p.kind ?? "minimap"]}</div>
                            {p.live && <span className="mt-0.5 inline-block rounded-sm bg-destructive px-1.5 py-0.5 text-xs font-bold text-destructive-foreground">LIVE</span>}
                          </td>
                          <td className="px-3 py-2 text-xs">
                            <button onClick={(e) => { e.stopPropagation(); setExpanded(isOpen ? null : p.id); }} className="font-semibold hover:text-primary">
                              {isOpen ? "▼" : "▶"} {m?.name ?? p.matchId}
                            </button>
                            <div className="text-muted-foreground">{t?.name ?? p.tournamentId}</div>
                          </td>
                          <td className="px-3 py-2 text-xs">
                            {p.pov === "team" ? (team?.name ?? team?.tag ?? "—") : <span className="text-muted-foreground">Map POV</span>}
                          </td>
                          <td className="px-3 py-2 text-xs">
                            {firstMap?.name ?? <span className="text-muted-foreground">—</span>}
                            {p.maps.length > 1 && <span className="ml-1 text-mono text-xs text-muted-foreground">+{p.maps.length - 1}</span>}
                          </td>
                          <td className="px-3 py-2"><span className={`rounded-sm px-1.5 py-0.5 text-xs uppercase ${STATUS_COLORS[p.status]}`}>{p.status}</span>
                            {p.needsReview && <span className="ml-1 rounded-sm border border-warning/40 bg-warning/10 px-1 text-xs font-semibold uppercase tracking-wider text-warning">review</span>}
                          </td>
                          <td className="px-3 py-2 w-44">
                            <ProgressMini status={p.status} prog={prog} />
                          </td>
                          <td className="px-3 py-2 text-xs">
                            {p.qualityScore !== undefined ? (
                              <span className={`text-mono font-semibold ${p.qualityScore >= 80 ? "text-success" : p.qualityScore >= 60 ? "text-warning" : "text-destructive"}`}>
                                {p.qualityScore}%
                              </span>
                            ) : <span className="text-muted-foreground">—</span>}
                          </td>
                          <td className="px-3 py-2 text-mono text-xs text-muted-foreground">
                            {p.startedAt ? relTime(p.startedAt) : "—"}
                            {p.startedAt && <div className="text-xs opacity-70">{durationLabel(p.startedAt, p.finishedAt)}</div>}
                          </td>
                          <td className="px-3 py-2 text-right" onClick={(e) => e.stopPropagation()}>
                            <StatusActions process={p} onAction={(a) => onAction(p, a)} />
                          </td>
                        </tr>
                        {isOpen && (
                          <tr className="border-b border-border bg-surface-2/40">
                            <td colSpan={9} className="px-4 py-3">
                              <ProcessAnalysisDetail process={p} teams={teams} matchTeamIds={m?.teamIds ?? []} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </section>
          )}
        </div>

        {/* RIGHT DETAIL PANEL */}
        {selected ? (
          <ProcessDetailPanel
            process={selected}
            match={matches.find((x) => x.id === selected.matchId)}
            tournament={tournaments.find((x) => x.id === selected.tournamentId)}
            team={selected.teamId ? teams.find((x) => x.id === selected.teamId) : null}
            onClose={() => setSelectedId(null)}
            onAction={(a) => onAction(selected, a)}
          />
        ) : (
          <aside className="w-[360px] shrink-0 overflow-auto border-l border-border bg-surface">
            <div className="sticky top-0 z-10 border-b border-border bg-surface px-4 py-3">
              <div className="label-eyebrow text-xs">Create</div>
              <div className="text-xs font-semibold">New process</div>
            </div>
            <div className="space-y-4 p-4">
              <button
                onClick={() => draft()}
                className="w-full rounded-sm bg-primary px-3 py-2.5 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110"
              >
                + New process
              </button>
              <p className="text-xs leading-relaxed text-muted-foreground">
                Configure a new analysis job — pick a process type, source VOD, map and preset, then start or queue it.
              </p>
              <div className="rounded-sm border border-border bg-surface-2 p-3 text-xs">
                <div className="label-eyebrow mb-1">Tip</div>
                <div className="text-muted-foreground">Click any row in the table to open its details here.</div>
              </div>
            </div>
          </aside>
        )}
      </div>

      {editing && (
        <ProcessEditor
          value={editing}
          duplicate={duplicate}
          tournaments={tournaments}
          matches={matches}
          teams={teams}
          onChange={setEditing}
          onClose={() => setEditing(null)}
          onSave={() => save(false)}
          onRun={() => save(true)}
        />
      )}
    </div>
  );
}

function ProcessEditor({
  value, duplicate, tournaments, matches, teams, onChange, onClose, onSave, onRun,
}: {
  value: AnalysisProcess;
  duplicate: boolean;
  tournaments: ReturnType<typeof useAdminStore>["tournaments"];
  matches: ReturnType<typeof useAdminStore>["matches"];
  teams: ReturnType<typeof useAdminStore>["teams"];
  onChange: (p: AnalysisProcess) => void;
  onClose: () => void;
  onSave: () => void;
  onRun: () => void;
}) {
  const set = <K extends keyof AnalysisProcess>(k: K, v: AnalysisProcess[K]) =>
    onChange({ ...value, [k]: v });

  const fetchMeta = () => {
    const meta = fetchVideoMeta(value.streamUrl);
    if (!meta) {
      alert("Could not detect metadata from that URL");
      return;
    }
    const tournamentId = meta.tournamentHint && tournaments.some((t) => t.id === meta.tournamentHint)
      ? meta.tournamentHint
      : value.tournamentId;
    const matchByHint = meta.matchHint
      ? matches.find((m) => m.tournamentId === tournamentId && m.name.toLowerCase().includes(meta.matchHint!.toLowerCase()))
      : undefined;
    const maps = meta.maps ?? value.maps;
    onChange({
      ...value,
      videoTitle: meta.title,
      videoChannel: meta.channel,
      videoDurationSec: meta.durationSec,
      region: meta.region ?? value.region,
      day: meta.day ?? value.day,
      matchup: meta.matchup ?? value.matchup,
      tournamentId,
      matchId: matchByHint?.id ?? value.matchId,
      maps,
      mapCount: maps.length || value.mapCount,
    });
  };

  const matchOptions = matches.filter((m) => m.tournamentId === value.tournamentId);
  const createNewMatch = () => {
    const name = prompt("New Match (Day) name", value.day ? `Day ${value.day}${value.matchup ? ` — ${value.matchup}` : ""}` : "New Day");
    if (!name) return;
    const id = `m-${Date.now()}`;
    const newMatch: MatchFull = {
      id,
      name,
      tournamentId: value.tournamentId,
      mapId: "",
      durationSec: value.videoDurationSec ?? 0,
      mapIds: value.maps.map((mp) => mp.mapId).filter(Boolean),
      vodLink: value.streamUrl,
      teamIds: teams.map((t) => t.id),
      teamVods: {},
    };
    addMatch(newMatch);
    onChange({ ...value, matchId: id });
  };
  const povBtn = (pov: ProcessPov, label: string) => (
    <button
      onClick={() => set("pov", pov)}
      className={`flex-1 rounded-sm border px-3 py-2 text-xs font-semibold uppercase tracking-wider ${
        value.pov === pov ? "border-primary bg-primary/15 text-primary" : "border-border bg-background hover:bg-surface-2"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div className="hud-panel-strong w-full max-w-2xl max-h-[90vh] overflow-auto bg-surface p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-bold uppercase tracking-wider">Analysis process</h3>
          <button onClick={onClose} className="text-xs text-muted-foreground hover:text-foreground">✕</button>
        </div>

        <div className="space-y-4">
          <div>
            <div className="label-eyebrow mb-1.5 text-xs">Process type</div>
            <div className="grid grid-cols-3 gap-1.5">
              {KIND_OPTIONS.map((k) => (
                <button key={k} onClick={() => set("kind", k)}
                  className={`rounded-sm border px-2 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors ${
                    (value.kind ?? "minimap") === k ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-background hover:bg-surface-2"
                  }`}>
                  {KIND_LABELS[k]}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="label-eyebrow mb-1.5 text-xs">Point of view</div>
            <div className="flex gap-2">
              {povBtn("map", "Map POV")}
              {povBtn("team", "Team POV")}
            </div>
            <label className="mt-2 flex items-center gap-2 text-xs">
              <input type="checkbox" checked={value.live} onChange={(e) => set("live", e.target.checked)} />
              <span>LIVE — track stream in realtime</span>
              {value.live && <span className="rounded-sm bg-destructive px-1.5 py-0.5 text-xs font-bold text-destructive-foreground">LIVE</span>}
            </label>
          </div>

          <div>
            <div className="label-eyebrow mb-1.5 text-xs">Stream URL</div>
            <div className="flex gap-2">
              <input
                value={value.streamUrl}
                onChange={(e) => set("streamUrl", e.target.value)}
                onBlur={(e) => { if (e.target.value && !value.videoTitle) fetchMeta(); }}
                placeholder="https://twitch.tv/... or https://youtube.com/watch?v=..."
                className="flex-1 rounded-sm border border-border bg-background px-2 py-1.5 text-xs"
              />
              <button onClick={fetchMeta} className="rounded-sm border border-border bg-surface-2 px-3 py-1.5 text-xs hover:bg-muted">Fetch meta</button>
            </div>
            {value.videoTitle && (
              <div className="mt-1.5 rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs text-muted-foreground">
                <div className="text-foreground">{value.videoTitle}</div>
                <div>{value.videoChannel} · {value.videoDurationSec ? mmss(value.videoDurationSec) : "—"}</div>
                {(value.region || value.day || value.matchup) && (
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                    {value.region && <span><span className="text-foreground/60">Region:</span> {value.region}</span>}
                    {value.day && <span><span className="text-foreground/60">Day:</span> {value.day}</span>}
                    {value.matchup && <span><span className="text-foreground/60">Matchup:</span> {value.matchup}</span>}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="label-eyebrow mb-1.5 text-xs">Tournament</div>
              <select value={value.tournamentId} onChange={(e) => set("tournamentId", e.target.value)} className="w-full rounded-sm border border-border bg-background px-2 py-1.5 text-xs">
                {tournaments.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </div>
            <div>
              <div className="label-eyebrow mb-1.5 text-xs">Match (Day)</div>
              <div className="flex gap-1.5">
                <select
                  value={value.matchId}
                  onChange={(e) => {
                    if (e.target.value === "__new__") { createNewMatch(); return; }
                    set("matchId", e.target.value);
                  }}
                  className="flex-1 rounded-sm border border-border bg-background px-2 py-1.5 text-xs"
                >
                  {matchOptions.length === 0 && <option value="">— No matches —</option>}
                  {matchOptions.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                  <option value="__new__">+ New Match (Day)…</option>
                </select>
                <button
                  type="button"
                  onClick={createNewMatch}
                  className="rounded-sm border border-border bg-surface-2 px-2 text-xs hover:bg-muted"
                  title="Create a new Match (Day) in this tournament"
                >+ New</button>
              </div>
            </div>
          </div>

          {value.pov === "team" && (
            <div>
              <div className="label-eyebrow mb-1.5 text-xs">Team</div>
              <select value={value.teamId ?? ""} onChange={(e) => set("teamId", e.target.value || undefined)} className="w-full rounded-sm border border-border bg-background px-2 py-1.5 text-xs">
                <option value="">— Select team —</option>
                {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </div>
          )}

          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <div className="label-eyebrow text-xs">Map timings</div>
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  Games count
                  <input
                    type="number"
                    min={0}
                    max={20}
                    value={value.mapCount ?? value.maps.length}
                    onChange={(e) => {
                      const n = Math.max(0, Math.min(20, +e.target.value || 0));
                      const next: MapTiming[] = Array.from({ length: n }, (_, i) =>
                        value.maps[i] ?? { mapId: "", startSec: 0, endSec: 1200 },
                      );
                      onChange({ ...value, mapCount: n, maps: next });
                    }}
                    className="w-14 rounded-sm border border-border bg-background px-1.5 py-0.5 text-xs text-mono"
                  />
                </label>
                <button
                  onClick={() => onChange({ ...value, maps: [...value.maps, { mapId: "", startSec: 0, endSec: 1200 }], mapCount: (value.mapCount ?? value.maps.length) + 1 })}
                  className="text-xs text-primary hover:underline"
                >+ Add</button>
              </div>
            </div>
            <div className="space-y-1.5">
              {value.maps.length === 0 && <div className="text-xs text-muted-foreground">No maps configured. Set maps count, fetch metadata, or add manually.</div>}
              {value.maps.map((mp, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <span className="w-6 text-xs text-muted-foreground text-mono">#{i + 1}</span>
                  <select
                    value={mp.mapId}
                    onChange={(e) => onChange({ ...value, maps: value.maps.map((x, j) => j === i ? { ...x, mapId: e.target.value } : x) })}
                    className="flex-1 rounded-sm border border-border bg-background px-2 py-1 text-xs"
                  >
                    <option value="">— Unknown map —</option>
                    {allMaps.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                  </select>
                  <input
                    type="text"
                    value={hhmmss(mp.startSec)}
                    onChange={(e) => {
                      const s = parseHMS(e.target.value);
                      if (s != null) onChange({ ...value, maps: value.maps.map((x, j) => j === i ? { ...x, startSec: s } : x) });
                    }}
                    className="w-24 rounded-sm border border-border bg-background px-2 py-1 text-xs text-mono"
                    placeholder="hh:mm:ss"
                  />
                  <span className="text-xs text-muted-foreground">→</span>
                  <input
                    type="text"
                    value={hhmmss(mp.endSec)}
                    onChange={(e) => {
                      const s = parseHMS(e.target.value);
                      if (s != null) onChange({ ...value, maps: value.maps.map((x, j) => j === i ? { ...x, endSec: s } : x) });
                    }}
                    className="w-24 rounded-sm border border-border bg-background px-2 py-1 text-xs text-mono"
                    placeholder="hh:mm:ss"
                  />
                  <button onClick={() => onChange({ ...value, maps: value.maps.filter((_, j) => j !== i), mapCount: Math.max(0, (value.mapCount ?? value.maps.length) - 1) })} className="text-xs text-destructive">✕</button>
                </div>
              ))}
            </div>
          </div>

          {duplicate && (
            <div className="rounded-sm border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
              ⚠ A {value.pov.toUpperCase()} POV process already exists for this match.
            </div>
          )}

          <div className="grid grid-cols-3 gap-3 border-t border-border pt-3">
            <div>
              <div className="label-eyebrow mb-1.5 text-xs">Preset</div>
              <select value={value.preset ?? "Default"} onChange={(e) => set("preset", e.target.value)}
                className="w-full rounded-sm border border-border bg-background px-2 py-1.5 text-xs">
                {PRESET_OPTIONS.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <div className="label-eyebrow mb-1.5 text-xs">Frame step</div>
              <input type="number" min={1} max={30} value={value.frameStep ?? 2}
                onChange={(e) => set("frameStep", Math.max(1, Math.min(30, +e.target.value || 1)))}
                className="w-full rounded-sm border border-border bg-background px-2 py-1.5 text-xs text-mono" />
            </div>
            <label className="flex items-end gap-2 pb-1.5 text-xs">
              <input type="checkbox" checked={value.debugMode ?? false} onChange={(e) => set("debugMode", e.target.checked)} />
              <span>Debug mode</span>
            </label>
          </div>
        </div>

        <div className="mt-5 flex items-center justify-end gap-2">
          <button onClick={onClose} className="rounded-sm border border-border bg-surface-2 px-3 py-1.5 text-xs hover:bg-muted">Cancel</button>
          <button onClick={onSave} className="rounded-sm border border-border bg-background px-3 py-1.5 text-xs hover:bg-surface-2">Save draft</button>
          <button onClick={onRun} disabled={!value.matchId || !value.streamUrl} className="rounded-sm bg-primary px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110 disabled:opacity-40">
            ▶ Run analysis
          </button>
        </div>
      </div>
    </div>
  );
}

function ProcessAnalysisDetail({
  process, teams, matchTeamIds,
}: {
  process: AnalysisProcess;
  teams: Team[];
  matchTeamIds: string[];
}) {
  if (process.maps.length === 0) {
    return <div className="text-xs text-muted-foreground">No maps configured for this process.</div>;
  }
  return (
    <div className="space-y-3">
      {process.maps.map((mp, mi) => {
        const map = allMaps.find((x) => x.id === mp.mapId);
        const analysis: MapAnalysis = process.mapAnalyses?.find((a) => a.mapIndex === mi)
          ?? { mapIndex: mi, ring: 0, start: 0, camera: 0, teams: matchTeamIds.map((tid) => ({ teamId: tid, progress: 0 })) };
        return (
          <div key={mi} className="rounded-sm border border-border bg-background p-3">
            <div className="mb-2 flex items-center justify-between">
              <div className="text-xs font-bold uppercase tracking-wider">
                Game {mi + 1} · {map?.name ?? <span className="text-muted-foreground">Unknown map</span>}
              </div>
              <div className="text-mono text-xs text-muted-foreground">
                {hhmmss(mp.startSec)} → {hhmmss(mp.endSec)}
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <TaskBar label="Ring tracking" value={analysis.ring} />
              <TaskBar label="Start detection" value={analysis.start} />
              <TaskBar label="Camera tracking" value={analysis.camera} />
            </div>
            {analysis.teams.length > 0 && (
              <div className="mt-3 border-t border-border pt-2">
                <div className="label-eyebrow mb-1.5 text-xs">Per-team detection</div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1 md:grid-cols-3 xl:grid-cols-4">
                  {analysis.teams.map((tp) => {
                    const team = teams.find((t) => t.id === tp.teamId);
                    return (
                      <div key={tp.teamId} className="flex items-center gap-2">
                        {team && <TeamLogo team={team} size={16} />}
                        <span className="w-24 truncate text-xs">{team?.name ?? tp.teamId}</span>
                        <ProgressCell value={tp.progress} />
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function TaskBar({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-sm border border-border bg-surface-2 px-2 py-1.5">
      <div className="label-eyebrow mb-1 text-xs">{label}</div>
      <ProgressCell value={value} />
    </div>
  );
}

function ProgressCell({ value }: { value: number }) {
  return (
    <div className="flex items-center gap-2">
      <Progress value={value} className="h-1.5 flex-1" />
      <span className="w-9 text-right text-mono text-xs text-muted-foreground">{value}%</span>
    </div>
  );
}

/* =========================================================================
   New helpers: progress mini, status actions, detail panel, needs attention
   ========================================================================= */

type FilterKey = "all" | "suggested" | "queued" | "running" | "done" | "failed" | "needs_review" | "draft";

function ProgressMini({
  status, prog,
}: {
  status: AnalysisProcess["status"];
  prog: { pct: number; etaSec: number | null; framesDone: number; framesTotal: number };
}) {
  if (status === "draft") return <span className="text-mono text-xs text-muted-foreground">—</span>;
  if (status === "queued") return <span className="text-mono text-xs text-primary">queued…</span>;
  if (status === "failed") return <span className="text-mono text-xs text-destructive">stopped</span>;
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-mono text-xs font-semibold">{prog.pct}%</span>
        <span className="text-mono text-xs text-muted-foreground">
          {prog.framesDone} / {prog.framesTotal}f
        </span>
      </div>
      <Progress value={prog.pct} className="h-1 mt-0.5" />
      {status === "running" && prog.etaSec !== null && (
        <div className="text-mono text-xs text-muted-foreground">ETA {mmss(prog.etaSec)}</div>
      )}
    </div>
  );
}

function StatusActions({ process, onAction }: { process: AnalysisProcess; onAction: (a: string) => void }) {
  const btn = (label: string, action: string, tone: "primary" | "muted" | "destructive" = "muted") => (
    <button
      key={action}
      onClick={() => onAction(action)}
      className={`rounded-sm px-1.5 py-0.5 text-xs hover:underline ${
        tone === "primary" ? "text-primary" : tone === "destructive" ? "text-destructive" : "text-foreground/80"
      }`}
    >{label}</button>
  );
  switch (process.status) {
    case "draft":
      return <div className="flex justify-end gap-1">{btn("Start", "start", "primary")}{btn("Edit", "edit")}{btn("Delete", "delete", "destructive")}</div>;
    case "queued":
      return <div className="flex justify-end gap-1">{btn("Cancel", "cancel", "destructive")}</div>;
    case "running":
      return <div className="flex justify-end gap-1">{btn("Open live", "open", "primary")}{btn("Stop", "stop", "destructive")}</div>;
    case "done":
      if (process.needsReview) {
        return <div className="flex justify-end gap-1">{btn("Open review", "review", "primary")}{btn("Re-run", "rerun")}</div>;
      }
      return <div className="flex justify-end gap-1">{btn("Open", "open", "primary")}{btn("Debug", "debug")}{btn("Re-run", "rerun")}</div>;
    case "failed":
      return <div className="flex justify-end gap-1">{btn("Error", "error", "destructive")}{btn("Retry", "retry", "primary")}{btn("Delete", "delete", "destructive")}</div>;
    default:
      return null;
  }
}

function NeedsAttention({
  suggestionsCount, failedCount, needsReviewCount, runningCount, onJump,
}: {
  suggestionsCount: number; failedCount: number; needsReviewCount: number; runningCount: number;
  onJump: (f: FilterKey) => void;
}) {
  const items: { label: string; count: number; tone: string; filter: FilterKey }[] = [];
  if (suggestionsCount) items.push({ label: `${suggestionsCount} matches without analysis`, count: suggestionsCount, tone: "text-primary", filter: "suggested" });
  if (failedCount) items.push({ label: `${failedCount} failed job${failedCount > 1 ? "s" : ""}`, count: failedCount, tone: "text-destructive", filter: "failed" });
  if (needsReviewCount) items.push({ label: `${needsReviewCount} low-confidence result${needsReviewCount > 1 ? "s" : ""}`, count: needsReviewCount, tone: "text-warning", filter: "needs_review" });
  if (runningCount) items.push({ label: `${runningCount} running`, count: runningCount, tone: "text-warning", filter: "running" });
  if (!items.length) return null;
  return (
    <section className="hud-panel border-l-2 border-l-warning/60 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="label-eyebrow">Needs attention</h2>
        <span className="text-mono text-xs text-muted-foreground">{items.length}</span>
      </div>
      <ul className="grid grid-cols-1 gap-1.5 md:grid-cols-2 xl:grid-cols-4">
        {items.map((it) => (
          <li key={it.filter}>
            <button onClick={() => onJump(it.filter)} className="w-full rounded-sm border border-border bg-surface-2 px-3 py-2 text-left text-xs hover:border-primary/40">
              <span className={`text-mono mr-2 font-bold ${it.tone}`}>● {it.count}</span>
              <span>{it.label}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ProcessDetailPanel({
  process, match, tournament, team, onClose, onAction,
}: {
  process: AnalysisProcess;
  match?: MatchFull;
  tournament?: { id: string; name: string };
  team?: Team | null;
  onClose: () => void;
  onAction: (a: string) => void;
}) {
  const prog = deriveProgress(process);
  const firstMap = process.maps[0] ? allMaps.find((x) => x.id === process.maps[0].mapId) : null;
  return (
    <aside className="w-[360px] shrink-0 overflow-auto border-l border-border bg-surface">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-surface px-4 py-3">
        <div>
          <div className="label-eyebrow text-xs">Process details</div>
          <div className="text-xs font-semibold">{KIND_LABELS[process.kind ?? "minimap"]}</div>
        </div>
        <button onClick={onClose} className="text-xs text-muted-foreground hover:text-foreground">✕</button>
      </div>

      <div className="space-y-3 p-4">
        <DetailSection title="Identification">
          <Kv k="Job ID" v={<span className="text-mono">{process.id}</span>} />
          <Kv k="Type" v={KIND_LABELS[process.kind ?? "minimap"]} />
          <Kv k="Status" v={<span className={`rounded-sm px-1.5 py-0.5 text-xs uppercase ${STATUS_COLORS[process.status]}`}>{process.status}</span>} />
          {process.needsReview && <Kv k="Flag" v={<span className="text-warning">needs review</span>} />}
        </DetailSection>

        <DetailSection title="Context">
          <Kv k="Tournament" v={tournament?.name ?? process.tournamentId} />
          <Kv k="Match" v={match?.name ?? process.matchId} />
          <Kv k="POV" v={process.pov === "team" ? (team?.name ?? "—") : "Map POV"} />
          <Kv k="Map" v={firstMap?.name ?? "—"} />
          {process.live && <Kv k="Live" v={<span className="text-destructive">● LIVE</span>} />}
        </DetailSection>

        <DetailSection title="Source video">
          <div className="text-mono text-[11px] break-all text-muted-foreground">{process.streamUrl || "—"}</div>
          {process.videoTitle && <div className="mt-1 text-xs">{process.videoTitle}</div>}
          {process.videoChannel && <div className="text-xs text-muted-foreground">{process.videoChannel}</div>}
        </DetailSection>

        <DetailSection title="Progress">
          <ProgressMini status={process.status} prog={prog} />
          <div className="mt-2 grid grid-cols-2 gap-y-1 text-xs">
            <span className="text-muted-foreground">Started</span><span className="text-mono">{relTime(process.startedAt)}</span>
            <span className="text-muted-foreground">Finished</span><span className="text-mono">{relTime(process.finishedAt)}</span>
            <span className="text-muted-foreground">Duration</span><span className="text-mono">{durationLabel(process.startedAt, process.finishedAt)}</span>
            <span className="text-muted-foreground">Frames</span><span className="text-mono">{prog.framesTotal}</span>
          </div>
        </DetailSection>

        <DetailSection title="Settings">
          <Kv k="Preset" v={process.preset ?? "Default"} />
          <Kv k="Frame step" v={(process.frameStep ?? 2).toString()} />
          <Kv k="Debug mode" v={process.debugMode ? "on" : "off"} />
        </DetailSection>

        <DetailSection title="Quality">
          {process.qualityScore !== undefined ? (
            <div className={`text-mono text-2xl font-bold ${process.qualityScore >= 80 ? "text-success" : process.qualityScore >= 60 ? "text-warning" : "text-destructive"}`}>
              {process.qualityScore}%
            </div>
          ) : <div className="text-xs text-muted-foreground">No quality score yet.</div>}
        </DetailSection>

        <DetailSection title="Result files">
          <ul className="text-mono space-y-1 text-[11px]">
            {["result.json", "camera_track.json", "trajectory_map.jpg", "debug_video.mp4"].map((f) => (
              <li key={f} className="flex items-center justify-between">
                <span className="text-muted-foreground">/tmp/tracker/{f}</span>
                <button className="text-primary hover:underline">Open</button>
              </li>
            ))}
          </ul>
        </DetailSection>

        {process.errorMessage && (
          <DetailSection title="Error log">
            <pre className="text-mono whitespace-pre-wrap rounded-sm border border-destructive/40 bg-destructive/10 p-2 text-[11px] text-destructive">{process.errorMessage}</pre>
          </DetailSection>
        )}

        <div className="grid grid-cols-2 gap-1.5">
          <button onClick={() => onAction("open")} className="rounded-sm bg-primary px-2 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110">Open result</button>
          <button onClick={() => onAction("debug")} className="rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider hover:bg-muted">Open debug</button>
          <button onClick={() => onAction("download")} className="rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider hover:bg-muted">Download JSON</button>
          <button onClick={() => onAction("rerun")} className="rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider hover:bg-muted">Retry</button>
          <button onClick={() => onAction("delete")} className="col-span-2 rounded-sm border border-destructive/40 bg-surface-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider text-destructive hover:bg-destructive/10">Delete</button>
        </div>
      </div>
    </aside>
  );
}

function DetailSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-sm border border-border bg-surface-2">
      <div className="border-b border-border px-3 py-1.5"><span className="label-eyebrow text-xs">{title}</span></div>
      <div className="space-y-1 p-3 text-xs">{children}</div>
    </div>
  );
}
function Kv({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-muted-foreground">{k}</span>
      <span className="text-right">{v}</span>
    </div>
  );
}