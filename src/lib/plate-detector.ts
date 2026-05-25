// Browser-side HSV plate detector — порт упрощённой логики build_dataset_opencv.py.
// Цель: предложить bounding-boxы плашек команд на кадре для последующей ручной
// корректировки и экспорта в YOLO. Не претендует на 1-в-1 совпадение с продовым
// детектором — это «черновой разметчик», человек поправит.

export type TeamHSV = {
  slot: number;
  id: string;
  name: string;
  hex: string;
  h: [number, number];
  s: [number, number];
  v: [number, number];
};

export type HSVPreset = {
  frame: string;
  teams: TeamHSV[];
};

export type Zone = {
  id: string;
  name?: string;
  tag?: string;
  x: number;
  y: number;
  w: number;
  h: number;
};

export type ZonesCfg = {
  base: [number, number];
  zones: Zone[];
};

export type Box = {
  slot: number; // 1..20 (class index = slot - 1 в YOLO)
  teamId: string;
  teamName: string;
  hex: string;
  // координаты в исходных пикселях полного кадра
  x: number;
  y: number;
  w: number;
  h: number;
  source: "auto" | "manual";
};

export type DetectOptions = {
  hTolExtra?: number;
  sTolExtra?: number;
  vTolExtra?: number;
  minWidth?: number;
  maxWidth?: number;
  minHeight?: number;
  maxHeight?: number;
  ignoreBottomPx?: number; // пропустить нижние N px ROI (HUD-крышка)
};

// RGB → HSV в OpenCV-конвенции: H in [0,179], S/V in [0,255].
function rgbToHsvCv(r: number, g: number, b: number): [number, number, number] {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const v = max;
  const d = max - min;
  const s = max === 0 ? 0 : (d * 255) / max;
  let h = 0;
  if (d !== 0) {
    if (max === r) h = 60 * (((g - b) / d) % 6);
    else if (max === g) h = 60 * ((b - r) / d + 2);
    else h = 60 * ((r - g) / d + 4);
  }
  if (h < 0) h += 360;
  return [Math.round(h / 2), Math.round(s), Math.round(v)];
}

export function pickMinimapZone(zones: ZonesCfg, fw: number, fh: number): { x: number; y: number; w: number; h: number } {
  const base = zones.base ?? [1920, 1080];
  const z = zones.zones.find((zz) => zz.tag === "minimap") ?? zones.zones[0];
  const sx = fw / base[0];
  const sy = fh / base[1];
  return {
    x: Math.max(0, Math.round(z.x * sx)),
    y: Math.max(0, Math.round(z.y * sy)),
    w: Math.max(1, Math.round(z.w * sx)),
    h: Math.max(1, Math.round(z.h * sy)),
  };
}

// Простейшая 4-связная маркировка компонентов на бинарной маске.
// Возвращает bbox каждого компонента в координатах ROI.
function connectedBoxes(
  mask: Uint8Array,
  W: number,
  H: number,
): { x: number; y: number; w: number; h: number; area: number }[] {
  const labels = new Int32Array(W * H);
  const out: { x: number; y: number; w: number; h: number; area: number }[] = [];
  const stack: number[] = [];
  let nextLabel = 0;
  for (let i = 0; i < mask.length; i++) {
    if (mask[i] === 0 || labels[i] !== 0) continue;
    nextLabel++;
    let minX = W, minY = H, maxX = 0, maxY = 0, area = 0;
    stack.push(i);
    labels[i] = nextLabel;
    while (stack.length) {
      const p = stack.pop()!;
      const x = p % W;
      const y = (p - x) / W;
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
      area++;
      if (x > 0 && mask[p - 1] && !labels[p - 1]) { labels[p - 1] = nextLabel; stack.push(p - 1); }
      if (x < W - 1 && mask[p + 1] && !labels[p + 1]) { labels[p + 1] = nextLabel; stack.push(p + 1); }
      if (y > 0 && mask[p - W] && !labels[p - W]) { labels[p - W] = nextLabel; stack.push(p - W); }
      if (y < H - 1 && mask[p + W] && !labels[p + W]) { labels[p + W] = nextLabel; stack.push(p + W); }
    }
    out.push({ x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1, area });
  }
  return out;
}

