import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import JSZip from "jszip";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { RouteGuard } from "@/components/auth/RouteGuard";
import stormPreset from "@/data/hsv_presets/storm-point.json";
import worldsPreset from "@/data/hsv_presets/worlds-edge.json";
import zonesCfgJson from "@/data/zones.vod.json";
import {
  type Box,
  type HSVPreset,
  type ZonesCfg,
  boxToYolo,
  detectPlates,
  makeBatchId,
  pickMinimapZone,
} from "@/lib/plate-detector";

export const Route = createFileRoute("/admin/dataset")({
  component: () => (
    <RouteGuard min="operator">
      <DatasetBuilder />
    </RouteGuard>
  ),
});

const PRESETS: Record<string, HSVPreset> = {
  "storm-point": stormPreset as HSVPreset,
  "worlds-edge": worldsPreset as HSVPreset,
};
const ZONES = zonesCfgJson as unknown as ZonesCfg;

type FrameItem = {
  name: string;
  blob: Blob;
  url: string;
  width: number;
  height: number;
  boxes: Box[];
  detected: boolean;
};

function DatasetBuilder() {
  const [presetKey, setPresetKey] = useState<keyof typeof PRESETS>("storm-point");
  const preset = PRESETS[presetKey];
  const [batchSlug, setBatchSlug] = useState("dataset");
  const [frames, setFrames] = useState<FrameItem[]>([]);
  const [activeIdx, setActiveIdx] = useState<number>(-1);
  const [hExtra, setHExtra] = useState(1);
  const [sExtra, setSExtra] = useState(8);
  const [vExtra, setVExtra] = useState(14);
  const [ignoreBottom, setIgnoreBottom] = useState(90);
  const [restrictROI, setRestrictROI] = useState(true);
  const [activeTeam, setActiveTeam] = useState<number>(1);
  const [drawMode, setDrawMode] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const drawStart = useRef<{ x: number; y: number } | null>(null);

  const active = activeIdx >= 0 ? frames[activeIdx] : null;

  // Загрузка ZIP с картинками
  const onZipUpload = useCallback(async (file: File) => {
    setBusy("Распаковка ZIP…");
    try {
      const zip = await JSZip.loadAsync(file);
      const items: FrameItem[] = [];
      const entries = Object.values(zip.files).filter(
        (e) => !e.dir && /\.(jpe?g|png|webp|bmp)$/i.test(e.name),
      );
      for (const entry of entries) {
        const blob = await entry.async("blob");
        const url = URL.createObjectURL(blob);
        const { width, height } = await loadDims(url);
        items.push({ name: entry.name.split("/").pop()!, blob, url, width, height, boxes: [], detected: false });
      }
      items.sort((a, b) => a.name.localeCompare(b.name));
      setFrames(items);
      setActiveIdx(items.length ? 0 : -1);
    } finally {
      setBusy(null);
    }
  }, []);

  const detectFrame = useCallback(async (idx: number): Promise<Box[]> => {
    const f = frames[idx];
    if (!f) return [];
    const img = await loadImage(f.url);
    const c = document.createElement("canvas");
    c.width = img.naturalWidth;
    c.height = img.naturalHeight;
    const ctx = c.getContext("2d", { willReadFrequently: true })!;
    ctx.drawImage(img, 0, 0);
    const data = ctx.getImageData(0, 0, c.width, c.height).data;
    const roi = restrictROI
      ? pickMinimapZone(ZONES, c.width, c.height)
      : { x: 0, y: 0, w: c.width, h: c.height };
    return detectPlates(data, c.width, c.height, preset, roi, {
      hTolExtra: hExtra,
      sTolExtra: sExtra,
      vTolExtra: vExtra,
      ignoreBottomPx: ignoreBottom,
    });
  }, [frames, preset, hExtra, sExtra, vExtra, ignoreBottom, restrictROI]);

  const runDetectActive = useCallback(async () => {
    if (activeIdx < 0) return;
    setBusy("Детекция кадра…");
    try {
      const boxes = await detectFrame(activeIdx);
      setFrames((arr) => arr.map((f, i) => (i === activeIdx ? { ...f, boxes, detected: true } : f)));
    } finally {
      setBusy(null);
    }
  }, [activeIdx, detectFrame]);

  const runDetectAll = useCallback(async () => {
    setBusy("Детекция всех кадров…");
    try {
      for (let i = 0; i < frames.length; i++) {
        const boxes = await detectFrame(i);
        setFrames((arr) => arr.map((f, j) => (j === i ? { ...f, boxes, detected: true } : f)));
        setBusy(`Детекция ${i + 1}/${frames.length}`);
      }
    } finally {
      setBusy(null);
    }
  }, [frames, detectFrame]);

  const exportZip = useCallback(async () => {
    if (!frames.length) return;
    setBusy("Сборка ZIP…");
    try {
      const batchId = makeBatchId(batchSlug);
      const zip = new JSZip();
      const imgDir = zip.folder("images")!;
      const lblDir = zip.folder("labels")!;
      const classes: string[] = [];
      for (let i = 0; i < 20; i++) {
        const t = preset.teams.find((tt) => tt.slot === i + 1);
        classes.push(t ? `${t.id}__${t.name}` : `slot_${i + 1}`);
      }
      zip.file("classes.txt", classes.join("\n"));
      zip.file("dataset.yaml", buildDatasetYaml(classes));
      for (const f of frames) {
        const ext = f.name.match(/\.(\w+)$/)?.[1] ?? "jpg";
        const stem = f.name.replace(/\.(\w+)$/, "");
        const safe = `${batchId}__${stem}`.replace(/[^A-Za-z0-9._-]+/g, "_");
        imgDir.file(`${safe}.${ext}`, f.blob);
        const lines = f.boxes.map((b) => boxToYolo(b, f.width, f.height));
        lblDir.file(`${safe}.txt`, lines.join("\n"));
      }
      const out = await zip.generateAsync({ type: "blob", compression: "DEFLATE", compressionOptions: { level: 6 } });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(out);
      a.download = `${batchId}.zip`;
      a.click();
      URL.revokeObjectURL(a.href);
    } finally {
      setBusy(null);
    }
  }, [frames, preset, batchSlug]);

  // Отрисовка холста
  useEffect(() => {
    if (!active || !canvasRef.current) return;
    const c = canvasRef.current;
    let cancelled = false;
    loadImage(active.url).then((img) => {
      if (cancelled) return;
      imgRef.current = img;
      c.width = img.naturalWidth;
      c.height = img.naturalHeight;
      drawScene(c, img, active.boxes, preset);
    });
    return () => { cancelled = true; };
  }, [active, preset]);

  useEffect(() => {
    if (!active || !canvasRef.current || !imgRef.current) return;
    drawScene(canvasRef.current, imgRef.current, active.boxes, preset);
  }, [active, preset]);

  // Координаты в canvas pixels с учётом CSS-масштаба
  const eventToCanvasXY = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const c = canvasRef.current!;
    const rect = c.getBoundingClientRect();
    return {
      x: Math.round(((e.clientX - rect.left) / rect.width) * c.width),
      y: Math.round(((e.clientY - rect.top) / rect.height) * c.height),
    };
  };

  const onCanvasMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!active) return;
    const p = eventToCanvasXY(e);
    if (drawMode) {
      drawStart.current = p;
      return;
    }
    // удаление по клику внутри бокса
    const hitIdx = [...active.boxes].reverse().findIndex(
      (b) => p.x >= b.x && p.x <= b.x + b.w && p.y >= b.y && p.y <= b.y + b.h,
    );
    if (hitIdx >= 0 && e.shiftKey) {
      const realIdx = active.boxes.length - 1 - hitIdx;
      const next = active.boxes.filter((_, i) => i !== realIdx);
      setFrames((arr) => arr.map((f, i) => (i === activeIdx ? { ...f, boxes: next } : f)));
    }
  };

  const onCanvasMouseUp = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!drawMode || !drawStart.current || !active) return;
    const p = eventToCanvasXY(e);
    const x = Math.min(drawStart.current.x, p.x);
    const y = Math.min(drawStart.current.y, p.y);
    const w = Math.abs(p.x - drawStart.current.x);
    const h = Math.abs(p.y - drawStart.current.y);
    drawStart.current = null;
    if (w < 6 || h < 6) return;
    const team = preset.teams.find((t) => t.slot === activeTeam);
    if (!team) return;
    // заменяем существующий бокс этого слота, если есть
    const next = active.boxes.filter((b) => b.slot !== activeTeam);
    next.push({
      slot: team.slot,
      teamId: team.id,
      teamName: team.name,
      hex: team.hex,
      x, y, w, h,
      source: "manual",
    });
    setFrames((arr) => arr.map((f, i) => (i === activeIdx ? { ...f, boxes: next } : f)));
  };

  const totalBoxes = useMemo(() => frames.reduce((s, f) => s + f.boxes.length, 0), [frames]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface px-4">
        <div>
          <div className="text-sm font-bold uppercase tracking-wider">Dataset Builder</div>
          <div className="text-xs text-muted-foreground">YOLO labels из ZIP с кадрами · план Б для ИИ-детектора</div>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {busy && <span className="text-primary">{busy}</span>}
          <span>frames: <b className="text-foreground tabular-nums">{frames.length}</b></span>
          <span>boxes: <b className="text-foreground tabular-nums">{totalBoxes}</b></span>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Левая панель: загрузка/настройки/список */}
        <aside className="flex w-[280px] shrink-0 flex-col gap-3 overflow-y-auto border-r border-border bg-surface p-3">
          <section className="space-y-2">
            <Label className="label-eyebrow text-xs">HSV preset</Label>
            <Select value={presetKey} onValueChange={(v) => setPresetKey(v as keyof typeof PRESETS)}>
              <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.keys(PRESETS).map((k) => <SelectItem key={k} value={k}>{k}</SelectItem>)}
              </SelectContent>
            </Select>
          </section>

          <section className="space-y-2">
            <Label className="label-eyebrow text-xs">Batch slug</Label>
            <Input value={batchSlug} onChange={(e) => setBatchSlug(e.target.value)} className="h-8 text-xs" />
            <div className="text-[10px] text-muted-foreground">
              имя: <span className="text-mono">batch_YYYYMMDD-HHMMSS_{batchSlug || "…"}</span>
            </div>
          </section>

          <section className="space-y-2">
            <Label className="label-eyebrow text-xs">ZIP с кадрами</Label>
            <Input
              type="file"
              accept=".zip"
              className="h-8 text-xs"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) onZipUpload(f); }}
            />
          </section>

          <section className="space-y-2 rounded-sm border border-border p-2">
            <div className="label-eyebrow text-xs">HSV tolerances</div>
            <SliderRow label={`H ±${hExtra}`} value={hExtra} min={0} max={10} onChange={setHExtra} />
            <SliderRow label={`S ±${sExtra}`} value={sExtra} min={0} max={40} onChange={setSExtra} />
            <SliderRow label={`V ±${vExtra}`} value={vExtra} min={0} max={60} onChange={setVExtra} />
            <SliderRow label={`Ignore bottom ${ignoreBottom}px`} value={ignoreBottom} min={0} max={200} onChange={setIgnoreBottom} />
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={restrictROI} onChange={(e) => setRestrictROI(e.target.checked)} />
              Restrict to minimap ROI (zones.vod.json)
            </label>
          </section>

          <section className="flex flex-col gap-2">
            <Button size="sm" variant="outline" disabled={!active || !!busy} onClick={runDetectActive}>Detect current</Button>
            <Button size="sm" disabled={!frames.length || !!busy} onClick={runDetectAll}>Detect all</Button>
            <Button size="sm" variant="secondary" disabled={!frames.length || !!busy} onClick={exportZip}>Export ZIP</Button>
          </section>

          <section className="space-y-1">
            <div className="label-eyebrow text-xs">Frames ({frames.length})</div>
            <div className="max-h-[40vh] overflow-y-auto rounded-sm border border-border">
              {frames.map((f, i) => (
                <button
                  key={f.name}
                  onClick={() => setActiveIdx(i)}
                  className={`flex w-full items-center justify-between gap-2 border-b border-border/50 px-2 py-1 text-left text-xs hover:bg-muted ${i === activeIdx ? "bg-primary/15 text-primary" : ""}`}
                >
                  <span className="truncate">{f.name}</span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">{f.boxes.length}</span>
                </button>
              ))}
              {!frames.length && <div className="p-3 text-xs text-muted-foreground">ZIP не загружен</div>}
            </div>
          </section>
        </aside>

        {/* Центр: канвас */}
        <div className="flex min-w-0 flex-1 flex-col bg-background">
          <div className="flex shrink-0 items-center gap-2 border-b border-border bg-surface px-3 py-2">
            <Button
              size="sm"
              variant={drawMode ? "default" : "outline"}
              onClick={() => setDrawMode((v) => !v)}
            >
              {drawMode ? "Drawing: ON (click+drag)" : "Draw box"}
            </Button>
            <span className="text-xs text-muted-foreground">Shift+click внутри бокса — удалить</span>
            <div className="ml-auto text-xs text-muted-foreground">
              {active ? `${active.name} · ${active.width}×${active.height}` : "—"}
            </div>
          </div>
          <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-3">
            {active ? (
              <canvas
                ref={canvasRef}
                onMouseDown={onCanvasMouseDown}
                onMouseUp={onCanvasMouseUp}
                className="max-h-full max-w-full rounded-sm border border-border"
                style={{ cursor: drawMode ? "crosshair" : "default" }}
              />
            ) : (
              <div className="text-sm text-muted-foreground">Загрузите ZIP с кадрами слева</div>
            )}
          </div>
        </div>

        {/* Правая панель: команды */}
        <aside className="flex w-[220px] shrink-0 flex-col gap-1 overflow-y-auto border-l border-border bg-surface p-3">
          <div className="label-eyebrow text-xs">Teams · click to draw</div>
          {preset.teams.map((t) => {
            const has = active?.boxes.some((b) => b.slot === t.slot);
            return (
              <button
                key={t.slot}
                onClick={() => { setActiveTeam(t.slot); setDrawMode(true); }}
                className={`flex items-center gap-2 rounded-sm border px-2 py-1 text-left text-xs transition-colors ${activeTeam === t.slot ? "border-primary bg-primary/10" : "border-border hover:bg-muted"}`}
              >
                <span className="h-3 w-3 shrink-0 rounded-sm border border-border" style={{ backgroundColor: t.hex }} />
                <span className="w-6 shrink-0 tabular-nums text-muted-foreground">{t.slot}</span>
                <span className="truncate">{t.name}</span>
                {has && <span className="ml-auto text-[10px] text-primary">●</span>}
              </button>
            );
          })}
        </aside>
      </div>
    </div>
  );
}

