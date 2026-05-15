import { BadRequestException, Injectable, ServiceUnavailableException } from "@nestjs/common";
import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { JobsService } from "../jobs/jobs.service";
/** Значения по умолчанию как в `tools/algs-collector/detect_map_start.py` → `parse_args()`. */
export const DETECT_MAP_START_RUN_DEFAULTS = {
  recordsDir: "ffmpeg_downloader/records",
  dbPath: "output/map_start_detection.sqlite",
  videoWorkers: 1,
  teamWorkers: 1,
  fastApprox: false,
  fastApproxSmallSteps: false,
  frameStep: 120,
  coarseJumpFrames: 3000,
  rollbackStepFrames: 100,
  refineWindowFrames: 300,
  startRefineStepFrames: 3,
  stableSeconds: 5.0,
  dryRun: false,
  debug: false,
  debugDir: "output/map_start_debug",
  ocrMinConfidence: 0.62,
  cameraMinConfidence: 0.58,
  textJsonDir: "output/map_start_text",
  textSummaryTopN: 3,
  textOcrMinConfidence: 0.0,
  textZonesMaxEnabled: 5000,
  stopOnFirstBoth: false,
  povScreenshotOffsetSec: 3.0,
  povScreenshotDir: "output/map_start_pov",
  ringCoarseSec: 5.0,
  ringRollbackSec: 5.0,
  ringRefineWindowSec: 5.0,
  ringRefineStepSec: 1.0,
  ringStableSeconds: 1.0,
  ringGeometryWindowSeconds: 2.0,
  ringGeometryStepSec: 1.0,
  elimCoarseSec: 5.0,
  elimRefineSec: 5.0,
  elimRefineStepSec: 1.0,
  forceClearRings: false,
  ringCountdownZoneMode: false,
  ringStrictLineProfile: false,
  ringArcOnlyMode: false,
  cameraTrackingMode: "geometry" as "geometry" | "edge_residual",
  disableStartDetection: false,
  disableTeamDetection: false,
  disableEliminationDetection: false,
  disableRingDetection: false,
  disableCameraTracking: false,
  runStartDetection: true,
  runTeamDetection: true,
  runEliminationDetection: true,
  runRingDetection: true,
  runCameraTracking: true,
  assumeStartSec: 0.0,
  assumeMapName: "" as string,
  textZonesFile: "" as string,
} as const;

export type DetectMapStartRunPayload = { videoName: string } & Partial<{
  [K in keyof typeof DETECT_MAP_START_RUN_DEFAULTS]: (typeof DETECT_MAP_START_RUN_DEFAULTS)[K];
}>;

type DetectorTaskId = "start" | "teams" | "eliminations" | "rings" | "camera";

@Injectable()
export class MapStartDetectionRunService {
  private readonly projectRoot = path.resolve(__dirname, "../../../../..");

  constructor(private readonly jobsService: JobsService) {}

  getDefaults(): typeof DETECT_MAP_START_RUN_DEFAULTS {
    return { ...DETECT_MAP_START_RUN_DEFAULTS };
  }

  private normalizeVideoKey(videoName: string): string {
    const trimmed = videoName.trim();
    if (!trimmed) throw new BadRequestException("videoName is required.");
    const norm = trimmed.replace(/\\/g, "/");
    if (norm.includes("..")) throw new BadRequestException("Invalid videoName.");
    return path.basename(norm);
  }

  private mergePayload(body: Record<string, unknown>): DetectMapStartRunPayload {
    const videoRaw = body.videoName;
    if (typeof videoRaw !== "string" || !videoRaw.trim()) {
      throw new BadRequestException("videoName is required.");
    }
    const videoName = this.normalizeVideoKey(videoRaw);
    const merged: DetectMapStartRunPayload = {
      ...DETECT_MAP_START_RUN_DEFAULTS,
      videoName,
    };
    const keys = Object.keys(DETECT_MAP_START_RUN_DEFAULTS) as (keyof typeof DETECT_MAP_START_RUN_DEFAULTS)[];
    for (const k of keys) {
      if (Object.prototype.hasOwnProperty.call(body, k) && body[k] !== undefined) {
        (merged as Record<string, unknown>)[k] = body[k];
      }
    }
    return merged;
  }

