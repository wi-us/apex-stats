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
  buildHSVMaskCanvas,
  detectPlates,
  makeBatchId,
  pickMinimapZone,
  refineBoxesByPeaks,
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

type DragMode =
  | { kind: "move"; idx: number; startBox: Box; px: number; py: number }
  | { kind: "resize"; idx: number; startBox: Box; handle: string; px: number; py: number }
  | { kind: "draw"; x: number; y: number }
  | null;

const HANDLE_PX = 8; // в координатах кадра — но мы рендерим на канвасе с учётом zoom

function DatasetBuilder() {
  const [presetKey, setPresetKey] = useState<keyof typeof PRESETS>("storm-point");
  const preset = PRESETS[presetKey];
  const [batchSlug, setBatchSlug] = useState("dataset");
  const [frames, setFrames] = useState<FrameItem[]>([]);
  const [activeIdx, setActiveIdx] = useState<number>(-1);
  const [selectedBox, setSelectedBox] = useState<number>(-1);

  // HSV
  const [hExtra, setHExtra] = useState(1);
  const [sExtra, setSExtra] = useState(8);
  const [vExtra, setVExtra] = useState(14);
  const [ignoreBottom, setIgnoreBottom] = useState(90);
  const [restrictROI, setRestrictROI] = useState(true);

  // Split / морфология
  const [expectedW, setExpectedW] = useState(70);
  const [expectedH, setExpectedH] = useState(18);
  const [erosion, setErosion] = useState(0);
  const [maxPerTeam, setMaxPerTeam] = useState(6);

  // Peak refine
  const [autoPeakRefine, setAutoPeakRefine] = useState(true);
  const [peakVThr, setPeakVThr] = useState(190);
  const [peakMinRun, setPeakMinRun] = useState(6);

  // HSV mask overlay
  const [maskMode, setMaskMode] = useState<"off" | "active" | "all">("off");
  const [maskAlpha, setMaskAlpha] = useState(130);

  // tool state
  const [activeTeam, setActiveTeam] = useState<number>(1);
  const [tool, setTool] = useState<"select" | "draw">("select");
  const [zoom, setZoom] = useState(1.5);
  const [busy, setBusy] = useState<string | null>(null);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const maskRef = useRef<HTMLCanvasElement | null>(null);
  const rgbaRef = useRef<{ data: Uint8ClampedArray; w: number; h: number } | null>(null);
  const dragRef = useRef<DragMode>(null);
  const undoRef = useRef<Box[][]>([]); // стек прошлых состояний boxes текущего кадра

  const active = activeIdx >= 0 ? frames[activeIdx] : null;

  // ============= ZIP load =============
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
      setSelectedBox(-1);
      undoRef.current = [];
    } finally {
      setBusy(null);
    }
  }, []);

  // ============= Detection =============
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
      expectedPlateW: expectedW,
      expectedPlateH: expectedH,
      erosionPx: erosion,
      maxBoxesPerTeam: maxPerTeam,
    });
  }, [frames, preset, hExtra, sExtra, vExtra, ignoreBottom, restrictROI, expectedW, expectedH, erosion, maxPerTeam]);

  const pushUndo = useCallback(() => {
    if (!active) return;
    undoRef.current.push(active.boxes.map((b) => ({ ...b })));
    if (undoRef.current.length > 50) undoRef.current.shift();
  }, [active]);

  const setActiveBoxes = useCallback((updater: (b: Box[]) => Box[]) => {
    setFrames((arr) => arr.map((f, i) => (i === activeIdx ? { ...f, boxes: updater(f.boxes) } : f)));
  }, [activeIdx]);

  const runDetectActive = useCallback(async () => {
    if (activeIdx < 0) return;
    pushUndo();
    setBusy("Детекция кадра…");
    try {
      let boxes = await detectFrame(activeIdx);
      if (autoPeakRefine && rgbaRef.current) {
        const { data, w, h } = rgbaRef.current;
        boxes = refineBoxesByPeaks(data, w, h, boxes, {
          vThreshold: peakVThr, minRunPx: peakMinRun,
        }).map((b) => ({ ...b, source: "auto" as const }));
      }
      setFrames((arr) => arr.map((f, i) => (i === activeIdx ? { ...f, boxes, detected: true } : f)));
      setSelectedBox(-1);
    } finally {
      setBusy(null);
    }
  }, [activeIdx, detectFrame, pushUndo, autoPeakRefine, peakVThr, peakMinRun]);

  const runDetectAll = useCallback(async () => {
    setBusy("Детекция всех кадров…");
    try {
      for (let i = 0; i < frames.length; i++) {
        let boxes = await detectFrame(i);
        if (autoPeakRefine) {
          // нужен rgba для конкретного кадра
          const f = frames[i];
          const img = await loadImage(f.url);
          const cv = document.createElement("canvas");
          cv.width = img.naturalWidth; cv.height = img.naturalHeight;
          const cx = cv.getContext("2d", { willReadFrequently: true })!;
          cx.drawImage(img, 0, 0);
          const data = cx.getImageData(0, 0, cv.width, cv.height).data;
          boxes = refineBoxesByPeaks(data, cv.width, cv.height, boxes, {
            vThreshold: peakVThr, minRunPx: peakMinRun,
          }).map((b) => ({ ...b, source: "auto" as const }));
        }
        setFrames((arr) => arr.map((f, j) => (j === i ? { ...f, boxes, detected: true } : f)));
        setBusy(`Детекция ${i + 1}/${frames.length}`);
      }
    } finally {
      setBusy(null);
    }
  }, [frames, detectFrame, autoPeakRefine, peakVThr, peakMinRun]);

  const refineNow = useCallback(() => {
    if (!active || !rgbaRef.current) return;
    pushUndo();
    const { data, w, h } = rgbaRef.current;
    setActiveBoxes((bs) => refineBoxesByPeaks(data, w, h, bs, {
      vThreshold: peakVThr, minRunPx: peakMinRun,
    }));
    setSelectedBox(-1);
  }, [active, pushUndo, setActiveBoxes, peakVThr, peakMinRun]);

  const copyFromPrev = useCallback(() => {
    if (activeIdx <= 0) return;
    const prev = frames[activeIdx - 1];
    if (!prev) return;
    pushUndo();
    setActiveBoxes(() => prev.boxes.map((b) => ({ ...b, source: "manual" as const })));
  }, [activeIdx, frames, pushUndo, setActiveBoxes]);

  // ============= Export =============
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

  // ============= Canvas render =============
  useEffect(() => {
    if (!active || !canvasRef.current) return;
    let cancelled = false;
    loadImage(active.url).then((img) => {
      if (cancelled) return;
      imgRef.current = img;
      const c = canvasRef.current!;
      c.width = img.naturalWidth;
      c.height = img.naturalHeight;
      // cache rgba для peak-refine и mask
      const tmp = document.createElement("canvas");
      tmp.width = img.naturalWidth; tmp.height = img.naturalHeight;
      const tx = tmp.getContext("2d", { willReadFrequently: true })!;
      tx.drawImage(img, 0, 0);
      rgbaRef.current = { data: tx.getImageData(0, 0, tmp.width, tmp.height).data, w: tmp.width, h: tmp.height };
      maskRef.current = null;
      drawScene(c, img, active.boxes, preset, selectedBox, null);
    });
    return () => { cancelled = true; };
  }, [active?.url]); // eslint-disable-line react-hooks/exhaustive-deps

  // пересоздание маски при смене параметров/команды
  useEffect(() => {
    if (!active || !rgbaRef.current || maskMode === "off") {
      maskRef.current = null;
    } else {
      const { data, w, h } = rgbaRef.current;
      const roi = restrictROI ? pickMinimapZone(ZONES, w, h) : { x: 0, y: 0, w, h };
      maskRef.current = buildHSVMaskCanvas(data, w, h, preset, roi, {
        hTolExtra: hExtra, sTolExtra: sExtra, vTolExtra: vExtra,
        alpha: maskAlpha, mode: maskMode === "active" ? "active" : "all", activeSlot: activeTeam,
      });
    }
    if (canvasRef.current && imgRef.current && active) {
      drawScene(canvasRef.current, imgRef.current, active.boxes, preset, selectedBox, maskRef.current);
    }
  }, [active, maskMode, maskAlpha, hExtra, sExtra, vExtra, activeTeam, preset, restrictROI, selectedBox]);

  useEffect(() => {
    if (!active || !canvasRef.current || !imgRef.current) return;
    drawScene(canvasRef.current, imgRef.current, active.boxes, preset, selectedBox, maskRef.current);
  }, [active, preset, selectedBox]);

  const eventToCanvasXY = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const c = canvasRef.current!;
    const rect = c.getBoundingClientRect();
    return {
      x: Math.round(((e.clientX - rect.left) / rect.width) * c.width),
      y: Math.round(((e.clientY - rect.top) / rect.height) * c.height),
    };
  };

  const hitHandle = (b: Box, p: { x: number; y: number }, tol: number): string | null => {
    const corners: [string, number, number][] = [
      ["nw", b.x, b.y],
      ["ne", b.x + b.w, b.y],
      ["sw", b.x, b.y + b.h],
      ["se", b.x + b.w, b.y + b.h],
      ["n", b.x + b.w / 2, b.y],
      ["s", b.x + b.w / 2, b.y + b.h],
      ["w", b.x, b.y + b.h / 2],
      ["e", b.x + b.w, b.y + b.h / 2],
    ];
    for (const [name, hx, hy] of corners) {
      if (Math.abs(p.x - hx) <= tol && Math.abs(p.y - hy) <= tol) return name;
    }
    return null;
  };

  const hitBox = (boxes: Box[], p: { x: number; y: number }): number => {
    // ищем сверху (новые рисуются позже) с приоритетом меньшей площади
    let bestIdx = -1, bestArea = Infinity;
    for (let i = boxes.length - 1; i >= 0; i--) {
      const b = boxes[i];
      if (p.x >= b.x && p.x <= b.x + b.w && p.y >= b.y && p.y <= b.y + b.h) {
        const a = b.w * b.h;
        if (a < bestArea) { bestArea = a; bestIdx = i; }
      }
    }
    return bestIdx;
  };

  const onCanvasMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!active) return;
    const p = eventToCanvasXY(e);
    const tol = Math.max(4, Math.round(HANDLE_PX / zoom));

    if (tool === "draw") {
      pushUndo();
      dragRef.current = { kind: "draw", x: p.x, y: p.y };
      return;
    }

    // select / move / resize
    if (selectedBox >= 0 && active.boxes[selectedBox]) {
      const handle = hitHandle(active.boxes[selectedBox], p, tol);
      if (handle) {
        pushUndo();
        dragRef.current = { kind: "resize", idx: selectedBox, startBox: { ...active.boxes[selectedBox] }, handle, px: p.x, py: p.y };
        return;
      }
    }
    const idx = hitBox(active.boxes, p);
    if (idx >= 0) {
      setSelectedBox(idx);
      if (e.shiftKey) {
        pushUndo();
        setActiveBoxes((bs) => bs.filter((_, i) => i !== idx));
        setSelectedBox(-1);
        return;
      }
      pushUndo();
      dragRef.current = { kind: "move", idx, startBox: { ...active.boxes[idx] }, px: p.x, py: p.y };
    } else {
      setSelectedBox(-1);
    }
  };

  const onCanvasMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const d = dragRef.current;
    if (!d || !active) return;
    const p = eventToCanvasXY(e);

    if (d.kind === "draw") {
      // показываем превью: перерисуем сцену + временный rect
      const c = canvasRef.current!;
      const img = imgRef.current!;
      drawScene(c, img, active.boxes, preset, selectedBox, maskRef.current);
      const ctx = c.getContext("2d")!;
      ctx.strokeStyle = "#fff";
      ctx.setLineDash([4, 3]);
      ctx.lineWidth = 2;
      ctx.strokeRect(d.x + 0.5, d.y + 0.5, p.x - d.x, p.y - d.y);
      ctx.setLineDash([]);
      return;
    }
    if (d.kind === "move") {
      const dx = p.x - d.px, dy = p.y - d.py;
      setActiveBoxes((bs) => bs.map((b, i) => i === d.idx
        ? { ...b, x: clamp(d.startBox.x + dx, 0, active.width - b.w), y: clamp(d.startBox.y + dy, 0, active.height - b.h) }
        : b));
      return;
    }
    if (d.kind === "resize") {
      const dx = p.x - d.px, dy = p.y - d.py;
      const s = d.startBox;
      let x = s.x, y = s.y, w = s.w, h = s.h;
      if (d.handle.includes("e")) w = Math.max(4, s.w + dx);
      if (d.handle.includes("s")) h = Math.max(4, s.h + dy);
      if (d.handle.includes("w")) { w = Math.max(4, s.w - dx); x = s.x + (s.w - w); }
      if (d.handle.includes("n")) { h = Math.max(4, s.h - dy); y = s.y + (s.h - h); }
      setActiveBoxes((bs) => bs.map((b, i) => i === d.idx ? { ...b, x, y, w, h, source: "manual" as const } : b));
    }
  };

  const onCanvasMouseUp = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const d = dragRef.current;
    dragRef.current = null;
    if (!d || !active) return;
    if (d.kind === "draw") {
      const p = eventToCanvasXY(e);
      const x = Math.min(d.x, p.x);
      const y = Math.min(d.y, p.y);
      const w = Math.abs(p.x - d.x);
      const h = Math.abs(p.y - d.y);
      if (w < 4 || h < 4) return;
      const team = preset.teams.find((t) => t.slot === activeTeam);
      if (!team) return;
      const newBox: Box = {
        slot: team.slot, teamId: team.id, teamName: team.name, hex: team.hex,
        x, y, w, h, source: "manual",
      };
      setActiveBoxes((bs) => {
        const next = [...bs, newBox];
        setSelectedBox(next.length - 1);
        return next;
      });
    }
  };

  // ============= Hotkeys =============
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (!active) return;
      // navigation
      if (e.key === "PageDown" || (e.key === "ArrowRight" && e.altKey)) {
        e.preventDefault();
        setActiveIdx((i) => Math.min(frames.length - 1, i + 1));
        setSelectedBox(-1);
        return;
      }
      if (e.key === "PageUp" || (e.key === "ArrowLeft" && e.altKey)) {
        e.preventDefault();
        setActiveIdx((i) => Math.max(0, i - 1));
        setSelectedBox(-1);
        return;
      }
      // undo
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        const prev = undoRef.current.pop();
        if (prev) setActiveBoxes(() => prev);
        return;
      }
      // copy from previous frame
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") {
        e.preventDefault();
        copyFromPrev();
        return;
      }
      // tool switch
      if (e.key === "b" || e.key === "B") { setTool("draw"); return; }
      if (e.key === "v" || e.key === "V" || e.key === "Escape") { setTool("select"); setSelectedBox(-1); return; }
      // number → team (1..9, 0 = 10)
      if (/^[0-9]$/.test(e.key)) {
        const slot = e.key === "0" ? 10 : parseInt(e.key, 10);
        if (e.shiftKey) {
          // shift+N = слоты 11..20
          setActiveTeam(Math.min(20, slot + 10));
        } else {
          setActiveTeam(slot);
        }
        return;
      }
      // delete selected
      if ((e.key === "Delete" || e.key === "Backspace") && selectedBox >= 0) {
        e.preventDefault();
        pushUndo();
        setActiveBoxes((bs) => bs.filter((_, i) => i !== selectedBox));
        setSelectedBox(-1);
        return;
      }
      // nudge selected
      if (selectedBox >= 0 && ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) {
        e.preventDefault();
        const step = e.shiftKey ? 10 : 1;
        setActiveBoxes((bs) => bs.map((b, i) => {
          if (i !== selectedBox) return b;
          let { x, y } = b;
          if (e.key === "ArrowUp") y -= step;
          if (e.key === "ArrowDown") y += step;
          if (e.key === "ArrowLeft") x -= step;
          if (e.key === "ArrowRight") x += step;
          return { ...b, x: clamp(x, 0, active.width - b.w), y: clamp(y, 0, active.height - b.h) };
        }));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, frames.length, selectedBox, copyFromPrev, pushUndo, setActiveBoxes]);

  const totalBoxes = useMemo(() => frames.reduce((s, f) => s + f.boxes.length, 0), [frames]);
  const teamCounts = useMemo(() => {
    const m = new Map<number, number>();
    if (active) for (const b of active.boxes) m.set(b.slot, (m.get(b.slot) ?? 0) + 1);
    return m;
  }, [active]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface px-4">
        <div>
          <div className="text-sm font-bold uppercase tracking-wider">Dataset Builder</div>
          <div className="text-xs text-muted-foreground">
            YOLO labels · multi-box · split слипшихся плашек · hotkeys: B/V draw·select, 1-9 team, Del, Ctrl+Z, Ctrl+D copy prev, PgUp/PgDn
          </div>
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          {busy && <span className="text-primary">{busy}</span>}
          <span>frames: <b className="text-foreground tabular-nums">{frames.length}</b></span>
          <span>boxes: <b className="text-foreground tabular-nums">{totalBoxes}</b></span>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Левая панель */}
        <aside className="flex w-[300px] shrink-0 flex-col gap-3 overflow-y-auto border-r border-border bg-surface p-3">
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
              Restrict to minimap ROI
            </label>
          </section>

          <section className="space-y-2 rounded-sm border border-border p-2">
            <div className="label-eyebrow text-xs">Split & morphology</div>
            <SliderRow label={`Expected plate W ${expectedW}px`} value={expectedW} min={20} max={160} onChange={setExpectedW} />
            <SliderRow label={`Expected plate H ${expectedH}px`} value={expectedH} min={8} max={48} onChange={setExpectedH} />
            <SliderRow label={`Erosion ${erosion}px`} value={erosion} min={0} max={3} onChange={setErosion} />
            <SliderRow label={`Max boxes/team ${maxPerTeam}`} value={maxPerTeam} min={1} max={12} onChange={setMaxPerTeam} />
            <p className="text-[10px] text-muted-foreground">
              Если плашки одной команды слипаются — увеличь Erosion (1-2px), а Expected W/H поможет разрезать большой блоб на N кусков.
            </p>
          </section>

          <section className="space-y-2 rounded-sm border border-border p-2">
            <div className="label-eyebrow text-xs">Peak refine (V-projection)</div>
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={autoPeakRefine} onChange={(e) => setAutoPeakRefine(e.target.checked)} />
              Auto после Detect
            </label>
            <SliderRow label={`V threshold ${peakVThr}`} value={peakVThr} min={120} max={250} onChange={setPeakVThr} />
            <SliderRow label={`Min run ${peakMinRun}px`} value={peakMinRun} min={2} max={20} onChange={setPeakMinRun} />
            <Button size="sm" variant="outline" disabled={!active || !!busy} onClick={refineNow}>
              Refine current frame
            </Button>
            <p className="text-[10px] text-muted-foreground">
              Режет/уточняет boxы по столбцам ярких пикселей (текст плашки). Помогает отделить соседних игроков и обрезать края.
            </p>
          </section>

          <section className="space-y-2 rounded-sm border border-border p-2">
            <div className="label-eyebrow text-xs">HSV mask overlay</div>
            <div className="flex gap-1">
              {(["off", "active", "all"] as const).map((m) => (
                <Button
                  key={m}
                  size="sm"
                  variant={maskMode === m ? "default" : "outline"}
                  className="h-7 flex-1 text-[11px]"
                  onClick={() => setMaskMode(m)}
                >
                  {m}
                </Button>
              ))}
            </div>
            <SliderRow label={`Alpha ${maskAlpha}`} value={maskAlpha} min={40} max={220} onChange={setMaskAlpha} />
            <p className="text-[10px] text-muted-foreground">
              "active" — маска только выбранной команды (R-панель), "all" — все команды цветом hex.
            </p>
          </section>

          <section className="flex flex-col gap-2">
            <Button size="sm" variant="outline" disabled={!active || !!busy} onClick={runDetectActive}>Detect current</Button>
            <Button size="sm" disabled={!frames.length || !!busy} onClick={runDetectAll}>Detect all</Button>
            <Button size="sm" variant="outline" disabled={activeIdx <= 0 || !!busy} onClick={copyFromPrev}>Copy from prev frame (Ctrl+D)</Button>
            <Button size="sm" variant="secondary" disabled={!frames.length || !!busy} onClick={exportZip}>Export ZIP</Button>
          </section>

          <section className="space-y-1">
            <div className="label-eyebrow text-xs">Frames ({frames.length})</div>
            <div className="max-h-[35vh] overflow-y-auto rounded-sm border border-border">
              {frames.map((f, i) => (
                <button
                  key={f.name}
                  onClick={() => { setActiveIdx(i); setSelectedBox(-1); }}
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

        {/* Центр */}
        <div className="flex min-w-0 flex-1 flex-col bg-background">
          <div className="flex shrink-0 items-center gap-2 border-b border-border bg-surface px-3 py-2">
            <Button size="sm" variant={tool === "select" ? "default" : "outline"} onClick={() => setTool("select")}>Select (V)</Button>
            <Button size="sm" variant={tool === "draw" ? "default" : "outline"} onClick={() => setTool("draw")}>Draw (B)</Button>
            <span className="text-xs text-muted-foreground">click — выбрать, drag углы — ресайз, Shift+click — удалить</span>
            <div className="ml-auto flex items-center gap-2">
              <span className="text-[10px] text-muted-foreground">Zoom</span>
              <Slider className="w-32" value={[zoom]} min={0.5} max={4} step={0.25} onValueChange={(v) => setZoom(v[0] ?? 1)} />
              <span className="w-8 text-xs tabular-nums">{zoom.toFixed(2)}×</span>
              <span className="text-xs text-muted-foreground">
                {active ? `${active.name} · ${active.width}×${active.height}` : "—"}
              </span>
            </div>
          </div>
          <div className="flex min-h-0 flex-1 items-start justify-start overflow-auto p-3">
            {active ? (
              <canvas
                ref={canvasRef}
                onMouseDown={onCanvasMouseDown}
                onMouseMove={onCanvasMouseMove}
                onMouseUp={onCanvasMouseUp}
                className="rounded-sm border border-border"
                style={{
                  width: active.width * zoom,
                  height: active.height * zoom,
                  imageRendering: "pixelated",
                  cursor: tool === "draw" ? "crosshair" : "default",
                }}
              />
            ) : (
              <div className="m-auto text-sm text-muted-foreground">Загрузите ZIP с кадрами слева</div>
            )}
          </div>
        </div>

        {/* Правая панель */}
        <aside className="flex w-[240px] shrink-0 flex-col gap-1 overflow-y-auto border-l border-border bg-surface p-3">
          <div className="label-eyebrow text-xs">Teams · нажмите для выбора</div>
          <p className="mb-1 text-[10px] text-muted-foreground">
            1-9, 0 — слоты 1..10. Shift+1..9 — слоты 11..19. Click — выбрать команду для рисования.
          </p>
          {preset.teams.map((t) => {
            const cnt = teamCounts.get(t.slot) ?? 0;
            return (
              <button
                key={t.slot}
                onClick={() => { setActiveTeam(t.slot); setTool("draw"); }}
                className={`flex items-center gap-2 rounded-sm border px-2 py-1 text-left text-xs transition-colors ${activeTeam === t.slot ? "border-primary bg-primary/10" : "border-border hover:bg-muted"}`}
              >
                <span className="h-3 w-3 shrink-0 rounded-sm border border-border" style={{ backgroundColor: t.hex }} />
                <span className="w-6 shrink-0 tabular-nums text-muted-foreground">{t.slot}</span>
                <span className="truncate">{t.name}</span>
                {cnt > 0 && <span className="ml-auto rounded bg-primary/20 px-1 text-[10px] text-primary tabular-nums">{cnt}</span>}
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

function clamp(v: number, lo: number, hi: number) { return Math.max(lo, Math.min(hi, v)); }

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

function drawScene(
  c: HTMLCanvasElement,
  img: HTMLImageElement,
  boxes: Box[],
  preset: HSVPreset,
  selectedIdx: number,
  mask: HTMLCanvasElement | null,
) {
  const ctx = c.getContext("2d")!;
  ctx.clearRect(0, 0, c.width, c.height);
  ctx.drawImage(img, 0, 0);
  if (mask) ctx.drawImage(mask, 0, 0);
  ctx.font = "bold 12px ui-sans-serif, system-ui, sans-serif";
  for (let i = 0; i < boxes.length; i++) {
    const b = boxes[i];
    const team = preset.teams.find((t) => t.slot === b.slot);
    const color = team?.hex ?? "#ff00ff";
    const sel = i === selectedIdx;
    // Тёмная подложка под белой рамкой — чтобы белое было видно на любом фоне.
    ctx.lineWidth = sel ? 4 : 2.5;
    ctx.strokeStyle = "rgba(0,0,0,0.55)";
    ctx.strokeRect(b.x + 0.5, b.y + 0.5, b.w, b.h);
    ctx.lineWidth = sel ? 2 : 1;
    ctx.strokeStyle = sel ? "#ffe066" : "#ffffff";
    ctx.strokeRect(b.x + 0.5, b.y + 0.5, b.w, b.h);
    // Бейдж номера — цвет команды.
    const tag = `${b.slot}${b.source === "manual" ? "*" : ""}`;
    const tw = ctx.measureText(tag).width + 6;
    ctx.fillStyle = color;
    ctx.fillRect(b.x, Math.max(0, b.y - 14), tw, 14);
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1;
    ctx.strokeRect(b.x + 0.5, Math.max(0, b.y - 14) + 0.5, tw, 14);
    ctx.fillStyle = "#000";
    ctx.fillText(tag, b.x + 3, Math.max(10, b.y - 3));
    if (sel) {
      // ручки
      const corners: [number, number][] = [
        [b.x, b.y], [b.x + b.w, b.y], [b.x, b.y + b.h], [b.x + b.w, b.y + b.h],
        [b.x + b.w / 2, b.y], [b.x + b.w / 2, b.y + b.h],
        [b.x, b.y + b.h / 2], [b.x + b.w, b.y + b.h / 2],
      ];
      ctx.fillStyle = "#fff";
      ctx.strokeStyle = "#000";
      ctx.lineWidth = 1;
      for (const [hx, hy] of corners) {
        ctx.fillRect(hx - 3, hy - 3, 6, 6);
        ctx.strokeRect(hx - 3 + 0.5, hy - 3 + 0.5, 6, 6);
      }
    }
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