function SliderRow({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (v: number) => void }) {
  return (
    <div className="space-y-1">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <Slider value={[value]} min={min} max={max} step={1} onValueChange={(v) => onChange(v[0] ?? 0)} />
    </div>
  );
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((res, rej) => {
    const img = new Image();
    img.onload = () => res(img);
    img.onerror = rej;
    img.src = url;
  });
}

function loadDims(url: string): Promise<{ width: number; height: number }> {
  return loadImage(url).then((i) => ({ width: i.naturalWidth, height: i.naturalHeight }));
}

function drawScene(c: HTMLCanvasElement, img: HTMLImageElement, boxes: Box[], preset: HSVPreset) {
  const ctx = c.getContext("2d")!;
  ctx.clearRect(0, 0, c.width, c.height);
  ctx.drawImage(img, 0, 0);
  ctx.lineWidth = 2;
  ctx.font = "bold 14px ui-sans-serif, system-ui, sans-serif";
  for (const b of boxes) {
    const team = preset.teams.find((t) => t.slot === b.slot);
    const color = team?.hex ?? "#ff00ff";
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.strokeRect(b.x + 0.5, b.y + 0.5, b.w, b.h);
    const tag = `${b.slot}${b.source === "manual" ? "*" : ""}`;
    const tw = ctx.measureText(tag).width + 8;
    ctx.fillRect(b.x, Math.max(0, b.y - 18), tw, 18);
    ctx.fillStyle = "#000";
    ctx.fillText(tag, b.x + 4, Math.max(12, b.y - 4));
  }
}

function buildDatasetYaml(classes: string[]): string {
  return [
    "# YOLO dataset · auto-generated by Dataset Builder",
    "path: .",
    "train: images",
    "val: images",
    `nc: ${classes.length}`,
    "names:",
    ...classes.map((c, i) => `  ${i}: ${JSON.stringify(c)}`),
    "",
  ].join("\n");
}
