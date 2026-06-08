import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  maps as allMaps,
  teams as seedTeams,
  generateTrajectory,
  ringPhases,
  events as seedEvents,
  type RingPhase,
} from "@/lib/mock-match";
import { useAdminStore } from "@/lib/admin-store";
import { getSlotColor } from "@/lib/team-colors";

export const Route = createFileRoute("/admin/camera")({ component: CameraAdmin });

/* =========================================================================
   Types & constants
   ========================================================================= */

type TrackingSettings = {
  // source
  videoUrl: string;
  sourceType: "vod" | "player_cam" | "observer";
  frameRate: number;
  // crop
  cropLeft: number; cropRight: number; cropTop: number; cropBottom: number;
  // smoothing / response
  smoothing: number;
  responseSpeed: number;
  deadzone: number;
  maxSpeed: number;
  ema: number;
  // zoom
  zoomMin: number; zoomMax: number; zoomStep: number; zoomLerp: number;
  zoomSensitivity: number; stepZoomEnabled: boolean;
  // ring / team
  ringWeight: number; teamWeight: number;
  ringNoiseTolerance: number; teamClusterTolerance: number; ringCenterLock: boolean;
  // jump detection
  jumpThreshold: number; jumpCooldownFrames: number;
  preJumpUnlock: number; antiLatchTail: number; relockThreshold: number;
  // advanced
  sampleStep: number; confidenceThreshold: number; lostFrameThreshold: number;
  debugMode: boolean; saveDebugFrames: boolean;
};

type Viewport = { x: number; y: number; size: number };
type Preset = {
  id: string;
  name: string;
  viewport: Viewport;
  settings: TrackingSettings;
};

const SRC_W = 1920;
const SRC_H = 1080;
const RING_CLOSE_FRACTION = 0.4;

type RingSegment = { phaseIndex: number; kind: "CD" | "Closing"; startSec: number; endSec: number };
const ringSegments: RingSegment[] = ringPhases.flatMap((p, i) => {
  const dur = p.endSec - p.startSec;
  const closeStart = p.startSec + dur * (1 - RING_CLOSE_FRACTION);
  return [
    { phaseIndex: i, kind: "CD",      startSec: p.startSec, endSec: closeStart } as RingSegment,
    { phaseIndex: i, kind: "Closing", startSec: closeStart, endSec: p.endSec }    as RingSegment,
  ];
});
const GAME_DURATION_SEC = ringPhases[ringPhases.length - 1].endSec;

const SAMPLE_VIDEO =
  "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4";

const baseSettings: TrackingSettings = {
  videoUrl: SAMPLE_VIDEO,
  sourceType: "observer",
  frameRate: 60,
  cropLeft: 420, cropRight: 420, cropTop: 0, cropBottom: 0,
  smoothing: 0.55, responseSpeed: 0.45, deadzone: 18, maxSpeed: 60, ema: 14,
  zoomMin: 1.0, zoomMax: 2.4, zoomStep: 0.1, zoomLerp: 0.35,
  zoomSensitivity: 0.5, stepZoomEnabled: false,
  ringWeight: 0.5, teamWeight: 0.6,
  ringNoiseTolerance: 0.25, teamClusterTolerance: 0.3, ringCenterLock: false,
  jumpThreshold: 140, jumpCooldownFrames: 8,
  preJumpUnlock: 0.6, antiLatchTail: 0.4, relockThreshold: 0.55,
  sampleStep: 2, confidenceThreshold: 0.45, lostFrameThreshold: 6,
  debugMode: false, saveDebugFrames: false,
};

const defaultPresets: Preset[] = [
  {
    id: "p-step", name: "Step zoom",
    viewport: { x: 0, y: 0, size: 1 },
    settings: { ...baseSettings, zoomLerp: 0.0, zoomStep: 0.25, smoothing: 0.7, stepZoomEnabled: true },
  },
  {
    id: "p-smooth", name: "Smooth observer",
    viewport: { x: 0.2, y: 0.2, size: 0.6 },
    settings: { ...baseSettings, smoothing: 0.85, zoomLerp: 0.7, responseSpeed: 0.35, ema: 22 },
  },
  {
    id: "p-fast", name: "Fast camera",
    viewport: { x: 0.3, y: 0.3, size: 0.4 },
    settings: { ...baseSettings, smoothing: 0.15, responseSpeed: 0.95, maxSpeed: 220, deadzone: 0, jumpThreshold: 60, ema: 3 },
  },
  {
    id: "p-lownoise", name: "Low noise",
    viewport: { x: 0.1, y: 0.1, size: 0.7 },
    settings: { ...baseSettings, ringWeight: 0.85, ringNoiseTolerance: 0.7, teamWeight: 0.3, smoothing: 0.75 },
  },
  {
    id: "p-custom", name: "Custom",
    viewport: { x: 0.2, y: 0.2, size: 0.6 },
    settings: { ...baseSettings },
  },
];

const PRESET_DESCRIPTIONS: Record<string, string> = {
  "Step zoom":       "Для трансляций со ступенчатым зумом. Меньше сглаживания zoom, быстрая реакция на резкие изменения масштаба.",
  "Smooth observer": "Для плавной камеры наблюдателя. Сильное сглаживание, меньше шума, но возможна задержка.",
  "Fast camera":     "Для быстрых перемещений. Высокая чувствительность и скорость реакции, выше риск шума.",
  "Low noise":       "Для шумного видео или нестабильного трека. Сильная фильтрация скачков, меньше ложных движений.",
  "Custom":          "Ручные настройки оператора.",
};

const METRIC_HINTS = {
  trackingQ:     "Интегральная оценка качества: 100 − штрафы за джампы, потерянные кадры и low-confidence. ≥80 — хорошо, 60–80 — приемлемо, <60 — пересчитать.",
  jumpEvents:    "Количество событий резкого смещения камеры между кадрами (> jump threshold). Меньше — лучше.",
  lostFrames:    "Кадры с уверенностью ниже порога подряд > lost frame threshold. Меньше — лучше.",
  avgConfidence: "Средняя уверенность распознавания позиции камеры по всем кадрам. Ближе к 1 — лучше.",
} as const;

const SPLIT_VIEW_PURPOSE =
  "Слева — реальная трансляция (crop). Справа — то, что система считает видимой областью на карте. " +
  "Сравнивайте: совпадает ли движение, не уезжает ли bbox, корректен ли zoom, нет ли скачков и потерь.";

type ViewMode = "overview" | "graphs" | "settings" | "debug";
type SplitOpts = {
  syncMapVideo: boolean;
  lockZoom: boolean;
  showRingCenter: boolean;
  showCameraBbox: boolean;
};

type SeriesVisibility = {
  raw: boolean; smoothed: boolean; ringCenter: boolean; jumpScore: boolean; confidence: boolean;
};

type TrackEvent = { t: number; kind: "ring" | "jump" | "relock" | "lost" | "manual"; label: string };
const eventColor: Record<TrackEvent["kind"], string> = {
  ring: "#22d3ee", jump: "#ef4444", relock: "#a855f7", lost: "#9ca3af", manual: "#06b6d4",
};
const eventLabel: Record<TrackEvent["kind"], string> = {
  ring: "Ring closing", jump: "Jump detected", relock: "Relock",
  lost: "Lost tracking", manual: "Manual correction",
};

type VideoOverlays = {
  showCrop: boolean; showHud: boolean; showDetected: boolean; showMinimap: boolean;
};
type EventFilters = Record<TrackEvent["kind"], boolean>;
type QualityMetrics = { trackingQ: number; jumpEvents: number; lostFrames: number; avgConfidence: number };

/* parameter explanations (tooltips) */
const HINTS: Record<string, string> = {
  smoothing: "Доля сглаживания EMA. Выше — плавнее, но возможна задержка реакции.",
  responseSpeed: "Скорость реакции камеры на смещение цели. Выше — быстрее, но больше шум.",
  deadzone: "Минимальное смещение, которое считается реальным движением. Меньше — больше шума.",
  maxSpeed: "Максимальное смещение камеры за кадр. Выше — возможны резкие скачки.",
  ema: "Количество кадров для сглаживания EMA. Выше — плавнее, но больше задержка.",
  cropLeft: "Срез слева в пикселях исходного кадра 1920×1080.",
  cropRight: "Срез справа в пикселях исходного кадра 1920×1080.",
  cropTop: "Срез сверху (HUD, имена игроков и т.п.).",
  cropBottom: "Срез снизу (HUD-индикаторы).",
  zoomLerp: "Скорость интерполяции зума. 0 — мгновенно, 1 — очень плавно.",
  zoomStep: "Шаг для step-zoom режима.",
  zoomSensitivity: "Чувствительность авто-зума к радиусу кольца / разлёту команд.",
  ringWeight: "Вес центра кольца при расчёте позиции камеры.",
  teamWeight: "Вес позиций команд при расчёте позиции камеры.",
  ringNoiseTolerance: "Допустимый шум распознавания кольца перед relock.",
  teamClusterTolerance: "Допустимый шум кластеризации команд.",
  jumpThreshold: "Смещение в px между кадрами, которое считается джампом.",
  jumpCooldownFrames: "Сколько кадров после джампа игнорировать новые джампы.",
  preJumpUnlock: "Сек до джампа, когда камера ослабляет привязку.",
  antiLatchTail: "Хвост anti-latch после джампа, секунды.",
  relockThreshold: "Порог уверенности для relock после lost.",
  confidenceThreshold: "Минимальная уверенность кадра, ниже которой кадр считается low-confidence.",
  lostFrameThreshold: "Сколько подряд low-confidence кадров считаются lost.",
  sampleStep: "Каждый N-й кадр для анализа (производительность).",
};

/* warnings for dangerous values */
function getWarn(key: string, v: number): string | null {
  switch (key) {
    case "maxSpeed":   return v > 300 ? "Max speed слишком высокий — возможны резкие скачки" : null;
    case "smoothing":  return v > 0.9 ? "Smoothing слишком высокий — возможна задержка реакции" : null;
    case "deadzone":   return v < 4   ? "Deadzone слишком низкий — возможен шум" : null;
    case "responseSpeed": return v > 0.95 ? "Response speed слишком высокий — рывки" : null;
    case "jumpThreshold": return v < 40 ? "Слишком низкий порог — ложные джампы" : null;
    default: return null;
  }
}

function buildEvents(duration: number): TrackEvent[] {
  const frac: Array<[number, TrackEvent["kind"]]> = [
    [0.15, "jump"], [0.16, "relock"],
    [0.25, "ring"],
    [0.35, "lost"],
    [0.42, "jump"], [0.43, "relock"],
    [0.50, "manual"],
    [0.60, "ring"],
    [0.70, "jump"], [0.71, "relock"],
    [0.78, "lost"],
    [0.85, "ring"],
    [0.92, "jump"], [0.925, "relock"],
  ];
  return frac.map(([f, k]) => ({ t: f * duration, kind: k, label: eventLabel[k] }));
}

const GRAPH_PRESETS = ["Step zoom", "Ring noise", "Balance", "Max sensitivity"] as const;

const DEBUG_FILES = [
  { name: "progress.json",        path: "/tmp/tracker/progress.json" },
  { name: "partial_result.json",  path: "/tmp/tracker/partial_result.json" },
  { name: "result.json",          path: "/tmp/tracker/result.json" },
  { name: "camera_track.json",    path: "/tmp/tracker/camera_track.json" },
  { name: "debug_video.mp4",      path: "/tmp/tracker/debug_video.mp4" },
  { name: "trajectory_map.jpg",   path: "/tmp/tracker/trajectory_map.jpg" },
] as const;

/* =========================================================================
   Root component
   ========================================================================= */

