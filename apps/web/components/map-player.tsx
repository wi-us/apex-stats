"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CameraTrackPoint, ObserverRoi, RingPoint, Team, TeamTrack } from "../lib/types";

const DEFAULT_MAP_ROI_X = 420;
const DEFAULT_MAP_ROI_Y = 0;
const DEFAULT_MAP_ROI_WIDTH = 1080;
const DEFAULT_MAP_ROI_HEIGHT = 1080;

function bgrToCss([b, g, r]: [number, number, number]) {
  return `rgb(${r}, ${g}, ${b})`;
}

function drawArrowHead(
  ctx: CanvasRenderingContext2D,
  fromX: number,
  fromY: number,
  toX: number,
  toY: number,
  color: string,
  size = 18
) {
  const angle = Math.atan2(toY - fromY, toX - fromX);
  const leftX = toX - size * Math.cos(angle - Math.PI / 6);
  const leftY = toY - size * Math.sin(angle - Math.PI / 6);
  const rightX = toX - size * Math.cos(angle + Math.PI / 6);
  const rightY = toY - size * Math.sin(angle + Math.PI / 6);

  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(toX, toY);
  ctx.lineTo(leftX, leftY);
  ctx.lineTo(rightX, rightY);
  ctx.closePath();
  ctx.fill();
}

export interface RenderSettings {
  enableStopGrouping: boolean;
  stopRadiusPx: number;
  stopMinDurationSec: number;
  smoothWindow: number;
  /** Доля max(w,h) канваса: разрыв трассы при скачке (дефолт 0.2). */
  pathJumpThresholdRatio?: number;
  /** Толщина линии трека в px до uiScale (дефолт 3). */
  trackStrokePx?: number;
  /** Множитель непрозрачности красной зоны вне кольца (дефолт 1). */
  ringShadeAlphaScale?: number;
  /** Оверлей отладки камеры (рамка, круги, подпись) — только при true. */
  showCameraDebugHud?: boolean;
  /** Применять смещение/зум камеры к траекториям команд. */
  applyCameraShiftToTracks?: boolean;
  /** Сила site-side zoom-компенсации треков: 1 = точный inverse zoom, >1 усиливает. */
  cameraShiftZoomStrength?: number;
}

/** Переопределение формулы EMA для подавления шума камеры (сайт). */
export type CameraSmoothingTuning = {
  kWhenSlider100?: number;
  span0To100?: number;
  kWhenSlider200?: number;
  /** Анти-латч длинных хвостов. */
  antiLatchEnabled?: boolean;
  /** Порог расхождения camera center vs ring center (px). */
  tailDistancePx?: number;
  /** Сколько кадров подряд держится хвост до коррекции. */
  tailFrames?: number;
  /** Если ring center движется медленно, считаем это дрейфом фильтра. */
  ringMotionQuietPx?: number;
  /** Сила возврата к центру кольца при срабатывании анти-латча. */
  tailSnapStrength?: number;
  /** Порог рассогласования по zoomRatio. */
  zoomTailGap?: number;
  /** Сколько кадров подряд держится zoom-хвост. */
  zoomTailFrames?: number;
  /** Порог «тихого» изменения raw zoom. */
  zoomQuietDelta?: number;
  /** Сила возврата z к rawZ при срабатывании анти-латча. */
  zoomSnapStrength?: number;
  /** Жесткий коридор до первого подтвержденного реального скачка камеры. */
  preJumpLockEnabled?: boolean;
  /** Коридор XY до первого скачка, доля от 1080 (0.02 = +-2%). */
  preJumpMaxDriftPct?: number;
  /** Коридор zoom до первого скачка, доля от baseline zoom. */
  preJumpMaxZoomPct?: number;
  /** Мин. jumpScore, чтобы считать событие реальным скачком. */
  preJumpUnlockMinJumpScore?: number;
  /** Мин. модуль смещения raw камеры от baseline для unlock. */
  preJumpUnlockShiftPx?: number;
  /** Мин. модуль изменения raw zoom от baseline для unlock. */
  preJumpUnlockZoomPct?: number;
  /** Сколько кадров подряд нужны для unlock. */
  preJumpUnlockFrames?: number;
};

type MappedPoint = { x: number; y: number; t: number; confidence: number; source: "map" | "frame" };

function distance(a: MappedPoint, b: MappedPoint) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** Ограничивает pan при масштабе > 1, чтобы не показывать пустоту за краями карты. */
function clampMapPanOffset(
  scale: number,
  offsetX: number,
  offsetY: number,
  canvasW: number,
  canvasH: number,
): { x: number; y: number } {
  const minX = canvasW * (1 - scale);
  const maxX = 0;
  const minY = canvasH * (1 - scale);
  const maxY = 0;
  return {
    x: clamp(offsetX, minX, maxX),
    y: clamp(offsetY, minY, maxY),
  };
}

export type DerivedCameraTrackPoint = CameraTrackPoint & {
  effectiveZoom: number;
  effectiveCameraSize: number;
  renderRoiX1: number;
  renderRoiY1: number;
  renderRoiX2: number;
  renderRoiY2: number;
  zoomedRadius: number;
  antiLatchXYTriggered?: boolean;
  antiLatchZoomTriggered?: boolean;
  preJumpLockReleased?: boolean;
};

function cameraSquareFromCenter(cx: number, cy: number, size: number) {
  const half = size * 0.5;
  return {
    x1: clamp(cx - half, 0, 1079),
    y1: clamp(cy - half, 0, 1079),
    x2: clamp(cx + half, 0, 1079),
    y2: clamp(cy + half, 0, 1079),
  };
}

const DEFAULT_RING_CAMERA_NOISE_STRENGTH = 35;

/** Макс. ползунка для колец 3–6; кольца 1–2 — до {@link RING_CAMERA_NOISE_SLIDER_MAX_HEAVY}. */
export const RING_CAMERA_NOISE_SLIDER_MAX = 100;
/** Расширенный максимум для шумных ранних колец. */
export const RING_CAMERA_NOISE_SLIDER_MAX_HEAVY = 200;

