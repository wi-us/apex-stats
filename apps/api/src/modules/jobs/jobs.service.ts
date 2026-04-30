import { Injectable, NotFoundException } from "@nestjs/common";
import * as fs from "node:fs";
import * as path from "node:path";
import { loadRuntimePaths } from "../runtime-paths";
import { Pool } from "pg";
import { getPostgresPool } from "../postgres";
import { DataSourceMode, resolveDataSourceMode } from "../data-source-mode";

type JobType = "ingest" | "analysis";
type JobStatus = "queued" | "running" | "completed" | "failed";

interface TeamJobStatus {
  teamId: string;
  teamName: string;
  status: JobStatus;
  progressPercent: number;
  lastFrame?: number;
  lastTimestampSec?: number;
  error?: string;
}

export interface JobRecord {
  id: string;
  jobType: JobType;
  status: JobStatus;
  command: string;
  currentAction?: string;
  lastHeartbeatAt?: string;
  progressPercent: number;
  queuedAt: string;
  startedAt?: string;
  finishedAt?: string;
  durationMs?: number;
  mapId?: string;
  matchId?: string;
  video?: string;
  teamStatuses: TeamJobStatus[];
  errors: string[];
  payload: Record<string, unknown>;
}

interface JobsStore {
  jobs: JobRecord[];
}

interface PgJobRow {
  id: string;
  job_type: JobType;
  status: JobStatus;
  command: string;
  current_action: string | null;
  last_heartbeat_at: string | null;
  progress_percent: number;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  map_id: string | null;
  match_id: string | null;
  video: string | null;
  team_statuses: unknown;
  errors: unknown;
  payload: unknown;
}

@Injectable()
export class JobsService {
  private readonly projectRoot = path.resolve(__dirname, "../../../../..");
  private readonly storePath = loadRuntimePaths(this.projectRoot).artifacts.jobsStore;
  private readonly postgresPool: Pool = getPostgresPool();
  private readonly jobsSourceMode: DataSourceMode = resolveDataSourceMode(process.env.JOBS_SOURCE, "postgres");

  private loadStore(): JobsStore {
    if (!fs.existsSync(this.storePath)) {
      return { jobs: [] };
    }
    try {
      const raw = fs.readFileSync(this.storePath, "utf-8");
      const parsed = JSON.parse(raw) as Partial<JobsStore>;
      return { jobs: Array.isArray(parsed.jobs) ? parsed.jobs : [] };
    } catch {
      return { jobs: [] };
    }
  }

  private saveStore(store: JobsStore): void {
    fs.mkdirSync(path.dirname(this.storePath), { recursive: true });
    const tmp = `${this.storePath}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(store, null, 2), "utf-8");
    fs.renameSync(tmp, this.storePath);
  }

  private createJobId(jobType: JobType): string {
    return `${jobType}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  }

  private async addJobPg(record: JobRecord): Promise<JobRecord> {
    await this.postgresPool.query(
      `INSERT INTO api_jobs (
         id, job_type, status, command, current_action, last_heartbeat_at, progress_percent,
         queued_at, started_at, finished_at, duration_ms, map_id, match_id, video, team_statuses, errors, payload
       ) VALUES (
         $1,$2,$3,$4,$5,$6,$7,
         $8,$9,$10,$11,$12,$13,$14,$15::jsonb,$16::jsonb,$17::jsonb
       )`,
      [
        record.id,
        record.jobType,
        record.status,
        record.command,
        record.currentAction ?? null,
        record.lastHeartbeatAt ?? null,
        record.progressPercent,
        record.queuedAt,
        record.startedAt ?? null,
        record.finishedAt ?? null,
        record.durationMs ?? null,
        record.mapId ?? null,
        record.matchId ?? null,
        record.video ?? null,
        JSON.stringify(record.teamStatuses ?? []),
        JSON.stringify(record.errors ?? []),
        JSON.stringify(record.payload ?? {}),
      ]
    );
    return record;
  }

  private async listJobsPg(filters: {
    jobType?: JobType;
    status?: JobStatus;
    page?: number;
    pageSize?: number;
  }) {
    const page = Math.max(1, Number(filters.page ?? 1));
    const pageSize = Math.min(200, Math.max(1, Number(filters.pageSize ?? 20)));
    const clauses: string[] = [];
    const params: unknown[] = [];
    if (filters.jobType) {
      params.push(filters.jobType);
      clauses.push(`job_type = $${params.length}`);
    }
    if (filters.status) {
      params.push(filters.status);
      clauses.push(`status = $${params.length}`);
    }
    const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
    const countRes = await this.postgresPool.query(`SELECT COUNT(*)::int AS total FROM api_jobs ${where}`, params);
    const total = Number(countRes.rows[0]?.total ?? 0);
    params.push((page - 1) * pageSize, pageSize);
    const rows = await this.postgresPool.query<PgJobRow>(
      `SELECT id, job_type, status, command, current_action, last_heartbeat_at, progress_percent,
              queued_at, started_at, finished_at, duration_ms, map_id, match_id, video, team_statuses, errors, payload
       FROM api_jobs ${where}
       ORDER BY queued_at DESC
       OFFSET $${params.length - 1}
       LIMIT $${params.length}`,
      params
    );
    const items = rows.rows.map((row: PgJobRow) => ({
      id: String(row.id),
      jobType: row.job_type as JobType,
      status: row.status as JobStatus,
      command: String(row.command ?? ""),
      currentAction: row.current_action ?? undefined,
      lastHeartbeatAt: row.last_heartbeat_at ?? undefined,
      progressPercent: Number(row.progress_percent ?? 0),
      queuedAt: String(row.queued_at),
      startedAt: row.started_at ?? undefined,
      finishedAt: row.finished_at ?? undefined,
      durationMs: row.duration_ms ?? undefined,
      mapId: row.map_id ?? undefined,
      matchId: row.match_id ?? undefined,
      video: row.video ?? undefined,
      teamStatuses: Array.isArray(row.team_statuses) ? row.team_statuses : [],
      errors: Array.isArray(row.errors) ? row.errors : [],
      payload: row.payload && typeof row.payload === "object" ? (row.payload as Record<string, unknown>) : {},
    })) as JobRecord[];
    return { page, pageSize, total, items };
  }

