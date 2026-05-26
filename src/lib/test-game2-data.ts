/**
 * Гидрация второго Test-матча (m-test2, game 1) — карта Olympus.
 * Источники: src/data/m-test-g2/ (выгрузка track_teams + hud_read для game_5.mp4).
 *
 * Подключаем:
 *  - tracks.json            → траектории (frame_px → нормализуем auto-fit'ом)
 *  - eliminations.json      → длительность матча, alive/dead, placement
 *  - rings.json             → ring phases (геометрии нет — рисуем только события)
 *  - ring_geometry_v2.json  → геометрия колец, если ring_locator её нашёл
 *  - team_tags_raw.json     → slot → tag (locked)
 *  - hud_timeline.json      → fallback для tag по teams[].name
 */
import tracksRaw from "@/data/m-test-g2/tracks.json";
import elimRaw from "@/data/m-test-g2/eliminations.json";
import ringsRaw from "@/data/m-test-g2/rings.json";
import ringsV2Raw from "@/data/m-test-g2/ring_geometry_v2.json";
import teamTagsRaw from "@/data/m-test-g2/team_tags_raw.json";
import hudTimelineRaw from "@/data/m-test-g2/hud_timeline.json";
import type { GameEvent, RingPhase, Team } from "./mock-match";
import { SLOT_COLORS } from "./team-colors";

// ── Types ────────────────────────────────────────────────────────────
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
type ElimTeam = {
  f_first_dead: number | null;
  t_first_dead: number | null;
  f_last_alive: number | null;
  t_last_alive: number | null;
};
type ElimFile = { fps: number; teams: Record<string, ElimTeam> };
type RingPhaseRaw = {
  ring: number;
  countdown_start_f: number | null;
  t_countdown_start: number | null;
  closing_start_f: number | null;
  t_closing_start: number | null;
  closed_f: number | null;
  t_closed: number | null;
};
type RingsFile = { fps: number; phases: RingPhaseRaw[] };
type RingGeomV2Phase = {
  ring: number;
  cx_canon_norm?: number;
  cy_canon_norm?: number;
  r_canon_norm?: number;
  geometry_confidence?: string;
  samples?: number;
};
type RingsV2File = { phases?: RingGeomV2Phase[] };
type TeamTagsFile = {
  slots: Record<string, { locked: string | null }>;
};
type HudTimelineFile = {
  timeline?: Array<{
    teams?: Array<{ slot: number; name: string | null }>;
  }>;
};

const tracks = tracksRaw as unknown as TracksFile;
const elim = elimRaw as unknown as ElimFile;
const rings = ringsRaw as unknown as RingsFile;
const ringsV2 = ringsV2Raw as unknown as RingsV2File;
const teamTags = teamTagsRaw as unknown as TeamTagsFile;
const hudTimeline = hudTimelineRaw as unknown as HudTimelineFile;

// ── Slot → broadcast tag / display name / color ──────────────────────
// Приоритет:
//   1) tracks.meta.teams[].broadcast_tag (+ name, color) — главный источник
//   2) team_tags_raw.json (locked) — fallback
//   3) hud_timeline.json teams[].name — fallback
const metaBySlot = new Map<string, MetaTeam>();
for (const t of tracks.meta.teams ?? []) {
  const slot = t.id.replace(/^slot_/, "");
  metaBySlot.set(slot, t);
}
const slotToTag: Record<string, string> = (() => {
  const out: Record<string, string> = {};
  for (const fr of hudTimeline.timeline ?? []) {
    for (const t of fr.teams ?? []) {
      if (t.slot == null || !t.name) continue;
      const k = String(t.slot);
      if (!out[k]) out[k] = t.name;
    }
  }
  for (const [slot, v] of Object.entries(teamTags.slots ?? {})) {
    if (v?.locked) out[slot] = v.locked;
  }
  for (const [slot, m] of metaBySlot.entries()) {
    const tag = m.broadcast_tag || m.team_tag;
    if (tag) out[slot] = tag;
  }
  return out;
})();

const [CW, CH] = tracks.meta.canonical_size ?? [2048, 2048];

/** Map ROI в кадре 1920×1080 (из scripts/tracking/configs/zones.vod.json,
 *  zone "camera roi" + map_bounds_in_roi):
 *    roi  = (437, 45, 1050, 1030)
 *    map  = roi + (45, 0, 960, 1030) → (482, 45, 960, 1030)
 *  tracks.frame_px — пиксели исходного кадра, нормализуем по этому прямоугольнику. */
const FIT = (() => {
  // Если в данных уже есть canonical_px (CW×CH), используем их.
  const useCanonical = tracks.frames.some((fr) =>
    fr.tracks.some((tr) => tr.canonical_px != null),
  );
  if (useCanonical) {
    return { sx: 1 / CW, sy: 1 / CH, ox: 0, oy: 0, mode: "canonical" as const };
  }
  // Иначе — фиксированный ROI карты в исходном кадре 1920×1080.
  const MAP_X = 482, MAP_Y = 45, MAP_W = 960, MAP_H = 1030;
  return {
    sx: 1 / MAP_W,
    sy: 1 / MAP_H,
    ox: -MAP_X / MAP_W,
    oy: -MAP_Y / MAP_H,
    mode: "frame_roi" as const,
  };
})();

/** Длительность = max t_last_alive из HUD eliminations (full-match покрытие). */
export const test2GameDurationSec: number = Math.ceil(
  Object.values(elim.teams).reduce(
    (m, t) => Math.max(m, t.t_last_alive ?? 0),
    0,
  ) || (tracks.frames.length ? tracks.frames[tracks.frames.length - 1].t : 0),
);

