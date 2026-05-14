"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "../../lib/api";
import type {
  JobRecord,
  JobStatus,
  JobType,
  MapStartVideoDetail,
  MapStartVideoSummaryRow,
  SegmentManifestRecord,
  Tournament,
} from "../../lib/types";
import { DetectMapStartRunModal, DetectorJobProgress } from "./DetectMapStartRunModal";
import styles from "./management.module.css";

const DEFAULT_RECORDS_REL = "ffmpeg_downloader/records";

function isVideoFile(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".mp4") || lower.endsWith(".mkv") || lower.endsWith(".webm") || lower.endsWith(".mov");
}

function normalizeRelPath(p: string): string {
  return p.trim().replace(/\\/g, "/");
}

function manifestMatchesVideo(manifestVideo: string | undefined, filePath: string): boolean {
  if (!manifestVideo) return false;
  const a = normalizeRelPath(manifestVideo);
  const b = normalizeRelPath(filePath);
  return a === b || a.endsWith(`/${b.split("/").pop()}`) || b.endsWith(`/${a.split("/").pop()}`);
}

function isTerminalStatus(status: string): boolean {
  return status === "ok" || status === "not_confident";
}

function labelTeamPhase(summary: MapStartVideoSummaryRow | undefined): { text: string; cls: string } {
  if (!summary) return { text: "Не начато", cls: styles.statusIdle };
  const terminal = isTerminalStatus(summary.status);
  if (summary.teamCount > 0 || terminal) return { text: "Завершено", cls: styles.statusOk };
  return { text: "В процессе", cls: styles.statusWarn };
}

function labelRingPhase(summary: MapStartVideoSummaryRow | undefined): { text: string; cls: string } {
  if (!summary) return { text: "Не начато", cls: styles.statusIdle };
  const terminal = isTerminalStatus(summary.status);
  if (summary.ringCount > 0 || terminal) return { text: "Завершено", cls: styles.statusOk };
  return { text: "В процессе", cls: styles.statusWarn };
}

