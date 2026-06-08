import { useSyncExternalStore } from "react";
import {
  teams as seedTeams,
  matches as seedMatches,
  matchSeedExtras,
  tournaments as seedTournaments,
  maps as seedMaps,
  type Team,
  type Tournament,
  type MatchFull,
} from "@/lib/mock-match";
import { getAlgsSnapshotBundle } from "@/lib/algs-snapshot";

export type PolygonTag = "forbidden" | "safe";
export type Polygon = {
  id: string;
  mapId: string;
  name: string;
  tag: PolygonTag;
  /** Normalized [0..1] points over the map. */
  points: { x: number; y: number }[];
};

/**
 * Zone tags are dynamic — теги команд (`team_1`..`team_20`), `hud`, и кастомные
 * добавляются на лету в /admin/zones. Поэтому `tag` — обычная строка, а не enum.
 */
export type ZoneTag = string;
export type Zone = { id: string; name: string; tag: ZoneTag; x: number; y: number; w: number; h: number };
export type ZoneMode = "vod" | "vod2" | "camera";

export type CustomMap = { id: string; name: string; image: string };

export type ProcessPov = "map" | "team";
export type ProcessStatus = "draft" | "queued" | "running" | "done" | "failed";
export type ProcessKind =
  | "minimap"
  | "camera"
  | "full"
  | "hsv"
  | "ring"
  | "debug_export";
export type MapTiming = { mapId: string; startSec: number; endSec: number };
export type MapAnalysis = {
  mapIndex: number;
  ring: number;    // 0..100, independent
  start: number;   // 0..100, independent
  camera: number;  // 0..100, independent
  teams: { teamId: string; progress: number }[]; // independent per-team detection
};
export type AnalysisProcess = {
  id: string;
  pov: ProcessPov;
  kind?: ProcessKind;
  live: boolean;
  streamUrl: string;
  videoTitle?: string;
  videoChannel?: string;
  videoDurationSec?: number;
  region?: string;
  day?: string;
  matchup?: string;
  tournamentId: string;
  matchId: string;
  teamId?: string;
  mapCount?: number;
  maps: MapTiming[];
  mapAnalyses?: MapAnalysis[];
  status: ProcessStatus;
  createdAt: number;
  /* extended fields for operator control center */
  preset?: string;
  frameStep?: number;
  debugMode?: boolean;
  startedAt?: number;
  finishedAt?: number;
  errorMessage?: string;
  qualityScore?: number;
  needsReview?: boolean;
};

const initialVod: Zone[] = [
  { id: "v-minimap",  name: "Minimap",    tag: "minimap",  x: 20,   y: 30,   w: 320, h: 320 },
  { id: "v-map-name", name: "Map name",   tag: "map_name", x: 360,  y: 170,  w: 380, h: 80  },
  { id: "v-timer",    name: "Round timer",tag: "timer",    x: 20,   y: 380,  w: 320, h: 90  },
  { id: "v-team-l",   name: "Team panel", tag: "team",     x: 20,   y: 720,  w: 540, h: 280 },
];
const initialVod2: Zone[] = [];
const initialCamera: Zone[] = [
  { id: "c-name",  name: "Player name",  tag: "camera",  x: 60,   y: 730, w: 480, h: 90 },
  { id: "c-squad", name: "Squad badge",  tag: "team",    x: 60,   y: 830, w: 480, h: 120 },
  { id: "c-time",  name: "Round timer",  tag: "timer",   x: 60,   y: 280, w: 320, h: 80  },
  { id: "c-mini",  name: "Minimap",      tag: "minimap", x: 20,   y: 20,  w: 360, h: 260 },
];

type State = {
  teams: Team[];
  matches: MatchFull[];
  tournaments: Tournament[];
  polygons: Polygon[];
  zones: { vod: Zone[]; vod2: Zone[]; camera: Zone[] };
  processes: AnalysisProcess[];
  customMaps: CustomMap[];
};

type AlgsBootstrapBundle = {
  teams: Team[];
  tournaments: Tournament[];
  matches: MatchFull[];
  maps: CustomMap[];
};

const CUSTOM_MAPS_KEY = "admin:customMaps";
const ALGS_BUNDLE_CACHE_KEY = "admin:algsBundle";

