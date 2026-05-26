// Mock data for the Apex Stats Match Viewer.
// Coordinates are normalized in [0..1] over the map viewport.

import worldsEdgeImg from "@/assets/maps/worlds-edge.webp";
import kingsCanyonImg from "@/assets/maps/kings-canyon.webp";
import stormPointImg from "@/assets/maps/storm-point.webp";
import brokenMoonImg from "@/assets/maps/broken-moon.webp";
import olympusImg from "@/assets/maps/olympus.webp";
import eDistrictImg from "@/assets/maps/e-district.webp";
import liquidLogo from "@/assets/teams/liquid.png";
import fazeLogo from "@/assets/teams/faze.png";
import luminosityLogo from "@/assets/teams/luminosity.png";
import darkzeroLogo from "@/assets/teams/darkzero.png";

export type TournamentType = "LAN" | "Online" | "Qualifier";
export type TournamentRegion = "EMEA" | "APAC" | "North America" | "South America";
export type TournamentStatus = "draft" | "upcoming" | "active" | "finished" | "archived";
export type TournamentStage = "Regular Season" | "Playoffs" | "Finals" | "Qualifier" | "Group Stage";
export type Tournament = {
  id: string;
  name: string;
  startDate: string; // ISO yyyy-mm-dd
  endDate: string;   // ISO yyyy-mm-dd
  year: number;      // 1..6
  type: TournamentType;
  region: TournamentRegion;
  /** Optional manual status override. When absent, derived from dates/matches. */
  status?: TournamentStatus;
  /** ALGS split (1 or 2), or free-form. */
  split?: string;
  /** Stage within the split, e.g. Playoffs, Regular Season. */
  stage?: TournamentStage;
  /** Free-form notes shown in the admin overview. */
  description?: string;
  /** Link to the Liquipedia page for this tournament. */
  liquipediaUrl?: string;
};
export type Match = { id: string; name: string; tournamentId: string; mapId: string; durationSec: number };

/** Extended fields layered on top of Match for the admin UI. */
export type MatchExtras = {
  vodLink?: string;
  /** Ordered list of maps played within this match. Falls back to [mapId]. */
  mapIds?: string[];
  /** Per-game duration in seconds, aligned with mapIds. Falls back to match.durationSec for each. */
  gameDurations?: number[];
  /** Per-team POV VOD links (YouTube URLs). */
  teamVods?: Record<string, string>;
  /** Per-map (per-game) VOD links, indexed by game index. */
  mapVods?: Record<number, string>;
  /** Common Map VOD link applied across all maps. */
  mapVodCommon?: string;
  /** Teams that participated. */
  teamIds?: string[];
  /** ISO timestamp when the match started (from ALGS). */
  startedAt?: string | null;
  /** ISO timestamp when the match completed (from ALGS). */
  completedAt?: string | null;
  /** Raw series status from ALGS (e.g. "completed", "live", "scheduled"). */
  seriesStatus?: string | null;
};
export type MatchFull = Match & MatchExtras;
export type MapConfigKey = "image" | "zones" | "polygons" | "hsv" | "camera" | "minimap";
export type ApexMap = {
  id: string;
  name: string;
  image: string;
  /** Short code, e.g. WE, KC, SP. */
  code?: string;
  /** Optional cover/preview image shown in admin lists. Falls back to `image`. */
  previewImage?: string;
  /** Per-feature configuration status for the admin pipeline. */
  config?: Partial<Record<MapConfigKey, boolean>>;
};

/**
 * A Game = a single map analyzed inside a Match.
 * Tournament → Match → Game (carte) is the canonical project hierarchy.
 */
export type Game = {
  id: string;
  matchId: string;
  index: number;
  mapId: string;
  durationSec: number;
};

export function gameIdFor(matchId: string, index: number): string {
  return `${matchId}-g${index + 1}`;
}
export function parseGameId(gameId: string): { matchId: string; index: number } | null {
  const m = /^(.*)-g(\d+)$/.exec(gameId);
  if (!m) return null;
  return { matchId: m[1], index: Number(m[2]) - 1 };
}
/** Resolve the ordered list of games for a match, deriving from mapIds (fallback [mapId]). */
export function getGames(match: Pick<MatchFull, "id" | "mapId" | "mapIds" | "durationSec" | "gameDurations">): Game[] {
  const ids = match.mapIds && match.mapIds.length > 0 ? match.mapIds : [match.mapId];
  return ids.map((mapId, i) => ({
    id: gameIdFor(match.id, i),
    matchId: match.id,
    index: i,
    mapId,
    durationSec: match.gameDurations?.[i] ?? match.durationSec,
  }));
}
export function matchDurationSec(match: Pick<MatchFull, "id" | "mapId" | "mapIds" | "durationSec" | "gameDurations">): number {
  return getGames(match).reduce((s, g) => s + g.durationSec, 0);
}

