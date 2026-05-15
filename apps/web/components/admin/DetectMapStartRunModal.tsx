"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { DetectMapStartRunDefaults, DetectMapStartRunRequest, JobRecord } from "../../lib/types";
import styles from "./management.module.css";

export type DetectMapStartRunMode = "full" | "rings";

type FormState = DetectMapStartRunDefaults;
type RunTab = "params" | "guide" | "progress";
type PresetId = "standard" | "fast" | "veryFast";
type TaskProgressItem = {
  id: string;
  label: string;
  enabled?: boolean;
  status?: string;
  progressPercent?: number;
  elapsedSec?: number;
  remainingSec?: number | null;
  stage?: string | null;
};
type TeamProgressItem = {
  slot: number;
  label?: string;
  status?: string;
  progressPercent?: number;
  frame?: number | null;
  extra?: string;
};

const TASKS: Array<{ key: keyof Pick<FormState, "runStartDetection" | "runTeamDetection" | "runEliminationDetection" | "runRingDetection" | "runCameraTracking">; label: string; hint: string }> = [
  { key: "runStartDetection", label: "Старт карты", hint: "Ищет момент перехода в карту и название карты." },
  { key: "runTeamDetection", label: "Команды", hint: "OCR слотов TEAM_1..TEAM_20." },
  { key: "runEliminationDetection", label: "Выбывания", hint: "Уточняет тайминги eliminated по OCR." },
  { key: "runRingDetection", label: "Кольца", hint: "Ищет тайминги и геометрию зон." },
  { key: "runCameraTracking", label: "Камера", hint: "Строит Camreman/camera rows из колец." },
];

const PARAM_GUIDE: Array<{ group: string; name: keyof FormState; recommended: string; why: string }> = [
  { group: "Старт карты", name: "frameStep", recommended: "120", why: "Дает быстрый грубый проход: для 60 FPS это проверка примерно раз в 2 секунды." },
  { group: "Параллельность", name: "teamWorkers", recommended: "1-4", why: "Параллелит обработку команд внутри одного видео; ставьте выше для OCR выбываний, но не путайте с video-workers." },
  { group: "Старт карты", name: "coarseJumpFrames", recommended: "3000", why: "Быстро перепрыгивает пустое начало VOD; около 50 секунд на 60 FPS." },
  { group: "Старт карты", name: "rollbackStepFrames", recommended: "100", why: "После первого уверенного кадра откатывается достаточно мелко, чтобы не промахнуться по началу." },
  { group: "Старт карты", name: "refineWindowFrames", recommended: "300", why: "Окно ±5 секунд на 60 FPS, обычно покрывает ошибку coarse-поиска." },
  { group: "Старт карты", name: "startRefineStepFrames", recommended: "3", why: "Точный refine без полного покадрового сканирования." },
  { group: "Старт карты", name: "stableSeconds", recommended: "5.0", why: "Защищает от случайных OCR/камерных всплесков: карта и камера должны быть стабильны." },
  { group: "OCR", name: "ocrMinConfidence", recommended: "0.62", why: "Баланс между fuzzy-match OCR названия карты и ложными срабатываниями." },
  { group: "OCR", name: "cameraMinConfidence", recommended: "0.58", why: "Чуть мягче OCR, потому что камера карты распознается по визуальному состоянию." },
  { group: "OCR", name: "textSummaryTopN", recommended: "3", why: "Достаточно для диагностики лучших строк без перегруза JSON-отчета." },
  { group: "OCR", name: "textOcrMinConfidence", recommended: "0.0", why: "Сохраняем все наблюдения, а фильтрацию делаем на этапе анализа." },
  { group: "OCR", name: "textZonesMaxEnabled", recommended: "5000", why: "Фактически включает все зоны; полезно при полном наборе OCR зон." },
  { group: "Кольца", name: "ringCoarseSec", recommended: "5.0", why: "Крупный шаг достаточно быстр и не теряет длинные countdown/closing события." },
  { group: "Кольца", name: "ringRollbackSec", recommended: "5.0", why: "Откат вокруг найденного события совпадает с coarse шагом и держит предсказуемую точность." },
  { group: "Кольца", name: "ringRefineWindowSec", recommended: "5.0", why: "Окно уточнения вокруг OCR-якоря, обычно покрывает дрожание распознавания." },
  { group: "Кольца", name: "ringRefineStepSec", recommended: "1.0", why: "Точность тайминга до секунды без чрезмерного числа OCR вызовов." },
  { group: "Кольца", name: "ringStableSeconds", recommended: "1.0", why: "Для ring text достаточно короткой стабильности: события на HUD не требуют 5 секунд." },
  { group: "Кольца", name: "ringGeometryWindowSeconds", recommended: "2.0", why: "Несколько кадров вокруг события сглаживают шум геометрии кольца." },
  { group: "Кольца", name: "ringGeometryStepSec", recommended: "1.0", why: "Хороший компромисс для оценки окружности и камеры." },
  { group: "Выбывания", name: "elimCoarseSec", recommended: "5.0", why: "Выбывания ищутся по крупной сетке перед уточнением." },
  { group: "Выбывания", name: "elimRefineSec", recommended: "5.0", why: "Уточняет момент назад от первого OCR-найденного eliminated." },
  { group: "Выбывания", name: "elimRefineStepSec", recommended: "1.0", why: "Секундная точность достаточна для статистики и не делает OCR слишком дорогим." },
  { group: "Камера", name: "cameraTrackingMode", recommended: "geometry", why: "Основной стабильный режим; edge_residual остается экспериментальным." },
  { group: "Отладка", name: "debug", recommended: "false", why: "Включать только при разборе проблем, иначе много артефактов на диске." },
  { group: "Отладка", name: "dryRun", recommended: "false", why: "Для реального запуска нужно писать SQLite; dry-run полезен только для проверки команды." },
];

