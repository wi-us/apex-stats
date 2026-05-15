import { Module } from "@nestjs/common";
import { ConfigModule } from "@nestjs/config";
import * as path from "node:path";
import { HealthController } from "./health.controller";
import { CatalogModule } from "./catalog/catalog.module";
import { JobsModule } from "./jobs/jobs.module";
import { WorkspaceModule } from "./workspace/workspace.module";
import { MapStartDetectionModule } from "./map-start-detection/map-start-detection.module";
import { MinimapLocatorModule } from "./minimap-locator/minimap-locator.module";

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      // Support running from both workspace root and apps/api directory.
      envFilePath: [path.resolve(process.cwd(), ".env"), path.resolve(process.cwd(), "../../.env")],
    }),
    CatalogModule,
    JobsModule,
    WorkspaceModule,
    MapStartDetectionModule,
    MinimapLocatorModule,
  ],
  controllers: [HealthController]
})
export class AppModule {}
