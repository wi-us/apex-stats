"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { formatMmSs } from "../../lib/match-viewer-utils";
import type { CameraTrackPoint } from "../../lib/types";
import type { CameraSmoothingTuning } from "../map-player";
import { deriveCameraFrames, type DerivedCameraTrackPoint } from "../map-player";
import styles from "./CameraTimelineCharts.module.css";

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

type PanelSeries = {
  key: string;
  label: string;
  traces: Array<{
    name: string;
    color: string;
    width?: number;
    dash?: number[];
    getter: (raw: CameraTrackPoint, sm: DerivedCameraTrackPoint) => number;
  }>;
};

const PANELS: PanelSeries[] = [
  {
    key: "x",
    label: "X: камера raw / камера см. · центр кольца",
    traces: [
      {
        name: "cam raw",
        color: "rgba(154,174,206,0.55)",
        width: 1.25,
        dash: [4, 3],
        getter: (raw) => Number(raw.cameraX ?? 540),
      },
      {
        name: "cam",
        color: "rgba(0,255,160,0.92)",
        width: 1.75,
        getter: (_, sm) => Number(sm.cameraX ?? 540),
      },
      {
        name: "ring",
        color: "rgba(255,170,60,0.78)",
        width: 1.25,
        getter: (raw) => Number(raw.centerX ?? 540),
      },
    ],
  },
  {
    key: "y",
    label: "Y: камера raw / камера см. · центр кольца",
    traces: [
      {
        name: "cam raw",
        color: "rgba(154,174,206,0.55)",
        width: 1.25,
        dash: [4, 3],
        getter: (raw) => Number(raw.cameraY ?? 540),
      },
      {
        name: "cam",
        color: "rgba(0,255,160,0.92)",
        width: 1.75,
        getter: (_, sm) => Number(sm.cameraY ?? 540),
      },
      {
        name: "ring",
        color: "rgba(255,170,60,0.78)",
        width: 1.25,
        getter: (raw) => Number(raw.centerY ?? 540),
      },
    ],
  },
  {
    key: "zoom",
    label: "Zoom ratio · effective (сайт)",
    traces: [
      {
        name: "raw z",
        color: "rgba(154,174,206,0.55)",
        width: 1.25,
        dash: [4, 3],
        getter: (raw) => Math.max(1, Number(raw.zoomRatio ?? 1)),
      },
      {
        name: "sm eff.",
        color: "rgba(120,200,255,0.92)",
        width: 1.75,
        getter: (_, sm) => Number(sm.effectiveZoom ?? sm.zoomRatio ?? 1),
      },
    ],
  },
  {
    key: "r",
    label: "Радиус кольца · zoomedRadius",
    traces: [
      {
        name: "R",
        color: "rgba(220,120,255,0.82)",
        width: 1.5,
        getter: (raw) => Math.max(0, Number(raw.radius ?? 0)),
      },
      {
        name: "zR",
        color: "rgba(255,200,110,0.85)",
        width: 1.5,
        dash: [5, 4],
        getter: (_, sm) => Math.max(0, Number(sm.zoomedRadius ?? 0)),
      },
    ],
  },
  {
    key: "ringNo",
    label: "Номер кольца",
    traces: [
      {
        name: "",
        color: "rgba(200,210,235,0.9)",
        width: 1.5,
        getter: (raw) => Number(raw.ringNumber ?? 1),
      },
    ],
  },
  {
    key: "aux",
    label: "moveDist · jumpScore",
    traces: [
      {
        name: "move",
        color: "rgba(100,230,190,0.88)",
        width: 1.5,
        getter: (raw) => Math.max(0, Number(raw.moveDist ?? 0)),
      },
      {
        name: "jump",
        color: "rgba(255,120,140,0.75)",
        width: 1.25,
        dash: [3, 3],
        getter: (raw) => Math.max(0, Number(raw.jumpScore ?? 0)),
      },
    ],
  },
];

type FirstMoveZoomEvent = {
  ringNumber: number;
  timestampSec: number;
  shiftPx: number;
  zoomDelta: number;
};

type DetectorPreset = {
  id: string;
  label: string;
  hint: string;
  shiftThresholdPx: number;
  zoomThresholdPermille: number;
  persistFrames: number;
  baselineFrames: number;
  ignoreStartFrames: number;
};

