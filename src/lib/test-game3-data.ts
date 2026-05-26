/**
 * Test3 (m-test3, game 1) — НИКАКИХ модификаций координат.
 * tracks.frame_px пишутся "как есть" в пиксели исходного кадра 1920×1080
 * и нормализуются ТОЛЬКО по размеру кадра (без ROI/affine/clip).
 * Используется, чтобы сравнить с m-test2-g1 и увидеть исходный сигнал трекера.
 *
 * Источники данных — копия src/data/m-test-g2/ в src/data/m-test-g3/.
 */
import tracksRaw from "@/data/m-test-g3/tracks.json";
import elimRaw from "@/data/m-test-g3/eliminations.json";
import ringsRaw from "@/data/m-test-g3/rings.json";
import ringsV2Raw from "@/data/m-test-g3/ring_geometry_v2.json";
import teamTagsRaw from "@/data/m-test-g3/team_tags_raw.json";
import hudTimelineRaw from "@/data/m-test-g3/hud_timeline.json";
import type { GameEvent, RingPhase, Team } from "./mock-match";
import { SLOT_COLORS } from "./team-colors";

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
  broadcast_tag?: string;
  team_tag?: string;
};
type TracksFile = {
  meta: { canonical_size: [number, number]; teams: MetaTeam[] };
  frames: TrackFrame[];
};
type ElimTeam = { t_first_dead: number | null; t_last_alive: number | null };
type ElimFile = { fps: number; teams: Record<string, ElimTeam> };
type RingPhaseRaw = {
  ring: number;
  t_countdown_start: number | null;
  t_closing_start: number | null;
  t_closed: number | null;
};
type RingsFile = { fps: number; phases: RingPhaseRaw[] };
type RingGeomV2Phase = {
  ring: number;
  cx_canon_norm?: number;
  cy_canon_norm?: number;
  r_canon_norm?: number;
  samples?: number;
};
type RingsV2File = { phases?: RingGeomV2Phase[] };
type TeamTagsFile = { slots: Record<string, { locked: string | null }> };
type HudTimelineFile = {
  timeline?: Array<{ teams?: Array<{ slot: number; name: string | null }> }>;
};

const tracks = tracksRaw as unknown as TracksFile;
const elim = elimRaw as unknown as ElimFile;
const rings = ringsRaw as unknown as RingsFile;
const ringsV2 = ringsV2Raw as unknown as RingsV2File;
const teamTags = teamTagsRaw as unknown as TeamTagsFile;
const hudTimeline = hudTimelineRaw as unknown as HudTimelineFile;

// ── Slot → tag / name / color (как в g2) ─────────────────────────────
const metaBySlot = new Map<string, MetaTeam>();
for (const t of tracks.meta.teams ?? []) {
  metaBySlot.set(t.id.replace(/^slot_/, ""), t);
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

/** RAW режим: нормализуем frame_px ТОЛЬКО по размеру исходного кадра.
 *  Никаких ROI / affine / clip / clamp. Точки могут лежать где угодно в [0..1]. */
const FRAME_W = 1920;
const FRAME_H = 1080;

export const test3GameDurationSec: number = Math.ceil(
  Object.values(elim.teams).reduce((m, t) => Math.max(m, t.t_last_alive ?? 0), 0) ||
    (tracks.frames.length ? tracks.frames[tracks.frames.length - 1].t : 0),
);

export const test3GameTeams: Team[] = (() => {
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
    const color =
      meta?.color || SLOT_COLORS[Math.max(0, Number(slot) - 1) % SLOT_COLORS.length];
    const dead = elim.teams[slot].t_first_dead != null;
    return {
      id: `t-test3-${slot}`,
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

/** Траектории RAW: frame_px / (1920, 1080). Без clip — что есть, то и рисуем. */
export const test3GameTrajectories: Record<
  string,
  { t: number; x: number; y: number }[]
> = (() => {
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
      const px = tr.frame_px;
      if (!px) continue;
      const x = px[0] / FRAME_W;
      const y = px[1] / FRAME_H;
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      const slotN = tr.team_id.replace(/^slot_/, "");
      (out[`t-test3-${slotN}`] ??= []).push({ t: fr.t, x, y });
    }
  }
  for (const k of Object.keys(out)) out[k].sort((a, b) => a.t - b.t);
  return out;
})();

export const test3GameRingPhases: RingPhase[] = (() => {
  const closing = (rings.phases ?? [])
    .filter((p) => p.t_closing_start != null)
    .sort((a, b) => a.ring - b.ring);
  if (closing.length === 0) return [];
  const geomByRing = new Map<number, RingGeomV2Phase>();
  for (const g of ringsV2.phases ?? []) {
    if (
      g.cx_canon_norm != null &&
      g.cy_canon_norm != null &&
      g.r_canon_norm != null &&
      (g.samples ?? 0) >= 2
    ) {
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
      cx = lastReal.cx;
      cy = lastReal.cy;
      r = lastReal.r;
      source = "inherited";
    } else {
      continue;
    }
    const startSec = cur.t_countdown_start ?? (i === 0 ? 0 : prev?.t_closing_start ?? 0);
    const closingStartSec = cur.t_closing_start ?? undefined;
    const endSec =
      i === closing.length - 1
        ? test3GameDurationSec
        : next?.t_countdown_start ?? next?.t_closing_start ?? test3GameDurationSec;
    out.push({ startSec, endSec, closingStartSec, cx, cy, r, source });
  }
  return out;
})();

export const test3GameEvents: GameEvent[] = (() => {
  const out: GameEvent[] = [];
  for (const [slot, t] of Object.entries(elim.teams)) {
    if (t.t_first_dead == null) continue;
    const tag = slotToTag[slot] ?? `T${slot}`;
    out.push({
      t: Math.round(t.t_first_dead),
      type: "wipe",
      team: tag,
      teamId: `t-test3-${slot}`,
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
  out.push({ t: test3GameDurationSec, type: "endgame", label: "Game ended" });
  return out.sort((a, b) => a.t - b.t);
})();