const DETECT_PRESETS: Array<{ id: PresetId; label: string; description: string; patch: Partial<FormState> }> = [
  {
    id: "standard",
    label: "Стандарт",
    description: "Максимально близко к текущим дефолтам: точнее, но дольше.",
    patch: {
      fastApprox: false,
      fastApproxSmallSteps: false,
      videoWorkers: 1,
      teamWorkers: 2,
      frameStep: 120,
      coarseJumpFrames: 3000,
      rollbackStepFrames: 100,
      refineWindowFrames: 300,
      startRefineStepFrames: 3,
      stableSeconds: 5,
      ringCoarseSec: 5,
      ringRollbackSec: 5,
      ringRefineWindowSec: 5,
      ringRefineStepSec: 1,
      ringGeometryWindowSeconds: 2,
      ringGeometryStepSec: 1,
      elimCoarseSec: 5,
      elimRefineSec: 5,
      elimRefineStepSec: 1,
    },
  },
  {
    id: "fast",
    label: "Быстрый",
    description: "Меньше OCR-точек на командах/кольцах, хорош для первичного прогона.",
    patch: {
      fastApprox: true,
      fastApproxSmallSteps: false,
      videoWorkers: 1,
      teamWorkers: 4,
      frameStep: 180,
      coarseJumpFrames: 1800,
      rollbackStepFrames: 120,
      refineWindowFrames: 240,
      startRefineStepFrames: 6,
      stableSeconds: 3,
      ringCoarseSec: 8,
      ringRollbackSec: 5,
      ringRefineWindowSec: 4,
      ringRefineStepSec: 1.5,
      ringGeometryWindowSeconds: 2,
      ringGeometryStepSec: 1.5,
      elimCoarseSec: 8,
      elimRefineSec: 4,
      elimRefineStepSec: 1.5,
    },
  },
  {
    id: "veryFast",
    label: "Очень быстрый",
    description: "Для быстрой проверки видео: меньше refine и геометрических сэмплов.",
    patch: {
      fastApprox: true,
      fastApproxSmallSteps: false,
      videoWorkers: 1,
      teamWorkers: 6,
      frameStep: 300,
      coarseJumpFrames: 3000,
      rollbackStepFrames: 180,
      refineWindowFrames: 180,
      startRefineStepFrames: 12,
      stableSeconds: 2,
      ringCoarseSec: 12,
      ringRollbackSec: 6,
      ringRefineWindowSec: 3,
      ringRefineStepSec: 2,
      ringGeometryWindowSeconds: 1.5,
      ringGeometryStepSec: 1.5,
      elimCoarseSec: 12,
      elimRefineSec: 3,
      elimRefineStepSec: 2,
    },
  },
];

function numIn(v: string, fallback: number): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function normalizeRelPath(p: string): string {
  return p.trim().replace(/\\/g, "/");
}

function formatDuration(sec: number): string {
  const safe = Math.max(0, Math.round(sec));
  const m = Math.floor(safe / 60);
  const s = safe % 60;
  return `${m} мин ${s.toString().padStart(2, "0")} с`;
}

function statusLabel(status: string | undefined): string {
  const s = String(status ?? "pending");
  if (s === "pending") return "ожидание";
  if (s === "running") return "в процессе";
  if (s === "completed") return "готово";
  if (s === "error" || s === "failed") return "ошибка";
  if (s === "skipped") return "пропущено";
  return s;
}

