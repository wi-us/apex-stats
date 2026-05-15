import {
  BadRequestException,
  Body,
  Controller,
  Get,
  Param,
  Post,
  Query,
  Res,
  UploadedFile,
  UseInterceptors,
} from "@nestjs/common";
import { FileInterceptor } from "@nestjs/platform-express";
import type { Response } from "express";
import * as fs from "node:fs";
import { MinimapLocatorService } from "./minimap-locator.service";
import { MinimapVideoUploadInterceptor } from "./minimap-video-upload.interceptor";

@Controller("admin/minimap-locator")
export class MinimapLocatorController {
  constructor(
    private readonly minimapLocatorService: MinimapLocatorService,
    private readonly minimapVideoUploadInterceptor: MinimapVideoUploadInterceptor
  ) {}

  @Get("maps")
  listMaps() {
    return { items: this.minimapLocatorService.listMaps() };
  }

  @Get("map-image")
  mapImage(@Query("mapId") mapId: string, @Res() res: Response) {
    if (!mapId?.trim()) {
      throw new BadRequestException("mapId is required");
    }
    const { absPath, mime } = this.minimapLocatorService.readMapImage(mapId.trim());
    res.setHeader("Content-Type", mime);
    res.setHeader("Cache-Control", "no-store");
    fs.createReadStream(absPath).pipe(res);
  }

  @Get("debug-file")
  debugFile(@Query("rel") rel: string, @Res() res: Response) {
    if (!rel?.trim()) {
      throw new BadRequestException("rel is required");
    }
    const { absPath, mime } = this.minimapLocatorService.readDebugFile(rel.trim());
    res.setHeader("Content-Type", mime);
    res.setHeader("Cache-Control", "no-store");
    fs.createReadStream(absPath).pipe(res);
  }

  @Post("locate")
  @UseInterceptors(FileInterceptor("file"))
  locate(
    @UploadedFile() file: Express.Multer.File | undefined,
    @Body("mapId") mapId: string,
    @Body("minimapX") minimapX: string,
    @Body("minimapY") minimapY: string,
    @Body("minimapSize") minimapSize: string,
    @Body("minimapBorder") minimapBorder: string,
    @Body("minScore") minScore: string,
    @Body("searchMode") searchMode: string
  ) {
    if (!file) {
      throw new BadRequestException("file is required (multipart field: file)");
    }
    if (!mapId?.trim()) {
      throw new BadRequestException("mapId is required");
    }
    const mode =
      searchMode === "tiled" ? "tiled" : searchMode === "full" ? "full" : "window";
    return this.minimapLocatorService.locateFromUpload(file, {
      mapId: mapId.trim(),
      minimapX: this.num(minimapX, 48, "minimapX"),
      minimapY: this.num(minimapY, 60, "minimapY"),
      minimapSize: this.num(minimapSize, 240, "minimapSize"),
      minimapBorder: this.num(minimapBorder, 10, "minimapBorder"),
      minScore: this.num(minScore, 0.35, "minScore"),
      searchMode: mode,
    });
  }

  @Post("video-jobs")
  @UseInterceptors(MinimapVideoUploadInterceptor)
  createVideoJob(
    @UploadedFile() file: Express.Multer.File | undefined,
    @Body("mapId") mapId: string,
    @Body("minimapX") minimapX: string,
    @Body("minimapY") minimapY: string,
    @Body("minimapSize") minimapSize: string,
    @Body("minimapBorder") minimapBorder: string,
    @Body("frameStep") frameStep: string,
    @Body("sampleIntervalSec") sampleIntervalSec: string,
    @Body("minScore") minScore: string,
    @Body("goodScore") goodScore: string,
    @Body("maxJumpDistance") maxJumpDistance: string,
    @Body("searchRadius") searchRadius: string,
    @Body("allowGlobalRelock") allowGlobalRelock: string,
    @Body("relockScore") relockScore: string,
    @Body("smoothing") smoothing: string,
    @Body("maxFrames") maxFrames: string,
    @Body("debugVideo") debugVideo: string,
    @Body("fastMode") fastMode: string,
    @Body("saveFrameDebug") saveFrameDebug: string
  ) {
    if (!file) {
      throw new BadRequestException("file is required (multipart field: file)");
    }
    if (!mapId?.trim()) {
      throw new BadRequestException("mapId is required");
    }
    return this.minimapLocatorService.createVideoJob(file, {
      mapId: mapId.trim(),
      minimapX: this.num(minimapX, 48, "minimapX"),
      minimapY: this.num(minimapY, 60, "minimapY"),
      minimapSize: this.num(minimapSize, 240, "minimapSize"),
      minimapBorder: this.num(minimapBorder, 12, "minimapBorder"),
      frameStep: this.num(frameStep, 0, "frameStep"),
      sampleIntervalSec: this.num(sampleIntervalSec, 1, "sampleIntervalSec"),
      minScore: this.num(minScore, 0.35, "minScore"),
      goodScore: this.num(goodScore, 0.55, "goodScore"),
      maxJumpDistance: this.num(maxJumpDistance, 120, "maxJumpDistance"),
      searchRadius: this.num(searchRadius, 180, "searchRadius"),
      allowGlobalRelock: this.bool(allowGlobalRelock, true),
      relockScore: this.num(relockScore, 0.55, "relockScore"),
      smoothing: this.bool(smoothing, true),
      maxFrames: maxFrames?.trim() ? this.num(maxFrames, 0, "maxFrames") : undefined,
      debugVideo: this.bool(debugVideo, false),
      fastMode: this.bool(fastMode, true),
      saveFrameDebug: this.bool(saveFrameDebug, false),
    });
  }

  @Get("video-jobs/:jobId")
  getVideoJob(@Param("jobId") jobId: string) {
    return this.minimapLocatorService.getVideoJob(jobId);
  }

  @Get("video-jobs/:jobId/result")
  getVideoJobResult(@Param("jobId") jobId: string) {
    return this.minimapLocatorService.getVideoJobResult(jobId);
  }

  @Get("video-jobs/:jobId/frame-debug")
  getVideoFrameDebug(@Param("jobId") jobId: string, @Query("frameIndex") frameIndex: string) {
    const fi = Number(frameIndex);
    if (!Number.isFinite(fi)) {
      throw new BadRequestException("frameIndex must be a number");
    }
    return this.minimapLocatorService.getVideoFrameDebugUrls(jobId, fi);
  }

  private num(raw: string, fallback: number, field: string): number {
    const n = Number(raw);
    if (!Number.isFinite(n)) {
      throw new BadRequestException(`${field} must be a number`);
    }
    return n;
  }

  private bool(raw: string | undefined, fallback: boolean): boolean {
    if (raw === undefined || raw === null || raw === "") return fallback;
    const v = String(raw).toLowerCase();
    return v === "true" || v === "1" || v === "yes";
  }
}