  private buildArgv(opts: DetectMapStartRunPayload, persistRingsOnly: boolean): string[] {
    const rd = String(opts.recordsDir).replace(/\\/g, "/").replace(/\/+$/, "");
    const videoArg = `${rd}/${opts.videoName}`;
    const args: string[] = [
      "--records-dir",
      rd,
      "--video",
      videoArg,
      "--db-path",
      String(opts.dbPath).replace(/\\/g, "/"),
      "--video-workers",
      String(this.resolveVideoWorkers(opts)),
      "--team-workers",
      String(Math.max(1, Number((opts as Record<string, unknown>).teamWorkers) || 1)),
    ];
    const num = (v: unknown, d: number) => (Number.isFinite(Number(v)) ? Number(v) : d);

    if (opts.fastApprox) args.push("--fast-approx");
    if (opts.fastApproxSmallSteps) args.push("--fast-approx-small-steps");

    args.push("--frame-step", String(num(opts.frameStep, DETECT_MAP_START_RUN_DEFAULTS.frameStep)));
    args.push("--coarse-jump-frames", String(num(opts.coarseJumpFrames, DETECT_MAP_START_RUN_DEFAULTS.coarseJumpFrames)));
    args.push(
      "--rollback-step-frames",
      String(num(opts.rollbackStepFrames, DETECT_MAP_START_RUN_DEFAULTS.rollbackStepFrames))
    );
    args.push(
      "--refine-window-frames",
      String(num(opts.refineWindowFrames, DETECT_MAP_START_RUN_DEFAULTS.refineWindowFrames))
    );
    args.push(
      "--start-refine-step-frames",
      String(num(opts.startRefineStepFrames, DETECT_MAP_START_RUN_DEFAULTS.startRefineStepFrames))
    );
    args.push("--stable-seconds", String(num(opts.stableSeconds, DETECT_MAP_START_RUN_DEFAULTS.stableSeconds)));

    if (opts.dryRun) args.push("--dry-run");
    if (opts.debug) args.push("--debug");
    args.push("--debug-dir", String(opts.debugDir).replace(/\\/g, "/"));

    args.push("--ocr-min-confidence", String(num(opts.ocrMinConfidence, DETECT_MAP_START_RUN_DEFAULTS.ocrMinConfidence)));
    args.push(
      "--camera-min-confidence",
      String(num(opts.cameraMinConfidence, DETECT_MAP_START_RUN_DEFAULTS.cameraMinConfidence))
    );
    args.push("--text-json-dir", String(opts.textJsonDir).replace(/\\/g, "/"));
    args.push("--text-summary-top-n", String(num(opts.textSummaryTopN, DETECT_MAP_START_RUN_DEFAULTS.textSummaryTopN)));
    args.push(
      "--text-ocr-min-confidence",
      String(num(opts.textOcrMinConfidence, DETECT_MAP_START_RUN_DEFAULTS.textOcrMinConfidence))
    );
    args.push(
      "--text-zones-max-enabled",
      String(num(opts.textZonesMaxEnabled, DETECT_MAP_START_RUN_DEFAULTS.textZonesMaxEnabled))
    );

    if (opts.stopOnFirstBoth) args.push("--stop-on-first-both");
    args.push(
      "--pov-screenshot-offset-sec",
      String(num(opts.povScreenshotOffsetSec, DETECT_MAP_START_RUN_DEFAULTS.povScreenshotOffsetSec))
    );
    args.push("--pov-screenshot-dir", String(opts.povScreenshotDir).replace(/\\/g, "/"));

    const tz = typeof opts.textZonesFile === "string" ? opts.textZonesFile.trim() : "";
    if (tz) {
      args.push("--text-zones-file", tz.replace(/\\/g, "/"));
    }

    args.push("--ring-coarse-sec", String(num(opts.ringCoarseSec, DETECT_MAP_START_RUN_DEFAULTS.ringCoarseSec)));
    args.push("--ring-rollback-sec", String(num(opts.ringRollbackSec, DETECT_MAP_START_RUN_DEFAULTS.ringRollbackSec)));
    args.push(
      "--ring-refine-window-sec",
      String(num(opts.ringRefineWindowSec, DETECT_MAP_START_RUN_DEFAULTS.ringRefineWindowSec))
    );
    args.push(
      "--ring-refine-step-sec",
      String(num(opts.ringRefineStepSec, DETECT_MAP_START_RUN_DEFAULTS.ringRefineStepSec))
    );
    args.push(
      "--ring-stable-seconds",
      String(num(opts.ringStableSeconds, DETECT_MAP_START_RUN_DEFAULTS.ringStableSeconds))
    );
    args.push(
      "--ring-geometry-window-seconds",
      String(num(opts.ringGeometryWindowSeconds, DETECT_MAP_START_RUN_DEFAULTS.ringGeometryWindowSeconds))
    );
    args.push(
      "--ring-geometry-step-sec",
      String(num(opts.ringGeometryStepSec, DETECT_MAP_START_RUN_DEFAULTS.ringGeometryStepSec))
    );

    args.push("--elim-coarse-sec", String(num(opts.elimCoarseSec, DETECT_MAP_START_RUN_DEFAULTS.elimCoarseSec)));
    args.push("--elim-refine-sec", String(num(opts.elimRefineSec, DETECT_MAP_START_RUN_DEFAULTS.elimRefineSec)));
    args.push(
      "--elim-refine-step-sec",
      String(num(opts.elimRefineStepSec, DETECT_MAP_START_RUN_DEFAULTS.elimRefineStepSec))
    );

    if (opts.forceClearRings) args.push("--force-clear-rings");
    if (opts.ringCountdownZoneMode) args.push("--ring-countdown-zone-mode");
    if (opts.ringStrictLineProfile) args.push("--ring-strict-line-profile");
    if (opts.ringArcOnlyMode) args.push("--ring-arc-only-mode");

    const cam = opts.cameraTrackingMode === "edge_residual" ? "edge_residual" : "geometry";
    args.push("--camera-tracking-mode", cam);

    const rawOpts = opts as Record<string, unknown>;
    const disableStart = Boolean(opts.disableStartDetection) || rawOpts.runStartDetection === false;
    const disableTeam = Boolean(opts.disableTeamDetection) || rawOpts.runTeamDetection === false;
    const disableElim =
      Boolean(opts.disableEliminationDetection) || rawOpts.runEliminationDetection === false || disableTeam;
    const disableRings = Boolean(rawOpts.disableRingDetection) || rawOpts.runRingDetection === false;
    const disableCamera = Boolean(rawOpts.disableCameraTracking) || rawOpts.runCameraTracking === false || disableRings;

    if (disableStart) args.push("--disable-start-detection");
    if (disableTeam) args.push("--disable-team-detection");
    if (disableElim) args.push("--disable-elimination-detection");
    if (disableRings) args.push("--disable-ring-detection");
    if (disableCamera) args.push("--disable-camera-tracking");

    args.push("--assume-start-sec", String(num(opts.assumeStartSec, DETECT_MAP_START_RUN_DEFAULTS.assumeStartSec)));
    const am = typeof opts.assumeMapName === "string" ? opts.assumeMapName.trim() : "";
    if (am) {
      args.push("--assume-map-name", am);
    }

    if (persistRingsOnly) {
      args.push("--persist-rings-only");
    }

    return args;
  }

