import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { teams } from "@/lib/mock-match";
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
import worldsEdgeSample from "@/assets/hsv-samples/worlds-edge.png";
import stormPointSample from "@/assets/hsv-samples/storm-point.png";
import eDistrictSample from "@/assets/hsv-samples/e-district.png";
import olympusSample from "@/assets/hsv-samples/olympus.png";
import { loadAdminSetting, saveAdminSetting } from "@/lib/admin-settings-client";

export const Route = createFileRoute("/admin/hsv")({
  component: HsvAdmin,
  validateSearch: (s: Record<string, unknown>) => ({
    mapId: typeof s.mapId === "string" ? s.mapId : undefined,
  }),
});

type Range3 = [number, number];
type Preset = { h: Range3; s: Range3; v: Range3 };
type PickedColor = { r: number; g: number; b: number; h: number; s: number; v: number };
type LensMode = "normal" | "red" | "white";
type HsvLens = { enabled: boolean; hsv: Preset };
type HsvSettings = {
  version: 1;
  presets: Record<string, Preset>;
  savedColors: Record<string, string>;
  lenses: { red: HsvLens; white: HsvLens };
};

type Frame = { id: string; name: string; image: string };

const DEFAULT_FRAMES: Frame[] = [
  { id: "worlds-edge", name: "World's Edge", image: worldsEdgeSample },
  { id: "storm-point", name: "Storm Point", image: stormPointSample },
  { id: "e-district",  name: "E-District",  image: eDistrictSample },
  { id: "olympus",     name: "Olympus",     image: olympusSample },
];

const HSV_SETTINGS_KEY = "admin-hsv";
const DEFAULT_LENSES: HsvSettings["lenses"] = {
  red: {
    enabled: true,
    hsv: { h: [0, 18], s: [80, 255], v: [70, 255] },
  },
  white: {
    enabled: true,
    hsv: { h: [0, 179], s: [0, 70], v: [150, 255] },
  },
};

function rgbToHsvCv(r: number, g: number, b: number): [number, number, number] {
  const rn = r / 255, gn = g / 255, bn = b / 255;
  const max = Math.max(rn, gn, bn), min = Math.min(rn, gn, bn);
  const d = max - min;
  let h = 0;
  if (d !== 0) {
    if (max === rn) h = ((gn - bn) / d) % 6;
    else if (max === gn) h = (bn - rn) / d + 2;
    else h = (rn - gn) / d + 4;
  }
  h = Math.round((h * 60) / 2);
  if (h < 0) h += 180;
  const s = max === 0 ? 0 : Math.round((d / max) * 255);
  const v = Math.round(max * 255);
  return [h, s, v];
}

function hexToRgb(hex: string): [number, number, number] {
  const c = hex.replace("#", "");
  return [parseInt(c.slice(0, 2), 16), parseInt(c.slice(2, 4), 16), parseInt(c.slice(4, 6), 16)];
}

function presetFromColor(color: string): Preset {
  const [r, g, b] = hexToRgb(color);
  const [h, s, v] = rgbToHsvCv(r, g, b);
  return rangesAround(h, s, v);
}

function rangesAround(h: number, s: number, v: number): Preset {
  return {
    h: [Math.max(0, h - 10), Math.min(179, h + 10)],
    s: [Math.max(0, s - 60), Math.min(255, s + 40)],
    v: [Math.max(40, v - 60), Math.min(255, v + 40)],
  };
}

function hsvCvToRgb(h: number, s: number, v: number): [number, number, number] {
  const hh = (h * 2) / 60;
  const ss = s / 255;
  const vv = v / 255;
  const c = vv * ss;
  const x = c * (1 - Math.abs((hh % 2) - 1));
  const m = vv - c;
  let r = 0, g = 0, b = 0;
  if (hh < 1) [r, g, b] = [c, x, 0];
  else if (hh < 2) [r, g, b] = [x, c, 0];
  else if (hh < 3) [r, g, b] = [0, c, x];
  else if (hh < 4) [r, g, b] = [0, x, c];
  else if (hh < 5) [r, g, b] = [x, 0, c];
  else [r, g, b] = [c, 0, x];
  return [Math.round((r + m) * 255), Math.round((g + m) * 255), Math.round((b + m) * 255)];
}
function presetCenterHex(p: Preset): string {
  const h = Math.round((p.h[0] + p.h[1]) / 2);
  const s = Math.round((p.s[0] + p.s[1]) / 2);
  const v = Math.round((p.v[0] + p.v[1]) / 2);
  const [r, g, b] = hsvCvToRgb(h, s, v);
  return `#${[r, g, b].map((n) => n.toString(16).padStart(2, "0")).join("")}`;
}

function isRange3(value: unknown): value is Range3 {
  return Array.isArray(value) && value.length === 2 && value.every((n) => typeof n === "number" && Number.isFinite(n));
}

function isPreset(value: unknown): value is Preset {
  if (!value || typeof value !== "object") return false;
  const maybe = value as Partial<Preset>;
  return isRange3(maybe.h) && isRange3(maybe.s) && isRange3(maybe.v);
}

function normalizeLens(value: unknown, fallback: HsvLens): HsvLens {
  if (!value || typeof value !== "object") return fallback;
  const raw = value as Partial<HsvLens> & { hShift?: number; sScale?: number; vScale?: number; weight?: number };
  if (isPreset(raw.hsv)) {
    return {
      enabled: typeof raw.enabled === "boolean" ? raw.enabled : fallback.enabled,
      hsv: raw.hsv,
    };
  }

  // Migration path for the previous numeric correction lens format.
  return {
    ...fallback,
    enabled: typeof raw.enabled === "boolean" ? raw.enabled : fallback.enabled,
  };
}

function rangeOverlap(a: Range3, b: Range3): number {
  const lo = Math.max(a[0], b[0]);
  const hi = Math.min(a[1], b[1]);
  return Math.max(0, hi - lo);
}
function rangeWidth(a: Range3): number {
  return Math.max(1, a[1] - a[0]);
}
function intersect(a: Preset, b: Preset): Preset {
  return {
    h: [Math.max(a.h[0], b.h[0]), Math.min(a.h[1], b.h[1])],
    s: [Math.max(a.s[0], b.s[0]), Math.min(a.s[1], b.s[1])],
    v: [Math.max(a.v[0], b.v[0]), Math.min(a.v[1], b.v[1])],
  };
}
/** Volumetric overlap of two HSV cuboids, as % of the smaller one. */
function presetOverlap(a: Preset, b: Preset): number {
  const oh = rangeOverlap(a.h, b.h);
  const os = rangeOverlap(a.s, b.s);
  const ov = rangeOverlap(a.v, b.v);
  if (oh === 0 || os === 0 || ov === 0) return 0;
  const vol = oh * os * ov;
  const va = rangeWidth(a.h) * rangeWidth(a.s) * rangeWidth(a.v);
  const vb = rangeWidth(b.h) * rangeWidth(b.s) * rangeWidth(b.v);
  return Math.round((vol / Math.min(va, vb)) * 100);
}