// Seed: каждый match содержит N games (через mapIds/gameDurations). Все 20 команд участвуют.
const initialMatches: MatchFull[] = seedMatches.map((m) => {
  const extras = matchSeedExtras[m.id];
  return {
    ...m,
    mapIds: extras?.mapIds ?? [m.mapId],
    gameDurations: extras?.gameDurations,
    vodLink: "",
    teamIds: seedTeams.map((t) => t.id),
    teamVods: {},
  };
});

const testTournamentIds = new Set(seedTournaments.filter((t) => t.id.startsWith("test-")).map((t) => t.id));
const testMatchIds = new Set(initialMatches.filter((m) => m.id.startsWith("m-test")).map((m) => m.id));
const snapshotBundle = getAlgsSnapshotBundle();

function loadCachedAlgsBundle(): AlgsBootstrapBundle | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(ALGS_BUNDLE_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<AlgsBootstrapBundle>;
    if (!Array.isArray(parsed.teams) || !Array.isArray(parsed.tournaments) || !Array.isArray(parsed.matches) || !Array.isArray(parsed.maps)) {
      return null;
    }
    return {
      teams: parsed.teams,
      tournaments: parsed.tournaments,
      matches: parsed.matches,
      maps: parsed.maps,
    };
  } catch {
    return null;
  }
}

function persistAlgsBundle(bundle: AlgsBootstrapBundle) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ALGS_BUNDLE_CACHE_KEY, JSON.stringify(bundle));
  } catch {
    /* cache is optional */
  }
}

function mergeById<T extends { id: string }>(primary: T[], secondary: T[]): T[] {
  const byId = new Map<string, T>();
  for (const item of secondary) byId.set(item.id, item);
  for (const item of primary) byId.set(item.id, item);
  return Array.from(byId.values());
}

