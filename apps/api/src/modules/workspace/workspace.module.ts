import { Module } from "@nestjs/common";
import { VideoSegmentUploadInterceptor } from "./video-segment-upload.interceptor";
import { WorkspaceController } from "./workspace.controller";
import { WorkspaceService } from "./workspace.service";

@Module({
  controllers: [WorkspaceController],
  providers: [WorkspaceService, VideoSegmentUploadInterceptor],
})
export class WorkspaceModule {}

