import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useRef, useState, useEffect } from "react";
import { maps as allMaps } from "@/lib/mock-match";
import {
  useAdminStore,
  addPolygon,
  updatePolygon,
  removePolygon,
  addCustomMap,
  updateCustomMap,
  removeCustomMap,
  type Polygon,
  type PolygonTag,
} from "@/lib/admin-store";

export const Route = createFileRoute("/admin/polygons")({
  component: PolygonsAdmin,
  validateSearch: (s: Record<string, unknown>) => ({
    mapId: typeof s.mapId === "string" ? s.mapId : undefined,
  }),
});

type Mode = "idle" | "draw";

function PolygonsAdmin() {
  const { polygons, customMaps } = useAdminStore();
  const allMapsCombined = useMemo(() => [...allMaps, ...customMaps], [customMaps]);
  const search = Route.useSearch();
  const initialMapId = search.mapId && allMapsCombined.some((m) => m.id === search.mapId)
    ? search.mapId
    : (allMapsCombined[0]?.id ?? "");
  const [mapId, setMapId] = useState(initialMapId);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("idle");
  const [draft, setDraft] = useState<{ x: number; y: number }[]>([]);
  const [drag, setDrag] = useState<{ polyId: string; pointIdx: number } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [copyOpen, setCopyOpen] = useState(false);
  const customUploadRef = useRef<HTMLInputElement | null>(null);
  const [renamingMapId, setRenamingMapId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const map = allMapsCombined.find((m) => m.id === mapId);
  const mapPolys = useMemo(() => polygons.filter((p) => p.mapId === mapId), [polygons, mapId]);

  useEffect(() => {
    setSelectedId(null);
    setMode("idle");
    setDraft([]);
  }, [mapId]);

  const toNorm = (e: React.MouseEvent | MouseEvent) => {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height)),
    };
  };

  const onCanvasClick = (e: React.MouseEvent) => {
    if (mode !== "draw") return;
    const p = toNorm(e);
    if (!p) return;
    setDraft((d) => [...d, p]);
  };

  const finishDraft = (tag: PolygonTag) => {
    if (draft.length < 3) return;
    const id = `pg-${Date.now()}`;
    const name = `${tag === "forbidden" ? "Forbidden" : "Safe"} ${mapPolys.length + 1}`;
    addPolygon({ id, mapId, name, tag, points: draft });
    setDraft([]);
    setMode("idle");
    setSelectedId(id);
  };

  // Drag handling
  useEffect(() => {
    if (!drag) return;
    const onMove = (e: MouseEvent) => {
      const p = toNorm(e);
      if (!p) return;
      const poly = polygons.find((x) => x.id === drag.polyId);
      if (!poly) return;
      const next = poly.points.map((pt, i) => (i === drag.pointIdx ? p : pt));
      updatePolygon(drag.polyId, { points: next });
    };
    const onUp = () => setDrag(null);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [drag, polygons]);

  const fillFor = (tag: PolygonTag, active: boolean) =>
    tag === "forbidden"
      ? active ? "rgba(239,68,68,0.42)" : "rgba(239,68,68,0.22)"
      : active ? "rgba(34,197,94,0.42)" : "rgba(34,197,94,0.22)";
  const strokeFor = (tag: PolygonTag) =>
    tag === "forbidden" ? "#ef4444" : "#22c55e";

  const exportJson = () => {
    const payload = {
      mapId,
      mapName: map?.name,
      exportedAt: new Date().toISOString(),
      polygons: mapPolys.map((p) => ({ name: p.name, tag: p.tag, points: p.points })),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `polygons-${map?.name ?? mapId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const importJson = async (file: File) => {
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const arr: Array<{ name?: string; tag?: PolygonTag; points?: { x: number; y: number }[] }> =
        Array.isArray(data) ? data : data.polygons ?? [];
      let added = 0;
      arr.forEach((p, i) => {
        if (!p.points || p.points.length < 3) return;
        const tag: PolygonTag = p.tag === "safe" ? "safe" : "forbidden";
        addPolygon({
          id: `pg-${Date.now()}-${i}`,
          mapId,
          name: p.name || `${tag === "forbidden" ? "Forbidden" : "Safe"} ${mapPolys.length + i + 1}`,
          tag,
          points: p.points,
        });
        added++;
      });
      alert(`Imported ${added} polygon${added === 1 ? "" : "s"}`);
    } catch (err) {
      alert(`Import failed: ${(err as Error).message}`);
    }
  };

  const copyToMap = (targetMapId: string) => {
    if (targetMapId === mapId) { setCopyOpen(false); return; }
    mapPolys.forEach((p, i) => {
      addPolygon({
        id: `pg-${Date.now()}-${i}`,
        mapId: targetMapId,
        name: p.name,
        tag: p.tag,
        points: p.points,
      });
    });
    setCopyOpen(false);
    alert(`Copied ${mapPolys.length} polygon${mapPolys.length === 1 ? "" : "s"} to ${allMapsCombined.find((m) => m.id === targetMapId)?.name}`);
  };

  const onUploadCustomMap = async (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      const id = `custom-${Date.now()}`;
      const base = file.name.replace(/\.[^.]+$/, "");
      addCustomMap({ id, name: base || "Custom map", image: String(reader.result) });
      setMapId(id);
    };
    reader.readAsDataURL(file);
  };

  const commitRename = () => {
    if (!renamingMapId) return;
    const v = renameValue.trim();
    if (v) updateCustomMap(renamingMapId, { name: v });
    setRenamingMapId(null);
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center gap-6 border-b border-border bg-surface px-6">
        <h1 className="text-sm font-bold uppercase tracking-wider">Polygons</h1>
        <div className="flex flex-wrap items-center gap-1">
          <span className="label-eyebrow mr-2 text-xs">Map</span>
          {allMapsCombined.map((m) => {
            const isCustom = customMaps.some((c) => c.id === m.id);
            const active = m.id === mapId;
            const renaming = renamingMapId === m.id;
            return (
              <div
                key={m.id}
                className={`group flex items-center gap-1 rounded-sm border px-2.5 py-1 text-xs font-semibold uppercase tracking-wider transition-colors ${
                  active
                    ? "border-primary/40 bg-primary/10 text-primary"
                    : "border-border bg-surface-2 text-muted-foreground hover:bg-muted"
                }`}
              >
                {renaming ? (
                  <input
                    autoFocus
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onBlur={commitRename}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commitRename();
                      if (e.key === "Escape") setRenamingMapId(null);
                    }}
                    className="w-32 rounded-sm border border-border bg-background px-1 py-0.5 text-xs normal-case tracking-normal"
                  />
                ) : (
                  <button
                    onClick={() => setMapId(m.id)}
                    onDoubleClick={() => {
                      if (!isCustom) return;
                      setRenamingMapId(m.id);
                      setRenameValue(m.name);
                    }}
                    title={isCustom ? "Double-click to rename" : m.name}
                  >
                    {m.name}
                  </button>
                )}
                {isCustom && !renaming && (
                  <button
                    onClick={() => {
                      if (!confirm(`Delete custom map "${m.name}" and its polygons?`)) return;
                      removeCustomMap(m.id);
                      if (mapId === m.id) setMapId(allMaps[0]?.id ?? "");
                    }}
                    className="ml-0.5 rounded-sm px-1 opacity-0 transition hover:bg-destructive/20 hover:text-destructive group-hover:opacity-100"
                    title="Delete custom map"
                  >
                    ×
                  </button>
                )}
              </div>
            );
          })}
          <button
            onClick={() => customUploadRef.current?.click()}
            className="ml-1 rounded-sm border border-dashed border-border bg-surface-2 px-2.5 py-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:bg-muted"
            title="Upload a custom map image"
          >
            + Custom
          </button>
          <input
            ref={customUploadRef}
            type="file"
            accept="image/*"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onUploadCustomMap(f);
              e.target.value = "";
            }}
          />
        </div>
        <span className="text-mono ml-auto text-xs text-muted-foreground">
          {mapPolys.length} polygon{mapPolys.length === 1 ? "" : "s"}
        </span>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Map canvas */}
        <div className="flex flex-1 items-center justify-center overflow-hidden bg-background p-4">
          <div className="relative h-full w-full max-w-full">
            <div className="relative mx-auto h-full" style={{ aspectRatio: "1 / 1", maxWidth: "100%" }}>
              {map && (
                <img
                  src={map.image}
                  alt={map.name}
                  className="pointer-events-none absolute inset-0 h-full w-full select-none object-contain"
                  draggable={false}
                />
              )}
              <svg
                ref={svgRef}
                viewBox="0 0 1000 1000"
                preserveAspectRatio="none"
                className={`absolute inset-0 h-full w-full ${mode === "draw" ? "cursor-crosshair" : "cursor-default"}`}
                onClick={onCanvasClick}
              >
                {mapPolys.map((p) => {
                  const active = p.id === selectedId;
                  const d = p.points.map((pt, i) => `${i === 0 ? "M" : "L"}${pt.x * 1000},${pt.y * 1000}`).join(" ") + " Z";
                  return (
                    <g key={p.id}>
                      <path
                        d={d}
                        fill={fillFor(p.tag, active)}
                        stroke={strokeFor(p.tag)}
                        strokeWidth={active ? 2.5 : 1.5}
                        onClick={(e) => { e.stopPropagation(); if (mode === "idle") setSelectedId(p.id); }}
                        style={{ cursor: mode === "idle" ? "pointer" : "crosshair" }}
                      />
                      {active && mode === "idle" && p.points.map((pt, i) => (
                        <circle
                          key={i}
                          cx={pt.x * 1000}
                          cy={pt.y * 1000}
                          r={7}
                          fill="#000"
                          stroke={strokeFor(p.tag)}
                          strokeWidth={2}
                          style={{ cursor: "grab" }}
                          onMouseDown={(e) => { e.stopPropagation(); setDrag({ polyId: p.id, pointIdx: i }); }}
                          onDoubleClick={(e) => {
                            e.stopPropagation();
                            if (p.points.length <= 3) return;
                            const next = p.points.filter((_, idx) => idx !== i);
                            updatePolygon(p.id, { points: next });
                          }}
                        />
                      ))}
                      {active && mode === "idle" && p.points.map((pt, i) => {
                        const next = p.points[(i + 1) % p.points.length];
                        const mx = (pt.x + next.x) / 2;
                        const my = (pt.y + next.y) / 2;
                        return (
                          <g key={`mid-${i}`}>
                            <circle
                              cx={mx * 1000}
                              cy={my * 1000}
                              r={6}
                              fill={strokeFor(p.tag)}
                              fillOpacity={0.25}
                              stroke={strokeFor(p.tag)}
                              strokeWidth={1.5}
                              strokeDasharray="2 2"
                              style={{ cursor: "copy" }}
                              onClick={(e) => {
                                e.stopPropagation();
                                const insertAt = i + 1;
                                const newPts = [
                                  ...p.points.slice(0, insertAt),
                                  { x: mx, y: my },
                                  ...p.points.slice(insertAt),
                                ];
                                updatePolygon(p.id, { points: newPts });
                              }}
                            >
                              <title>Click to insert vertex · double-click vertex to remove</title>
                            </circle>
                            <text
                              x={mx * 1000}
                              y={my * 1000 + 3}
                              textAnchor="middle"
                              fontSize={10}
                              fill={strokeFor(p.tag)}
                              style={{ pointerEvents: "none", userSelect: "none" }}
                            >+</text>
                          </g>
                        );
                      })}
                    </g>
                  );
                })}
                {/* Draft */}
                {mode === "draw" && draft.length > 0 && (
                  <g>
                    {draft.length >= 2 && (
                      <path
                        d={draft.map((pt, i) => `${i === 0 ? "M" : "L"}${pt.x * 1000},${pt.y * 1000}`).join(" ") + (draft.length >= 3 ? " Z" : "")}
                        fill="rgba(99,102,241,0.18)"
                        stroke="#818cf8"
                        strokeWidth={1.5}
                        strokeDasharray="6 4"
                      />
                    )}
                    {draft.map((pt, i) => (
                      <circle key={i} cx={pt.x * 1000} cy={pt.y * 1000} r={5} fill="#818cf8" stroke="#000" strokeWidth={1.5} />
                    ))}
                  </g>
                )}
              </svg>
            </div>
          </div>
        </div>

        {/* Sidebar */}
        <aside className="w-80 shrink-0 overflow-auto border-l border-border bg-surface">
          <div className="border-b border-border px-4 py-3">
            <div className="label-eyebrow text-xs">Polygons on {map?.name}</div>
          </div>
          <div className="flex flex-col gap-2 border-b border-border px-4 py-3">
            <div className="label-eyebrow text-xs text-muted-foreground">Import / Export</div>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={exportJson}
                disabled={mapPolys.length === 0}
                className="rounded-sm border border-border bg-surface px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted disabled:opacity-40"
              >
                Export JSON
              </button>
              <button
                onClick={() => fileInputRef.current?.click()}
                className="rounded-sm border border-border bg-surface px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted"
              >
                Import JSON
              </button>
              <button
                onClick={() => setCopyOpen((v) => !v)}
                disabled={mapPolys.length === 0}
                className="rounded-sm border border-border bg-surface px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted disabled:opacity-40"
              >
                Copy to map
              </button>
            </div>
            {copyOpen && (
              <select
                autoFocus
                defaultValue=""
                onChange={(e) => { if (e.target.value) copyToMap(e.target.value); }}
                className="rounded-sm border border-border bg-background px-2 py-1 text-xs"
              >
                <option value="" disabled>Select target map…</option>
                {allMapsCombined.filter((m) => m.id !== mapId).map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
              </select>
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) importJson(f);
                e.target.value = "";
              }}
            />
          </div>
          <div className="flex flex-col gap-2 border-b border-border px-4 py-3">
            {mode === "draw" ? (
              <>
                <span className="text-mono text-xs text-muted-foreground">
                  {draft.length} pts · click map to add · need ≥3
                </span>
                <button
                  onClick={() => finishDraft("forbidden")}
                  disabled={draft.length < 3}
                  className="rounded-sm border border-destructive/40 bg-destructive/10 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-destructive hover:bg-destructive/20 disabled:opacity-40"
                >
                  Save Forbidden
                </button>
                <button
                  onClick={() => finishDraft("safe")}
                  disabled={draft.length < 3}
                  className="rounded-sm border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-emerald-400 hover:bg-emerald-500/20 disabled:opacity-40"
                >
                  Save Safe
                </button>
                <button
                  onClick={() => { setDraft([]); setMode("idle"); }}
                  className="rounded-sm border border-border bg-surface px-3 py-1.5 text-xs uppercase tracking-wider hover:bg-muted"
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                onClick={() => { setMode("draw"); setDraft([]); setSelectedId(null); }}
                className="rounded-sm bg-primary px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110"
              >
                + Draw polygon
              </button>
            )}
          </div>
          {mapPolys.length === 0 && (
            <div className="px-4 py-6 text-center text-xs text-muted-foreground">
              No polygons yet. Click <span className="text-foreground">+ Draw polygon</span> and click on the map to add points.
            </div>
          )}
          <ul className="divide-y divide-border">
            {mapPolys.map((p) => (
              <PolygonRow
                key={p.id}
                poly={p}
                selected={p.id === selectedId}
                onSelect={() => setSelectedId(p.id)}
                onDelete={() => {
                  if (!confirm(`Delete "${p.name}"?`)) return;
                  removePolygon(p.id);
                  if (selectedId === p.id) setSelectedId(null);
                }}
              />
            ))}
          </ul>
        </aside>
      </div>
    </div>
  );
}

function PolygonRow({ poly, selected, onSelect, onDelete }: {
  poly: Polygon; selected: boolean; onSelect: () => void; onDelete: () => void;
}) {
  const [editingName, setEditingName] = useState(false);
  const [name, setName] = useState(poly.name);
  useEffect(() => setName(poly.name), [poly.name]);

  const commitName = () => {
    const v = name.trim();
    if (v && v !== poly.name) updatePolygon(poly.id, { name: v });
    else setName(poly.name);
    setEditingName(false);
  };

  const tagColor = poly.tag === "forbidden" ? "text-destructive border-destructive/40 bg-destructive/10" : "text-emerald-400 border-emerald-500/40 bg-emerald-500/10";

  return (
    <li
      onClick={onSelect}
      className={`cursor-pointer px-4 py-3 hover:bg-surface-2 ${selected ? "bg-surface-2" : ""}`}
    >
      <div className="flex items-center gap-2">
        {editingName ? (
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={commitName}
            onKeyDown={(e) => { if (e.key === "Enter") commitName(); if (e.key === "Escape") { setName(poly.name); setEditingName(false); } }}
            onClick={(e) => e.stopPropagation()}
            className="flex-1 rounded-sm border border-border bg-background px-2 py-1 text-xs"
          />
        ) : (
          <div
            className="flex-1 truncate text-sm font-semibold cursor-text"
            onDoubleClick={(e) => { e.stopPropagation(); setEditingName(true); }}
            title="Double-click to rename"
          >
            {poly.name}
          </div>
        )}
        <span className={`rounded-sm border px-1.5 py-0.5 text-xs uppercase tracking-wider ${tagColor}`}>
          {poly.tag}
        </span>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <select
          value={poly.tag}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => updatePolygon(poly.id, { tag: e.target.value as PolygonTag })}
          className="rounded-sm border border-border bg-background px-1.5 py-1 text-xs"
        >
          <option value="forbidden">forbidden</option>
          <option value="safe">safe</option>
        </select>
        <button
          onClick={(e) => { e.stopPropagation(); setEditingName(true); }}
          className="rounded-sm border border-border bg-surface px-2 py-1 text-xs hover:bg-muted"
        >
          Rename
        </button>
        <span className="flex-1" />
        <span className="text-mono text-xs text-muted-foreground">{poly.points.length} pts</span>
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          className="rounded-sm border border-destructive/40 bg-surface px-2 py-1 text-xs text-destructive hover:bg-destructive/10"
        >
          Delete
        </button>
      </div>
    </li>
  );
}