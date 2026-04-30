import { Injectable, NotFoundException } from "@nestjs/common";
import * as fs from "node:fs";
import * as path from "node:path";
import { loadRuntimePaths } from "../runtime-paths";

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

@Injectable()
export class JobsService {
  private readonly projectRoot = path.resolve(__dirname, "../../../../..");
  private readonly storePath = loadRuntimePaths(this.projectRoot).artifacts.jobsStore;

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

  private addJob(record: JobRecord): JobRecord {
    const store = this.loadStore();
    store.jobs.unshift(record);
    this.saveStore(store);
    return record;
  }

  enqueueIngest(faceitMatchId: string) {
    return this.addJob({
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
    });
  }

  enqueueAnalysis(mapId: string) {
    return this.addJob({
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
    });
  }

  listJobs(filters: {
    jobType?: JobType;
    status?: JobStatus;
    page?: number;
    pageSize?: number;
  }) {
    const page = Math.max(1, Number(filters.page ?? 1));
    const pageSize = Math.min(200, Math.max(1, Number(filters.pageSize ?? 20)));
    const store = this.loadStore();
    let jobs = store.jobs;
    if (filters.jobType) {
      jobs = jobs.filter((job) => job.jobType === filters.jobType);
    }
    if (filters.status) {
      jobs = jobs.filter((job) => job.status === filters.status);
    }
    const total = jobs.length;
    const from = (page - 1) * pageSize;
    const to = from + pageSize;
    return {
      page,
      pageSize,
      total,
      items: jobs.slice(from, to),
    };
  }

  getJob(jobId: string): JobRecord {
    const store = this.loadStore();
    const job = store.jobs.find((entry) => entry.id === jobId);
    if (!job) {
      throw new NotFoundException(`Job not found: ${jobId}`);
    }
    return job;
  }
}