/** Команды: 20 слотов из eliminations.json, теги из slotToTag, цвета по slot. */
export const test2GameTeams: Team[] = (() => {
  const slots = Object.keys(elim.teams).sort((a, b) => Number(a) - Number(b));
  const ranked = [...slots].sort((a, b) => {
    const ta = elim.teams[a].t_first_dead;
    const tb = elim.teams[b].t_first_dead;
    if (ta == null && tb == null) return 0;
    if (ta == null) return -1;
    if (tb == null) return 1;
    return tb - ta;
  });
  const placementBySlot = new Map<string, number>();
  ranked.forEach((slot, i) => placementBySlot.set(slot, i + 1));

  return slots.map((slot) => {
    const meta = metaBySlot.get(slot);
    const tag = slotToTag[slot] ?? `T${slot}`;
    const displayName = meta?.name || tag;
    const color = meta?.color || SLOT_COLORS[Math.max(0, Number(slot) - 1) % SLOT_COLORS.length];
    const dead = elim.teams[slot].t_first_dead != null;
    return {
      id: `t-test2-${slot}`,
      tag,
      name: displayName,
      color,
      players: [],
      placement: placementBySlot.get(slot) ?? Number(slot),
      kills: 0,
      alive: !dead,
    } satisfies Team;
  });
})();

/** Траектории, обрезаются по t_first_dead из HUD. */
export const test2GameTrajectories: Record<string, { t: number; x: number; y: number }[]> = (() => {
  const out: Record<string, { t: number; x: number; y: number }[]> = {};
  const deadAt: Record<string, number> = {};
  for (const [slot, t] of Object.entries(elim.teams)) {
    if (t.t_first_dead != null) deadAt[`slot_${slot}`] = t.t_first_dead;
  }
  for (const fr of tracks.frames) {
    for (const tr of fr.tracks) {
      if (tr.state === "lost" || tr.state === "wiped") continue;
      const dead = deadAt[tr.team_id];
      if (dead != null && fr.t > dead) continue;
      const px = tr.canonical_px ?? tr.world ?? tr.frame_px;
      if (!px) continue;
      const x = px[0] * FIT.sx + FIT.ox;
      const y = px[1] * FIT.sy + FIT.oy;
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      if (x < 0 || x > 1 || y < 0 || y > 1) continue;
      const slotN = tr.team_id.replace(/^slot_/, "");
      (out[`t-test2-${slotN}`] ??= []).push({ t: fr.t, x, y });
    }
  }
  for (const k of Object.keys(out)) out[k].sort((a, b) => a.t - b.t);
  return out;
})();

/** Ring phases. Без реальной геометрии (samples=0) — фазы пропускаем,
 *  на карте кольца не появятся, но события "Ring N closing" пойдут в events. */
export const test2GameRingPhases: RingPhase[] = (() => {
  const closing = (rings.phases ?? [])
    .filter((p) => p.t_closing_start != null)
    .sort((a, b) => a.ring - b.ring);
  if (closing.length === 0) return [];

  const geomByRing = new Map<number, RingGeomV2Phase>();
  for (const g of ringsV2.phases ?? []) {
    if (g.cx_canon_norm != null && g.cy_canon_norm != null && g.r_canon_norm != null
        && (g.samples ?? 0) >= 2) {
      geomByRing.set(g.ring, g);
    }
  }

  const out: RingPhase[] = [];
  let lastReal: { cx: number; cy: number; r: number } | null = null;
  for (let i = 0; i < closing.length; i++) {
    const cur = closing[i];
    const next = closing[i + 1];
    const prev = closing[i - 1];
    const real = geomByRing.get(cur.ring);
    let cx: number, cy: number, r: number;
    let source: "real" | "inherited";
    if (real) {
      cx = real.cx_canon_norm!;
      cy = real.cy_canon_norm!;
      r = real.r_canon_norm!;
      source = "real";
      lastReal = { cx, cy, r };
    } else if (lastReal) {
      cx = lastReal.cx; cy = lastReal.cy; r = lastReal.r;
      source = "inherited";
    } else {
      continue;
    }
    const startSec = cur.t_countdown_start
      ?? (i === 0 ? 0 : (prev?.t_closing_start ?? 0));
    const closingStartSec = cur.t_closing_start ?? undefined;
    const endSec = i === closing.length - 1
      ? test2GameDurationSec
      : (next?.t_countdown_start ?? next?.t_closing_start ?? test2GameDurationSec);
    out.push({ startSec, endSec, closingStartSec, cx, cy, r, source });
  }
  return out;
})();

export const test2GameEvents: GameEvent[] = (() => {
  const out: GameEvent[] = [];
  for (const [slot, t] of Object.entries(elim.teams)) {
    if (t.t_first_dead == null) continue;
    const tag = slotToTag[slot] ?? `T${slot}`;
    out.push({
      t: Math.round(t.t_first_dead),
      type: "wipe",
      team: tag,
      teamId: `t-test2-${slot}`,
      slot: Number(slot),
      label: `${tag} eliminated`,
    });
  }
  for (const p of rings.phases ?? []) {
    if (p.t_closing_start != null && p.ring >= 1) {
      out.push({
        t: Math.round(p.t_closing_start),
        type: "ring",
        label: `Ring ${p.ring} closing`,
      });
    }
  }
  out.push({ t: test2GameDurationSec, type: "endgame", label: "Game ended" });
  return out.sort((a, b) => a.t - b.t);
})();