function CameraAdmin() {
  const { tournaments, matches } = useAdminStore();

  const [tournamentId, setTournamentId] = useState(tournaments[0]?.id ?? "");
  const tournamentMatches = useMemo(
    () => matches.filter((m) => m.tournamentId === tournamentId),
    [matches, tournamentId],
  );
  const [matchId, setMatchId] = useState(tournamentMatches[0]?.id ?? "");
  useEffect(() => { setMatchId(tournamentMatches[0]?.id ?? ""); }, [tournamentId]);
  const match = matches.find((m) => m.id === matchId);
  const matchMapIds = (match?.mapIds && match.mapIds.length ? match.mapIds : match ? [match.mapId] : []);
  const [mapId, setMapId] = useState(matchMapIds[0] ?? allMaps[0].id);
  useEffect(() => { setMapId(matchMapIds[0] ?? allMaps[0].id); }, [matchId]);
  const map = allMaps.find((m) => m.id === mapId) ?? allMaps[0];

  /* presets + draft/committed split */
  const [presets, setPresets] = useState<Preset[]>(defaultPresets);
  const [activePresetId, setActivePresetId] = useState<string>(defaultPresets[0].id);
  const active = presets.find((p) => p.id === activePresetId) ?? presets[0];

  const [viewport, setViewport] = useState<Viewport>(active.viewport);
  const [draft, setDraft] = useState<TrackingSettings>(active.settings);
  const [committed, setCommitted] = useState<TrackingSettings>(active.settings);

  const patchDraft = (patch: Partial<TrackingSettings>) =>
    setDraft((d) => ({ ...d, ...patch }));

  const applyPreset = (id: string) => {
    const p = presets.find((x) => x.id === id);
    if (!p) return;
    setActivePresetId(id);
    setViewport(p.viewport);
    setDraft(p.settings);
  };
  const commitUpdate = () => setCommitted(draft);
  const saveAs = () => {
    const name = window.prompt("Preset name?");
    if (!name) return;
    const np: Preset = { id: `p-${Date.now()}`, name, viewport, settings: draft };
    setPresets((arr) => [...arr, np]);
    setActivePresetId(np.id);
  };
  const updateActivePreset = () => {
    setPresets((arr) => arr.map((p) => (p.id === activePresetId
      ? { ...p, viewport, settings: draft } : p)));
  };
  const duplicatePreset = () => {
    const np: Preset = { id: `p-${Date.now()}`, name: `${active.name} copy`, viewport, settings: draft };
    setPresets((arr) => [...arr, np]);
    setActivePresetId(np.id);
  };
  const deleteActivePreset = () => {
    if (presets.length <= 1) return;
    if (!window.confirm(`Delete preset "${active.name}"?`)) return;
    const next = presets.filter((p) => p.id !== activePresetId);
    setPresets(next);
    setActivePresetId(next[0].id);
  };
  const resetToDefault = () => {
    setDraft(baseSettings);
  };

  /* video / timeline */
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(60);
  const [playing, setPlaying] = useState(false);
  const [videoLoaded, setVideoLoaded] = useState<"loading" | "loaded" | "error">("loading");
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    setVideoLoaded("loading");
    const onLoaded = () => { setDuration(v.duration || 60); setVideoLoaded("loaded"); };
    const onTime = () => setTime(v.currentTime);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onErr = () => setVideoLoaded("error");
    v.addEventListener("loadedmetadata", onLoaded);
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("error", onErr);
    return () => {
      v.removeEventListener("loadedmetadata", onLoaded);
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("error", onErr);
    };
  }, [committed.videoUrl]);

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play(); else v.pause();
  };
  const seek = (t: number) => {
    const v = videoRef.current;
    if (v) v.currentTime = t;
    setTime(t);
  };

  const events = useMemo(() => buildEvents(duration), [duration]);

  /* metrics derived from committed settings */
  const quality = useMemo(() => {
    const totalFrames = Math.max(60, Math.round(duration * committed.frameRate));
    const jumpEvents = events.filter((e) => e.kind === "jump").length;
    const lostFrames = Math.max(0, Math.round(18 + committed.responseSpeed * 30 - committed.smoothing * 16));
    const lowConfFrames = Math.round(totalFrames * (1 - (0.55 + committed.smoothing * 0.25 + committed.ringWeight * 0.1)));
    const lowConfRatio = Math.max(0, Math.min(1, lowConfFrames / totalFrames));
    const lostRatio = Math.max(0, Math.min(1, lostFrames / totalFrames));
    const penalty = jumpEvents * 2 + lostRatio * 100 + lowConfRatio * 50;
    const trackingQ = Math.max(0, Math.min(100, Math.round(100 - penalty)));
    const avgConfidence = Math.max(0, Math.min(1, 0.55 + committed.smoothing * 0.25 + committed.ringWeight * 0.1));
    return { trackingQ, jumpEvents, lostFrames, avgConfidence };
  }, [committed, events, duration]);

  /* game time + trajectories + rings + deaths (kept from before) */
  const gameTime = useMemo(
    () => (duration > 0 ? (time / duration) * GAME_DURATION_SEC : 0),
    [time, duration],
  );
  const trajectories = useMemo(
    () => Object.fromEntries(seedTeams.map((t, i) => [t.id, generateTrajectory(i + 7, GAME_DURATION_SEC)])),
    [],
  );
  const teamByTag = useMemo(() => new Map(seedTeams.map((t) => [t.tag, t])), []);
  const deathTimes = useMemo<Record<string, number>>(() => {
    const out: Record<string, number> = {};
    for (const e of seedEvents) {
      if (e.type !== "wipe") continue;
      const m = /wipes\s+([A-Za-z0-9]+)/i.exec(e.label);
      const victim = m?.[1] ? teamByTag.get(m[1]) : undefined;
      if (!victim) continue;
      if (out[victim.id] === undefined || e.t < out[victim.id]) out[victim.id] = e.t;
    }
    for (const t of seedTeams) {
      if (t.alive || out[t.id] !== undefined) continue;
      const k = (seedTeams.length - t.placement + 1) / (seedTeams.length + 1);
      out[t.id] = Math.round(GAME_DURATION_SEC * k);
    }
    return out;
  }, [teamByTag]);
  const ring = useMemo<RingPhase>(() => {
    const seg = ringSegments.find((s) => gameTime >= s.startSec && gameTime <= s.endSec)
      ?? ringSegments[ringSegments.length - 1];
    const target = ringPhases[seg.phaseIndex];
    const prev: RingPhase = seg.phaseIndex === 0
      ? { startSec: 0, endSec: 0, cx: 0.5, cy: 0.5, r: 0.72 }
      : ringPhases[seg.phaseIndex - 1];
    if (seg.kind === "CD") return prev;
    const k = Math.max(0, Math.min(1, (gameTime - seg.startSec) / (seg.endSec - seg.startSec)));
    return {
      ...target,
      cx: prev.cx + (target.cx - prev.cx) * k,
      cy: prev.cy + (target.cy - prev.cy) * k,
      r:  prev.r  + (target.r  - prev.r ) * k,
    };
  }, [gameTime]);
  const teamPositions = useMemo(() => {
    return seedTeams.map((tm, i) => {
      const path = trajectories[tm.id];
      const deathT = deathTimes[tm.id];
      const isDead = deathT !== undefined && gameTime >= deathT;
      const effT = isDead ? deathT! : gameTime;
      let head = path[0];
      for (const p of path) { if (p.t > effT) break; head = p; }
      return { id: tm.id, tag: tm.tag, slotIdx: i, x: head.x, y: head.y, isDead };
    });
  }, [trajectories, gameTime, deathTimes]);

  /* view-state */
  const [viewMode, setViewMode] = useState<ViewMode>("overview");
  const [splitOpts, setSplitOpts] = useState<SplitOpts>({
    syncMapVideo: true, lockZoom: false, showRingCenter: true, showCameraBbox: true,
  });
  const [showOriginal, setShowOriginal] = useState(true);
  const [seriesVis, setSeriesVis] = useState<SeriesVisibility>({
    raw: true, smoothed: true, ringCenter: true, jumpScore: true, confidence: true,
  });
  const [graphPreset, setGraphPreset] = useState<typeof GRAPH_PRESETS[number]>("Balance");
  const [selectedEvent, setSelectedEvent] = useState<TrackEvent | null>(null);
  const [selectedDebugFile, setSelectedDebugFile] = useState<string>("result.json");
  const [videoOverlays, setVideoOverlays] = useState<VideoOverlays>({
    showCrop: true, showHud: false, showDetected: true, showMinimap: false,
  });
  const [eventFilters, setEventFilters] = useState<EventFilters>({
    ring: true, jump: true, relock: true, lost: true, manual: true,
  });
  const [prevQuality, setPrevQuality] = useState<QualityMetrics | null>(null);

  const visibleEvents = useMemo(
    () => events.filter((e) => eventFilters[e.kind]),
    [events, eventFilters],
  );

  /* commit snapshots previous metrics for compare */
  const commitUpdateWithSnapshot = () => {
    setPrevQuality(quality);
    setCommitted(draft);
  };
  const resetDraftToActive = () => setDraft(active.settings);

  /* map pan/zoom + viewport drag */
  const mapRef = useRef<HTMLDivElement | null>(null);
  const [vpDrag, setVpDrag] = useState<null | { kind: "move" | "resize"; startX: number; startY: number; v: Viewport }>(null);
  useEffect(() => {
    if (!vpDrag) return;
    const onMove = (e: MouseEvent) => {
      const el = mapRef.current; if (!el) return;
      const r = el.getBoundingClientRect();
      const dx = (e.clientX - vpDrag.startX) / r.width;
      const dy = (e.clientY - vpDrag.startY) / r.height;
      setViewport((curr) => {
        if (vpDrag.kind === "move") {
          const x = Math.max(0, Math.min(1 - vpDrag.v.size, vpDrag.v.x + dx));
          const y = Math.max(0, Math.min(1 - vpDrag.v.size, vpDrag.v.y + dy));
          return { ...curr, x, y };
        }
        const size = Math.max(0.08, Math.min(1, vpDrag.v.size + Math.max(dx, dy)));
        const x = Math.min(vpDrag.v.x, 1 - size);
        const y = Math.min(vpDrag.v.y, 1 - size);
        return { x, y, size };
      });
    };
    const onUp = () => setVpDrag(null);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [vpDrag]);
  const resetViewport = () => setViewport({ x: 0.2, y: 0.2, size: 0.6 });
  const fitMap = () => setViewport({ x: 0, y: 0, size: 1 });

  /* Problems list derived from events */
  const problems = useMemo(
    () => events.filter((e) => e.kind === "jump" || e.kind === "lost" || e.kind === "relock"),
    [events],
  );

  /* visible crop area for video preview */
  const visibleW = Math.max(1, SRC_W - committed.cropLeft - committed.cropRight);
  const visibleH = Math.max(1, SRC_H - committed.cropTop - committed.cropBottom);
  const visibleAspect = visibleW / visibleH;

  /* dirty marker: are draft != committed? */
  const isDirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(committed), [draft, committed]);

  /* ====== render ====== */

  const mapPreview = (
    <MapPreview
      map={map}
      mapRef={mapRef}
      viewport={viewport}
      setViewport={setViewport}
      vpDrag={vpDrag}
      setVpDrag={setVpDrag}
      ring={ring}
      teamPositions={teamPositions}
      splitOpts={splitOpts}
      onFit={fitMap}
      onReset={resetViewport}
    />
  );

  const videoPreview = (
    <VideoPreview
      videoRef={videoRef}
      videoUrl={committed.videoUrl}
      cropLeft={committed.cropLeft}
      cropRight={committed.cropRight}
      cropTop={committed.cropTop}
      cropBottom={committed.cropBottom}
      visibleAspect={visibleAspect}
      visibleW={visibleW}
      visibleH={visibleH}
      time={time}
      duration={duration}
      showCameraBbox={splitOpts.showCameraBbox}
      videoLoaded={videoLoaded}
      syncMapVideo={splitOpts.syncMapVideo}
      overlays={videoOverlays}
      onOverlaysChange={setVideoOverlays}
      onOpenSourceSettings={() => setViewMode("settings")}
    />
  );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <HeaderStrip
        tournamentId={tournamentId} setTournamentId={setTournamentId}
        tournaments={tournaments}
        matchId={matchId} setMatchId={setMatchId} tournamentMatches={tournamentMatches}
        mapId={mapId} setMapId={setMapId} matchMapIds={matchMapIds}
        viewMode={viewMode} setViewMode={setViewMode}
      />
      <QualityBar quality={quality} prevQuality={prevQuality} preset={active.name} isDirty={isDirty} />

      <div className="flex flex-1 overflow-hidden">
        {/* LEFT: tab content */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {viewMode === "overview" && (
            <OverviewTab
              splitOpts={splitOpts} setSplitOpts={setSplitOpts}
              onResetViewport={resetViewport} onFitMap={fitMap}
              videoPreview={videoPreview} mapPreview={mapPreview}
              time={time} duration={duration} playing={playing}
              onSeek={seek} onTogglePlay={togglePlay}
              events={visibleEvents}
              eventFilters={eventFilters} setEventFilters={setEventFilters}
              onSelectEvent={setSelectedEvent}
              selectedEvent={selectedEvent}
            />
          )}
          {viewMode === "graphs" && (
            <GraphsTab
              time={time} duration={duration} onSeek={seek}
              events={visibleEvents} showOriginal={showOriginal}
              onToggleOriginal={() => setShowOriginal((v) => !v)}
              seriesVis={seriesVis}
              onSelectEvent={setSelectedEvent}
            />
          )}
          {viewMode === "settings" && (
            <SettingsTabContent
              videoPreview={videoPreview} mapPreview={mapPreview}
            />
          )}
          {viewMode === "debug" && (
            <DebugTab
              settings={committed} viewport={viewport} quality={quality}
              events={visibleEvents} time={time}
              selectedDebugFile={selectedDebugFile}
              videoPreview={videoPreview}
            />
          )}
        </div>

        {/* RIGHT: contextual panel */}
        <RightPanel
          viewMode={viewMode}
          active={active}
          presets={presets}
          activePresetId={activePresetId}
          onApplyPreset={applyPreset}
          onUpdateCommit={commitUpdateWithSnapshot}
          onSaveAs={saveAs}
          onUpdateActivePreset={updateActivePreset}
          onDuplicatePreset={duplicatePreset}
          onDeletePreset={deleteActivePreset}
          onResetToDefault={resetToDefault}
          onResetDraftToActive={resetDraftToActive}
          isDirty={isDirty}
          quality={quality}
          prevQuality={prevQuality}
          problems={problems}
          onSeek={seek}
          draft={draft} patchDraft={patchDraft}
          committed={committed}
          graphPreset={graphPreset} setGraphPreset={setGraphPreset}
          seriesVis={seriesVis} setSeriesVis={setSeriesVis}
          selectedEvent={selectedEvent}
          selectedDebugFile={selectedDebugFile} setSelectedDebugFile={setSelectedDebugFile}
          time={time}
          eventFilters={eventFilters} setEventFilters={setEventFilters}
        />
      </div>
    </div>
  );
}