export const tournaments: Tournament[] = [
  { id: "test-tournament", name: "Test турнир",                  startDate: "2026-05-01", endDate: "2026-05-31", year: 6, type: "Online",    region: "EMEA", description: "Реальные данные из hud_read pipeline" },
  { id: "algs-2026-split-1", name: "ALGS 2026 — Split 1 Playoffs", startDate: "2026-02-14", endDate: "2026-02-18", year: 6, type: "LAN",       region: "North America" },
  { id: "esl-pro-league-12", name: "ESL Apex Pro League S12",      startDate: "2026-03-02", endDate: "2026-03-29", year: 6, type: "Online",    region: "EMEA" },
  { id: "scrims-eu-week-4",  name: "EU Pro Scrims — Week 4",       startDate: "2026-04-06", endDate: "2026-04-10", year: 6, type: "Qualifier", region: "EMEA" },
  { id: "algs-2026-split-2", name: "ALGS 2026 — Split 2 Playoffs", startDate: "2026-05-15", endDate: "2026-05-22", year: 6, type: "LAN",       region: "APAC" },
  { id: "apac-pro-league",   name: "APAC Pro League S3",           startDate: "2026-06-10", endDate: "2026-06-28", year: 6, type: "Online",    region: "APAC" },
  { id: "algs-championship", name: "ALGS Championship 2026",       startDate: "2026-08-20", endDate: "2026-08-30", year: 6, type: "LAN",       region: "EMEA" },
];

export const maps: ApexMap[] = [
  { id: "worlds-edge",  name: "World's Edge",  image: worldsEdgeImg },
  { id: "kings-canyon", name: "King's Canyon", image: kingsCanyonImg },
  { id: "storm-point",  name: "Storm Point",   image: stormPointImg },
  { id: "broken-moon",  name: "Broken Moon",   image: brokenMoonImg },
  { id: "olympus",      name: "Olympus",       image: olympusImg },
  { id: "e-district",   name: "E-District",    image: eDistrictImg },
];

export const matches: Match[] = [
  // Test = единственный матч с реальными данными hud_read (см. src/data/m-test-g1/).
  { id: "m-test", name: "Test матч", tournamentId: "test-tournament", mapId: "storm-point", durationSec: 1174 },
  // Test2 = трекинг-only тест на карте Olympus (см. src/data/m-test-g2/).
  { id: "m-test2", name: "Test матч · Olympus", tournamentId: "test-tournament", mapId: "olympus", durationSec: 1192 },
  // Match = серия игр (карт) внутри турнира. mapId/durationSec — это первая игра (для обратной совместимости),
  // полный список игр живёт в MatchExtras.mapIds (см. admin-store / getGames()).
  { id: "m-001", name: "Match Day 1", tournamentId: "algs-2026-split-1", mapId: "worlds-edge", durationSec: 1320 },
  { id: "m-002", name: "Match Day 2", tournamentId: "algs-2026-split-1", mapId: "broken-moon", durationSec: 1190 },
  { id: "m-003", name: "Week 1",      tournamentId: "esl-pro-league-12", mapId: "olympus",     durationSec: 1400 },
];

/**
 * Расширенный seed: каждый Match содержит несколько games (карт).
 * Применяется через admin-store. Длительности по каждой game — gameDurations.
 */
export const matchSeedExtras: Record<string, Pick<MatchExtras, "mapIds" | "gameDurations">> = {
  "m-test": { mapIds: ["storm-point"],                              gameDurations: [1174] },
  "m-test2": { mapIds: ["olympus"],                                  gameDurations: [1192] },
  "m-001": { mapIds: ["worlds-edge", "storm-point"],                 gameDurations: [1320, 1480] },
  "m-002": { mapIds: ["broken-moon", "e-district"],                  gameDurations: [1190, 1260] },
  "m-003": { mapIds: ["olympus", "kings-canyon"],                    gameDurations: [1400, 1320] },
};

export type Team = {
  id: string;
  tag: string;
  name: string;
  color: string;
  /** Optional logo URL. When absent, the UI falls back to the site logo. */
  logo?: string;
  /** Optional logo variant for light theme. Falls back to `logo`. */
  logoLight?: string;
  /** Optional logo variant for dark theme. Falls back to `logo`. */
  logoDark?: string;
  players: string[];
  placement: number;
  kills: number;
  alive: boolean;
  /** Lifecycle status. Defaults to "active" when missing. */
  status?: "active" | "archived";
  /** Optional Liquipedia team page URL. */
  liquipediaUrl?: string;
};