/**
 * Сглаживание EMA по cameraX/Y/zoomRatio внутри одного кольца.
 * 0 — почти без сглаживания; 100 — сильное; 101–200 — ещё сильнее (только при расширенном ползунке).
 */
function blendFactorFromNoiseSlider(
  pctRaw: number,
  sliderMax: number,
  tuning?: CameraSmoothingTuning,
): number {
  const k100 = tuning?.kWhenSlider100 ?? 0.02;
  const span = tuning?.span0To100 ?? 0.58;
  const k200 = tuning?.kWhenSlider200 ?? 0.0025;
  const effectiveMax = clamp(
    Number(sliderMax) || RING_CAMERA_NOISE_SLIDER_MAX,
    RING_CAMERA_NOISE_SLIDER_MAX,
    RING_CAMERA_NOISE_SLIDER_MAX_HEAVY,
  );
  const pct = clamp(Number(pctRaw), 0, effectiveMax);
  if (pct <= RING_CAMERA_NOISE_SLIDER_MAX) {
    const noise01 = pct / RING_CAMERA_NOISE_SLIDER_MAX;
    return k100 + (1 - noise01) * span;
  }
  const t = (pct - RING_CAMERA_NOISE_SLIDER_MAX) / (RING_CAMERA_NOISE_SLIDER_MAX_HEAVY - RING_CAMERA_NOISE_SLIDER_MAX);
  return k100 * (1 - t) + k200 * t;
}

export function deriveCameraFrames(
  rows: CameraTrackPoint[],
  ringCameraNoiseByRing: Record<number, number> = {},
  ringNoiseSliderMaxByRing?: Record<number, number>,
  cameraSmoothingTuning?: CameraSmoothingTuning,
): DerivedCameraTrackPoint[] {
  const antiLatchEnabled = cameraSmoothingTuning?.antiLatchEnabled ?? true;
  const antiLatchTailDistancePx = cameraSmoothingTuning?.tailDistancePx ?? 34;
  const antiLatchTailFrames = Math.max(1, Math.round(cameraSmoothingTuning?.tailFrames ?? 16));
  const antiLatchRingMotionQuietPx = cameraSmoothingTuning?.ringMotionQuietPx ?? 2.6;
  const antiLatchSnapStrength = clamp(cameraSmoothingTuning?.tailSnapStrength ?? 0.32, 0.01, 1);
  const antiLatchZoomTailGap = cameraSmoothingTuning?.zoomTailGap ?? 0.035;
  const antiLatchZoomTailFrames = Math.max(1, Math.round(cameraSmoothingTuning?.zoomTailFrames ?? 12));
  const antiLatchZoomQuietDelta = cameraSmoothingTuning?.zoomQuietDelta ?? 0.012;
  const antiLatchZoomSnapStrength = clamp(cameraSmoothingTuning?.zoomSnapStrength ?? 0.38, 0.01, 1);
  const preJumpLockEnabled = cameraSmoothingTuning?.preJumpLockEnabled ?? false;
  const preJumpMaxDriftPct = clamp(cameraSmoothingTuning?.preJumpMaxDriftPct ?? 0.02, 0.001, 0.1);
  const preJumpMaxZoomPct = clamp(cameraSmoothingTuning?.preJumpMaxZoomPct ?? 0.02, 0.001, 0.1);
  const preJumpUnlockMinJumpScore = cameraSmoothingTuning?.preJumpUnlockMinJumpScore ?? 220;
  const preJumpUnlockShiftPx = cameraSmoothingTuning?.preJumpUnlockShiftPx ?? 36;
  const preJumpUnlockZoomPct = cameraSmoothingTuning?.preJumpUnlockZoomPct ?? 0.035;
  const preJumpUnlockFrames = Math.max(1, Math.round(cameraSmoothingTuning?.preJumpUnlockFrames ?? 4));

  const sorted = [...rows].sort((a, b) => a.timestampSec - b.timestampSec);
  let sx = 540;
  let sy = 540;
  let sz = 1;
  let prevRing: number | null = null;
  let prevRingCx = 540;
  let prevRingCy = 540;
  let prevRawZ = 1;
  let xyTailCount = 0;
  let zoomTailCount = 0;
  let preJumpBaselineX = 540;
  let preJumpBaselineY = 540;
  let preJumpBaselineZ = 1;
  let preJumpUnlocked = false;
  let preJumpUnlockCount = 0;

  return sorted.map((row) => {
    const radius = Number(row.radius ?? 0);
    const rn = Number(row.ringNumber ?? 1);
    const rawX = Number(row.cameraX ?? 540);
    const rawY = Number(row.cameraY ?? 540);
    const rawZ = Math.max(1.0, Number(row.zoomRatio ?? 1.0));
    const ringCx = Number(row.centerX ?? rawX);
    const ringCy = Number(row.centerY ?? rawY);
    let antiLatchXYTriggered = false;
    let antiLatchZoomTriggered = false;
    let preJumpLockReleased = false;

    if (prevRing !== rn) {
      sx = rawX;
      sy = rawY;
      sz = rawZ;
      prevRing = rn;
      prevRingCx = ringCx;
      prevRingCy = ringCy;
      prevRawZ = rawZ;
      xyTailCount = 0;
      zoomTailCount = 0;
      if (preJumpLockEnabled && !preJumpUnlocked) {
        preJumpBaselineX = sx;
        preJumpBaselineY = sy;
        preJumpBaselineZ = Math.max(1, sz);
        preJumpUnlockCount = 0;
      }
    } else {
      const sliderMax = Number(ringNoiseSliderMaxByRing?.[rn] ?? RING_CAMERA_NOISE_SLIDER_MAX);
      const pct = Number(ringCameraNoiseByRing[rn] ?? DEFAULT_RING_CAMERA_NOISE_STRENGTH);
      const k = blendFactorFromNoiseSlider(pct, sliderMax, cameraSmoothingTuning);
      sx = sx + (rawX - sx) * k;
      sy = sy + (rawY - sy) * k;
      sz = sz + (rawZ - sz) * k;

      if (antiLatchEnabled) {
        const ringMotionPx = Math.hypot(ringCx - prevRingCx, ringCy - prevRingCy);
        const tailDistancePx = Math.hypot(sx - ringCx, sy - ringCy);
        const rawZoomDelta = Math.abs(rawZ - prevRawZ);
        const zoomTailGap = Math.abs(sz - rawZ);

        if (tailDistancePx >= antiLatchTailDistancePx && ringMotionPx <= antiLatchRingMotionQuietPx) {
          xyTailCount += 1;
        } else {
          xyTailCount = 0;
        }

        if (zoomTailGap >= antiLatchZoomTailGap && rawZoomDelta <= antiLatchZoomQuietDelta) {
          zoomTailCount += 1;
        } else {
          zoomTailCount = 0;
        }

        if (xyTailCount >= antiLatchTailFrames) {
          sx = sx + (ringCx - sx) * antiLatchSnapStrength;
          sy = sy + (ringCy - sy) * antiLatchSnapStrength;
          xyTailCount = Math.max(0, antiLatchTailFrames - 2);
          antiLatchXYTriggered = true;
        }

        if (zoomTailCount >= antiLatchZoomTailFrames) {
          sz = sz + (rawZ - sz) * antiLatchZoomSnapStrength;
          zoomTailCount = Math.max(0, antiLatchZoomTailFrames - 2);
          antiLatchZoomTriggered = true;
        }
      }

      prevRingCx = ringCx;
      prevRingCy = ringCy;
      prevRawZ = rawZ;
    }

    if (preJumpLockEnabled && !preJumpUnlocked) {
      const unlockByScore = Number(row.jumpScore ?? 0) >= preJumpUnlockMinJumpScore || Boolean(row.jumpFlag);
      const unlockByShift = Math.hypot(rawX - preJumpBaselineX, rawY - preJumpBaselineY) >= preJumpUnlockShiftPx;
      const unlockByZoom = Math.abs(rawZ - preJumpBaselineZ) >= preJumpUnlockZoomPct;
      const unlockByZoomOnlyEvent = unlockByScore && unlockByZoom && String(row.ringStatus ?? "countdown") === "countdown";
      if ((unlockByScore && unlockByShift && unlockByZoom) || unlockByZoomOnlyEvent) {
        preJumpUnlockCount += 1;
      } else {
        preJumpUnlockCount = 0;
      }
      if (preJumpUnlockCount >= preJumpUnlockFrames) {
        preJumpUnlocked = true;
        preJumpLockReleased = true;
      } else {
        const maxDriftPx = 1080 * preJumpMaxDriftPct;
        const dx = clamp(sx - preJumpBaselineX, -maxDriftPx, maxDriftPx);
        const dy = clamp(sy - preJumpBaselineY, -maxDriftPx, maxDriftPx);
        sx = preJumpBaselineX + dx;
        sy = preJumpBaselineY + dy;
        const minZ = Math.max(1, preJumpBaselineZ * (1 - preJumpMaxZoomPct));
        const maxZ = preJumpBaselineZ * (1 + preJumpMaxZoomPct);
        sz = clamp(sz, minZ, maxZ);
      }
    }

    sz = Math.max(1, sz);

    const effectiveZoom = sz;
    const effectiveCameraSize = clamp(1080 / Math.max(1e-6, effectiveZoom), 120, 1080);
    const square = cameraSquareFromCenter(sx, sy, effectiveCameraSize);
    const zoomedRadius = Math.max(1, radius / Math.max(1e-6, effectiveZoom));

    return {
      ...row,
      cameraX: sx,
      cameraY: sy,
      zoomRatio: sz,
      effectiveZoom,
      effectiveCameraSize,
      renderRoiX1: square.x1,
      renderRoiY1: square.y1,
      renderRoiX2: square.x2,
      renderRoiY2: square.y2,
      zoomedRadius,
      antiLatchXYTriggered,
      antiLatchZoomTriggered,
      preJumpLockReleased,
    };
  });
}

