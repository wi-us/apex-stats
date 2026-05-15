import {
  BadRequestException,
  Injectable,
  NotFoundException,
  ServiceUnavailableException,
} from "@nestjs/common";
import { spawn, spawnSync } from "node:child_process";
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import { loadRuntimePaths } from "../../core/runtime-paths";

export interface MinimapLocatorMapOption {
  mapId: string;
  label: string;
  mapPath: string;
  exists: boolean;
}

export interface MinimapLocateDebugUrls {
  frameWithCropUrl: string;
  minimapRawUrl: string;
  minimapProcessedUrl: string;
  mapMatchUrl: string;
  matchedPatchUrl: string;
  candidateProcessedUrl: string;
  candidatesUrl: string;
  mapImageUrl: string;
  debugPanelUrl: string;
}

export interface MinimapLocateCandidate {
  x: number;
  y: number;
  w: number;
  h: number;
  score: number;
  windowSize: number;
}

export interface MinimapLocateResponse {
  ok: boolean;
  mapId: string;
  score: number;
  scale: number;
  windowSize: number;
  ambiguous: boolean;
  suspicious: boolean;
  bbox: { x: number; y: number; w: number; h: number };
  center: { x: number; y: number };
  debug: MinimapLocateDebugUrls;
  reason: string | null;
  uploadId: string;
  searchMode: string;
  topCandidates: MinimapLocateCandidate[];
}

interface LocateBody {
  mapId: string;
  minimapX: number;
  minimapY: number;
  minimapSize: number;
  minimapBorder: number;
  minScore: number;
  searchMode: "window" | "full" | "tiled";
}

export interface VideoJobBody {
  mapId: string;
  minimapX: number;
  minimapY: number;
  minimapSize: number;
  minimapBorder: number;
  frameStep: number;
  sampleIntervalSec: number;
  minScore: number;
  goodScore: number;
  maxJumpDistance: number;
  searchRadius: number;
  allowGlobalRelock: boolean;
  relockScore: number;
  smoothing: boolean;
  fastMode: boolean;
  saveFrameDebug: boolean;
  maxFrames?: number;
  debugVideo?: boolean;
}

export interface VideoJobStatusResponse {
  jobId: string;
  status: "queued" | "processing" | "completed" | "failed";
  mapId: string;
  processedFrames: number;
  totalFramesToProcess: number;
  currentFrameIndex: number;
  currentTimestampSec: number;
  acceptedPoints: number;
  rejectedJumps: number;
  lowScore: number;
  averageScore: number;
  error?: string;
}

export interface VideoTrackingPathPoint {
  frameIndex: number;
  timestampSec: number;
  center: { x: number; y: number } | null;
  smoothedCenter: { x: number; y: number } | null;
  bbox: { x: number; y: number; w: number; h: number } | null;
  score: number;
  windowSize: number | null;
  status: string;
  jumpDistance: number | null;
  reason: string | null;
}

export interface VideoJobResultResponse {
  ok: boolean;
  jobId: string;
  mapId: string;
  summary: Record<string, number>;
  path: VideoTrackingPathPoint[];
  debug: {
    resultJsonUrl: string;
    trajectoryImageUrl: string;
    trajectoryCleanUrl: string;
    debugVideoUrl: string | null;
    mapImageUrl: string;
  };
}

@Injectable()
export class MinimapLocatorService {
  private readonly projectRoot = path.resolve(__dirname, "../../../../..");
  private readonly runtimePaths = loadRuntimePaths(this.projectRoot);
  private readonly cliPath = path.join(
    this.projectRoot,
    "services",
    "analysis",
    "app",
    "minimap_locator",
    "cli.py"
  );