/* =========================================================================
   Header + quality bar
   ========================================================================= */

function HeaderStrip(props: {
  tournamentId: string; setTournamentId: (v: string) => void; tournaments: { id: string; name: string }[];
  matchId: string; setMatchId: (v: string) => void; tournamentMatches: { id: string; name: string }[];
  mapId: string; setMapId: (v: string) => void; matchMapIds: string[];
  viewMode: ViewMode; setViewMode: (v: ViewMode) => void;
}) {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border bg-surface px-6">
      <div className="flex items-center gap-3">
        <h1 className="text-sm font-bold uppercase tracking-wider">Camera tracking</h1>
        <span className="text-mono text-xs text-muted-foreground">·</span>
        <select value={props.tournamentId} onChange={(e) => props.setTournamentId(e.target.value)}
          className="rounded-sm border border-border bg-background px-2 py-1 text-xs">
          {props.tournaments.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <select value={props.matchId} onChange={(e) => props.setMatchId(e.target.value)}
          className="rounded-sm border border-border bg-background px-2 py-1 text-xs"
          disabled={!props.tournamentMatches.length}>
          {props.tournamentMatches.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
        <select value={props.mapId} onChange={(e) => props.setMapId(e.target.value)}
          className="rounded-sm border border-border bg-background px-2 py-1 text-xs">
          {(props.matchMapIds.length ? props.matchMapIds : allMaps.map((m) => m.id)).map((id) => {
            const m = allMaps.find((x) => x.id === id);
            return <option key={id} value={id}>{m?.name ?? id}</option>;
          })}
        </select>
        <div className="ml-2 flex items-center gap-1 border-l border-border pl-3">
          <span className="label-eyebrow mr-1 text-xs">View</span>
          {(["overview", "graphs", "settings", "debug"] as ViewMode[]).map((m) => (
            <button key={m} onClick={() => props.setViewMode(m)}
              className={`rounded-sm border px-2.5 py-1 text-xs font-semibold uppercase tracking-wider transition-colors ${
                m === props.viewMode ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-surface-2 text-muted-foreground hover:bg-muted"
              }`}>{m}</button>
          ))}
        </div>
      </div>
    </header>
  );
}

function QualityBar({ quality, preset, isDirty, prevQuality }: {
  quality: { trackingQ: number; jumpEvents: number; lostFrames: number; avgConfidence: number };
  prevQuality?: QualityMetrics | null;
  preset: string; isDirty: boolean;
}) {
  const tone = quality.trackingQ >= 80 ? "text-emerald-400" : quality.trackingQ >= 60 ? "text-amber-400" : "text-destructive";
  const delta = (cur: number, prev: number | undefined, isPct = false, decimals = 0, lowerIsBetter = false) => {
    if (prev === undefined) return null;
    const d = cur - prev;
    if (Math.abs(d) < (isPct ? 0.5 : decimals ? 0.005 : 0.5)) return null;
    const good = lowerIsBetter ? d < 0 : d > 0;
    const cls = good ? "text-emerald-400" : "text-destructive";
    const sign = d > 0 ? "+" : "";
    const val = decimals ? d.toFixed(decimals) : Math.round(d).toString();
    return <span className={`ml-1 text-mono text-xs ${cls}`}>{sign}{val}{isPct ? "%" : ""}</span>;
  };
  const prev = prevQuality;
  return (
    <div className="flex shrink-0 items-center gap-6 border-b border-border bg-surface-2 px-6 py-2">
      <Stat label="Tracking quality" hint={METRIC_HINTS.trackingQ} value={`${quality.trackingQ}%`} valueClass={tone} after={delta(quality.trackingQ, prev?.trackingQ, true)} />
      <Stat label="Jump events" hint={METRIC_HINTS.jumpEvents} value={quality.jumpEvents.toString()} after={delta(quality.jumpEvents, prev?.jumpEvents, false, 0, true)} />
      <Stat label="Lost frames" hint={METRIC_HINTS.lostFrames} value={quality.lostFrames.toString()} after={delta(quality.lostFrames, prev?.lostFrames, false, 0, true)} />
      <Stat label="Avg confidence" hint={METRIC_HINTS.avgConfidence} value={quality.avgConfidence.toFixed(2)} after={delta(quality.avgConfidence, prev?.avgConfidence, false, 2)} />
      <div className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
        {isDirty && (
          <span className="rounded-sm border border-amber-400/40 bg-amber-400/10 px-2 py-0.5 font-semibold uppercase tracking-wider text-amber-400">
            Pending update
          </span>
        )}
        {prev && (
          <span className="text-mono text-[11px]">vs prev · Q {prev.trackingQ}% · J {prev.jumpEvents} · L {prev.lostFrames}</span>
        )}
        <span>current preset · <span className="text-foreground">{preset}</span></span>
      </div>
    </div>
  );
}

function Stat({ label, value, valueClass = "", after, hint }: { label: string; value: string; valueClass?: string; after?: React.ReactNode; hint?: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="label-eyebrow text-xs" title={hint}>
        {label}{hint && <span className="ml-1 cursor-help text-muted-foreground/60">ⓘ</span>}
      </span>
      <span className={`text-mono text-sm font-semibold ${valueClass}`}>{value}{after}</span>
    </div>
  );
}

/* =========================================================================
   OVERVIEW TAB
   ========================================================================= */

function OverviewTab(props: {
  splitOpts: SplitOpts; setSplitOpts: (v: SplitOpts) => void;
  onResetViewport: () => void; onFitMap: () => void;
  videoPreview: React.ReactNode; mapPreview: React.ReactNode;
  time: number; duration: number; playing: boolean;
  onSeek: (t: number) => void; onTogglePlay: () => void;
  events: TrackEvent[];
  eventFilters: EventFilters; setEventFilters: (v: EventFilters) => void;
  onSelectEvent: (e: TrackEvent | null) => void;
  selectedEvent: TrackEvent | null;
}) {
  return (
    <>
      <SplitControls opts={props.splitOpts} onChange={props.setSplitOpts}
        onReset={props.onResetViewport} onFit={props.onFitMap} />
      <div className="shrink-0 border-b border-border bg-surface-2/60 px-3 py-1.5 text-[11px] leading-snug text-muted-foreground">
        <span className="label-eyebrow mr-2 text-xs text-foreground">Split view</span>
        {SPLIT_VIEW_PURPOSE}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-2 gap-3 p-3">
        {props.videoPreview}
        {props.mapPreview}
      </div>
      <Timeline time={props.time} duration={props.duration} playing={props.playing}
        onTogglePlay={props.onTogglePlay} onSeek={props.onSeek} events={props.events}
        eventFilters={props.eventFilters} setEventFilters={props.setEventFilters}
        onSelectEvent={props.onSelectEvent} selectedEvent={props.selectedEvent} />
    </>
  );
}

function SplitControls({ opts, onChange, onReset, onFit }: {
  opts: SplitOpts; onChange: (next: SplitOpts) => void;
  onReset: () => void; onFit: () => void;
}) {
  const toggle = (k: keyof SplitOpts) => onChange({ ...opts, [k]: !opts[k] });
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-1 border-b border-border bg-surface px-3 py-1.5">
      <span className="label-eyebrow mr-2 text-xs">Split view</span>
      <ToggleBtn active={opts.syncMapVideo} onClick={() => toggle("syncMapVideo")}>Sync map/video</ToggleBtn>
      <ToggleBtn active={opts.lockZoom} onClick={() => toggle("lockZoom")}>Lock zoom</ToggleBtn>
      <ToggleBtn active={opts.showRingCenter} onClick={() => toggle("showRingCenter")}>Show ring center</ToggleBtn>
      <ToggleBtn active={opts.showCameraBbox} onClick={() => toggle("showCameraBbox")}>Show camera bbox</ToggleBtn>
      <span className="mx-2 h-4 w-px bg-border" />
      <button onClick={onReset} className="rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:bg-muted">Reset viewport</button>
      <button onClick={onFit} className="rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:bg-muted">Fit map</button>
    </div>
  );
}

function ToggleBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick}
      className={`rounded-sm border px-2 py-1 text-xs font-semibold uppercase tracking-wider transition-colors ${
        active ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-surface-2 text-muted-foreground hover:bg-muted"
      }`}>
      {children}
    </button>
  );
}

