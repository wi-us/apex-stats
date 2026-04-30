import { Body, Controller, Get, Param, Post, Query } from "@nestjs/common";
import { JobsService } from "./jobs.service";

@Controller("jobs")
export class JobsController {
  constructor(private readonly jobsService: JobsService) {}

  @Post("ingest")
  enqueueIngest(@Body() body: { faceitMatchId: string }) {
    return this.jobsService.enqueueIngest(body.faceitMatchId);
  }

  @Post("analysis")
  enqueueAnalysis(@Body() body: { mapId: string }) {
    return this.jobsService.enqueueAnalysis(body.mapId);
  }

  @Get()
  listJobs(
    @Query("jobType") jobType?: "ingest" | "analysis",
    @Query("status") status?: "queued" | "running" | "completed" | "failed",
    @Query("page") page?: string,
    @Query("pageSize") pageSize?: string
  ) {
    return this.jobsService.listJobs({
      jobType,
      status,
      page: page !== undefined ? Number(page) : undefined,
      pageSize: pageSize !== undefined ? Number(pageSize) : undefined,
    });
  }

  @Get(":jobId")
  getJob(@Param("jobId") jobId: string) {
    return this.jobsService.getJob(jobId);
  }
}