function ringStartSec(ring: RingPoint): number {
  if (ringNumber(ring) === 1) {
    return 0;
  }
  const value = ring.timeStartSec ?? ring.timestampSec;
  return Number.isFinite(value) ? Number(value) : 0;
}

function ringEndSec(ring: RingPoint): number {
  const value = ring.timeEndSec;
  return Number.isFinite(value as number) ? Number(value) : Number.POSITIVE_INFINITY;
}

function ringNumber(ring: RingPoint): number {
  const value = ring.ringNumber ?? ring.segment ?? 1;
  return Number.isFinite(value) ? Number(value) : 1;
}

function ringToMapSpace(
  ring: RingPoint,
  roi: ObserverRoi = {
    x: DEFAULT_MAP_ROI_X,
    y: DEFAULT_MAP_ROI_Y,
    width: DEFAULT_MAP_ROI_WIDTH,
    height: DEFAULT_MAP_ROI_HEIGHT,
  }
): { x: number; y: number; radius: number } {
  if (ring.coordinateSpace === "map") {
    return {
      x: Number(ring.x),
      y: Number(ring.y),
      radius: Number(ring.radius),
    };
  }
  return {
    x: Number(ring.x) - Number(roi.x),
    y: Number(ring.y) - Number(roi.y),
    radius: Number(ring.radius),
  };
}

function isRingNestedInside(prev: RingPoint, next: RingPoint, roi: ObserverRoi): boolean {
  const a = ringToMapSpace(prev, roi);
  const b = ringToMapSpace(next, roi);
  if (!Number.isFinite(a.x) || !Number.isFinite(a.y) || !Number.isFinite(a.radius)) return false;
  if (!Number.isFinite(b.x) || !Number.isFinite(b.y) || !Number.isFinite(b.radius)) return false;
  // Every next ring must be strictly smaller than previous.
  if (b.radius >= a.radius - 1.0) return false;
  const centerDistance = Math.hypot(a.x - b.x, a.y - b.y);
  // Entire next circle must stay inside previous (with reasonable tolerance for tracking errors).
  return centerDistance + b.radius <= a.radius + 35.0;
}

