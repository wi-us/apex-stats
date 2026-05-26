/**
 * Гидрация второго Test-матча (m-test2, game 1) — карта Olympus.
 * Источник: src/data/m-test-g2/tracks.json (выгрузка track_teams для game_5.mp4).
 *
 * В файле заполнено только `frame_px` (нет world/canonical_px), поэтому
 * нормализуем напрямую по canonical_size из meta. Цвета и теги команд
 * берём из meta.teams (broadcast_tag).
 *
 * HUD-данных (eliminations/rings) для этого матча нет — поэтому
 * длительность считаем по последнему кадру треков, кольца пусты,
 * событий — только endgame. Это «голый» трекинг-тест.
 */
import tracksRaw from "@/data/m-test-g2/tracks.json";
import type { GameEvent, RingPhase, Team } from "./mock-match";

type TrackPoint = {
  team_id: string;
  slot_id?: string;
  frame_px: [number, number] | null;
  canonical_px: [number, number] | null;
  world: [number, number] | null;
  state: string;
  confidence: number;
};
type TrackFrame = { t: number; frame: number; tracks: TrackPoint[] };
type MetaTeam = {
  id: string;
  name: string;
  color: string;
  team_id?: string;
  team_tag?: string;
  broadcast_tag?: string;
};
type TracksFile = {
  meta: {
    canonical_size: [number, number];
    world_bounds?: { x: [number, number]; y: [number, number] };
    teams: MetaTeam[];
  };
  frames: TrackFrame[];
};

const tracks = tracksRaw as unknown as TracksFile;

const [CW, CH] = tracks.meta.canonical_size ?? [2048, 2048];

/** Длительность = последний наблюдённый кадр. */
export const test2GameDurationSec: number = Math.ceil(
  tracks.frames.length ? tracks.frames[tracks.frames.length - 1].t : 0,
);

/** Команды из meta. id команды в UI = `t-test2-${N}` по порядку slot_id. */
export const test2GameTeams: Team[] = tracks.meta.teams.map((t, idx) => {
  const slotN = t.id.replace(/^slot_/, "") || String(idx + 1);
  const tag = t.broadcast_tag ?? t.team_tag ?? `T${slotN}`;
  return {
    id: `t-test2-${slotN}`,
    tag,
    name: t.name ?? tag,
    color: t.color || "#888888",
    players: [],
    placement: Number(slotN),
    kills: 0,
    alive: true,
  } satisfies Team;
});

/** Траектории, нормализованные в [0..1] от canonical_size.
 *  В этом файле есть только frame_px → используем его как «канонические» px. */
export const test2GameTrajectories: Record<string, { t: number; x: number; y: number }[]> = (() => {
  const out: Record<string, { t: number; x: number; y: number }[]> = {};
  for (const fr of tracks.frames) {
    for (const tr of fr.tracks) {
      if (tr.state === "lost" || tr.state === "wiped") continue;
      const px = tr.canonical_px ?? tr.world ?? tr.frame_px;
      if (!px) continue;
      const x = px[0] / CW;
      const y = px[1] / CH;
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      if (x < 0 || x > 1 || y < 0 || y > 1) continue;
      const slotN = tr.team_id.replace(/^slot_/, "");
      const key = `t-test2-${slotN}`;
      (out[key] ??= []).push({ t: fr.t, x, y });
    }
  }
  for (const k of Object.keys(out)) out[k].sort((a, b) => a.t - b.t);
  return out;
})();

export const test2GameRingPhases: RingPhase[] = [];

export const test2GameEvents: GameEvent[] = [
  { t: test2GameDurationSec, type: "endgame", label: "Game ended" },
];