function normalizeMapKey(map: Pick<CustomMap, "id" | "name">): string {
  const seed = seedMaps.find((item) => item.id === map.id);
  const name = seed?.name ?? map.name ?? map.id;
  return name
    .toLowerCase()
    .replace(/['’]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

function isSeedMapId(id: string): boolean {
  return seedMaps.some((map) => map.id === id);
}

function mergeCustomMaps(snapshotMaps: CustomMap[], savedMaps: CustomMap[]): CustomMap[] {
  const byKey = new Map<string, CustomMap>();
  for (const item of [...snapshotMaps, ...savedMaps]) {
    const key = normalizeMapKey(item);
    const prev = byKey.get(key);
    const preferItemId = !prev || isSeedMapId(item.id) || !isSeedMapId(prev.id);
    byKey.set(key, {
      id: preferItemId ? item.id : prev.id,
      name: item.name || prev?.name || item.id,
      image: item.image || prev?.image || "",
    });
  }
  return Array.from(byKey.values());
}

const cachedBundle = loadCachedAlgsBundle();
const bootstrapBundle = cachedBundle ?? snapshotBundle;

let state: State = {
  teams: bootstrapBundle.teams.length > 0 ? bootstrapBundle.teams : seedTeams,
  matches: mergeById(
    initialMatches.filter((m) => testMatchIds.has(m.id)),
    bootstrapBundle.matches.length > 0 ? bootstrapBundle.matches : initialMatches,
  ),
  tournaments: mergeById(
    seedTournaments.filter((t) => testTournamentIds.has(t.id)),
    bootstrapBundle.tournaments.length > 0 ? bootstrapBundle.tournaments : seedTournaments,
  ),
  polygons: [],
  zones: { vod: initialVod, vod2: initialVod2, camera: initialCamera },
  processes: [],
  customMaps: mergeCustomMaps(bootstrapBundle.maps, loadCustomMaps()),
};

function loadCustomMaps(): CustomMap[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(CUSTOM_MAPS_KEY);
    return raw ? (JSON.parse(raw) as CustomMap[]) : [];
  } catch {
    return [];
  }
}
function persistCustomMaps(maps: CustomMap[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(CUSTOM_MAPS_KEY, JSON.stringify(maps));
  } catch {
    /* quota or serialization errors – ignore */
  }
}

const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
};
const getSnapshot = () => state;

export function useAdminStore(): State {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export function setTeams(teams: Team[]) {
  state = { ...state, teams };
  emit();
}
export function setMatches(matches: MatchFull[]) {
  state = { ...state, matches };
  emit();
}
export function addMatch(m: MatchFull) {
  state = { ...state, matches: [...state.matches, m] };
  emit();
}
export function updateMatch(id: string, patch: Partial<MatchFull>) {
  state = {
    ...state,
    matches: state.matches.map((m) => (m.id === id ? { ...m, ...patch } : m)),
  };
  emit();
}
export function updateTeam(id: string, patch: Partial<Team>) {
  state = {
    ...state,
    teams: state.teams.map((t) => (t.id === id ? { ...t, ...patch } : t)),
  };
  emit();
}
export function setTournaments(tournaments: Tournament[]) {
  state = { ...state, tournaments };
  emit();
}

/**
 * Replace teams/tournaments/matches in bulk with data fetched from ALGS
 * (algs_* tables). Existing customMaps from local edits are preserved;
 * incoming ALGS maps are merged by id (ALGS wins on name).
 */
export function replaceFromAlgs(payload: {
  teams: Team[];
  tournaments: Tournament[];
  matches: MatchFull[];
  maps: CustomMap[];
}) {
  const customMaps = mergeCustomMaps(payload.maps, state.customMaps);
  persistCustomMaps(customMaps);
  state = {
    ...state,
    teams: payload.teams.length > 0 ? payload.teams : state.teams,
    tournaments: payload.tournaments.length > 0
      ? mergeById(seedTournaments.filter((t) => testTournamentIds.has(t.id)), payload.tournaments)
      : state.tournaments,
    matches: payload.matches.length > 0
      ? mergeById(initialMatches.filter((m) => testMatchIds.has(m.id)), payload.matches)
      : state.matches,
    customMaps,
  };
  persistAlgsBundle({
    teams: state.teams,
    tournaments: state.tournaments.filter((t) => !testTournamentIds.has(t.id)),
    matches: state.matches.filter((m) => !testMatchIds.has(m.id)),
    maps: customMaps,
  });
  emit();
}

export function setPolygons(polygons: Polygon[]) {
  state = { ...state, polygons };
  emit();
}
export function addPolygon(p: Polygon) {
  state = { ...state, polygons: [...state.polygons, p] };
  emit();
}
export function updatePolygon(id: string, patch: Partial<Polygon>) {
  state = {
    ...state,
    polygons: state.polygons.map((p) => (p.id === id ? { ...p, ...patch } : p)),
  };
  emit();
}
export function removePolygon(id: string) {
  state = { ...state, polygons: state.polygons.filter((p) => p.id !== id) };
  emit();
}

export function setZones(mode: ZoneMode, zones: Zone[]) {
  state = { ...state, zones: { ...state.zones, [mode]: zones } };
  emit();
}
export function getMinimapZone(mode: ZoneMode = "vod"): Zone | undefined {
  return state.zones[mode].find((z) => z.tag === "minimap");
}

export function addProcess(p: AnalysisProcess) {
  state = { ...state, processes: [p, ...state.processes] };
  emit();
}
export function updateProcess(id: string, patch: Partial<AnalysisProcess>) {
  state = {
    ...state,
    processes: state.processes.map((p) => (p.id === id ? { ...p, ...patch } : p)),
  };
  emit();
}
export function removeProcess(id: string) {
  state = { ...state, processes: state.processes.filter((p) => p.id !== id) };
  emit();
}

export function addCustomMap(m: CustomMap) {
  const next = [...state.customMaps, m];
  state = { ...state, customMaps: next };
  persistCustomMaps(next);
  emit();
}
export function updateCustomMap(id: string, patch: Partial<CustomMap>) {
  const next = state.customMaps.map((m) => (m.id === id ? { ...m, ...patch } : m));
  state = { ...state, customMaps: next };
  persistCustomMaps(next);
  emit();
}
export function removeCustomMap(id: string) {
  const next = state.customMaps.filter((m) => m.id !== id);
  state = { ...state, customMaps: next, polygons: state.polygons.filter((p) => p.mapId !== id) };
  persistCustomMaps(next);
  emit();
}