export const teams: Team[] = [
  { id: "t-tsm",  tag: "TSM",  name: "TSM",            color: "#ff5b12", players: ["ImperialHal", "Verhulst", "Reps"],     placement: 1,  kills: 11, alive: true  },
  { id: "t-drg",  tag: "DZ",   name: "DarkZero",       color: "#22c4f5", logo: darkzeroLogo, players: ["Zer0", "Gild", "Sharky"],              placement: 2,  kills: 9,  alive: true  },
  { id: "t-nrg",  tag: "NRG",  name: "NRG",            color: "#ffd23f", players: ["Sweet", "Gent", "nafen"],              placement: 3,  kills: 7,  alive: true  },
  { id: "t-sen",  tag: "SEN",  name: "Sentinels",      color: "#e879f9", players: ["Naghz", "Zenoo", "Ojrein"],            placement: 4,  kills: 8,  alive: true  },
  { id: "t-c9",   tag: "C9",   name: "Cloud9",         color: "#a78bfa", players: ["Wxltzy", "Genburten", "Mande"],        placement: 5,  kills: 6,  alive: true  },
  { id: "t-faze", tag: "FAZE", name: "FaZe Clan",      color: "#fb923c", logo: fazeLogo, players: ["Sikezz", "rpr", "Snip3down"],          placement: 6,  kills: 3,  alive: true  },
  { id: "t-tl",   tag: "TL",   name: "Team Liquid",    color: "#60a5fa", logo: liquidLogo, players: ["Hakis", "Yuki", "Keon"],               placement: 7,  kills: 5,  alive: true  },
  { id: "t-fa",   tag: "FA",   name: "Furia",          color: "#f87171", players: ["Pandxrz", "Albralelie", "Rambeau"],    placement: 8,  kills: 4,  alive: true  },
  { id: "t-mv",   tag: "MV",   name: "Moist Esports",  color: "#86efac", players: ["Xeratricky", "Frexs", "Effect"],       placement: 9,  kills: 3,  alive: true  },
  { id: "t-aw",   tag: "AW",   name: "Alliance",       color: "#38bdf8", players: ["Vaifs", "Reptar", "Yuki"],             placement: 10, kills: 4,  alive: true  },
  { id: "t-lg",   tag: "LG",   name: "Luminosity",     color: "#34d399", logo: luminosityLogo, players: ["Knoqd", "Monsoon", "Lou"],             placement: 11, kills: 5,  alive: false },
  { id: "t-vk",   tag: "VK",   name: "Vexed Gaming",   color: "#facc15", players: ["Taisheen", "rynnv", "Bjornfot"],       placement: 12, kills: 4,  alive: false },
  { id: "t-ofg",  tag: "OXG",  name: "Oxygen",         color: "#fca5a5", players: ["Sweetdreams", "Reptar", "rkn"],        placement: 13, kills: 1,  alive: false },
  { id: "t-100t", tag: "100T", name: "100 Thieves",    color: "#fde68a", players: ["Pandxrz", "Senoxe", "Keon"],           placement: 14, kills: 2,  alive: false },
  { id: "t-ssg",  tag: "SSG",  name: "Spacestation",   color: "#22d3ee", players: ["Frexs", "RamBeau", "noiizyy"],         placement: 15, kills: 3,  alive: false },
  { id: "t-dz2",  tag: "ROC",  name: "Rocket",         color: "#f472b6", players: ["Cl0udyy", "Pioneer", "Ulvi"],          placement: 16, kills: 2,  alive: false },
  { id: "t-eg",   tag: "EG",   name: "Evil Geniuses",  color: "#84cc16", players: ["Dropped", "Ras", "Snowy"],             placement: 17, kills: 2,  alive: false },
  { id: "t-aft",  tag: "AFT",  name: "Aftershock",     color: "#c084fc", players: ["LamoBro", "Xera", "Zac"],              placement: 18, kills: 1,  alive: false },
  { id: "t-ssg2", tag: "WTL",  name: "Wettle",         color: "#fb7185", players: ["Wettle", "Garrik", "Pollen"],          placement: 19, kills: 0,  alive: false },
  { id: "t-xyz",  tag: "XYZ",  name: "Crazy Raccoon",  color: "#5eead4", players: ["MatuFps", "Suzaku", "Ryotsu"],         placement: 20, kills: 1,  alive: false },
];