  private controlPathForJob(jobId: string): string {
    return path.join(this.projectRoot, "output", "job_controls", `${jobId}.json`);
  }

  private resolvePythonBin(): string {
    const fromEnv = process.env.PYTHON?.trim() || process.env.PYTHON_BIN?.trim();
    if (fromEnv) return fromEnv;
    return process.platform === "win32" ? "python" : "python3";
  }

  private resolveVideoWorkers(opts: DetectMapStartRunPayload): number {
    const rawOpts = opts as Record<string, unknown>;
    const requested = Math.max(1, Number(opts.videoWorkers) || 1);
    const heavy =
      rawOpts.runRingDetection !== false ||
      rawOpts.runCameraTracking !== false ||
      rawOpts.runEliminationDetection !== false ||
      Boolean(rawOpts.persistRingsOnly);
    return heavy ? 1 : requested;
  }

  private pushLog(payload: Record<string, unknown>, line: string, stream: "stdout" | "stderr") {
    const existing = Array.isArray(payload.recentLogs) ? payload.recentLogs : [];
    return [...existing, { stream, line, at: new Date().toISOString() }].slice(-80);
  }

  private pushDetectorError(payload: Record<string, unknown>, progress: Record<string, unknown>) {
    const existing = Array.isArray(payload.detectorErrors) ? payload.detectorErrors : [];
    return [...existing, { ...progress, at: new Date().toISOString() }].slice(-40);
  }

