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

/** Auto-fit affine: считаем bbox реальных точек и растягиваем его в [0..1]×[0..1]
 *  с сохранением аспекта (центрируем по короткой оси). Это компенсирует то, что
 *  в этом файле frame_px заполнен не в полном canonical-пространстве, а в
 *  его подобласти (верхняя часть карты). */
const FIT = (() => {
  let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
  for (const fr of tracks.frames) {
    for (const tr of fr.tracks) {
      const px = tr.canonical_px ?? tr.world ?? tr.frame_px;
      if (!px) continue;
      if (px[0] < xmin) xmin = px[0];
      if (px[0] > xmax) xmax = px[0];
      if (px[1] < ymin) ymin = px[1];
      if (px[1] > ymax) ymax = px[1];
    }
  }
  if (!Number.isFinite(xmin)) {
    return { ok: false, sx: 1 / CW, sy: 1 / CH, ox: 0, oy: 0 };
  }
  // 6% паддинг вокруг bbox, чтобы крайние точки не липли к рамке.
  const pad = 0.06;
  const w = xmax - xmin;
  const h = ymax - ymin;
  const px = w * pad, py = h * pad;
  const fx0 = xmin - px, fx1 = xmax + px;
  const fy0 = ymin - py, fy1 = ymax + py;
  // Сохраняем аспект — берём максимальный размер и центрируем по короткой оси.
  const fw = fx1 - fx0, fh = fy1 - fy0;
  const side = Math.max(fw, fh);
  const cx = (fx0 + fx1) / 2, cy = (fy0 + fy1) / 2;
  const sx = 1 / side, sy = 1 / side;
  const ox = 0.5 - cx * sx;
  const oy = 0.5 - cy * sy;
  return { ok: true, sx, sy, ox, oy };
})();

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
      const x = px[0] * FIT.sx + FIT.ox;
      const y = px[1] * FIT.sy + FIT.oy;
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