  listMaps(): MinimapLocatorMapOption[] {
    const mapsDir = this.runtimePaths.media.mapsDir;
    const options: MinimapLocatorMapOption[] = [];
    const seen = new Set<string>();

    const mapsCfgPath = path.join(this.projectRoot, "config", "maps.json");
    if (fs.existsSync(mapsCfgPath)) {
      try {
        const raw = JSON.parse(fs.readFileSync(mapsCfgPath, "utf-8")) as {
          referenceFiles?: string[];
        };
        for (const fileName of raw.referenceFiles ?? []) {
          const mapId = path.basename(fileName, path.extname(fileName));
          if (seen.has(mapId)) continue;
          seen.add(mapId);
          const mapPath = path.join(mapsDir, fileName);
          options.push({
            mapId,
            label: this.humanizeMapId(mapId),
            mapPath,
            exists: fs.existsSync(mapPath),
          });
        }
      } catch {
        // ignore malformed config
      }
    }

    if (fs.existsSync(mapsDir)) {
      for (const entry of fs.readdirSync(mapsDir, { withFileTypes: true })) {
        if (!entry.isFile()) continue;
        if (!/\.(png|webp|jpe?g)$/i.test(entry.name)) continue;
        const mapId = path.basename(entry.name, path.extname(entry.name));
        if (seen.has(mapId)) continue;
        seen.add(mapId);
        const mapPath = path.join(mapsDir, entry.name);
        options.push({
          mapId,
          label: this.humanizeMapId(mapId),
          mapPath,
          exists: true,
        });
      }
    }

    return options.sort((a, b) => a.mapId.localeCompare(b.mapId));
  }

  resolveMapPath(mapId: string): string {
    const maps = this.listMaps();
    const hit = maps.find((m) => m.mapId === mapId);
    if (!hit) {
      throw new NotFoundException(`Unknown map_id: ${mapId}`);
    }
    if (!hit.exists || !fs.existsSync(hit.mapPath)) {
      throw new NotFoundException(`Map file not found for map_id=${mapId}: ${hit.mapPath}`);
    }
    return hit.mapPath;
  }

  readMapImage(mapId: string): { absPath: string; mime: string } {
    const absPath = this.resolveMapPath(mapId);
    const ext = path.extname(absPath).toLowerCase();
    const mime =
      ext === ".webp"
        ? "image/webp"
        : ext === ".png"
          ? "image/png"
          : ext === ".jpg" || ext === ".jpeg"
            ? "image/jpeg"
            : "application/octet-stream";
    return { absPath, mime };
  }

  readDebugFile(relPath: string): { absPath: string; mime: string } {
    const normalized = relPath.replace(/\\/g, "/").replace(/^\/+/, "");
    if (!normalized.startsWith("output/minimap_locator/")) {
      throw new BadRequestException("debug path must be under output/minimap_locator/");
    }
    const absPath = path.resolve(this.projectRoot, normalized);
    const allowedRoot = path.resolve(this.projectRoot, "output", "minimap_locator");
    if (!absPath.startsWith(allowedRoot)) {
      throw new BadRequestException("invalid debug path");
    }
    if (!fs.existsSync(absPath)) {
      throw new NotFoundException(`debug file not found: ${normalized}`);
    }
    const ext = path.extname(absPath).toLowerCase();
    const mime =
      ext === ".png"
        ? "image/png"
        : ext === ".jpg" || ext === ".jpeg"
          ? "image/jpeg"
          : ext === ".webp"
            ? "image/webp"
            : ext === ".mp4"
              ? "video/mp4"
              : ext === ".webm"
                ? "video/webm"
                : ext === ".mov"
                  ? "video/quicktime"
                  : "application/octet-stream";
    return { absPath, mime };
  }