function clampChildRingInsideParent(prev: RingPoint, child: RingPoint, roi: ObserverRoi): RingPoint {
  const parentSpace = ringToMapSpace(prev, roi);
  const childSpace = ringToMapSpace(child, roi);
  const centerDistance = Math.hypot(parentSpace.x - childSpace.x, parentSpace.y - childSpace.y);
  const maxAllowedRadius = Math.max(1, parentSpace.radius + 35.0 - centerDistance);
  if (!Number.isFinite(maxAllowedRadius) || childSpace.radius <= maxAllowedRadius) {
    return child;
  }
  const nextRadius = Math.max(1, Math.min(childSpace.radius, maxAllowedRadius));
  return {
    ...child,
    radius: nextRadius,
  };
}

function normalizeRingSequence(rings: RingPoint[], roi: ObserverRoi): RingPoint[] {
  const sorted = [...rings].sort((a, b) => {
    const byNumber = ringNumber(a) - ringNumber(b);
    if (byNumber !== 0) return byNumber;
    return ringStartSec(a) - ringStartSec(b);
  });
  const accepted: RingPoint[] = [];
  for (const ring of sorted) {
    const rn = ringNumber(ring);
    const prev = accepted[accepted.length - 1];
    if (!prev || rn <= 1) {
      accepted.push(ring);
      continue;
    }
    const clamped = clampChildRingInsideParent(prev, ring, roi);
    if (isRingNestedInside(prev, clamped, roi)) {
      accepted.push(clamped);
    }
  }
  return accepted;
}

function smoothPoints(points: MappedPoint[], windowSize: number): MappedPoint[] {
  if (windowSize <= 1 || points.length < 3) return points;
  const half = Math.floor(windowSize / 2);
  return points.map((point, idx) => {
    const start = Math.max(0, idx - half);
    const end = Math.min(points.length - 1, idx + half);
    let sumX = 0;
    let sumY = 0;
    let cnt = 0;
    for (let i = start; i <= end; i++) {
      sumX += points[i].x;
      sumY += points[i].y;
      cnt += 1;
    }
    return { ...point, x: sumX / cnt, y: sumY / cnt };
  });
}

function groupStops(
  points: MappedPoint[],
  radiusPx: number,
  minDurationSec: number
): { pathPoints: MappedPoint[]; stops: Array<{ x: number; y: number; durationSec: number }> } {
  if (points.length < 2) return { pathPoints: points, stops: [] };

  const pathPoints: MappedPoint[] = [];
  const stops: Array<{ x: number; y: number; durationSec: number }> = [];

  let i = 0;
  while (i < points.length) {
    let j = i;
    let minX = points[i].x;
    let maxX = points[i].x;
    let minY = points[i].y;
    let maxY = points[i].y;
    let sumX = points[i].x;
    let sumY = points[i].y;
    let count = 1;

    // Expand compact cluster while all points fit into radius bounding box.
    while (j + 1 < points.length) {
      const probe = points[j + 1];
      const nextMinX = Math.min(minX, probe.x);
      const nextMaxX = Math.max(maxX, probe.x);
      const nextMinY = Math.min(minY, probe.y);
      const nextMaxY = Math.max(maxY, probe.y);

      const spanX = nextMaxX - nextMinX;
      const spanY = nextMaxY - nextMinY;
      if (spanX > radiusPx * 2 || spanY > radiusPx * 2) {
        break;
      }

      j += 1;
      minX = nextMinX;
      maxX = nextMaxX;
      minY = nextMinY;
      maxY = nextMaxY;
      sumX += probe.x;
      sumY += probe.y;
      count += 1;
    }

    const durationSec = points[j].t - points[i].t;
    const isStop = count >= 3 && durationSec >= minDurationSec;

    if (!isStop) {
      pathPoints.push(points[i]);
      i += 1;
      continue;
    }

    const cx = sumX / count;
    const cy = sumY / count;
    const stopPoint: MappedPoint = {
      x: cx,
      y: cy,
      t: points[j].t,
      confidence: points[j].confidence,
      source: points[j].source
    };

    // Merge neighboring stop clusters if they belong to the same knot.
    const prevStop = stops[stops.length - 1];
    const prevPathPoint = pathPoints[pathPoints.length - 1];
    if (prevStop && prevPathPoint && Math.hypot(prevStop.x - cx, prevStop.y - cy) <= radiusPx) {
      const prevDuration = prevStop.durationSec;
      const mergedDuration = prevDuration + durationSec;
      const mergedX = (prevStop.x * prevDuration + cx * durationSec) / Math.max(1e-6, mergedDuration);
      const mergedY = (prevStop.y * prevDuration + cy * durationSec) / Math.max(1e-6, mergedDuration);

      prevStop.x = mergedX;
      prevStop.y = mergedY;
      prevStop.durationSec = mergedDuration;
      prevPathPoint.x = mergedX;
      prevPathPoint.y = mergedY;
      prevPathPoint.t = points[j].t;
      prevPathPoint.confidence = points[j].confidence;
    } else {
      stops.push({ x: cx, y: cy, durationSec });
      pathPoints.push(stopPoint);
    }

    // Skip all inner points: no line drawing inside stop cluster.
    i = j + 1;
  }

  if (pathPoints.length === 0 && points.length > 0) pathPoints.push(points[0]);
  return { pathPoints, stops };
}

type CameraShiftEvent = {
  timestampSec: number;
  scale: number;
  tx: number;
  ty: number;
};

const loggedCameraShiftEventSignatures = new Set<string>();

