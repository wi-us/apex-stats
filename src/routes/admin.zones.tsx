import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Eye, EyeOff, Lock, Unlock, Pencil, Copy, RotateCcw, AlignCenter, Files, Plus, Trash2, Check, ZoomIn, ZoomOut, Maximize2, Hand } from "lucide-react";
import vodBg from "@/assets/hsv-samples/worlds-edge.png";
import vodBg2 from "@/assets/zones-samples/vod-stream-2.png";
import cameraBg from "@/assets/zones-samples/camera.png";
import { useAdminStore, setZones as setZonesStore, type Zone, type ZoneMode } from "@/lib/admin-store";
import { ActionBtn, Field, NumField } from "@/components/admin/zones-parts";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogAction,
  AlertDialogCancel,
} from "@/components/ui/alert-dialog";
import { SLOT_COLORS } from "@/lib/team-colors";

export const Route = createFileRoute("/admin/zones")({ component: ZonesAdmin });

type BuiltinPreset = "vod" | "vod2" | "camera";
type CustomPreset = { id: string; label: string; mode: ZoneMode; zones: Zone[] };

const BUILTIN: { id: BuiltinPreset; label: string; mode: ZoneMode }[] = [
  { id: "vod",    label: "VOD Stream", mode: "vod" },
  { id: "vod2",   label: "VOD Stream 2", mode: "vod2" },
  { id: "camera", label: "Player Cam", mode: "camera" },
];

// Semantic colors for default tags. Custom tags get colors from this palette.
const DEFAULT_TAG_COLORS: Record<string, string> = {
  team:     "#22c4f5",
  camera:   "#a78bfa",
  minimap:  "#ff8a00",
  timer:    "#facc15",
  map_name: "#34d399",
  hud:      "#38bdf8",
  ...Object.fromEntries(SLOT_COLORS.map((c, i) => [`team_${i + 1}`, c])),
};
const FALLBACK_PALETTE = ["#f472b6", "#fb7185", "#60a5fa", "#4ade80", "#fbbf24", "#c084fc", "#f87171", "#2dd4bf"];

const isTeamTag = (id: string) => /^team_\d+$/.test(id);

let _idc = 0;
const newId = (p = "z") => `${p}-${Date.now().toString(36)}-${_idc++}`;

type ZoneMeta = { hidden?: boolean; locked?: boolean };