  private parseProgressLine(line: string): Record<string, unknown> | null {
    const idx = line.indexOf("PROGRESS_JSON ");
    if (idx < 0) return null;
    const rawText = line.slice(idx + "PROGRESS_JSON ".length).trim();
    const endIdx = rawText.lastIndexOf("}");
    const raw = endIdx >= 0 ? rawText.slice(0, endIdx + 1) : rawText;
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  }

  private taskDefinitions(opts: DetectMapStartRunPayload): Array<{ id: DetectorTaskId; label: string; enabled: boolean }> {
    const rawOpts = opts as Record<string, unknown>;
    const runStart = rawOpts.runStartDetection !== false && !Boolean(opts.disableStartDetection);
    const runTeams = rawOpts.runTeamDetection !== false && !Boolean(opts.disableTeamDetection);
    const runElims = runTeams && rawOpts.runEliminationDetection !== false && !Boolean(opts.disableEliminationDetection);
    const runRings = rawOpts.runRingDetection !== false && !Boolean(rawOpts.disableRingDetection);
    const runCamera = runRings && rawOpts.runCameraTracking !== false && !Boolean(rawOpts.disableCameraTracking);
    return [
      { id: "start", label: "Старт карты", enabled: runStart },
      { id: "teams", label: "Команды", enabled: runTeams },
      { id: "eliminations", label: "Выбывания", enabled: runElims },
      { id: "rings", label: "Кольца", enabled: runRings },
      { id: "camera", label: "Камера", enabled: runCamera },
    ];
  }

  private initializeTaskProgress(opts: DetectMapStartRunPayload) {
    return this.taskDefinitions(opts).map((task) => ({
      id: task.id,
      label: task.label,
      enabled: task.enabled,
      status: task.enabled ? "pending" : "skipped",
      progressPercent: task.enabled ? 0 : 100,
      elapsedSec: 0,
      remainingSec: null,
      stage: null,
    }));
  }

  private initializeTeamProgress(opts: DetectMapStartRunPayload) {
    const rawOpts = opts as Record<string, unknown>;
    const enabled = rawOpts.runTeamDetection !== false && !Boolean(opts.disableTeamDetection);
    return Array.from({ length: 20 }, (_, index) => {
      const slot = index + 1;
      return {
        slot,
        label: `TEAM_${slot}`,
        status: enabled ? "pending" : "skipped",
        progressPercent: enabled ? 0 : 100,
        frame: null,
        extra: enabled ? "waiting" : "team_detection_disabled",
      };
    });
  }

  private taskIdForStage(stage: string | undefined): DetectorTaskId | null {
    if (!stage) return null;
    const s = stage.toLowerCase();
    if (s.startsWith("ring") || s.includes("geometry")) return "rings";
    if (s.startsWith("ocr_snapshot")) return "teams";
    if (s.startsWith("elim")) return "eliminations";
    if (s.includes("camera")) return "camera";
    if (s.includes("start") || s.includes("coarse_jump") || s === "rollback" || s === "refine") return "start";
    return null;
  }

  private updateTaskProgress(payload: Record<string, unknown>, progress: Record<string, unknown>) {
    const existing = Array.isArray(payload.taskProgress) ? payload.taskProgress : [];
    const tasks = existing.map((item) => ({ ...(item as Record<string, unknown>) }));
    const stage = typeof progress.stage === "string" ? progress.stage : undefined;
    const taskId = this.taskIdForStage(stage);
    const totalElapsedSec = Number(progress.totalElapsedSec);
    const overallPercent = Number(progress.percent);
    const currentIndex = tasks.findIndex((item) => item.id === taskId);
    if (currentIndex < 0) return tasks;
    for (let i = 0; i < tasks.length; i += 1) {
      const task = tasks[i];
      if (!task.enabled) continue;
      if (i < currentIndex && task.status !== "completed") {
        task.status = "completed";
        task.progressPercent = 100;
        task.remainingSec = 0;
      } else if (i === currentIndex) {
        const pct = Number.isFinite(overallPercent) ? Math.max(Number(task.progressPercent ?? 0), Math.min(99, overallPercent)) : Number(task.progressPercent ?? 0);
        task.status = "running";
        task.stage = stage ?? null;
        task.progressPercent = pct;
        task.elapsedSec = Number.isFinite(totalElapsedSec) ? Math.max(Number(task.elapsedSec ?? 0), totalElapsedSec) : Number(task.elapsedSec ?? 0);
        task.remainingSec = pct > 0 ? Math.max(0, (Number(task.elapsedSec ?? 0) / pct) * (100 - pct)) : null;
      }
    }
    return tasks;
  }

