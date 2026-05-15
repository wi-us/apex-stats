"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../lib/api";
import type {
  MinimapLocatorMapOption,
  VideoFrameDebugUrls,
  VideoJobResultResponse,
  VideoJobStatusResponse,
  VideoTrackingPathPoint,
} from "../../lib/types";
import styles from "./minimap-locator.module.css";

const VIDEO_DEFAULTS = {
  minimapX: 48,
  minimapY: 60,
  minimapSize: 240,
  minimapBorder: 12,
  frameStep: 0,
  sampleIntervalSec: 1,
  minScore: 0.35,
  goodScore: 0.55,
  maxJumpDistance: 120,
  searchRadius: 180,
  relockScore: 0.55,
  allowGlobalRelock: true,
  smoothing: true,
  fastMode: true,
  saveFrameDebug: false,
  debugVideo: false,
};

const SAMPLE_PRESETS = [
  { label: "1 / сек", sec: 1 },
  { label: "1 / 2 сек", sec: 2 },
  { label: "1 / 5 сек", sec: 5 },
] as const;

const STATUS_COLORS: Record<string, string> = {
  accepted: "#22c55e",
  accepted_low_score: "#ffb020",
  relocked: "#a855f7",
  rejected_jump: "#ef4444",
  low_score: "#64748b",
  skipped: "#64748b",
  failed: "#64748b",
};

function TrajectoryMap({
  mapUrl,
  points,
  selected,
  onSelect,
}: {
  mapUrl: string;
  points: VideoTrackingPathPoint[];
  selected: VideoTrackingPathPoint | null;
  onSelect: (p: VideoTrackingPathPoint) => void;
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
  }, [syncSize, mapUrl, points, selected]);

  const sx = rendered.rw / rendered.nw;
  const sy = rendered.rh / rendered.nh;

  const pathLine = useMemo(() => {
    const track = points.filter(
      (p) =>
        p.status === "accepted" ||
        p.status === "accepted_low_score" ||
        p.status === "relocked"
    );
    return track
      .map((p) => {
        const c = p.smoothedCenter ?? p.center;
        if (!c) return null;
        return `${c.x * sx},${c.y * sy}`;
      })
      .filter(Boolean)
      .join(" ");
  }, [points, sx, sy]);

  return (
    <MapStage
      mapUrl={mapUrl}
      imgRef={imgRef}
      syncSize={syncSize}
      pathLine={pathLine}
      points={points}
      selected={selected}
      onSelect={onSelect}
      sx={sx}
      sy={sy}
      rw={rendered.rw}
      rh={rendered.rh}
    />
  );
}

function MapStage({
  mapUrl,
  imgRef,
  syncSize,
  pathLine,
  points,
  selected,
  onSelect,
  sx,
  sy,
  rw,
  rh,
}: {
  mapUrl: string;
  imgRef: React.RefObject<HTMLImageElement | null>;
  syncSize: () => void;
  pathLine: string;
  points: VideoTrackingPathPoint[];
  selected: VideoTrackingPathPoint | null;
  onSelect: (p: VideoTrackingPathPoint) => void;
  sx: number;
  sy: number;
  rw: number;
  rh: number;
}) {
  return (
    <div className={styles.mapStage}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img ref={imgRef} src={mapUrl} alt="Trajectory map" onLoad={syncSize} />
      <svg className={styles.trajectorySvg} viewBox={`0 0 ${rw || 1} ${rh || 1}`}>
        {pathLine ? (
          <polyline points={pathLine} fill="none" stroke="#18d6e8" strokeWidth={2} />
        ) : null}
        {points.map((p) => {
          const c = p.smoothedCenter ?? p.center;
          if (!c) return null;
          const isSel = selected?.frameIndex === p.frameIndex;
          const color = isSel ? "#ff5b12" : STATUS_COLORS[p.status] ?? "#94a3b8";
          const r = isSel ? 7 : p.status === "rejected_jump" ? 4 : 5;
          return (
            <circle
              key={p.frameIndex}
              cx={c.x * sx}
              cy={c.y * sy}
              r={r}
              fill={color}
              stroke={isSel ? "#fff" : "none"}
              strokeWidth={2}
              style={{ cursor: "pointer" }}
              onClick={() => onSelect(p)}
            />
          );
        })}
        {selected?.bbox && selected.bbox.w > 0 ? (
          <rect
            x={selected.bbox.x * sx}
            y={selected.bbox.y * sy}
            width={selected.bbox.w * sx}
            height={selected.bbox.h * sy}
            fill="none"
            stroke="#ff5b12"
            strokeWidth={2}
          />
        ) : null}
      </svg>
    </div>
  );
}