function estimatePresetSeconds(form: FormState | null, patch: Partial<FormState>, durationSec: number | null | undefined): number {
  const f = { ...(form ?? {}), ...patch } as FormState;
  const videoSec = Number.isFinite(Number(durationSec)) && Number(durationSec) > 0 ? Number(durationSec) : 20 * 60;
  const fps = 60;
  const startSamples =
    f.runStartDetection === false
      ? 0
      : Math.ceil(videoSec / Math.max(0.5, Number(f.frameStep || 120) / fps)) +
        Math.ceil((Number(f.refineWindowFrames || 300) * 2) / Math.max(1, Number(f.startRefineStepFrames || 3)));
  const teamSamples =
    f.runTeamDetection === false ? 0 : Math.ceil(Math.min(20, videoSec) / Math.max(0.5, Number(f.frameStep || 120) / fps)) * 20;
  const elimSamples =
    f.runEliminationDetection === false
      ? 0
      : 20 *
        (Math.ceil(videoSec / Math.max(0.5, Number(f.elimCoarseSec || 5))) +
          Math.ceil((Number(f.elimRefineSec || 5) * 2) / Math.max(0.25, Number(f.elimRefineStepSec || 1))));
  const ringSamples =
    f.runRingDetection === false
      ? 0
      : 5 *
        (Math.ceil(videoSec / Math.max(0.5, Number(f.ringCoarseSec || 5))) +
          Math.ceil((Number(f.ringRefineWindowSec || 5) * 2) / Math.max(0.25, Number(f.ringRefineStepSec || 1))) +
          Math.ceil(Number(f.ringGeometryWindowSeconds || 2) / Math.max(0.25, Number(f.ringGeometryStepSec || 1))));
  const cameraSamples = f.runCameraTracking === false ? 0 : Math.ceil(videoSec / Math.max(0.25, Number(f.ringGeometryStepSec || 1)));
  const weightedOps = startSamples * 0.018 + teamSamples * 0.055 + elimSamples * 0.045 + ringSamples * 0.05 + cameraSamples * 0.012;
  return Math.max(8, weightedOps + 5);
}

function DetectPipeline({ form, mode, onToggle }: { form: FormState | null; mode: DetectMapStartRunMode; onToggle: (key: (typeof TASKS)[number]["key"], checked: boolean) => void }) {
  const steps = [
    { id: "start", key: "runStartDetection", label: "Старт карты" },
    { id: "teams", key: "runTeamDetection", label: "Команды" },
    { id: "elims", key: "runEliminationDetection", label: "Выбывания" },
    { id: "rings", key: "runRingDetection", label: "Кольца" },
    { id: "camera", key: "runCameraTracking", label: "Камера" },
  ];
  return (
    <div className={styles.detectPipeline}>
      {steps.map((s, i) => {
        const checked = form ? Boolean(form[s.key as keyof FormState]) : true;
        const dim = !checked || (mode === "rings" && i < 3);
        return (
          <div key={s.id} className={styles.detectPipelineInner}>
            <label className={`${styles.detectPipeStep} ${dim ? styles.detectPipeStepDim : styles.detectPipeStepHot}`}>
              <input
                type="checkbox"
                checked={checked}
                disabled={!form}
                onChange={(e) => onToggle(s.key as (typeof TASKS)[number]["key"], e.target.checked)}
              />
              <span className={styles.detectPipeStepLabel}>{s.label}</span>
            </label>
            {i < steps.length - 1 ? <span className={styles.detectPipeArrow}>→</span> : null}
          </div>
        );
      })}
    </div>
  );
}