function Timeline({ time, duration, playing, onTogglePlay, onSeek, events, eventFilters, setEventFilters, onSelectEvent, selectedEvent }: {
  time: number; duration: number; playing: boolean;
  onTogglePlay: () => void; onSeek: (t: number) => void; events: TrackEvent[];
  eventFilters?: EventFilters; setEventFilters?: (v: EventFilters) => void;
  onSelectEvent?: (e: TrackEvent | null) => void;
  selectedEvent?: TrackEvent | null;
}) {
  const toggleFilter = (k: TrackEvent["kind"]) =>
    eventFilters && setEventFilters && setEventFilters({ ...eventFilters, [k]: !eventFilters[k] });
  return (
    <div className="shrink-0 border-t border-border bg-surface px-3 py-2">
      {eventFilters && setEventFilters && (
        <div className="mb-1.5 flex flex-wrap items-center gap-1">
          <span className="label-eyebrow mr-1 text-xs">Layers</span>
          {(Object.keys(eventFilters) as Array<TrackEvent["kind"]>).map((k) => (
            <button key={k} onClick={() => toggleFilter(k)}
              className={`flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wider transition-colors ${
                eventFilters[k] ? "border-border bg-surface-2 text-foreground" : "border-border/50 bg-surface-2/40 text-muted-foreground/60 line-through"
              }`}>
              <span className="inline-block h-2 w-2 rounded-sm" style={{ background: eventColor[k] }} />
              {k}
            </button>
          ))}
        </div>
      )}
      <div className="flex items-center gap-3">
        <button onClick={onTogglePlay} className="rounded-sm bg-primary px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110">
          {playing ? "Pause" : "Play"}
        </button>
        <span className="text-mono text-xs text-muted-foreground">{fmt(time)}</span>
        <div className="relative flex-1">
          <input type="range" min={0} max={duration} step={0.05} value={time}
            onChange={(e) => onSeek(Number(e.target.value))} className="w-full accent-primary" />
          <div className="absolute inset-x-0 -bottom-2 h-3">
            {events.map((ev, i) => (
              <button key={i}
                onClick={(e) => { e.stopPropagation(); onSelectEvent?.(ev); onSeek(ev.t); }}
                className={`absolute top-0 h-3 w-1 cursor-pointer rounded-sm transition-transform hover:scale-y-110 ${selectedEvent === ev ? "ring-1 ring-white" : ""}`}
                style={{ left: `${(ev.t / Math.max(duration, 0.001)) * 100}%`, background: eventColor[ev.kind] }}
                title={`${ev.label} · ${fmt(ev.t)}`}
              />
            ))}
          </div>
        </div>
        <span className="text-mono text-xs text-muted-foreground">{fmt(duration)}</span>
      </div>
      {selectedEvent && (
        <div className="mt-1.5 flex items-center gap-3 rounded-sm border border-border bg-surface-2 px-2 py-1 text-[11px]">
          <span className="text-mono text-muted-foreground">{fmt(selectedEvent.t)}</span>
          <span className="font-semibold" style={{ color: eventColor[selectedEvent.kind] }}>{selectedEvent.label}</span>
          <span className="ml-auto text-muted-foreground">click marker → seek + map sync</span>
          <button onClick={() => onSelectEvent?.(null)} className="text-muted-foreground hover:text-foreground">×</button>
        </div>
      )}
    </div>
  );
}

/* =========================================================================
   VIDEO + MAP PREVIEWS (shared across tabs)
   ========================================================================= */

function VideoPreview(props: {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  videoUrl: string;
  cropLeft: number; cropRight: number; cropTop: number; cropBottom: number;
  visibleAspect: number; visibleW: number; visibleH: number;
  time: number; duration: number;
  showCameraBbox: boolean;
  videoLoaded: "loading" | "loaded" | "error";
  syncMapVideo: boolean;
  overlays?: VideoOverlays;
  onOverlaysChange?: (v: VideoOverlays) => void;
  onOpenSourceSettings?: () => void;
}) {
  const loadTone =
    props.videoLoaded === "loaded" ? "text-emerald-400"
      : props.videoLoaded === "error" ? "text-destructive"
      : "text-amber-400";
  const loadLabel =
    props.videoLoaded === "loaded" ? "video loaded"
      : props.videoLoaded === "error" ? "no video"
      : "loading";
  const hasVideo = !!props.videoUrl && props.videoLoaded !== "error";
  const ov = props.overlays ?? { showCrop: true, showHud: false, showDetected: true, showMinimap: false };
  const toggleOv = (k: keyof VideoOverlays) =>
    props.onOverlaysChange?.({ ...ov, [k]: !ov[k] });
  return (
    <div className="hud-panel relative flex min-h-0 flex-col overflow-hidden bg-black">
      <div className="flex items-center justify-between border-b border-border bg-surface px-3 py-1.5">
        <div className="label-eyebrow text-xs">
          Observer · crop L{props.cropLeft} R{props.cropRight} T{props.cropTop} B{props.cropBottom}
        </div>
        <div className="flex items-center gap-3 text-mono text-xs text-muted-foreground">
          <span className={loadTone}>● {loadLabel}</span>
          {props.syncMapVideo && <span className="text-primary">sync</span>}
          <span>{fmt(props.time)} / {fmt(props.duration)}</span>
        </div>
      </div>
      {props.onOverlaysChange && (
        <div className="flex flex-wrap items-center gap-1 border-b border-border bg-surface/60 px-2 py-1">
          <ToggleBtn active={ov.showCrop} onClick={() => toggleOv("showCrop")}>Show crop</ToggleBtn>
          <ToggleBtn active={ov.showHud} onClick={() => toggleOv("showHud")}>Show HUD zones</ToggleBtn>
          <ToggleBtn active={ov.showDetected} onClick={() => toggleOv("showDetected")}>Show detected frame</ToggleBtn>
          <ToggleBtn active={ov.showMinimap} onClick={() => toggleOv("showMinimap")}>Show minimap crop</ToggleBtn>
        </div>
      )}
      <div className="relative flex flex-1 items-center justify-center overflow-hidden bg-black p-2">
        {!hasVideo ? (
          <div className="flex max-w-md flex-col items-center gap-3 text-center">
            <div className="rounded-full border border-border bg-surface-2 px-3 py-1 text-mono text-[11px] uppercase tracking-wider text-muted-foreground">
              ● no video
            </div>
            <div className="text-sm font-semibold text-foreground">Видео не загружено</div>
            <div className="text-xs text-muted-foreground">
              Укажите Video URL или загрузите файл в настройках Source.
            </div>
            {props.onOpenSourceSettings && (
              <button onClick={props.onOpenSourceSettings}
                className="mt-1 rounded-sm bg-primary px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110">
                Open Source Settings
              </button>
            )}
          </div>
        ) : (
        <div
          className={`relative overflow-hidden bg-black ${ov.showCrop ? "border border-primary/40" : ""}`}
          style={{ aspectRatio: `${props.visibleAspect}`, maxWidth: "100%", maxHeight: "100%", width: "auto", height: "100%" }}
        >
          <video
            ref={props.videoRef}
            src={props.videoUrl}
            className="absolute"
            style={{
              width: `${(SRC_W / props.visibleW) * 100}%`,
              height: `${(SRC_H / props.visibleH) * 100}%`,
              left: `${-(props.cropLeft / props.visibleW) * 100}%`,
              top: `${-(props.cropTop / props.visibleH) * 100}%`,
              maxWidth: "none",
            }}
            playsInline preload="metadata" crossOrigin="anonymous"
          />
          {props.showCameraBbox && ov.showDetected && (
            <div className="pointer-events-none absolute inset-0 border-2 border-dashed border-emerald-400/70" />
          )}
          {ov.showHud && (
            <>
              <div className="pointer-events-none absolute inset-x-0 top-0 h-[12%] border-b border-amber-400/40 bg-amber-400/5" />
              <div className="pointer-events-none absolute inset-x-0 bottom-0 h-[14%] border-t border-amber-400/40 bg-amber-400/5" />
              <div className="pointer-events-none absolute left-1 top-1 text-xs font-semibold uppercase tracking-wider text-amber-400/80">HUD top</div>
              <div className="pointer-events-none absolute bottom-1 left-1 text-xs font-semibold uppercase tracking-wider text-amber-400/80">HUD bottom</div>
            </>
          )}
          {ov.showMinimap && (
            <div className="pointer-events-none absolute right-2 top-2 h-[18%] w-[14%] border border-cyan-400/60 bg-cyan-400/5">
              <div className="absolute left-1 top-0.5 text-[9px] font-semibold uppercase tracking-wider text-cyan-300">minimap</div>
            </div>
          )}
        </div>
        )}
      </div>
    </div>
  );
}