function ZonesAdmin() {
  const store = useAdminStore();

  // Preset state: either a builtin or a custom preset id
  const [activeId, setActiveId] = useState<string>("vod");
  const [customs, setCustoms] = useState<CustomPreset[]>([]);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameVal, setRenameVal] = useState("");

  // Tags (dynamic) with colors
  const [tags, setTags] = useState<{ id: string; color: string }[]>(
    Object.entries(DEFAULT_TAG_COLORS).map(([id, color]) => ({ id, color })),
  );
  const tagColor = (id: string) => tags.find((t) => t.id === id)?.color ?? "#94a3b8";

  const builtin = BUILTIN.find((b) => b.id === activeId);
  const custom = customs.find((c) => c.id === activeId);
  const mode: ZoneMode = builtin?.mode ?? custom?.mode ?? "vod";
  const zones: Zone[] = builtin ? store.zones[builtin.mode] : custom?.zones ?? [];

  const [sel, setSel] = useState<string | null>(zones[0]?.id ?? null);
  const [selSet, setSelSet] = useState<Set<string>>(new Set());
  const [meta, setMeta] = useState<Record<string, ZoneMeta>>({});
  const [snap, setSnap] = useState(true);
  const [gridSize, setGridSize] = useState<5 | 10 | 20>(20);
  const [showGrid, setShowGrid] = useState(true);
  const [showSafe, setShowSafe] = useState(false);
  // Zoom & pan inside the stage
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [spaceDown, setSpaceDown] = useState(false);
  const [hover, setHover] = useState<null | { z: Zone; top: number; left: number }>(null);
  const [tagsOpen, setTagsOpen] = useState(false);
  const [hiddenTags, setHiddenTags] = useState<Set<string>>(new Set());
  const stageRef = useRef<HTMLDivElement | null>(null);
  const panRef = useRef<null | { startX: number; startY: number; orig: { x: number; y: number } }>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const dragRef = useRef<
    | null
    | { id: string; mode: "move" | "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw"; startX: number; startY: number; orig: Zone; origs?: Record<string, Zone> }
  >(null);

  const W = 1920, H = 1080;
  const bg = mode === "vod" ? vodBg : mode === "vod2" ? vodBg2 : cameraBg;
  const selZone = zones.find((z) => z.id === sel);

  // Effective multi-selection (always includes the primary `sel`)
  const selectedIds = useMemo(() => {
    const s = new Set(selSet);
    if (sel) s.add(sel);
    return s;
  }, [selSet, sel]);
  const multi = selectedIds.size > 1;

  const selectZone = (id: string, ctrl: boolean) => {
    if (ctrl) {
      setSelSet((prev) => {
        const n = new Set(prev);
        if (sel && sel !== id) n.add(sel);
        if (n.has(id)) n.delete(id); else n.add(id);
        return n;
      });
      setSel(id);
    } else {
      setSelSet(new Set());
      setSel(id);
    }
  };

  // ── Bulk actions on selectedIds ───────────────────────────────────
  const bulkDelete = () => {
    setZones((zs) => zs.filter((z) => !selectedIds.has(z.id)));
    setSelSet(new Set());
    setSel(null);
  };
  const bulkDuplicate = () => {
    const map = new Map<string, string>();
    const dups: Zone[] = [];
    zones.forEach((z) => {
      if (!selectedIds.has(z.id)) return;
      const nid = newId();
      map.set(z.id, nid);
      dups.push({ ...z, id: nid, name: z.name + " copy", x: Math.min(W - z.w, z.x + 30), y: Math.min(H - z.h, z.y + 30) });
    });
    setZones((zs) => [...zs, ...dups]);
    setSelSet(new Set(Array.from(map.values()).slice(0, -1)));
    setSel(Array.from(map.values()).slice(-1)[0] ?? sel);
  };
  const bulkCopyJson = () => {
    const subset = zones.filter((z) => selectedIds.has(z.id));
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(JSON.stringify(subset, null, 2)).catch(() => {});
    }
  };
  const bulkSetTag = (tag: string) => {
    setZones((zs) => zs.map((z) => (selectedIds.has(z.id) ? { ...z, tag: tag as Zone["tag"] } : z)));
  };
  const bulkDownload = () => {
    const subset = zones.filter((z) => selectedIds.has(z.id));
    const payload = { base: [W, H], mode, zones: subset };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `zones.${mode}.selection.json`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const setZones = (next: Zone[] | ((zs: Zone[]) => Zone[])) => {
    const computed = typeof next === "function" ? (next as (zs: Zone[]) => Zone[])(zones) : next;
    if (builtin) setZonesStore(builtin.mode, computed);
    else if (custom) setCustoms((cs) => cs.map((c) => (c.id === custom.id ? { ...c, zones: computed } : c)));
  };

  // ── Import dialog state ──────────────────────────────────────────────
  type PendingImport = {
    zones: Zone[];
    mode: ZoneMode | null;       // mode из payload, если был
    presetLabel: string | null;  // label из payload, если был
    missingTags: string[];       // теги из файла, которых ещё нет
  };
  const [pendingImport, setPendingImport] = useState<PendingImport | null>(null);
  const [importAddTags, setImportAddTags] = useState(true);
  // Свёрнутость группы тегов команд
  const [teamsCollapsed, setTeamsCollapsed] = useState(true);

  const choosePreset = (id: string) => {
    setActiveId(id);
    const b = BUILTIN.find((x) => x.id === id);
    const c = customs.find((x) => x.id === id);
    const list = b ? store.zones[b.mode] : c?.zones ?? [];
    setSel(list[0]?.id ?? null);
  };

  const addCustomPreset = () => {
    const id = newId("p");
    const next: CustomPreset = { id, label: `Custom ${customs.length + 1}`, mode: "vod", zones: [...store.zones.vod] };
    setCustoms((cs) => [...cs, next]);
    setActiveId(id);
    setSel(next.zones[0]?.id ?? null);
    setRenamingId(id);
    setRenameVal(next.label);
  };
  const removeCustomPreset = (id: string) => {
    setCustoms((cs) => cs.filter((c) => c.id !== id));
    if (activeId === id) choosePreset("vod");
  };
  const commitRename = () => {
    if (!renamingId) return;
    setCustoms((cs) => cs.map((c) => (c.id === renamingId ? { ...c, label: renameVal.trim() || c.label } : c)));
    setRenamingId(null);
  };

  const getMeta = (id: string): ZoneMeta => meta[id] ?? {};
  const patchMeta = (id: string, p: ZoneMeta) => setMeta((m) => ({ ...m, [id]: { ...(m[id] ?? {}), ...p } }));

  const snapVal = (v: number) => (snap ? Math.round(v / gridSize) * gridSize : Math.round(v));

  const update = (id: string, patch: Partial<Zone>) =>
    setZones((zs) => zs.map((z) => (z.id === id ? { ...z, ...patch } : z)));

  const addZone = () => {
    const firstTag = tags[0]?.id ?? "team";
    const z: Zone = { id: newId(), name: "New zone", tag: firstTag as Zone["tag"], x: 760, y: 460, w: 400, h: 160 };
    setZones((zs) => [...zs, z]);
    setSel(z.id);
  };
  const removeZone = (id: string) => {
    setZones((zs) => zs.filter((z) => z.id !== id));
    if (sel === id) setSel(null);
  };
  const duplicateZone = (z: Zone) => {
    const nz: Zone = { ...z, id: newId(), name: z.name + " copy", x: Math.min(W - z.w, z.x + 30), y: Math.min(H - z.h, z.y + 30) };
    setZones((zs) => [...zs, nz]);
    setSel(nz.id);
  };
  const centerZone = (z: Zone) => update(z.id, { x: Math.round((W - z.w) / 2), y: Math.round((H - z.h) / 2) });
  const resetZone = (z: Zone) => update(z.id, { x: 100, y: 100, w: 400, h: 200 });
  const copyCoords = (z: Zone) => {
    const txt = `x:${z.x} y:${z.y} w:${z.w} h:${z.h}`;
    if (typeof navigator !== "undefined" && navigator.clipboard) navigator.clipboard.writeText(txt).catch(() => {});
  };

  // Tag editing
  const addTag = () => {
    const usedColors = new Set(tags.map((t) => t.color));
    const color = FALLBACK_PALETTE.find((c) => !usedColors.has(c)) ?? FALLBACK_PALETTE[0];
    setTags((ts) => [...ts, { id: `tag_${ts.length + 1}`, color }]);
  };
  const renameTag = (oldId: string, nextId: string) => {
    const clean = nextId.trim().replace(/\s+/g, "_").toLowerCase();
    if (!clean || clean === oldId) return;
    if (tags.some((t) => t.id === clean)) return;
    setTags((ts) => ts.map((t) => (t.id === oldId ? { ...t, id: clean } : t)));
    // Update zones using old tag (only mutate built-in ones via store; custom via state)
    (["vod", "vod2", "camera"] as ZoneMode[]).forEach((m) => {
      const next = store.zones[m].map((z) => (z.tag === oldId ? { ...z, tag: clean as Zone["tag"] } : z));
      setZonesStore(m, next);
    });
    setCustoms((cs) => cs.map((c) => ({ ...c, zones: c.zones.map((z) => (z.tag === oldId ? { ...z, tag: clean as Zone["tag"] } : z)) })));
  };
  const recolorTag = (id: string, color: string) => setTags((ts) => ts.map((t) => (t.id === id ? { ...t, color } : t)));
  const deleteTag = (id: string) => {
    if (tags.length <= 1) return;
    const fallback = tags.find((t) => t.id !== id)!.id;
    setTags((ts) => ts.filter((t) => t.id !== id));
    (["vod", "vod2", "camera"] as ZoneMode[]).forEach((m) => {
      const next = store.zones[m].map((z) => (z.tag === id ? { ...z, tag: fallback as Zone["tag"] } : z));
      setZonesStore(m, next);
    });
    setCustoms((cs) => cs.map((c) => ({ ...c, zones: c.zones.map((z) => (z.tag === id ? { ...z, tag: fallback as Zone["tag"] } : z)) })));
  };

  // Apply pending JSON import with the chosen target.
  // target: 'current' = active preset, 'mode' = matching builtin from payload,
  //         'new' = create a new custom preset, 'cancel' = no-op.
  const applyImport = (target: "current" | "mode" | "new") => {
    if (!pendingImport) return;
    const { zones: incoming, mode: payloadMode, presetLabel, missingTags } = pendingImport;

    // Optionally add missing tags so zones don't lose their tag identity.
    if (importAddTags && missingTags.length) {
      setTags((ts) => {
        const used = new Set([...ts.map((t) => t.color)]);
        const palette = [...Object.values(DEFAULT_TAG_COLORS), ...FALLBACK_PALETTE];
        const extra = missingTags.map((id) => {
          const preferred = DEFAULT_TAG_COLORS[id];
          const color = preferred ?? palette.find((c) => !used.has(c)) ?? FALLBACK_PALETTE[0];
          used.add(color);
          return { id, color };
        });
        return [...ts, ...extra];
      });
    } else {
      // Remap unknown tags to first available tag so import doesn't silently drop them.
      const fallback = tags[0]?.id ?? "team";
      incoming.forEach((z) => {
        if (missingTags.includes(z.tag)) z.tag = fallback as Zone["tag"];
      });
    }

    if (target === "new") {
      const id = newId("p");
      const next: CustomPreset = {
        id,
        label: presetLabel || `Imported ${customs.length + 1}`,
        mode: payloadMode ?? "vod",
        zones: incoming,
      };
      setCustoms((cs) => [...cs, next]);
      setActiveId(id);
      setSel(incoming[0]?.id ?? null);
    } else if (target === "mode" && payloadMode) {
      // Apply to matching builtin without switching active tab.
      setZonesStore(payloadMode, incoming);
    } else {
      // 'current'
      setZones(incoming);
      setSel(incoming[0]?.id ?? null);
    }

    setPendingImport(null);
  };

  const toSvg = (clientX: number, clientY: number) => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const rect = svg.getBoundingClientRect();
    return { x: ((clientX - rect.left) / rect.width) * W, y: ((clientY - rect.top) / rect.height) * H };
  };

  const onPointerDown = (e: React.PointerEvent, zone: Zone, m: "move" | "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw") => {
    if (getMeta(zone.id).locked) return;
    e.stopPropagation();
    // Ctrl/Cmd+click → toggle multi-selection, no drag
    if ((e.ctrlKey || e.metaKey) && m === "move") {
      selectZone(zone.id, true);
      return;
    }
    (e.target as Element).setPointerCapture?.(e.pointerId);
    // If clicking a zone outside the current selection, reset to single-select.
    if (!selectedIds.has(zone.id)) {
      setSelSet(new Set());
      setSel(zone.id);
    } else {
      setSel(zone.id);
    }
    const p = toSvg(e.clientX, e.clientY);
    // Snapshot all selected zones for group-move
    const origs: Record<string, Zone> = {};
    if (m === "move") {
      const ids = new Set(selSet);
      ids.add(zone.id);
      zones.forEach((z) => { if (ids.has(z.id) && !getMeta(z.id).locked) origs[z.id] = { ...z }; });
    }
    dragRef.current = { id: zone.id, mode: m, startX: p.x, startY: p.y, orig: { ...zone }, origs: m === "move" ? origs : undefined };
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    const p = toSvg(e.clientX, e.clientY);
    const dx = p.x - d.startX;
    const dy = p.y - d.startY;
    // Group move when multiple zones are selected
    if (d.mode === "move" && d.origs && Object.keys(d.origs).length > 1) {
      setZones((zs) => zs.map((z) => {
        const o = d.origs![z.id];
        if (!o) return z;
        const nx = Math.max(0, Math.min(W - o.w, o.x + dx));
        const ny = Math.max(0, Math.min(H - o.h, o.y + dy));
        return { ...z, x: snapVal(nx), y: snapVal(ny) };
      }));
      return;
    }
    let { x, y, w, h } = d.orig;
    const min = 20;
    if (d.mode === "move") {
      x = Math.max(0, Math.min(W - w, d.orig.x + dx));
      y = Math.max(0, Math.min(H - h, d.orig.y + dy));
    } else {
      if (d.mode.includes("e")) w = Math.max(min, d.orig.w + dx);
      if (d.mode.includes("s")) h = Math.max(min, d.orig.h + dy);
      if (d.mode.includes("w")) { const nw = Math.max(min, d.orig.w - dx); x = d.orig.x + (d.orig.w - nw); w = nw; }
      if (d.mode.includes("n")) { const nh = Math.max(min, d.orig.h - dy); y = d.orig.y + (d.orig.h - nh); h = nh; }
    }
    update(d.id, { x: snapVal(x), y: snapVal(y), w: snapVal(w), h: snapVal(h) });
  };
  const onPointerUp = () => { dragRef.current = null; };

  // ── Zoom & pan handlers ─────────────────────────────────────────────
  const clampZoom = (z: number) => Math.max(0.5, Math.min(8, z));
  const resetView = () => { setZoom(1); setPan({ x: 0, y: 0 }); };
  const zoomAt = (factor: number, cx?: number, cy?: number) => {
    const stage = stageRef.current;
    if (!stage) { setZoom((z) => clampZoom(z * factor)); return; }
    const rect = stage.getBoundingClientRect();
    // Cursor relative to stage CENTER (element is centered, transformOrigin "center")
    const dx = (cx ?? rect.width / 2) - rect.width / 2;
    const dy = (cy ?? rect.height / 2) - rect.height / 2;
    setZoom((z) => {
      const nz = clampZoom(z * factor);
      const k = nz / z;
      setPan((p) => ({ x: dx - (dx - p.x) * k, y: dy - (dy - p.y) * k }));
      return nz;
    });
  };
  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const rect = stageRef.current!.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - rect.left, e.clientY - rect.top);
  };
  const onStagePointerDown = (e: React.PointerEvent) => {
    if (e.button !== 1 && !spaceDown) return; // middle-click or space-drag
    e.preventDefault();
    (e.target as Element).setPointerCapture?.(e.pointerId);
    panRef.current = { startX: e.clientX, startY: e.clientY, orig: { ...pan } };
  };
  const onStagePointerMove = (e: React.PointerEvent) => {
    const pr = panRef.current;
    if (!pr) return;
    setPan({ x: pr.orig.x + (e.clientX - pr.startX), y: pr.orig.y + (e.clientY - pr.startY) });
  };
  const onStagePointerUp = () => { panRef.current = null; };

  useEffect(() => {
    const down = (e: KeyboardEvent) => { if (e.code === "Space") setSpaceDown(true); };
    const up = (e: KeyboardEvent) => { if (e.code === "Space") setSpaceDown(false); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, []);

  const gridLines = useMemo(() => {
    if (!showGrid) return null;
    const step = gridSize === 5 ? 40 : gridSize === 10 ? 80 : 160;
    const lines: React.ReactElement[] = [];
    for (let x = step; x < W; x += step) lines.push(<line key={`vx${x}`} x1={x} y1={0} x2={x} y2={H} stroke="#ffffff" strokeOpacity={0.08} strokeWidth={1} />);
    for (let y = step; y < H; y += step) lines.push(<line key={`hy${y}`} x1={0} y1={y} x2={W} y2={y} stroke="#ffffff" strokeOpacity={0.08} strokeWidth={1} />);
    return <g pointerEvents="none">{lines}</g>;
  }, [showGrid, gridSize]);

  // Preview crop: max box keeping zone's aspect ratio (no fixed-frame distortion).
  const cropBox = (zw: number, zh: number) => {
    const MAX_W = 100, MAX_H = 64;
    const r = zw / zh;
    let w = MAX_W, h = MAX_W / r;
    if (h > MAX_H) { h = MAX_H; w = MAX_H * r; }
    return { w: Math.round(w), h: Math.round(h) };
  };
  const cropBoxBig = (zw: number, zh: number) => {
    const MAX_W = 320, MAX_H = 180;
    const r = zw / zh;
    let w = MAX_W, h = MAX_W / r;
    if (h > MAX_H) { h = MAX_H; w = MAX_H * r; }
    return { w: Math.round(w), h: Math.round(h) };
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface px-6 gap-4">
        <div className="flex items-center gap-4 min-w-0">
          <div className="shrink-0">
            <h1 className="text-xs font-bold uppercase tracking-wider">Zones</h1>
            <div className="label-eyebrow text-xs">1920 × 1080</div>
          </div>
          <div className="flex items-center gap-1 rounded-sm border border-border bg-surface-2 p-0.5 overflow-x-auto">
            {BUILTIN.map((p) => (
              <button key={p.id} onClick={() => choosePreset(p.id)}
                className={`shrink-0 rounded-sm px-3 py-1 text-xs font-semibold uppercase tracking-wider transition-colors ${
                  activeId === p.id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                }`}>
                {p.label}
              </button>
            ))}
            <div className="mx-1 h-5 w-px bg-border" />
            {customs.map((c) => (
              <div key={c.id} className={`flex shrink-0 items-center rounded-sm ${activeId === c.id ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}>
                {renamingId === c.id ? (
                  <>
                    <input
                      autoFocus value={renameVal} onChange={(e) => setRenameVal(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") commitRename(); if (e.key === "Escape") setRenamingId(null); }}
                      onBlur={commitRename}
                      className="w-28 rounded-sm bg-background px-2 py-1 text-xs text-foreground outline-none"
                    />
                    <button onClick={commitRename} className="px-1.5 py-1"><Check className="h-3 w-3" /></button>
                  </>
                ) : (
                  <>
                    <button onClick={() => choosePreset(c.id)}
                      onDoubleClick={() => { setRenamingId(c.id); setRenameVal(c.label); }}
                      className="px-3 py-1 text-xs font-semibold uppercase tracking-wider hover:text-foreground">
                      {c.label}
                    </button>
                    <button onClick={() => { setRenamingId(c.id); setRenameVal(c.label); }} title="Rename"
                      className="grid h-6 w-5 place-items-center opacity-60 hover:opacity-100"><Pencil className="h-3 w-3" /></button>
                    <button onClick={() => removeCustomPreset(c.id)} title="Delete preset"
                      className="grid h-6 w-5 place-items-center opacity-60 hover:opacity-100"><Trash2 className="h-3 w-3" /></button>
                  </>
                )}
              </div>
            ))}
            <button onClick={addCustomPreset} title="Add custom preset"
              className="grid h-6 w-7 shrink-0 place-items-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground">
              <Plus className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </header>

      {/* Toolbar */}
      <div className="flex h-10 shrink-0 items-center gap-4 border-b border-border bg-surface-2 px-6 text-xs">
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" checked={snap} onChange={(e) => setSnap(e.target.checked)} />
          <span className="font-semibold uppercase tracking-wider text-muted-foreground">Snap to grid</span>
        </label>
        <div className="flex rounded-sm border border-border bg-surface p-0.5">
          {[5, 10, 20].map((g) => (
            <button key={g} onClick={() => setGridSize(g as 5 | 10 | 20)}
              className={`px-2 py-0.5 text-xs font-semibold ${gridSize === g ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}>
              {g}px
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" checked={showGrid} onChange={(e) => setShowGrid(e.target.checked)} />
          <span className="font-semibold uppercase tracking-wider text-muted-foreground">Show grid</span>
        </label>
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" checked={showSafe} onChange={(e) => setShowSafe(e.target.checked)} />
          <span className="font-semibold uppercase tracking-wider text-muted-foreground">Safe frame</span>
        </label>

        <div className="relative ml-auto">
          <button onClick={() => setTagsOpen((v) => !v)}
            className={`rounded-sm border border-border px-2 py-1 text-xs font-semibold uppercase tracking-wider ${tagsOpen ? "bg-primary text-primary-foreground" : "bg-surface text-muted-foreground hover:text-foreground"}`}>
            Tags ({tags.length})
          </button>
          {tagsOpen && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setTagsOpen(false)} />
              <section className="absolute right-0 top-full z-50 mt-1 w-[320px] rounded-sm border border-border bg-surface-2 p-2.5 shadow-2xl">
                <div className="mb-2 flex items-center justify-between">
                  <div className="label-eyebrow">Tags ({tags.length})</div>
                  <button onClick={addTag}
                    className="rounded-sm border border-border bg-surface px-2 py-0.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:bg-muted hover:text-foreground">
                    + Add
                  </button>
                </div>
                {(() => {
                  const nonTeam = tags.filter((t) => !isTeamTag(t.id));
                  const teamTags = tags
                    .filter((t) => isTeamTag(t.id))
                    .sort((a, b) => Number(a.id.slice(5)) - Number(b.id.slice(5)));
                  const renderRow = (t: { id: string; color: string }) => (
                    <div key={t.id} className="mb-1 flex items-center gap-2 rounded-sm border border-border bg-surface px-2 py-1.5">
                      <label className="relative block h-2.5 w-2.5 shrink-0 cursor-pointer overflow-hidden rounded-sm"
                        style={{ backgroundColor: t.color }} title={t.color}>
                        <input type="color" value={t.color} onChange={(e) => recolorTag(t.id, e.target.value)}
                          className="absolute inset-0 h-full w-full cursor-pointer opacity-0" />
                      </label>
                      <input defaultValue={t.id} onBlur={(e) => renameTag(t.id, e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                        className="text-mono min-w-0 flex-1 rounded-sm bg-background px-2 py-1 text-xs outline-none focus:ring-1 focus:ring-primary/40" />
                      <button onClick={() => deleteTag(t.id)} disabled={tags.length <= 1} title="Delete tag"
                        className="grid h-6 w-6 place-items-center rounded-sm text-muted-foreground hover:bg-destructive/20 hover:text-destructive disabled:opacity-30">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  );
                  return (
                    <>
                      {nonTeam.map(renderRow)}
                      {teamTags.length > 0 && (
                        <>
                          <button
                            onClick={() => setTeamsCollapsed((v) => !v)}
                            className="mt-1 mb-1 flex w-full items-center justify-between rounded-sm border border-border bg-surface px-2 py-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground">
                            <span>Teams ({teamTags.length})</span>
                            <span>{teamsCollapsed ? "▸" : "▾"}</span>
                          </button>
                          {!teamsCollapsed && teamTags.map(renderRow)}
                        </>
                      )}
                    </>
                  );
                })()}
              </section>
            </>
          )}
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Stage — fills available space, keeps aspect ratio without clipping */}
        <div
          ref={stageRef}
          className="relative flex min-h-0 min-w-0 flex-1 items-center justify-center overflow-hidden bg-background p-4"
          style={{ containerType: "size", cursor: panRef.current ? "grabbing" : spaceDown ? "grab" : "default" } as React.CSSProperties}
          onWheel={onWheel}
          onPointerDown={onStagePointerDown}
          onPointerMove={onStagePointerMove}
          onPointerUp={onStagePointerUp}
          onPointerLeave={onStagePointerUp}
        >
          <div
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              transformOrigin: "center center",
              width: `min(100cqw, calc(100cqh * ${W} / ${H}))`,
              height: `min(100cqh, calc(100cqw * ${H} / ${W}))`,
            }}
          >
          <div
            className="hud-panel-strong relative overflow-hidden"
            style={{ width: "100%", height: "100%", aspectRatio: `${W}/${H}` }}
          >
            <img src={bg} alt="" className="absolute inset-0 h-full w-full object-cover opacity-90" draggable={false} />
            <div className="absolute inset-0 bg-background/10" />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 h-full w-full touch-none select-none"
              onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerLeave={onPointerUp}>
              {gridLines}
              {showSafe && (
                <rect x={W * 0.05} y={H * 0.05} width={W * 0.9} height={H * 0.9}
                  fill="none" stroke="#ff5b12" strokeOpacity={0.7} strokeDasharray="12 8" strokeWidth={2} pointerEvents="none" />
              )}
              {zones.map((z) => {
                if (getMeta(z.id).hidden) return null;
                const active = z.id === sel;
                const inSel = selectedIds.has(z.id);
                const locked = getMeta(z.id).locked;
                const c = tagColor(z.tag);
                 const handle = 12 / zoom;
                const handles: { m: "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw"; cx: number; cy: number; cur: string }[] = [
                  { m: "nw", cx: z.x,         cy: z.y,         cur: "nwse-resize" },
                  { m: "ne", cx: z.x + z.w,   cy: z.y,         cur: "nesw-resize" },
                  { m: "sw", cx: z.x,         cy: z.y + z.h,   cur: "nesw-resize" },
                  { m: "se", cx: z.x + z.w,   cy: z.y + z.h,   cur: "nwse-resize" },
                  { m: "n",  cx: z.x + z.w/2, cy: z.y,         cur: "ns-resize" },
                  { m: "s",  cx: z.x + z.w/2, cy: z.y + z.h,   cur: "ns-resize" },
                  { m: "w",  cx: z.x,         cy: z.y + z.h/2, cur: "ew-resize" },
                  { m: "e",  cx: z.x + z.w,   cy: z.y + z.h/2, cur: "ew-resize" },
                ];
                const fillOpacity = active ? "33" : inSel ? "22" : "0d";
                const strokeOpacity = active ? 1 : inSel ? 0.9 : 0.45;
                return (
                  <g key={z.id} opacity={active ? 1 : inSel ? 0.95 : 0.75}>
                     <rect x={z.x} y={z.y} width={z.w} height={z.h}
                      fill={`${c}${fillOpacity}`} stroke={c} strokeOpacity={strokeOpacity}
                      strokeWidth={(active ? 3 : inSel ? 2.25 : 1.5) / zoom} strokeDasharray={locked ? `${8/zoom} ${6/zoom}` : undefined}
                      style={{ cursor: locked ? "not-allowed" : "move" }}
                      onPointerDown={(e) => onPointerDown(e, z, "move")} />
                    {active && (
                      <>
                        <rect x={z.x} y={z.y - 28/zoom} width={Math.max(160, z.name.length * 11 + 90) / zoom} height={24/zoom} fill={c}
                          style={{ cursor: locked ? "not-allowed" : "move" }}
                          onPointerDown={(e) => onPointerDown(e, z, "move")} />
                        <text x={z.x + 8/zoom} y={z.y - 10/zoom} fontSize={14/zoom} fontWeight={800} fill="#0a0a0a" fontFamily="Manrope, sans-serif" pointerEvents="none">
                          {z.name} · {z.tag}{locked ? " · locked" : ""}
                        </text>
                        {!locked && handles.map((h) => (
                          <rect key={h.m} x={h.cx - handle/2} y={h.cy - handle/2} width={handle} height={handle}
                            fill="#0a0a0a" stroke={c} strokeWidth={1.5/zoom} style={{ cursor: h.cur }}
                            onPointerDown={(e) => onPointerDown(e, z, h.m)} />
                        ))}
                      </>
                    )}
                  </g>
                );
              })}
            </svg>
          </div>
          </div>

          {/* Zoom controls */}
          <div className="absolute bottom-3 left-3 z-10 flex items-center gap-1 rounded-sm border border-border bg-surface/95 p-1 backdrop-blur">
            <button onClick={() => zoomAt(1 / 1.25)} title="Zoom out" className="grid h-7 w-7 place-items-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground">
              <ZoomOut className="h-3.5 w-3.5" />
            </button>
            <button onClick={resetView} className="text-mono px-2 py-1 text-xs font-semibold text-muted-foreground hover:text-foreground" title="Reset view (100%)">
              {Math.round(zoom * 100)}%
            </button>
            <button onClick={() => zoomAt(1.25)} title="Zoom in" className="grid h-7 w-7 place-items-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground">
              <ZoomIn className="h-3.5 w-3.5" />
            </button>
            <div className="mx-1 h-5 w-px bg-border" />
            <button onClick={resetView} title="Fit" className="grid h-7 w-7 place-items-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground">
              <Maximize2 className="h-3.5 w-3.5" />
            </button>
            <div className={`flex items-center gap-1 rounded-sm px-2 py-1 text-xs uppercase tracking-wider ${spaceDown ? "bg-primary/20 text-primary" : "text-muted-foreground"}`} title="Hold Space or middle-click to pan">
              <Hand className="h-3 w-3" /> Pan
            </div>
          </div>

        </div>

        <aside className="w-[340px] shrink-0 overflow-y-auto border-l border-border bg-surface p-3">
          {multi && (
            <div className="mb-3 rounded-sm border border-primary/50 bg-primary/10 p-2.5">
              <div className="mb-2 flex items-center justify-between">
                <div className="label-eyebrow">Multi-select · {selectedIds.size}</div>
                <button onClick={() => { setSelSet(new Set()); }}
                  className="rounded-sm border border-border bg-surface px-2 py-0.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground">
                  Clear
                </button>
              </div>
              <label className="mb-2 block">
                <span className="label-eyebrow mb-1 block text-xs">Set tag for all</span>
                <select
                  value=""
                  onChange={(e) => { if (e.target.value) { bulkSetTag(e.target.value); e.target.value = ""; } }}
                  className="w-full rounded-sm border border-border bg-background px-2 py-1.5 text-xs outline-none focus:border-primary/60">
                  <option value="">— change to —</option>
                  {tags.map((t) => <option key={t.id} value={t.id}>{t.id}</option>)}
                </select>
              </label>
              <div className="grid grid-cols-3 gap-1">
                <ActionBtn icon={<Copy className="h-3 w-3" />} label="Copy" onClick={bulkCopyJson} />
                <ActionBtn icon={<Files className="h-3 w-3" />} label="Dup" onClick={bulkDuplicate} />
                <ActionBtn icon={<Trash2 className="h-3 w-3" />} label="Del" onClick={bulkDelete} />
              </div>
              <button onClick={bulkCopyJson}
                className="mt-3 w-full rounded-sm bg-primary px-2 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110">
                Save (copy JSON)
              </button>
              <button onClick={bulkDownload}
                className="mt-2 w-full rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider hover:bg-muted">
                Download selection.json
              </button>
              <div className="mt-2 text-xs uppercase tracking-wider text-muted-foreground">
                Ctrl+ЛКМ — добавить / убрать
              </div>
            </div>
          )}
          {!multi && selZone && (
            <div className="mb-3 rounded-sm border border-border bg-surface-2 p-2.5">
              <div className="label-eyebrow mb-2">Edit</div>
              <Field label="Name" value={selZone.name} onChange={(v) => update(selZone.id, { name: v })} />
              <label className="mb-2 block">
                <span className="label-eyebrow mb-1 block text-xs">Tag</span>
                <select value={selZone.tag} onChange={(e) => update(selZone.id, { tag: e.target.value as Zone["tag"] })}
                  className="w-full rounded-sm border border-border bg-background px-2 py-1.5 text-xs outline-none focus:border-primary/60">
                  {tags.map((t) => <option key={t.id} value={t.id}>{t.id}</option>)}
                </select>
              </label>
              <div className="grid grid-cols-2 gap-2">
                <NumField label="X" value={selZone.x} onChange={(v) => update(selZone.id, { x: snapVal(v) })} />
                <NumField label="Y" value={selZone.y} onChange={(v) => update(selZone.id, { y: snapVal(v) })} />
                <NumField label="W" value={selZone.w} onChange={(v) => update(selZone.id, { w: snapVal(v) })} />
                <NumField label="H" value={selZone.h} onChange={(v) => update(selZone.id, { h: snapVal(v) })} />
              </div>
              <div className="mt-2 grid grid-cols-4 gap-1">
                <ActionBtn icon={<Copy className="h-3 w-3" />} label="Copy" onClick={() => copyCoords(selZone)} />
                <ActionBtn icon={<RotateCcw className="h-3 w-3" />} label="Reset" onClick={() => resetZone(selZone)} />
                <ActionBtn icon={<AlignCenter className="h-3 w-3" />} label="Center" onClick={() => centerZone(selZone)} />
                <ActionBtn icon={<Files className="h-3 w-3" />} label="Dup" onClick={() => duplicateZone(selZone)} />
              </div>
              <button
                onClick={() => {
                  if (typeof navigator !== "undefined" && navigator.clipboard) {
                    navigator.clipboard.writeText(JSON.stringify(zones, null, 2)).catch(() => {});
                  }
                }}
                className="mt-3 w-full rounded-sm bg-primary px-2 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110">
                Save
              </button>
              <button
                onClick={() => {
                  const payload = { base: [W, H], mode, zones };
                  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url; a.download = `zones.${mode}.json`; a.click();
                  setTimeout(() => URL.revokeObjectURL(url), 1000);
                }}
                className="mt-2 w-full rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider hover:bg-muted">
                Download zones.json
              </button>
              <button
                onClick={() => importInputRef.current?.click()}
                className="mt-2 w-full rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider hover:bg-muted">
                Import zones.json
              </button>
              <input
                ref={importInputRef}
                type="file"
                accept="application/json,.json"
                hidden
                onChange={async (e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (!f) return;
                  try {
                    const data = JSON.parse(await f.text());
                    const arr: Zone[] = Array.isArray(data) ? data : data.zones ?? [];
                    const cleaned: Zone[] = arr
                      .filter((z) => z && typeof z.x === "number" && typeof z.y === "number" && typeof z.w === "number" && typeof z.h === "number")
                      .map((z, i) => ({
                        id: z.id || newId(),
                        name: z.name || `Zone ${i + 1}`,
                        tag: (z.tag || tags[0]?.id || "team") as Zone["tag"],
                        x: z.x, y: z.y, w: z.w, h: z.h,
                      }));
                    const payloadMode = (data?.mode === "vod" || data?.mode === "vod2" || data?.mode === "camera")
                      ? (data.mode as ZoneMode)
                      : null;
                    const payloadLabel = typeof data?.label === "string" ? data.label : null;
                    const known = new Set(tags.map((t) => t.id));
                    const missing = Array.from(new Set(cleaned.map((z) => z.tag))).filter((t) => !known.has(t));
                    setImportAddTags(true);
                    setPendingImport({
                      zones: cleaned,
                      mode: payloadMode,
                      presetLabel: payloadLabel,
                      missingTags: missing,
                    });
                  } catch (err) {
                    alert(`Import failed: ${(err as Error).message}`);
                  }
                }}
              />
              <button onClick={() => removeZone(selZone.id)}
                className="mt-2 w-full rounded-sm border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider text-destructive hover:bg-destructive/20">
                Delete zone
              </button>
            </div>
          )}

          <div className="mb-2 flex items-center justify-between">
            <div className="label-eyebrow">Zones ({zones.filter((z) => !hiddenTags.has(z.tag)).length})</div>
            <button onClick={addZone} className="rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs font-semibold hover:bg-muted">+ Add</button>
          </div>
          <div className="mb-2 flex flex-wrap gap-1">
            {tags.map((t) => (
              <button
                key={t.id}
                onClick={() => setHiddenTags((prev) => {
                  const next = new Set(prev);
                  if (next.has(t.id)) next.delete(t.id); else next.add(t.id);
                  return next;
                })}
                className={`flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wider transition-colors ${
                  hiddenTags.has(t.id) ? "border-border/50 bg-surface text-muted-foreground/50 line-through" : "border-border bg-surface-2 text-foreground"
                }`}>
                <span className="inline-block h-2 w-2 rounded-sm" style={{ backgroundColor: t.color, opacity: hiddenTags.has(t.id) ? 0.3 : 1 }} />
                {t.id}
              </button>
            ))}
          </div>
          {zones.filter((z) => !hiddenTags.has(z.tag)).map((z) => {
            const m = getMeta(z.id);
            const c = tagColor(z.tag);
            const cb = cropBox(z.w, z.h);
            const big = cropBoxBig(z.w, z.h);
            const inSel = selectedIds.has(z.id);
            return (
              <div key={z.id}
                className={`mb-1 rounded-sm border px-2 py-1.5 transition-colors ${
                  z.id === sel ? "border-primary/60 bg-primary/10" : inSel ? "border-primary/30 bg-primary/5" : "border-transparent hover:bg-muted"} ${m.hidden ? "opacity-50" : ""}`}>
                <button onClick={(e) => selectZone(z.id, e.ctrlKey || e.metaKey)} className="mb-1 flex w-full items-center gap-1.5 text-left">
                  <input type="checkbox" readOnly checked={inSel}
                    onClick={(e) => { e.stopPropagation(); selectZone(z.id, true); }}
                    className="h-3 w-3 shrink-0 cursor-pointer" />
                  <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: c }} />
                  <span className="flex-1 truncate text-xs font-semibold">{z.name}</span>
                </button>
                <div className="flex items-center gap-2">
                  <button
                    onClick={(e) => selectZone(z.id, e.ctrlKey || e.metaKey)}
                    onMouseEnter={(e) => {
                      const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                      setHover({ z, top: r.top + r.height / 2, left: r.left });
                    }}
                    onMouseLeave={() => setHover((h) => (h?.z.id === z.id ? null : h))}
                    className="relative shrink-0"
                  >
                    <div className="relative overflow-hidden rounded-sm border border-border bg-background"
                      style={{ width: cb.w, height: cb.h }}>
                      <div className="absolute inset-0" style={{
                        backgroundImage: `url(${bg})`,
                        backgroundSize: `${(cb.w * W) / z.w}px ${(cb.h * H) / z.h}px`,
                        backgroundPosition: `-${(z.x * cb.w) / z.w}px -${(z.y * cb.h) / z.h}px`,
                        backgroundRepeat: "no-repeat",
                      }} />
                      <div className="absolute inset-0" style={{ boxShadow: `inset 0 0 0 1px ${c}` }} />
                    </div>
                  </button>
                  <span className="text-mono flex-1 text-xs uppercase text-muted-foreground">{z.w}×{z.h}</span>
                  <div className="grid shrink-0 grid-cols-2 gap-0.5">
                    <button onClick={() => patchMeta(z.id, { hidden: !m.hidden })} title={m.hidden ? "Show" : "Hide"}
                      className="grid h-6 w-6 place-items-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground">
                      {m.hidden ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                    </button>
                    <button onClick={() => patchMeta(z.id, { locked: !m.locked })} title={m.locked ? "Unlock" : "Lock"}
                      className={`grid h-6 w-6 place-items-center rounded-sm hover:bg-muted ${m.locked ? "text-primary" : "text-muted-foreground hover:text-foreground"}`}>
                      {m.locked ? <Lock className="h-3.5 w-3.5" /> : <Unlock className="h-3.5 w-3.5" />}
                    </button>
                    <button onClick={() => setSel(z.id)} title="Edit"
                      className="grid h-6 w-6 place-items-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground"><Pencil className="h-3.5 w-3.5" /></button>
                    <button onClick={() => removeZone(z.id)} title="Delete"
                      className="grid h-6 w-6 place-items-center rounded-sm text-muted-foreground hover:bg-destructive/20 hover:text-destructive"><Trash2 className="h-3.5 w-3.5" /></button>
                  </div>
                </div>
              </div>
            );
          })}

        </aside>
      </div>

      {hover && typeof document !== "undefined" && createPortal(
        (() => {
          const z = hover.z;
          const big = cropBoxBig(z.w, z.h);
          const c = tagColor(z.tag);
          return (
            <div
              className="pointer-events-none fixed z-[1000] -translate-y-1/2 rounded-sm border border-border bg-surface p-1 shadow-2xl"
              style={{ top: hover.top, left: hover.left - big.w - 12 }}
            >
              <div className="relative overflow-hidden rounded-sm bg-background" style={{ width: big.w, height: big.h }}>
                <div className="absolute inset-0" style={{
                  backgroundImage: `url(${bg})`,
                  backgroundSize: `${(big.w * W) / z.w}px ${(big.h * H) / z.h}px`,
                  backgroundPosition: `-${(z.x * big.w) / z.w}px -${(z.y * big.h) / z.h}px`,
                  backgroundRepeat: "no-repeat",
                }} />
                <div className="absolute inset-0" style={{ boxShadow: `inset 0 0 0 2px ${c}` }} />
              </div>
              <div className="text-mono mt-1 px-1 text-xs uppercase text-muted-foreground">{z.name} · {z.w}×{z.h}</div>
            </div>
          );
        })(),
        document.body,
      )}

      <AlertDialog open={!!pendingImport} onOpenChange={(o) => !o && setPendingImport(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Import zones — where?</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-xs">
                <div>
                  <span className="text-mono font-semibold">{pendingImport?.zones.length ?? 0}</span> zones from file
                  {pendingImport?.mode && (
                    <> · source mode <span className="text-mono font-semibold">{pendingImport.mode}</span></>
                  )}
                  {pendingImport?.presetLabel && (
                    <> · label <span className="text-mono font-semibold">{pendingImport.presetLabel}</span></>
                  )}
                </div>
                {pendingImport?.missingTags.length ? (
                  <label className="flex items-center gap-2">
                    <input type="checkbox" checked={importAddTags} onChange={(e) => setImportAddTags(e.target.checked)} />
                    <span>Add {pendingImport.missingTags.length} missing tag{pendingImport.missingTags.length === 1 ? "" : "s"}: <span className="text-mono">{pendingImport.missingTags.join(", ")}</span></span>
                  </label>
                ) : null}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="flex flex-wrap gap-2">
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => applyImport("current")}>
              Replace current preset
            </AlertDialogAction>
            {pendingImport?.mode && pendingImport.mode !== mode && (
              <AlertDialogAction onClick={() => applyImport("mode")}>
                Apply to {pendingImport.mode} (без переключения)
              </AlertDialogAction>
            )}
            <AlertDialogAction onClick={() => applyImport("new")}>
              Create new custom preset
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