const DETECTOR_PRESETS: DetectorPreset[] = [
  {
    id: "step-zoom",
    label: "Step zoom",
    hint: "Чувствительный режим для резких ступеней zoom/pan: минимум устойчивости, короткая база.",
    shiftThresholdPx: 8,
    zoomThresholdPermille: 18,
    persistFrames: 1,
    baselineFrames: 8,
    ignoreStartFrames: 0,
  },
  {
    id: "ring-noise",
    label: "Шум кольца",
    hint: "Строже к шумному радиусу кольца: требует длительного устойчивого изменения.",
    shiftThresholdPx: 24,
    zoomThresholdPermille: 35,
    persistFrames: 6,
    baselineFrames: 30,
    ignoreStartFrames: 8,
  },
  {
    id: "balanced",
    label: "Баланс",
    hint: "Текущий умеренный профиль для обычного просмотра графика.",
    shiftThresholdPx: 18,
    zoomThresholdPermille: 45,
    persistFrames: 4,
    baselineFrames: 18,
    ignoreStartFrames: 5,
  },
  {
    id: "very-sensitive",
    label: "Макс. чувств.",
    hint: "Для поиска слабых кандидатов, может ловить шум.",
    shiftThresholdPx: 6,
    zoomThresholdPermille: 10,
    persistFrames: 1,
    baselineFrames: 5,
    ignoreStartFrames: 0,
  },
];

function mean(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((acc, v) => acc + v, 0) / values.length;
}

function Hint({ text }: { text: string }) {
  return (
    <span className={styles.paramHint} title={text} aria-label={text}>
      ⓘ
    </span>
  );
}

export type CameraTimelineChartsProps = {
  cameraTracks: CameraTrackPoint[];
  ringCameraNoiseByRing: Record<number, number>;
  ringNoiseSliderMaxByRing: Record<number, number>;
  cameraSmoothingTuning: CameraSmoothingTuning;
  currentTimeSec: number;
  onSeek?: (timestampSec: number) => void;
  onApplyTuningPreset?: (presetId: string) => void;
};

