import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { maps as allMaps } from "@/lib/mock-match";
import { useAdminStore, addProcess, type AnalysisProcess } from "@/lib/admin-store";

export const Route = createFileRoute("/admin/minimap")({ component: MinimapAdmin });

const SRC_W = 1920;
const SRC_H = 1080;
const FPS = 30;

type PointStatus = "accepted" | "low_conf" | "relock" | "rejected" | "no_minimap";
type TrackPoint = {
  frame: number;
  t: number;
  x: number;
  y: number;
  status: PointStatus;
  score: number;
  confidence: number;
  jump: number;
  bbox: { x: number; y: number; w: number; h: number };
  window: number;
};

type Tab = "frame" | "video" | "results" | "debug";
type SourceMode = "team_pov" | "observer" | "screenshot" | "upload";
type SearchMode = "window" | "semantic" | "legacy";

const TAB_LABELS: Record<Tab, string> = {
  frame: "FRAME",
  video: "VIDEO",
  results: "RESULTS",
  debug: "DEBUG",
};

const STATUS_COLORS: Record<PointStatus, string> = {
  accepted: "#22c55e",
  low_conf: "#eab308",
  relock: "#a855f7",
  rejected: "#ef4444",
  no_minimap: "#6b7280",
};

const STATUS_LABELS: Record<PointStatus, string> = {
  accepted: "Accepted",
  low_conf: "Low confidence",
  relock: "Relock",
  rejected: "Rejected jump",
  no_minimap: "No minimap",
};

