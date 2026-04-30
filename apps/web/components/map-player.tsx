"use client";

import { useEffect, useMemo, useRef } from "react";
import { RingPoint, Team, TeamTrack } from "../lib/types";

const MAP_ROI_X = 420;
const MAP_ROI_Y = 0;
const MAP_ROI_WIDTH = 1080;
const MAP_ROI_HEIGHT = 1080;

function bgrToCss([b, g, r]: [number, number, number]) {
  return `rgb(${r}, ${g}, ${b})`;
}

function drawArrowHead(
  ctx: CanvasRenderingContext2D,
  fromX: number,
  fromY: number,
  toX: number,
  toY: number,
  color: string
) {
  const angle = Math.atan2(toY - fromY, toX - fromX);
  const size = 18;
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

interface RenderSettings {
  enableStopGrouping: boolean;
  stopRadiusPx: number;
  stopMinDurationSec: number;
  smoothWindow: number;
}

type MappedPoint = { x: number; y: number; t: number; confidence: number };

function distance(a: MappedPoint, b: MappedPoint) {
  return Math.hypot(a.x - b.x, a.y - b.y);
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
      confidence: points[j].confidence
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

export function MapPlayer({
  tracks,
  rings,
  currentTimeSec,
  teams,
  backgroundSrc,
  renderSettings
}: {
  tracks: TeamTrack[];
  rings: RingPoint[];
  currentTimeSec: number;
  teams: Team[];
  backgroundSrc?: string;
  renderSettings: RenderSettings;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

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
      if (rings.length > 0) {
        const visibleRing = rings
          .filter((ring) => ring.timestampSec <= currentTimeSec)
          .sort((a, b) => a.timestampSec - b.timestampSec)
          .at(-1);
        if (visibleRing) {
          const rx = ((visibleRing.x - MAP_ROI_X) / MAP_ROI_WIDTH) * canvas.width;
          const ry = ((visibleRing.y - MAP_ROI_Y) / MAP_ROI_HEIGHT) * canvas.height;
          const rr = (visibleRing.radius / MAP_ROI_WIDTH) * canvas.width;
          context.beginPath();
          context.strokeStyle = "rgba(255,255,255,0.85)";
          context.lineWidth = 2;
          context.arc(rx, ry, Math.max(4, rr), 0, Math.PI * 2);
          context.stroke();
        }
      }

      tracks.forEach((track) => {
        const color = teamColorMap.get(track.teamId) ?? "#ffffff";
        if (track.points.length < 1) return;

        let mappedPoints: MappedPoint[] = track.points.map((point) => {
          const localX = point.x - MAP_ROI_X;
          const localY = point.y - MAP_ROI_Y;
          return {
            x: (localX / MAP_ROI_WIDTH) * canvas.width,
            y: (localY / MAP_ROI_HEIGHT) * canvas.height,
            t: point.timestampSec,
            confidence: point.confidence
          };
        });

        mappedPoints = smoothPoints(mappedPoints, Math.max(1, renderSettings.smoothWindow));
        const startAnchor = mappedPoints[0];
        const pointsWithoutStart = mappedPoints.length > 1 ? mappedPoints.slice(1) : [];
        const grouped = renderSettings.enableStopGrouping
          ? groupStops(pointsWithoutStart, renderSettings.stopRadiusPx, renderSettings.stopMinDurationSec)
          : { pathPoints: pointsWithoutStart, stops: [] };

        mappedPoints = startAnchor ? [startAnchor, ...grouped.pathPoints] : grouped.pathPoints;

        context.strokeStyle = color;
        context.lineWidth = 3;

        if (mappedPoints.length > 1) {
          context.beginPath();
          mappedPoints.forEach((p, index) => {
            if (index === 0) context.moveTo(p.x, p.y);
            else context.lineTo(p.x, p.y);
          });
          context.stroke();
        }

        if (mappedPoints.length === 0) return;
        const start = startAnchor ?? mappedPoints[0];
        context.fillStyle = color;
        context.beginPath();
        context.arc(start.x, start.y, 4, 0, Math.PI * 2);
        context.fill();

        const last = mappedPoints[mappedPoints.length - 1];
        const prev = mappedPoints[Math.max(0, mappedPoints.length - 2)];
        if (mappedPoints.length > 1) {
          drawArrowHead(context, prev.x, prev.y, last.x, last.y, color);
        }

        const teamName = teamNameMap.get(track.teamId) ?? track.teamId;
        context.font = "18px Arial";
        const paddingX = 9;
        const paddingY = 6;
        const textWidth = context.measureText(teamName).width;
        const boxWidth = textWidth + paddingX * 2;
        const boxHeight = 27;
        const boxX = Math.min(Math.max(last.x - boxWidth / 2, 0), canvas.width - boxWidth);
        const boxY = Math.max(last.y - 39, 0);

        context.fillStyle = color;
        context.fillRect(boxX, boxY, boxWidth, boxHeight);
        const textX = boxX + paddingX;
        const textY = boxY + boxHeight - paddingY - 2;
        context.lineWidth = 2;
        context.lineJoin = "round";
        context.strokeStyle = "#000000";
        context.strokeText(teamName, textX, textY);
        context.fillStyle = "#ffffff";
        context.fillText(teamName, textX, textY);

        grouped.stops.forEach((stop) => {
          context.strokeStyle = color;
          context.lineWidth = 2;
          context.beginPath();
          context.arc(stop.x, stop.y, 10, 0, Math.PI * 2);
          context.stroke();

          const text = `${Math.round(stop.durationSec)}s`;
          context.font = "13px Arial";
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

    const drawFallback = () => {
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.fillStyle = "#1b2738";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.strokeStyle = "#2b3e59";
      context.strokeRect(0, 0, canvas.width, canvas.height);
      context.fillStyle = "#90a4c2";
      context.font = "16px Arial";
      context.fillText("Map background unavailable", 20, 30);
      drawTracks();
    };

    if (!backgroundSrc) {
      drawFallback();
      return;
    }

    const image = new Image();
    image.onload = () => {
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      drawTracks();
    };
    image.onerror = () => {
      drawFallback();
    };
    image.src = backgroundSrc;
  }, [backgroundSrc, currentTimeSec, renderSettings, rings, teamColorMap, tracks]);

  return <canvas ref={canvasRef} className="mapCanvas" width={900} height={900} />;
}