function VideoUploadPreview({
  videoUrl,
  minimapX,
  minimapY,
  minimapSize,
  onMeta,
}: {
  videoUrl: string;
  minimapX: number;
  minimapY: number;
  minimapSize: number;
  onMeta: (meta: { durationSec: number; width: number; height: number }) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [display, setDisplay] = useState({ w: 0, h: 0 });
  const [native, setNative] = useState({ w: 1920, h: 1080 });

  const syncDisplay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    setDisplay({ w: v.clientWidth, h: v.clientHeight });
    if (v.videoWidth > 0) setNative({ w: v.videoWidth, h: v.videoHeight });
  }, []);

  useEffect(() => {
    syncDisplay();
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => syncDisplay());
    ro.observe(el);
    return () => ro.disconnect();
  }, [syncDisplay, videoUrl]);

  const scaleX = native.w > 0 ? display.w / native.w : 1;
  const scaleY = native.h > 0 ? display.h / native.h : 1;

  return (
    <figure className={styles.previewCard}>
      <div ref={wrapRef} className={styles.videoPreviewWrap}>
        <video
          ref={videoRef}
          src={videoUrl}
          className={styles.videoPreview}
          controls
          muted
          playsInline
          preload="metadata"
          onLoadedMetadata={(e) => {
            const v = e.currentTarget;
            setNative({ w: v.videoWidth, h: v.videoHeight });
            onMeta({
              durationSec: Number.isFinite(v.duration) ? v.duration : 0,
              width: v.videoWidth,
              height: v.videoHeight,
            });
            syncDisplay();
          }}
          onLoadedData={syncDisplay}
        />
        <CropOverlay
          left={minimapX * scaleX}
          top={minimapY * scaleY}
          width={minimapSize * scaleX}
          height={minimapSize * scaleY}
        />
      </div>
      <figcaption className={styles.previewCaption}>Предпросмотр + crop миникарты</figcaption>
    </figure>
  );
}

function CropOverlay({ left, top, width, height }: { left: number; top: number; width: number; height: number }) {
  return (
    <div
      className={styles.cropOverlay}
      style={{ left, top, width, height }}
    />
  );
}

