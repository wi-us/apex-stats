import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useRef, useState } from "react";
import {
  MAP_IDS,
  MAP_LABELS,
  type MapId,
  type PoiZone,
  getMapImage,
  getSeedZones,
  matchZone,
  normalizeSpot,
  slugifyPoi,
} from "@/lib/poi-zones";

/** ALGS spawn-location id -> canonical map id used in src/data/maps/<id>/. */
const MAP_ID_BY_ULID: Record<string, MapId> = {
  "01J6508ZVM8PZKJ9VSKA9SF33P": "olympus",
  "01J6508ZVMQGRZDC3XSNER795R": "kings_canyon",
  "01J6508ZVME92QPVXGJN21ZWCA": "storm_point",
  "01J6508ZVM9M8WFR5KVFB6R1FD": "worlds_edge",
  "01J6M00SDXM1G05TA8D96559MJ": "e_district",
  "01J6508ZVMSXSMEN6J4M5G5V38": "broken_moon",
};

function canonicalMapId(ulid: string | undefined | null): MapId | null {
  return ulid && MAP_ID_BY_ULID[ulid] ? MAP_ID_BY_ULID[ulid] : null;
}

export const Route = createFileRoute("/admin/poi")({ component: PoiAdmin });

type Drag = { id: string; mode: "move" | "resize"; ox: number; oy: number; or: number };

