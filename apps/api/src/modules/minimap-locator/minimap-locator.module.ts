import { Module } from "@nestjs/common";
import { MinimapLocatorController } from "./minimap-locator.controller";
import { MinimapLocatorService } from "./minimap-locator.service";
import { MinimapVideoUploadInterceptor } from "./minimap-video-upload.interceptor";

@Module({
  controllers: [MinimapLocatorController],
  providers: [MinimapLocatorService, MinimapVideoUploadInterceptor],
})
export class MinimapLocatorModule {}