  private async getJobPg(jobId: string): Promise<JobRecord | null> {
    const rows = await this.postgresPool.query<PgJobRow>(
      `SELECT id, job_type, status, command, current_action, last_heartbeat_at, progress_percent,
              queued_at, started_at, finished_at, duration_ms, map_id, match_id, video, team_statuses, errors, payload
       FROM api_jobs
       WHERE id = $1
       LIMIT 1`,
      [jobId]
    );
    if (!rows.rowCount) return null;
    const row = rows.rows[0];
    return {
      id: String(row.id),
      jobType: row.job_type as JobType,
      status: row.status as JobStatus,
      command: String(row.command ?? ""),
      currentAction: row.current_action ?? undefined,
      lastHeartbeatAt: row.last_heartbeat_at ?? undefined,
      progressPercent: Number(row.progress_percent ?? 0),
      queuedAt: String(row.queued_at),
      startedAt: row.started_at ?? undefined,
      finishedAt: row.finished_at ?? undefined,
      durationMs: row.duration_ms ?? undefined,
      mapId: row.map_id ?? undefined,
      matchId: row.match_id ?? undefined,
      video: row.video ?? undefined,
      teamStatuses: Array.isArray(row.team_statuses) ? row.team_statuses : [],
      errors: Array.isArray(row.errors) ? row.errors : [],
      payload: row.payload && typeof row.payload === "object" ? (row.payload as Record<string, unknown>) : {},
    };
  }

  private addJob(record: JobRecord): JobRecord {
    const store = this.loadStore();
    store.jobs.unshift(record);
    this.saveStore(store);
    return record;
  }

  async enqueueIngest(faceitMatchId: string) {
    const record: JobRecord = {
      id: this.createJobId("ingest"),
      jobType: "ingest",
      status: "queued",
      command: `ingest faceitMatchId=${faceitMatchId}`,
      progressPercent: 0,
      queuedAt: new Date().toISOString(),
      teamStatuses: [],
      errors: [],
      payload: { faceitMatchId },
      matchId: faceitMatchId,
    };
    if (this.jobsSourceMode !== "sqlite") {
      try {
        return await this.addJobPg(record);
      } catch {
        // fallback to file store
      }
    }
    return this.addJob(record);
  }

  async enqueueAnalysis(mapId: string) {
    const record: JobRecord = {
      id: this.createJobId("analysis"),
      jobType: "analysis",
      status: "queued",
      command: `analysis mapId=${mapId}`,
      progressPercent: 0,
      queuedAt: new Date().toISOString(),
      teamStatuses: [],
      errors: [],
      payload: { mapId },
      mapId,
    };
    if (this.jobsSourceMode !== "sqlite") {
      try {
        return await this.addJobPg(record);
      } catch {
        // fallback to file store
      }
    }
    return this.addJob(record);
  }

  async listJobs(filters: {
    jobType?: JobType;
    status?: JobStatus;
    page?: number;
    pageSize?: number;
  }) {
    if (this.jobsSourceMode !== "sqlite") {
      try {
        const pgResult = await this.listJobsPg(filters);
        if (pgResult.items.length) {
          if (this.jobsSourceMode === "hybrid") {
            const store = this.loadStore();
            let fallbackJobs = store.jobs;
            if (filters.jobType) fallbackJobs = fallbackJobs.filter((job) => job.jobType === filters.jobType);
            if (filters.status) fallbackJobs = fallbackJobs.filter((job) => job.status === filters.status);
            if (fallbackJobs.length !== pgResult.total) {
              // eslint-disable-next-line no-console
              console.warn(`[jobs][hybrid] mismatch totals pg=${pgResult.total} file=${fallbackJobs.length}`);
            }
          }
          return pgResult;
        }
      } catch {
        // fallback to file store
      }
    }
    const page = Math.max(1, Number(filters.page ?? 1));
    const pageSize = Math.min(200, Math.max(1, Number(filters.pageSize ?? 20)));
    const store = this.loadStore();
    let jobs = store.jobs;
    if (filters.jobType) jobs = jobs.filter((job) => job.jobType === filters.jobType);
    if (filters.status) jobs = jobs.filter((job) => job.status === filters.status);
    const total = jobs.length;
    const from = (page - 1) * pageSize;
    const to = from + pageSize;
    return { page, pageSize, total, items: jobs.slice(from, to) };
  }

  async getJob(jobId: string): Promise<JobRecord> {
    if (this.jobsSourceMode !== "sqlite") {
      try {
        const fromPg = await this.getJobPg(jobId);
        if (fromPg) return fromPg;
      } catch {
        // fallback to file store
      }
    }
    const store = this.loadStore();
    const job = store.jobs.find((entry) => entry.id === jobId);
    if (!job) {
      throw new NotFoundException(`Job not found: ${jobId}`);
    }
    return job;
  }
}
