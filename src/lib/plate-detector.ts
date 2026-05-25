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
  /** Ожидаемая «канонiчная» ширина одной плашки в пикселях. Используется для сплита слипшихся блобов. */
  expectedPlateW?: number;
  /** Ожидаемая высота одной плашки. */
  expectedPlateH?: number;
  /** Сколько boxов максимум выдавать на одну команду (после сплита). */
  maxBoxesPerTeam?: number;
  /** Эрозия маски (px) перед connected-components — помогает «оторвать» соседние плашки. */
  erosionPx?: number;
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

// Прямоугольная эрозия (min-filter) бинарной маски. r — радиус в px.
function erodeMask(mask: Uint8Array, W: number, H: number, r: number): Uint8Array {
  if (r <= 0) return mask;
  const tmp = new Uint8Array(W * H);
  // горизонтальная
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      let ok = 1;
      for (let k = -r; k <= r; k++) {
        const xx = x + k;
        if (xx < 0 || xx >= W || !mask[y * W + xx]) { ok = 0; break; }
      }
      tmp[y * W + x] = ok;
    }
  }
  const out = new Uint8Array(W * H);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      let ok = 1;
      for (let k = -r; k <= r; k++) {
        const yy = y + k;
        if (yy < 0 || yy >= H || !tmp[yy * W + x]) { ok = 0; break; }
      }
      out[y * W + x] = ok;
    }
  }
  return out;
}

