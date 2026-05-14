import { Module } from "@nestjs/common";
import { JobsModule } from "../jobs/jobs.module";
import { MapStartDetectionController } from "./map-start-detection.controller";
import { MapStartDetectionRunService } from "./map-start-detection-run.service";
import { MapStartDetectionService } from "./map-start-detection.service";

@Module({
  imports: [JobsModule],
  controllers: [MapStartDetectionController],
  providers: [MapStartDetectionService, MapStartDetectionRunService],
})
export class MapStartDetectionModule {}