function seedRand(seed: number) {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

export type TrajectoryPoint = { t: number; x: number; y: number };

export function generateTrajectory(seed: number, durationSec: number): TrajectoryPoint[] {
  const rnd = seedRand(seed);
  const points: TrajectoryPoint[] = [];
  let x = 0.15 + rnd() * 0.7;
  let y = 0.15 + rnd() * 0.7;
  let vx = (rnd() - 0.5) * 0.01;
  let vy = (rnd() - 0.5) * 0.01;
  const step = 6;
  for (let t = 0; t <= durationSec; t += step) {
    vx += (rnd() - 0.5) * 0.006;
    vy += (rnd() - 0.5) * 0.006;
    vx = Math.max(-0.012, Math.min(0.012, vx));
    vy = Math.max(-0.012, Math.min(0.012, vy));
    x += vx;
    y += vy;
    const ringPull = 0.0009 * (t / durationSec);
    x += (0.5 - x) * ringPull;
    y += (0.5 - y) * ringPull;
    x = Math.max(0.04, Math.min(0.96, x));
    y = Math.max(0.04, Math.min(0.96, y));
    points.push({ t, x, y });
  }
  return points;
}

export type RingPhase = { startSec: number; endSec: number; closingStartSec?: number; cx: number; cy: number; r: number; source?: "real" | "inherited" };

/**
 * Six concentric ring phases. Each child ring is half the radius of its parent
 * and sits fully inside the parent at a fixed offset (not centered).
 */
const RING_OFFSETS: { fx: number; fy: number }[] = [
  { fx: 0.0,  fy: 0.0  },
  { fx: 0.35, fy: -0.2 },
  { fx: -0.3, fy: 0.25 },
  { fx: 0.2,  fy: 0.3  },
  { fx: -0.25,fy: -0.15},
  { fx: 0.15, fy: 0.1  },
];

function buildRingPhases(): RingPhase[] {
  const PHASE_BOUNDS: [number, number][] = [
    [0,    220 ],
    [220,  480 ],
    [480,  740 ],
    [740,  980 ],
    [980,  1200],
    [1200, 1480],
  ];
  const rings: RingPhase[] = [];
  let cx = 0.5, cy = 0.5, r = 0.46;
  for (let i = 0; i < 6; i++) {
    if (i > 0) {
      const parent = rings[i - 1];
      const off = RING_OFFSETS[i];
      r = parent.r / 2;
      cx = parent.cx + parent.r * off.fx;
      cy = parent.cy + parent.r * off.fy;
    }
    rings.push({ startSec: PHASE_BOUNDS[i][0], endSec: PHASE_BOUNDS[i][1], cx, cy, r });
  }
  return rings;
}

export const ringPhases: RingPhase[] = buildRingPhases();

export type GameEvent = {
  t: number;
  type: "kill" | "knock" | "ring" | "care" | "wipe" | "endgame";
  team?: string;
  /** Stable team id (e.g. "t-test-1") — preferred over `team` (tag) for joins. */
  teamId?: string;
  /** HUD slot (1..20) if event originated from a HUD source. */
  slot?: number;
  label: string;
};

export const events: GameEvent[] = [
  { t: 38,   type: "ring",  label: "Ring 1 closing" },
  { t: 142,  type: "kill",  team: "TSM",  label: "TSM eliminates OXG player" },
  { t: 215,  type: "knock", team: "DZ",   label: "DarkZero knock on C9" },
  { t: 260,  type: "ring",  label: "Ring 2 closing" },
  { t: 388,  type: "wipe",  team: "TSM",  label: "TSM wipes 100T" },
  { t: 510,  type: "care",  label: "Care package dropped" },
  { t: 612,  type: "kill",  team: "SEN",  label: "Sentinels triple kill" },
  { t: 730,  type: "ring",  label: "Ring 3 closing" },
  { t: 845,  type: "wipe",  team: "DZ",   label: "DarkZero wipes FA" },
  { t: 980,  type: "kill",  team: "NRG",  label: "NRG eliminates LG" },
  { t: 1080, type: "ring",  label: "Ring 4 closing" },
  { t: 1210, type: "wipe",    team: "TSM", label: "TSM wipes MV" },
  { t: 1211, type: "endgame",              label: "Game ended" },
];

// ── Override pipeline для реальных данных ────────────────────────────
import {
  testGameRingPhases,
  testGameEvents,
  testGameDurationSec,
  testGameTeams,
  testGameTrajectories,
} from "./test-game-data";
import {
  test2GameRingPhases,
  test2GameEvents,
  test2GameDurationSec,
  test2GameTeams,
  test2GameTrajectories,
} from "./test-game2-data";

export type GameDataOverride = {
  ringPhases?: RingPhase[];
  events?: GameEvent[];
  durationSec?: number;
  teams?: Team[];
  trajectories?: Record<string, { t: number; x: number; y: number }[]>;
};

export const gameDataOverrides: Record<string, GameDataOverride> = {
  "m-test-g1": {
    ringPhases: testGameRingPhases,
    events: testGameEvents,
    durationSec: testGameDurationSec,
    teams: testGameTeams,
    trajectories: testGameTrajectories,
  },
  "m-test2-g1": {
    ringPhases: test2GameRingPhases,
    events: test2GameEvents,
    durationSec: test2GameDurationSec,
    teams: test2GameTeams,
    trajectories: test2GameTrajectories,
  },
};