function MapPreview(props: {
  map: { id: string; name: string; image: string };
  mapRef: React.RefObject<HTMLDivElement | null>;
  viewport: Viewport; setViewport: (v: Viewport) => void;
  vpDrag: null | { kind: "move" | "resize"; startX: number; startY: number; v: Viewport };
  setVpDrag: (v: any) => void;
  ring: RingPhase;
  teamPositions: { id: string; tag: string; slotIdx: number; x: number; y: number; isDead: boolean }[];
  splitOpts: SplitOpts;
  onFit: () => void; onReset: () => void;
}) {
  const mapWrapRef = useRef<HTMLDivElement | null>(null);
  const [mapView, setMapView] = useState({ scale: 1, tx: 0, ty: 0 });
  const mapPan = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const clampScale = (s: number) => Math.max(1, Math.min(6, s));
  const clampPan = (v: { scale: number; tx: number; ty: number }, w: number, h: number) => {
    const minX = w - w * v.scale;
    const minY = h - h * v.scale;
    return { scale: v.scale, tx: Math.min(0, Math.max(minX, v.tx)), ty: Math.min(0, Math.max(minY, v.ty)) };
  };
  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const rect = mapWrapRef.current!.getBoundingClientRect();
    const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
    setMapView((v) => {
      const ns = clampScale(v.scale * Math.exp(-e.deltaY * 0.0015));
      const k = ns / v.scale;
      return clampPan({ scale: ns, tx: cx - k * (cx - v.tx), ty: cy - k * (cy - v.ty) }, rect.width, rect.height);
    });
  };
  const onMouseDown = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest("[data-vp-handle]")) return;
    mapPan.current = { x: e.clientX, y: e.clientY, tx: mapView.tx, ty: mapView.ty };
  };
  const onMouseMove = (e: React.MouseEvent) => {
    if (!mapPan.current) return;
    const rect = mapWrapRef.current!.getBoundingClientRect();
    const nx = mapPan.current.tx + (e.clientX - mapPan.current.x);
    const ny = mapPan.current.ty + (e.clientY - mapPan.current.y);
    setMapView((v) => clampPan({ scale: v.scale, tx: nx, ty: ny }, rect.width, rect.height));
  };
  const onMouseUp = () => { mapPan.current = null; };
  const zoomBy = (factor: number) => {
    const rect = mapWrapRef.current!.getBoundingClientRect();
    const cx = rect.width / 2, cy = rect.height / 2;
    setMapView((v) => {
      const ns = clampScale(v.scale * factor);
      const k = ns / v.scale;
      return clampPan({ scale: ns, tx: cx - k * (cx - v.tx), ty: cy - k * (cy - v.ty) }, rect.width, rect.height);
    });
  };
  const resetView = () => setMapView({ scale: 1, tx: 0, ty: 0 });

  const { ring, viewport, splitOpts } = props;

  return (
    <div className="hud-panel relative flex min-h-0 flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border bg-surface px-3 py-1.5">
        <div className="label-eyebrow text-xs">Map · {props.map.name}</div>
        <div className="text-mono text-xs text-muted-foreground">
          zoom {(mapView.scale * 100).toFixed(0)}% · viewport {(viewport.size * 100).toFixed(0)}%
        </div>
      </div>
      <div
        ref={mapWrapRef}
        onWheel={onWheel}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        className="relative min-h-0 flex-1 overflow-hidden bg-background hud-grid-bg select-none"
        style={{ cursor: mapPan.current ? "grabbing" : "grab" }}
      >
        <div className="absolute inset-0 origin-top-left"
          style={{ transform: `translate(${mapView.tx}px, ${mapView.ty}px) scale(${mapView.scale})` }}>
          <div ref={props.mapRef} className="relative h-full w-full">
            <img src={props.map.image} alt={props.map.name} draggable={false}
              className="absolute inset-0 h-full w-full object-contain opacity-95" />
            <svg viewBox="0 0 1000 1000" preserveAspectRatio="xMidYMid meet"
              className="pointer-events-none absolute inset-0 h-full w-full">
              <defs>
                <filter id="cam-glow">
                  <feGaussianBlur stdDeviation="2.5" result="b" />
                  <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
                </filter>
                <clipPath id="cam-map-bounds">
                  <rect x="0" y="0" width="1000" height="1000" />
                </clipPath>
              </defs>
              <g clipPath="url(#cam-map-bounds)">
                <path
                  d={`M0,0 H1000 V1000 H0 Z M ${ring.cx * 1000},${(ring.cy * 1000) - ring.r * 1000} a ${ring.r * 1000},${ring.r * 1000} 0 1,0 0,${ring.r * 2000} a ${ring.r * 1000},${ring.r * 1000} 0 1,0 0,${-ring.r * 2000} Z`}
                  fillRule="evenodd" fill="rgba(239,68,68,0.28)" stroke="none" />
                {ringPhases.map((p, i) => (
                  <circle key={`prev-${i}`} cx={p.cx * 1000} cy={p.cy * 1000} r={p.r * 1000}
                    fill="none" stroke="rgba(255,255,255,0.85)"
                    strokeWidth={1.6 / mapView.scale}
                    strokeDasharray={`${4 / mapView.scale} ${4 / mapView.scale}`} />
                ))}
                <circle cx={ring.cx * 1000} cy={ring.cy * 1000} r={ring.r * 1000}
                  fill="rgba(34,196,245,0.08)" stroke="#22c4f5"
                  strokeWidth={3.5 / mapView.scale}
                  strokeDasharray={`${10 / mapView.scale} ${5 / mapView.scale}`} />
                {splitOpts.showRingCenter && (
                  <circle cx={ring.cx * 1000} cy={ring.cy * 1000} r={3 / mapView.scale} fill="#22c4f5" />
                )}
              </g>
              {props.teamPositions.map((t) => {
                const slot = getSlotColor(t.slotIdx);
                const labelW = t.tag.length * 7 + 6;
                const labelH = 14;
                return (
                  <g key={t.id} opacity={t.isDead ? 0.55 : 1}>
                    {t.isDead ? (
                      <g transform={`translate(${t.x * 1000} ${t.y * 1000})`}>
                        <circle r={9 / mapView.scale} fill="none" stroke={slot} strokeWidth={2 / mapView.scale} opacity={0.9} />
                        <circle r={5.5 / mapView.scale} fill="#6b7280" stroke="rgba(0,0,0,0.85)" strokeWidth={1 / mapView.scale} />
                        <path d={`M${-3 / mapView.scale},${-3 / mapView.scale} L${3 / mapView.scale},${3 / mapView.scale} M${3 / mapView.scale},${-3 / mapView.scale} L${-3 / mapView.scale},${3 / mapView.scale}`}
                          stroke="#fff" strokeWidth={1.4 / mapView.scale} strokeLinecap="round" />
                      </g>
                    ) : (
                      <g filter="url(#cam-glow)">
                        <circle cx={t.x * 1000} cy={t.y * 1000} r={11 / mapView.scale}
                          fill="none" stroke={slot} strokeWidth={1 / mapView.scale} opacity={0.5} />
                        <circle cx={t.x * 1000} cy={t.y * 1000} r={6 / mapView.scale}
                          fill={slot} stroke="rgba(0,0,0,0.8)" strokeWidth={1 / mapView.scale} />
                      </g>
                    )}
                    <g transform={`translate(${t.x * 1000 + 14 / mapView.scale} ${t.y * 1000 - (labelH / 2) / mapView.scale})`}>
                      <rect x={0} y={0}
                        width={labelW / mapView.scale} height={labelH / mapView.scale}
                        rx={3 / mapView.scale} ry={3 / mapView.scale}
                        fill="rgba(0,0,0,0.7)"
                        stroke={t.isDead ? "#9ca3af" : slot}
                        strokeWidth={2 / mapView.scale}
                        strokeDasharray={t.isDead ? `${3 / mapView.scale} ${2 / mapView.scale}` : undefined} />
                      <text x={(labelW / 2) / mapView.scale} y={(labelH * 0.72) / mapView.scale}
                        textAnchor="middle" fontSize={11 / mapView.scale} fontWeight={800}
                        fill={t.isDead ? "#d1d5db" : "#fff"} fontFamily="Manrope, sans-serif">{t.tag}</text>
                    </g>
                  </g>
                );
              })}
            </svg>
            <div
              data-vp-handle
              className={`absolute border-2 border-primary ${splitOpts.lockZoom ? "" : "cursor-move"}`}
              style={{
                left: `${viewport.x * 100}%`, top: `${viewport.y * 100}%`,
                width: `${viewport.size * 100}%`, height: `${viewport.size * 100}%`,
                boxShadow: "0 0 0 9999px rgba(0,0,0,0.35) inset",
              }}
              onMouseDown={(e) => { if (!splitOpts.lockZoom) { e.stopPropagation(); props.setVpDrag({ kind: "move", startX: e.clientX, startY: e.clientY, v: viewport }); } }}
            >
              {!splitOpts.lockZoom && (
                <div data-vp-handle className="absolute -bottom-1 -right-1 h-3 w-3 cursor-nwse-resize border border-primary bg-background"
                  onMouseDown={(e) => { e.stopPropagation(); props.setVpDrag({ kind: "resize", startX: e.clientX, startY: e.clientY, v: viewport }); }} />
              )}
            </div>
          </div>
        </div>
        <div className="pointer-events-auto absolute right-3 bottom-3 hud-panel-strong flex flex-col overflow-hidden text-xs">
          <button onClick={() => zoomBy(1.5)} className="flex h-7 w-7 items-center justify-center border-b border-border hover:bg-muted" aria-label="Zoom in">+</button>
          <button onClick={() => zoomBy(1 / 1.5)} className="flex h-7 w-7 items-center justify-center border-b border-border hover:bg-muted" aria-label="Zoom out">−</button>
          <button onClick={resetView} className="text-mono flex h-7 w-7 items-center justify-center border-b border-border hover:bg-muted" aria-label="Reset zoom">1:1</button>
          <button onClick={props.onFit} className="flex h-7 w-7 items-center justify-center text-xs hover:bg-muted" title="Fit map" aria-label="Fit">⤢</button>
        </div>
      </div>
    </div>
  );
}

/* =========================================================================
   GRAPHS TAB
   ========================================================================= */

function GraphsTab(props: {
  time: number; duration: number; onSeek: (t: number) => void;
  events: TrackEvent[]; showOriginal: boolean; onToggleOriginal: () => void;
  seriesVis: SeriesVisibility;
  onSelectEvent: (e: TrackEvent | null) => void;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-auto bg-background">
      <div className="flex flex-wrap items-center gap-4 border-b border-border bg-surface px-3 py-1.5 text-xs text-muted-foreground">
        {props.seriesVis.smoothed && <div className="flex items-center gap-1"><span className="h-2 w-3 bg-emerald-400" />smoothed</div>}
        {props.seriesVis.raw && <div className="flex items-center gap-1"><span className="h-2 w-3 bg-zinc-400" />raw</div>}
        <span className="mx-1 h-4 w-px bg-border" />
        {(Object.keys(eventColor) as TrackEvent["kind"][]).map((k) => (
          <div key={k} className="flex items-center gap-1">
            <span className="h-3 w-0.5" style={{ background: eventColor[k] }} />
            {eventLabel[k]}
          </div>
        ))}
        <span className="ml-auto" />
        <button onClick={props.onToggleOriginal}
          className={`rounded-sm border px-2 py-1 text-xs font-semibold uppercase tracking-wider transition-colors ${
            props.showOriginal ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-surface-2 text-muted-foreground hover:bg-muted"
          }`}>
          {props.showOriginal ? "Before / After" : "After only"}
        </button>
      </div>
      <div className="space-y-2 p-2">
        {[
          { key: "x",        label: "X: camera raw / smoothed", range: "484.7 … 827.0", seed: 1 },
          { key: "y",        label: "Y: camera raw / smoothed", range: "334.0 … 661.0", seed: 2 },
          { key: "zoom",     label: "Zoom ratio · effective",   range: "-0.1 … 2.1",    seed: 3 },
          { key: "radius",   label: "Ring radius · zoomedRadius", range: "139.3 … 562.7", seed: 4 },
          { key: "ring",     label: "Ring number",              range: "0 … 8",         seed: 5 },
          { key: "move",     label: "moveDist · jumpScore",     range: "-45.6 … 805.3", seed: 6 },
          { key: "conf",     label: "Confidence",               range: "0 … 1",         seed: 7 },
        ].map((lane) => (
          <div key={lane.key} className="rounded-sm border border-border bg-surface">
            <ChartLanes lanes={[lane]} height={90} time={props.time} duration={props.duration}
              onSeek={props.onSeek} events={props.events} showOriginal={props.showOriginal && props.seriesVis.raw}
              showSmoothed={props.seriesVis.smoothed}
              onSelectEvent={props.onSelectEvent} />
          </div>
        ))}
      </div>
    </div>
  );
}

/* =========================================================================
   SETTINGS TAB content (preview area)
   ========================================================================= */

function SettingsTabContent(props: { videoPreview: React.ReactNode; mapPreview: React.ReactNode }) {
  return (
    <div className="grid min-h-0 flex-1 grid-cols-2 gap-3 p-3">
      {props.videoPreview}
      {props.mapPreview}
    </div>
  );
}

/* =========================================================================
   DEBUG TAB
   ========================================================================= */