function hsvInPreset(h: number, s: number, v: number, p: Preset): boolean {
  return h >= p.h[0] && h <= p.h[1] && s >= p.s[0] && s <= p.s[1] && v >= p.v[0] && v <= p.v[1];
}

/** Build a 3D histogram + summed-area-volume of the current frame's HSV pixels.
 *  Bins: H=36 (×5), S=32 (×8), V=32 (×8). Used by Compare tool / Auto-tune
 *  to evaluate any HSV cuboid in O(1). */
const HB = 36, SB = 32, VB = 32;
function buildHsvVolume(src: ImageData): Int32Array {
  const hist = new Int32Array(HB * SB * VB);
  const d = src.data;
  for (let i = 0; i < d.length; i += 4) {
    const [h, s, v] = rgbToHsvCv(d[i], d[i + 1], d[i + 2]);
    const hi = Math.min(HB - 1, (h / 5) | 0);
    const si = Math.min(SB - 1, (s / 8) | 0);
    const vi = Math.min(VB - 1, (v / 8) | 0);
    hist[hi * SB * VB + si * VB + vi]++;
  }
  // 3D prefix-sum (summed area volume).
  const sav = new Int32Array(HB * SB * VB);
  const idx = (h: number, s: number, v: number) => h * SB * VB + s * VB + v;
  for (let h = 0; h < HB; h++)
    for (let s = 0; s < SB; s++)
      for (let v = 0; v < VB; v++) {
        let val = hist[idx(h, s, v)];
        if (h > 0) val += sav[idx(h - 1, s, v)];
        if (s > 0) val += sav[idx(h, s - 1, v)];
        if (v > 0) val += sav[idx(h, s, v - 1)];
        if (h > 0 && s > 0) val -= sav[idx(h - 1, s - 1, v)];
        if (h > 0 && v > 0) val -= sav[idx(h - 1, s, v - 1)];
        if (s > 0 && v > 0) val -= sav[idx(h, s - 1, v - 1)];
        if (h > 0 && s > 0 && v > 0) val += sav[idx(h - 1, s - 1, v - 1)];
        sav[idx(h, s, v)] = val;
      }
  return sav;
}
function querySAV(sav: Int32Array, p: Preset): number {
  if (p.h[0] > p.h[1] || p.s[0] > p.s[1] || p.v[0] > p.v[1]) return 0;
  // Convert HSV ranges (inclusive) to bin indices.
  const h0 = Math.max(0, Math.min(HB - 1, (p.h[0] / 5) | 0));
  const h1 = Math.max(0, Math.min(HB - 1, (p.h[1] / 5) | 0));
  const s0 = Math.max(0, Math.min(SB - 1, (p.s[0] / 8) | 0));
  const s1 = Math.max(0, Math.min(SB - 1, (p.s[1] / 8) | 0));
  const v0 = Math.max(0, Math.min(VB - 1, (p.v[0] / 8) | 0));
  const v1 = Math.max(0, Math.min(VB - 1, (p.v[1] / 8) | 0));
  const idx = (h: number, s: number, v: number) => h * SB * VB + s * VB + v;
  const at = (h: number, s: number, v: number) =>
    h < 0 || s < 0 || v < 0 ? 0 : sav[idx(h, s, v)];
  return (
    at(h1, s1, v1) -
    at(h0 - 1, s1, v1) - at(h1, s0 - 1, v1) - at(h1, s1, v0 - 1) +
    at(h0 - 1, s0 - 1, v1) + at(h0 - 1, s1, v0 - 1) + at(h1, s0 - 1, v0 - 1) -
    at(h0 - 1, s0 - 1, v0 - 1)
  );
}
/** Brute-force search over HSV cuboids around `seed`, maximizing
 *  (own_pixels - λ * overlap_with_other_team). */
function autoTunePreset(
  sav: Int32Array,
  seed: Preset,
  rivalSav: Int32Array | null,
  lambda = 2.0,
): { best: Preset; ownPx: number; rivalPx: number; tested: number } {
  // Seed center.
  const hC = (seed.h[0] + seed.h[1]) / 2;
  const sC = (seed.s[0] + seed.s[1]) / 2;
  const vC = (seed.v[0] + seed.v[1]) / 2;
  const hWs = [4, 6, 8, 10, 12];
  const sWs = [40, 60, 80, 100];
  const vWs = [40, 60, 80, 100];
  const hOffsets = [-6, -3, 0, 3, 6];
  const sOffsets = [-30, 0, 30];
  const vOffsets = [-30, 0, 30];
  let best: Preset = seed;
  let bestScore = -Infinity;
  let bestOwn = 0, bestRival = 0, tested = 0;
  for (const hW of hWs) for (const hO of hOffsets)
  for (const sW of sWs) for (const sO of sOffsets)
  for (const vW of vWs) for (const vO of vOffsets) {
    const p: Preset = {
      h: [Math.max(0, Math.round(hC + hO - hW)), Math.min(179, Math.round(hC + hO + hW))],
      s: [Math.max(0, Math.round(sC + sO - sW)), Math.min(255, Math.round(sC + sO + sW))],
      v: [Math.max(40, Math.round(vC + vO - vW)), Math.min(255, Math.round(vC + vO + vW))],
    };
    const own = querySAV(sav, p);
    if (own < 20) continue; // too narrow / dead
    const rival = rivalSav ? querySAV(rivalSav, p) : 0;
    const score = own - lambda * rival;
    tested++;
    if (score > bestScore) {
      bestScore = score; best = p; bestOwn = own; bestRival = rival;
    }
  }
  return { best, ownPx: bestOwn, rivalPx: bestRival, tested };
}

/** Render an HSV mask of `p` onto canvas `dst`, using `tint` for matched pixels. */
function renderMask(
  src: ImageData,
  dst: HTMLCanvasElement,
  p: Preset,
  tint: [number, number, number],
): number {
  const ctx = dst.getContext("2d")!;
  const out = ctx.createImageData(dst.width, dst.height);
  let detected = 0;
  for (let i = 0; i < src.data.length; i += 4) {
    const [h, s, v] = rgbToHsvCv(src.data[i], src.data[i + 1], src.data[i + 2]);
    const ok = h >= p.h[0] && h <= p.h[1] && s >= p.s[0] && s <= p.s[1] && v >= p.v[0] && v <= p.v[1];
    if (ok) {
      out.data[i] = tint[0]; out.data[i + 1] = tint[1]; out.data[i + 2] = tint[2];
      detected++;
    } else {
      out.data[i] = 12; out.data[i + 1] = 12; out.data[i + 2] = 12;
    }
    out.data[i + 3] = 255;
  }
  ctx.putImageData(out, 0, 0);
  return detected;
}

