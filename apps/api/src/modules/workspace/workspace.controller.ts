import { BadRequestException, Body, Controller, Delete, Get, Post, Put, Query, UploadedFile, UseInterceptors } from "@nestjs/common";
import {
  CreateDirectoryDto,
  DeleteDbRowDto,
  DeletePathDto,
  InsertDbRowDto,
  MovePathDto,
  UpdateDbRowDto,
  WriteFileDto,
} from "./workspace.dto";
import { VideoSegmentUploadInterceptor } from "./video-segment-upload.interceptor";
import { WorkspaceService } from "./workspace.service";

@Controller("workspace")
export class WorkspaceController {
  constructor(private readonly workspaceService: WorkspaceService) {}

  @Get("databases")
  getDatabases() {
    return this.workspaceService.listDatabases();
  }

  @Get("databases/tables")
  getTables(@Query("dbPath") dbPath: string) {
    return this.workspaceService.listTables(dbPath);
  }

  @Get("databases/rows")
  getRows(
    @Query("dbPath") dbPath: string,
    @Query("table") table: string,
    @Query("limit") limit?: string,
    @Query("offset") offset?: string
  ) {
    const parsedLimit = Number.isFinite(Number(limit)) ? Number(limit) : 100;
    const parsedOffset = Number.isFinite(Number(offset)) ? Number(offset) : 0;
    return this.workspaceService.getTableRows(dbPath, table, parsedLimit, parsedOffset);
  }

  @Put("databases/rows")
  updateRow(@Body() dto: UpdateDbRowDto) {
    return this.workspaceService.updateRow(dto.dbPath, dto.table, dto.rowId, dto.values);
  }

  @Post("databases/rows")
  insertRow(@Body() dto: InsertDbRowDto) {
    return this.workspaceService.insertRow(dto.dbPath, dto.table, dto.values);
  }

  @Delete("databases/rows")
  deleteRow(@Body() dto: DeleteDbRowDto) {
    return this.workspaceService.deleteRow(dto.dbPath, dto.table, dto.rowId);
  }

  @Get("files")
  listFiles(@Query("path") relPath?: string) {
    return this.workspaceService.listDirectory(relPath);
  }

  @Get("files/read")
  readFile(@Query("path") relPath: string) {
    return this.workspaceService.readTextFile(relPath);
  }

  @Put("files/write")
  writeFile(@Body() dto: WriteFileDto) {
    return this.workspaceService.writeTextFile(dto.path, dto.content);
  }

  @Post("files/mkdir")
  createDirectory(@Body() dto: CreateDirectoryDto) {
    return this.workspaceService.createDirectory(dto.path);
  }

  @Post("files/move")
  movePath(@Body() dto: MovePathDto) {
    return this.workspaceService.movePath(dto.from, dto.to);
  }

  @Delete("files")
  deletePath(@Body() dto: DeletePathDto) {
    return this.workspaceService.deletePath(dto.path, dto.recursive ?? false);
  }

  @Get("management/segment-manifests")
  listSegmentManifests() {
    return this.workspaceService.listSegmentManifestSummaries();
  }

  @Post("management/segment-manifest")
  createSegmentManifest(
    @Body()
    body: {
      tournamentId: string;
      videoRelativePath: string;
      startSec: number;
      endSec: number;
      segmentDurationSec: number;
    }
  ) {
    return this.workspaceService.createSegmentManifest(body);
  }

  @Post("files/upload-video-segment")
  @UseInterceptors(VideoSegmentUploadInterceptor)
  uploadVideoSegment(
    @UploadedFile() file: { path: string } | undefined,
    @Body("tournamentId") tournamentId: string,
    @Body("startSec") startSec: string,
    @Body("endSec") endSec: string,
    @Body("segmentDurationSec") segmentDurationSec: string
  ) {
    if (!file?.path) {
      throw new BadRequestException("Не загружен файл видео (поле file).");
    }
    const videoRelativePath = this.workspaceService.fileAbsoluteToProjectRelative(file.path);
    return this.workspaceService.createSegmentManifest({
      tournamentId,
      videoRelativePath,
      startSec: Number(startSec),
      endSec: Number(endSec),
      segmentDurationSec: Number(segmentDurationSec),
    });
  }
}