export function AdminMinimapLocatorVideoTab({ maps }: { maps: MinimapLocatorMapOption[] }) {
  const [mapId, setMapId] = useState("mp_storm_point");
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [videoMeta, setVideoMeta] = useState({ durationSec: 0, width: 0, height: 0 });
  const [minimapX, setMinimapX] = useState(VIDEO_DEFAULTS.minimapX);
  const [minimapY, setMinimapY] = useState(VIDEO_DEFAULTS.minimapY);
  const [minimapSize, setMinimapSize] = useState(VIDEO_DEFAULTS.minimapSize);
  const [minimapBorder, setMinimapBorder] = useState(VIDEO_DEFAULTS.minimapBorder);
  const [manualFrameStep, setManualFrameStep] = useState(false);
  const [frameStep, setFrameStep] = useState(VIDEO_DEFAULTS.frameStep);
  const [sampleIntervalSec, setSampleIntervalSec] = useState(VIDEO_DEFAULTS.sampleIntervalSec);
  const [minScore, setMinScore] = useState(VIDEO_DEFAULTS.minScore);
  const [goodScore, setGoodScore] = useState(VIDEO_DEFAULTS.goodScore);
  const [maxJumpDistance, setMaxJumpDistance] = useState(VIDEO_DEFAULTS.maxJumpDistance);
  const [searchRadius, setSearchRadius] = useState(VIDEO_DEFAULTS.searchRadius);
  const [relockScore, setRelockScore] = useState(VIDEO_DEFAULTS.relockScore);
  const [allowGlobalRelock, setAllowGlobalRelock] = useState(VIDEO_DEFAULTS.allowGlobalRelock);
  const [smoothing, setSmoothing] = useState(VIDEO_DEFAULTS.smoothing);
  const [fastMode, setFastMode] = useState(VIDEO_DEFAULTS.fastMode);
  const [saveFrameDebug, setSaveFrameDebug] = useState(VIDEO_DEFAULTS.saveFrameDebug);
  const [maxFrames, setMaxFrames] = useState("");
  const [debugVideo, setDebugVideo] = useState(VIDEO_DEFAULTS.debugVideo);

  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<VideoJobStatusResponse | null>(null);
  const [result, setResult] = useState<VideoJobResultResponse | null>(null);
  const [selected, setSelected] = useState<VideoTrackingPathPoint | null>(null);
  const [frameDebug, setFrameDebug] = useState<VideoFrameDebugUrls | null>(null);
  const [frameDebugMissing, setFrameDebugMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (maps.length && !maps.some((m) => m.mapId === mapId)) {
      const first = maps.find((m) => m.exists) ?? maps[0];
      setMapId(first.mapId);
    }
  }, [maps, mapId]);

  useEffect(() => {
    if (!videoFile) {
      setVideoUrl(null);
      setVideoMeta({ durationSec: 0, width: 0, height: 0 });
      return;
    }
    const url = URL.createObjectURL(videoFile);
    setVideoUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [videoFile]);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const estimatedSamples = useMemo(() => {
    if (videoMeta.durationSec <= 0) return null;
    if (manualFrameStep && frameStep > 0) {
      const fpsGuess = 60;
      const total = Math.ceil(videoMeta.durationSec * fpsGuess);
      return Math.max(1, Math.ceil(total / frameStep));
    }
    return Math.max(1, Math.ceil(videoMeta.durationSec / sampleIntervalSec));
  }, [videoMeta.durationSec, manualFrameStep, frameStep, sampleIntervalSec]);

  const etaHint = useMemo(() => {
    if (!estimatedSamples) return null;
    const secPerSample = fastMode ? 2.5 : 8;
    const sec = estimatedSamples * secPerSample;
    if (sec < 60) return `~${Math.ceil(sec)} с`;
    return `~${Math.ceil(sec / 60)} мин`;
  }, [estimatedSamples, fastMode]);

  const samplingLabel = manualFrameStep
    ? `frame_step = ${frameStep}`
    : `авто (~1 кадр / ${sampleIntervalSec} с по FPS видео)`;

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const pollJob = (id: string) => {
    stopPolling();
    pollRef.current = setInterval(() => {
      void api
        .getMinimapVideoJob(id)
        .then(async (st) => {
          setJobStatus(st);
          if (st.status === "completed") {
            stopPolling();
            const res = await api.getMinimapVideoJobResult(id);
            setResult(res);
            setBusy(false);
            if (res.path.length) setSelected(res.path[0]);
          } else if (st.status === "failed") {
            stopPolling();
            setBusy(false);
            setErr(st.error ?? "Анализ видео завершился с ошибкой");
          }
        })
        .catch((e) => {
          stopPolling();
          setBusy(false);
          setErr(e instanceof Error ? e.message : String(e));
        });
    }, 1500);
  };

  const onStart = async () => {
    setErr(null);
    setResult(null);
    setJobStatus(null);
    setFrameDebug(null);
    setFrameDebugMissing(false);
    setSelected(null);
    if (!videoFile) {
      setErr("Загрузите видео.");
      return;
    }
    const selectedMap = maps.find((m) => m.mapId === mapId);
    if (selectedMap && !selectedMap.exists) {
      setErr(`Файл карты не найден: ${selectedMap.mapPath}`);
      return;
    }

    const form = new FormData();
    form.append("file", videoFile);
    form.append("mapId", mapId);
    form.append("minimapX", String(minimapX));
    form.append("minimapY", String(minimapY));
    form.append("minimapSize", String(minimapSize));
    form.append("minimapBorder", String(minimapBorder));
    form.append("frameStep", String(manualFrameStep ? frameStep : 0));
    form.append("sampleIntervalSec", String(sampleIntervalSec));
    form.append("minScore", String(minScore));
    form.append("goodScore", String(goodScore));
    form.append("maxJumpDistance", String(maxJumpDistance));
    form.append("searchRadius", String(searchRadius));
    form.append("allowGlobalRelock", String(allowGlobalRelock));
    form.append("relockScore", String(relockScore));
    form.append("smoothing", String(smoothing));
    form.append("fastMode", String(fastMode));
    form.append("saveFrameDebug", String(saveFrameDebug));
    form.append("debugVideo", String(debugVideo));
    if (maxFrames.trim()) form.append("maxFrames", maxFrames.trim());

    setBusy(true);
    try {
      const created = await api.createMinimapVideoJob(form);
      setJobId(created.jobId);
      pollJob(created.jobId);
    } catch (e) {
      setBusy(false);
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const onSelectPoint = async (p: VideoTrackingPathPoint) => {
    setSelected(p);
    setFrameDebug(null);
    setFrameDebugMissing(false);
    if (!jobId) return;
    try {
      const dbg = await api.getMinimapVideoFrameDebug(jobId, p.frameIndex);
      setFrameDebug(dbg);
    } catch {
      setFrameDebugMissing(true);
    }
  };

  const mapImageUrl = result ? api.minimapAssetUrl(result.debug.mapImageUrl) : null;

  return (
    <div className={styles.videoLayout}>
      <aside className={styles.panel}>
        <h3>Анализ видео</h3>
        <label className={styles.label}>
          Видео
          <input
            className={styles.fileInput}
            type="file"
            accept="video/mp4,video/quicktime,video/x-matroska,video/webm,video/avi,.mp4,.mov,.mkv,.webm,.avi"
            onChange={(e) => setVideoFile(e.target.files?.[0] ?? null)}
          />
        </label>
        {videoFile ? (
          <p className={styles.subtitle}>
            {videoFile.name} · {(videoFile.size / (1024 * 1024)).toFixed(1)} MB
            {videoMeta.width > 0
              ? ` · ${videoMeta.width}×${videoMeta.height} · ${videoMeta.durationSec.toFixed(1)} с`
              : ""}
          </p>
        ) : null}

        {videoUrl ? (
          <VideoUploadPreview
            videoUrl={videoUrl}
            minimapX={minimapX}
            minimapY={minimapY}
            minimapSize={minimapSize}
            onMeta={setVideoMeta}
          />
        ) : null}

        <label className={styles.label}>
          Карта
          <select value={mapId} onChange={(e) => setMapId(e.target.value)}>
            {maps.map((m) => (
              <option key={m.mapId} value={m.mapId} disabled={!m.exists}>
                {m.label} ({m.mapId}){m.exists ? "" : " — нет файла"}
              </option>
            ))}
          </select>
        </label>

        <p className={styles.subtitle}>
          Интервал выборки: для 120 FPS при «1/сек» сервер возьмёт frame_step ≈ 120
        </p>
        <div className={styles.presetRow}>
          {SAMPLE_PRESETS.map((p) => (
            <button
              key={p.sec}
              type="button"
              className={`${styles.presetBtn}${!manualFrameStep && sampleIntervalSec === p.sec ? ` ${styles.presetBtnActive}` : ""}`}
              onClick={() => {
                setManualFrameStep(false);
                setSampleIntervalSec(p.sec);
              }}
            >
              {p.label}
            </button>
          ))}
        </div>

        <label className={styles.toggleRow}>
          <input
            type="checkbox"
            checked={manualFrameStep}
            onChange={(e) => setManualFrameStep(e.target.checked)}
          />
          Ручной frame_step
        </label>
        {manualFrameStep ? (
          <label className={styles.label}>
            frame_step
            <input type="number" min={1} value={frameStep || ""} onChange={(e) => setFrameStep(Number(e.target.value))} />
          </label>
        ) : (
          <label className={styles.label}>
            интервал (сек)
            <input
              type="number"
              min={0.25}
              step={0.25}
              value={sampleIntervalSec}
              onChange={(e) => setSampleIntervalSec(Number(e.target.value))}
            />
          </label>
        )}

        {estimatedSamples ? (
          <p className={styles.subtitle}>
            Оценка: ~{estimatedSamples} точек · {etaHint ?? ""} · {samplingLabel}
            {fastMode ? " · быстрый режим" : ""}
          </p>
        ) : null}

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
            <input type="number" step="0.01" value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} />
          </label>
          <label className={styles.label}>
            good_score
            <input type="number" step="0.01" value={goodScore} onChange={(e) => setGoodScore(Number(e.target.value))} />
          </label>
          <label className={styles.label}>
            max_jump
            <input
              type="number"
              value={maxJumpDistance}
              onChange={(e) => setMaxJumpDistance(Number(e.target.value))}
            />
          </label>
          <label className={styles.label}>
            search_radius
            <input type="number" value={searchRadius} onChange={(e) => setSearchRadius(Number(e.target.value))} />
          </label>
          <label className={styles.label}>
            relock_score
            <input type="number" step="0.01" value={relockScore} onChange={(e) => setRelockScore(Number(e.target.value))} />
          </label>
          <label className={styles.label}>
            max_frames
            <input value={maxFrames} placeholder="все" onChange={(e) => setMaxFrames(e.target.value)} />
          </label>
        </div>

        <label className={styles.toggleRow}>
          <input type="checkbox" checked={fastMode} onChange={(e) => setFastMode(e.target.checked)} />
          Быстрый режим (реже шаг поиска, меньше окон)
        </label>
        <label className={styles.toggleRow}>
          <input type="checkbox" checked={allowGlobalRelock} onChange={(e) => setAllowGlobalRelock(e.target.checked)} />
          allow_global_relock
        </label>
        <label className={styles.toggleRow}>
          <input type="checkbox" checked={smoothing} onChange={(e) => setSmoothing(e.target.checked)} />
          smoothing
        </label>
        <label className={styles.toggleRow}>
          <input type="checkbox" checked={saveFrameDebug} onChange={(e) => setSaveFrameDebug(e.target.checked)} />
          Сохранять debug кадры (медленнее)
        </label>
        <label className={styles.toggleRow}>
          <input type="checkbox" checked={debugVideo} onChange={(e) => setDebugVideo(e.target.checked)} />
          debug_video
        </label>
        <button type="button" className={styles.btn} disabled={busy} onClick={() => void onStart()}>
          {busy ? "Анализ…" : "Запустить анализ"}
        </button>
        {jobId && result ? (
          <a className={styles.linkBtn} href={api.minimapAssetUrl(result.debug.resultJsonUrl)} download>
            Скачать JSON
          </a>
        ) : null}
        {jobId && result?.debug.debugVideoUrl ? (
          <a className={styles.linkBtn} href={api.minimapAssetUrl(result.debug.debugVideoUrl)} download>
            Скачать debug-видео
          </a>
        ) : null}
        {err ? <div className={styles.err}>{err}</div> : null}
      </aside>

      <main className={styles.panel}>
        {jobStatus ? (
          <JobMetrics jobStatus={jobStatus} />
        ) : (
          <p className={styles.subtitle}>Загрузите видео, проверьте crop и запустите анализ. {samplingLabel}</p>
        )}

        {result && mapImageUrl ? (
          <>
            <TrajectoryMap
              mapUrl={mapImageUrl}
              points={result.path}
              selected={selected}
              onSelect={(p) => void onSelectPoint(p)}
            />
            <div className={styles.timeline}>
              {result.path.map((p) => (
                <button
                  key={p.frameIndex}
                  type="button"
                  className={styles.timelineTick}
                  style={{ background: STATUS_COLORS[p.status] ?? "#64748b" }}
                  title={`#${p.frameIndex} t=${p.timestampSec.toFixed(1)}s ${p.status} score=${p.score.toFixed(3)}`}
                  onClick={() => void onSelectPoint(p)}
                />
              ))}
            </div>
            {selected ? (
              <div className={styles.metrics}>
                <span>
                  frame {selected.frameIndex} · t={selected.timestampSec.toFixed(1)}s · {selected.status}
                </span>
                <span>score {selected.score.toFixed(3)}</span>
                <span>ws {selected.windowSize ?? "—"}</span>
                <span>jump {selected.jumpDistance?.toFixed(1) ?? "—"}</span>
              </div>
            ) : null}
            {frameDebugMissing && !frameDebug ? (
              <p className={styles.subtitle}>
                Debug кадры не сохранялись. Включите «Сохранять debug кадры» при следующем запуске.
              </p>
            ) : null}
            {frameDebug ? (
              <div className={styles.debugGrid}>
                {[
                  ["Кадр", frameDebug.frameWithCropUrl],
                  ["Миникарта", frameDebug.minimapRawUrl],
                  ["Processed", frameDebug.minimapProcessedUrl],
                  ["Match", frameDebug.mapMatchUrl],
                  ["Patch", frameDebug.matchedPatchUrl],
                  ["Panel", frameDebug.debugPanelUrl],
                ].map(([label, url]) => (
                  <figure key={label} className={styles.previewCard}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={api.minimapAssetUrl(url)} alt={label} />
                    <figcaption className={styles.previewCaption}>{label}</figcaption>
                  </figure>
                ))}
              </div>
            ) : null}
            <figure className={styles.previewCard}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={api.minimapAssetUrl(result.debug.trajectoryImageUrl)} alt="Trajectory debug" />
              <figcaption className={styles.previewCaption}>trajectory_map.jpg</figcaption>
            </figure>
          </>
        ) : null}
      </main>
    </div>
  );
}

function JobMetrics({ jobStatus }: { jobStatus: VideoJobStatusResponse }) {
  return (
    <div className={styles.metrics}>
      <span>
        статус: <code className={styles.mono}>{jobStatus.status}</code>
      </span>
      <span>
        кадры: {jobStatus.processedFrames}/{jobStatus.totalFramesToProcess}
      </span>
      <span>t={jobStatus.currentTimestampSec.toFixed(1)}s</span>
      <span>accepted: {jobStatus.acceptedPoints}</span>
      <span>rejected: {jobStatus.rejectedJumps}</span>
      <span>avg score: {jobStatus.averageScore.toFixed(3)}</span>
    </div>
  );
}
