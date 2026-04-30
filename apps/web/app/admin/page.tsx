"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { api } from "../../lib/api";
import {
  JobRecord,
  JobStatus,
  JobType,
  MapAdminConfig,
  MapAssetEntry,
  TextRectZone,
  TextZonesPayload,
  ZonePolygon,
  ZonesPayload,
} from "../../lib/types";

function formatDate(value?: string): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatDuration(durationMs?: number): string {
  if (durationMs === undefined || durationMs <= 0) return "-";
  const sec = Math.floor(durationMs / 1000);
  const mm = Math.floor(sec / 60);
  const ss = sec % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

function formatMmSs(totalSec: number): string {
  const sec = Math.max(0, Math.floor(totalSec));
  const mm = Math.floor(sec / 60);
  const ss = sec % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

function rgbToHsv(r: number, g: number, b: number): [number, number, number] {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const delta = max - min;

  let h = 0;
  if (delta > 0) {
    if (max === rn) h = ((gn - bn) / delta) % 6;
    else if (max === gn) h = (bn - rn) / delta + 2;
    else h = (rn - gn) / delta + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  const s = max === 0 ? 0 : delta / max;
  const v = max;
  return [Math.round(h / 2), Math.round(s * 255), Math.round(v * 255)];
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

type OverlayKind = "hsv" | "zones" | "textZones";

type DragState = {
  kind: OverlayKind;
  startMouseX: number;
  startMouseY: number;
  startX: number;
  startY: number;
};

export default function AdminPage() {
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [jobType, setJobType] = useState<"" | JobType>("");
  const [status, setStatus] = useState<"" | JobStatus>("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mapAssets, setMapAssets] = useState<MapAssetEntry[]>([]);
  const [selectedMap, setSelectedMap] = useState<string>("");
  const [mapConfig, setMapConfig] = useState<MapAdminConfig | null>(null);
  const [configMessage, setConfigMessage] = useState<string | null>(null);
  const [isSavingConfig, setIsSavingConfig] = useState(false);
  const [selectedTeamId, setSelectedTeamId] = useState<string>("");
  const [hsvImageUrl, setHsvImageUrl] = useState<string | null>(null);
  const [hsvImageMeta, setHsvImageMeta] = useState<{ name: string; width: number; height: number } | null>(null);
  const [hsvLower, setHsvLower] = useState<[number, number, number]>([0, 0, 0]);
  const [hsvUpper, setHsvUpper] = useState<[number, number, number]>([179, 255, 255]);
  const [zones, setZones] = useState<ZonePolygon[]>([]);
  const [zoneMessage, setZoneMessage] = useState<string | null>(null);
  const [zonesImageUrl, setZonesImageUrl] = useState<string | null>(null);
  const [zonesImageMeta, setZonesImageMeta] = useState<{ name: string; width: number; height: number } | null>(null);
  const [currentPolygon, setCurrentPolygon] = useState<number[][]>([]);
  const [currentZoneType, setCurrentZoneType] = useState<ZonePolygon["type"]>("forbidden");
  const [transientMaxDwellSec, setTransientMaxDwellSec] = useState<number>(8);
  const [textZones, setTextZones] = useState<TextRectZone[]>([]);
  const [textZoneMessage, setTextZoneMessage] = useState<string | null>(null);
  const [textZonesImageUrl, setTextZonesImageUrl] = useState<string | null>(null);
  const [textZonesImageMeta, setTextZonesImageMeta] = useState<{ name: string; width: number; height: number } | null>(null);
  const [currentTextZone, setCurrentTextZone] = useState<TextRectZone | null>(null);
  const [isDrawingTextZone, setIsDrawingTextZone] = useState(false);
  const [textZoneDraftLabel, setTextZoneDraftLabel] = useState("zone");
  const [hsvFullscreen, setHsvFullscreen] = useState(false);
  const [zonesFullscreen, setZonesFullscreen] = useState(false);
  const [textZonesFullscreen, setTextZonesFullscreen] = useState(false);
  const [hsvOverlayPos, setHsvOverlayPos] = useState({ x: 10, y: 10 });
  const [zonesOverlayPos, setZonesOverlayPos] = useState({ x: 10, y: 10 });
  const [textZonesOverlayPos, setTextZonesOverlayPos] = useState({ x: 10, y: 10 });
  const hsvSourceRef = useRef<HTMLCanvasElement | null>(null);
  const hsvMaskRef = useRef<HTMLCanvasElement | null>(null);
  const zonesCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const textZonesCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const hsvOverlayRef = useRef<HTMLDivElement | null>(null);
  const zonesOverlayRef = useRef<HTMLDivElement | null>(null);
  const textZonesOverlayRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const textZoneDrawStartRef = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      setIsLoading(true);
      try {
        const response = await api.getJobs({
          jobType: jobType || undefined,
          status: status || undefined,
          page: 1,
          pageSize: 50,
        });
        if (!active) return;
        setJobs(response.items);
        setError(null);
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Failed to load jobs");
      } finally {
        if (active) setIsLoading(false);
      }
    };

    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [jobType, status]);

  useEffect(() => {
    let active = true;
    const loadAssets = async () => {
      try {
        const assets = await api.getMapAssets();
        if (!active) return;
        setMapAssets(assets);
        if (!selectedMap && assets.length > 0) {
          setSelectedMap(assets[0].mapName);
        }
      } catch {
        if (!active) return;
        setConfigMessage("Failed to load maps from /maps.");
      }
    };
    void loadAssets();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedMap) return;
    let active = true;
    const loadMapConfig = async () => {
      try {
        const config = await api.getMapAdminConfig(selectedMap);
        if (!active) return;
        setMapConfig(config);
        setConfigMessage(null);
      } catch (err) {
        if (!active) return;
        setConfigMessage(err instanceof Error ? err.message : "Failed to load map config");
      }
    };
    void loadMapConfig();
    return () => {
      active = false;
    };
  }, [selectedMap]);

  const runningCount = useMemo(
    () => jobs.filter((job) => job.status === "running" || job.status === "queued").length,
    [jobs]
  );

  const teamIds = useMemo(() => {
    if (!mapConfig) return [];
    return Object.keys(mapConfig.teamHsv).sort((a, b) => a.localeCompare(b));
  }, [mapConfig]);

  useEffect(() => {
    if (!teamIds.length) return;
    setSelectedTeamId((prev) => (prev && teamIds.includes(prev) ? prev : teamIds[0]));
  }, [teamIds]);

  useEffect(() => {
    if (!mapConfig || !selectedTeamId) return;
    const cfg = mapConfig.teamHsv[selectedTeamId];
    if (!cfg) return;
    setHsvLower([cfg.lower[0], cfg.lower[1], cfg.lower[2]]);
    setHsvUpper([cfg.upper[0], cfg.upper[1], cfg.upper[2]]);
  }, [mapConfig, selectedTeamId]);

  const updateTeamHsvValue = (teamId: string, bound: "lower" | "upper", idx: 0 | 1 | 2, value: number) => {
    setMapConfig((prev) => {
      if (!prev) return prev;
      const team = prev.teamHsv[teamId];
      if (!team) return prev;
      const nextBound = [...team[bound]] as [number, number, number];
      nextBound[idx] = Math.max(0, Math.min(255, value));
      return {
        ...prev,
        teamHsv: {
          ...prev.teamHsv,
          [teamId]: {
            ...team,
            [bound]: nextBound,
          },
        },
      };
    });
  };

  const onSaveMapConfig = async () => {
    if (!selectedMap || !mapConfig) return;
    setIsSavingConfig(true);
    try {
      const updated = await api.updateMapAdminConfig(selectedMap, mapConfig);
      setMapConfig(updated);
      setConfigMessage("Map config saved.");
    } catch (err) {
      setConfigMessage(err instanceof Error ? err.message : "Failed to save map config");
    } finally {
      setIsSavingConfig(false);
    }
  };

  const onUploadToolImage = (file: File, target: "hsv" | "zones" | "textZones") => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      const meta = { name: file.name, width: image.width, height: image.height };
      if (target === "hsv") {
        setHsvImageUrl(url);
        setHsvImageMeta(meta);
      } else if (target === "zones") {
        setZonesImageUrl(url);
        setZonesImageMeta(meta);
        setCurrentPolygon([]);
      } else {
        setTextZonesImageUrl(url);
        setTextZonesImageMeta(meta);
        setCurrentTextZone(null);
      }
    };
    image.src = url;
  };

  useEffect(() => {
    if (!hsvImageUrl || !hsvSourceRef.current || !hsvMaskRef.current) return;
    let active = true;
    const image = new Image();
    image.onload = () => {
      if (!active || !hsvSourceRef.current || !hsvMaskRef.current) return;
      const source = hsvSourceRef.current;
      const mask = hsvMaskRef.current;
      source.width = image.width;
      source.height = image.height;
      mask.width = image.width;
      mask.height = image.height;
      const sourceCtx = source.getContext("2d");
      const maskCtx = mask.getContext("2d");
      if (!sourceCtx || !maskCtx) return;
      sourceCtx.drawImage(image, 0, 0);
      const imageData = sourceCtx.getImageData(0, 0, image.width, image.height);
      const out = maskCtx.createImageData(image.width, image.height);

      for (let i = 0; i < imageData.data.length; i += 4) {
        const r = imageData.data[i];
        const g = imageData.data[i + 1];
        const b = imageData.data[i + 2];
        const [h, s, v] = rgbToHsv(r, g, b);
        const ok =
          h >= hsvLower[0] &&
          h <= hsvUpper[0] &&
          s >= hsvLower[1] &&
          s <= hsvUpper[1] &&
          v >= hsvLower[2] &&
          v <= hsvUpper[2];
        const px = ok ? 255 : 0;
        out.data[i] = px;
        out.data[i + 1] = px;
        out.data[i + 2] = px;
        out.data[i + 3] = 255;
      }
      maskCtx.putImageData(out, 0, 0);
    };
    image.src = hsvImageUrl;
    return () => {
      active = false;
    };
  }, [hsvImageUrl, hsvLower, hsvUpper]);

  const drawZonesCanvas = (ctx: CanvasRenderingContext2D, width: number, height: number) => {
    const zoneColor: Record<ZonePolygon["type"], string> = {
      forbidden: "rgba(230, 60, 60, 0.9)",
      transient: "rgba(240, 160, 20, 0.9)",
      trusted: "rgba(70, 190, 70, 0.9)",
    };
    for (const zone of zones) {
      if (!zone.polygon.length) continue;
      ctx.beginPath();
      zone.polygon.forEach((point, idx) => {
        const x = clamp(point[0], 0, width);
        const y = clamp(point[1], 0, height);
        if (idx === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.closePath();
      ctx.fillStyle = zoneColor[zone.type].replace("0.9", "0.25");
      ctx.fill();
      ctx.strokeStyle = zoneColor[zone.type];
      ctx.lineWidth = 2;
      ctx.stroke();
      const first = zone.polygon[0];
      ctx.fillStyle = zoneColor[zone.type];
      ctx.font = "12px Arial";
      const tail = zone.type === "transient" && zone.max_dwell_sec ? ` (${zone.max_dwell_sec}s)` : "";
      ctx.fillText(`${zone.id}${tail}`, first[0] + 6, first[1] - 6);
    }
    if (currentPolygon.length) {
      ctx.beginPath();
      currentPolygon.forEach((point, idx) => {
        if (idx === 0) ctx.moveTo(point[0], point[1]);
        else ctx.lineTo(point[0], point[1]);
      });
      ctx.strokeStyle = zoneColor[currentZoneType];
      ctx.lineWidth = 2;
      ctx.stroke();
      for (const point of currentPolygon) {
        ctx.beginPath();
        ctx.arc(point[0], point[1], 3.5, 0, Math.PI * 2);
        ctx.fillStyle = "#ffffff";
        ctx.fill();
      }
    }
  };

  useEffect(() => {
    const canvas = zonesCanvasRef.current;
    if (!canvas || !zonesImageUrl) return;
    let active = true;
    const image = new Image();
    image.onload = () => {
      if (!active || !zonesCanvasRef.current) return;
      const localCanvas = zonesCanvasRef.current;
      localCanvas.width = image.width;
      localCanvas.height = image.height;
      const ctx = localCanvas.getContext("2d");
      if (!ctx) return;
      ctx.clearRect(0, 0, image.width, image.height);
      ctx.drawImage(image, 0, 0);
      drawZonesCanvas(ctx, image.width, image.height);
    };
    image.src = zonesImageUrl;
    return () => {
      active = false;
    };
  }, [zonesImageUrl, zones, currentPolygon, currentZoneType]);

  const drawTextZonesCanvas = (ctx: CanvasRenderingContext2D, width: number, height: number) => {
    const drawSingleZone = (zone: TextRectZone, color: string, fillOpacity: number) => {
      const x = clamp(zone.x, 0, width);
      const y = clamp(zone.y, 0, height);
      const w = clamp(zone.width, 1, width - x);
      const h = clamp(zone.height, 1, height - y);
      ctx.fillStyle = color.replace("1)", `${fillOpacity})`);
      ctx.fillRect(x, y, w, h);
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.strokeRect(x, y, w, h);
      const label = zone.label?.trim() ? zone.label.trim() : zone.id;
      ctx.fillStyle = color;
      ctx.font = "12px Arial";
      ctx.fillText(`${label}${zone.enabled === false ? " (off)" : ""}`, x + 6, Math.max(14, y - 6));
    };

    for (const zone of textZones) {
      const color = zone.enabled === false ? "rgba(130, 130, 130, 1)" : "rgba(40, 170, 230, 1)";
      drawSingleZone(zone, color, zone.enabled === false ? 0.18 : 0.24);
    }
    if (currentTextZone) {
      drawSingleZone(currentTextZone, "rgba(250, 215, 70, 1)", 0.12);
    }
  };

  useEffect(() => {
    const canvas = textZonesCanvasRef.current;
    if (!canvas || !textZonesImageUrl) return;
    let active = true;
    const image = new Image();
    image.onload = () => {
      if (!active || !textZonesCanvasRef.current) return;
      const localCanvas = textZonesCanvasRef.current;
      localCanvas.width = image.width;
      localCanvas.height = image.height;
      const ctx = localCanvas.getContext("2d");
      if (!ctx) return;
      ctx.clearRect(0, 0, image.width, image.height);
      ctx.drawImage(image, 0, 0);
      drawTextZonesCanvas(ctx, image.width, image.height);
    };
    image.src = textZonesImageUrl;
    return () => {
      active = false;
    };
  }, [textZonesImageUrl, textZones, currentTextZone]);

  const applyHsvToSelectedTeam = () => {
    if (!selectedTeamId) return;
    setMapConfig((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        teamHsv: {
          ...prev.teamHsv,
          [selectedTeamId]: {
            lower: [...hsvLower],
            upper: [...hsvUpper],
          },
        },
      };
    });
  };

  const onZonesCanvasClick = (event: MouseEvent<HTMLCanvasElement>) => {
    const canvas = zonesCanvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const x = clamp((event.clientX - rect.left) * (canvas.width / Math.max(1, rect.width)), 0, canvas.width);
    const y = clamp((event.clientY - rect.top) * (canvas.height / Math.max(1, rect.height)), 0, canvas.height);
    setCurrentPolygon((prev) => [...prev, [Math.round(x), Math.round(y)]]);
  };

  const closePolygon = () => {
    if (currentPolygon.length < 3) return;
    const nextId = `${currentZoneType}_${zones.length + 1}`;
    const next: ZonePolygon = {
      id: nextId,
      type: currentZoneType,
      polygon: currentPolygon.map((point) => [point[0], point[1]]),
      ...(currentZoneType === "transient" ? { max_dwell_sec: transientMaxDwellSec } : {}),
    };
    setZones((prev) => [...prev, next]);
    setCurrentPolygon([]);
  };

  const loadZonesFromServer = async () => {
    if (!selectedMap) return;
    try {
      const payload = await api.getMapZones(selectedMap);
      setZones(payload.zones ?? []);
      setZoneMessage(`Loaded ${payload.zones?.length ?? 0} zones from server.`);
    } catch (err) {
      setZoneMessage(err instanceof Error ? err.message : "Failed to load zones.");
    }
  };

  const saveZonesToServer = async () => {
    if (!selectedMap) return;
    const imageSize = {
      width: zonesImageMeta?.width ?? 0,
      height: zonesImageMeta?.height ?? 0,
    };
    const payload: ZonesPayload = {
      map: selectedMap,
      image_path: zonesImageMeta?.name ?? undefined,
      image_size: imageSize,
      zones,
    };
    try {
      await api.updateMapZones(selectedMap, payload);
      setZoneMessage(`Saved ${zones.length} zones.`);
    } catch (err) {
      setZoneMessage(err instanceof Error ? err.message : "Failed to save zones.");
    }
  };

  const loadTextZonesFromServer = async () => {
    if (!selectedMap) return;
    try {
      const payload = await api.getMapTextZones(selectedMap);
      setTextZones(payload.zones ?? []);
      setTextZoneMessage(`Loaded ${payload.zones?.length ?? 0} text zones from server.`);
    } catch (err) {
      setTextZoneMessage(err instanceof Error ? err.message : "Failed to load text zones.");
    }
  };

  const saveTextZonesToServer = async () => {
    if (!selectedMap) return;
    const payload: TextZonesPayload = {
      map: selectedMap,
      image_path: textZonesImageMeta?.name ?? undefined,
      image_size: {
        width: textZonesImageMeta?.width ?? 0,
        height: textZonesImageMeta?.height ?? 0,
      },
      zones: textZones,
    };
    try {
      await api.updateMapTextZones(selectedMap, payload);
      setTextZoneMessage(`Saved ${textZones.length} text zones.`);
    } catch (err) {
      setTextZoneMessage(err instanceof Error ? err.message : "Failed to save text zones.");
    }
  };

  const toCanvasPoint = (event: MouseEvent<HTMLCanvasElement>, canvas: HTMLCanvasElement) => {
    const rect = canvas.getBoundingClientRect();
    return {
      x: clamp((event.clientX - rect.left) * (canvas.width / Math.max(1, rect.width)), 0, canvas.width),
      y: clamp((event.clientY - rect.top) * (canvas.height / Math.max(1, rect.height)), 0, canvas.height),
    };
  };

  const onTextZonesMouseDown = (event: MouseEvent<HTMLCanvasElement>) => {
    if (event.button !== 0) return;
    const canvas = textZonesCanvasRef.current;
    if (!canvas) return;
    const start = toCanvasPoint(event, canvas);
    textZoneDrawStartRef.current = start;
    setCurrentTextZone({
      id: `text_zone_${textZones.length + 1}`,
      x: Math.round(start.x),
      y: Math.round(start.y),
      width: 1,
      height: 1,
      label: textZoneDraftLabel.trim() || `zone_${textZones.length + 1}`,
      enabled: true,
    });
    setIsDrawingTextZone(true);
  };

  const onTextZonesMouseMove = (event: MouseEvent<HTMLCanvasElement>) => {
    if (!isDrawingTextZone) return;
    const canvas = textZonesCanvasRef.current;
    const start = textZoneDrawStartRef.current;
    if (!canvas || !start) return;
    const current = toCanvasPoint(event, canvas);
    const x = Math.round(Math.min(start.x, current.x));
    const y = Math.round(Math.min(start.y, current.y));
    const width = Math.round(Math.max(1, Math.abs(current.x - start.x)));
    const height = Math.round(Math.max(1, Math.abs(current.y - start.y)));
    setCurrentTextZone((prev) =>
      prev
        ? {
            ...prev,
            x,
            y,
            width,
            height,
          }
        : prev
    );
  };

  const onTextZonesMouseUp = () => {
    if (!isDrawingTextZone) return;
    setIsDrawingTextZone(false);
    textZoneDrawStartRef.current = null;
    if (!currentTextZone || currentTextZone.width < 4 || currentTextZone.height < 4) {
      setCurrentTextZone(null);
      return;
    }
    setTextZones((prev) => [...prev, currentTextZone]);
    setCurrentTextZone(null);
  };

  const stopOverlayDrag = () => {
    dragRef.current = null;
    window.removeEventListener("mousemove", onOverlayDrag);
    window.removeEventListener("mouseup", stopOverlayDrag);
  };

  const onOverlayDrag = (event: globalThis.MouseEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const panel = drag.kind === "hsv"
      ? hsvOverlayRef.current
      : drag.kind === "zones"
      ? zonesOverlayRef.current
      : textZonesOverlayRef.current;
    if (!panel) return;
    const dx = event.clientX - drag.startMouseX;
    const dy = event.clientY - drag.startMouseY;
    const maxX = Math.max(10, window.innerWidth - panel.offsetWidth - 10);
    const maxY = Math.max(10, window.innerHeight - panel.offsetHeight - 10);
    const next = {
      x: clamp(drag.startX + dx, 10, maxX),
      y: clamp(drag.startY + dy, 10, maxY),
    };
    if (drag.kind === "hsv") setHsvOverlayPos(next);
    else if (drag.kind === "zones") setZonesOverlayPos(next);
    else setTextZonesOverlayPos(next);
  };

  const startOverlayDrag = (kind: OverlayKind, event: MouseEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const pos = kind === "hsv" ? hsvOverlayPos : kind === "zones" ? zonesOverlayPos : textZonesOverlayPos;
    dragRef.current = {
      kind,
      startMouseX: event.clientX,
      startMouseY: event.clientY,
      startX: pos.x,
      startY: pos.y,
    };
    window.addEventListener("mousemove", onOverlayDrag);
    window.addEventListener("mouseup", stopOverlayDrag);
  };

  useEffect(() => {
    return () => {
      stopOverlayDrag();
    };
  }, []);

  return (
    <main className="container">
      <div className="panel adminConfigPanel">
        <div className="adminHeader">
          <h2>Map Config</h2>
          <span className="adminHint">Base HSV preset: mp_storm_point</span>
        </div>
        <div className="adminFilters">
          <label>
            Map
            <select value={selectedMap} onChange={(event) => setSelectedMap(event.target.value)}>
              {mapAssets.map((asset) => (
                <option key={asset.mapName} value={asset.mapName}>
                  {asset.mapName}
                </option>
              ))}
            </select>
          </label>
          <label>
            Frame skip
            <input
              type="number"
              min={1}
              max={120}
              value={mapConfig?.runtime.frameSkip ?? 8}
              onChange={(event) =>
                setMapConfig((prev) =>
                  prev
                    ? {
                        ...prev,
                        runtime: {
                          ...prev.runtime,
                          frameSkip: Math.max(1, Number(event.target.value) || 1),
                        },
                      }
                    : prev
                )
              }
            />
          </label>
        </div>
        <div className="adminRingGrid">
          <label>
            Round 1 start/end (sec)
            <input
              type="text"
              value={mapConfig ? `${mapConfig.runtime.roundWindows.round1.startSec},${mapConfig.runtime.roundWindows.round1.endSec}` : ""}
              onChange={(event) => {
                const [startSec, endSec] = event.target.value.split(",").map((item) => Number(item.trim()));
                if (Number.isFinite(startSec) && Number.isFinite(endSec)) {
                  setMapConfig((prev) =>
                    prev
                      ? {
                          ...prev,
                          runtime: {
                            ...prev.runtime,
                            roundWindows: {
                              ...prev.runtime.roundWindows,
                              round1: { startSec, endSec },
                            },
                          },
                        }
                      : prev
                  );
                }
              }}
            />
          </label>
          <label>
            Round 2 start/end (sec)
            <input
              type="text"
              value={mapConfig ? `${mapConfig.runtime.roundWindows.round2.startSec},${mapConfig.runtime.roundWindows.round2.endSec}` : ""}
              onChange={(event) => {
                const [startSec, endSec] = event.target.value.split(",").map((item) => Number(item.trim()));
                if (Number.isFinite(startSec) && Number.isFinite(endSec)) {
                  setMapConfig((prev) =>
                    prev
                      ? {
                          ...prev,
                          runtime: {
                            ...prev.runtime,
                            roundWindows: {
                              ...prev.runtime.roundWindows,
                              round2: { startSec, endSec },
                            },
                          },
                        }
                      : prev
                  );
                }
              }}
            />
          </label>
          <div className="adminHint" style={{ alignSelf: "end", marginBottom: "14px" }}>
            Durations: R1 {mapConfig ? formatMmSs(mapConfig.runtime.roundWindows.round1.endSec - mapConfig.runtime.roundWindows.round1.startSec) : "--:--"} / R2{" "}
            {mapConfig ? formatMmSs(mapConfig.runtime.roundWindows.round2.endSec - mapConfig.runtime.roundWindows.round2.startSec) : "--:--"}
          </div>
        </div>
        <div className="adminConfigRows">
          <label>
            Zones file
            <input
              type="text"
              value={mapConfig?.polygons.zonesFile ?? ""}
              onChange={(event) =>
                setMapConfig((prev) =>
                  prev ? { ...prev, polygons: { ...prev.polygons, zonesFile: event.target.value } } : prev
                )
              }
            />
          </label>
          <label className="adminCheckLabel">
            <input
              type="checkbox"
              checked={Boolean(mapConfig?.polygons.enabled)}
              onChange={(event) =>
                setMapConfig((prev) =>
                  prev ? { ...prev, polygons: { ...prev.polygons, enabled: event.target.checked } } : prev
                )
              }
            />
            Enable polygon filtering
          </label>
        </div>
        <div className={`adminToolSection ${hsvFullscreen ? "adminFullscreenTool" : ""}`}>
          <h3>HSV Tool (Image + Mask)</h3>
          <div
            ref={hsvOverlayRef}
            className={`adminToolControls ${hsvFullscreen ? "overlay" : ""}`}
            style={hsvFullscreen ? { left: `${hsvOverlayPos.x}px`, top: `${hsvOverlayPos.y}px` } : undefined}
          >
            {hsvFullscreen && (
              <div className="adminOverlayHandle" onMouseDown={(event) => startOverlayDrag("hsv", event)}>
                Drag filters
              </div>
            )}
            <div className="adminRingGrid">
              <label>
                Team for tuning
                <select value={selectedTeamId} onChange={(event) => setSelectedTeamId(event.target.value)}>
                  {teamIds.map((teamId) => (
                    <option key={teamId} value={teamId}>
                      {teamId}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Upload studied image
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) onUploadToolImage(file, "hsv");
                  }}
                />
              </label>
              <div className="adminHint" style={{ alignSelf: "end", marginBottom: "14px" }}>
                {hsvImageMeta ? `${hsvImageMeta.name} (${hsvImageMeta.width}x${hsvImageMeta.height})` : "No image loaded"}
              </div>
            </div>
            <div className="adminSlidersGrid">
              {[
                { key: "H min", idx: 0 as const, max: 179, source: "lower" as const },
                { key: "H max", idx: 0 as const, max: 179, source: "upper" as const },
                { key: "S min", idx: 1 as const, max: 255, source: "lower" as const },
                { key: "S max", idx: 1 as const, max: 255, source: "upper" as const },
                { key: "V min", idx: 2 as const, max: 255, source: "lower" as const },
                { key: "V max", idx: 2 as const, max: 255, source: "upper" as const },
              ].map((item) => {
                const value = item.source === "lower" ? hsvLower[item.idx] : hsvUpper[item.idx];
                return (
                  <label key={item.key}>
                    {item.key}: {value}
                    <input
                      type="range"
                      min={0}
                      max={item.max}
                      step={1}
                      value={value}
                      onChange={(event) => {
                        const val = Number(event.target.value);
                        if (item.source === "lower") {
                          setHsvLower((prev) => {
                            const next = [...prev] as [number, number, number];
                            next[item.idx] = val;
                            return next;
                          });
                        } else {
                          setHsvUpper((prev) => {
                            const next = [...prev] as [number, number, number];
                            next[item.idx] = val;
                            return next;
                          });
                        }
                      }}
                    />
                  </label>
                );
              })}
            </div>
            <div className="adminConfigActions">
              <button className="controlBtn controlBtnPrimary" onClick={applyHsvToSelectedTeam}>
                Apply HSV to selected team
              </button>
              <button className="controlBtn" onClick={() => setHsvFullscreen((prev) => !prev)}>
                {hsvFullscreen ? "Exit fullscreen" : "Open fullscreen"}
              </button>
            </div>
          </div>
          <div className="adminCanvasGrid">
            <div>
              <div className="adminHint">Loaded image</div>
              <canvas ref={hsvSourceRef} className="adminPreviewCanvas" />
            </div>
            <div>
              <div className="adminHint">HSV mask</div>
              <canvas ref={hsvMaskRef} className="adminPreviewCanvas" />
            </div>
          </div>
        </div>

        <div className={`adminToolSection ${zonesFullscreen ? "adminFullscreenTool" : ""}`}>
          <h3>Polygon Tool (Web)</h3>
          <div
            ref={zonesOverlayRef}
            className={`adminToolControls ${zonesFullscreen ? "overlay" : ""}`}
            style={zonesFullscreen ? { left: `${zonesOverlayPos.x}px`, top: `${zonesOverlayPos.y}px` } : undefined}
          >
            {zonesFullscreen && (
              <div className="adminOverlayHandle" onMouseDown={(event) => startOverlayDrag("zones", event)}>
                Drag tools
              </div>
            )}
            <div className="adminRingGrid">
              <label>
                Upload map image
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) onUploadToolImage(file, "zones");
                  }}
                />
              </label>
              <label>
                Zone type
                <select value={currentZoneType} onChange={(event) => setCurrentZoneType(event.target.value as ZonePolygon["type"])}>
                  <option value="forbidden">forbidden</option>
                  <option value="transient">transient</option>
                  <option value="trusted">trusted</option>
                </select>
              </label>
              <label>
                transient max dwell (sec)
                <input
                  type="number"
                  min={1}
                  max={120}
                  step={0.5}
                  value={transientMaxDwellSec}
                  onChange={(event) => setTransientMaxDwellSec(Number(event.target.value) || 8)}
                />
              </label>
            </div>
            <div className="adminConfigActions">
              <button className="controlBtn" onClick={() => setCurrentPolygon((prev) => prev.slice(0, -1))}>
                Undo point
              </button>
              <button className="controlBtn controlBtnPrimary" onClick={closePolygon}>
                Close polygon
              </button>
              <button className="controlBtn" onClick={() => setCurrentPolygon([])}>
                Clear current
              </button>
              <button className="controlBtn" onClick={() => setZones((prev) => prev.slice(0, -1))}>
                Delete last zone
              </button>
              <button className="controlBtn" onClick={() => void loadZonesFromServer()}>
                Load zones
              </button>
              <button className="controlBtn controlBtnPrimary" onClick={() => void saveZonesToServer()}>
                Save zones
              </button>
              <button className="controlBtn" onClick={() => setZonesFullscreen((prev) => !prev)}>
                {zonesFullscreen ? "Exit fullscreen" : "Open fullscreen"}
              </button>
            </div>
          </div>
          {zoneMessage && <div className="adminHint">{zoneMessage}</div>}
          <div className="adminHint">
            LMB click on image to add points. Finish with "Close polygon". Current points: {currentPolygon.length}. Saved zones: {zones.length}.
          </div>
          <canvas ref={zonesCanvasRef} className="adminZonesCanvas" onClick={onZonesCanvasClick} />
          <textarea
            className="calibrationOutput"
            readOnly
            value={JSON.stringify(
              {
                map: selectedMap,
                image_path: zonesImageMeta?.name ?? null,
                image_size: {
                  width: zonesImageMeta?.width ?? 0,
                  height: zonesImageMeta?.height ?? 0,
                },
                zones,
              },
              null,
              2
            )}
          />
        </div>

        <div className={`adminToolSection ${textZonesFullscreen ? "adminFullscreenTool" : ""}`}>
          <h3>OCR Text Zones (Rectangles)</h3>
          <div
            ref={textZonesOverlayRef}
            className={`adminToolControls ${textZonesFullscreen ? "overlay" : ""}`}
            style={textZonesFullscreen ? { left: `${textZonesOverlayPos.x}px`, top: `${textZonesOverlayPos.y}px` } : undefined}
          >
            {textZonesFullscreen && (
              <div className="adminOverlayHandle" onMouseDown={(event) => startOverlayDrag("textZones", event)}>
                Drag text-zones tools
              </div>
            )}
            <div className="adminRingGrid">
              <label>
                Upload map image
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) onUploadToolImage(file, "textZones");
                  }}
                />
              </label>
              <label>
                New zone label
                <input value={textZoneDraftLabel} onChange={(event) => setTextZoneDraftLabel(event.target.value)} />
              </label>
            </div>
            <div className="adminConfigActions">
              <button className="controlBtn" onClick={() => setTextZones((prev) => prev.slice(0, -1))}>
                Delete last zone
              </button>
              <button className="controlBtn" onClick={() => setTextZones([])}>
                Clear all
              </button>
              <button className="controlBtn" onClick={() => void loadTextZonesFromServer()}>
                Load text zones
              </button>
              <button className="controlBtn controlBtnPrimary" onClick={() => void saveTextZonesToServer()}>
                Save text zones
              </button>
              <button className="controlBtn" onClick={() => setTextZonesFullscreen((prev) => !prev)}>
                {textZonesFullscreen ? "Exit fullscreen" : "Open fullscreen"}
              </button>
            </div>
          </div>
          {textZoneMessage && <div className="adminHint">{textZoneMessage}</div>}
          <div className="adminHint">
            Drag with LMB to create rectangle. Saved zones: {textZones.length}. Draft zone label: {textZoneDraftLabel || "zone"}.
          </div>
          <canvas
            ref={textZonesCanvasRef}
            className="adminZonesCanvas"
            onMouseDown={onTextZonesMouseDown}
            onMouseMove={onTextZonesMouseMove}
            onMouseUp={onTextZonesMouseUp}
            onMouseLeave={onTextZonesMouseUp}
          />
          <div className="adminTeamTable">
            <div className="adminTeamHeader">
              <span>ID</span>
              <span>Label / Enabled</span>
              <span>Rect (x,y,w,h)</span>
            </div>
            {textZones.map((zone, idx) => (
              <div key={zone.id} className="adminTeamRow">
                <span>{zone.id}</span>
                <div className="adminHsvInputs">
                  <input
                    value={zone.label ?? ""}
                    onChange={(event) =>
                      setTextZones((prev) => prev.map((item, innerIdx) => (innerIdx === idx ? { ...item, label: event.target.value } : item)))
                    }
                  />
                  <label className="adminCheckLabel">
                    <input
                      type="checkbox"
                      checked={zone.enabled !== false}
                      onChange={(event) =>
                        setTextZones((prev) =>
                          prev.map((item, innerIdx) => (innerIdx === idx ? { ...item, enabled: event.target.checked } : item))
                        )
                      }
                    />
                    enabled
                  </label>
                </div>
                <div className="adminHsvInputs">
                  {(["x", "y", "width", "height"] as const).map((key) => (
                    <input
                      key={`${zone.id}-${key}`}
                      type="number"
                      min={0}
                      value={zone[key]}
                      onChange={(event) =>
                        setTextZones((prev) =>
                          prev.map((item, innerIdx) =>
                            innerIdx === idx ? { ...item, [key]: Math.max(0, Number(event.target.value) || 0) } : item
                          )
                        )
                      }
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
          <textarea
            className="calibrationOutput"
            readOnly
            value={JSON.stringify(
              {
                map: selectedMap,
                image_path: textZonesImageMeta?.name ?? null,
                image_size: {
                  width: textZonesImageMeta?.width ?? 0,
                  height: textZonesImageMeta?.height ?? 0,
                },
                zones: textZones,
              },
              null,
              2
            )}
          />
        </div>

        <div className="adminRingGrid">
          <label>Ring HSV lower (H,S,V)
            <input
              type="text"
              value={mapConfig ? `${mapConfig.ring.hsvLower[0]},${mapConfig.ring.hsvLower[1]},${mapConfig.ring.hsvLower[2]}` : ""}
              onChange={(event) => {
                const parts = event.target.value.split(",").map((item) => Number(item.trim()));
                if (parts.length === 3 && parts.every((item) => Number.isFinite(item))) {
                  setMapConfig((prev) =>
                    prev ? { ...prev, ring: { ...prev.ring, hsvLower: [parts[0], parts[1], parts[2]] } } : prev
                  );
                }
              }}
            />
          </label>
          <label>Ring HSV upper (H,S,V)
            <input
              type="text"
              value={mapConfig ? `${mapConfig.ring.hsvUpper[0]},${mapConfig.ring.hsvUpper[1]},${mapConfig.ring.hsvUpper[2]}` : ""}
              onChange={(event) => {
                const parts = event.target.value.split(",").map((item) => Number(item.trim()));
                if (parts.length === 3 && parts.every((item) => Number.isFinite(item))) {
                  setMapConfig((prev) =>
                    prev ? { ...prev, ring: { ...prev.ring, hsvUpper: [parts[0], parts[1], parts[2]] } } : prev
                  );
                }
              }}
            />
          </label>
          <label>Ring sample step (frames)
            <input
              type="number"
              min={1}
              max={5000}
              value={mapConfig?.ring.sampleStepFrames ?? 1000}
              onChange={(event) =>
                setMapConfig((prev) =>
                  prev ? { ...prev, ring: { ...prev.ring, sampleStepFrames: Math.max(1, Number(event.target.value) || 1) } } : prev
                )
              }
            />
          </label>
          <label>Ring Hough P2
            <input
              type="number"
              min={1}
              max={300}
              value={mapConfig?.ring.houghP2 ?? 100}
              onChange={(event) =>
                setMapConfig((prev) => (prev ? { ...prev, ring: { ...prev.ring, houghP2: Math.max(1, Number(event.target.value) || 1) } } : prev))
              }
            />
          </label>
          <label>Ring Gray min/max
            <input
              type="text"
              value={mapConfig ? `${mapConfig.ring.grayMin},${mapConfig.ring.grayMax}` : ""}
              onChange={(event) => {
                const [minValue, maxValue] = event.target.value.split(",").map((item) => Number(item.trim()));
                if (Number.isFinite(minValue) && Number.isFinite(maxValue)) {
                  setMapConfig((prev) =>
                    prev ? { ...prev, ring: { ...prev.ring, grayMin: minValue, grayMax: maxValue } } : prev
                  );
                }
              }}
            />
          </label>
          <label>Ring radius % min/max
            <input
              type="text"
              value={mapConfig ? `${mapConfig.ring.minRPct},${mapConfig.ring.maxRPct}` : ""}
              onChange={(event) => {
                const [minValue, maxValue] = event.target.value.split(",").map((item) => Number(item.trim()));
                if (Number.isFinite(minValue) && Number.isFinite(maxValue)) {
                  setMapConfig((prev) =>
                    prev ? { ...prev, ring: { ...prev.ring, minRPct: minValue, maxRPct: maxValue } } : prev
                  );
                }
              }}
            />
          </label>
        </div>

        <div className="adminTeamTable">
          <div className="adminTeamHeader">
            <span>Team</span>
            <span>HSV lower (H,S,V)</span>
            <span>HSV upper (H,S,V)</span>
          </div>
          {teamIds.map((teamId) => {
            const team = mapConfig?.teamHsv[teamId];
            if (!team) return null;
            return (
              <div key={teamId} className="adminTeamRow">
                <span>{teamId}</span>
                <div className="adminHsvInputs">
                  {[0, 1, 2].map((index) => (
                    <input
                      key={`${teamId}-lower-${index}`}
                      type="number"
                      min={0}
                      max={255}
                      value={team.lower[index as 0 | 1 | 2]}
                      onChange={(event) => updateTeamHsvValue(teamId, "lower", index as 0 | 1 | 2, Number(event.target.value))}
                    />
                  ))}
                </div>
                <div className="adminHsvInputs">
                  {[0, 1, 2].map((index) => (
                    <input
                      key={`${teamId}-upper-${index}`}
                      type="number"
                      min={0}
                      max={255}
                      value={team.upper[index as 0 | 1 | 2]}
                      onChange={(event) => updateTeamHsvValue(teamId, "upper", index as 0 | 1 | 2, Number(event.target.value))}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
        <div className="adminConfigActions">
          <button className="controlBtn controlBtnPrimary" onClick={() => void onSaveMapConfig()} disabled={isSavingConfig}>
            {isSavingConfig ? "Saving..." : "Save map config"}
          </button>
          {configMessage && <span className="adminHint">{configMessage}</span>}
        </div>
      </div>
      <div className="panel">
        <div className="adminHeader">
          <h2>Admin Jobs</h2>
          <div className="adminHeaderMeta">
            <span>Active: {runningCount}</span>
            <Link href="/">Back to player</Link>
          </div>
        </div>

        <div className="adminFilters">
          <label>
            Job type
            <select value={jobType} onChange={(event) => setJobType(event.target.value as "" | JobType)}>
              <option value="">All</option>
              <option value="ingest">ingest</option>
              <option value="analysis">analysis</option>
            </select>
          </label>
          <label>
            Status
            <select value={status} onChange={(event) => setStatus(event.target.value as "" | JobStatus)}>
              <option value="">All</option>
              <option value="queued">queued</option>
              <option value="running">running</option>
              <option value="completed">completed</option>
              <option value="failed">failed</option>
            </select>
          </label>
        </div>

        {error && <div className="adminError">{error}</div>}
        {isLoading && <div className="adminHint">Refreshing jobs (5s)...</div>}

        <div className="jobsGrid">
          {jobs.map((job) => (
            <article key={job.id} className="jobCard">
              <div className="jobCardTop">
                <strong>{job.jobType}</strong>
                <span className={`jobStatus jobStatus_${job.status}`}>{job.status}</span>
              </div>
              <div className="jobLine">ID: <code>{job.id}</code></div>
              <div className="jobLine">Command: <code>{job.command}</code></div>
              <div className="jobLine">Current: {job.currentAction ?? "-"}</div>
              <div className="jobLine">Heartbeat: {formatDate(job.lastHeartbeatAt)}</div>
              <div className="jobLine">Queued: {formatDate(job.queuedAt)}</div>
              <div className="jobLine">Started: {formatDate(job.startedAt)}</div>
              <div className="jobLine">Finished: {formatDate(job.finishedAt)}</div>
              <div className="jobLine">Duration: {formatDuration(job.durationMs)}</div>

              <div className="jobProgressWrap">
                <div className="jobProgressMeta">
                  <span>Progress</span>
                  <span>{Math.max(0, Math.min(100, Math.round(job.progressPercent)))}%</span>
                </div>
                <div className="jobProgressBar">
                  <div style={{ width: `${Math.max(0, Math.min(100, job.progressPercent))}%` }} />
                </div>
              </div>

              {job.teamStatuses.length > 0 && (
                <div className="jobTeams">
                  <div className="jobTeamsTitle">Teams</div>
                  {job.teamStatuses.map((team) => (
                    <div key={`${job.id}-${team.teamId}`} className="jobTeamRow">
                      <span>{team.teamName}</span>
                      <span>{team.status}</span>
                      <span>{Math.round(team.progressPercent)}%</span>
                      <span className="jobTeamError">{team.error ?? ""}</span>
                    </div>
                  ))}
                </div>
              )}

              {job.errors.length > 0 && (
                <div className="jobErrors">
                  {job.errors.map((item, idx) => (
                    <div key={`${job.id}-err-${idx}`}>{item}</div>
                  ))}
                </div>
              )}
            </article>
          ))}
          {jobs.length === 0 && <div className="adminHint">No jobs found.</div>}
        </div>
      </div>
    </main>
  );
}