  private updateTeamProgress(payload: Record<string, unknown>, progress: Record<string, unknown>) {
    const existing = Array.isArray(payload.teamProgress) ? payload.teamProgress : [];
    const teams = existing.map((item) => ({ ...(item as Record<string, unknown>) }));
    const slot = Number(progress.slot);
    if (!Number.isFinite(slot) || slot <= 0) return teams;
    const idx = teams.findIndex((item) => Number(item.slot) === slot);
    const next = {
      slot,
      label: `TEAM_${slot}`,
      status: String(progress.status ?? "running"),
      progressPercent: Math.max(0, Math.min(100, Number(progress.progressPercent ?? 0) || 0)),
      frame: progress.frame ?? null,
      extra: progress.extra ?? "",
      updatedAt: new Date().toISOString(),
    };
    if (idx >= 0) teams[idx] = { ...teams[idx], ...next };
    else teams.push(next);
    return teams.sort((a, b) => Number(a.slot) - Number(b.slot));
  }

  private finishTaskProgress(payload: Record<string, unknown>, completed: boolean) {
    const existing = Array.isArray(payload.taskProgress) ? payload.taskProgress : [];
    return existing.map((item) => {
      const task = { ...(item as Record<string, unknown>) };
      if (!task.enabled) return task;
      if (completed) {
        task.status = "completed";
        task.progressPercent = 100;
        task.remainingSec = 0;
      } else if (task.status === "running") {
        task.status = "failed";
      }
      return task;
    });
  }