export function CameraTimelineCharts({
  cameraTracks,
  ringCameraNoiseByRing,
  ringNoiseSliderMaxByRing,
  cameraSmoothingTuning,
  currentTimeSec,
  onSeek,
  onApplyTuningPreset,
}: CameraTimelineChartsProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [shiftThresholdPx, setShiftThresholdPx] = useState(6);
  const [zoomThresholdPermille, setZoomThresholdPermille] = useState(10);
  const [persistFrames, setPersistFrames] = useState(1);
  const [baselineFrames, setBaselineFrames] = useState(5);
  const [ignoreStartFrames, setIgnoreStartFrames] = useState(0);

  const { sortedRaw, smoothed, tGlobMin, tGlobMax } = useMemo(() => {
    const sorted = [...cameraTracks].sort((a, b) => a.timestampSec - b.timestampSec);
    if (sorted.length === 0) {
      return { sortedRaw: [] as CameraTrackPoint[], smoothed: [] as DerivedCameraTrackPoint[], tGlobMin: 0, tGlobMax: 1 };
    }
    const sm = deriveCameraFrames(sorted, ringCameraNoiseByRing, ringNoiseSliderMaxByRing, cameraSmoothingTuning);
    const t0 = sorted[0]!.timestampSec;
    const t1 = sorted[sorted.length - 1]!.timestampSec;
    return { sortedRaw: sorted, smoothed: sm, tGlobMin: t0, tGlobMax: Math.max(t0 + 1e-6, t1) };
  }, [cameraSmoothingTuning, cameraTracks, ringCameraNoiseByRing, ringNoiseSliderMaxByRing]);

  const dataFinger = useMemo(
    () => `${sortedRaw.length}:${tGlobMin.toFixed(3)}:${tGlobMax.toFixed(3)}`,
    [sortedRaw.length, tGlobMin, tGlobMax],
  );

  const [view, setView] = useState<{ t0: number; t1: number }>(() => ({
    t0: tGlobMin,
    t1: tGlobMax,
  }));

  useEffect(() => {
    setView({ t0: tGlobMin, t1: tGlobMax });
  }, [dataFinger, tGlobMax, tGlobMin]);

  const viewRef = useRef(view);
  useEffect(() => {
    viewRef.current = view;
  }, [view]);

  const globRef = useRef({ tGlobMin, tGlobMax });
  useEffect(() => {
    globRef.current = { tGlobMin, tGlobMax };
  }, [tGlobMin, tGlobMax]);

  const pairs = useMemo(() => {
    const out: Array<{ t: number; raw: CameraTrackPoint; sm: DerivedCameraTrackPoint }> = [];
    const n = Math.min(sortedRaw.length, smoothed.length);
    for (let i = 0; i < n; i++) {
      out.push({ t: sortedRaw[i]!.timestampSec, raw: sortedRaw[i]!, sm: smoothed[i]! });
    }
    return out;
  }, [smoothed, sortedRaw]);

  const firstMoveZoomByRing = useMemo(() => {
    const SHIFT_THRESHOLD_PX = shiftThresholdPx;
    const ZOOM_THRESHOLD = zoomThresholdPermille / 1000;
    const PERSIST_FRAMES = persistFrames;
    const BASELINE_FRAMES = baselineFrames;
    const SKIP_FRAMES = ignoreStartFrames;
    const found: FirstMoveZoomEvent[] = [];

    const byRing = new Map<number, Array<{ t: number; raw: CameraTrackPoint; sm: DerivedCameraTrackPoint }>>();
    for (const p of pairs) {
      const ring = Number(p.raw.ringNumber ?? 0);
      if (!Number.isFinite(ring) || ring <= 0) continue;
      if (!byRing.has(ring)) byRing.set(ring, []);
      byRing.get(ring)!.push(p);
    }

    for (const [ring, rows] of byRing.entries()) {
      if (rows.length < 6) continue;
      const baseWindow = rows.slice(0, Math.min(BASELINE_FRAMES, rows.length));
      const baseX = mean(baseWindow.map((p) => Number(p.sm.cameraX ?? 540)));
      const baseY = mean(baseWindow.map((p) => Number(p.sm.cameraY ?? 540)));
      const baseZ = mean(baseWindow.map((p) => Number(p.sm.effectiveZoom ?? p.sm.zoomRatio ?? 1)));

      for (let i = SKIP_FRAMES; i < rows.length; i++) {
        const sustained = rows.slice(i, i + PERSIST_FRAMES);
        if (sustained.length < PERSIST_FRAMES) break;
        const isStableHit = sustained.every((row) => {
          const shiftPx = Math.hypot(Number(row.sm.cameraX ?? 540) - baseX, Number(row.sm.cameraY ?? 540) - baseY);
          const zoomDelta = Math.abs(Number(row.sm.effectiveZoom ?? row.sm.zoomRatio ?? 1) - baseZ);
          const shiftZoomHit = shiftPx >= SHIFT_THRESHOLD_PX && zoomDelta >= ZOOM_THRESHOLD;
          const zoomOnlyCountdownHit =
            String(row.raw.ringStatus ?? "countdown") === "countdown" &&
            zoomDelta >= Math.max(ZOOM_THRESHOLD * 2, 0.08);
          return shiftZoomHit || zoomOnlyCountdownHit;
        });
        if (!isStableHit) continue;
        const row = rows[i]!;
        found.push({
          ringNumber: ring,
          timestampSec: row.t,
          shiftPx: Math.hypot(Number(row.sm.cameraX ?? 540) - baseX, Number(row.sm.cameraY ?? 540) - baseY),
          zoomDelta: Math.abs(Number(row.sm.effectiveZoom ?? row.sm.zoomRatio ?? 1) - baseZ),
        });
        break;
      }
    }

    return found.sort((a, b) => a.timestampSec - b.timestampSec);
  }, [baselineFrames, ignoreStartFrames, pairs, persistFrames, shiftThresholdPx, zoomThresholdPermille]);

  const antiLatchStats = useMemo(() => {
    let xy = 0;
    let z = 0;
    for (const p of pairs) {
      if (p.sm.antiLatchXYTriggered) xy += 1;
      if (p.sm.antiLatchZoomTriggered) z += 1;
    }
    return { xy, z };
  }, [pairs]);

  const preJumpUnlockTs = useMemo(() => {
    const hit = pairs.find((p) => p.sm.preJumpLockReleased);
    return hit ? hit.t : null;
  }, [pairs]);

  useEffect(() => {
    if (pairs.length === 0) return;
    const focus = pairs.filter((p) => p.t >= 390 && p.t <= 430);
    const rawZoom = focus.map((p) => Number(p.raw.zoomRatio ?? 1)).filter(Number.isFinite);
    const effZoom = focus.map((p) => Number(p.sm.effectiveZoom ?? p.sm.zoomRatio ?? 1)).filter(Number.isFinite);
    const moveDist = focus.map((p) => Number(p.raw.moveDist ?? 0)).filter(Number.isFinite);
    let maxStepZoom = 0;
    let maxStepAt: number | null = null;
    for (let i = 1; i < focus.length; i++) {
      const prev = Math.max(1e-6, Number(focus[i - 1]!.raw.zoomRatio ?? 1));
      const curr = Math.max(1e-6, Number(focus[i]!.raw.zoomRatio ?? 1));
      const step = Math.abs(curr / prev - 1);
      if (step > maxStepZoom) {
        maxStepZoom = step;
        maxStepAt = focus[i]!.t;
      }
    }
    // #region agent log
    fetch('http://127.0.0.1:7664/ingest/0aa35fc0-93f5-4a7d-ae43-8a87e2b19087',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'b91ec1'},body:JSON.stringify({sessionId:'b91ec1',runId:'pre-fix',hypothesisId:'H1,H3,H4',location:'apps/web/components/broadcast/CameraTimelineCharts.tsx:chart_summary',message:'camera_chart_render_summary',data:{pairs:pairs.length,tMin:tGlobMin,tMax:tGlobMax,focusCount:focus.length,focusRawZoomMin:rawZoom.length?Math.min(...rawZoom):null,focusRawZoomMax:rawZoom.length?Math.max(...rawZoom):null,focusEffZoomMin:effZoom.length?Math.min(...effZoom):null,focusEffZoomMax:effZoom.length?Math.max(...effZoom):null,focusMoveMax:moveDist.length?Math.max(...moveDist):null,maxStepZoom,maxStepAt,firstMoveZoomByRing,preJumpUnlockTs,thresholds:{shiftThresholdPx,zoomThresholdPermille,persistFrames,baselineFrames,ignoreStartFrames}},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
  }, [
    baselineFrames,
    firstMoveZoomByRing,
    ignoreStartFrames,
    pairs,
    persistFrames,
    preJumpUnlockTs,
    shiftThresholdPx,
    tGlobMax,
    tGlobMin,
    zoomThresholdPermille,
  ]);

  useEffect(() => {
    if (pairs.length < 3) return;
    const candidates: Array<{
      timestampSec: number;
      ringNumber: number;
      ringStatus: string;
      prevZoom: number;
      currZoom: number;
      zoomDelta: number;
      zoomRel: number;
      jumpScore: number;
      jumpFlag: boolean;
      futureMinZoom: number;
      futureMaxZoom: number;
      futureEndZoom: number;
      revertedWithin120Sec: boolean;
    }> = [];

    for (let i = 1; i < pairs.length; i++) {
      const prev = pairs[i - 1]!;
      const row = pairs[i]!;
      const prevZoom = Math.max(1e-6, Number(prev.raw.zoomRatio ?? 1));
      const currZoom = Math.max(1e-6, Number(row.raw.zoomRatio ?? 1));
      const zoomDelta = currZoom - prevZoom;
      const zoomRel = currZoom / prevZoom - 1;
      const jumpScore = Number(row.raw.jumpScore ?? 0);
      const jumpFlag = Boolean(row.raw.jumpFlag);
      const isZoomCandidate = zoomDelta >= 0.035 || zoomRel >= 0.03 || (jumpFlag && zoomDelta > 0.005) || (jumpScore >= 180 && zoomDelta > 0.005);
      if (!isZoomCandidate) continue;

      const future = pairs.filter((probe) => probe.t > row.t && probe.t <= row.t + 120);
      const futureZooms = future.map((probe) => Math.max(1e-6, Number(probe.raw.zoomRatio ?? 1))).filter(Number.isFinite);
      const futureMinZoom = futureZooms.length ? Math.min(...futureZooms) : currZoom;
      const futureMaxZoom = futureZooms.length ? Math.max(...futureZooms) : currZoom;
      const futureEndZoom = futureZooms.length ? futureZooms[futureZooms.length - 1]! : currZoom;
      const revertedWithin120Sec = futureZooms.length > 0 && futureMinZoom <= prevZoom + Math.max(0.012, zoomDelta * 0.35);

      candidates.push({
        timestampSec: row.t,
        ringNumber: Number(row.raw.ringNumber ?? 0),
        ringStatus: String(row.raw.ringStatus ?? "countdown"),
        prevZoom,
        currZoom,
        zoomDelta,
        zoomRel,
        jumpScore,
        jumpFlag,
        futureMinZoom,
        futureMaxZoom,
        futureEndZoom,
        revertedWithin120Sec,
      });
    }

    const strongCandidates = candidates
      .filter((candidate) => candidate.zoomDelta >= 0.035 || candidate.jumpFlag || candidate.jumpScore >= 180)
      .slice(0, 30);
    const summary = {
      pairs: pairs.length,
      tMin: tGlobMin,
      tMax: tGlobMax,
      candidates: strongCandidates,
      acceptedByFutureHold: strongCandidates.filter((candidate) => !candidate.revertedWithin120Sec),
      rejectedByFutureReturn: strongCandidates.filter((candidate) => candidate.revertedWithin120Sec),
    };
    // #region agent log
    fetch("http://127.0.0.1:7664/ingest/0aa35fc0-93f5-4a7d-ae43-8a87e2b19087", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Debug-Session-Id": "b91ec1",
      },
      body: JSON.stringify({
        sessionId: "b91ec1",
        runId: "pre-fix-6",
        hypothesisId: "H7,H8,H9",
        location: "apps/web/components/broadcast/CameraTimelineCharts.tsx:zoom_candidate_future_validation",
        message: "zoom_candidate_future_validation",
        data: summary,
        timestamp: Date.now(),
      }),
    }).catch(() => {});
    // #endregion
  }, [pairs, tGlobMax, tGlobMin]);

  const clampView = useCallback((t0: number, t1: number) => {
    const spanFull = globRef.current.tGlobMax - globRef.current.tGlobMin;
    const minSpan = Math.min(2, spanFull * 0.05);
    let ta = Math.min(t0, t1);
    let tb = Math.max(t0, t1);
    let span = Math.max(minSpan, tb - ta);
    if (span > spanFull) {
      return { t0: globRef.current.tGlobMin, t1: globRef.current.tGlobMax };
    }
    if (ta < globRef.current.tGlobMin) {
      ta = globRef.current.tGlobMin;
      tb = ta + span;
    }
    if (tb > globRef.current.tGlobMax) {
      tb = globRef.current.tGlobMax;
      ta = tb - span;
      ta = Math.max(globRef.current.tGlobMin, ta);
    }
    return { t0: ta, t1: tb };
  }, []);

  const handleReset = () => {
    setView({ t0: tGlobMin, t1: tGlobMax });
  };

  const applyPreset = (preset: DetectorPreset) => {
    setShiftThresholdPx(preset.shiftThresholdPx);
    setZoomThresholdPermille(preset.zoomThresholdPermille);
    setPersistFrames(preset.persistFrames);
    setBaselineFrames(preset.baselineFrames);
    setIgnoreStartFrames(preset.ignoreStartFrames);
    onApplyTuningPreset?.(preset.id);
    // #region agent log
    fetch('http://127.0.0.1:7664/ingest/0aa35fc0-93f5-4a7d-ae43-8a87e2b19087',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'b91ec1'},body:JSON.stringify({sessionId:'b91ec1',runId:'pre-fix',hypothesisId:'H3,H4',location:'apps/web/components/broadcast/CameraTimelineCharts.tsx:applyPreset',message:'camera_chart_preset_applied',data:{preset:preset.id,values:preset,pairs:pairs.length,tMin:tGlobMin,tMax:tGlobMax},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
  };

  const panDrag = useRef<{ pointerId: number; lastX: number } | null>(null);

  const applyPan = useCallback((deltaX: number, plotInnerWidth: number) => {
    if (plotInnerWidth < 24) return;
    const { t0: cur0, t1: cur1 } = viewRef.current;
    const span = cur1 - cur0;
    const dt = (-deltaX / plotInnerWidth) * span;
    setView(clampView(cur0 + dt, cur1 + dt));
  }, [clampView]);

  const plotMetricsRef = useRef({ iw: 1, ml: 48 });

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host || pairs.length === 0) return;

    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (ev.shiftKey) {
        const zoomOut = ev.deltaY > 0;
        const { t0: cur0, t1: cur1 } = viewRef.current;
        const mid = (cur0 + cur1) * 0.5;
        const half = Math.max(1e-9, (cur1 - cur0) * 0.5 * (zoomOut ? 1.08 : 0.925));
        setView(clampView(mid - half, mid + half));
        return;
      }

      const rect = canvas.getBoundingClientRect();
      const ml = Number(host.dataset.marginLeftPx) || 52;
      const mr = Number(host.dataset.marginRightPx) || 10;
      const iw = rect.width - ml - mr;
      const fraction = iw > 0 ? clamp((ev.clientX - rect.left - ml) / iw, 0, 1) : 0.5;

      const { t0: cur0, t1: cur1 } = viewRef.current;
      const span = Math.max(1e-9, cur1 - cur0);
      const cursorT = cur0 + fraction * span;
      const factor = ev.deltaY > 0 ? 1.14 : 0.87;
      const nextSpan = span * factor;
      setView(clampView(cursorT - fraction * nextSpan, cursorT + (1 - fraction) * nextSpan));
    };

    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [clampView, pairs.length]);

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;

    const marginLeft = 52;
    const marginRight = 10;
    const topPad = 6;
    const bottomAxis = 22;
    const rowH = 72;
    const rowGap = 4;
    const plotHeight = topPad + PANELS.length * rowH + (PANELS.length - 1) * rowGap + bottomAxis;

    host.dataset.marginLeftPx = String(marginLeft);
    host.dataset.marginRightPx = String(marginRight);

    const redraw = () => {
      const cssW = Math.max(200, host.clientWidth);
      const cssH = Math.max(280, plotHeight);
      host.style.height = `${cssH}px`;
      const dpr = Math.min(2.5, typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1);
      canvas.width = Math.floor(cssW * dpr);
      canvas.height = Math.floor(cssH * dpr);
      canvas.style.width = `${cssW}px`;
      canvas.style.height = `${cssH}px`;

      const plotW = cssW - marginLeft - marginRight;
      plotMetricsRef.current = { iw: plotW, ml: marginLeft };

      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.scale(dpr, dpr);

      const { t0, t1 } = viewRef.current;
      const spanT = Math.max(1e-9, t1 - t0);

      ctx.fillStyle = "#0f141d";
      ctx.fillRect(0, 0, cssW, cssH);

      const vis =
        pairs.length === 0
          ? []
          : pairs.filter((p) => p.t >= Math.min(t0, t1) && p.t <= Math.max(t0, t1));

      if (pairs.length === 0) {
        ctx.fillStyle = "#6a7790";
        ctx.font = "13px system-ui,sans-serif";
        ctx.fillText("Нет дорожки камеры для этой карты.", marginLeft, 42);
        return;
      }

      if (vis.length < 2) {
        ctx.fillStyle = "#6a7790";
        ctx.font = "13px system-ui,sans-serif";
        ctx.fillText("Нет точек в окне времени — нажмите «Весь матч».", marginLeft, 42);
        return;
      }

      const playheadX = marginLeft + ((currentTimeSec - t0) / spanT) * plotW;
      const inWindow = currentTimeSec >= Math.min(t0, t1) && currentTimeSec <= Math.max(t0, t1);
      const visibleEvents = firstMoveZoomByRing.filter((evt) => evt.timestampSec >= t0 && evt.timestampSec <= t1);
      const unlockVisible =
        preJumpUnlockTs != null && preJumpUnlockTs >= Math.min(t0, t1) && preJumpUnlockTs <= Math.max(t0, t1);

      let rowY = topPad;
      for (let pi = 0; pi < PANELS.length; pi++) {
        const panel = PANELS[pi]!;
        const rowTop = rowY;
        const rowInnerH = rowH - 14;
        const plotTop = rowTop + 4;
        const plotBottom = plotTop + rowInnerH;

        ctx.save();
        ctx.beginPath();
        ctx.rect(marginLeft, plotTop, plotW, rowInnerH);
        ctx.clip();

        ctx.fillStyle = "rgba(255,255,255,0.03)";
        ctx.fillRect(marginLeft, plotTop, plotW, rowInnerH);

        const gridLines = 4;
        for (let g = 0; g <= gridLines; g++) {
          const gy = plotBottom - (g / gridLines) * rowInnerH;
          ctx.strokeStyle = "rgba(255,255,255,0.06)";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(marginLeft, gy);
          ctx.lineTo(marginLeft + plotW, gy);
          ctx.stroke();
        }

        const flatVals: number[] = [];
        for (const p of vis) {
          for (const tr of panel.traces) {
            flatVals.push(tr.getter(p.raw, p.sm));
          }
        }
        if (flatVals.length === 0) continue;
        let vmin = Math.min(...flatVals);
        let vmax = Math.max(...flatVals);
        if (!Number.isFinite(vmin) || !Number.isFinite(vmax)) {
          vmin = 0;
          vmax = 1;
        }
        if (vmin === vmax) {
          vmin -= 1;
          vmax += 1;
        }
        const pad = panel.key === "ringNo" ? 0.4 : (vmax - vmin) * 0.06;
        vmin -= pad;
        vmax += pad;

        const timeToX = (tv: number) => marginLeft + ((tv - t0) / spanT) * plotW;
        const valToY = (vv: number) => plotBottom - ((vv - vmin) / Math.max(1e-12, vmax - vmin)) * rowInnerH;

        const drawSteps = panel.key === "ringNo";

        for (const tr of panel.traces) {
          const pts = vis.map((p) => ({
            tx: timeToX(p.t),
            vy: valToY(tr.getter(p.raw, p.sm)),
          }));
          ctx.strokeStyle = tr.color;
          ctx.lineWidth = tr.width ?? 1.5;
          ctx.lineJoin = "round";
          ctx.setLineDash(tr.dash ?? []);
          ctx.beginPath();
          if (drawSteps && pts.length > 0) {
            ctx.moveTo(pts[0]!.tx, pts[0]!.vy);
            for (let i = 1; i < pts.length; i++) {
              ctx.lineTo(pts[i]!.tx, pts[i - 1]!.vy);
              ctx.lineTo(pts[i]!.tx, pts[i]!.vy);
            }
          } else if (pts.length > 0) {
            ctx.moveTo(pts[0]!.tx, pts[0]!.vy);
            for (let i = 1; i < pts.length; i++) {
              ctx.lineTo(pts[i]!.tx, pts[i]!.vy);
            }
          }
          ctx.stroke();
          ctx.setLineDash([]);
        }

        if (inWindow && playheadX >= marginLeft && playheadX <= marginLeft + plotW) {
          ctx.strokeStyle = "rgba(255,220,80,0.55)";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(playheadX, plotTop);
          ctx.lineTo(playheadX, plotBottom);
          ctx.stroke();
        }

        if (visibleEvents.length > 0) {
          ctx.strokeStyle = "rgba(255,95,175,0.65)";
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 3]);
          for (const evt of visibleEvents) {
            const markerX = marginLeft + ((evt.timestampSec - t0) / spanT) * plotW;
            ctx.beginPath();
            ctx.moveTo(markerX, plotTop);
            ctx.lineTo(markerX, plotBottom);
            ctx.stroke();
          }
          ctx.setLineDash([]);
        }

        if (unlockVisible && preJumpUnlockTs != null) {
          const unlockX = marginLeft + ((preJumpUnlockTs - t0) / spanT) * plotW;
          ctx.strokeStyle = "rgba(80,220,255,0.75)";
          ctx.lineWidth = 1.2;
          ctx.setLineDash([6, 3]);
          ctx.beginPath();
          ctx.moveTo(unlockX, plotTop);
          ctx.lineTo(unlockX, plotBottom);
          ctx.stroke();
          ctx.setLineDash([]);
        }

        ctx.restore();

        ctx.fillStyle = "#9aabbf";
        ctx.font = "11px system-ui,sans-serif";
        ctx.textAlign = "left";
        ctx.fillText(panel.label, marginLeft, rowTop - 1);
        ctx.textAlign = "right";
        ctx.fillText(`${vmin.toFixed(1)} … ${vmax.toFixed(1)}`, marginLeft + plotW, rowTop - 1);
        ctx.textAlign = "left";

        rowY += rowH + rowGap;
      }

      ctx.fillStyle = "rgba(145,164,188,0.85)";
      ctx.font = "11px ui-monospace,monospace";
      ctx.textAlign = "left";
      const axisY = cssH - 18;
      const ticks = 6;
      for (let i = 0; i <= ticks; i++) {
        const ft = i / ticks;
        const ts = t0 + ft * spanT;
        const xx = marginLeft + ft * plotW;
        ctx.fillText(formatMmSs(ts), xx - 2, axisY);
        ctx.strokeStyle = "rgba(255,255,255,0.08)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(xx, topPad);
        ctx.lineTo(xx, cssH - bottomAxis);
        ctx.stroke();
      }

      ctx.fillStyle = "rgba(200,216,238,0.75)";
      ctx.font = "11px system-ui,sans-serif";
      ctx.fillText(`Окно ${spanT.toFixed(1)}s · Shift+колесо — масштаб от центра`, marginLeft, cssH - 4);
    };

    redraw();
    const ro = new ResizeObserver(() => redraw());
    ro.observe(host);
    return () => ro.disconnect();
  }, [cameraTracks.length, currentTimeSec, firstMoveZoomByRing, pairs, view]);

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    panDrag.current = { pointerId: e.pointerId, lastX: e.clientX };
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const drag = panDrag.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    const dx = e.clientX - drag.lastX;
    drag.lastX = e.clientX;
    applyPan(dx, plotMetricsRef.current.iw);
  };

  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (panDrag.current?.pointerId === e.pointerId) panDrag.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  };

  if (pairs.length === 0) {
    return (
      <section className={styles.wrap}>
        <h3 className={styles.title}>Графики камеры</h3>
        <p className={styles.empty}>Нет дорожки камеры.</p>
      </section>
    );
  }

  const spanShown = Math.max(1e-9, view.t1 - view.t0);

  return (
    <section className={styles.wrap}>
      <div className={styles.header}>
        <div>
          <h3 className={styles.title}>Графики камеры (сырой трек vs сглаживание сайта)</h3>
          <p className={styles.hint}>
            Колесо мыши — масштаб времени относительно курсора. Зажать левую кнопку — сдвиг по времени. Shift+колесо —
            масштаб от середины окна.
          </p>
        </div>
        <div className={styles.actions}>
          <span className={styles.rangeReadout}>
            Окно: {formatMmSs(view.t0)} — {formatMmSs(view.t1)} ({spanShown.toFixed(1)} s)
          </span>
          <span className={styles.rangeReadout}>
            anti-latch hits: XY {antiLatchStats.xy} · Z {antiLatchStats.z}
          </span>
          <span className={styles.rangeReadout}>
            unlock ts: {preJumpUnlockTs != null ? formatMmSs(preJumpUnlockTs) : "—"}
          </span>
          {preJumpUnlockTs != null ? (
            <button type="button" className={styles.resetBtn} onClick={() => onSeek?.(preJumpUnlockTs)}>
              Jump unlock
            </button>
          ) : null}
          <button type="button" className={styles.resetBtn} onClick={handleReset}>
            Весь матч
          </button>
        </div>
      </div>

      <div className={styles.controlsGrid}>
        <label className={styles.ctrlLabel} htmlFor="det-shift">
          Порог смещения: {shiftThresholdPx}px
          <Hint text="Минимальный сдвиг камеры, чтобы считать это кандидатом на первое значимое движение." />
          <div className="timeline-track-wrap">
            <input
              id="det-shift"
              type="range"
              className="timeline"
              min={6}
              max={60}
              step={1}
              value={shiftThresholdPx}
              onChange={(e) => setShiftThresholdPx(Number(e.target.value))}
            />
          </div>
        </label>
        <label className={styles.ctrlLabel} htmlFor="det-zoom">
          Порог зума: {(zoomThresholdPermille / 1000).toFixed(3)}
          <Hint text="Минимальное изменение зума, которое должно сопровождать сдвиг камеры для фиксации события." />
          <div className="timeline-track-wrap">
            <input
              id="det-zoom"
              type="range"
              className="timeline"
              min={10}
              max={120}
              step={1}
              value={zoomThresholdPermille}
              onChange={(e) => setZoomThresholdPermille(Number(e.target.value))}
            />
          </div>
        </label>
        <label className={styles.ctrlLabel} htmlFor="det-persist">
          Устойчивость (кадры): {persistFrames}
          <Hint text="Сколько кадров подряд условия должны выполняться, чтобы событие считалось устойчивым, а не шумом." />
          <div className="timeline-track-wrap">
            <input
              id="det-persist"
              type="range"
              className="timeline"
              min={1}
              max={10}
              step={1}
              value={persistFrames}
              onChange={(e) => setPersistFrames(Number(e.target.value))}
            />
          </div>
        </label>
        <label className={styles.ctrlLabel} htmlFor="det-baseline">
          База (кадры): {baselineFrames}
          <Hint text="Количество начальных кадров кольца для вычисления базового положения/зума, от которого ищется первое смещение." />
          <div className="timeline-track-wrap">
            <input
              id="det-baseline"
              type="range"
              className="timeline"
              min={5}
              max={60}
              step={1}
              value={baselineFrames}
              onChange={(e) => setBaselineFrames(Number(e.target.value))}
            />
          </div>
        </label>
        <label className={styles.ctrlLabel} htmlFor="det-skip">
          Пропуск старта кольца (кадры): {ignoreStartFrames}
          <Hint text="Игнорирует первые кадры кольца, чтобы не ловить стартовые технические рывки." />
          <div className="timeline-track-wrap">
            <input
              id="det-skip"
              type="range"
              className="timeline"
              min={0}
              max={30}
              step={1}
              value={ignoreStartFrames}
              onChange={(e) => setIgnoreStartFrames(Number(e.target.value))}
            />
          </div>
        </label>
      </div>

      <div className={styles.presetRow}>
        <span className={styles.presetLabel}>Пресеты графика:</span>
        {DETECTOR_PRESETS.map((preset) => (
          <button
            key={preset.id}
            type="button"
            className={styles.presetBtn}
            onClick={() => applyPreset(preset)}
            title={preset.hint}
          >
            {preset.label}
          </button>
        ))}
      </div>

      <div className={styles.legend} aria-hidden>
        <span className={styles.legendItem}>
          <span className={styles.legendSwatch} style={{ background: "rgba(154,174,206,0.85)" }} />
          сырое
        </span>
        <span className={styles.legendItem}>
          <span className={styles.legendSwatch} style={{ background: "rgba(0,255,160,0.92)" }} />
          после EMA на сайте
        </span>
        <span className={styles.legendItem}>
          <span className={styles.legendSwatch} style={{ background: "rgba(255,170,60,0.85)" }} />
          центр кольца
        </span>
        <span className={styles.legendItem}>
          <span className={styles.legendSwatch} style={{ background: "rgba(255,95,175,0.85)" }} />
          1-й сдвиг+зум (авто)
        </span>
        <span className={styles.legendItem}>
          <span className={styles.legendSwatch} style={{ background: "rgba(80,220,255,0.95)" }} />
          pre-jump unlock ts
        </span>
      </div>

      {firstMoveZoomByRing.length > 0 ? (
        <div className={styles.eventRow}>
          {firstMoveZoomByRing.map((evt) => (
            <button
              key={`${evt.ringNumber}-${evt.timestampSec.toFixed(3)}`}
              type="button"
              className={styles.eventChip}
              onClick={() => onSeek?.(evt.timestampSec)}
              title={`ring ${evt.ringNumber}, shift ${evt.shiftPx.toFixed(1)}px, zoom ${evt.zoomDelta.toFixed(3)}`}
            >
              R{evt.ringNumber} {formatMmSs(evt.timestampSec)} · {evt.shiftPx.toFixed(1)}px · z{evt.zoomDelta.toFixed(3)}
            </button>
          ))}
        </div>
      ) : (
        <p className={styles.noEventHint}>
          Первое смещение+зум не найдено по текущему треку. Поиск идёт по всему кольцу, не только в фазе closing.
        </p>
      )}

      <div ref={hostRef} className={styles.canvasHost}>
        <canvas
          ref={canvasRef}
          className={styles.canvas}
          aria-label="Графики камеры во времени"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerUp}
          onPointerCancel={onPointerUp}
        />
      </div>
    </section>
  );
}