function DebugTab(props: {
  settings: TrackingSettings; viewport: Viewport;
  quality: { trackingQ: number; jumpEvents: number; lostFrames: number; avgConfidence: number };
  events: TrackEvent[]; time: number;
  selectedDebugFile: string;
  videoPreview: React.ReactNode;
}) {
  return (
    <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)_minmax(0,1fr)] gap-3 overflow-auto p-3">
      <div className="grid min-h-0 grid-cols-2 gap-3">
        <DebugBlock title="Current frame debug">
          <div className="grid h-full grid-cols-2 gap-2">
            <div className="min-h-[160px]">{props.videoPreview}</div>
            <div className="hud-panel flex min-h-[160px] flex-col p-3 text-xs">
              <div className="label-eyebrow mb-2">Detected camera bbox</div>
              <dl className="text-mono grid grid-cols-2 gap-y-1">
                <dt className="text-muted-foreground">vp x</dt><dd>{props.viewport.x.toFixed(3)}</dd>
                <dt className="text-muted-foreground">vp y</dt><dd>{props.viewport.y.toFixed(3)}</dd>
                <dt className="text-muted-foreground">vp size</dt><dd>{props.viewport.size.toFixed(3)}</dd>
                <dt className="text-muted-foreground">frame</dt><dd>{Math.round(props.time * props.settings.frameRate)}</dd>
              </dl>
            </div>
          </div>
        </DebugBlock>
        <DebugBlock title="Event log">
          <div className="max-h-[320px] overflow-auto">
            <table className="text-mono w-full text-xs">
              <thead className="sticky top-0 bg-surface text-muted-foreground">
                <tr>
                  <th className="px-2 py-1 text-left">time</th>
                  <th className="px-2 py-1 text-left">type</th>
                  <th className="px-2 py-1 text-left">message</th>
                  <th className="px-2 py-1 text-left">conf</th>
                  <th className="px-2 py-1 text-left">action</th>
                </tr>
              </thead>
              <tbody>
                {props.events.map((e, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="px-2 py-1">{fmt(e.t)}</td>
                    <td className="px-2 py-1" style={{ color: eventColor[e.kind] }}>{e.kind}</td>
                    <td className="px-2 py-1">{e.label}</td>
                    <td className="px-2 py-1">{(0.4 + Math.random() * 0.5).toFixed(2)}</td>
                    <td className="px-2 py-1 text-muted-foreground">log</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DebugBlock>
      </div>
      <div className="grid min-h-0 grid-cols-2 gap-3">
        <DebugBlock title="Top candidates / rejected">
          <table className="text-mono w-full text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="px-2 py-1 text-left">center</th>
                <th className="px-2 py-1 text-left">score</th>
                <th className="px-2 py-1 text-left">conf</th>
                <th className="px-2 py-1 text-left">reason</th>
              </tr>
            </thead>
            <tbody>
              {[
                { c: "(0.41, 0.38)", s: 0.82, k: 0.71, r: "accepted" },
                { c: "(0.42, 0.39)", s: 0.74, k: 0.62, r: "below relock" },
                { c: "(0.38, 0.40)", s: 0.55, k: 0.41, r: "ring penalty" },
                { c: "(0.10, 0.15)", s: 0.49, k: 0.36, r: "outside ring" },
                { c: "(0.62, 0.71)", s: 0.31, k: 0.20, r: "low conf" },
              ].map((row, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="px-2 py-1">{row.c}</td>
                  <td className="px-2 py-1">{row.s.toFixed(2)}</td>
                  <td className="px-2 py-1">{row.k.toFixed(2)}</td>
                  <td className="px-2 py-1 text-muted-foreground">{row.r}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </DebugBlock>
        <DebugBlock title={`Raw JSON · ${props.selectedDebugFile}`}>
          <pre className="text-mono max-h-[260px] overflow-auto text-xs leading-relaxed">
{JSON.stringify({
  timestamp: Number(props.time.toFixed(3)),
  frame: Math.round(props.time * props.settings.frameRate),
  viewport: props.viewport,
  quality: props.quality,
  settings: props.settings,
}, null, 2)}
          </pre>
        </DebugBlock>
      </div>
    </div>
  );
}

function DebugBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="hud-panel flex min-h-0 flex-col overflow-hidden">
      <div className="border-b border-border bg-surface px-3 py-1.5">
        <span className="label-eyebrow text-xs">{title}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2">{children}</div>
    </div>
  );
}

/* =========================================================================
   RIGHT PANEL — switches by viewMode
   ========================================================================= */

function RightPanel(props: {
  viewMode: ViewMode;
  active: Preset;
  presets: Preset[]; activePresetId: string;
  onApplyPreset: (id: string) => void;
  onUpdateCommit: () => void;
  onSaveAs: () => void;
  onUpdateActivePreset: () => void;
  onDuplicatePreset: () => void;
  onDeletePreset: () => void;
  onResetToDefault: () => void;
  onResetDraftToActive: () => void;
  isDirty: boolean;
  quality: { trackingQ: number; jumpEvents: number; lostFrames: number; avgConfidence: number };
  prevQuality?: QualityMetrics | null;
  problems: TrackEvent[];
  onSeek: (t: number) => void;
  draft: TrackingSettings; patchDraft: (p: Partial<TrackingSettings>) => void;
  committed: TrackingSettings;
  graphPreset: typeof GRAPH_PRESETS[number]; setGraphPreset: (v: typeof GRAPH_PRESETS[number]) => void;
  seriesVis: SeriesVisibility; setSeriesVis: (v: SeriesVisibility) => void;
  selectedEvent: TrackEvent | null;
  selectedDebugFile: string; setSelectedDebugFile: (v: string) => void;
  time: number;
  eventFilters: EventFilters; setEventFilters: (v: EventFilters) => void;
}) {
  const wide = props.viewMode === "settings";
  return (
    <aside className={`${wide ? "w-[400px]" : "w-80"} shrink-0 overflow-auto border-l border-border bg-surface`}>
      <div className="border-b border-border px-4 py-3">
        <div className="label-eyebrow text-xs">Camera tracking settings</div>
        <div className="mt-0.5 text-xs text-muted-foreground">
          preset · <span className="text-foreground">{props.active.name}</span>
          {props.isDirty && <span className="ml-2 text-amber-400">· edited</span>}
        </div>
      </div>

      {props.viewMode === "overview" && <OverviewPanel {...props} />}
      {props.viewMode === "graphs"   && <GraphsPanel {...props} />}
      {props.viewMode === "settings" && <SettingsPanel {...props} />}
      {props.viewMode === "debug"    && <DebugPanel {...props} />}
    </aside>
  );
}

/* ---- Overview right panel ---- */
function OverviewPanel(props: Parameters<typeof RightPanel>[0]) {
  const { draft, patchDraft } = props;
  const prev = props.prevQuality;
  return (
    <div className="space-y-3 p-3">
      <Section title="Quick tuning">
        <Field label="Current preset">
          <select value={props.activePresetId} onChange={(e) => props.onApplyPreset(e.target.value)}
            className="w-full rounded-sm border border-border bg-background px-2 py-1.5 text-xs">
            {props.presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          {PRESET_DESCRIPTIONS[props.active.name] && (
            <div className="mt-1.5 rounded-sm border border-border bg-surface px-2 py-1.5 text-[11px] leading-snug text-muted-foreground">
              {PRESET_DESCRIPTIONS[props.active.name]}
            </div>
          )}
          <PresetPipeline isDirty={props.isDirty} hasPrev={!!prev} />
        </Field>
        <SliderField label="Smoothing" value={draft.smoothing} min={0} max={1} step={0.01}
          hint={HINTS.smoothing} warn={getWarn("smoothing", draft.smoothing)}
          onChange={(v) => patchDraft({ smoothing: v })} />
        <SliderField label="Response speed" value={draft.responseSpeed} min={0} max={1} step={0.01}
          hint={HINTS.responseSpeed} warn={getWarn("responseSpeed", draft.responseSpeed)}
          onChange={(v) => patchDraft({ responseSpeed: v })} />
        <NumField label="Deadzone (px)" value={draft.deadzone} min={0} max={200} step={1}
          hint={HINTS.deadzone} warn={getWarn("deadzone", draft.deadzone)}
          onChange={(v) => patchDraft({ deadzone: v })} />
        <NumField label="Max speed (px/frame)" value={draft.maxSpeed} min={1} max={500} step={1}
          hint={HINTS.maxSpeed} warn={getWarn("maxSpeed", draft.maxSpeed)}
          onChange={(v) => patchDraft({ maxSpeed: v })} />
        <PresetActions {...props} />
      </Section>

      <Section title="Tracking health">
        <div className="text-mono space-y-1 text-xs">
          <Row k="Tracking quality" v={`${props.quality.trackingQ}%`} />
          <Row k="Jump events" v={props.quality.jumpEvents.toString()} />
          <Row k="Lost frames" v={props.quality.lostFrames.toString()} />
          <Row k="Avg confidence" v={props.quality.avgConfidence.toFixed(2)} />
          <Row k="Current mode" v={props.committed.stepZoomEnabled ? "Step zoom" : "Smooth zoom"} />
        </div>
      </Section>

      <Section title="Compare with previous">
        {prev ? (
          <div className="text-mono space-y-1 text-xs">
            <CompareRow k="Tracking quality" cur={`${props.quality.trackingQ}%`} prev={`${prev.trackingQ}%`} delta={props.quality.trackingQ - prev.trackingQ} suffix="%" />
            <CompareRow k="Jump events" cur={props.quality.jumpEvents.toString()} prev={prev.jumpEvents.toString()} delta={props.quality.jumpEvents - prev.jumpEvents} lowerBetter />
            <CompareRow k="Lost frames" cur={props.quality.lostFrames.toString()} prev={prev.lostFrames.toString()} delta={props.quality.lostFrames - prev.lostFrames} lowerBetter />
            <CompareRow k="Avg confidence" cur={props.quality.avgConfidence.toFixed(2)} prev={prev.avgConfidence.toFixed(2)} delta={props.quality.avgConfidence - prev.avgConfidence} decimals={2} />
          </div>
        ) : (
          <div className="text-xs text-muted-foreground">
            Нет предыдущего снимка. Нажмите <span className="text-foreground">Apply</span>, измените настройки и нажмите ещё раз — здесь появится сравнение.
          </div>
        )}
      </Section>

      <Section title={`Problems detected (${props.problems.length})`}>
        <ul className="text-mono max-h-[220px] divide-y divide-border overflow-auto text-xs">
          {props.problems.map((p, i) => (
            <li key={i}>
              <button onClick={() => props.onSeek(p.t)}
                className="flex w-full items-center justify-between px-2 py-1.5 text-left hover:bg-muted">
                <span className="text-muted-foreground">{fmt(p.t)}</span>
                <span style={{ color: eventColor[p.kind] }}>{p.label}</span>
              </button>
            </li>
          ))}
          {!props.problems.length && <li className="px-2 py-1.5 text-muted-foreground">none</li>}
        </ul>
      </Section>
    </div>
  );
}

/* ---- Graphs right panel ---- */
function GraphsPanel(props: Parameters<typeof RightPanel>[0]) {
  const { seriesVis, setSeriesVis } = props;
  const toggle = (k: keyof SeriesVisibility) => setSeriesVis({ ...seriesVis, [k]: !seriesVis[k] });
  return (
    <div className="space-y-3 p-3">
      <Section title="Graph presets">
        <div className="flex flex-wrap gap-1.5">
          {GRAPH_PRESETS.map((g) => (
            <button key={g} onClick={() => props.setGraphPreset(g)}
              className={`rounded-sm border px-2 py-1 text-xs font-semibold uppercase tracking-wider transition-colors ${
                g === props.graphPreset ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-surface-2 text-muted-foreground hover:bg-muted"
              }`}>{g}</button>
          ))}
        </div>
      </Section>
      <Section title="Series visibility">
        {(Object.keys(seriesVis) as Array<keyof SeriesVisibility>).map((k) => (
          <label key={k} className="flex cursor-pointer items-center gap-2 py-0.5 text-xs">
            <input type="checkbox" checked={seriesVis[k]} onChange={() => toggle(k)} className="accent-primary" />
            <span className="capitalize">{k.replace(/([A-Z])/g, " $1").trim()}</span>
          </label>
        ))}
      </Section>
      <Section title="Selected event">
        {props.selectedEvent ? (
          <dl className="text-mono grid grid-cols-2 gap-y-1 text-xs">
            <dt className="text-muted-foreground">timestamp</dt><dd>{fmt(props.selectedEvent.t)}</dd>
            <dt className="text-muted-foreground">type</dt><dd style={{ color: eventColor[props.selectedEvent.kind] }}>{props.selectedEvent.kind}</dd>
            <dt className="text-muted-foreground">label</dt><dd>{props.selectedEvent.label}</dd>
            <dt className="text-muted-foreground">reason</dt><dd className="text-muted-foreground">threshold exceeded</dd>
          </dl>
        ) : (
          <div className="text-xs text-muted-foreground">Click an event marker on a graph</div>
        )}
      </Section>
    </div>
  );
}

/* ---- Settings right panel: full accordion ---- */
function SettingsPanel(props: Parameters<typeof RightPanel>[0]) {
  const { draft, patchDraft } = props;
  return (
    <>
      <div className="space-y-1 p-3">
        <Collapsible title="Source" defaultOpen>
          <Field label="Video URL">
            <input value={draft.videoUrl} onChange={(e) => patchDraft({ videoUrl: e.target.value })}
              className="text-mono w-full rounded-sm border border-border bg-background px-2 py-1.5 text-xs"
              placeholder="https://…/observer.mp4" />
          </Field>
          <Field label="Upload video">
            <input type="file" accept="video/*" className="w-full text-xs"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) patchDraft({ videoUrl: URL.createObjectURL(f) }); }} />
          </Field>
          <Field label="Source type">
            <select value={draft.sourceType} onChange={(e) => patchDraft({ sourceType: e.target.value as TrackingSettings["sourceType"] })}
              className="w-full rounded-sm border border-border bg-background px-2 py-1.5 text-xs">
              <option value="vod">VOD stream</option>
              <option value="player_cam">Player cam</option>
              <option value="observer">Observer</option>
            </select>
          </Field>
          <NumField label="Frame rate" value={draft.frameRate} min={1} max={240} step={1}
            hint="Кадры в секунду исходного видео. Влияет на расчёт frame-based параметров."
            onChange={(v) => patchDraft({ frameRate: v })} />
        </Collapsible>

        <Collapsible title="Crop">
          <div className="grid grid-cols-2 gap-2">
            <NumField label="Crop L" value={draft.cropLeft} min={0} max={900} step={10} hint={HINTS.cropLeft} onChange={(v) => patchDraft({ cropLeft: v })} />
            <NumField label="Crop R" value={draft.cropRight} min={0} max={900} step={10} hint={HINTS.cropRight} onChange={(v) => patchDraft({ cropRight: v })} />
            <NumField label="Crop T" value={draft.cropTop} min={0} max={500} step={10} hint={HINTS.cropTop} onChange={(v) => patchDraft({ cropTop: v })} />
            <NumField label="Crop B" value={draft.cropBottom} min={0} max={500} step={10} hint={HINTS.cropBottom} onChange={(v) => patchDraft({ cropBottom: v })} />
          </div>
          <button onClick={() => patchDraft({ cropLeft: 0, cropRight: 0, cropTop: 0, cropBottom: 0 })}
            className="w-full rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs font-semibold uppercase tracking-wider hover:bg-muted">
            Reset crop
          </button>
        </Collapsible>

        <Collapsible title="Smoothing / response" defaultOpen>
          <SliderField label="Smoothing" value={draft.smoothing} min={0} max={1} step={0.01} hint={HINTS.smoothing} warn={getWarn("smoothing", draft.smoothing)} onChange={(v) => patchDraft({ smoothing: v })} />
          <SliderField label="Response speed" value={draft.responseSpeed} min={0} max={1} step={0.01} hint={HINTS.responseSpeed} warn={getWarn("responseSpeed", draft.responseSpeed)} onChange={(v) => patchDraft({ responseSpeed: v })} />
          <NumField label="Deadzone (px)" value={draft.deadzone} min={0} max={200} step={1} hint={HINTS.deadzone} warn={getWarn("deadzone", draft.deadzone)} onChange={(v) => patchDraft({ deadzone: v })} />
          <NumField label="Max speed (px/frame)" value={draft.maxSpeed} min={1} max={500} step={1} hint={HINTS.maxSpeed} warn={getWarn("maxSpeed", draft.maxSpeed)} onChange={(v) => patchDraft({ maxSpeed: v })} />
          <NumField label="EMA window (frames)" value={draft.ema} min={1} max={60} step={1} hint={HINTS.ema} onChange={(v) => patchDraft({ ema: v })} />
        </Collapsible>

        <Collapsible title="Zoom">
          <div className="grid grid-cols-2 gap-2">
            <NumField label="Zoom min" value={draft.zoomMin} min={0.5} max={3} step={0.05} onChange={(v) => patchDraft({ zoomMin: v })} />
            <NumField label="Zoom max" value={draft.zoomMax} min={1} max={5} step={0.05} onChange={(v) => patchDraft({ zoomMax: v })} />
          </div>
          <NumField label="Zoom step" value={draft.zoomStep} min={0} max={1} step={0.01} hint={HINTS.zoomStep} onChange={(v) => patchDraft({ zoomStep: v })} />
          <SliderField label="Zoom lerp" value={draft.zoomLerp} min={0} max={1} step={0.01} hint={HINTS.zoomLerp} onChange={(v) => patchDraft({ zoomLerp: v })} />
          <SliderField label="Zoom sensitivity" value={draft.zoomSensitivity} min={0} max={1} step={0.01} hint={HINTS.zoomSensitivity} onChange={(v) => patchDraft({ zoomSensitivity: v })} />
          <CheckField label="Step zoom enabled" checked={draft.stepZoomEnabled} onChange={(v) => patchDraft({ stepZoomEnabled: v })} />
        </Collapsible>

        <Collapsible title="Ring / team weighting">
          <SliderField label="Ring weight" value={draft.ringWeight} min={0} max={1} step={0.01} hint={HINTS.ringWeight} onChange={(v) => patchDraft({ ringWeight: v })} />
          <SliderField label="Team weight" value={draft.teamWeight} min={0} max={1} step={0.01} hint={HINTS.teamWeight} onChange={(v) => patchDraft({ teamWeight: v })} />
          <SliderField label="Ring noise tolerance" value={draft.ringNoiseTolerance} min={0} max={1} step={0.01} hint={HINTS.ringNoiseTolerance} onChange={(v) => patchDraft({ ringNoiseTolerance: v })} />
          <SliderField label="Team cluster tolerance" value={draft.teamClusterTolerance} min={0} max={1} step={0.01} hint={HINTS.teamClusterTolerance} onChange={(v) => patchDraft({ teamClusterTolerance: v })} />
          <CheckField label="Ring center lock" checked={draft.ringCenterLock} onChange={(v) => patchDraft({ ringCenterLock: v })} />
        </Collapsible>

        <Collapsible title="Jump detection">
          <NumField label="Jump threshold" value={draft.jumpThreshold} min={0} max={1000} step={5} hint={HINTS.jumpThreshold} warn={getWarn("jumpThreshold", draft.jumpThreshold)} onChange={(v) => patchDraft({ jumpThreshold: v })} />
          <NumField label="Jump cooldown (frames)" value={draft.jumpCooldownFrames} min={0} max={120} step={1} hint={HINTS.jumpCooldownFrames} onChange={(v) => patchDraft({ jumpCooldownFrames: v })} />
          <NumField label="Pre-jump unlock (s)" value={draft.preJumpUnlock} min={0} max={3} step={0.05} hint={HINTS.preJumpUnlock} onChange={(v) => patchDraft({ preJumpUnlock: v })} />
          <NumField label="Anti-latch tail (s)" value={draft.antiLatchTail} min={0} max={3} step={0.05} hint={HINTS.antiLatchTail} onChange={(v) => patchDraft({ antiLatchTail: v })} />
          <SliderField label="Relock threshold" value={draft.relockThreshold} min={0} max={1} step={0.01} hint={HINTS.relockThreshold} onChange={(v) => patchDraft({ relockThreshold: v })} />
        </Collapsible>

        <Collapsible title="Advanced">
          <NumField label="Sample step" value={draft.sampleStep} min={1} max={20} step={1} hint={HINTS.sampleStep} onChange={(v) => patchDraft({ sampleStep: v })} />
          <SliderField label="Confidence threshold" value={draft.confidenceThreshold} min={0} max={1} step={0.01} hint={HINTS.confidenceThreshold} onChange={(v) => patchDraft({ confidenceThreshold: v })} />
          <NumField label="Lost frame threshold" value={draft.lostFrameThreshold} min={1} max={60} step={1} hint={HINTS.lostFrameThreshold} onChange={(v) => patchDraft({ lostFrameThreshold: v })} />
          <CheckField label="Debug mode" checked={draft.debugMode} onChange={(v) => patchDraft({ debugMode: v })} />
          <CheckField label="Save debug frames" checked={draft.saveDebugFrames} onChange={(v) => patchDraft({ saveDebugFrames: v })} />
        </Collapsible>
      </div>

      <div className="sticky bottom-0 z-10 border-t border-border bg-surface/95 px-3 py-3 backdrop-blur">
        <div className="label-eyebrow mb-2 text-xs">Presets ({props.presets.length})</div>
        <div className="mb-3 flex flex-wrap gap-1.5">
          {props.presets.map((p) => (
            <button key={p.id} onClick={() => props.onApplyPreset(p.id)}
              className={`rounded-sm border px-2.5 py-1 text-xs font-semibold uppercase tracking-wider transition-colors ${
                p.id === props.activePresetId ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-surface-2 text-muted-foreground hover:bg-muted"
              }`}>
              {p.name}
            </button>
          ))}
        </div>
        {PRESET_DESCRIPTIONS[props.active.name] && (
          <div className="mb-2 rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-[11px] leading-snug text-muted-foreground">
            <span className="label-eyebrow mr-1 text-xs text-foreground">{props.active.name}</span>
            {PRESET_DESCRIPTIONS[props.active.name]}
          </div>
        )}
        <PresetPipeline isDirty={props.isDirty} hasPrev={!!props.prevQuality} />
        <PresetActions {...props} />
        <div className="mt-2 grid grid-cols-3 gap-1.5">
          <button onClick={props.onDuplicatePreset}
            className="rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider hover:bg-muted">Duplicate</button>
          <button onClick={props.onDeletePreset}
            className="rounded-sm border border-destructive/40 bg-surface-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider text-destructive hover:bg-destructive/10">Delete</button>
          <button onClick={props.onResetToDefault}
            className="rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider hover:bg-muted">Reset default</button>
        </div>
      </div>
    </>
  );
}