function formatStartSec(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || !Number.isFinite(sec)) return "—";
  const s = Number(sec);
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${m}:${r.toString().padStart(2, "0")} (${s.toFixed(1)} с)`;
}

function formatTimeSpan(a: number | null, b: number | null): string {
  if (a === null || b === null || !Number.isFinite(a) || !Number.isFinite(b)) return "—";
  return `${formatStartSec(a)} → ${formatStartSec(b)}`;
}

export function AdminManagementModule() {
  const [tournaments, setTournaments] = useState<Tournament[]>([]);
  const [recordsRel, setRecordsRel] = useState(DEFAULT_RECORDS_REL);
  const [videoFiles, setVideoFiles] = useState<
    Array<{ name: string; path: string; size: number; modifiedAt: string; durationSec?: number | null }>
  >([]);
  const [mapByVideoName, setMapByVideoName] = useState<Record<string, MapStartVideoSummaryRow>>({});
  const [manifests, setManifests] = useState<SegmentManifestRecord[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [jobFilterType, setJobFilterType] = useState<"" | JobType>("");
  const [jobFilterStatus, setJobFilterStatus] = useState<"" | JobStatus>("");
  const [jobPageSize, setJobPageSize] = useState(30);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [expandedJobId, setExpandedJobId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [formOk, setFormOk] = useState<string | null>(null);

  const [uploadTournamentId, setUploadTournamentId] = useState("");
  const [uploadStart, setUploadStart] = useState(0);
  const [uploadEnd, setUploadEnd] = useState(3600);
  const [uploadSeg, setUploadSeg] = useState(600);
  const [existingVideoPath, setExistingVideoPath] = useState("");
  const [pickFile, setPickFile] = useState<File | null>(null);

  const [detailVideoName, setDetailVideoName] = useState<string | null>(null);
  const [detail, setDetail] = useState<MapStartVideoDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [runAnalysis, setRunAnalysis] = useState<{ video: string; mode: "full" | "rings" } | null>(null);

  const refreshData = useCallback(async () => {
    setLoadError(null);
    try {
      const [tm, dir, man, jobRes, mapRows] = await Promise.all([
        api.getTournaments(),
        api.listWorkspaceDirectory(recordsRel),
        api.listSegmentManifests(),
        api.getJobs({
          jobType: jobFilterType || undefined,
          status: jobFilterStatus || undefined,
          page: 0,
          pageSize: jobPageSize,
        }),
        api.listMapStartSummaries(),
      ]);
      setTournaments(tm);
      setManifests(man);
      setJobs(jobRes.items);
      const m: Record<string, MapStartVideoSummaryRow> = {};
      for (const row of mapRows) {
        m[row.videoName] = row;
      }
      setMapByVideoName(m);
      const vids = dir.items
        .filter((i) => i.type === "file" && isVideoFile(i.name))
        .map((i) => ({ name: i.name, path: i.path, size: i.size, modifiedAt: i.modifiedAt, durationSec: i.durationSec }));
      setVideoFiles(vids);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, [recordsRel, jobFilterType, jobFilterStatus, jobPageSize]);

  useEffect(() => {
    if (uploadTournamentId) return;
    if (tournaments.length) setUploadTournamentId(tournaments[0].id);
  }, [tournaments, uploadTournamentId]);

  useEffect(() => {
    void refreshData();
  }, [refreshData]);

  useEffect(() => {
    if (!autoRefresh) return;
    const hasRunningAnalysis = jobs.some((job) => job.jobType === "analysis" && job.status === "running");
    const t = setInterval(() => void refreshData(), hasRunningAnalysis ? 1_500 : 12_000);
    return () => clearInterval(t);
  }, [autoRefresh, refreshData, jobs]);

  useEffect(() => {
    if (!detailVideoName) {
      setDetail(null);
      setDetailError(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    void api
      .getMapStartVideoDetail(detailVideoName)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((err) => {
        if (!cancelled) setDetailError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [detailVideoName]);

  useEffect(() => {
    if (!detailVideoName) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDetailVideoName(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detailVideoName]);

  const onSubmitSegment = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setFormOk(null);
    if (!uploadTournamentId.trim()) {
      setFormError("Выберите турнир.");
      return;
    }
    if (uploadEnd <= uploadStart || uploadSeg <= 0) {
      setFormError("Проверьте интервал и длительность фрагмента.");
      return;
    }
    setBusy(true);
    try {
      if (pickFile) {
        const fd = new FormData();
        fd.set("file", pickFile);
        fd.set("tournamentId", uploadTournamentId);
        fd.set("startSec", String(uploadStart));
        fd.set("endSec", String(uploadEnd));
        fd.set("segmentDurationSec", String(uploadSeg));
        const r = await api.uploadVideoSegment(fd);
        setFormOk(`Файл загружен, манифест: ${r.manifestPath}`);
        setPickFile(null);
      } else if (existingVideoPath.trim()) {
        const r = await api.createSegmentManifest({
          tournamentId: uploadTournamentId,
          videoRelativePath: normalizeRelPath(existingVideoPath),
          startSec: uploadStart,
          endSec: uploadEnd,
          segmentDurationSec: uploadSeg,
        });
        setFormOk(`Манифест создан: ${r.manifestPath}`);
      } else {
        setFormError("Укажите файл для загрузки или путь к уже существующему видео в репозитории.");
        return;
      }
      await refreshData();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const videoManifestStatus = (filePath: string) => {
    const m = manifests.find((x) => manifestMatchesVideo(x.videoRelativePath, filePath));
    if (m) return { text: "Заявка на нарезку", cls: styles.statusWarn, detail: m.manifestPath };
    return { text: "Файл на диске", cls: styles.statusOk, detail: "—" };
  };

  const jobStatusClass = (s: string) => {
    if (s === "failed") return styles.statusBad;
    if (s === "completed") return styles.statusOk;
    if (s === "running") return styles.statusWarn;
    return "";
  };

  const summaryForFile = useCallback(
    (fileName: string) => mapByVideoName[fileName],
    [mapByVideoName]
  );
  const controlJob = useCallback(
    async (jobId: string, action: "pause" | "resume" | "cancel") => {
      await api.controlJob(jobId, action);
      await refreshData();
    },
    [refreshData]
  );
  const runningAnalysisJobs = jobs.filter((job) => job.jobType === "analysis" && job.status === "running");

  return (
    <div className={styles.root}>
      {loadError ? (
        <div className={styles.errBox} role="alert">
          {loadError}
        </div>
      ) : null}

      <section className={styles.section}>
        <h2>Параметры обзора</h2>
        <p className={styles.hint}>
          Каталог с сырыми записями задаётся относительно корня репозитория (как в API <code>runtime_paths</code>).
          Статусы трекинга и колец читаются из <code className={styles.mono}>output/map_start_detection.sqlite</code>.
        </p>
        <div className={styles.grid2}>
          <label>
            Каталог записей
            <input value={recordsRel} onChange={(e) => setRecordsRel(normalizeRelPath(e.target.value))} />
          </label>
          <label>
            Заданий на странице
            <input
              type="number"
              min={10}
              max={200}
              value={jobPageSize}
              onChange={(e) => setJobPageSize(Number(e.target.value) || 30)}
            />
          </label>
          <label>
            Тип job
            <select value={jobFilterType} onChange={(e) => setJobFilterType(e.target.value as "" | JobType)}>
              <option value="">Все</option>
              <option value="ingest">ingest</option>
              <option value="analysis">analysis</option>
            </select>
          </label>
          <label>
            Статус job
            <select value={jobFilterStatus} onChange={(e) => setJobFilterStatus(e.target.value as "" | JobStatus)}>
              <option value="">Все</option>
              <option value="queued">queued</option>
              <option value="running">running</option>
              <option value="completed">completed</option>
              <option value="failed">failed</option>
            </select>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 0 }}>
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
            Автообновление (~12 с)
          </label>
        </div>
        <div className={styles.rowActions}>
          <button type="button" className={`${styles.btn} ${styles.btnPrimary}`} onClick={() => void refreshData()}>
            Обновить сейчас
          </button>
        </div>
      </section>

      <section className={styles.section}>
        <h2>Видео в каталоге записей</h2>
        <p className={styles.mono}>{recordsRel}</p>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Файл</th>
                <th>Размер</th>
                <th>Трекинг команд</th>
                <th>Анализ колец</th>
                <th>Время старта</th>
                <th>Последний анализ</th>
                <th>Статус нарезки</th>
                <th colSpan={2}>Запуск детектора</th>
              </tr>
            </thead>
            <tbody>
              {videoFiles.length === 0 ? (
                <tr>
                  <td colSpan={9}>
                    Нет видеофайлов или каталог недоступен. Проверьте путь и что API смотрит в тот же корень проекта.
                  </td>
                </tr>
              ) : (
                videoFiles.map((v) => {
                  const detRow = summaryForFile(v.name);
                  const lt = labelTeamPhase(detRow);
                  const lr = labelRingPhase(detRow);
                  const mf = videoManifestStatus(v.path);
                  return (
                    <tr
                      key={v.path}
                      className={styles.clickRow}
                      tabIndex={0}
                      role="button"
                      onClick={() => setDetailVideoName(v.name)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setDetailVideoName(v.name);
                        }
                      }}
                    >
                      <td className={styles.mono}>{v.name}</td>
                      <td>{(v.size / (1024 * 1024)).toFixed(1)} МБ</td>
                      <td>
                        <span className={lt.cls}>{lt.text}</span>
                      </td>
                      <td>
                        <span className={lr.cls}>{lr.text}</span>
                      </td>
                      <td>{formatStartSec(detRow?.startTimestampSec ?? null)}</td>
                      <td>{detRow?.updatedAt ? new Date(detRow.updatedAt).toLocaleString() : "—"}</td>
                      <td>
                        <span className={mf.cls}>{mf.text}</span>
                        {mf.detail !== "—" ? <div className={styles.mono}>{mf.detail}</div> : null}
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <button
                          type="button"
                          className={`${styles.btn} ${styles.thinBtn}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            setRunAnalysis({ video: v.name, mode: "full" });
                          }}
                        >
                          Полный
                        </button>
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <button
                          type="button"
                          className={`${styles.btn} ${styles.thinBtn}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            setRunAnalysis({ video: v.name, mode: "rings" });
                          }}
                        >
                          Кольца
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        <p className={styles.hint} style={{ marginTop: 10, marginBottom: 0 }}>
          Строка кликабельна: откроется детализация по командам и кольцам из БД детекции.
        </p>
      </section>

      <details className={styles.section}>
        <summary className={styles.sectionSummary}>
          Прогресс запущенных процессов ({runningAnalysisJobs.length})
        </summary>
        {runningAnalysisJobs.length === 0 ? (
          <p className={styles.hint} style={{ marginTop: 10 }}>
            Сейчас нет активных процессов анализа.
          </p>
        ) : (
          <div className={styles.managementProgressList}>
            {runningAnalysisJobs.map((job) => (
              <article key={job.id} className={styles.detectCard}>
                <div className={styles.detectCardTitle}>
                  {job.video ?? job.mapId ?? job.id} <span className={styles.mono}>({job.id})</span>
                </div>
                <DetectorJobProgress job={job} compact onControl={controlJob} />
              </article>
            ))}
          </div>
        )}
      </details>

      <section className={styles.section}>
        <h2>Задачи анализа / ingest</h2>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Id</th>
                <th>Тип</th>
                <th>Статус</th>
                <th>Прогресс</th>
                <th>Команда / контекст</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <FragmentJobRow
                  key={j.id}
                  job={j}
                  expanded={expandedJobId === j.id}
                  onToggle={() => setExpandedJobId((cur) => (cur === j.id ? null : j.id))}
                  statusClass={jobStatusClass}
                  styleModule={styles}
                />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className={styles.section}>
        <h2>Загрузка видео и заявка на нарезку</h2>
        <p className={styles.hint}>
          Файл сохраняется в каталог записей на сервере; рядом создаётся JSON-манифест в{" "}
          <code className={styles.mono}>output/management_segment_requests/</code>. Дальнейшая нарезка выполняется
          внешними скриптами по этим файлам.
        </p>
        {formError ? <div className={styles.errBox}>{formError}</div> : null}
        {formOk ? <p className={styles.statusOk}>{formOk}</p> : null}
        <form onSubmit={onSubmitSegment}>
          <div className={styles.grid2}>
            <label>
              Турнир
              <select value={uploadTournamentId} onChange={(e) => setUploadTournamentId(e.target.value)} required>
                {tournaments.length === 0 ? <option value="">Нет турниров в каталоге</option> : null}
                {tournaments.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} ({t.season})
                  </option>
                ))}
              </select>
            </label>
            <label>
              Начало отрезка, сек
              <input type="number" min={0} value={uploadStart} onChange={(e) => setUploadStart(Number(e.target.value))} />
            </label>
            <label>
              Конец отрезка, сек
              <input type="number" min={0} value={uploadEnd} onChange={(e) => setUploadEnd(Number(e.target.value))} />
            </label>
            <label>
              Длительность фрагмента нарезки, сек
              <input
                type="number"
                min={1}
                value={uploadSeg}
                onChange={(e) => setUploadSeg(Number(e.target.value))}
              />
            </label>
            <label>
              Файл с компьютера
              <input
                type="file"
                accept="video/*"
                onChange={(e) => setPickFile(e.target.files?.[0] ?? null)}
              />
            </label>
            <label>
              Или путь к видео уже в репозитории
              <input
                placeholder={`например ${DEFAULT_RECORDS_REL}/match.mp4`}
                value={existingVideoPath}
                onChange={(e) => setExistingVideoPath(e.target.value)}
              />
            </label>
            <label>
              Выбрать из списка каталога
              <select
                value=""
                onChange={(e) => {
                  const val = e.target.value;
                  if (val) setExistingVideoPath(val);
                }}
              >
                <option value="">—</option>
                {videoFiles.map((vf) => (
                  <option key={vf.path} value={vf.path}>
                    {vf.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <button type="submit" className={`${styles.btn} ${styles.btnPrimary}`} disabled={busy}>
            {busy ? "Отправка…" : "Создать манифест"}
          </button>
        </form>
      </section>

      {detailVideoName ? (
        <div
          className={styles.modalBackdrop}
          role="presentation"
          onClick={(e) => {
            if (e.target === e.currentTarget) setDetailVideoName(null);
          }}
        >
          <div className={styles.modalPanel} role="dialog" aria-modal="true" aria-labelledby="mgmt-video-detail-title">
            <div className={styles.modalHeader}>
              <div>
                <h3 id="mgmt-video-detail-title">{detailVideoName}</h3>
                {detail ? (
                  <p className={styles.hint} style={{ margin: "6px 0 0" }}>
                    Карта: {detail.mapName ?? "—"} · статус детекции: <span className={styles.mono}>{detail.status}</span>
                    {detail.notes ? (
                      <>
                        {" "}
                        · <span className={styles.mono}>{detail.notes}</span>
                      </>
                    ) : null}
                  </p>
                ) : null}
              </div>
              <div style={{ display: "flex", gap: 8, flexShrink: 0, alignItems: "flex-start", flexWrap: "wrap" }}>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.thinBtn}`}
                  onClick={() => setRunAnalysis({ video: detailVideoName, mode: "full" })}
                >
                  Полный анализ
                </button>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.thinBtn}`}
                  onClick={() => setRunAnalysis({ video: detailVideoName, mode: "rings" })}
                >
                  Только кольца
                </button>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.closeBtn}`}
                  onClick={() => setDetailVideoName(null)}
                  aria-label="Закрыть"
                >
                  Закрыть
                </button>
              </div>
            </div>
            <div className={styles.modalBody}>
              {detailLoading ? <p>Загрузка…</p> : null}
              {detailError ? <div className={styles.errBox}>{detailError}</div> : null}
              {detail && !detailLoading ? (
                <div className={styles.modalColumns}>
                  <div>
                    <h4 className={styles.modalColTitle}>Команды и трекинг</h4>
                    <div className={styles.tableWrap}>
                      <table className={styles.table}>
                        <thead>
                          <tr>
                            <th>Слот</th>
                            <th>Команда</th>
                            <th>Статус</th>
                            <th>Время (сек)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detail.teams.length === 0 ? (
                            <tr>
                              <td colSpan={4}>Нет строк в Teams для этого видео.</td>
                            </tr>
                          ) : (
                            detail.teams.map((t) => (
                              <tr key={t.slot}>
                                <td>TEAM_{t.slot}</td>
                                <td>{t.teamName ?? "—"}</td>
                                <td className={t.isEliminated ? styles.statusBad : styles.statusOk}>
                                  {t.isEliminated ? "Выбыл" : "В игре"}
                                </td>
                                <td className={styles.mono}>
                                  {t.isEliminated && t.timeEliminated != null ? t.timeEliminated.toFixed(2) : "—"}
                                </td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  <div>
                    <h4 className={styles.modalColTitle}>Кольца — тайминги и размеры</h4>
                    <div className={styles.tableWrap}>
                      <table className={styles.table}>
                        <thead>
                          <tr>
                            <th>№</th>
                            <th>Интервал</th>
                            <th>Радиус</th>
                            <th>Диаметр</th>
                            <th>Центр</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detail.rings.length === 0 ? (
                            <tr>
                              <td colSpan={5}>Нет колец в Rings для этого видео.</td>
                            </tr>
                          ) : (
                            detail.rings.map((r) => (
                              <tr key={r.ringNumber}>
                                <td>{r.ringNumber}</td>
                                <td className={styles.mono}>{formatTimeSpan(r.timeStart, r.timeEnd)}</td>
                                <td className={styles.mono}>
                                  {r.radius != null && Number.isFinite(r.radius) ? r.radius.toFixed(3) : "—"}
                                </td>
                                <td className={styles.mono}>
                                  {r.diameter != null && Number.isFinite(r.diameter) ? r.diameter.toFixed(3) : "—"}
                                </td>
                                <td className={styles.mono}>{r.center ?? "—"}</td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      <DetectMapStartRunModal
        open={runAnalysis !== null}
        videoName={runAnalysis?.video ?? null}
        mode={runAnalysis?.mode ?? "full"}
        recordsDir={recordsRel}
        durationSec={videoFiles.find((vf) => vf.name === runAnalysis?.video)?.durationSec ?? null}
        prefill={
          detailVideoName === runAnalysis?.video && detail
            ? { startTimestampSec: detail.startTimestampSec, mapName: detail.mapName }
            : undefined
        }
        onClose={() => setRunAnalysis(null)}
        onStarted={(info) => {
          setFormOk(
            `Запущен детектор (PID ${info.pid ?? "—"}). См. вкладку прогресса в модалке и таблицу задач. ${info.command.slice(0, 200)}`
          );
          void refreshData();
        }}
      />
    </div>
  );
}

function FragmentJobRow({
  job,
  expanded,
  onToggle,
  statusClass,
  styleModule,
}: {
  job: JobRecord;
  expanded: boolean;
  onToggle: () => void;
  statusClass: (st: string) => string;
  styleModule: typeof styles;
}) {
  const s = styleModule;
  const teamErrs = job.teamStatuses?.filter((t) => t.error || t.status === "failed") ?? [];
  const hasDetail =
    (job.errors?.length ?? 0) > 0 || teamErrs.length > 0 || Boolean(job.currentAction) || Boolean(job.video);

  return (
    <>
      <tr>
        <td className={s.mono}>{job.id}</td>
        <td>{job.jobType}</td>
        <td className={statusClass(job.status)}>{job.status}</td>
        <td>{job.progressPercent}%</td>
        <td className={s.mono}>
          {job.command.length > 80 ? `${job.command.slice(0, 80)}…` : job.command}
          {job.mapId ? <div>map: {job.mapId}</div> : null}
        </td>
        <td>
          {hasDetail ? (
            <button type="button" className={s.btn} onClick={onToggle}>
              {expanded ? "Скрыть" : "Ошибки / детали"}
            </button>
          ) : (
            "—"
          )}
        </td>
      </tr>
      {expanded && hasDetail ? (
        <tr>
          <td colSpan={6}>
            {job.video ? <div className={s.mono}>video: {job.video}</div> : null}
            {job.currentAction ? <div>current: {job.currentAction}</div> : null}
            {job.errors?.length ? (
              <div className={s.errBox}>
                <strong>Ошибки job:</strong>
                {"\n"}
                {job.errors.join("\n")}
              </div>
            ) : null}
            {teamErrs.length ? (
              <div className={s.errBox}>
                <strong>По командам:</strong>
                {"\n"}
                {teamErrs.map((t) => `${t.teamName} (${t.teamId}): ${t.error ?? t.status}`).join("\n")}
              </div>
            ) : null}
          </td>
        </tr>
      ) : null}
    </>
  );
}