  locateFromUpload(
    file: { buffer?: Buffer; path?: string; originalname?: string; mimetype?: string },
    body: LocateBody
  ): MinimapLocateResponse {
    let uploadBuffer = file.buffer;
    if ((!uploadBuffer || uploadBuffer.length === 0) && file.path && fs.existsSync(file.path)) {
      uploadBuffer = fs.readFileSync(file.path);
    }
    if (!uploadBuffer?.length) {
      throw new BadRequestException("file is required");
    }
    const mime = String(file.mimetype ?? "");
    if (mime && !mime.startsWith("image/")) {
      throw new BadRequestException(`unsupported file type: ${mime}`);
    }

    const mapPath = this.resolveMapPath(body.mapId);
    const uploadId = crypto.randomUUID();
    const outDir = path.join(this.projectRoot, "output", "minimap_locator", "manual_uploads", uploadId);
    fs.mkdirSync(outDir, { recursive: true });

    const ext = this.guessImageExt(file.originalname, mime);
    const framePath = path.join(outDir, `input${ext}`);
    fs.writeFileSync(framePath, uploadBuffer);
    if (!fs.existsSync(framePath) || fs.statSync(framePath).size === 0) {
      throw new BadRequestException(`failed to save uploaded frame to ${framePath}`);
    }

    if (!fs.existsSync(this.cliPath)) {
      throw new ServiceUnavailableException(`minimap locator CLI not found: ${this.cliPath}`);
    }

    const py = this.resolvePythonBin();
    const args = [
      this.cliPath,
      "--frame",
      framePath,
      "--map-id",
      body.mapId,
      "--map-path",
      mapPath.replace(/\\/g, "/"),
      "--output-dir",
      outDir.replace(/\\/g, "/"),
      "--minimap-x",
      String(body.minimapX),
      "--minimap-y",
      String(body.minimapY),
      "--minimap-size",
      String(body.minimapSize),
      "--minimap-border",
      String(body.minimapBorder),
      "--min-score",
      String(body.minScore),
      "--search-mode",
      body.searchMode,
    ];

    const proc = spawnSync(py, args, {
      cwd: this.projectRoot,
      encoding: "utf-8",
      maxBuffer: 16 * 1024 * 1024,
      windowsHide: true,
    });

    const resultJsonPath = path.join(outDir, "frame_000001_result.json");
    if (!fs.existsSync(resultJsonPath)) {
      const stderr = (proc.stderr || "").trim();
      const stdout = (proc.stdout || "").trim();
      throw new ServiceUnavailableException(
        `minimap locator failed (exit ${proc.status ?? "?"}): ${stderr || stdout || "no result json"}`
      );
    }

    const parsed = JSON.parse(fs.readFileSync(resultJsonPath, "utf-8")) as {
      search_mode?: string;
      match?: {
        ok?: boolean;
        score?: number;
        scale?: number;
        window_size?: number;
        ambiguous?: boolean;
        suspicious?: boolean;
        bbox?: { x: number; y: number; w: number; h: number };
        center?: { x: number; y: number };
        reason?: string | null;
      };
      top_candidates?: Array<{
        x: number;
        y: number;
        w: number;
        h: number;
        score: number;
        window_size?: number;
      }>;
    };
    const match = parsed.match ?? {};
    const bbox = match.bbox ?? { x: 0, y: 0, w: 0, h: 0 };
    const center = match.center ?? { x: 0, y: 0 };
    const score = Number(match.score ?? 0);
    const scale = Number(match.scale ?? 0);
    const windowSize = Number(match.window_size ?? bbox.w ?? 0);
    const ok = Boolean(match.ok);
    const ambiguous = Boolean(match.ambiguous);
    const suspicious = Boolean(match.suspicious);
    const reason =
      typeof match.reason === "string"
        ? match.reason
        : !ok
          ? "Score below threshold"
          : null;

    const topCandidates: MinimapLocateCandidate[] = (parsed.top_candidates ?? []).map((c) => ({
      x: Number(c.x ?? 0),
      y: Number(c.y ?? 0),
      w: Number(c.w ?? 0),
      h: Number(c.h ?? 0),
      score: Number(c.score ?? 0),
      windowSize: Number(c.window_size ?? c.w ?? 0),
    }));

    const relBase = `output/minimap_locator/manual_uploads/${uploadId}`;
    const debug: MinimapLocateDebugUrls = {
      frameWithCropUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/frame_000001_frame_crop.jpg`,
      minimapRawUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/frame_000001_minimap_raw.jpg`,
      minimapProcessedUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/frame_000001_minimap_processed.jpg`,
      mapMatchUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/frame_000001_map_match.jpg`,
      matchedPatchUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/frame_000001_matched_patch.jpg`,
      candidateProcessedUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/frame_000001_candidate_processed.jpg`,
      candidatesUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/frame_000001_candidates.jpg`,
      mapImageUrl: `/admin/minimap-locator/map-image?mapId=${encodeURIComponent(body.mapId)}`,
      debugPanelUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/frame_000001_debug.jpg`,
    };

    return {
      ok,
      mapId: body.mapId,
      score,
      scale,
      windowSize,
      ambiguous,
      suspicious,
      bbox: {
        x: Number(bbox.x ?? 0),
        y: Number(bbox.y ?? 0),
        w: Number(bbox.w ?? 0),
        h: Number(bbox.h ?? 0),
      },
      center: {
        x: Number(center.x ?? 0),
        y: Number(center.y ?? 0),
      },
      debug,
      reason,
      uploadId,
      searchMode: parsed.search_mode ?? body.searchMode,
      topCandidates,
    };
  }