export function DetectorJobProgress({
  job,
  compact = false,
  onControl,
}: {
  job: JobRecord | null;
  compact?: boolean;
  onControl?: (jobId: string, action: "pause" | "resume" | "cancel") => void;
}) {
  const payload = job?.payload ?? {};
  const taskProgress = Array.isArray(payload.taskProgress) ? (payload.taskProgress as TaskProgressItem[]) : [];
  const teamProgress = Array.isArray(payload.teamProgress) ? (payload.teamProgress as TeamProgressItem[]) : [];
  const progress = payload.progress && typeof payload.progress === "object" ? (payload.progress as Record<string, unknown>) : null;
  const recentLogs = Array.isArray(payload.recentLogs) ? payload.recentLogs : [];
  const control = payload.control && typeof payload.control === "object" ? (payload.control as Record<string, unknown>) : null;
  const controlAction = String(control?.action ?? "run");
  const pid = payload.pid ?? payload.killedPid;

  return (
    <div className={compact ? styles.detectorProgressCompact : undefined}>
      {job ? (
        <>
          <div className={styles.progressBar} aria-label={`Прогресс ${job.progressPercent}%`}>
            <span style={{ width: `${Math.max(0, Math.min(100, job.progressPercent))}%` }} />
          </div>
          <p className={styles.hint}>
            Статус: <strong>{job.status}</strong> · этап: <code className={styles.mono}>{job.currentAction ?? "—"}</code> ·{" "}
            {job.progressPercent.toFixed(1)}%
          </p>
          <p className={styles.hint}>
            PID: <code className={styles.mono}>{pid == null ? "—" : String(pid)}</code> · heartbeat:{" "}
            <code className={styles.mono}>
              {job.lastHeartbeatAt ? new Date(job.lastHeartbeatAt).toLocaleTimeString() : "—"}
            </code>
          </p>
          {onControl && job.status === "running" ? (
            <div className={styles.rowActions}>
              <button
                type="button"
                className={styles.btn}
                onClick={() => onControl(job.id, controlAction === "pause" ? "resume" : "pause")}
              >
                {controlAction === "pause" ? "Возобновить" : "Пауза"}
              </button>
              <button type="button" className={styles.btn} onClick={() => onControl(job.id, "cancel")}>
                Отменить
              </button>
            </div>
          ) : null}
          <div className={styles.taskProgressGrid}>
            {taskProgress.map((task) => {
              const pct = Math.max(0, Math.min(100, Number(task.progressPercent ?? 0)));
              return (
                <article key={task.id} className={styles.taskProgressCard}>
                  <div className={styles.taskProgressHead}>
                    <strong>{task.label}</strong>
                    <span>{statusLabel(task.status)}</span>
                  </div>
                  <div className={styles.progressBar} aria-label={`${task.label}: ${pct}%`}>
                    <span style={{ width: `${pct}%` }} />
                  </div>
                  <p className={styles.hint}>
                    Прошло: <code className={styles.mono}>{formatDuration(Number(task.elapsedSec ?? 0))}</code> · осталось:{" "}
                    <code className={styles.mono}>
                      {task.remainingSec == null ? "—" : formatDuration(Number(task.remainingSec))}
                    </code>
                  </p>
                  {task.stage ? <p className={styles.mono}>{task.stage}</p> : null}
                </article>
              );
            })}
          </div>
          {teamProgress.length ? (
            <>
              <div className={styles.detectCardTitle}>Команды</div>
              <div className={styles.teamProgressGrid}>
                {teamProgress.map((team) => {
                  const pct = Math.max(0, Math.min(100, Number(team.progressPercent ?? 0)));
                  return (
                    <article key={team.slot} className={styles.teamProgressCard}>
                      <div className={styles.taskProgressHead}>
                        <strong>{team.label ?? `TEAM_${team.slot}`}</strong>
                        <span>{statusLabel(team.status)}</span>
                      </div>
                      <div className={styles.progressBar} aria-label={`${team.label ?? team.slot}: ${pct}%`}>
                        <span style={{ width: `${pct}%` }} />
                      </div>
                      {team.extra ? <p className={styles.mono}>{team.extra}</p> : null}
                    </article>
                  );
                })}
              </div>
            </>
          ) : null}
          {progress ? (
            <p className={styles.hint}>
              Кадр: <code className={styles.mono}>{String(progress.frame ?? "—")}</code> /{" "}
              <code className={styles.mono}>{String(progress.totalFrames ?? "—")}</code>
            </p>
          ) : null}
          {recentLogs.length ? (
            <details className={styles.progressConsole} open={!compact || recentLogs.length <= 8}>
              <summary>Консоль</summary>
              <pre className={styles.detectLog}>
                {recentLogs
                  .slice(compact ? -6 : -16)
                  .map((entry) =>
                    entry && typeof entry === "object"
                      ? `${String((entry as Record<string, unknown>).stream ?? "")}: ${String((entry as Record<string, unknown>).line ?? "")}`
                      : String(entry)
                  )
                  .join("\n")}
              </pre>
            </details>
          ) : null}
          {job.errors?.length ? <div className={styles.errBox}>{job.errors.join("\n")}</div> : null}
        </>
      ) : (
        <p className={styles.hint}>Запустите анализ, чтобы увидеть прогресс.</p>
      )}
    </div>
  );
}