// Сплит большого компонента по ожидаемой ширине/высоте плашки.
// Идея: если ширина блоба ≈ k × expectedW (k>=2) — режем вертикально на k кусков по проекции плотности.
function splitBlob(
  mask: Uint8Array,
  W: number,
  H: number,
  b: { x: number; y: number; w: number; h: number; area: number },
  expW: number,
  expH: number,
): { x: number; y: number; w: number; h: number; area: number }[] {
  const kx = Math.max(1, Math.round(b.w / Math.max(1, expW)));
  const ky = Math.max(1, Math.round(b.h / Math.max(1, expH)));
  if (kx <= 1 && ky <= 1) return [b];

  const pieces: { x: number; y: number; w: number; h: number; area: number }[] = [];
  const stepX = b.w / kx;
  const stepY = b.h / ky;
  for (let iy = 0; iy < ky; iy++) {
    for (let ix = 0; ix < kx; ix++) {
      const x0 = Math.floor(b.x + ix * stepX);
      const y0 = Math.floor(b.y + iy * stepY);
      const x1 = Math.min(b.x + b.w, Math.floor(b.x + (ix + 1) * stepX));
      const y1 = Math.min(b.y + b.h, Math.floor(b.y + (iy + 1) * stepY));
      // ужать к фактическим пикселям маски внутри куска
      let minX = x1, minY = y1, maxX = x0, maxY = y0, area = 0;
      for (let y = y0; y < y1; y++) {
        for (let x = x0; x < x1; x++) {
          if (mask[y * W + x]) {
            if (x < minX) minX = x;
            if (y < minY) minY = y;
            if (x > maxX) maxX = x;
            if (y > maxY) maxY = y;
            area++;
          }
        }
      }
      if (area >= 8) {
        pieces.push({ x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1, area });
      }
    }
  }
  return pieces.length ? pieces : [b];
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
  const minW = opts.minWidth ?? 18;
  const maxW = opts.maxWidth ?? 320;
  const minH = opts.minHeight ?? 10;
  const maxH = opts.maxHeight ?? 80;
  const ignoreBottom = opts.ignoreBottomPx ?? 90;
  const expW = opts.expectedPlateW ?? 70;
  const expH = opts.expectedPlateH ?? 18;
  const maxPerTeam = opts.maxBoxesPerTeam ?? 6;
  const erosion = opts.erosionPx ?? 0;

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
    const workMask = erosion > 0 ? erodeMask(mask, rw, usableH, erosion) : mask;
    const rawComps = connectedBoxes(workMask, rw, usableH);
    // сначала отсеиваем шум по площади/высоте — слишком мелкие отбрасываем,
    // слишком большие — отдаём в split.
    const filtered: { x: number; y: number; w: number; h: number; area: number }[] = [];
    for (const c of rawComps) {
      if (c.area < 12) continue;
      if (c.h > maxH * 2 || c.w > maxW * 2) continue; // явная заливка/фон
      filtered.push(c);
    }
    // сплит слипшихся
    const split: typeof filtered = [];
    for (const c of filtered) {
      const tooWide = c.w >= expW * 1.6;
      const tooTall = c.h >= expH * 1.6;
      if (tooWide || tooTall) {
        split.push(...splitBlob(workMask, rw, usableH, c, expW, expH));
      } else {
        split.push(c);
      }
    }
    // финальная валидация
    const comps = split.filter((c) => {
      const ar = c.w / Math.max(1, c.h);
      return c.w >= minW && c.w <= maxW && c.h >= minH && c.h <= maxH && ar >= 0.8 && ar <= 14.0;
    });
    if (!comps.length) continue;
    comps.sort((a, b) => b.area - a.area);
    for (const c of comps.slice(0, maxPerTeam)) {
      boxes.push({
        slot: team.slot,
        teamId: team.id,
        teamName: team.name,
        hex: team.hex,
        x: rx + c.x,
        y: ry + c.y,
        w: c.w,
        h: c.h,
        source: "auto",
      });
    }
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

// ============================================================================
// Peak-refine: уточнение и разрез box-а по проекции ярких пикселей (текста).
// Идея: внутри плашки белый/яркий текст имеет V > vThr. Сумма по столбцам даёт
// "пик-профиль" — несколько игроков одной команды стоят рядом => несколько пиков
// с провалами между ними. Разрезаем box по провалам и обрезаем края до первого
// столбца, где count > minCount.
// ============================================================================
export type PeakRefineOpts = {
  vThreshold?: number;   // V для "яркого" пикселя (текст плашки)
  minRunPx?: number;     // минимальная ширина одного пика (px)
  valleyRatio?: number;  // провал считается разделителем, если < ratio * peakMax
  padPx?: number;        // расширение box перед анализом, px
  marginPx?: number;     // отступ слева/справа от пика
};

export function refineBoxesByPeaks(
  rgba: Uint8ClampedArray,
  fw: number,
  fh: number,
  boxes: Box[],
  opts: PeakRefineOpts = {},
): Box[] {
  const vThr = opts.vThreshold ?? 190;
  const minRun = opts.minRunPx ?? 6;
  const ratio = opts.valleyRatio ?? 0.35;
  const pad = opts.padPx ?? 2;
  const margin = opts.marginPx ?? 1;

  const out: Box[] = [];
  for (const b of boxes) {
    const x0 = Math.max(0, b.x - pad);
    const y0 = Math.max(0, b.y - pad);
    const x1 = Math.min(fw, b.x + b.w + pad);
    const y1 = Math.min(fh, b.y + b.h + pad);
    const W = x1 - x0, H = y1 - y0;
    if (W < 6 || H < 4) { out.push(b); continue; }

    // column-wise count of bright pixels
    const col = new Int32Array(W);
    for (let y = y0; y < y1; y++) {
      const row = y * fw;
      for (let x = x0; x < x1; x++) {
        const i = (row + x) * 4;
        const r = rgba[i], g = rgba[i + 1], bb = rgba[i + 2];
        const v = r >= g ? (r >= bb ? r : bb) : (g >= bb ? g : bb);
        if (v >= vThr) col[x - x0]++;
      }
    }
    // smooth 3-window
    const sm = new Float32Array(W);
    for (let i = 0; i < W; i++) {
      const a = i > 0 ? col[i - 1] : col[i];
      const c = i < W - 1 ? col[i + 1] : col[i];
      sm[i] = (a + col[i] + c) / 3;
    }
    let peakMax = 0;
    for (let i = 0; i < W; i++) if (sm[i] > peakMax) peakMax = sm[i];
    if (peakMax < 1.5) { out.push(b); continue; }
    const thr = Math.max(0.6, peakMax * ratio);

    // runs: contiguous columns with sm[i] > thr
    const runs: { s: number; e: number }[] = [];
    let s = -1;
    for (let i = 0; i < W; i++) {
      if (sm[i] > thr) {
        if (s < 0) s = i;
      } else if (s >= 0) {
        runs.push({ s, e: i - 1 });
        s = -1;
      }
    }
    if (s >= 0) runs.push({ s, e: W - 1 });
    const valid = runs.filter((r) => r.e - r.s + 1 >= minRun);

    if (valid.length === 0) { out.push(b); continue; }
    // merge runs closer than minRun (single text cluster со внутренними провалами)
    const merged: { s: number; e: number }[] = [valid[0]];
    for (let i = 1; i < valid.length; i++) {
      const last = merged[merged.length - 1];
      if (valid[i].s - last.e < minRun) last.e = valid[i].e;
      else merged.push(valid[i]);
    }
    for (const r of merged) {
      const sx = Math.max(0, x0 + r.s - margin);
      const ex = Math.min(fw, x0 + r.e + 1 + margin);
      const w = ex - sx;
      if (w < 6) continue;
      out.push({ ...b, x: sx, y: b.y, w, h: b.h, source: "manual" });
    }
  }
  return out;
}

// ============================================================================
// HSV mask overlay: строим RGBA-канву, где пиксели маски залиты цветом команды.
// Используется для визуальной отладки tolerance-ов в UI.
// mode: "active" — только activeSlot; "all" — все команды (цвета суммируются).
// ============================================================================
export type MaskOpts = {
  hTolExtra?: number;
  sTolExtra?: number;
  vTolExtra?: number;
  alpha?: number;      // 0..255
  activeSlot?: number; // если задан и mode === "active" — рисуем только эту команду
  mode?: "active" | "all";
};

function hexToRgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return [255, 0, 255];
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function buildHSVMaskCanvas(
  rgba: Uint8ClampedArray,
  fw: number,
  fh: number,
  preset: HSVPreset,
  roi: { x: number; y: number; w: number; h: number },
  opts: MaskOpts = {},
): HTMLCanvasElement {
  const hExtra = opts.hTolExtra ?? 1;
  const sExtra = opts.sTolExtra ?? 8;
  const vExtra = opts.vTolExtra ?? 14;
  const alpha = opts.alpha ?? 130;
  const mode = opts.mode ?? "all";

  const out = document.createElement("canvas");
  out.width = fw;
  out.height = fh;
  const octx = out.getContext("2d")!;
  const img = octx.createImageData(fw, fh);
  const data = img.data;

  const teams = mode === "active"
    ? preset.teams.filter((t) => t.slot === opts.activeSlot)
    : preset.teams;
  const teamRgb = teams.map((t) => hexToRgb(t.hex));

  for (let y = roi.y; y < roi.y + roi.h; y++) {
    for (let x = roi.x; x < roi.x + roi.w; x++) {
      const i = (y * fw + x) * 4;
      const [H, S, V] = rgbToHsvCv(rgba[i], rgba[i + 1], rgba[i + 2]);
      for (let t = 0; t < teams.length; t++) {
        const tm = teams[t];
        const [h0, h1] = tm.h, [s0, s1] = tm.s, [v0, v1] = tm.v;
        if (
          H >= h0 - hExtra && H <= h1 + hExtra &&
          S >= s0 - sExtra && S <= s1 + sExtra &&
          V >= v0 - vExtra && V <= v1 + vExtra
        ) {
          const [r, g, b] = teamRgb[t];
          data[i] = r; data[i + 1] = g; data[i + 2] = b; data[i + 3] = alpha;
          break;
        }
      }
    }
  }
  octx.putImageData(img, 0, 0);
  return out;
}

export function makeBatchId(slug: string): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  const safe = slug.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "batch";
  return `batch_${stamp}_${safe}`;
}
