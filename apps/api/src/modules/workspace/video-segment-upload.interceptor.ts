import type { CallHandler, ExecutionContext, NestInterceptor } from "@nestjs/common";
import { Injectable } from "@nestjs/common";
import * as fs from "node:fs";
import * as path from "node:path";
import { Observable } from "rxjs";
import type { Multer } from "multer";
import multer = require("multer");
import { WorkspaceService } from "./workspace.service";

const MAX_VIDEO_BYTES = 32 * 1024 * 1024 * 1024;

@Injectable()
export class VideoSegmentUploadInterceptor implements NestInterceptor {
  private readonly upload: Multer;

  constructor(private readonly workspaceService: WorkspaceService) {
    this.upload = multer({
      storage: multer.diskStorage({
        destination: (_req, _file, cb) => {
          const dest = this.workspaceService.getMediaRecordsAbsPath();
          fs.mkdirSync(dest, { recursive: true });
          cb(null, dest);
        },
        filename: (_req, file, cb) => {
          const raw = path.basename(file.originalname || "video.mp4");
          const safe = raw.replace(/[^\w.\- ()[\]]+/g, "_") || `upload_${Date.now()}.mp4`;
          cb(null, safe);
        },
      }),
      limits: { fileSize: MAX_VIDEO_BYTES },
    });
  }

  intercept(context: ExecutionContext, next: CallHandler): Observable<unknown> {
    const ctx = context.switchToHttp();
    const req = ctx.getRequest();
    const res = ctx.getResponse();
    return new Observable((subscriber) => {
      this.upload.single("file")(req, res, (err: unknown) => {
        if (err) {
          subscriber.error(err);
          return;
        }
        next.handle().subscribe(subscriber);
      });
    });
  }
}