function MinimapAdmin() {
  const { tournaments, matches, teams, zones, processes } = useAdminStore();
  const minimapZone = zones.vod.find((z) => z.tag === "minimap");

  // Tournament → Match → Team (POV) → Map
  const [tournamentId, setTournamentId] = useState(tournaments[0]?.id ?? "");
  const tMatches = useMemo(() => matches.filter((m) => m.tournamentId === tournamentId), [matches, tournamentId]);
  const [matchId, setMatchId] = useState(tMatches[0]?.id ?? "");
  useEffect(() => { setMatchId(tMatches[0]?.id ?? ""); }, [tournamentId]);
  const match = matches.find((m) => m.id === matchId);
  const matchTeams = (match?.teamIds ?? []).map((id) => teams.find((t) => t.id === id)).filter(Boolean) as typeof teams;
  const [teamId, setTeamId] = useState(matchTeams[0]?.id ?? "");
  useEffect(() => { setTeamId(matchTeams[0]?.id ?? ""); }, [matchId]);
  const team = teams.find((t) => t.id === teamId);
  const povUrl = (match?.teamVods?.[teamId]) || "";

  const matchMapIds = (match?.mapIds && match.mapIds.length ? match.mapIds : match ? [match.mapId] : []);
  const [mapId, setMapId] = useState(matchMapIds[0] ?? allMaps[0].id);
  useEffect(() => { setMapId(matchMapIds[0] ?? allMaps[0].id); }, [matchId]);
  const map = allMaps.find((m) => m.id === mapId) ?? allMaps[0];

  // Mode / tab / source
  const [tab, setTab] = useState<Tab>("video");
  const [source, setSource] = useState<SourceMode>("team_pov");
  const [uploadedUrl, setUploadedUrl] = useState<string>("");
  const [uploadedKind, setUploadedKind] = useState<"video" | "image" | null>(null);

  const activeUrl = source === "team_pov" || source === "observer"
    ? povUrl
    : uploadedKind === "video" ? uploadedUrl : "";
  const activeImage = source === "screenshot" && uploadedKind === "image" ? uploadedUrl : "";

  // Analysis settings
  const [searchMode, setSearchMode] = useState<SearchMode>("window");
  const [skipFrames, setSkipFrames] = useState(15);
  const [frameStep, setFrameStep] = useState(1);
  const [minScore, setMinScore] = useState(0.18);
  const [confThreshold, setConfThreshold] = useState(0.45);
  const [maxJump, setMaxJump] = useState(0.08); // normalized
  const [searchRadius, setSearchRadius] = useState(0.25);
  const [globalRelock, setGlobalRelock] = useState(true);
  const [debugMode, setDebugMode] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Playback / track
  const [analyzing, setAnalyzing] = useState(false);
  const [track, setTrack] = useState<TrackPoint[]>([]);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(60);
  const [playing, setPlaying] = useState(false);
  const [selectedFrame, setSelectedFrame] = useState<number | null>(null);
  const [processId, setProcessId] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const lastSampleFrameRef = useRef<number>(-1);
  const lastPointRef = useRef<TrackPoint | null>(null);

  // Linked process status (mocked progress)
  const linkedProcess = processId ? processes.find((p) => p.id === processId) : null;

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onLoaded = () => setDuration(v.duration || 60);
    const onTime = () => {
      setTime(v.currentTime);
      if (!analyzing) return;
      const frame = Math.floor(v.currentTime * FPS);
      if (lastSampleFrameRef.current < 0 || frame - lastSampleFrameRef.current >= skipFrames) {
        lastSampleFrameRef.current = frame;
        const t = v.currentTime;
        const px = clamp01(0.5 + Math.sin(t * 0.35 + 0.7) * 0.32 + Math.sin(t * 1.7) * 0.04);
        const py = clamp01(0.5 + Math.cos(t * 0.28 + 1.3) * 0.28 + Math.cos(t * 1.9) * 0.04);
        const score = 0.15 + Math.random() * 0.3;
        const confidence = 0.3 + Math.random() * 0.6;
        const prev = lastPointRef.current;
        const jump = prev ? Math.hypot(px - prev.x, py - prev.y) : 0;

        let status: PointStatus = "accepted";
        const noise = Math.random();
        if (noise < 0.06) status = "no_minimap";
        else if (jump > maxJump && prev) status = "rejected";
        else if (confidence < confThreshold) status = "low_conf";
        else if (prev && jump > maxJump * 0.6 && globalRelock) status = "relock";

        const pt: TrackPoint = {
          frame, t, x: px, y: py, status, score, confidence, jump,
          bbox: { x: minimapZone?.x ?? 20, y: minimapZone?.y ?? 30, w: minimapZone?.w ?? 320, h: minimapZone?.h ?? 320 },
          window: 320,
        };
        if (status !== "no_minimap" && status !== "rejected") lastPointRef.current = pt;
        setTrack((arr) => [...arr, pt]);
      }
    };
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    v.addEventListener("loadedmetadata", onLoaded);
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    return () => {
      v.removeEventListener("loadedmetadata", onLoaded);
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
    };
  }, [activeUrl, analyzing, skipFrames, maxJump, confThreshold, globalRelock, minimapZone]);

  const togglePlay = () => { const v = videoRef.current; if (!v) return; if (v.paused) v.play(); else v.pause(); };
  const seek = (t: number) => { const v = videoRef.current; if (v) v.currentTime = t; setTime(t); };

  const startAnalysis = () => {
    setTrack([]);
    lastSampleFrameRef.current = -1;
    lastPointRef.current = null;
    setAnalyzing(true);
    const v = videoRef.current;
    if (v && v.paused) v.play();

    // Create linked process
    const id = `proc-${Date.now().toString(36)}`;
    const proc: AnalysisProcess = {
      id,
      pov: source === "team_pov" ? "team" : "map",
      kind: "minimap",
      live: false,
      streamUrl: activeUrl,
      tournamentId,
      matchId,
      teamId: source === "team_pov" ? teamId : undefined,
      maps: [{ mapId, startSec: 0, endSec: duration }],
      status: "running",
      createdAt: Date.now(),
      startedAt: Date.now(),
      preset: searchMode,
      frameStep: skipFrames,
      debugMode,
    };
    addProcess(proc);
    setProcessId(id);
  };
  const stopAnalysis = () => setAnalyzing(false);
  const resetAll = () => { setTrack([]); lastSampleFrameRef.current = -1; lastPointRef.current = null; setSelectedFrame(null); setProcessId(null); };
  const saveResult = () => { /* mock */ alert("Result saved (mock)."); };

  const onUpload = (file: File) => {
    const url = URL.createObjectURL(file);
    const isImg = file.type.startsWith("image/");
    setUploadedUrl(url);
    setUploadedKind(isImg ? "image" : "video");
    setSource(isImg ? "screenshot" : "upload");
    setTab(isImg ? "frame" : "video");
  };

  // Track summary
  const summary = useMemo(() => {
    const total = track.length;
    const accepted = track.filter((p) => p.status === "accepted").length;
    const rejected = track.filter((p) => p.status === "rejected").length;
    const lowConf = track.filter((p) => p.status === "low_conf").length;
    const relock = track.filter((p) => p.status === "relock").length;
    const noMini = track.filter((p) => p.status === "no_minimap").length;
    const valid = track.filter((p) => p.status !== "no_minimap");
    const avgScore = valid.length ? valid.reduce((s, p) => s + p.score, 0) / valid.length : 0;
    const avgConf = valid.length ? valid.reduce((s, p) => s + p.confidence, 0) / valid.length : 0;
    return { total, accepted, rejected, lowConf, relock, noMini, avgScore, avgConf };
  }, [track]);

  const visibleTrack = track.filter((p) => p.status !== "no_minimap");
  const selectedPoint = selectedFrame != null ? track.find((p) => p.frame === selectedFrame) : null;

  const mz = minimapZone;
  const cropAspect = mz ? mz.w / mz.h : 1;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border bg-surface px-6">
        <div className="flex items-center gap-3">
          <h1 className="text-sm font-bold uppercase tracking-wider">Minimap locator</h1>
          <span className="text-mono text-xs text-muted-foreground">·</span>
          <select value={tournamentId} onChange={(e) => setTournamentId(e.target.value)} className="rounded-sm border border-border bg-background px-2 py-1 text-xs">
            {tournaments.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          <select value={matchId} onChange={(e) => setMatchId(e.target.value)} className="rounded-sm border border-border bg-background px-2 py-1 text-xs">
            {tMatches.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
          <select value={teamId} onChange={(e) => setTeamId(e.target.value)} className="rounded-sm border border-border bg-background px-2 py-1 text-xs">
            {matchTeams.map((t) => <option key={t!.id} value={t!.id}>{t!.tag} · {t!.name}</option>)}
          </select>
          <select value={mapId} onChange={(e) => setMapId(e.target.value)} className="rounded-sm border border-border bg-background px-2 py-1 text-xs">
            {(matchMapIds.length ? matchMapIds : allMaps.map((m) => m.id)).map((id) => {
              const m = allMaps.find((x) => x.id === id);
              return <option key={id} value={id}>{m?.name ?? id}</option>;
            })}
          </select>
        </div>
        <div className="flex items-center gap-1">
          {(Object.keys(TAB_LABELS) as Tab[]).map((k) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`rounded-sm px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors ${
                tab === k ? "bg-primary text-primary-foreground" : "border border-border bg-surface hover:bg-muted"
              }`}
            >
              {TAB_LABELS[k]}
            </button>
          ))}
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <div className="grid min-h-0 flex-1 grid-cols-2 gap-3 p-3">
            {/* LEFT: source preview */}
            <div className="hud-panel relative flex min-h-0 flex-col overflow-hidden bg-black">
              <div className="flex items-center justify-between border-b border-border bg-surface px-3 py-1.5">
                <div className="label-eyebrow text-xs">
                  Source minimap{team ? ` · ${team.tag} POV` : ""}{mz ? ` · ${mz.w}×${mz.h} · x${mz.x} y${mz.y}` : " · no zone"}
                </div>
                <div className="text-mono text-xs text-muted-foreground">{fmt(time)} / {fmt(duration)}</div>
              </div>
              <div className="relative flex flex-1 items-center justify-center overflow-hidden bg-black p-2">
                {!activeUrl && !activeImage && (
                  <EmptySource source={source} onUpload={onUpload} />
                )}
                {activeImage && (
                  <img src={activeImage} alt="screenshot" className="max-h-full max-w-full object-contain" />
                )}
                {activeUrl && (
                  <div
                    className="relative overflow-hidden border border-primary/40 bg-black"
                    style={{ aspectRatio: `${cropAspect}`, maxWidth: "100%", maxHeight: "100%", height: "100%", width: "auto" }}
                  >
                    <video
                      ref={videoRef}
                      src={activeUrl}
                      className="absolute top-0 h-full"
                      style={mz ? {
                        width: `${(SRC_W / mz.w) * 100}%`,
                        height: `${(SRC_H / mz.h) * 100}%`,
                        left: `${-(mz.x / mz.w) * 100}%`,
                        top: `${-(mz.y / mz.h) * 100}%`,
                        maxWidth: "none",
                      } : undefined}
                      playsInline
                      preload="metadata"
                      crossOrigin="anonymous"
                    />
                    {analyzing && (
                      <div className="absolute right-1.5 top-1.5 z-10 flex items-center gap-1 rounded-sm bg-destructive/20 px-2 py-0.5 text-xs font-semibold uppercase tracking-wider text-destructive">
                        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-destructive" />
                        REC
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* RIGHT: full map */}
            <div className="hud-panel relative flex min-h-0 flex-col overflow-hidden">
              <div className="flex items-center justify-between border-b border-border bg-surface px-3 py-1.5">
                <div className="label-eyebrow text-xs">Map · {map.name}</div>
                <div className="text-mono text-xs text-muted-foreground">{visibleTrack.length} pt{visibleTrack.length === 1 ? "" : "s"}</div>
              </div>
              <div className="relative flex flex-1 items-center justify-center overflow-hidden bg-background p-2">
                <div className="relative" style={{ aspectRatio: "1 / 1", height: "100%", maxWidth: "100%" }}>
                  <img src={map.image} alt={map.name} className="absolute inset-0 h-full w-full object-contain" draggable={false} />
                  <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" className="absolute inset-0 h-full w-full">
                    {visibleTrack.length >= 2 && (
                      <polyline
                        points={visibleTrack.map((p) => `${p.x * 1000},${p.y * 1000}`).join(" ")}
                        fill="none"
                        stroke={team?.color ?? "#22d3a8"}
                        strokeWidth={1.6}
                        strokeLinejoin="round"
                        strokeLinecap="round"
                        opacity={0.6}
                      />
                    )}
                    {visibleTrack.map((p, i) => {
                      const isLast = i === visibleTrack.length - 1;
                      const isSel = selectedFrame === p.frame;
                      return (
                        <circle
                          key={p.frame}
                          cx={p.x * 1000}
                          cy={p.y * 1000}
                          r={isSel ? 10 : isLast ? 7 : 3.5}
                          fill={STATUS_COLORS[p.status]}
                          stroke={isSel ? "#fff" : "#000"}
                          strokeWidth={isSel ? 2 : 1}
                          opacity={isLast || isSel ? 1 : 0.85}
                          style={{ cursor: "pointer" }}
                          onClick={() => { setSelectedFrame(p.frame); seek(p.t); }}
                        />
                      );
                    })}
                  </svg>
                </div>
                {/* Legend */}
                <div className="absolute bottom-2 left-2 flex flex-wrap gap-1.5 rounded-sm border border-border bg-surface/90 px-2 py-1 text-xs">
                  {(["accepted", "low_conf", "relock", "rejected"] as PointStatus[]).map((s) => (
                    <span key={s} className="flex items-center gap-1">
                      <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: STATUS_COLORS[s] }} />
                      <span className="text-muted-foreground">{STATUS_LABELS[s]}</span>
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* TIMELINE */}
          <div className="shrink-0 border-t border-border bg-surface px-3 py-2">
            <div className="flex items-center gap-3">
              <button onClick={togglePlay} disabled={!activeUrl} className="rounded-sm bg-primary px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110 disabled:opacity-40">
                {playing ? "Pause" : "Play"}
              </button>
              <span className="text-mono text-xs text-muted-foreground">{fmt(time)}</span>
              <div className="relative flex-1">
                <input type="range" min={0} max={duration} step={0.05} value={time}
                  onChange={(e) => seek(Number(e.target.value))} className="w-full accent-primary" />
                {/* status ticks */}
                <div className="pointer-events-none absolute inset-x-0 -top-2 h-2">
                  {track.map((p) => (
                    <span
                      key={p.frame}
                      title={`${fmt(p.t)} · ${STATUS_LABELS[p.status]}`}
                      className="pointer-events-auto absolute top-0 h-2 w-[2px] cursor-pointer"
                      style={{
                        left: `${(p.t / duration) * 100}%`,
                        background: STATUS_COLORS[p.status],
                        opacity: selectedFrame === p.frame ? 1 : 0.8,
                      }}
                      onClick={() => { setSelectedFrame(p.frame); seek(p.t); }}
                    />
                  ))}
                </div>
              </div>
              <span className="text-mono text-xs text-muted-foreground">{fmt(duration)}</span>
            </div>
          </div>

          {/* DEBUG STRIP */}
          {tab === "debug" && (
            <div className="shrink-0 border-t border-border bg-surface-2 p-3">
              <div className="grid grid-cols-5 gap-2">
                {["Frame", "Minimap crop", "Processed", "Match on map", "Top candidates"].map((lbl) => (
                  <div key={lbl} className="hud-panel aspect-square overflow-hidden bg-black">
                    <div className="border-b border-border bg-surface px-2 py-1 label-eyebrow text-xs">{lbl}</div>
                    <div className="flex h-full items-center justify-center text-xs text-muted-foreground">no data</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* RIGHT SIDEBAR */}
        <aside className="w-[340px] shrink-0 overflow-auto border-l border-border bg-surface">
          <div className="border-b border-border px-4 py-3">
            <div className="label-eyebrow text-xs">Configuration</div>
            <div className="text-xs font-semibold">{TAB_LABELS[tab]} mode</div>
          </div>

          <div className="space-y-4 p-4">
            {/* SOURCE */}
            <Section title="Source">
              <div className="space-y-1.5">
                {([
                  ["team_pov", "Team POV VOD"],
                  ["observer", "Observer VOD"],
                  ["screenshot", "Uploaded screenshot"],
                  ["upload", "Uploaded video"],
                ] as [SourceMode, string][]).map(([k, l]) => (
                  <label key={k} className="flex cursor-pointer items-center gap-2 text-xs">
                    <input type="radio" checked={source === k} onChange={() => setSource(k)} />
                    <span>{l}</span>
                  </label>
                ))}
              </div>
              {(source === "screenshot" || source === "upload") && (
                <label className="mt-2 block cursor-pointer rounded-sm border border-dashed border-border bg-surface-2 px-2 py-2 text-center text-xs hover:bg-muted">
                  <input
                    type="file"
                    accept={source === "screenshot" ? "image/*" : "video/*"}
                    className="hidden"
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) onUpload(f); }}
                  />
                  {uploadedKind ? "Replace file" : "Upload file"}
                </label>
              )}
              {source === "team_pov" && !povUrl && (
                <div className="mt-2 space-y-1.5">
                  <div className="text-xs text-muted-foreground">No POV VOD for this team.</div>
                  <div className="flex flex-wrap gap-1">
                    <Link to="/admin/matches/$matchId" params={{ matchId }} className="rounded-sm border border-border bg-surface px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted">+ Add POV VOD</Link>
                    <Link to="/admin/matches" className="rounded-sm border border-border bg-surface px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted">Open match</Link>
                  </div>
                </div>
              )}
            </Section>

            {/* MINIMAP ZONE */}
            <Section title="Minimap zone">
              {mz ? (
                <div className="text-mono space-y-0.5 text-xs">
                  <div>x: {mz.x} · y: {mz.y}</div>
                  <div>w: {mz.w} · h: {mz.h}</div>
                  <Link to="/admin/zones" className="mt-1 inline-block text-xs text-primary hover:underline">Edit in Zones →</Link>
                </div>
              ) : (
                <div className="text-xs text-muted-foreground">
                  No minimap zone. <Link to="/admin/zones" className="text-primary hover:underline">Define one in Zones</Link>.
                </div>
              )}
            </Section>

            {/* SAMPLING */}
            <Section title="Sampling">
              <Slider label="Skip frames" value={skipFrames} min={1} max={120} step={1} onChange={setSkipFrames} hint={`every ${skipFrames}f (~${(skipFrames / FPS).toFixed(2)}s)`} />
              <Slider label="Frame step" value={frameStep} min={1} max={10} step={1} onChange={setFrameStep} />
            </Section>

            {/* MATCHING */}
            <Section title="Matching settings">
              <div className="mb-2">
                <Label>Search mode</Label>
                <div className="mt-1 grid grid-cols-3 gap-1">
                  {(["window", "semantic", "legacy"] as SearchMode[]).map((m) => (
                    <button
                      key={m}
                      onClick={() => setSearchMode(m)}
                      className={`rounded-sm border px-2 py-1 text-xs uppercase tracking-wider ${
                        searchMode === m ? "border-primary bg-primary/15 text-primary" : "border-border bg-surface hover:bg-muted"
                      }`}
                    >
                      {m}
                    </button>
                  ))}
                </div>
              </div>
              <Slider label="Min score" value={minScore} min={0} max={1} step={0.01} onChange={setMinScore} />
              <Slider label="Confidence threshold" value={confThreshold} min={0} max={1} step={0.01} onChange={setConfThreshold} />
            </Section>

            {/* TRACKING RULES */}
            <Section title="Tracking rules">
              <Slider label="Max jump distance" value={maxJump} min={0.01} max={0.5} step={0.01} onChange={setMaxJump} hint={`${(maxJump * 100).toFixed(0)}% of map`} />
              <button onClick={() => setShowAdvanced((v) => !v)} className="mt-1 text-xs text-primary hover:underline">
                {showAdvanced ? "Hide advanced" : "Show advanced"}
              </button>
              {showAdvanced && (
                <div className="mt-2 space-y-3 border-t border-border pt-2">
                  <Slider label="Search radius" value={searchRadius} min={0.05} max={1} step={0.01} onChange={setSearchRadius} />
                  <label className="flex items-center gap-2 text-xs">
                    <input type="checkbox" checked={globalRelock} onChange={(e) => setGlobalRelock(e.target.checked)} />
                    Global relock
                  </label>
                  <label className="flex items-center gap-2 text-xs">
                    <input type="checkbox" checked={debugMode} onChange={(e) => setDebugMode(e.target.checked)} />
                    Debug mode
                  </label>
                </div>
              )}
            </Section>

            {/* TRACK SUMMARY */}
            <Section title={`Track summary · ${summary.total}`}>
              <SummaryRow label="Points" value={summary.total} />
              <SummaryRow label="Accepted" value={summary.accepted} tone="success" />
              <SummaryRow label="Low confidence" value={summary.lowConf} tone="warning" />
              <SummaryRow label="Relock" value={summary.relock} tone="info" />
              <SummaryRow label="Rejected jumps" value={summary.rejected} tone="destructive" />
              <SummaryRow label="No minimap" value={summary.noMini} tone="muted" />
              <div className="my-1 border-t border-border" />
              <SummaryRow label="Avg score" value={summary.avgScore.toFixed(2)} />
              <SummaryRow label="Avg confidence" value={summary.avgConf.toFixed(2)} />
            </Section>

            {/* SELECTED POINT */}
            {selectedPoint && (
              <Section title={`Frame ${selectedPoint.frame}`}>
                <SummaryRow label="Time" value={fmt(selectedPoint.t)} />
                <SummaryRow label="Status" value={STATUS_LABELS[selectedPoint.status]} />
                <SummaryRow label="Score" value={selectedPoint.score.toFixed(2)} />
                <SummaryRow label="Confidence" value={selectedPoint.confidence.toFixed(2)} />
                <SummaryRow label="Jump" value={`${(selectedPoint.jump * 100).toFixed(1)}%`} />
                <SummaryRow label="bbox" value={`${selectedPoint.bbox.x},${selectedPoint.bbox.y} ${selectedPoint.bbox.w}×${selectedPoint.bbox.h}`} />
                <SummaryRow label="Window" value={selectedPoint.window} />
                <div className="mt-2 flex flex-wrap gap-1">
                  <button onClick={() => setTab("debug")} className="rounded-sm border border-border bg-surface px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted">Open debug</button>
                  <button className="rounded-sm border border-destructive/40 bg-surface px-2 py-1 text-xs uppercase tracking-wider text-destructive hover:bg-destructive/10">Mark wrong</button>
                  <button className="rounded-sm border border-border bg-surface px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted">Export frame</button>
                </div>
              </Section>
            )}

            {/* LINKED PROCESS */}
            {linkedProcess && (
              <Section title="Linked process">
                <div className="text-mono text-xs">{linkedProcess.id}</div>
                <div className="text-xs text-muted-foreground capitalize">{linkedProcess.status} · {summary.total > 0 ? Math.min(99, Math.round((time / duration) * 100)) : 0}%</div>
                <Link to="/admin/processes" className="mt-1 inline-block text-xs text-primary hover:underline">Open in Processes →</Link>
              </Section>
            )}

            {/* ACTIONS */}
            <Section title="Actions">
              <div className="grid grid-cols-2 gap-1.5">
                {!analyzing ? (
                  <button onClick={startAnalysis} disabled={!activeUrl && !activeImage} className="col-span-2 rounded-sm bg-primary px-3 py-2 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110 disabled:opacity-40">
                    Start analysis
                  </button>
                ) : (
                  <button onClick={stopAnalysis} className="col-span-2 rounded-sm border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-destructive hover:bg-destructive/20">
                    Stop
                  </button>
                )}
                <button onClick={resetAll} className="rounded-sm border border-border bg-surface px-2 py-1.5 text-xs uppercase tracking-wider hover:bg-muted">Reset</button>
                <button onClick={saveResult} disabled={!summary.total} className="rounded-sm border border-border bg-surface px-2 py-1.5 text-xs uppercase tracking-wider hover:bg-muted disabled:opacity-40">Save result</button>
                <Link to="/admin/processes" className="col-span-2 rounded-sm border border-border bg-surface px-2 py-1.5 text-center text-xs uppercase tracking-wider hover:bg-muted">Open Processes</Link>
              </div>
            </Section>
          </div>
        </aside>
      </div>
    </div>
  );
}

/* ---------- helpers ---------- */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-sm border border-border bg-surface-2 p-3">
      <div className="label-eyebrow mb-2 text-xs">{title}</div>
      {children}
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <div className="label-eyebrow text-xs text-muted-foreground">{children}</div>;
}

function Slider({ label, value, min, max, step, onChange, hint }: {
  label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; hint?: string;
}) {
  return (
    <div className="mb-2">
      <div className="mb-1 flex items-baseline justify-between text-xs">
        <span className="text-muted-foreground uppercase tracking-wider">{label}</span>
        <span className="text-mono">{typeof value === "number" && step < 1 ? value.toFixed(2) : value}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className="w-full accent-primary" />
      {hint && <div className="mt-0.5 text-[11px] text-muted-foreground">{hint}</div>}
    </div>
  );
}

function SummaryRow({ label, value, tone = "default" }: { label: string; value: React.ReactNode; tone?: "default" | "success" | "warning" | "destructive" | "info" | "muted" }) {
  const cls = {
    default: "",
    success: "text-success",
    warning: "text-warning",
    destructive: "text-destructive",
    info: "text-primary",
    muted: "text-muted-foreground",
  }[tone];
  return (
    <div className="flex justify-between text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className={`text-mono ${cls}`}>{value}</span>
    </div>
  );
}

function EmptySource({ source, onUpload }: { source: SourceMode; onUpload: (f: File) => void }) {
  const isUpload = source === "screenshot" || source === "upload";
  return (
    <div className="flex max-w-sm flex-col items-center gap-3 text-center">
      <div className="text-sm font-semibold">No source loaded</div>
      <div className="text-xs text-muted-foreground">
        {source === "team_pov" && "No POV VOD link for this team. Add one in Matches → team VODs."}
        {source === "observer" && "No observer VOD configured for this match."}
        {source === "screenshot" && "Upload a screenshot to inspect a single frame."}
        {source === "upload" && "Upload a video file to analyze locally."}
      </div>
      <div className="flex flex-wrap justify-center gap-1.5">
        {!isUpload && (
          <>
            <Link to="/admin/matches" className="rounded-sm bg-primary px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110">
              + Add POV VOD
            </Link>
            <Link to="/admin/matches" className="rounded-sm border border-border bg-surface px-3 py-1.5 text-xs uppercase tracking-wider hover:bg-muted">
              Open match settings
            </Link>
          </>
        )}
        <label className="cursor-pointer rounded-sm border border-border bg-surface px-3 py-1.5 text-xs uppercase tracking-wider hover:bg-muted">
          <input
            type="file"
            accept={source === "screenshot" ? "image/*" : "video/*,image/*"}
            className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) onUpload(f); }}
          />
          Upload {source === "screenshot" ? "screenshot" : "video"}
        </label>
      </div>
    </div>
  );
}

function clamp01(v: number) { return Math.max(0, Math.min(1, v)); }
function fmt(s: number) {
  if (!isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60).toString().padStart(2, "0");
  return `${m}:${sec}`;
}