  async run(body: Record<string, unknown>): Promise<{ started: boolean; pid: number | undefined; jobId: string; command: string }> {
    const persistRingsOnly = body.persistRingsOnly === true;
    const opts = this.mergePayload(body);
    const scriptPath = path.join(this.projectRoot, "tools", "algs-collector", "detect_map_start.py");
    if (!fs.existsSync(scriptPath)) {
      throw new ServiceUnavailableException(`Script not found: tools/algs-collector/detect_map_start.py`);
    }
    const videoFsPath = path.resolve(this.projectRoot, String(opts.recordsDir), opts.videoName);
    if (!fs.existsSync(videoFsPath)) {
      throw new BadRequestException(`Video file not found: ${opts.recordsDir}/${opts.videoName}`);
    }

    const argv = this.buildArgv(opts, persistRingsOnly);
    const py = this.resolvePythonBin();
    const command = `${py} ${path.basename(scriptPath)} ${argv.join(" ")}`;
    const job = await this.jobsService.createRunningAnalysisJob({
      command,
      video: opts.videoName,
      payload: {
        mode: persistRingsOnly ? "rings" : "full",
        options: {
          runStartDetection: opts.runStartDetection,
          runTeamDetection: opts.runTeamDetection,
          runEliminationDetection: opts.runEliminationDetection,
          runRingDetection: opts.runRingDetection,
          runCameraTracking: opts.runCameraTracking,
        },
        taskProgress: this.initializeTaskProgress(opts),
        teamProgress: this.initializeTeamProgress(opts),
        recentLogs: [],
      },
    });
    const controlPath = this.controlPathForJob(job.id);
    fs.mkdirSync(path.dirname(controlPath), { recursive: true });
    fs.writeFileSync(controlPath, JSON.stringify({ action: "run", updatedAt: new Date().toISOString() }), "utf-8");
    argv.push("--control-file", controlPath.replace(/\\/g, "/"));
    await this.jobsService.patchJobPayload(job.id, { controlPath, control: { action: "run", updatedAt: new Date().toISOString() } });

    const child = spawn(py, [scriptPath, ...argv], {
      cwd: this.projectRoot,
      detached: false,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    });
    if (child.pid === undefined && child.exitCode === null) {
      await this.jobsService.failJob(job.id, "Failed to spawn Python process (no PID). Is Python on PATH?");
      throw new ServiceUnavailableException("Failed to spawn Python process (no PID). Is Python on PATH?");
    }

    let payload: Record<string, unknown> = { ...job.payload, pid: child.pid };
    await this.jobsService.patchJobPayload(job.id, { pid: child.pid });
    let stdoutBuffer = "";
    let stderrBuffer = "";
    let lastUpdate = Promise.resolve();

    const enqueueUpdate = (line: string, stream: "stdout" | "stderr") => {
      lastUpdate = lastUpdate
        .then(async () => {
          payload = { ...payload, recentLogs: this.pushLog(payload, line, stream) };
          const progress = this.parseProgressLine(line);
          if (progress) {
            payload = { ...payload, progress };
            if (progress.kind === "team") {
              payload = { ...payload, teamProgress: this.updateTeamProgress(payload, progress) };
            } else if (progress.kind === "error") {
              const message = String(progress.message ?? progress.extra ?? "Detector error");
              payload = { ...payload, detectorErrors: this.pushDetectorError(payload, progress) };
              await this.jobsService.updateJobProgress(job.id, {
                currentAction: typeof progress.stage === "string" ? progress.stage : "error",
                payload,
                errors: [message],
              });
              return;
            } else {
              payload = { ...payload, taskProgress: this.updateTaskProgress(payload, progress) };
            }
            const percent = Number(progress.percent);
            const stage = typeof progress.stage === "string" ? progress.stage : undefined;
            await this.jobsService.updateJobProgress(job.id, {
              currentAction: stage,
              progressPercent: Number.isFinite(percent) ? percent : undefined,
              payload,
            });
          } else {
            await this.jobsService.updateJobProgress(job.id, {
              currentAction: stream === "stderr" ? "stderr" : undefined,
              payload,
            });
          }
        })
        .catch((error) => {
          // eslint-disable-next-line no-console
          console.warn(`[map-start-detection] failed to update job ${job.id}:`, error);
        });
    };

    const consume = (chunk: Buffer, stream: "stdout" | "stderr") => {
      const text = chunk.toString("utf-8");
      let buffer = stream === "stdout" ? stdoutBuffer : stderrBuffer;
      buffer += text;
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      if (stream === "stdout") stdoutBuffer = buffer;
      else stderrBuffer = buffer;
      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed) enqueueUpdate(trimmed, stream);
      }
    };

    child.stdout?.on("data", (chunk: Buffer) => consume(chunk, "stdout"));
    child.stderr?.on("data", (chunk: Buffer) => consume(chunk, "stderr"));
    child.on("error", (error) => {
      void lastUpdate.then(() => this.jobsService.failJob(job.id, error.message, { payload }));
    });
    child.on("close", (code) => {
      if (stdoutBuffer.trim()) enqueueUpdate(stdoutBuffer.trim(), "stdout");
      if (stderrBuffer.trim()) enqueueUpdate(stderrBuffer.trim(), "stderr");
      void lastUpdate.then(async () => {
        payload = { ...payload, exitCode: code };
        const currentJob = await this.jobsService.getJob(job.id);
        if (currentJob.currentAction === "cancelled" || (currentJob.payload.control as Record<string, unknown> | undefined)?.action === "cancel") {
          payload = { ...currentJob.payload, ...payload, taskProgress: this.finishTaskProgress(currentJob.payload, false) };
          await this.jobsService.failJob(job.id, "Cancelled by user", {
            currentAction: "cancelled",
            payload,
          });
          return;
        }
        if (code === 0) {
          payload = { ...payload, taskProgress: this.finishTaskProgress(payload, true) };
          await this.jobsService.completeJob(job.id, { currentAction: "completed", payload });
        } else {
          payload = { ...payload, taskProgress: this.finishTaskProgress(payload, false) };
          await this.jobsService.failJob(job.id, `Detector exited with code ${code ?? "unknown"}`, {
            currentAction: "failed",
            payload,
          });
        }
      });
    });

    return { started: true, pid: child.pid, jobId: job.id, command };
  }
}