/* ---- Debug right panel ---- */
function DebugPanel(props: Parameters<typeof RightPanel>[0]) {
  const copy = (s: string) => navigator.clipboard?.writeText(s);
  return (
    <div className="space-y-3 p-3">
      <Section title="Tracker pipeline">
        <ul className="text-mono space-y-1 text-[11px]">
          {[
            { k: "ingest",   l: "Video ingest",        s: props.committed.videoUrl ? "ok" : "idle" },
            { k: "crop",     l: "Crop & rescale",      s: "ok" },
            { k: "detect",   l: "Frame detection",     s: "ok" },
            { k: "score",    l: "Candidate scoring",   s: props.quality.avgConfidence < 0.5 ? "warn" : "ok" },
            { k: "smooth",   l: "EMA smoothing",       s: "ok" },
            { k: "jump",     l: "Jump detection",      s: props.quality.jumpEvents > 5 ? "warn" : "ok" },
            { k: "output",   l: "Camera track output", s: "ok" },
          ].map((row) => (
            <li key={row.k} className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">{row.l}</span>
              <span className={
                row.s === "ok" ? "text-emerald-400" :
                row.s === "warn" ? "text-amber-400" :
                "text-muted-foreground"
              }>● {row.s}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Processing stats">
        <dl className="text-mono grid grid-cols-2 gap-y-1 text-xs">
          <dt className="text-muted-foreground">total frames</dt>
          <dd>{Math.max(60, Math.round((props.time || 1) * props.committed.frameRate)).toString()}</dd>
          <dt className="text-muted-foreground">sample step</dt><dd>{props.committed.sampleStep}</dd>
          <dt className="text-muted-foreground">fps in</dt><dd>{props.committed.frameRate}</dd>
          <dt className="text-muted-foreground">ema window</dt><dd>{props.committed.ema}</dd>
        </dl>
      </Section>

      <Section title="Debug mode">
        <CheckField label="Debug mode enabled" checked={props.draft.debugMode}
          onChange={(v) => props.patchDraft({ debugMode: v })} />
        <CheckField label="Save debug frames" checked={props.draft.saveDebugFrames}
          onChange={(v) => props.patchDraft({ saveDebugFrames: v })} />
        <PresetActions {...props} />
      </Section>

      <Section title="Selected timestamp">
        <div className="text-mono text-xs">
          {fmt(props.time)} · frame {Math.round(props.time * props.committed.frameRate)}
        </div>
      </Section>

      <Section title="Event filters">
        <div className="flex flex-wrap gap-1">
          {(Object.keys(props.eventFilters) as Array<TrackEvent["kind"]>).map((k) => (
            <button key={k} onClick={() => props.setEventFilters({ ...props.eventFilters, [k]: !props.eventFilters[k] })}
              className={`flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wider transition-colors ${
                props.eventFilters[k] ? "border-border bg-surface-2 text-foreground" : "border-border/50 bg-surface-2/40 text-muted-foreground/60 line-through"
              }`}>
              <span className="inline-block h-2 w-2 rounded-sm" style={{ background: eventColor[k] }} />{k}
            </button>
          ))}
        </div>
      </Section>

      <Section title="Debug files">
        <ul className="space-y-1 text-xs">
          {DEBUG_FILES.map((f) => (
            <li key={f.name}
              className={`rounded-sm border px-2 py-1.5 ${f.name === props.selectedDebugFile ? "border-primary/40 bg-primary/10" : "border-border bg-surface-2"}`}>
              <button onClick={() => props.setSelectedDebugFile(f.name)}
                className="text-mono mb-1 block w-full text-left">
                {f.name}
              </button>
              <div className="flex gap-1">
                <button onClick={() => window.open(f.path, "_blank")} className="rounded-sm border border-border bg-background px-1.5 py-0.5 text-xs hover:bg-muted">Open</button>
                <button className="rounded-sm border border-border bg-background px-1.5 py-0.5 text-xs hover:bg-muted">Download</button>
                <button onClick={() => copy(f.path)} className="rounded-sm border border-border bg-background px-1.5 py-0.5 text-xs hover:bg-muted">Copy path</button>
              </div>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Event log">
        <ul className="text-mono max-h-[180px] divide-y divide-border overflow-auto text-xs">
          {props.problems.map((p, i) => (
            <li key={i}>
              <button onClick={() => props.onSeek(p.t)}
                className="flex w-full items-center justify-between px-2 py-1 text-left hover:bg-muted">
                <span className="text-muted-foreground">{fmt(p.t)}</span>
                <span style={{ color: eventColor[p.kind] }}>{p.label}</span>
              </button>
            </li>
          ))}
        </ul>
      </Section>

      <button className="w-full rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider hover:bg-muted">
        Export
      </button>
    </div>
  );
}

/* =========================================================================
   Helpers
   ========================================================================= */

function UpdateActions({ onUpdate, onSaveAs, isDirty }: { onUpdate: () => void; onSaveAs: () => void; isDirty: boolean }) {
  return (
    <div className="mt-1 flex gap-2">
      <button onClick={onUpdate}
        className={`flex-1 rounded-sm px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition ${
          isDirty ? "bg-primary text-primary-foreground hover:brightness-110" : "bg-surface-2 text-muted-foreground"
        }`}>
        Update{isDirty ? " *" : ""}
      </button>
      <button onClick={onSaveAs}
        className="flex-1 rounded-sm border border-border bg-surface-2 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider hover:bg-muted">
        Save as…
      </button>
    </div>
  );
}

/* Apply (commit draft → committed) · Save (update active preset) · Save as · Reset (draft → active preset) */
function PresetActions(props: {
  onUpdateCommit: () => void;
  onUpdateActivePreset: () => void;
  onSaveAs: () => void;
  onResetDraftToActive: () => void;
  isDirty: boolean;
}) {
  return (
    <div className="mt-1 grid grid-cols-4 gap-1.5">
      <button onClick={props.onUpdateCommit} title="Применить настройки временно (без сохранения в пресет)"
        className={`rounded-sm px-2 py-1.5 text-xs font-semibold uppercase tracking-wider transition ${
          props.isDirty ? "bg-primary text-primary-foreground hover:brightness-110" : "bg-surface-2 text-muted-foreground"
        }`}>Apply{props.isDirty ? " *" : ""}</button>
      <button onClick={props.onUpdateActivePreset} title="Сохранить изменения в текущий пресет"
        className="rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider hover:bg-muted">Save</button>
      <button onClick={props.onSaveAs} title="Сохранить как новый пресет"
        className="rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider hover:bg-muted">Save as…</button>
      <button onClick={props.onResetDraftToActive} title="Вернуть значения текущего пресета"
        className="rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider hover:bg-muted">Reset</button>
    </div>
  );
}

/* Visual pipeline showing the preset workflow: select → edit → apply → recalc → save */
function PresetPipeline({ isDirty, hasPrev }: { isDirty: boolean; hasPrev: boolean }) {
  const steps = [
    { k: "select", label: "Select", on: true },
    { k: "edit",   label: "Edit",   on: isDirty },
    { k: "apply",  label: "Apply",  on: hasPrev || !isDirty },
    { k: "recalc", label: "Recalc", on: hasPrev },
    { k: "save",   label: "Save",   on: false },
  ];
  return (
    <div className="mt-2 flex items-center gap-1 rounded-sm border border-border bg-surface px-1.5 py-1 text-[9px] font-semibold uppercase tracking-wider">
      {steps.map((s, i) => (
        <span key={s.k} className="flex items-center gap-1">
          <span className={s.on ? "text-primary" : "text-muted-foreground/50"}>{s.label}</span>
          {i < steps.length - 1 && <span className="text-muted-foreground/40">→</span>}
        </span>
      ))}
    </div>
  );
}

function CompareRow({ k, cur, prev, delta, suffix = "", decimals = 0, lowerBetter = false }: {
  k: string; cur: string; prev: string; delta: number; suffix?: string; decimals?: number; lowerBetter?: boolean;
}) {
  const sign = delta > 0 ? "+" : "";
  const val = decimals ? delta.toFixed(decimals) : Math.round(delta).toString();
  const isZero = Math.abs(delta) < (decimals ? 0.005 : 0.5);
  const good = lowerBetter ? delta < 0 : delta > 0;
  const cls = isZero ? "text-muted-foreground" : good ? "text-emerald-400" : "text-destructive";
  return (
    <div className="flex items-baseline justify-between">
      <span className="text-muted-foreground">{k}</span>
      <span>
        <span className="text-muted-foreground/70 line-through mr-1">{prev}</span>
        <span>{cur}</span>
        {!isZero && <span className={`ml-1 ${cls}`}>{sign}{val}{suffix}</span>}
      </span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-sm border border-border bg-surface-2">
      <div className="border-b border-border px-3 py-1.5">
        <span className="label-eyebrow text-xs">{title}</span>
      </div>
      <div className="space-y-2 p-3">{children}</div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted-foreground">{k}</span>
      <span>{v}</span>
    </div>
  );
}

function Collapsible({ title, defaultOpen = false, children }: {
  title: string; defaultOpen?: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-sm border border-border bg-surface-2">
      <button onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-left hover:bg-muted">
        <span className="label-eyebrow text-xs">{title}</span>
        <span className={`text-xs text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`}>▶</span>
      </button>
      {open && <div className="space-y-2 border-t border-border px-3 py-2">{children}</div>}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="label-eyebrow mb-1 text-xs">{label}</div>{children}</div>;
}

function SliderField({ label, value, min, max, step, onChange, hint, warn }: { label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; hint?: string; warn?: string | null }) {
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-xs">
        <span className="uppercase tracking-wider text-muted-foreground" title={hint}>{label}{hint && <span className="ml-1 text-muted-foreground/50">ⓘ</span>}</span>
        <span className="text-mono">{value.toFixed(2)}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} className="w-full accent-primary" />
      {hint && <div className="mt-0.5 text-xs leading-tight text-muted-foreground/70">{hint}</div>}
      {warn && <div className="mt-1 rounded-sm border border-amber-400/40 bg-amber-400/10 px-1.5 py-0.5 text-xs text-amber-300">⚠ {warn}</div>}
    </div>
  );
}

function NumField({ label, value, min, max, step, onChange, hint, warn }: { label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; hint?: string; warn?: string | null }) {
  return (
    <div>
      <div className="label-eyebrow mb-1 text-xs" title={hint}>{label}{hint && <span className="ml-1 text-muted-foreground/50">ⓘ</span>}</div>
      <input type="number" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="text-mono w-full rounded-sm border border-border bg-background px-2 py-1 text-xs" />
      {hint && <div className="mt-0.5 text-xs leading-tight text-muted-foreground/70">{hint}</div>}
      {warn && <div className="mt-1 rounded-sm border border-amber-400/40 bg-amber-400/10 px-1.5 py-0.5 text-xs text-amber-300">⚠ {warn}</div>}
    </div>
  );
}

function CheckField({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-xs">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="accent-primary" />
      <span>{label}</span>
    </label>
  );
}

function fmt(s: number) {
  if (!isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60).toString().padStart(2, "0");
  return `${m}:${sec}`;
}

/* =========================================================================
   Chart lanes — single lane stack
   ========================================================================= */

type LaneSpec = { key: string; label: string; range: string; seed: number };

function ChartLanes({ lanes, height, time, duration, onSeek, events, showOriginal, showSmoothed, onSelectEvent }: {
  lanes: LaneSpec[]; height: number; time: number; duration: number;
  onSeek: (t: number) => void; events: TrackEvent[];
  showOriginal: boolean; showSmoothed?: boolean;
  onSelectEvent?: (e: TrackEvent | null) => void;
}) {
  const W = 1200, N = 600;
  const H = height;
  const showSm = showSmoothed !== false;

  const rand = (seed: number) => {
    let s = seed | 0;
    return () => { s = (s * 1664525 + 1013904223) | 0; return ((s >>> 0) % 100000) / 100000; };
  };
  const sharpSeries = (seed: number) => {
    const r = rand(seed * 9973 + 1);
    const vals: number[] = []; let v = 0;
    for (let i = 0; i < N; i++) {
      if (r() < 0.04) v = (r() - 0.5) * 1.6; else v += (r() - 0.5) * 0.35;
      v = Math.max(-1, Math.min(1, v * 0.96));
      vals.push(v);
    }
    return vals;
  };
  const path = (vals: number[]) =>
    vals.map((v, i) => `${i === 0 ? "M" : "L"}${(i / (N - 1)) * W},${H / 2 - v * (H / 2.2)}`).join(" ");

  const totalH = lanes.length * H;
  const [hover, setHover] = useState<{ x: number; t: number; ev?: TrackEvent } | null>(null);
  const eventAtX = (xFrac: number): TrackEvent | undefined => {
    const t = xFrac * duration;
    let best: TrackEvent | undefined; let bestD = Infinity;
    for (const ev of events) {
      const d = Math.abs(ev.t - t);
      if (d < bestD && d < duration * 0.012) { bestD = d; best = ev; }
    }
    return best;
  };
  const onMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const xFrac = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    setHover({ x: xFrac, t: xFrac * duration, ev: eventAtX(xFrac) });
  };
  const onClick = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const xFrac = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    const ev = eventAtX(xFrac);
    if (ev && onSelectEvent) onSelectEvent(ev);
    onSeek(xFrac * duration);
  };

  return (
    <div className="relative overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${totalH}`} className="block w-full"
        style={{ minHeight: totalH, shapeRendering: "crispEdges" }}
        onMouseMove={onMouseMove} onMouseLeave={() => setHover(null)} onClick={onClick}>
        {events.map((ev, i) => (
          <line key={`e-${i}`}
            x1={(ev.t / Math.max(duration, 0.001)) * W}
            x2={(ev.t / Math.max(duration, 0.001)) * W}
            y1={0} y2={totalH}
            stroke={eventColor[ev.kind]} strokeWidth={1} opacity={0.55} strokeDasharray="2 3" />
        ))}
        {lanes.map((l, idx) => {
          const a = sharpSeries(l.seed * 7 + 1);
          const b = sharpSeries(l.seed * 7 + 4).map((v) => v * 0.7);
          const y = idx * H;
          return (
            <g key={l.key} transform={`translate(0,${y})`}>
              <rect x={0} y={0} width={W} height={H} fill={idx % 2 === 0 ? "rgba(255,255,255,0.015)" : "rgba(255,255,255,0.03)"} />
              <line x1={0} y1={H} x2={W} y2={H} stroke="rgba(255,255,255,0.06)" />
              <text x={6} y={14} fill="rgba(255,255,255,0.7)" fontSize={10}>{l.label}</text>
              <text x={W - 6} y={14} textAnchor="end" fill="rgba(255,255,255,0.5)" fontSize={10}>{l.range}</text>
              {showOriginal && (
                <path d={path(b)} fill="none" stroke="#a1a1aa" strokeWidth={1} opacity={0.55} />
              )}
              {showSm && (
                <path d={path(a)} fill="none" stroke="#34d399" strokeWidth={1.2} opacity={0.95} />
              )}
            </g>
          );
        })}
        <line
          x1={(time / Math.max(duration, 0.001)) * W}
          x2={(time / Math.max(duration, 0.001)) * W}
          y1={0} y2={totalH}
          stroke="#fff" strokeWidth={1} strokeDasharray="3 3" opacity={0.7} />
        {hover && (
          <line x1={hover.x * W} x2={hover.x * W} y1={0} y2={totalH}
            stroke="#fff" strokeWidth={0.5} opacity={0.3} />
        )}
      </svg>
      {hover && (
        <div
          className="pointer-events-none absolute top-1 rounded-sm border border-border bg-surface px-2 py-1 text-xs text-foreground shadow-lg"
          style={{ left: `${hover.x * 100}%`, transform: "translateX(-50%)" }}
        >
          <span className="text-mono text-muted-foreground">{fmt(hover.t)}</span>
          {hover.ev && (
            <span className="ml-2 font-semibold" style={{ color: eventColor[hover.ev.kind] }}>{hover.ev.label}</span>
          )}
        </div>
      )}
    </div>
  );
}
