import type { CallHandler, ExecutionContext, NestInterceptor } from "@nestjs/common";
import { Injectable, PayloadTooLargeException } from "@nestjs/common";
import * as fs from "node:fs";
import * as path from "node:path";
import { Observable } from "rxjs";
import type { Multer } from "multer";
import multer = require("multer");

const MAX_VIDEO_BYTES = 4 * 1024 * 1024 * 1024;

@Injectable()
export class MinimapVideoUploadInterceptor implements NestInterceptor {
  private readonly upload: Multer;
  private readonly uploadRoot: string;

  constructor() {
    this.uploadRoot = path.resolve(process.cwd(), "output", "minimap_locator", "_uploads");
    fs.mkdirSync(this.uploadRoot, { recursive: true });
    this.upload = multer({
      storage: multer.diskStorage({
        destination: (_req, _file, cb) => {
          fs.mkdirSync(this.uploadRoot, { recursive: true });
          cb(null, this.uploadRoot);
        },
        filename: (_req, file, cb) => {
          const raw = path.basename(file.originalname || "video.mp4");
          const safe = raw.replace(/[^\w.\- ()[\]]+/g, "_") || `upload_${Date.now()}.mp4`;
          cb(null, `${Date.now()}_${safe}`);
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
          const code = (err as { code?: string }).code;
          if (code === "LIMIT_FILE_SIZE") {
            subscriber.error(new PayloadTooLargeException("video file too large"));
            return;
          }
          subscriber.error(err);
          return;
        }
        next.handle().subscribe(subscriber);
      });
    });
  }
}
