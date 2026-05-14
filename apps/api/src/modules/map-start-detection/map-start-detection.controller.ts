import { Body, Controller, Get, Post, Query } from "@nestjs/common";
import { MapStartDetectionService } from "./map-start-detection.service";
import { MapStartDetectionRunService } from "./map-start-detection-run.service";

@Controller("map-start-detection")
export class MapStartDetectionController {
  constructor(
    private readonly mapStartDetectionService: MapStartDetectionService,
    private readonly mapStartDetectionRunService: MapStartDetectionRunService
  ) {}

  @Get("run-defaults")
  runDefaults() {
    return this.mapStartDetectionRunService.getDefaults();
  }

  @Post("run")
  run(@Body() body: Record<string, unknown>) {
    return this.mapStartDetectionRunService.run(body);
  }

  @Get("summary")
  summary() {
    return this.mapStartDetectionService.listSummaries();
  }

  @Get("video-detail")
  videoDetail(@Query("videoName") videoName: string) {
    return this.mapStartDetectionService.getVideoDetail(videoName);
  }
}