function buildCameraShiftEvents(rows: DerivedCameraTrackPoint[], zoomStrength = 1.0): CameraShiftEvent[] {
  const out: CameraShiftEvent[] = [];
  if (rows.length < 2) return out;
  let lastAcceptedTs = Number.NEGATIVE_INFINITY;
  const strength = clamp(Number(zoomStrength), 0.5, 10.0);
  for (let i = 1; i < rows.length; i++) {
    const prev = rows[i - 1];
    const cur = rows[i];
    const curTs = Number(cur.timestampSec);
    if (!Number.isFinite(curTs)) continue;
    if (curTs - lastAcceptedTs < 0.45) continue;

    const prevCamX = Number(prev.cameraX ?? 540);
    const prevCamY = Number(prev.cameraY ?? 540);
    const curCamX = Number(cur.cameraX ?? prevCamX);
    const curCamY = Number(cur.cameraY ?? prevCamY);
    const shiftPx = Math.hypot(curCamX - prevCamX, curCamY - prevCamY);

    const prevZoom = Math.max(1e-6, Number(prev.effectiveZoom ?? prev.zoomRatio ?? 1));
    const curZoom = Math.max(1e-6, Number(cur.effectiveZoom ?? cur.zoomRatio ?? 1));
    const zoomRel = Math.abs(curZoom / prevZoom - 1);

    const jumpScore = Number(cur.jumpScore ?? 0);
    const isJump = Boolean(cur.jumpFlag) || jumpScore >= 180 || (shiftPx >= 24 && zoomRel >= 0.015);
    if (!isJump) continue;

    const baseScale = clamp(prevZoom / curZoom, 0.5, 2.0);
    const scale = clamp(Math.pow(baseScale, strength), 0.02, 2.5);
    const anchorX = Number(prev.centerX ?? prevCamX);
    const anchorY = Number(prev.centerY ?? prevCamY);
    const dx = curCamX - prevCamX;
    const dy = curCamY - prevCamY;
    out.push({
      timestampSec: curTs,
      scale,
      tx: (1 - scale) * anchorX + dx,
      ty: (1 - scale) * anchorY + dy,
    });
    lastAcceptedTs = curTs;
  }
  if (out.length > 0) {
    const focusEvents = out.filter((event) => (event.timestampSec >= 390 && event.timestampSec <= 430) || (event.timestampSec >= 500 && event.timestampSec <= 520));
    const signature = `${strength.toFixed(3)}|${focusEvents.map((event) => `${event.timestampSec.toFixed(3)}:${event.scale.toFixed(4)}:${event.tx.toFixed(1)}:${event.ty.toFixed(1)}`).join("|")}`;
    if (signature && !loggedCameraShiftEventSignatures.has(signature)) {
      loggedCameraShiftEventSignatures.add(signature);
      // #region agent log
      fetch("http://127.0.0.1:7664/ingest/0aa35fc0-93f5-4a7d-ae43-8a87e2b19087", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Debug-Session-Id": "b91ec1",
        },
        body: JSON.stringify({
          sessionId: "b91ec1",
          runId: "post-fix-5",
          hypothesisId: "H6",
          location: "apps/web/components/map-player.tsx:buildCameraShiftEvents",
          message: "camera_shift_events_built",
          data: {
            totalEvents: out.length,
            zoomStrength: strength,
            focusEvents,
          },
          timestamp: Date.now(),
        }),
      }).catch(() => {});
      // #endregion
    }
  }
  return out;
}