  private resolvePythonBin(): string {
    const fromEnv = process.env.PYTHON?.trim() || process.env.PYTHON_BIN?.trim();
    if (fromEnv) return fromEnv;
    return process.platform === "win32" ? "python" : "python3";
  }

  private humanizeMapId(mapId: string): string {
    return mapId
      .replace(/^mp_/, "")
      .replace(/_/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  createVideoJob(
    file: { buffer?: Buffer; path?: string; originalname?: string; mimetype?: string },
    body: VideoJobBody
  ): { jobId: string; status: string } {
    if (!file) {
      throw new BadRequestException("file is required");
    }

    const mapPath = this.resolveMapPath(body.mapId);
    const jobId = crypto.randomUUID();
    const outDir = path.join(this.projectRoot, "output", "minimap_locator", "video_jobs", jobId);
    fs.mkdirSync(outDir, { recursive: true });

    const ext = this.guessVideoExt(file.originalname, file.mimetype);
    const videoPath = path.join(outDir, `input_video${ext}`);

    if (file.path && fs.existsSync(file.path)) {
      fs.copyFileSync(file.path, videoPath);
      try {
        fs.unlinkSync(file.path);
      } catch {
        // ignore temp cleanup errors
      }
    } else if (file.buffer?.length) {
      fs.writeFileSync(videoPath, file.buffer);
    } else {
      throw new BadRequestException("file is required");
    }
    if (!fs.existsSync(videoPath) || fs.statSync(videoPath).size === 0) {
      throw new BadRequestException("failed to save uploaded video file");
    }

    fs.writeFileSync(
      path.join(outDir, "job.json"),
      JSON.stringify(
        {
          jobId,
          status: "queued",
          mapId: body.mapId,
          createdAt: new Date().toISOString(),
        },
        null,
        2
      )
    );

    this.spawnVideoTracker(jobId, videoPath, mapPath, outDir, body);
    return { jobId, status: "processing" };
  }

  getVideoJob(jobId: string): VideoJobStatusResponse {
    const outDir = this.videoJobDir(jobId);
    if (!fs.existsSync(outDir)) {
      throw new NotFoundException(`video job not found: ${jobId}`);
    }

    let jobMeta: { mapId?: string; status?: string; error?: string } = {};
    const jobPath = path.join(outDir, "job.json");
    if (fs.existsSync(jobPath)) {
      try {
        jobMeta = JSON.parse(fs.readFileSync(jobPath, "utf-8")) as typeof jobMeta;
      } catch {
        // ignore
      }
    }

    let progress: Record<string, unknown> = {};
    const progressPath = path.join(outDir, "progress.json");
    if (fs.existsSync(progressPath)) {
      try {
        progress = JSON.parse(fs.readFileSync(progressPath, "utf-8")) as Record<string, unknown>;
      } catch {
        // ignore
      }
    }

    const resultExists = fs.existsSync(path.join(outDir, "result.json"));
    let status: VideoJobStatusResponse["status"] = "queued";
    if (jobMeta.status === "failed") status = "failed";
    else if (resultExists || progress.status === "completed") status = "completed";
    else if (progress.status === "processing" || fs.existsSync(path.join(outDir, "process.stdout.log")))
      status = "processing";

    return {
      jobId,
      status,
      mapId: String(jobMeta.mapId ?? ""),
      processedFrames: Number(progress.processed_frames ?? 0),
      totalFramesToProcess: Number(progress.total_frames_to_process ?? 0),
      currentFrameIndex: Number(progress.current_frame_index ?? 0),
      currentTimestampSec: Number(progress.current_timestamp_sec ?? 0),
      acceptedPoints: Number(progress.accepted_points ?? 0),
      rejectedJumps: Number(progress.rejected_jumps ?? 0),
      lowScore: Number(progress.low_score ?? 0),
      averageScore: Number(progress.average_score ?? 0),
      error: jobMeta.error,
    };
  }

  getVideoJobResult(jobId: string): VideoJobResultResponse {
    const outDir = this.videoJobDir(jobId);
    const resultPath = path.join(outDir, "result.json");
    if (!fs.existsSync(resultPath)) {
      throw new NotFoundException(`video job result not ready: ${jobId}`);
    }

    const parsed = JSON.parse(fs.readFileSync(resultPath, "utf-8")) as {
      map_id?: string;
      summary?: Record<string, number>;
      points?: Array<{
        frame_index: number;
        timestamp_sec: number;
        status: string;
        score: number;
        window_size?: number | null;
        jump_distance?: number | null;
        reason?: string | null;
        bbox?: { x: number; y: number; w: number; h: number } | null;
        center?: { x: number; y: number } | null;
        smoothed_center?: { x: number; y: number } | null;
      }>;
    };

    const relBase = `output/minimap_locator/video_jobs/${jobId}`;
    const debugVideoPath = path.join(outDir, "debug_video.mp4");

    const pathPoints: VideoTrackingPathPoint[] = (parsed.points ?? []).map((p) => ({
      frameIndex: Number(p.frame_index),
      timestampSec: Number(p.timestamp_sec),
      center: p.center ?? null,
      smoothedCenter: p.smoothed_center ?? null,
      bbox: p.bbox ?? null,
      score: Number(p.score ?? 0),
      windowSize: p.window_size ?? null,
      status: String(p.status),
      jumpDistance: p.jump_distance ?? null,
      reason: p.reason ?? null,
    }));

    const summary = parsed.summary ?? {};
    const mapId = String(parsed.map_id ?? "");

    return {
      ok: Number(summary.accepted_points ?? 0) > 0,
      jobId,
      mapId,
      summary,
      path: pathPoints,
      debug: {
        resultJsonUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/result.json`,
        trajectoryImageUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/trajectory_map.jpg`,
        trajectoryCleanUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/trajectory_map_clean.jpg`,
        debugVideoUrl: fs.existsSync(debugVideoPath)
          ? `/admin/minimap-locator/debug-file?rel=${relBase}/debug_video.mp4`
          : null,
        mapImageUrl: `/admin/minimap-locator/map-image?mapId=${encodeURIComponent(mapId)}`,
      },
    };
  }

  getVideoFrameDebugUrls(jobId: string, frameIndex: number) {
    const relBase = `output/minimap_locator/video_jobs/${jobId}/frames`;
    const prefix = `frame_${String(frameIndex).padStart(6, "0")}`;
    return {
      frameWithCropUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/${prefix}_frame_crop.jpg`,
      minimapRawUrl: `/admin/minimap-locator/debug-file?rel=${relBase.replace("/frames", "/minimaps")}/${prefix}_minimap_raw.jpg`,
      minimapProcessedUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/${prefix}_minimap_processed.jpg`,
      mapMatchUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/${prefix}_map_match.jpg`,
      matchedPatchUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/${prefix}_matched_patch.jpg`,
      debugPanelUrl: `/admin/minimap-locator/debug-file?rel=${relBase}/${prefix}_debug.jpg`,
    };
  }

  private videoJobDir(jobId: string): string {
    const safe = jobId.replace(/[^a-zA-Z0-9-]/g, "");
    if (!safe) throw new BadRequestException("invalid jobId");
    return path.join(this.projectRoot, "output", "minimap_locator", "video_jobs", safe);
  }

  private spawnVideoTracker(
    jobId: string,
    videoPath: string,
    mapPath: string,
    outDir: string,
    body: VideoJobBody
  ): void {
    if (!fs.existsSync(this.cliPath)) {
      throw new ServiceUnavailableException(`minimap locator CLI not found: ${this.cliPath}`);
    }

    const py = this.resolvePythonBin();
    const args = [
      this.cliPath,
      "--video",
      videoPath.replace(/\\/g, "/"),
      "--map-id",
      body.mapId,
      "--map-path",
      mapPath.replace(/\\/g, "/"),
      "--output-dir",
      outDir.replace(/\\/g, "/"),
      "--minimap-x",
      String(body.minimapX),
      "--minimap-y",
      String(body.minimapY),
      "--minimap-size",
      String(body.minimapSize),
      "--minimap-border",
      String(body.minimapBorder),
      "--frame-step",
      String(body.frameStep),
      "--sample-interval-sec",
      String(body.sampleIntervalSec),
      "--min-score",
      String(body.minScore),
      "--good-score",
      String(body.goodScore),
      "--max-jump-distance",
      String(body.maxJumpDistance),
      "--search-radius",
      String(body.searchRadius),
      "--relock-score",
      String(body.relockScore),
      "--search-mode",
      "window",
    ];
    if (body.maxFrames !== undefined) {
      args.push("--max-frames", String(body.maxFrames));
    }
    if (body.debugVideo) args.push("--debug-video");
    if (body.allowGlobalRelock) args.push("--allow-global-relock");
    else args.push("--no-allow-global-relock");
    if (body.smoothing) args.push("--smoothing");
    else args.push("--no-smoothing");
    if (body.fastMode) args.push("--fast-mode");
    else args.push("--no-fast-mode");
    if (body.saveFrameDebug) args.push("--save-frame-debug");
    else args.push("--no-save-frame-debug");

    const logOut = path.join(outDir, "process.stdout.log");
    const logErr = path.join(outDir, "process.stderr.log");
    fs.writeFileSync(logOut, `[${new Date().toISOString()}] spawn ${py} ${args.join(" ")}\n`, {
      flag: "a",
    });

    const child = spawn(py, args, {
      cwd: this.projectRoot,
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });

    const appendLog = (target: string, chunk: Buffer) => {
      if (!chunk.length) return;
      try {
        fs.appendFileSync(target, chunk);
      } catch {
        // ignore log write errors
      }
    };
    child.stdout?.on("data", (chunk: Buffer) => appendLog(logOut, chunk));
    child.stderr?.on("data", (chunk: Buffer) => appendLog(logErr, chunk));
    child.on("error", (spawnErr) => {
      try {
        fs.appendFileSync(
          logErr,
          `\n[spawn error] ${spawnErr instanceof Error ? spawnErr.message : String(spawnErr)}\n`
        );
        fs.writeFileSync(
          path.join(outDir, "job.json"),
          JSON.stringify({ jobId, status: "failed", error: String(spawnErr) }, null, 2)
        );
      } catch {
        // ignore
      }
    });
    child.unref();

    fs.writeFileSync(
      path.join(outDir, "job.json"),
      JSON.stringify({ jobId, status: "processing", mapId: body.mapId }, null, 2)
    );
  }

  private guessVideoExt(originalName?: string, mime?: string): string {
    const fromName = originalName ? path.extname(originalName).toLowerCase() : "";
    if ([".mp4", ".mov", ".mkv", ".webm", ".avi"].includes(fromName)) return fromName;
    if (mime === "video/quicktime") return ".mov";
    if (mime === "video/webm") return ".webm";
    if (mime === "video/x-matroska") return ".mkv";
    return ".mp4";
  }

  private guessImageExt(originalName?: string, mime?: string): string {
    const fromName = originalName ? path.extname(originalName).toLowerCase() : "";
    if (fromName === ".png" || fromName === ".jpg" || fromName === ".jpeg" || fromName === ".webp") {
      return fromName;
    }
    if (mime === "image/png") return ".png";
    if (mime === "image/webp") return ".webp";
    return ".jpg";
  }
}