export function DetectMapStartRunModal({
  open,
  videoName,
  mode,
  recordsDir,
  durationSec,
  prefill,
  onClose,
  onStarted,
}: {
  open: boolean;
  videoName: string | null;
  mode: DetectMapStartRunMode;
  recordsDir: string;
  durationSec?: number | null;
  prefill?: { startTimestampSec?: number | null; mapName?: string | null };
  onClose: () => void;
  onStarted: (info: { command: string; pid?: number }) => void;
}) {
  const [form, setForm] = useState<FormState | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [submitErr, setSubmitErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<RunTab>("params");
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobRecord | null>(null);

  useEffect(() => {
    if (!open || !videoName) {
      setForm(null);
      setLoadErr(null);
      setSubmitErr(null);
      setTab("params");
      setJobId(null);
      setJob(null);
      return;
    }
    let cancel = false;
    setLoadErr(null);
    const startSec = prefill?.startTimestampSec;
    const mapNm = prefill?.mapName;
    void api
      .getMapStartRunDefaults()
      .then((d) => {
        if (cancel) return;
        let merged: FormState = {
          ...d,
          recordsDir: normalizeRelPath(recordsDir).replace(/\/+$/, "") || d.recordsDir,
        };
        if (startSec != null && Number.isFinite(Number(startSec))) {
          merged = { ...merged, assumeStartSec: Number(startSec) };
        }
        const mn = typeof mapNm === "string" ? mapNm.trim() : "";
        if (mn) merged = { ...merged, assumeMapName: mn };
        setForm(merged);
      })
      .catch((e) => {
        if (!cancel) setLoadErr(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancel = true;
    };
  }, [open, videoName, recordsDir, mode, prefill?.startTimestampSec, prefill?.mapName]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const upd = useCallback(<K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((f) => (f ? { ...f, [key]: value } : f));
  }, []);

  const toggleTask = useCallback((key: (typeof TASKS)[number]["key"], checked: boolean) => {
    setForm((f) => {
      if (!f) return f;
      const next = { ...f, [key]: checked };
      if (key === "runTeamDetection" && !checked) next.runEliminationDetection = false;
      if (key === "runRingDetection" && !checked) next.runCameraTracking = false;
      return next;
    });
  }, []);

  const applyPreset = useCallback((preset: (typeof DETECT_PRESETS)[number]) => {
    setForm((f) => (f ? { ...f, ...preset.patch } : f));
  }, []);

  const controlJob = useCallback(async (targetJobId: string, action: "pause" | "resume" | "cancel") => {
    const next = await api.controlJob(targetJobId, action);
    setJob(next);
  }, []);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const load = async () => {
      try {
        const next = await api.getJob(jobId);
        if (!cancelled) setJob(next);
      } catch {
        // Progress polling should not close the run dialog.
      }
    };
    void load();
    const t = setInterval(() => void load(), 1500);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [jobId]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!videoName || !form) return;
    setSubmitErr(null);
    setBusy(true);
    try {
      const body: DetectMapStartRunRequest = {
        ...form,
        videoName,
        persistRingsOnly: mode === "rings",
      };
      const r = await api.postMapStartRun(body);
      setJobId(r.jobId);
      setTab("progress");
      onStarted({ command: r.command, pid: r.pid });
    } catch (err) {
      setSubmitErr(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (!open || !videoName) return null;

  const ringsOnly = mode === "rings";
  const submitLabel = ringsOnly ? "Запустить только кольца и камеру" : "Запустить полный анализ";

  return (
    <div
      className={`${styles.modalBackdrop} ${styles.modalBackdropTop}`}
      role="presentation"
      onClick={(ev) => {
        if (ev.target === ev.currentTarget) onClose();
      }}
    >
      <div
        className={`paper-broadcast paper-broadcast--theme-dark paper-broadcast--palette-virtus ${styles.modalPanel} ${styles.detectModalShell}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="detect-run-title"
        onClick={(ev) => ev.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <div>
            <h3 id="detect-run-title">
              Детектор карты:{" "}
              <span className={`${styles.detectModePill} ${ringsOnly ? styles.detectModePillRings : styles.detectModePillFull}`}>
                {ringsOnly ? "только кольца" : "полный прогон"}
              </span>
            </h3>
            <p className={styles.mono} style={{ margin: "6px 0 0", fontSize: 12 }}>
              {videoName}
            </p>
            <p className={styles.hint} style={{ margin: "8px 0 0" }}>
              Фоновый запуск{" "}
              <code className={styles.mono}>tools/algs-collector/detect_map_start.py</code> на хосте API. При необходимости
              задайте <code className={styles.mono}>PYTHON</code> / <code className={styles.mono}>PYTHON_BIN</code>.
            </p>
          </div>
          <button type="button" className={`${styles.btn} ${styles.closeBtn}`} onClick={onClose}>
            Закрыть
          </button>
        </div>

        <DetectPipeline form={form} mode={mode} onToggle={toggleTask} />

        <form className={styles.modalBody} onSubmit={onSubmit}>
          <div className={styles.detectTabs} role="tablist" aria-label="Разделы запуска детектора">
            {[
              ["params", "Параметры"],
              ["guide", "Гайд"],
              ["progress", "Прогресс"],
            ].map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={`${styles.detectTab} ${tab === id ? styles.detectTabActive : ""}`}
                onClick={() => setTab(id as RunTab)}
              >
                {label}
              </button>
            ))}
          </div>

          {ringsOnly ? (
            <div className={styles.detectCallout}>
              Режим с флагом <code className={styles.mono}>--persist-rings-only</code>: в БД обновляются в основном{" "}
              <strong>Rings</strong> и трекинг <strong>камеры</strong> для уже существующей строки{" "}
              <code className={styles.mono}>map_start_detection</code>. Убедитесь, что запись для видео уже есть, и при
              необходимости задайте <code className={styles.mono}>assume-start-sec</code> / карту ниже.
            </div>
          ) : null}

          {loadErr ? <div className={styles.errBox}>{loadErr}</div> : null}
          {submitErr ? <div className={styles.errBox}>{submitErr}</div> : null}

          {tab === "guide" ? (
            <div className={styles.detectGuideGrid}>
              {PARAM_GUIDE.map((item) => (
                <article key={`${item.group}-${String(item.name)}`} className={styles.detectGuideCard}>
                  <div className={styles.detectGuideGroup}>{item.group}</div>
                  <h4>{String(item.name)}</h4>
                  <p>
                    <strong>Рекомендуем:</strong> <code className={styles.mono}>{item.recommended}</code>
                  </p>
                  <p>{item.why}</p>
                </article>
              ))}
            </div>
          ) : null}

          {tab === "progress" ? (
            <div className={styles.detectCard}>
              <div className={styles.detectCardTitle}>Прогресс выполнения</div>
              {jobId ? <p className={styles.mono}>job: {jobId}</p> : null}
              <DetectorJobProgress job={job} onControl={controlJob} />
            </div>
          ) : null}

          {form && tab === "params" ? (
            <>
              <div className={styles.detectPresetGrid}>
                {DETECT_PRESETS.map((preset) => {
                  const estimate = estimatePresetSeconds(form, preset.patch, durationSec);
                  return (
                    <button
                      key={preset.id}
                      type="button"
                      className={styles.detectPresetCard}
                      onClick={() => applyPreset(preset)}
                    >
                      <strong>{preset.label}</strong>
                      <span>{preset.description}</span>
                      <small>
                        Оценка для {formatDuration(durationSec ?? 20 * 60)} видео, 20 команд, 5 кругов:{" "}
                        <b>{formatDuration(estimate)}</b>
                      </small>
                    </button>
                  );
                })}
              </div>

              <details className={styles.detectDetails}>
                <summary>Детально</summary>
                <div className={styles.detectDetailsBody}>
                  <div className={styles.detectCard}>
                    <div className={styles.detectCardTitle}>Каталог, БД и параллельность</div>
                    <div className={styles.detectGrid}>
                      <label>
                        Каталог записей
                        <input
                          value={form.recordsDir}
                          onChange={(e) => upd("recordsDir", normalizeRelPath(e.target.value))}
                        />
                      </label>
                      <label>
                        SQLite БД
                        <input value={form.dbPath} onChange={(e) => upd("dbPath", e.target.value.replace(/\\/g, "/"))} />
                      </label>
                      <label>
                        Параллельных видео (только лёгкие задачи)
                        <input
                          type="number"
                          min={1}
                          max={32}
                          value={form.videoWorkers}
                          onChange={(e) => upd("videoWorkers", numIn(e.target.value, 1))}
                        />
                      </label>
                      <label>
                        Параллельных команд
                        <input
                          type="number"
                          min={1}
                          max={20}
                          value={form.teamWorkers}
                          onChange={(e) => upd("teamWorkers", numIn(e.target.value, 1))}
                        />
                      </label>
                      <label className={styles.detectCheck}>
                        <input
                          type="checkbox"
                          checked={form.fastApprox}
                          onChange={(e) => upd("fastApprox", e.target.checked)}
                        />
                        fast-approx
                      </label>
                      <label className={styles.detectCheck}>
                        <input
                          type="checkbox"
                          checked={form.fastApproxSmallSteps}
                          onChange={(e) => upd("fastApproxSmallSteps", e.target.checked)}
                        />
                        fast-approx-small-steps
                      </label>
                    </div>
                  </div>

              <details className={styles.detectDetails} open={!ringsOnly}>
                <summary>Детекция старта карты (кадры, устойчивость, OCR)</summary>
                <div className={styles.detectDetailsBody}>
                  <div className={styles.detectGrid}>
                    <label>
                      frame-step
                      <input
                        type="number"
                        value={form.frameStep}
                        onChange={(e) => upd("frameStep", numIn(e.target.value, form.frameStep))}
                      />
                    </label>
                    <label>
                      coarse-jump-frames
                      <input
                        type="number"
                        value={form.coarseJumpFrames}
                        onChange={(e) => upd("coarseJumpFrames", numIn(e.target.value, form.coarseJumpFrames))}
                      />
                    </label>
                    <label>
                      rollback-step-frames
                      <input
                        type="number"
                        value={form.rollbackStepFrames}
                        onChange={(e) => upd("rollbackStepFrames", numIn(e.target.value, form.rollbackStepFrames))}
                      />
                    </label>
                    <label>
                      refine-window-frames
                      <input
                        type="number"
                        value={form.refineWindowFrames}
                        onChange={(e) => upd("refineWindowFrames", numIn(e.target.value, form.refineWindowFrames))}
                      />
                    </label>
                    <label>
                      start-refine-step-frames
                      <input
                        type="number"
                        value={form.startRefineStepFrames}
                        onChange={(e) =>
                          upd("startRefineStepFrames", numIn(e.target.value, form.startRefineStepFrames))
                        }
                      />
                    </label>
                    <label>
                      stable-seconds
                      <input
                        type="number"
                        step="0.1"
                        value={form.stableSeconds}
                        onChange={(e) => upd("stableSeconds", numIn(e.target.value, form.stableSeconds))}
                      />
                    </label>
                    <label>
                      ocr-min-confidence
                      <input
                        type="number"
                        step="0.01"
                        value={form.ocrMinConfidence}
                        onChange={(e) => upd("ocrMinConfidence", numIn(e.target.value, form.ocrMinConfidence))}
                      />
                    </label>
                    <label>
                      camera-min-confidence
                      <input
                        type="number"
                        step="0.01"
                        value={form.cameraMinConfidence}
                        onChange={(e) => upd("cameraMinConfidence", numIn(e.target.value, form.cameraMinConfidence))}
                      />
                    </label>
                    <label className={styles.detectCheck}>
                      <input
                        type="checkbox"
                        checked={form.disableStartDetection}
                        onChange={(e) => upd("disableStartDetection", e.target.checked)}
                      />
                      disable-start-detection
                    </label>
                    <label>
                      assume-start-sec
                      <input
                        type="number"
                        step="0.1"
                        value={form.assumeStartSec}
                        onChange={(e) => upd("assumeStartSec", numIn(e.target.value, form.assumeStartSec))}
                      />
                    </label>
                    <label>
                      assume-map-name
                      <input
                        value={form.assumeMapName}
                        onChange={(e) => upd("assumeMapName", e.target.value)}
                        placeholder="STORM POINT"
                      />
                    </label>
                  </div>
                </div>
              </details>

              <details className={styles.detectDetails} open={!ringsOnly}>
                <summary>OCR трекинга команд, выбывания, POV</summary>
                <div className={styles.detectDetailsBody}>
                  <div className={styles.detectGrid}>
                    <label>
                      text-json-dir
                      <input value={form.textJsonDir} onChange={(e) => upd("textJsonDir", e.target.value)} />
                    </label>
                    <label>
                      text-summary-top-n
                      <input
                        type="number"
                        value={form.textSummaryTopN}
                        onChange={(e) => upd("textSummaryTopN", numIn(e.target.value, form.textSummaryTopN))}
                      />
                    </label>
                    <label>
                      text-ocr-min-confidence
                      <input
                        type="number"
                        step="0.01"
                        value={form.textOcrMinConfidence}
                        onChange={(e) => upd("textOcrMinConfidence", numIn(e.target.value, form.textOcrMinConfidence))}
                      />
                    </label>
                    <label>
                      text-zones-max-enabled
                      <input
                        type="number"
                        value={form.textZonesMaxEnabled}
                        onChange={(e) => upd("textZonesMaxEnabled", numIn(e.target.value, form.textZonesMaxEnabled))}
                      />
                    </label>
                    <label>
                      text-zones-file (опц.)
                      <input
                        value={form.textZonesFile}
                        onChange={(e) => upd("textZonesFile", e.target.value)}
                        placeholder="путь к JSON зон OCR"
                      />
                    </label>
                    <label>
                      elim-coarse-sec
                      <input
                        type="number"
                        step="0.1"
                        value={form.elimCoarseSec}
                        onChange={(e) => upd("elimCoarseSec", numIn(e.target.value, form.elimCoarseSec))}
                      />
                    </label>
                    <label>
                      elim-refine-sec
                      <input
                        type="number"
                        step="0.1"
                        value={form.elimRefineSec}
                        onChange={(e) => upd("elimRefineSec", numIn(e.target.value, form.elimRefineSec))}
                      />
                    </label>
                    <label>
                      elim-refine-step-sec
                      <input
                        type="number"
                        step="0.05"
                        value={form.elimRefineStepSec}
                        onChange={(e) => upd("elimRefineStepSec", numIn(e.target.value, form.elimRefineStepSec))}
                      />
                    </label>
                    <label>
                      pov-screenshot-offset-sec
                      <input
                        type="number"
                        step="0.1"
                        value={form.povScreenshotOffsetSec}
                        onChange={(e) =>
                          upd("povScreenshotOffsetSec", numIn(e.target.value, form.povScreenshotOffsetSec))
                        }
                      />
                    </label>
                    <label>
                      pov-screenshot-dir
                      <input value={form.povScreenshotDir} onChange={(e) => upd("povScreenshotDir", e.target.value)} />
                    </label>
                    <label className={styles.detectCheck}>
                      <input
                        type="checkbox"
                        checked={form.stopOnFirstBoth}
                        onChange={(e) => upd("stopOnFirstBoth", e.target.checked)}
                      />
                      stop-on-first-both
                    </label>
                    <label className={styles.detectCheck}>
                      <input
                        type="checkbox"
                        checked={form.disableTeamDetection}
                        onChange={(e) => upd("disableTeamDetection", e.target.checked)}
                      />
                      disable-team-detection
                    </label>
                    <label className={styles.detectCheck}>
                      <input
                        type="checkbox"
                        checked={form.disableEliminationDetection}
                        onChange={(e) => upd("disableEliminationDetection", e.target.checked)}
                      />
                      disable-elimination-detection
                    </label>
                  </div>
                </div>
              </details>

              <details className={styles.detectDetails} open>
                <summary>Кольца (геометрия, откат, устойчивость)</summary>
                <div className={styles.detectDetailsBody}>
                  <div className={styles.detectGrid}>
                    <label>
                      ring-coarse-sec
                      <input
                        type="number"
                        step="0.1"
                        value={form.ringCoarseSec}
                        onChange={(e) => upd("ringCoarseSec", numIn(e.target.value, form.ringCoarseSec))}
                      />
                    </label>
                    <label>
                      ring-rollback-sec
                      <input
                        type="number"
                        step="0.1"
                        value={form.ringRollbackSec}
                        onChange={(e) => upd("ringRollbackSec", numIn(e.target.value, form.ringRollbackSec))}
                      />
                    </label>
                    <label>
                      ring-refine-window-sec
                      <input
                        type="number"
                        step="0.1"
                        value={form.ringRefineWindowSec}
                        onChange={(e) => upd("ringRefineWindowSec", numIn(e.target.value, form.ringRefineWindowSec))}
                      />
                    </label>
                    <label>
                      ring-refine-step-sec
                      <input
                        type="number"
                        step="0.05"
                        value={form.ringRefineStepSec}
                        onChange={(e) => upd("ringRefineStepSec", numIn(e.target.value, form.ringRefineStepSec))}
                      />
                    </label>
                    <label>
                      ring-stable-seconds
                      <input
                        type="number"
                        step="0.05"
                        value={form.ringStableSeconds}
                        onChange={(e) => upd("ringStableSeconds", numIn(e.target.value, form.ringStableSeconds))}
                      />
                    </label>
                    <label>
                      ring-geometry-window-seconds
                      <input
                        type="number"
                        step="0.1"
                        value={form.ringGeometryWindowSeconds}
                        onChange={(e) =>
                          upd("ringGeometryWindowSeconds", numIn(e.target.value, form.ringGeometryWindowSeconds))
                        }
                      />
                    </label>
                    <label>
                      ring-geometry-step-sec
                      <input
                        type="number"
                        step="0.05"
                        value={form.ringGeometryStepSec}
                        onChange={(e) => upd("ringGeometryStepSec", numIn(e.target.value, form.ringGeometryStepSec))}
                      />
                    </label>
                    <label className={styles.detectCheck}>
                      <input
                        type="checkbox"
                        checked={form.ringCountdownZoneMode}
                        onChange={(e) => upd("ringCountdownZoneMode", e.target.checked)}
                      />
                      ring-countdown-zone-mode
                    </label>
                    <label className={styles.detectCheck}>
                      <input
                        type="checkbox"
                        checked={form.ringStrictLineProfile}
                        onChange={(e) => upd("ringStrictLineProfile", e.target.checked)}
                      />
                      ring-strict-line-profile
                    </label>
                    <label className={styles.detectCheck}>
                      <input
                        type="checkbox"
                        checked={form.ringArcOnlyMode}
                        onChange={(e) => upd("ringArcOnlyMode", e.target.checked)}
                      />
                      ring-arc-only-mode
                    </label>
                    <label className={styles.detectCheck}>
                      <input
                        type="checkbox"
                        checked={form.forceClearRings}
                        onChange={(e) => upd("forceClearRings", e.target.checked)}
                      />
                      force-clear-rings (при пустом результате — по логике скрипта)
                    </label>
                  </div>
                </div>
              </details>

              <details className={styles.detectDetails} open={ringsOnly}>
                <summary>Камера после колец и отладка</summary>
                <div className={styles.detectDetailsBody}>
                  <div className={styles.detectGrid}>
                    <label>
                      Режим камеры (camera-tracking-mode)
                      <select
                        value={form.cameraTrackingMode}
                        onChange={(e) =>
                          upd("cameraTrackingMode", e.target.value as "geometry" | "edge_residual")
                        }
                      >
                        <option value="geometry">geometry</option>
                        <option value="edge_residual">edge_residual</option>
                      </select>
                    </label>
                    <label className={styles.detectCheck}>
                      <input type="checkbox" checked={form.dryRun} onChange={(e) => upd("dryRun", e.target.checked)} />
                      dry-run (не писать БД)
                    </label>
                    <label className={styles.detectCheck}>
                      <input type="checkbox" checked={form.debug} onChange={(e) => upd("debug", e.target.checked)} />
                      debug
                    </label>
                    <label>
                      debug-dir
                      <input value={form.debugDir} onChange={(e) => upd("debugDir", e.target.value)} />
                    </label>
                  </div>
                </div>
              </details>
                </div>
              </details>

              <div className={styles.detectActions}>
                <button type="button" className={styles.btn} onClick={onClose} disabled={busy}>
                  Отмена
                </button>
                <button type="submit" className={`${styles.btn} ${styles.btnPrimary}`} disabled={busy || !form}>
                  {busy ? "Запуск…" : submitLabel}
                </button>
              </div>
            </>
          ) : (
            !form && !loadErr && <p className={styles.hint}>Загрузка параметров…</p>
          )}
          {form && tab !== "params" ? (
            <div className={styles.detectActions}>
              <button type="button" className={styles.btn} onClick={onClose} disabled={busy}>
                Закрыть
              </button>
              <button type="submit" className={`${styles.btn} ${styles.btnPrimary}`} disabled={busy || !form}>
                {busy ? "Запуск…" : submitLabel}
              </button>
            </div>
          ) : null}
        </form>
      </div>
    </div>
  );
}
