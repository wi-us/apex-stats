"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../lib/api";
import type { MinimapLocateResponse, MinimapLocatorMapOption } from "../../lib/types";
import { AdminMinimapLocatorVideoTab } from "./AdminMinimapLocatorVideoTab";
import styles from "./minimap-locator.module.css";

const DEFAULTS = {
  minimapX: 48,
  minimapY: 60,
  minimapSize: 240,
  minimapBorder: 12,
  minScore: 0.35,
  searchMode: "window" as "window" | "full" | "tiled",
};

const MIN_BBOX_SIZE = 120;

function scoreBand(score: number): "poor" | "medium" | "good" {
  if (score >= 0.55) return "good";
  if (score >= 0.35) return "medium";
  return "poor";
}

function MapOverlay({
  mapUrl,
  result,
  showTopCandidates,
}: {
  mapUrl: string;
  result: MinimapLocateResponse;
  showTopCandidates: boolean;
}) {
  const imgRef = useRef<HTMLImageElement>(null);
  const [rendered, setRendered] = useState({ rw: 0, rh: 0, nw: 1, nh: 1 });

  const syncSize = useCallback(() => {
    const img = imgRef.current;
    if (!img) return;
    setRendered({
      rw: img.clientWidth,
      rh: img.clientHeight,
      nw: img.naturalWidth || 1,
      nh: img.naturalHeight || 1,
    });
  }, []);

  useEffect(() => {
    syncSize();
    window.addEventListener("resize", syncSize);
    return () => window.removeEventListener("resize", syncSize);
  }, [syncSize, mapUrl, result, showTopCandidates]);

  const sx = rendered.rw / rendered.nw;
  const sy = rendered.rh / rendered.nh;
  const { bbox, center, score, mapId } = result;
  const candidateBoxes = showTopCandidates ? result.topCandidates.slice(0, 5) : [];

  return (
    <div className={styles.mapStage}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img ref={imgRef} src={mapUrl} alt={`Map ${mapId}`} onLoad={syncSize} />
      <div className={styles.mapOverlay}>
        {candidateBoxes.map((c, i) => (
          <div
            key={`${c.x}-${c.y}-${i}`}
            className={styles.candidateBbox}
            style={{
              left: c.x * sx,
              top: c.y * sy,
              width: c.w * sx,
              height: c.h * sy,
              borderColor: i === 0 ? "#66ff99" : "#4a9eff",
            }}
          >
            <span className={styles.candidateScore}>{c.score.toFixed(3)}</span>
          </div>
        ))}
        {bbox.w > 0 && bbox.h > 0 ? (
          <>
            <div
              className={styles.bbox}
              style={{
                left: bbox.x * sx,
                top: bbox.y * sy,
                width: bbox.w * sx,
                height: bbox.h * sy,
              }}
            />
            <div
              className={styles.centerDot}
              style={{ left: center.x * sx, top: center.y * sy }}
            />
            <div className={styles.scoreLabel} style={{ left: bbox.x * sx, top: bbox.y * sy }}>
              {`${mapId} · score ${score.toFixed(3)} · ws ${result.windowSize || bbox.w}`}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}

export function AdminMinimapLocatorModule() {
  const [tab, setTab] = useState<"screenshot" | "video">("screenshot");
  const [maps, setMaps] = useState<MinimapLocatorMapOption[]>([]);
  const [mapId, setMapId] = useState("mp_storm_point");
  const [minimapX, setMinimapX] = useState(DEFAULTS.minimapX);
  const [minimapY, setMinimapY] = useState(DEFAULTS.minimapY);
  const [minimapSize, setMinimapSize] = useState(DEFAULTS.minimapSize);
  const [minimapBorder, setMinimapBorder] = useState(DEFAULTS.minimapBorder);
  const [minScore, setMinScore] = useState(DEFAULTS.minScore);
  const [searchMode, setSearchMode] = useState<"window" | "full" | "tiled">(DEFAULTS.searchMode);
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [submitErr, setSubmitErr] = useState<string | null>(null);
  const [result, setResult] = useState<MinimapLocateResponse | null>(null);
  const [showTopCandidates, setShowTopCandidates] = useState(true);

  useEffect(() => {
    void api
      .getMinimapLocatorMaps()
      .then((items) => {
        setMaps(items);
        if (items.length && !items.some((m) => m.mapId === mapId)) {
          const first = items.find((m) => m.exists) ?? items[0];
          setMapId(first.mapId);
        }
      })
      .catch((e) => setLoadErr(e instanceof Error ? e.message : String(e)));
  }, [mapId]);

  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const cropPreviewStyle = useMemo(() => {
    if (!previewUrl) return null;
    return {
      width: minimapSize,
      height: minimapSize,
      marginLeft: minimapX,
      marginTop: minimapY,
    } as const;
  }, [previewUrl, minimapX, minimapY, minimapSize]);

  const onLocate = async () => {
    setSubmitErr(null);
    setResult(null);
    if (!file) {
      setSubmitErr("Загрузите скриншот (PNG/JPG).");
      return;
    }
    if (!mapId) {
      setSubmitErr("Выберите карту.");
      return;
    }
    const selected = maps.find((m) => m.mapId === mapId);
    if (selected && !selected.exists) {
      setSubmitErr(`Файл карты не найден на сервере: ${selected.mapPath}`);
      return;
    }

    const form = new FormData();
    form.append("file", file);
    form.append("mapId", mapId);
    form.append("minimapX", String(minimapX));
    form.append("minimapY", String(minimapY));
    form.append("minimapSize", String(minimapSize));
    form.append("minimapBorder", String(minimapBorder));
    form.append("minScore", String(minScore));
    form.append("searchMode", searchMode);

    setBusy(true);
    try {
      const res = await api.locateMinimap(form);
      setResult(res);
    } catch (e) {
      setSubmitErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const mapImageUrl = result ? api.minimapAssetUrl(result.debug.mapImageUrl) : null;
  const bboxTooSmall = result ? result.bbox.w < MIN_BBOX_SIZE || result.bbox.h < MIN_BBOX_SIZE : false;
  const band = result ? scoreBand(result.score) : null;

  return (
    <div className={styles.root}>
      <div>
        <h1 className={styles.title}>ПОИСК ТОЧКИ ПО МИНИКАРТЕ</h1>
        <p className={styles.subtitle}>
          Sliding window: окно полной карты сравнивается с миникартой (не уменьшенный template). Режим по умолчанию —
          window.
        </p>
      </div>

      {loadErr ? <div className={styles.err}>{loadErr}</div> : null}

      <div className={styles.tabs}>
        <button
          type="button"
          className={`${styles.tabBtn}${tab === "screenshot" ? ` ${styles.tabBtnActive}` : ""}`}
          onClick={() => setTab("screenshot")}
        >
          Скриншот
        </button>
        <button
          type="button"
          className={`${styles.tabBtn}${tab === "video" ? ` ${styles.tabBtnActive}` : ""}`}
          onClick={() => setTab("video")}
        >
          Видео
        </button>
      </div>

      {tab === "video" ? <AdminMinimapLocatorVideoTab maps={maps} /> : null}

      {tab === "screenshot" ? (
      <div className={styles.layout}>
        <aside className={styles.panel}>
          <h3>Параметры</h3>
          <label className={styles.label}>
            Скриншот
            <input
              className={styles.fileInput}
              type="file"
              accept="image/png,image/jpeg,image/jpg"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <label className={styles.label}>
            Карта (map_id)
            <select value={mapId} onChange={(e) => setMapId(e.target.value)}>
              {maps.length === 0 ? <option value={mapId}>{mapId}</option> : null}
              {maps.map((m) => (
                <option key={m.mapId} value={m.mapId} disabled={!m.exists}>
                  {m.label} ({m.mapId}){m.exists ? "" : " — нет файла"}
                </option>
              ))}
            </select>
          </label>
          <div className={styles.grid2}>
            <label className={styles.label}>
              minimap_x
              <input type="number" value={minimapX} onChange={(e) => setMinimapX(Number(e.target.value))} />
            </label>
            <label className={styles.label}>
              minimap_y
              <input type="number" value={minimapY} onChange={(e) => setMinimapY(Number(e.target.value))} />
            </label>
            <label className={styles.label}>
              minimap_size
              <input type="number" value={minimapSize} onChange={(e) => setMinimapSize(Number(e.target.value))} />
            </label>
            <label className={styles.label}>
              minimap_border
              <input type="number" value={minimapBorder} onChange={(e) => setMinimapBorder(Number(e.target.value))} />
            </label>
            <label className={styles.label}>
              min_score
              <input
                type="number"
                step="0.01"
                min={0}
                max={1}
                value={minScore}
                onChange={(e) => setMinScore(Number(e.target.value))}
              />
            </label>
            <label className={styles.label}>
              search_mode
              <select
                value={searchMode}
                onChange={(e) => setSearchMode(e.target.value as "window" | "full" | "tiled")}
              >
                <option value="window">window (рекомендуется)</option>
                <option value="full">full (legacy template)</option>
                <option value="tiled">tiled (legacy)</option>
              </select>
            </label>
          </div>
          <button type="button" className={styles.btn} disabled={busy} onClick={() => void onLocate()}>
            {busy ? "Поиск…" : "Найти на карте"}
          </button>
          {submitErr ? <div className={styles.err}>{submitErr}</div> : null}
        </aside>

        <main className={styles.panel}>
          <h3>Результат</h3>
          {!result && !previewUrl ? (
            <p className={styles.subtitle}>Загрузите скриншот и нажмите «Найти на карте».</p>
          ) : null}

          {previewUrl ? (
            <div className={styles.previewRow}>
              <figure className={styles.previewCard}>
                <div style={{ position: "relative", display: "inline-block", lineHeight: 0 }}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={previewUrl} alt="Upload preview" style={{ maxWidth: "100%", height: "auto" }} />
                  {cropPreviewStyle ? (
                    <div
                      style={{
                        position: "absolute",
                        left: cropPreviewStyle.marginLeft,
                        top: cropPreviewStyle.marginTop,
                        width: cropPreviewStyle.width,
                        height: cropPreviewStyle.height,
                        border: "2px solid #ff8c32",
                        boxSizing: "border-box",
                        pointerEvents: "none",
                      }}
                    />
                  ) : null}
                </div>
                <figcaption className={styles.previewCaption}>Скриншот + crop миникарты</figcaption>
              </figure>
            </div>
          ) : null}

          {result ? (
            <>
              {bboxTooSmall ? (
                <div className={styles.err}>
                  Найден слишком маленький фрагмент ({result.bbox.w}×{result.bbox.h}) — вероятно, алгоритм зацепился за
                  локальный паттерн. Попробуйте search_mode=window или скорректируйте crop.
                </div>
              ) : null}
              {result.ambiguous ? (
                <div className={styles.warn}>
                  Неоднозначный результат: top-1 и top-2 кандидаты близки по score. Проверьте top candidates на карте.
                </div>
              ) : null}
              {result.suspicious ? (
                <div className={styles.warn}>
                  Подозрительное совпадение у края карты при низком score — нужна ручная проверка.
                </div>
              ) : null}
              {!result.ok && !bboxTooSmall ? (
                <div className={styles.warn}>
                  {band === "poor"
                    ? "Низкая уверенность (score < 0.35)."
                    : band === "medium"
                      ? "Средняя уверенность (0.35–0.55) — рекомендуется ручная проверка."
                      : "Результат ниже порога ok."}{" "}
                  {result.reason ?? ""} Результат показан для оценки.
                </div>
              ) : result.ok ? (
                <div className={styles.metrics}>
                  <span>
                    Статус: <strong>OK</strong> (score ≥ {minScore})
                  </span>
                </div>
              ) : null}

              <label className={styles.toggleRow}>
                <input
                  type="checkbox"
                  checked={showTopCandidates}
                  onChange={(e) => setShowTopCandidates(e.target.checked)}
                />
                Показать top-5 кандидатов на карте
              </label>

              <div className={styles.metrics}>
                <span>
                  score: <code className={styles.mono}>{result.score.toFixed(4)}</code>
                </span>
                <span>
                  window_size: <code className={styles.mono}>{result.windowSize || result.bbox.w}</code>
                </span>
                <span>
                  scale: <code className={styles.mono}>{result.scale.toFixed(3)}</code>
                </span>
                <span>
                  mode: <code className={styles.mono}>{result.searchMode}</code>
                </span>
                <span>
                  center:{" "}
                  <code className={styles.mono}>
                    ({result.center.x.toFixed(1)}, {result.center.y.toFixed(1)})
                  </code>
                </span>
                <span>
                  bbox:{" "}
                  <code className={styles.mono}>
                    x={result.bbox.x} y={result.bbox.y} w={result.bbox.w} h={result.bbox.h}
                  </code>
                </span>
              </div>

              {mapImageUrl ? (
                <MapOverlay mapUrl={mapImageUrl} result={result} showTopCandidates={showTopCandidates} />
              ) : null}

              <div className={styles.debugGrid}>
                {[
                  ["Кадр + crop", result.debug.frameWithCropUrl],
                  ["Миникарта", result.debug.minimapRawUrl],
                  ["Processed", result.debug.minimapProcessedUrl],
                  ["Match на карте", result.debug.mapMatchUrl],
                  ["Фрагмент карты", result.debug.matchedPatchUrl],
                  ["Candidate processed", result.debug.candidateProcessedUrl],
                  ["Top candidates", result.debug.candidatesUrl],
                  ["Debug panel", result.debug.debugPanelUrl],
                ].map(([label, url]) => (
                  <figure key={label} className={styles.previewCard}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={api.minimapAssetUrl(url)} alt={label} />
                    <figcaption className={styles.previewCaption}>{label}</figcaption>
                  </figure>
                ))}
              </div>
            </>
          ) : null}
        </main>
      </div>
      ) : null}
    </div>
  );
}