export function MapPlayer({
  tracks,
  rings,
  cameraTracks,
  currentTimeSec,
  teams,
  backgroundSrc,
  renderSettings,
  observerRoi,
  ringCameraNoiseByRing,
  ringNoiseSliderMaxByRing,
  cameraSmoothingTuning,
}: {
  tracks: TeamTrack[];
  rings: RingPoint[];
  cameraTracks: CameraTrackPoint[];
  currentTimeSec: number;
  teams: Team[];
  backgroundSrc?: string;
  renderSettings: RenderSettings;
  observerRoi?: ObserverRoi;
  /** На номер кольца: подавление шума (0–100 или до 200 для колец с расширенным max). */
  ringCameraNoiseByRing?: Record<number, number>;
  /** Для каждого кольца — верхняя граница ползунка (100 или 200). */
  ringNoiseSliderMaxByRing?: Record<number, number>;
  cameraSmoothingTuning?: CameraSmoothingTuning;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const lastCameraDriftDebugTsRef = useRef<number>(-1);
  const lastContainmentDebugTsRef = useRef<number>(-1);
  const [viewportScale, setViewportScale] = useState(1);
  const [viewportOffset, setViewportOffset] = useState({ x: 0, y: 0 });
  const derivedCameraTracks = useMemo(
    () =>
      deriveCameraFrames(
        cameraTracks,
        ringCameraNoiseByRing ?? {},
        ringNoiseSliderMaxByRing,
        cameraSmoothingTuning,
      ),
    [cameraSmoothingTuning, cameraTracks, ringCameraNoiseByRing, ringNoiseSliderMaxByRing],
  );

  const handleWheel = useCallback((event: React.WheelEvent<HTMLCanvasElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const localX = (event.clientX - rect.left) * (canvas.width / rect.width);
    const localY = (event.clientY - rect.top) * (canvas.height / rect.height);
    const zoomFactor = event.deltaY < 0 ? 1.12 : 0.88;
    const nextScale = clamp(viewportScale * zoomFactor, 1.0, 4.0);
    const worldX = (localX - viewportOffset.x) / viewportScale;
    const worldY = (localY - viewportOffset.y) / viewportScale;
    const nextOffsetX = localX - worldX * nextScale;
    const nextOffsetY = localY - worldY * nextScale;
    const clamped = clampMapPanOffset(nextScale, nextOffsetX, nextOffsetY, canvas.width, canvas.height);
    setViewportScale(nextScale);
    setViewportOffset(clamped);
  }, [viewportOffset.x, viewportOffset.y, viewportScale]);

  const teamColorMap = useMemo(() => {
    const map = new Map<string, string>();
    teams.forEach((team) => map.set(team.id, bgrToCss(team.colorBgr)));
    return map;
  }, [teams]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    const teamNameMap = new Map<string, string>();
    teams.forEach((team) => teamNameMap.set(team.id, team.name));

    const drawTracks = () => {
      const roiX = Number(observerRoi?.x ?? DEFAULT_MAP_ROI_X);
      const roiY = Number(observerRoi?.y ?? DEFAULT_MAP_ROI_Y);
      const roiWidth = Math.max(1, Number(observerRoi?.width ?? DEFAULT_MAP_ROI_WIDTH));
      const roiHeight = Math.max(1, Number(observerRoi?.height ?? DEFAULT_MAP_ROI_HEIGHT));
      const roiForRing = { x: roiX, y: roiY, width: roiWidth, height: roiHeight };
      const uiScale = clamp(1 / viewportScale, 0.45, 1.0);
      const sortedRings = normalizeRingSequence(rings, roiForRing).sort((a, b) => ringStartSec(a) - ringStartSec(b));

      const targetRing = sortedRings.find((ring) => ringEndSec(ring) > currentTimeSec) ?? null;
      let visualX = canvas.width / 2;
      let visualY = canvas.height / 2;
      let visualRadius = Math.hypot(canvas.width, canvas.height) * 0.5;
      const redAlpha = 0.14 * 1.3 * (renderSettings.ringShadeAlphaScale ?? 1);

      if (targetRing) {
        const number = ringNumber(targetRing);
        const prevRing = sortedRings.find((r) => ringNumber(r) === number - 1);

        const isMapSpace = targetRing.coordinateSpace === "map";
        const endX = isMapSpace ? (targetRing.x / roiWidth) * canvas.width : ((targetRing.x - roiX) / roiWidth) * canvas.width;
        const endY = isMapSpace ? (targetRing.y / roiHeight) * canvas.height : ((targetRing.y - roiY) / roiHeight) * canvas.height;
        const endR = (targetRing.radius / roiWidth) * canvas.width;

        let startX = endX;
        let startY = endY;
        let startR = Math.hypot(canvas.width, canvas.height) * 0.5;

        if (prevRing) {
          const pMapSpace = prevRing.coordinateSpace === "map";
          startX = pMapSpace ? (prevRing.x / roiWidth) * canvas.width : ((prevRing.x - roiX) / roiWidth) * canvas.width;
          startY = pMapSpace ? (prevRing.y / roiHeight) * canvas.height : ((prevRing.y - roiY) / roiHeight) * canvas.height;
          startR = (prevRing.radius / roiWidth) * canvas.width;
        }

        const closingStart = Number(targetRing.timeStartSec);
        const phaseStart = Number.isFinite(closingStart) ? closingStart : ringStartSec(targetRing);
        const phaseEnd = ringEndSec(targetRing);
        const phaseDuration = Number.isFinite(phaseEnd) ? Math.max(0.001, phaseEnd - phaseStart) : 1.0;
        const progress = clamp((currentTimeSec - phaseStart) / phaseDuration, 0, 1);

        visualX = startX + (endX - startX) * progress;
        visualY = startY + (endY - startY) * progress;
        visualRadius = startR - (startR - endR) * progress;

      } else if (sortedRings.length > 0) {
        const lastRing = sortedRings[sortedRings.length - 1];
        const isMapSpace = lastRing.coordinateSpace === "map";
        visualX = isMapSpace ? (lastRing.x / roiWidth) * canvas.width : ((lastRing.x - roiX) / roiWidth) * canvas.width;
        visualY = isMapSpace ? (lastRing.y / roiHeight) * canvas.height : ((lastRing.y - roiY) / roiHeight) * canvas.height;
        visualRadius = (lastRing.radius / roiWidth) * canvas.width;
      }

      context.save();
      context.fillStyle = `rgba(255, 80, 80, ${redAlpha.toFixed(4)})`;
      context.beginPath();
      context.rect(0, 0, canvas.width, canvas.height);
      context.arc(visualX, visualY, Math.max(4, visualRadius), 0, Math.PI * 2, true);
      context.fill("evenodd");
      context.restore();

      context.beginPath();
      context.strokeStyle = "rgba(255,255,255,0.9)";
      context.lineWidth = Math.max(1, 2 * uiScale);
      context.arc(visualX, visualY, Math.max(4, visualRadius), 0, Math.PI * 2);
      context.stroke();

      if (targetRing) {
        const isMapSpace = targetRing.coordinateSpace === "map";
        const nx = isMapSpace ? (targetRing.x / roiWidth) * canvas.width : ((targetRing.x - roiX) / roiWidth) * canvas.width;
        const ny = isMapSpace ? (targetRing.y / roiHeight) * canvas.height : ((targetRing.y - roiY) / roiHeight) * canvas.height;
        const nr = (targetRing.radius / roiWidth) * canvas.width;

        context.save();
        context.setLineDash([8 * uiScale, 6 * uiScale]);
        context.beginPath();
        context.strokeStyle = "rgba(255,255,255,0.85)";
        context.lineWidth = Math.max(1, 2 * uiScale);
        context.arc(nx, ny, Math.max(4, nr), 0, Math.PI * 2);
        context.stroke();
        context.restore();
      }

      if (renderSettings.showCameraDebugHud === true && derivedCameraTracks.length === 0) {
        context.save();
        context.fillStyle = "rgba(0,255,120,0.92)";
        context.font = `${Math.max(10, Math.round(12 * uiScale))}px Arial`;
        context.fillText("Отладка камеры: нет дорожки камеры — ROI и цветные кольца появятся после загрузки трека.", 8, 20);
        context.restore();
      }

      if (derivedCameraTracks.length > 0) {
        const sortedCamera = derivedCameraTracks;
        const activeCamera =
          sortedCamera
            .filter((row) => Number.isFinite(row.timestampSec) && row.timestampSec <= currentTimeSec)
            .slice(-1)[0] ?? sortedCamera[0];
        if (activeCamera) {
          const rx1 = (activeCamera.renderRoiX1 / roiWidth) * canvas.width;
          const ry1 = (activeCamera.renderRoiY1 / roiHeight) * canvas.height;
          const rx2 = (activeCamera.renderRoiX2 / roiWidth) * canvas.width;
          const ry2 = (activeCamera.renderRoiY2 / roiHeight) * canvas.height;
          const camX = (activeCamera.cameraX / roiWidth) * canvas.width;
          const camY = (activeCamera.cameraY / roiHeight) * canvas.height;
          const ringCamX = (activeCamera.centerX / roiWidth) * canvas.width;
          const ringCamY = (activeCamera.centerY / roiHeight) * canvas.height;
          const ringCamR = (activeCamera.radius / roiWidth) * canvas.width;
          const ringZoomedR = (activeCamera.zoomedRadius / roiWidth) * canvas.width;
          const orangeToWhiteRatio = ringCamR / Math.max(1e-6, Math.max(4, visualRadius));
          const halfSquare = Math.max(1, Math.max(2, rx2 - rx1) * 0.5);
          const containmentOverflow = Math.max(0, ringCamR - halfSquare);

          if (renderSettings.showCameraDebugHud === true) {
            context.save();
            context.strokeStyle = "rgba(0,255,120,0.95)";
            context.lineWidth = Math.max(1, 2 * uiScale);
            context.strokeRect(rx1, ry1, Math.max(2, rx2 - rx1), Math.max(2, ry2 - ry1));
            context.fillStyle = "rgba(0,255,120,0.95)";
            context.beginPath();
            context.arc(camX, camY, Math.max(2, 3 * uiScale), 0, Math.PI * 2);
            context.fill();
            context.strokeStyle = "rgba(255,170,30,0.95)";
            context.lineWidth = Math.max(1, 2 * uiScale);
            context.beginPath();
            context.arc(ringCamX, ringCamY, Math.max(2, ringCamR), 0, Math.PI * 2);
            context.stroke();
            context.strokeStyle = "rgba(220,120,255,0.95)";
            context.lineWidth = Math.max(1, 1.5 * uiScale);
            context.beginPath();
            context.arc(ringCamX, ringCamY, Math.max(2, ringZoomedR), 0, Math.PI * 2);
            context.stroke();
            context.fillStyle = "rgba(0,255,120,0.9)";
            context.font = `${Math.max(10, Math.round(12 * uiScale))}px Arial`;
            context.fillText(
              `cam zoom=${activeCamera.effectiveZoom.toFixed(2)} size=${activeCamera.effectiveCameraSize.toFixed(0)} zR=${Math.round(activeCamera.zoomedRadius)} o/w=${orangeToWhiteRatio.toFixed(3)} move=${String(activeCamera.moveSide ?? "none")} ${Number(activeCamera.moveDist ?? 0).toFixed(1)}px antiLatch(xy=${activeCamera.antiLatchXYTriggered ? "1" : "0"},z=${activeCamera.antiLatchZoomTriggered ? "1" : "0"})`,
              Math.max(8, rx1 + 6),
              Math.max(14, ry1 - 6)
            );
            context.restore();
          }

          const driftPx = Math.hypot(ringCamX - camX, ringCamY - camY);
          if (Number.isFinite(driftPx) && driftPx > 90 && activeCamera.timestampSec > lastCameraDriftDebugTsRef.current) {
            lastCameraDriftDebugTsRef.current = activeCamera.timestampSec;
            // #region agent log
            fetch("http://127.0.0.1:7913/ingest/62f142c5-9b92-4b24-af19-a188fbff1d59", {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "X-Debug-Session-Id": "dd3473",
              },
              body: JSON.stringify({
                sessionId: "dd3473",
                runId: "pre-fix-7",
                hypothesisId: "H13",
                location: "apps/web/components/map-player.tsx:camera_overlay",
                message: "camera_ring_drift_high",
                data: {
                  timestampSec: activeCamera.timestampSec,
                  driftPx,
                  ringCenter: [ringCamX, ringCamY],
                  cameraCenter: [camX, camY],
                  effectiveZoom: activeCamera.effectiveZoom,
                  effectiveCameraSize: activeCamera.effectiveCameraSize,
                },
                timestamp: Date.now(),
              }),
            }).catch(() => {});
            // #endregion
          }
          if (Number.isFinite(containmentOverflow) && containmentOverflow > 40 && activeCamera.timestampSec > lastContainmentDebugTsRef.current) {
            lastContainmentDebugTsRef.current = activeCamera.timestampSec;
            // #region agent log
            fetch("http://127.0.0.1:7913/ingest/62f142c5-9b92-4b24-af19-a188fbff1d59", {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "X-Debug-Session-Id": "dd3473",
              },
              body: JSON.stringify({
                sessionId: "dd3473",
                runId: "pre-fix-7",
                hypothesisId: "H15",
                location: "apps/web/components/map-player.tsx:camera_overlay",
                message: "ring_outside_camera_square",
                data: {
                  timestampSec: activeCamera.timestampSec,
                  containmentOverflow,
                  orangeToWhiteRatio,
                  ringCamR,
                  halfSquare,
                  moveSide: String(activeCamera.moveSide ?? "none"),
                  moveDist: Number(activeCamera.moveDist ?? 0),
                },
                timestamp: Date.now(),
              }),
            }).catch(() => {});
            // #endregion
          }
        }
      }

      tracks.forEach((track) => {
        const color = teamColorMap.get(track.teamId) ?? "#ffffff";
        if (track.points.length < 1) return;

        const hasAnyMapSpacePoints = track.points.some(
          (point) =>
            point.mapSpaceValid !== false &&
            point.mapX !== null &&
            point.mapX !== undefined &&
            point.mapY !== null &&
            point.mapY !== undefined
        );

        const cameraShiftEvents = renderSettings.applyCameraShiftToTracks
          ? buildCameraShiftEvents(derivedCameraTracks, renderSettings.cameraShiftZoomStrength ?? 1.0)
          : [];
        let shiftEventIdx = 0;
        let accScale = 1.0;
        let accTx = 0.0;
        let accTy = 0.0;

        let mappedPoints: MappedPoint[] = track.points.map((point) => {
          const hasMapSpace =
            point.mapSpaceValid !== false &&
            point.mapX !== null &&
            point.mapX !== undefined &&
            point.mapY !== null &&
            point.mapY !== undefined;
          // If map-space exists at least for a part of trajectory, suppress frame-space fallback.
          if (hasAnyMapSpacePoints && !hasMapSpace) {
            return null;
          }
          let localX = hasMapSpace ? Number(point.mapX) : point.x - roiX;
          let localY = hasMapSpace ? Number(point.mapY) : point.y - roiY;

          if (cameraShiftEvents.length > 0) {
            const pointTs = Number(point.timestampSec);
            while (
              shiftEventIdx < cameraShiftEvents.length &&
              Number(cameraShiftEvents[shiftEventIdx].timestampSec) <= pointTs
            ) {
              const ev = cameraShiftEvents[shiftEventIdx];
              accScale = ev.scale * accScale;
              accTx = ev.scale * accTx + ev.tx;
              accTy = ev.scale * accTy + ev.ty;
              shiftEventIdx += 1;
            }
            localX = localX * accScale + accTx;
            localY = localY * accScale + accTy;
          }

          return {
            x: (localX / roiWidth) * canvas.width,
            y: (localY / roiHeight) * canvas.height,
            t: point.timestampSec,
            confidence: point.confidence,
            source: hasMapSpace ? "map" : "frame"
          };
        }).filter((point): point is MappedPoint => point !== null);

        mappedPoints = smoothPoints(mappedPoints, Math.max(1, renderSettings.smoothWindow));
        const startAnchor = mappedPoints[0];
        const pointsWithoutStart = mappedPoints.length > 1 ? mappedPoints.slice(1) : [];
        const grouped = renderSettings.enableStopGrouping
          ? groupStops(pointsWithoutStart, renderSettings.stopRadiusPx, renderSettings.stopMinDurationSec)
          : { pathPoints: pointsWithoutStart, stops: [] };

        mappedPoints = startAnchor ? [startAnchor, ...grouped.pathPoints] : grouped.pathPoints;

        context.strokeStyle = color;
        context.lineWidth = Math.max(1, (renderSettings.trackStrokePx ?? 3) * uiScale);

        if (mappedPoints.length > 1) {
          const jumpCutoff =
            Math.max(canvas.width, canvas.height) * (renderSettings.pathJumpThresholdRatio ?? 0.2);
          context.beginPath();
          context.moveTo(mappedPoints[0].x, mappedPoints[0].y);
          for (let i = 1; i < mappedPoints.length; i++) {
            const prev = mappedPoints[i - 1];
            const curr = mappedPoints[i];
            const hasModeSwitch = prev.source !== curr.source;
            const hasHardJump = distance(prev, curr) > jumpCutoff;
            if (hasModeSwitch || hasHardJump) {
              context.moveTo(curr.x, curr.y);
              continue;
            }
            context.lineTo(curr.x, curr.y);
          }
          context.stroke();
        }

        if (mappedPoints.length === 0) return;
        const start = startAnchor ?? mappedPoints[0];
        context.fillStyle = color;
        context.beginPath();
        context.arc(start.x, start.y, Math.max(2.2, 4 * uiScale), 0, Math.PI * 2);
        context.fill();

        const last = mappedPoints[mappedPoints.length - 1];
        const prev = mappedPoints[Math.max(0, mappedPoints.length - 2)];
        if (mappedPoints.length > 1) {
          drawArrowHead(context, prev.x, prev.y, last.x, last.y, color, Math.max(8, 18 * uiScale));
        }

        const teamName = teamNameMap.get(track.teamId) ?? track.teamId;
        context.font = `${Math.max(11, Math.round(18 * uiScale))}px Arial`;
        const paddingX = 9;
        const paddingY = 6;
        const textWidth = context.measureText(teamName).width;
        const boxWidth = textWidth + paddingX * 2;
        const boxHeight = Math.max(18, Math.round(27 * uiScale));
        const boxX = Math.min(Math.max(last.x - boxWidth / 2, 0), canvas.width - boxWidth);
        const boxY = Math.max(last.y - 39, 0);

        context.fillStyle = color;
        context.fillRect(boxX, boxY, boxWidth, boxHeight);
        const textX = boxX + paddingX;
        const textY = boxY + boxHeight - paddingY - 2;
        context.lineWidth = Math.max(1, 2 * uiScale);
        context.lineJoin = "round";
        context.strokeStyle = "#000000";
        context.strokeText(teamName, textX, textY);
        context.fillStyle = "#ffffff";
        context.fillText(teamName, textX, textY);

        grouped.stops.forEach((stop) => {
          context.strokeStyle = color;
          context.lineWidth = Math.max(1, 2 * uiScale);
          context.beginPath();
          context.arc(stop.x, stop.y, Math.max(5, 10 * uiScale), 0, Math.PI * 2);
          context.stroke();

          const text = `${Math.round(stop.durationSec)}s`;
          context.font = `${Math.max(10, Math.round(13 * uiScale))}px Arial`;
          const textWidth = context.measureText(text).width;
          const padX = 5;
          const padY = 3;
          const boxW = textWidth + padX * 2;
          const boxH = 18;
          const boxX = stop.x + 8;
          const boxY = stop.y - boxH - 4;

          context.fillStyle = "rgba(8, 12, 20, 0.85)";
          context.fillRect(boxX, boxY, boxW, boxH);
          context.fillStyle = "#ffffff";
          context.fillText(text, boxX + padX, boxY + boxH - padY - 1);
        });
      });
    };

    const beginFrame = () => {
      context.setTransform(1, 0, 0, 1, 0, 0);
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.setTransform(viewportScale, 0, 0, viewportScale, viewportOffset.x, viewportOffset.y);
    };

    const drawFallback = () => {
      beginFrame();
      context.fillStyle = "#1b2738";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.strokeStyle = "#2b3e59";
      context.strokeRect(0, 0, canvas.width, canvas.height);
      context.fillStyle = "#90a4c2";
      context.font = "16px Arial";
      context.fillText("Map background unavailable", 20, 30);
      drawTracks();
      context.setTransform(1, 0, 0, 1, 0, 0);
    };

    if (!backgroundSrc) {
      drawFallback();
      return;
    }

    const image = new Image();
    image.onload = () => {
      beginFrame();
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      drawTracks();
      context.setTransform(1, 0, 0, 1, 0, 0);
    };
    image.onerror = () => {
      drawFallback();
    };
    image.src = backgroundSrc;
  }, [backgroundSrc, currentTimeSec, derivedCameraTracks, observerRoi?.height, observerRoi?.width, observerRoi?.x, observerRoi?.y, renderSettings, rings, teamColorMap, tracks, viewportOffset.x, viewportOffset.y, viewportScale]);

  return <canvas ref={canvasRef} className="mapCanvas" width={900} height={900} onWheel={handleWheel} />;
}