function HsvAdmin() {
  const teamList = useMemo(
    () => teams.map((t, i) => ({ ...t, displayName: `Team ${i + 1}` })),
    [],
  );

  // Presets are stored per (team, frame) so each map keeps its own calibration.
  const presetKey = (tid: string, fid: string) => `${tid}|${fid}`;
  const buildDefaultPresets = () => {
    const init: Record<string, Preset> = {};
    for (const t of teams) for (const f of DEFAULT_FRAMES) init[presetKey(t.id, f.id)] = presetFromColor(t.color);
    return init;
  };
  const buildDefaultSavedColors = () => {
    const init: Record<string, string> = {};
    for (const t of teams) for (const f of DEFAULT_FRAMES) init[presetKey(t.id, f.id)] = t.color;
    return init;
  };
  const [presets, setPresets] = useState<Record<string, Preset>>(buildDefaultPresets);
  const [savedColors, setSavedColors] = useState<Record<string, string>>(buildDefaultSavedColors);
  const [lenses, setLenses] = useState<HsvSettings["lenses"]>(DEFAULT_LENSES);
  const [activeLens, setActiveLens] = useState<LensMode>("normal");
  const [settingsStatus, setSettingsStatus] = useState("loading settings...");

  const [frames, setFrames] = useState<Frame[]>(DEFAULT_FRAMES);
  const [frameId, setFrameId] = useState<string>(DEFAULT_FRAMES[0].id);
  const [teamId, setTeamId] = useState(teamList[0].id);
  const [history, setHistory] = useState<PickedColor[]>([]);
  const [lastPick, setLastPick] = useState<PickedColor | null>(null);
  const [compareAll, setCompareAll] = useState(false);
  const [showDevSnippet, setShowDevSnippet] = useState(false);
  const [maskStats, setMaskStats] = useState<{ detected: number; total: number; overlapPct: number }>({
    detected: 0, total: 1, overlapPct: 0,
  });

  type PendingImport = {
    sourceFrame: string;
    rows: Array<{ id?: string; slot?: number; hex?: string; h?: Range3; s?: Range3; v?: Range3 }>;
  };
  const [pendingImport, setPendingImport] = useState<PendingImport | null>(null);

  useEffect(() => {
    let cancelled = false;
    const defaults: HsvSettings = {
      version: 1,
      presets: buildDefaultPresets(),
      savedColors: buildDefaultSavedColors(),
      lenses: DEFAULT_LENSES,
    };
    loadAdminSetting<HsvSettings>(HSV_SETTINGS_KEY)
      .then((saved) => {
        if (cancelled) return;
        if (saved?.version === 1) {
          setPresets({ ...defaults.presets, ...(saved.presets ?? {}) });
          setSavedColors({ ...defaults.savedColors, ...(saved.savedColors ?? {}) });
          setLenses({
            red: normalizeLens(saved.lenses?.red, DEFAULT_LENSES.red),
            white: normalizeLens(saved.lenses?.white, DEFAULT_LENSES.white),
          });
          setSettingsStatus("loaded from server");
          return;
        }
        void saveAdminSetting(HSV_SETTINGS_KEY, defaults);
        setSettingsStatus("default profile saved");
      })
      .catch((error) => {
        if (!cancelled) setSettingsStatus(`settings unavailable: ${(error as Error).message}`);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const team = teamList.find((t) => t.id === teamId)!;
  const frame = frames.find((f) => f.id === frameId) ?? frames[0];
  const k = presetKey(teamId, frame.id);
  const preset = presets[k] ?? presetFromColor(team.color);
  const teamSwatch = (id: string) =>
    savedColors[presetKey(id, frame.id)] ?? teamList.find((t) => t.id === id)!.color;

  const setPreset = (p: Partial<Preset>) =>
    setPresets((prev) => ({ ...prev, [k]: { ...(prev[k] ?? preset), ...p } }));

  const saveHsvSettings = async () => {
    const nextColors = { ...savedColors, [k]: presetCenterHex(preset) };
    const payload: HsvSettings = {
      version: 1,
      presets,
      savedColors: nextColors,
      lenses,
    };
    setSavedColors(nextColors);
    setSettingsStatus("saving...");
    try {
      await saveAdminSetting(HSV_SETTINGS_KEY, payload);
      setSettingsStatus("saved to server");
    } catch (error) {
      setSettingsStatus(`save failed: ${(error as Error).message}`);
    }
  };

  const updateLens = (name: "red" | "white", patch: Partial<HsvLens>) =>
    setLenses((prev) => ({ ...prev, [name]: { ...prev[name], ...patch } }));

  // Compute conflicts vs other teams.
  const conflicts = useMemo(() => {
    return teamList
      .filter((t) => t.id !== teamId)
      .map((t) => ({ team: t, pct: presetOverlap(preset, presets[presetKey(t.id, frame.id)] ?? presetFromColor(t.color)) }))
      .filter((c) => c.pct >= 5)
      .sort((a, b) => b.pct - a.pct);
  }, [presets, preset, teamList, teamId, frame.id]);

  // Sample canvas (full resolution offscreen) for eyedropper sampling
  const sampleCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const previewRef = useRef<HTMLCanvasElement>(null);
  const maskRef = useRef<HTMLCanvasElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const importInputRef = useRef<HTMLInputElement>(null);
  const [imgReady, setImgReady] = useState(false);

  // ---- Compare-colors tool state ----
  const [compareOpen, setCompareOpen] = useState(false);
  const otherCandidates = useMemo(() => teamList.filter((t) => t.id !== teamId), [teamList, teamId]);
  const [otherId, setOtherId] = useState<string>(otherCandidates[0]?.id ?? "");
  useEffect(() => {
    if (!otherCandidates.some((t) => t.id === otherId)) setOtherId(otherCandidates[0]?.id ?? "");
  }, [otherCandidates, otherId]);
  const compareARef = useRef<HTMLCanvasElement>(null);
  const compareBRef = useRef<HTMLCanvasElement>(null);
  const compareOverlapRef = useRef<HTMLCanvasElement>(null);
  const [compareStats, setCompareStats] = useState<{ a: number; b: number; overlap: number }>({ a: 0, b: 0, overlap: 0 });
  const [savCache, setSavCache] = useState<{ frameId: string; sav: Int32Array; src: ImageData } | null>(null);
  const [tuning, setTuning] = useState(false);
  const [tuneReport, setTuneReport] = useState<string>("");

  // Build SAV once per frame (used by Compare & Auto-tune).
  useEffect(() => {
    if (!imgReady) return;
    const off = sampleCanvasRef.current; if (!off) return;
    const ctx = off.getContext("2d")!;
    const src = ctx.getImageData(0, 0, off.width, off.height);
    setSavCache({ frameId: frame.id, sav: buildHsvVolume(src), src });
  }, [imgReady, frame.id]);

  // Re-render comparison panel when inputs change.
  useEffect(() => {
    if (!compareOpen || !savCache || !otherId) return;
    const other = teamList.find((t) => t.id === otherId);
    if (!other) return;
    const pA = preset;
    const pB = presets[presetKey(other.id, frame.id)] ?? presetFromColor(other.color);
    const cA = compareARef.current, cB = compareBRef.current, cO = compareOverlapRef.current;
    const off = sampleCanvasRef.current;
    if (!cA || !cB || !cO || !off) return;
    [cA, cB, cO].forEach((c) => { c.width = off.width; c.height = off.height; });
    const [ra, ga, ba] = hexToRgb(teamSwatch(team.id));
    const [rb, gb, bb] = hexToRgb(teamSwatch(other.id));
    const aPx = renderMask(savCache.src, cA, pA, [ra, ga, ba]);
    const bPx = renderMask(savCache.src, cB, pB, [rb, gb, bb]);
    // overlap mask: pixels matched by BOTH
    const ctx = cO.getContext("2d")!;
    const out = ctx.createImageData(cO.width, cO.height);
    let overlap = 0;
    const src = savCache.src;
    for (let i = 0; i < src.data.length; i += 4) {
      const [h, s, v] = rgbToHsvCv(src.data[i], src.data[i + 1], src.data[i + 2]);
      const inA = h >= pA.h[0] && h <= pA.h[1] && s >= pA.s[0] && s <= pA.s[1] && v >= pA.v[0] && v <= pA.v[1];
      const inB = h >= pB.h[0] && h <= pB.h[1] && s >= pB.s[0] && s <= pB.s[1] && v >= pB.v[0] && v <= pB.v[1];
      if (inA && inB) { out.data[i] = 240; out.data[i + 1] = 80; out.data[i + 2] = 80; overlap++; }
      else if (inA)    { out.data[i] = ra;  out.data[i + 1] = ga;  out.data[i + 2] = ba; }
      else if (inB)    { out.data[i] = rb;  out.data[i + 1] = gb;  out.data[i + 2] = bb; }
      else             { out.data[i] = 12;  out.data[i + 1] = 12;  out.data[i + 2] = 12; }
      out.data[i + 3] = 255;
    }
    ctx.putImageData(out, 0, 0);
    setCompareStats({ a: aPx, b: bPx, overlap });
  }, [compareOpen, savCache, otherId, presets, preset, frame.id, teamId, teamList, savedColors]);

  const runAutoTune = () => {
    if (!savCache) return;
    setTuning(true);
    setTuneReport("Scanning HSV cuboids…");
    // Build rival SAV by accumulating all OTHER teams' counts into one combined SAV
    // → cheap "any-other-team" overlap proxy. We just sum per-team querySAV at scoring time.
    // For O(1) per candidate, we instead compute rivalSav by re-using main sav with a mask:
    // since per-team masks are disjoint cuboids in HSV space, sum of querySAV over other
    // teams equals the count of image pixels matched by ANY other team — but those teams
    // may overlap, so it's a (conservative) upper bound, which is what we want to minimize.
    setTimeout(() => {
      const seed = preset;
      // Score against ALL other teams as rivals — combine cuboids by querying each.
      const rivals = teamList.filter((t) => t.id !== teamId).map((t) =>
        presets[presetKey(t.id, frame.id)] ?? presetFromColor(t.color));
      const sav = savCache.sav;
      // Custom search that scores per-candidate against all rivals.
      const hC = (seed.h[0] + seed.h[1]) / 2;
      const sC = (seed.s[0] + seed.s[1]) / 2;
      const vC = (seed.v[0] + seed.v[1]) / 2;
      const hWs = [4, 6, 8, 10];
      const sWs = [40, 60, 80];
      const vWs = [40, 60, 80];
      const hOff = [-6, -3, 0, 3, 6];
      const sOff = [-30, 0, 30];
      const vOff = [-30, 0, 30];
      let bestScore = -Infinity, best: Preset = seed, bestOwn = 0, bestRival = 0, tested = 0;
      for (const hW of hWs) for (const hO of hOff)
      for (const sW of sWs) for (const sO of sOff)
      for (const vW of vWs) for (const vO of vOff) {
        const p: Preset = {
          h: [Math.max(0, Math.round(hC + hO - hW)), Math.min(179, Math.round(hC + hO + hW))],
          s: [Math.max(0, Math.round(sC + sO - sW)), Math.min(255, Math.round(sC + sO + sW))],
          v: [Math.max(40, Math.round(vC + vO - vW)), Math.min(255, Math.round(vC + vO + vW))],
        };
        const own = querySAV(sav, p);
        if (own < 20) continue;
        let rival = 0;
        for (const r of rivals) rival += querySAV(sav, r) > 0 ? querySAV(sav, intersect(p, r)) : 0;
        const score = own - 3 * rival;
        tested++;
        if (score > bestScore) { bestScore = score; best = p; bestOwn = own; bestRival = rival; }
      }
      setPresets((prev) => ({ ...prev, [k]: best }));
      setTuneReport(
        `Tested ${tested.toLocaleString()} cuboids · own=${bestOwn} px · rival overlap=${bestRival} px ` +
        `· H[${best.h[0]}–${best.h[1]}] S[${best.s[0]}–${best.s[1]}] V[${best.v[0]}–${best.v[1]}]`,
      );
      setTuning(false);
    }, 0);
  };

  useEffect(() => {
    setImgReady(false);
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.src = frame.image;
    img.onload = () => {
      const W = 640;
      const H = Math.round((img.height / img.width) * W);
      const off = document.createElement("canvas");
      off.width = W; off.height = H;
      const ctx = off.getContext("2d")!;
      ctx.drawImage(img, 0, 0, W, H);
      sampleCanvasRef.current = off;
      const pv = previewRef.current;
      if (pv) {
        pv.width = W; pv.height = H;
        pv.getContext("2d")!.drawImage(off, 0, 0);
      }
      const mk = maskRef.current;
      if (mk) { mk.width = W; mk.height = H; }
      setImgReady(true);
    };
  }, [frame.image]);

  // Recompute mask whenever preset / image / compare mode changes
  useEffect(() => {
    if (!imgReady) return;
    const off = sampleCanvasRef.current;
    const mk = maskRef.current;
    if (!off || !mk) return;
    const ctx = off.getContext("2d")!;
    const mctx = mk.getContext("2d")!;
    const { width: W, height: H } = off;
    const src = ctx.getImageData(0, 0, W, H);
    const out = mctx.createImageData(W, H);

    let detected = 0;
    let overlapPixels = 0;
    const total = W * H;

    if (activeLens !== "normal") {
      const lens = lenses[activeLens];
      const tint: [number, number, number] = activeLens === "red" ? [255, 91, 45] : [210, 238, 255];
      for (let i = 0; i < src.data.length; i += 4) {
        const [h, s, v] = rgbToHsvCv(src.data[i], src.data[i + 1], src.data[i + 2]);
        const ok = lens.enabled && hsvInPreset(h, s, v, lens.hsv);
        if (ok) detected++;
        if (ok) {
          out.data[i] = tint[0]; out.data[i + 1] = tint[1]; out.data[i + 2] = tint[2];
        } else {
          out.data[i] = 12; out.data[i + 1] = 12; out.data[i + 2] = 12;
        }
        out.data[i + 3] = 255;
      }
    } else if (!compareAll) {
      const [hL, hU] = preset.h, [sL, sU] = preset.s, [vL, vU] = preset.v;
      const others = teamList
        .filter((t) => t.id !== teamId)
        .map((t) => ({ p: presets[presetKey(t.id, frame.id)] ?? presetFromColor(t.color), c: teamSwatch(t.id) }));
      for (let i = 0; i < src.data.length; i += 4) {
        const [h, s, v] = rgbToHsvCv(src.data[i], src.data[i + 1], src.data[i + 2]);
        const ok = h >= hL && h <= hU && s >= sL && s <= sU && v >= vL && v <= vU;
        let conflict = false;
        if (ok) {
          detected++;
          for (const o of others) {
            if (h >= o.p.h[0] && h <= o.p.h[1] && s >= o.p.s[0] && s <= o.p.s[1] && v >= o.p.v[0] && v <= o.p.v[1]) {
              conflict = true; break;
            }
          }
          if (conflict) overlapPixels++;
        }
        if (ok && conflict) {
          out.data[i] = 240; out.data[i + 1] = 80; out.data[i + 2] = 80;
        } else if (ok) {
          out.data[i] = 255; out.data[i + 1] = 255; out.data[i + 2] = 255;
        } else {
          out.data[i] = 12; out.data[i + 1] = 12; out.data[i + 2] = 12;
        }
        out.data[i + 3] = 255;
      }
    } else {
      // colorize each pixel by first matching team
      const all = teamList.map((t) => ({
        id: t.id,
        p: presets[presetKey(t.id, frame.id)] ?? presetFromColor(t.color),
        c: teamSwatch(t.id),
      }));
      const myIdx = all.findIndex((a) => a.id === teamId);
      for (let i = 0; i < src.data.length; i += 4) {
        const [h, s, v] = rgbToHsvCv(src.data[i], src.data[i + 1], src.data[i + 2]);
        let matched = -1;
        for (let k = 0; k < all.length; k++) {
          const a = all[k];
          if (h >= a.p.h[0] && h <= a.p.h[1] && s >= a.p.s[0] && s <= a.p.s[1] && v >= a.p.v[0] && v <= a.p.v[1]) {
            matched = k; break;
          }
        }
        if (matched < 0) {
          out.data[i] = 12; out.data[i + 1] = 12; out.data[i + 2] = 12;
        } else {
          const [cr, cg, cb] = hexToRgb(all[matched].c);
          out.data[i] = cr; out.data[i + 1] = cg; out.data[i + 2] = cb;
          if (matched === myIdx) detected++;
        }
        out.data[i + 3] = 255;
      }
    }
    mctx.putImageData(out, 0, 0);
    setMaskStats({ detected, total, overlapPct: detected > 0 ? Math.round((overlapPixels / detected) * 100) : 0 });
  }, [preset, presets, imgReady, compareAll, teamId, teamList, frame.id, savedColors, activeLens, lenses]);

  const onPreviewClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const off = sampleCanvasRef.current;
    const pv = previewRef.current;
    if (!off || !pv) return;
    const rect = pv.getBoundingClientRect();
    const x = Math.floor(((e.clientX - rect.left) / rect.width) * off.width);
    const y = Math.floor(((e.clientY - rect.top) / rect.height) * off.height);
    const d = off.getContext("2d")!.getImageData(x, y, 1, 1).data;
    const r = d[0], g = d[1], b = d[2];
    const [h, s, v] = rgbToHsvCv(r, g, b);
    const pick: PickedColor = { r, g, b, h, s, v };
    setLastPick(pick);
    setHistory((prev) => [pick, ...prev.filter((p) => !(p.r === r && p.g === g && p.b === b))].slice(0, 5));
    setPreset(rangesAround(h, s, v));
  };

  const onUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const url = URL.createObjectURL(file);
    const id = `upload-${Date.now()}`;
    setFrames((prev) => [...prev, { id, name: `Upload · ${file.name.slice(0, 14)}`, image: url }]);
    setPresets((prev) => {
      const next = { ...prev };
      for (const t of teams) next[presetKey(t.id, id)] = presetFromColor(t.color);
      return next;
    });
    setSavedColors((prev) => {
      const next = { ...prev };
      for (const t of teams) next[presetKey(t.id, id)] = t.color;
      return next;
    });
    setFrameId(id);
    e.target.value = "";
  };

  // Mask quality heuristic
  const detectedPct = (maskStats.detected / maskStats.total) * 100;
  let status: { label: string; tone: "good" | "warn" | "bad" } = { label: "good", tone: "good" };
  let noise: "low" | "medium" | "high" = "low";
  if (detectedPct < 0.1) { status = { label: "too narrow", tone: "warn" }; noise = "low"; }
  else if (detectedPct > 12) { status = { label: "too wide", tone: "bad" }; noise = "high"; }
  else if (detectedPct > 6) { status = { label: "noisy", tone: "warn" }; noise = "medium"; }
  if (activeLens === "normal" && maskStats.overlapPct > 25) status = { label: "conflicts", tone: "bad" };

  const activeLensLabel =
    activeLens === "normal" ? "Normal team HSV" : activeLens === "red" ? "Red filter lens" : "White filter lens";
  const activeLensTone = activeLens === "red" ? "text-primary" : activeLens === "white" ? "text-sky-300" : "text-muted-foreground";

  const applyImport = (rows: PendingImport["rows"], targetFrame: string, switchTo: boolean) => {
    const nextPresets: Record<string, Preset> = { ...presets };
    const nextColors: Record<string, string> = { ...savedColors };
    let n = 0;
    rows.forEach((row, i) => {
      const tid = row.id ?? teamList[(row.slot ?? i + 1) - 1]?.id;
      if (!tid) return;
      if (row.h && row.s && row.v) {
        nextPresets[presetKey(tid, targetFrame)] = { h: row.h, s: row.s, v: row.v };
      }
      if (row.hex) nextColors[presetKey(tid, targetFrame)] = row.hex;
      n++;
    });
    setPresets(nextPresets);
    setSavedColors(nextColors);
    if (switchTo && frames.some((fr) => fr.id === targetFrame)) setFrameId(targetFrame);
    setPendingImport(null);
    alert(`Imported ${n} team preset${n === 1 ? "" : "s"} for frame "${targetFrame}"`);
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center gap-6 border-b border-border bg-surface px-6">
        <h1 className="text-sm font-bold uppercase tracking-wider">HSV — Team Color Calibration</h1>
        <div className="flex items-center gap-1">
          <span className="label-eyebrow mr-2 text-xs">Sample</span>
          {frames.map((s) => (
            <button key={s.id} onClick={() => setFrameId(s.id)}
              className={`rounded-sm border px-2.5 py-1 text-xs font-semibold uppercase tracking-wider transition-colors ${
                s.id === frameId ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-surface-2 text-muted-foreground hover:bg-muted"}`}>
              {s.name}
            </button>
          ))}
          <button
            onClick={() => fileInputRef.current?.click()}
            className="ml-1 rounded-sm border border-dashed border-border bg-surface-2 px-2.5 py-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:bg-muted">
            + Upload sample
          </button>
          <input ref={fileInputRef} type="file" accept="image/*" hidden onChange={onUpload} />
        </div>
        <div className="ml-auto text-mono text-xs uppercase text-muted-foreground">{settingsStatus}</div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Team list */}
        <aside className="w-[260px] shrink-0 overflow-y-auto border-r border-border bg-surface p-2">
          {teamList.map((t, i) => {
            const active = t.id === teamId;
            return (
              <button key={t.id} onClick={() => setTeamId(t.id)}
                className={`mb-1 flex w-full items-center gap-2 rounded-sm border px-2 text-left transition-colors ${
                  active
                    ? "border-primary/50 bg-primary/10 py-2.5"
                    : "border-transparent py-1.5 hover:bg-muted"
                }`}>
                <span className={`shrink-0 rounded-sm ring-1 ring-border ${active ? "h-6 w-6" : "h-3 w-3"}`} style={{ backgroundColor: teamSwatch(t.id) }} />
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className={`font-semibold ${active ? "text-sm" : "text-xs"}`}>{t.displayName}</span>
                  {active && <span className="text-mono text-xs uppercase text-muted-foreground">{teamSwatch(t.id)}</span>}
                </div>
                <span className="text-mono ml-auto text-xs text-muted-foreground">{String(i + 1).padStart(2, "0")}</span>
              </button>
            );
          })}
        </aside>

        {/* Editor */}
        <div className="flex min-w-0 flex-1 flex-col overflow-auto p-6">
          <div className="mb-4 flex items-center gap-3">
            <span className="h-7 w-7 rounded-sm ring-1 ring-border" style={{ backgroundColor: teamSwatch(team.id) }} />
            <h2 className="text-lg font-bold">{team.displayName}</h2>
            <span className="text-mono text-xs text-muted-foreground">preset</span>

            <div className="ml-auto inline-flex rounded-sm border border-border bg-surface-2 p-0.5">
              <button onClick={() => setCompareAll(false)}
                className={`rounded-sm px-2.5 py-1 text-xs font-semibold uppercase tracking-wider ${
                  !compareAll ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}>
                Show only {team.displayName}
              </button>
              <button onClick={() => setCompareAll(true)}
                className={`rounded-sm px-2.5 py-1 text-xs font-semibold uppercase tracking-wider ${
                  compareAll ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}>
                Show all teams mask
              </button>
            </div>
          </div>

          {/* Two previews */}
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <div className="hud-panel p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="label-eyebrow text-xs">Sample frame — click to pick color</span>
                <span className="text-mono text-xs text-muted-foreground">{frame.name}</span>
              </div>
              <div className="relative w-full overflow-hidden rounded-sm border border-border bg-background">
                <canvas ref={previewRef} onClick={onPreviewClick} className="block w-full cursor-crosshair" />
              </div>

              {/* Picked color + history */}
              <div className="mt-3 flex items-stretch gap-3">
                <div className="flex min-w-0 flex-1 items-center gap-3 rounded-sm border border-border bg-surface-2 p-2.5">
                  <div className="h-12 w-12 shrink-0 rounded-sm ring-1 ring-border"
                       style={{ backgroundColor: lastPick ? `rgb(${lastPick.r},${lastPick.g},${lastPick.b})` : "transparent" }} />
                  <div className="min-w-0 flex-1">
                    <div className="label-eyebrow mb-1 text-xs">Picked pixel</div>
                    {lastPick ? (
                      <div className="text-mono text-xs leading-snug tabular-nums">
                        <div>H: {lastPick.h} / S: {lastPick.s} / V: {lastPick.v}</div>
                        <div className="text-muted-foreground">RGB: {lastPick.r}, {lastPick.g}, {lastPick.b}</div>
                      </div>
                    ) : (
                      <div className="text-xs text-muted-foreground">Click anywhere on the frame…</div>
                    )}
                  </div>
                </div>
                <div className="flex flex-col rounded-sm border border-border bg-surface-2 p-2.5">
                  <div className="label-eyebrow mb-1.5 text-xs">Last 5</div>
                  <div className="flex gap-1.5">
                    {Array.from({ length: 5 }).map((_, i) => {
                      const p = history[i];
                      return (
                        <button key={i}
                          onClick={() => p && (setLastPick(p), setPreset(rangesAround(p.h, p.s, p.v)))}
                          title={p ? `H${p.h} S${p.s} V${p.v}` : "—"}
                          className="h-7 w-7 rounded-sm border border-border"
                          style={{ backgroundColor: p ? `rgb(${p.r},${p.g},${p.b})` : "transparent" }} />
                      );
                    })}
                  </div>
                </div>
              </div>
            </div>

            <div className="hud-panel p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="label-eyebrow text-xs">
                  {activeLens === "normal" ? (compareAll ? "All teams mask" : "Binary HSV mask") : `${activeLensLabel} mask`}
                </span>
                <span className={`text-mono text-xs ${activeLensTone}`}>live</span>
              </div>
              <div className="relative w-full overflow-hidden rounded-sm border border-border bg-background">
                <canvas ref={maskRef} className="block w-full" />
              </div>

              {/* Quality score */}
              <div className="mt-3 grid grid-cols-4 gap-2">
                <Stat label="Detected" value={`${maskStats.detected.toLocaleString()} px`} sub={`${detectedPct.toFixed(2)}%`} />
                <Stat label="Noise" value={noise} />
                <Stat label="Overlap" value={activeLens === "normal" ? `${maskStats.overlapPct}%` : "n/a"} sub={activeLens === "normal" && !compareAll ? "(red pixels)" : ""} />
                <Stat label="Status" value={status.label} tone={status.tone} />
              </div>
            </div>
          </div>

          {/* Conflict warning */}
          {conflicts.length > 0 && (
            <div className="hud-panel mt-4 border-l-4 border-l-warning p-3">
              <div className="label-eyebrow mb-2 text-xs text-warning">Conflict warning</div>
              <div className="flex flex-wrap gap-2">
                {conflicts.slice(0, 6).map((c) => (
                  <div key={c.team.id} className="inline-flex items-center gap-2 rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs">
                    <span className="h-3 w-3 rounded-sm ring-1 ring-border" style={{ backgroundColor: teamSwatch(c.team.id) }} />
                    <span className="font-semibold">{c.team.displayName}</span>
                    <span className={`text-mono tabular-nums ${c.pct >= 30 ? "text-destructive" : "text-warning"}`}>{c.pct}%</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Controls */}
          <div className="hud-panel mt-4 p-4">
            <div className="label-eyebrow mb-3">HSV range</div>
            <Range label="Hue" min={0} max={179} value={preset.h} onChange={(h) => setPreset({ h: h as Range3 })} />
            <Range label="Saturation" min={0} max={255} value={preset.s} onChange={(s) => setPreset({ s: s as Range3 })} />
            <Range label="Value" min={0} max={255} value={preset.v} onChange={(v) => setPreset({ v: v as Range3 })} />

            <div className="mt-5 border-t border-border pt-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="label-eyebrow text-xs">Ring filter lenses</div>
                  <div className="text-xs text-muted-foreground">
                    Switch the live preview between the team HSV mask and separate red / white filter masks.
                  </div>
                </div>
                <div className="inline-flex rounded-sm border border-border bg-background p-0.5">
                  {(["normal", "red", "white"] as const).map((name) => (
                    <button
                      key={name}
                      onClick={() => setActiveLens(name)}
                      className={`rounded-sm px-2.5 py-1 text-xs font-semibold uppercase tracking-wider ${
                        activeLens === name ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {name === "normal" ? "Normal" : name === "red" ? "Red filter" : "White filter"}
                    </button>
                  ))}
                </div>
              </div>

              {activeLens === "normal" ? (
                <div className="rounded-sm border border-border bg-surface-2 p-3 text-xs text-muted-foreground">
                  Normal preview uses the selected team's HSV range above. Red and white filter lenses have their own HSV ranges and are saved with the same server profile.
                </div>
              ) : (
                <LensRangeControl
                  mode={activeLens}
                  lens={lenses[activeLens]}
                  onChange={(patch) => updateLens(activeLens, patch)}
                />
              )}
            </div>

            <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
              <button
                onClick={() => setPresets((p) => ({ ...p, [k]: presetFromColor(team.color) }))}
                className="rounded-sm border border-border bg-surface-2 px-3 py-2 text-xs font-semibold hover:bg-muted">
                Reset to team color
              </button>
              <button className="rounded-sm border border-primary/50 bg-surface-2 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-primary hover:bg-primary/10">
                Save as new profile
              </button>
              <button
                onClick={() => void saveHsvSettings()}
                className="rounded-sm bg-primary px-5 py-2 text-sm font-bold uppercase tracking-wider text-primary-foreground shadow-md hover:brightness-110">
                Save to server
              </button>
              <button
                onClick={() => {
                  const exported = {
                    frame: frame.id,
                    lenses,
                    teams: teamList.map((t, i) => {
                      const p = presets[presetKey(t.id, frame.id)] ?? presetFromColor(t.color);
                      return {
                        slot: i + 1,
                        id: t.id,
                        name: t.displayName,
                        hex: savedColors[presetKey(t.id, frame.id)] ?? t.color,
                        h: p.h, s: p.s, v: p.v,
                      };
                    }),
                  };
                  const blob = new Blob([JSON.stringify(exported, null, 2)], { type: "application/json" });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url; a.download = `hsv_presets.${frame.id}.json`; a.click();
                  setTimeout(() => URL.revokeObjectURL(url), 1000);
                }}
                className="rounded-sm border border-border bg-surface-2 px-3 py-2 text-xs font-semibold uppercase tracking-wider hover:bg-muted">
                Download hsv_presets.json
              </button>
              <button
                onClick={() => importInputRef.current?.click()}
                className="rounded-sm border border-border bg-surface-2 px-3 py-2 text-xs font-semibold uppercase tracking-wider hover:bg-muted">
                Import hsv_presets.json
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
                    const sourceFrame: string = data.frame ?? frame.id;
                    const arr: Array<{ id?: string; slot?: number; hex?: string; h?: Range3; s?: Range3; v?: Range3 }> =
                      Array.isArray(data) ? data : data.teams ?? [];
                    if (sourceFrame && sourceFrame !== frame.id) {
                      setPendingImport({ sourceFrame, rows: arr });
                    } else {
                      applyImport(arr, frame.id, false);
                    }
                  } catch (err) {
                    alert(`Import failed: ${(err as Error).message}`);
                  }
                }}
              />
            </div>
          </div>

          {/* Developer block */}
          <div className="hud-panel mt-4">
            <button
              onClick={() => setShowDevSnippet((v) => !v)}
              className="flex w-full items-center justify-between px-4 py-3 text-left">
              <span className="label-eyebrow text-xs">For developer — OpenCV snippet</span>
              <span className="text-mono text-xs text-muted-foreground">{showDevSnippet ? "▾ hide" : "▸ show"}</span>
            </button>
            {showDevSnippet && (
              <div className="border-t border-border p-4">
                <pre className="text-mono overflow-x-auto rounded-sm border border-border bg-background p-3 text-xs leading-relaxed text-foreground">
{`# ${team.displayName}
lower = np.array([${preset.h[0]}, ${preset.s[0]}, ${preset.v[0]}])
upper = np.array([${preset.h[1]}, ${preset.s[1]}, ${preset.v[1]}])
mask  = cv2.inRange(hsv, lower, upper)`}
                </pre>
                <p className="mt-3 text-xs text-muted-foreground">
                  Tip: click anywhere on the sample frame to pick a pixel — HSV ranges are seeded around that color. Each team keeps its own preset.
                </p>
              </div>
            )}
          </div>

          {/* Compare colors tool */}
          <div className="hud-panel mt-4">
            <button
              onClick={() => setCompareOpen((v) => !v)}
              className="flex w-full items-center justify-between px-4 py-3 text-left">
              <span className="label-eyebrow text-xs">Compare colors — side-by-side masks & auto-tune</span>
              <span className="text-mono text-xs text-muted-foreground">{compareOpen ? "▾ hide" : "▸ show"}</span>
            </button>
            {compareOpen && (
              <div className="border-t border-border p-4">
                <div className="mb-3 flex flex-wrap items-center gap-3">
                  <div className="flex items-center gap-2">
                    <span className="label-eyebrow text-xs">A</span>
                    <span className="h-5 w-5 rounded-sm ring-1 ring-border" style={{ backgroundColor: teamSwatch(team.id) }} />
                    <span className="text-xs font-semibold">{team.displayName}</span>
                  </div>
                  <span className="text-muted-foreground">vs</span>
                  <div className="flex items-center gap-2">
                    <span className="label-eyebrow text-xs">B</span>
                    <select
                      value={otherId}
                      onChange={(e) => setOtherId(e.target.value)}
                      className="rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs">
                      {otherCandidates.map((t) => (
                        <option key={t.id} value={t.id}>{t.displayName}</option>
                      ))}
                    </select>
                    <span className="h-5 w-5 rounded-sm ring-1 ring-border" style={{ backgroundColor: teamSwatch(otherId || team.id) }} />
                  </div>

                  <button
                    disabled={tuning || !savCache}
                    onClick={runAutoTune}
                    className="ml-auto rounded-sm border border-primary/50 bg-primary/10 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary hover:bg-primary/20 disabled:opacity-50">
                    {tuning ? "Tuning…" : `Auto-tune ${team.displayName} (scan all shades)`}
                  </button>
                </div>

                <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
                  <div>
                    <div className="label-eyebrow mb-1 text-xs">Mask A · {compareStats.a.toLocaleString()} px</div>
                    <canvas ref={compareARef} className="block w-full rounded-sm border border-border bg-background" />
                  </div>
                  <div>
                    <div className="label-eyebrow mb-1 text-xs">Mask B · {compareStats.b.toLocaleString()} px</div>
                    <canvas ref={compareBRef} className="block w-full rounded-sm border border-border bg-background" />
                  </div>
                  <div>
                    <div className="label-eyebrow mb-1 text-xs">
                      Overlap (red) · {compareStats.overlap.toLocaleString()} px
                      {compareStats.a + compareStats.b > 0 && (
                        <span className="ml-1 text-muted-foreground">
                          ({Math.round((compareStats.overlap * 100) / Math.max(1, Math.min(compareStats.a || 1, compareStats.b || 1)))}%)
                        </span>
                      )}
                    </div>
                    <canvas ref={compareOverlapRef} className="block w-full rounded-sm border border-border bg-background" />
                  </div>
                </div>

                {tuneReport && (
                  <p className="text-mono mt-3 rounded-sm border border-border bg-surface-2 p-2 text-[11px] text-muted-foreground">
                    {tuneReport}
                  </p>
                )}
                <p className="mt-2 text-xs text-muted-foreground">
                  Auto-tune greedily searches H/S/V cuboids around the seed color to maximize own-pixels
                  and minimize overlap with all other teams. Result is applied to the active preset —
                  click <span className="font-semibold">Save preset</span> above to lock it in.
                </p>
              </div>
            )}
          </div>
        </div>
      </div>

      <AlertDialog open={!!pendingImport} onOpenChange={(o) => !o && setPendingImport(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Import preset to which map?</AlertDialogTitle>
            <AlertDialogDescription>
              The file was saved for frame{" "}
              <span className="text-mono font-semibold">
                {pendingImport?.sourceFrame}
              </span>
              , but the current frame is{" "}
              <span className="text-mono font-semibold">{frame.id}</span>. Where
              should these HSV ranges be applied?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() =>
                pendingImport &&
                applyImport(pendingImport.rows, frame.id, false)
              }
            >
              Import to current ({frame.id})
            </AlertDialogAction>
            <AlertDialogAction
              onClick={() =>
                pendingImport &&
                applyImport(
                  pendingImport.rows,
                  pendingImport.sourceFrame,
                  true,
                )
              }
            >
              Switch to {pendingImport?.sourceFrame}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "good" | "warn" | "bad" }) {
  const toneCls = tone === "good" ? "text-success" : tone === "warn" ? "text-warning" : tone === "bad" ? "text-destructive" : "text-foreground";
  return (
    <div className="rounded-sm border border-border bg-surface-2 p-2">
      <div className="label-eyebrow text-xs">{label}</div>
      <div className={`text-mono text-sm font-bold tabular-nums ${toneCls}`}>{value}</div>
      {sub && <div className="text-mono text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

function LensRangeControl({
  mode,
  lens,
  onChange,
}: {
  mode: "red" | "white";
  lens: HsvLens;
  onChange: (patch: Partial<HsvLens>) => void;
}) {
  const title = mode === "red" ? "Red filter HSV range" : "White filter HSV range";
  const description =
    mode === "red"
      ? "Highlights red/orange pixels caused by the outside-ring overlay and damage flashes."
      : "Highlights low-saturation, high-value pixels caused by the white safe-zone filter.";
  const setHsv = (patch: Partial<Preset>) => onChange({ hsv: { ...lens.hsv, ...patch } });

  return (
    <div className="rounded-sm border border-border bg-surface-2 p-3">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="label-eyebrow text-xs">{title}</div>
          <div className="mt-1 text-xs text-muted-foreground">{description}</div>
        </div>
        <label className="inline-flex items-center gap-2 rounded-sm border border-border bg-background px-2 py-1 text-xs font-semibold uppercase tracking-wider">
          <input
            type="checkbox"
            checked={lens.enabled}
            onChange={(e) => onChange({ enabled: e.target.checked })}
          />
          Enabled
        </label>
      </div>

      <HsvRangePreview preset={lens.hsv} tone={mode} />

      <div className="mt-3">
        <Range label="Hue" min={0} max={179} value={lens.hsv.h} onChange={(h) => setHsv({ h: h as Range3 })} />
        <Range label="Saturation" min={0} max={255} value={lens.hsv.s} onChange={(s) => setHsv({ s: s as Range3 })} />
        <Range label="Value" min={0} max={255} value={lens.hsv.v} onChange={(v) => setHsv({ v: v as Range3 })} />
      </div>
    </div>
  );
}

function HsvRangePreview({ preset, tone }: { preset: Preset; tone: "red" | "white" }) {
  const low = hsvCvToRgb(preset.h[0], preset.s[0], preset.v[0]);
  const mid = hsvCvToRgb(
    Math.round((preset.h[0] + preset.h[1]) / 2),
    Math.round((preset.s[0] + preset.s[1]) / 2),
    Math.round((preset.v[0] + preset.v[1]) / 2),
  );
  const high = hsvCvToRgb(preset.h[1], preset.s[1], preset.v[1]);
  const toRgb = ([r, g, b]: [number, number, number]) => `rgb(${r}, ${g}, ${b})`;
  const border = tone === "red" ? "border-primary/40" : "border-sky-300/50";

  return (
    <div className={`overflow-hidden rounded-sm border ${border} bg-background`}>
      <div
        className="h-12"
        style={{ background: `linear-gradient(90deg, ${toRgb(low)}, ${toRgb(mid)}, ${toRgb(high)})` }}
      />
      <div className="grid grid-cols-3 divide-x divide-border border-t border-border text-mono text-xs tabular-nums">
        <div className="px-2 py-1">
          <div className="label-eyebrow text-xs">Low</div>
          H{preset.h[0]} S{preset.s[0]} V{preset.v[0]}
        </div>
        <div className="px-2 py-1">
          <div className="label-eyebrow text-xs">Mid</div>
          H{Math.round((preset.h[0] + preset.h[1]) / 2)} S{Math.round((preset.s[0] + preset.s[1]) / 2)} V{Math.round((preset.v[0] + preset.v[1]) / 2)}
        </div>
        <div className="px-2 py-1">
          <div className="label-eyebrow text-xs">High</div>
          H{preset.h[1]} S{preset.s[1]} V{preset.v[1]}
        </div>
      </div>
    </div>
  );
}

function Range({ label, min, max, value, onChange }: {
  label: string; min: number; max: number; value: [number, number]; onChange: (v: [number, number]) => void;
}) {
  return (
    <div className="mb-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="label-eyebrow text-xs">{label}</span>
        <span className="text-mono text-xs tabular-nums text-muted-foreground">{value[0]} — {value[1]}</span>
      </div>
      <div className="flex items-center gap-2">
        <input type="range" min={min} max={max} value={value[0]}
          onChange={(e) => onChange([Math.min(+e.target.value, value[1]), value[1]])}
          className="w-full accent-[var(--color-primary)]" />
        <input type="range" min={min} max={max} value={value[1]}
          onChange={(e) => onChange([value[0], Math.max(+e.target.value, value[0])])}
          className="w-full accent-[var(--color-primary)]" />
      </div>
    </div>
  );
}