function PoiAdmin() {
  const [mapId, setMapId] = useState<MapId>("storm_point");
  const [zonesByMap, setZonesByMap] = useState<Record<MapId, PoiZone[]>>(() => {
    const o = {} as Record<MapId, PoiZone[]>;
    for (const m of MAP_IDS) o[m] = getSeedZones(m);
    return o;
  });
  const zones = zonesByMap[mapId];
  const setZones = (next: PoiZone[]) =>
    setZonesByMap((prev) => ({ ...prev, [mapId]: next }));

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [importedPicks, setImportedPicks] = useState<
    { stage: string; map_id: string; spot: string; team_name: string; team_slug: string }[]
  >([]);

  type StartSlot = {
    algs?: { cx_norm: number; cy_norm: number; r_norm?: number };
    motion?: { cx_norm: number; cy_norm: number; n_points?: number };
    delta_norm?: number;
    team_tag?: string | null;
    team_name?: string | null;
    poi?: { id?: string | null; name?: string | null } | null;
  };
  type StartCoords = {
    meta?: { series_id?: string; map?: string; canonical_size?: [number, number] };
    slots: Record<string, StartSlot>;
  };
  const [startCoords, setStartCoords] = useState<StartCoords | null>(null);

  // slot label per zone id (для подсветки POI-зон именами команд)
  const slotsByZoneId = useMemo(() => {
    const map = new Map<string, { slot: string; label: string }[]>();
    if (!startCoords) return map;
    for (const [slot, s] of Object.entries(startCoords.slots)) {
      const poiId = s.poi?.id;
      const poiName = s.poi?.name;
      let zoneId: string | null = null;
      if (poiId && zones.some((z) => z.id === poiId)) zoneId = poiId;
      else if (poiName) {
        const z = matchZone(zones, poiName);
        if (z) zoneId = z.id;
      }
      if (!zoneId) continue;
      const label = s.team_tag || s.team_name || slot.replace(/^slot_/, "");
      const arr = map.get(zoneId) ?? [];
      arr.push({ slot, label });
      map.set(zoneId, arr);
    }
    return map;
  }, [startCoords, zones]);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<Drag | null>(null);

  const selected = zones.find((z) => z.id === selectedId) ?? null;

  const toNorm = (clientX: number, clientY: number): { x: number; y: number } | null => {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (clientY - rect.top) / rect.height)),
    };
  };

  const onSvgClick = (e: React.MouseEvent) => {
    if (e.target !== svgRef.current) return; // ignore clicks on circles
    const p = toNorm(e.clientX, e.clientY);
    if (!p) return;
    const name = window.prompt("POI name?");
    if (!name) return;
    const id = uniqueId(slugifyPoi(name), zones);
    const zone: PoiZone = { id, name: name.trim(), cx: p.x, cy: p.y, r: 0.03 };
    setZones([...zones, zone]);
    setSelectedId(id);
  };

  const startMove = (e: React.PointerEvent, z: PoiZone) => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture(e.pointerId);
    dragRef.current = { id: z.id, mode: "move", ox: z.cx, oy: z.cy, or: z.r };
    setSelectedId(z.id);
  };

  const startResize = (e: React.PointerEvent, z: PoiZone) => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture(e.pointerId);
    dragRef.current = { id: z.id, mode: "resize", ox: z.cx, oy: z.cy, or: z.r };
    setSelectedId(z.id);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    const p = toNorm(e.clientX, e.clientY);
    if (!p) return;
    setZones(
      zones.map((z) => {
        if (z.id !== d.id) return z;
        if (d.mode === "move") return { ...z, cx: p.x, cy: p.y };
        const dx = p.x - z.cx;
        const dy = p.y - z.cy;
        const r = Math.min(0.4, Math.max(0.005, Math.hypot(dx, dy)));
        return { ...z, r };
      }),
    );
  };

  const endDrag = () => {
    dragRef.current = null;
  };

  const deleteSelected = () => {
    if (!selected) return;
    if (!window.confirm(`Delete POI "${selected.name}"?`)) return;
    setZones(zones.filter((z) => z.id !== selected.id));
    setSelectedId(null);
  };

  const renameSelected = () => {
    if (!selected) return;
    const name = window.prompt("New name", selected.name);
    if (!name) return;
    setZones(zones.map((z) => (z.id === selected.id ? { ...z, name: name.trim() } : z)));
  };

  const setAliasesForSelected = () => {
    if (!selected) return;
    const raw = window.prompt(
      "Aliases (comma-separated)",
      selected.aliases?.join(", ") ?? "",
    );
    if (raw == null) return;
    const aliases = raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    setZones(
      zones.map((z) =>
        z.id === selected.id ? { ...z, aliases: aliases.length ? aliases : undefined } : z,
      ),
    );
  };

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(zones, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${mapId}__poi_zones.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const importZonesFile = async (file: File) => {
    try {
      const text = await file.text();
      const parsed = JSON.parse(text) as PoiZone[];
      if (!Array.isArray(parsed)) throw new Error("Expected an array");
      setZones(parsed);
      setSelectedId(null);
    } catch (e) {
      alert(`Import failed: ${(e as Error).message}`);
    }
  };

  const importStartCoords = async (file: File) => {
    try {
      const text = await file.text();
      const data = JSON.parse(text) as StartCoords;
      if (!data || typeof data !== "object" || !data.slots) {
        throw new Error("Expected { meta, slots: {...} }");
      }
      setStartCoords(data);
      const m = data.meta?.map;
      if (m && (MAP_IDS as readonly string[]).includes(m)) {
        setMapId(m as MapId);
      }
    } catch (e) {
      alert(`Import failed: ${(e as Error).message}`);
    }
  };

  const importTournamentJson = async (file: File) => {
    try {
      const text = await file.text();
      const data = JSON.parse(text);

      // 1. ALGS /v1/poi-drafts/{id}/locations — { spawnLocations: [...] }
      if (Array.isArray(data?.spawnLocations)) {
        let added = 0;
        let skippedWrongMap = 0;
        const next = [...zones];
        const ids = new Set(next.map((z) => z.id));
        for (const loc of data.spawnLocations as Array<{
          id: string;
          name: string;
          x: string | number;
          y: string | number;
          inGameDropId?: number;
          map?: { id?: string };
        }>) {
          const targetMap = canonicalMapId(loc.map?.id);
          if (targetMap && targetMap !== mapId) {
            skippedWrongMap++;
            continue;
          }
          if (ids.has(loc.id)) continue;
          const cx = Number(loc.x) / 100;
          const cy = Number(loc.y) / 100;
          if (!Number.isFinite(cx) || !Number.isFinite(cy)) continue;
          next.push({ id: loc.id, name: loc.name, cx, cy, r: 0.03 });
          ids.add(loc.id);
          added++;
        }
        setZones(next);
        alert(
          `Imported ${added} ALGS spawn locations.` +
            (skippedWrongMap
              ? ` Skipped ${skippedWrongMap} for other maps.`
              : ""),
        );
        return;
      }

      // 2. ALGS /v1/poi-drafts/{id}/pick — { picks: [...] }
      if (Array.isArray(data?.picks)) {
        const picks: typeof importedPicks = [];
        for (const p of data.picks as Array<{
          spawnLocation?: { name?: string };
          team?: { name?: string; shortName?: string };
          map?: { id?: string; name?: string };
        }>) {
          const mid = canonicalMapId(p.map?.id) ?? mapId;
          const spot = p.spawnLocation?.name;
          const name = p.team?.name ?? p.team?.shortName;
          if (!spot || !name) continue;
          picks.push({
            stage: "algs",
            map_id: mid,
            spot,
            team_name: name,
            team_slug: (p.team?.shortName ?? name).toLowerCase(),
          });
        }
        setImportedPicks(picks);
        return;
      }

      // 3. Legacy Liquipedia tournament JSON ({ poi_drafts: { stage: { map: [...] } } })
      const picks: typeof importedPicks = [];
      const drafts = data?.poi_drafts as
        | Record<string, Record<string, { team_slug: string; team_name: string; spot: string | null }[]>>
        | undefined;
      if (!drafts) {
        alert(
          "Unknown JSON shape. Expected ALGS spawnLocations/picks or " +
            "Liquipedia poi_drafts.",
        );
        return;
      }
      for (const [stage, byMap] of Object.entries(drafts)) {
        for (const [mid, rows] of Object.entries(byMap)) {
          for (const r of rows) {
            if (r.spot)
              picks.push({
                stage,
                map_id: mid,
                spot: r.spot,
                team_name: r.team_name,
                team_slug: r.team_slug,
              });
          }
        }
      }
      setImportedPicks(picks);
    } catch (e) {
      alert(`Import failed: ${(e as Error).message}`);
    }
  };

  const picksForMap = importedPicks.filter((p) => p.map_id === mapId);
  const missingSpots = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const p of picksForMap) {
      const k = normalizeSpot(p.spot);
      if (seen.has(k)) continue;
      seen.add(k);
      if (!matchZone(zones, p.spot)) out.push(p.spot);
    }
    return out;
  }, [picksForMap, zones]);

  const addMissingAsStubs = () => {
    if (!missingSpots.length) return;
    const stubs: PoiZone[] = missingSpots.map((name, i) => ({
      id: uniqueId(slugifyPoi(name), zones),
      name,
      cx: 0.5 + ((i % 5) - 2) * 0.02,
      cy: 0.5 + Math.floor(i / 5) * 0.02,
      r: 0.025,
    }));
    setZones([...zones, ...stubs]);
  };

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
        <div className="flex items-center gap-3">
          <h1 className="text-sm font-semibold uppercase tracking-wider">POI Zones</h1>
          <select
            value={mapId}
            onChange={(e) => {
              setMapId(e.target.value as MapId);
              setSelectedId(null);
            }}
            className="rounded-sm border border-border bg-surface px-2 py-1 text-xs"
          >
            {MAP_IDS.map((m) => (
              <option key={m} value={m}>
                {MAP_LABELS[m]}
              </option>
            ))}
          </select>
          <span className="text-xs text-muted-foreground">{zones.length} zones</span>
        </div>
        <div className="flex items-center gap-2">
          <label className="cursor-pointer rounded-sm border border-border bg-surface px-2 py-1 text-xs hover:bg-muted">
            Import zones JSON
            <input
              type="file"
              accept="application/json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void importZonesFile(f);
                e.target.value = "";
              }}
            />
          </label>
          <label className="cursor-pointer rounded-sm border border-border bg-surface px-2 py-1 text-xs hover:bg-muted">
            Load ALGS / tournament JSON
            <input
              type="file"
              accept="application/json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void importTournamentJson(f);
                e.target.value = "";
              }}
            />
          </label>
          <label className="cursor-pointer rounded-sm border border-border bg-surface px-2 py-1 text-xs hover:bg-muted">
            Load start_coords.json
            <input
              type="file"
              accept="application/json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void importStartCoords(f);
                e.target.value = "";
              }}
            />
          </label>
          {startCoords && (
            <button
              type="button"
              onClick={() => setStartCoords(null)}
              className="rounded-sm border border-border bg-surface px-2 py-1 text-xs hover:bg-muted"
              title="Clear start coords overlay"
            >
              Clear starts
            </button>
          )}
          <button
            type="button"
            onClick={exportJson}
            className="rounded-sm border border-border bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground hover:opacity-90"
          >
            Export JSON
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <section className="flex min-w-0 flex-1 items-center justify-center overflow-hidden bg-background p-4">
          <div className="relative aspect-square w-full max-w-[min(100%,calc(100vh-9rem))]">
            <img
              src={getMapImage(mapId)}
              alt={MAP_LABELS[mapId]}
              className="absolute inset-0 h-full w-full select-none object-contain"
              draggable={false}
            />
            <svg
              ref={svgRef}
              viewBox="0 0 1 1"
              preserveAspectRatio="none"
              className="absolute inset-0 h-full w-full"
              onClick={onSvgClick}
              onPointerMove={onPointerMove}
              onPointerUp={endDrag}
              onPointerLeave={endDrag}
            >
              {zones.map((z) => {
                const isSel = z.id === selectedId;
                const slotsHere = slotsByZoneId.get(z.id);
                const hasSlots = !!slotsHere?.length;
                return (
                  <g key={z.id}>
                    <circle
                      cx={z.cx}
                      cy={z.cy}
                      r={z.r}
                      fill={
                        isSel
                          ? "rgba(34,196,245,0.25)"
                          : hasSlots
                            ? "rgba(255,77,109,0.35)"
                            : "rgba(250,204,21,0.18)"
                      }
                      stroke={isSel ? "#22c4f5" : hasSlots ? "#ff4d6d" : "#facc15"}
                      strokeWidth={hasSlots ? 0.003 : 0.002}
                      onPointerDown={(e) => startMove(e, z)}
                      style={{ cursor: "move" }}
                    />
                    {isSel && (
                      <circle
                        cx={z.cx + z.r}
                        cy={z.cy}
                        r={0.006}
                        fill="#22c4f5"
                        stroke="#fff"
                        strokeWidth={0.001}
                        onPointerDown={(e) => startResize(e, z)}
                        style={{ cursor: "ew-resize" }}
                      />
                    )}
                    <text
                      x={z.cx}
                      y={z.cy - z.r - 0.005}
                      textAnchor="middle"
                      fontSize={0.014}
                      fill="#fff"
                      stroke="#000"
                      strokeWidth={0.0008}
                      paintOrder="stroke"
                      style={{ pointerEvents: "none" }}
                    >
                      {z.name}
                    </text>
                    {hasSlots && (
                      <text
                        x={z.cx}
                        y={z.cy + 0.005}
                        textAnchor="middle"
                        fontSize={0.018}
                        fontWeight={700}
                        fill="#fff"
                        stroke="#000"
                        strokeWidth={0.001}
                        paintOrder="stroke"
                        style={{ pointerEvents: "none" }}
                      >
                        {slotsHere!.map((s) => s.label).join(" · ")}
                      </text>
                    )}
                  </g>
                );
              })}
            </svg>
            {startCoords && (
              <svg
                viewBox="0 0 1 1"
                preserveAspectRatio="none"
                className="pointer-events-none absolute inset-0 h-full w-full"
              >
                {Object.entries(startCoords.slots).map(([slot, s]) => {
                  const label = slot.replace(/^slot_/, "");
                  return (
                    <g key={slot}>
                      {s.algs && (
                        <circle
                          cx={s.algs.cx_norm}
                          cy={s.algs.cy_norm}
                          r={0.012}
                          fill="none"
                          stroke="#22c4f5"
                          strokeWidth={0.0025}
                        />
                      )}
                      {s.motion && (
                        <>
                          <circle
                            cx={s.motion.cx_norm}
                            cy={s.motion.cy_norm}
                            r={0.008}
                            fill="#ff4d6d"
                            stroke="#fff"
                            strokeWidth={0.0015}
                          />
                          <text
                            x={s.motion.cx_norm + 0.012}
                            y={s.motion.cy_norm + 0.005}
                            fontSize={0.014}
                            fill="#fff"
                            stroke="#000"
                            strokeWidth={0.0008}
                            paintOrder="stroke"
                          >
                            {label}
                          </text>
                        </>
                      )}
                      {s.algs && s.motion && (
                        <line
                          x1={s.algs.cx_norm}
                          y1={s.algs.cy_norm}
                          x2={s.motion.cx_norm}
                          y2={s.motion.cy_norm}
                          stroke="#ffffff"
                          strokeOpacity={0.5}
                          strokeWidth={0.0012}
                          strokeDasharray="0.006 0.004"
                        />
                      )}
                    </g>
                  );
                })}
              </svg>
            )}
          </div>
        </section>

        <aside className="flex w-[280px] shrink-0 flex-col gap-3 border-l border-border bg-surface p-3 text-xs">
          <div>
            <div className="label-eyebrow mb-1 text-muted-foreground">Selected</div>
            {selected ? (
              <div className="space-y-1">
                <div className="font-semibold">{selected.name}</div>
                <div className="font-mono text-muted-foreground">
                  cx={selected.cx.toFixed(3)} cy={selected.cy.toFixed(3)} r={selected.r.toFixed(3)}
                </div>
                {selected.aliases?.length ? (
                  <div className="text-muted-foreground">aliases: {selected.aliases.join(", ")}</div>
                ) : null}
                <div className="flex flex-wrap gap-1 pt-1">
                  <button onClick={renameSelected} className="rounded-sm border border-border px-2 py-1 hover:bg-muted">Rename</button>
                  <button onClick={setAliasesForSelected} className="rounded-sm border border-border px-2 py-1 hover:bg-muted">Aliases</button>
                  <button onClick={deleteSelected} className="rounded-sm border border-destructive px-2 py-1 text-destructive hover:bg-destructive/10">Delete</button>
                </div>
              </div>
            ) : (
              <div className="text-muted-foreground">Click on the map to add a POI.</div>
            )}
          </div>

          <hr className="border-border" />

          <div className="min-h-0 flex-1 overflow-auto">
            <div className="label-eyebrow mb-1 text-muted-foreground">Zones</div>
            <ul className="space-y-0.5">
              {zones.map((z) => (
                <li key={z.id}>
                  <button
                    onClick={() => setSelectedId(z.id)}
                    className={`block w-full truncate rounded-sm px-2 py-1 text-left ${
                      z.id === selectedId ? "bg-primary/15 text-primary" : "hover:bg-muted"
                    }`}
                    title={z.id}
                  >
                    {z.name}
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {picksForMap.length > 0 && (
            <>
              <hr className="border-border" />
              <div>
                <div className="label-eyebrow mb-1 text-muted-foreground">
                  Tournament picks · {picksForMap.length}
                </div>
                <div className="mb-1 text-muted-foreground">
                  Missing in zones: {missingSpots.length}
                </div>
                {missingSpots.length > 0 && (
                  <button
                    onClick={addMissingAsStubs}
                    className="mb-2 w-full rounded-sm border border-border bg-muted px-2 py-1 text-left hover:bg-muted/70"
                  >
                    + Add {missingSpots.length} missing as stubs (center)
                  </button>
                )}
                <ul className="max-h-40 overflow-auto text-muted-foreground">
                  {missingSpots.map((s) => (
                    <li key={s} className="truncate font-mono">{s}</li>
                  ))}
                </ul>
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

function uniqueId(base: string, existing: PoiZone[]): string {
  let id = base;
  let n = 2;
  const ids = new Set(existing.map((z) => z.id));
  while (ids.has(id)) {
    id = `${base}-${n++}`;
  }
  return id;
}