export function detectPlates(
  rgba: Uint8ClampedArray,
  fw: number,
  fh: number,
  preset: HSVPreset,
  roi: { x: number; y: number; w: number; h: number },
  opts: DetectOptions = {},
): Box[] {
  const hExtra = opts.hTolExtra ?? 1;
  const sExtra = opts.sTolExtra ?? 8;
  const vExtra = opts.vTolExtra ?? 14;
  const minW = opts.minWidth ?? 24;
  const maxW = opts.maxWidth ?? 240;
  const minH = opts.minHeight ?? 14;
  const maxH = opts.maxHeight ?? 60;
  const ignoreBottom = opts.ignoreBottomPx ?? 90;

  const rx = roi.x, ry = roi.y, rw = roi.w, rh = roi.h;
  const usableH = Math.max(1, rh - ignoreBottom);

  // Предвычисляем HSV ROI один раз.
  const hsv = new Uint8Array(rw * usableH * 3);
  for (let y = 0; y < usableH; y++) {
    const srcRow = (ry + y) * fw + rx;
    for (let x = 0; x < rw; x++) {
      const idx = (srcRow + x) * 4;
      const [h, s, v] = rgbToHsvCv(rgba[idx], rgba[idx + 1], rgba[idx + 2]);
      const k = (y * rw + x) * 3;
      hsv[k] = h; hsv[k + 1] = s; hsv[k + 2] = v;
    }
  }

  const boxes: Box[] = [];
  const mask = new Uint8Array(rw * usableH);

  for (const team of preset.teams) {
    const [h0, h1] = team.h, [s0, s1] = team.s, [v0, v1] = team.v;
    const H0 = Math.max(0, h0 - hExtra), H1 = Math.min(179, h1 + hExtra);
    const S0 = Math.max(0, s0 - sExtra), S1 = Math.min(255, s1 + sExtra);
    const V0 = Math.max(0, v0 - vExtra), V1 = Math.min(255, v1 + vExtra);
    mask.fill(0);
    for (let i = 0, j = 0; i < hsv.length; i += 3, j++) {
      const H = hsv[i], S = hsv[i + 1], V = hsv[i + 2];
      if (H >= H0 && H <= H1 && S >= S0 && S <= S1 && V >= V0 && V <= V1) {
        mask[j] = 1;
      }
    }
    // Лучший компонент по площади на одну команду.
    const comps = connectedBoxes(mask, rw, usableH).filter((c) => {
      const ar = c.w / Math.max(1, c.h);
      return c.w >= minW && c.w <= maxW && c.h >= minH && c.h <= maxH && ar >= 1.0 && ar <= 12.0 && c.area >= c.w * c.h * 0.35;
    });
    if (!comps.length) continue;
    comps.sort((a, b) => b.area - a.area);
    const best = comps[0];
    boxes.push({
      slot: team.slot,
      teamId: team.id,
      teamName: team.name,
      hex: team.hex,
      x: rx + best.x,
      y: ry + best.y,
      w: best.w,
      h: best.h,
      source: "auto",
    });
  }
  return boxes;
}

// YOLO line: "class cx cy w h" (normalized)
export function boxToYolo(box: Box, fw: number, fh: number): string {
  const cls = box.slot - 1; // 0..19
  const cx = (box.x + box.w / 2) / fw;
  const cy = (box.y + box.h / 2) / fh;
  const w = box.w / fw;
  const h = box.h / fh;
  return `${cls} ${cx.toFixed(6)} ${cy.toFixed(6)} ${w.toFixed(6)} ${h.toFixed(6)}`;
}

export function makeBatchId(slug: string): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  const safe = slug.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "batch";
  return `batch_${stamp}_${safe}`;
}
