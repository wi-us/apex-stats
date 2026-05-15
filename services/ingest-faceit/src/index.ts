import "dotenv/config";
import { Queue, Worker } from "bullmq";
import IORedis from "ioredis";
import fs from "node:fs";
import path from "node:path";
import { FaceitClient } from "./clients/faceit-client";
import { detectFirstTwoRings, detectWorkFragment, downloadVideo } from "./workflow/video-workflow";
import { loadJobsStorePath } from "./config/runtime-paths";

const redisUrl = process.env.REDIS_URL ?? "redis://localhost:6379";
const connection = new IORedis(redisUrl, { maxRetriesPerRequest: null });
const ingestQueueName = "ingest-jobs";
const jobsStorePath = loadJobsStorePath(process.cwd());

const ingestQueue = new Queue(ingestQueueName, { connection });

type JobStatus = "queued" | "running" | "completed" | "failed";

function updateJobStore(
  jobId: string,
  patch: {
    status: JobStatus;
    progressPercent?: number;
    startedAt?: string;
    finishedAt?: string;
    durationMs?: number;
    errors?: string[];
  },
  payload: Record<string, unknown>
) {
  let jobs: any[] = [];
  if (fs.existsSync(jobsStorePath)) {
    try {
      const parsed = JSON.parse(fs.readFileSync(jobsStorePath, "utf-8")) as { jobs?: any[] };
      jobs = Array.isArray(parsed.jobs) ? parsed.jobs : [];
    } catch {
      jobs = [];
    }
  }
  const idx = jobs.findIndex((item) => item.id === jobId);
  if (idx >= 0) {
    jobs[idx] = { ...jobs[idx], ...patch };
  } else {
    jobs.unshift({
      id: jobId,
      jobType: "ingest",
      status: patch.status,
      command: `ingest faceitMatchId=${String(payload.faceitMatchId ?? "unknown")}`,
      progressPercent: patch.progressPercent ?? 0,
      queuedAt: new Date().toISOString(),
      startedAt: patch.startedAt,
      finishedAt: patch.finishedAt,
      durationMs: patch.durationMs,
      matchId: String(payload.faceitMatchId ?? ""),
      teamStatuses: [],
      errors: patch.errors ?? [],
      payload,
    });
  }
  fs.mkdirSync(path.dirname(jobsStorePath), { recursive: true });
  const tmp = `${jobsStorePath}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify({ jobs }, null, 2), "utf-8");
  fs.renameSync(tmp, jobsStorePath);
}

async function runWorker() {
  const apiKey = process.env.FACEIT_API_KEY;
  if (!apiKey) {
    throw new Error("FACEIT_API_KEY is required");
  }

  const faceit = new FaceitClient(apiKey);

  const worker = new Worker(
    ingestQueueName,
    async (job) => {
      const matchId = String(job.data.faceitMatchId);
      const metadata = await faceit.getMatchMetadata(matchId);

      const localVideoPath = `data/raw/${matchId}.mp4`;
      await downloadVideo(metadata.vodUrl, localVideoPath);

      const fragment = await detectWorkFragment(localVideoPath);
      const rings = await detectFirstTwoRings(fragment.outputVideoPath);

      return {
        metadata,
        fragment,
        rings
      };
    },
    { connection }
  );

  worker.on("completed", (job, result) => {
    const now = new Date().toISOString();
    const startedAt = (job?.processedOn ?? Date.now()) as number;
    const finishedAt = (job?.finishedOn ?? Date.now()) as number;
    updateJobStore(
      String(job?.id ?? "unknown"),
      {
        status: "completed",
        progressPercent: 100,
        startedAt: new Date(startedAt).toISOString(),
        finishedAt: now,
        durationMs: Math.max(0, finishedAt - startedAt),
        errors: [],
      },
      (job?.data ?? {}) as Record<string, unknown>
    );
    console.log("Ingest job completed:", job?.id, result?.metadata?.matchId);
  });

  worker.on("failed", (job, error) => {
    const now = new Date().toISOString();
    const startedAt = (job?.processedOn ?? Date.now()) as number;
    const finishedAt = (job?.finishedOn ?? Date.now()) as number;
    updateJobStore(
      String(job?.id ?? "unknown"),
      {
        status: "failed",
        progressPercent: Number(job?.progress ?? 0),
        startedAt: new Date(startedAt).toISOString(),
        finishedAt: now,
        durationMs: Math.max(0, finishedAt - startedAt),
        errors: [error.message],
      },
      (job?.data ?? {}) as Record<string, unknown>
    );
    console.error("Ingest job failed:", job?.id, error.message);
  });

  worker.on("active", (job) => {
    updateJobStore(
      String(job?.id ?? "unknown"),
      {
        status: "running",
        progressPercent: Number(job?.progress ?? 0),
        startedAt: new Date((job?.processedOn ?? Date.now()) as number).toISOString(),
      },
      (job?.data ?? {}) as Record<string, unknown>
    );
  });
}

async function bootstrap() {
  if (process.env.INGEST_ENQUEUE_DEMO === "1") {
    await ingestQueue.add("ingest-match", { faceitMatchId: process.env.DEMO_FACEIT_MATCH_ID ?? "demo" });
  }
  await runWorker();
}

bootstrap().catch((error) => {
  console.error(error);
  process.exit(1);
});
