import { Module } from "@nestjs/common";
import { ConfigModule } from "@nestjs/config";
import { HealthController } from "./health.controller";
import { CatalogModule } from "./catalog/catalog.module";
import { JobsModule } from "./jobs/jobs.module";

@Module({
  imports: [ConfigModule.forRoot({ isGlobal: true }), CatalogModule, JobsModule],
  controllers: [HealthController]
})
export class AppModule {}
