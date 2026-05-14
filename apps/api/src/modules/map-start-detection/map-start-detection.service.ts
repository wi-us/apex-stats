import { BadRequestException, Injectable, NotFoundException } from "@nestjs/common";
import Database = require("better-sqlite3");
import * as fs from "node:fs";
import * as path from "node:path";
import { loadRuntimePaths } from "../runtime-paths";

export interface MapStartVideoSummaryRow {
  videoName: string;
  videoPath: string;
  mapName: string | null;
  startTimestampSec: number | null;
  status: string;
  updatedAt: string;
  teamCount: number;
  ringCount: number;
}

export interface MapStartTeamRowOut {
  slot: number;
  teamName: string | null;
  isEliminated: boolean;
  timeEliminated: number | null;
}

export interface MapStartRingRowOut {
  ringNumber: number;
  center: string | null;
  radius: number | null;
  diameter: number | null;
  timeStart: number | null;
  timeEnd: number | null;
}

export interface MapStartVideoDetail extends MapStartVideoSummaryRow {
  notes: string | null;
  confidence: number | null;
  teams: MapStartTeamRowOut[];
  rings: MapStartRingRowOut[];
}

@Injectable()
export class MapStartDetectionService {
  private readonly projectRoot = path.resolve(__dirname, "../../../../..");
  private readonly dbPath: string;

  constructor() {
    this.dbPath = loadRuntimePaths(this.projectRoot).databases.mapStartDetection;
  }

  private openDbReadonly(): Database.Database | null {
    if (!fs.existsSync(this.dbPath)) {
      return null;
    }
    return new Database(this.dbPath, { readonly: true, fileMustExist: true });
  }

  static normalizeVideoKey(videoName: string): string {
    const trimmed = videoName.trim();
    if (!trimmed) throw new BadRequestException("videoName is required.");
    const norm = trimmed.replace(/\\/g, "/");
    if (norm.includes("..")) throw new BadRequestException("Invalid videoName.");
    return path.basename(norm);
  }

  listSummaries(): MapStartVideoSummaryRow[] {
    const db = this.openDbReadonly();
    if (!db) return [];
    try {
      const rows = db
        .prepare(
          `SELECT m.video_name AS videoName,
                  m.video_path AS videoPath,
                  m.map_name AS mapName,
                  m.start_timestamp_sec AS startTimestampSec,
                  m.status AS status,
                  m.updated_at AS updatedAt,
                  (SELECT COUNT(*) FROM Teams t WHERE t.game_id = m.rowid) AS teamCount,
                  (SELECT COUNT(*) FROM Rings r WHERE r.game_id = m.rowid) AS ringCount
           FROM map_start_detection m
           ORDER BY m.updated_at DESC`
        )
        .all() as MapStartVideoSummaryRow[];
      return rows.map((r) => ({
        ...r,
        teamCount: Number(r.teamCount ?? 0),
        ringCount: Number(r.ringCount ?? 0),
        startTimestampSec:
          r.startTimestampSec === null || r.startTimestampSec === undefined
            ? null
            : Number(r.startTimestampSec),
      }));
    } finally {
      db.close();
    }
  }

  getVideoDetail(videoNameRaw: string): MapStartVideoDetail {
    const videoName = MapStartDetectionService.normalizeVideoKey(videoNameRaw);
    const db = this.openDbReadonly();
    if (!db) {
      throw new NotFoundException("map_start_detection database not found.");
    }
    try {
      const head = db
        .prepare(
          `SELECT m.rowid AS gameId,
                  m.video_name AS videoName,
                  m.video_path AS videoPath,
                  m.map_name AS mapName,
                  m.start_timestamp_sec AS startTimestampSec,
                  m.status AS status,
                  m.notes AS notes,
                  m.confidence AS confidence,
                  m.updated_at AS updatedAt,
                  (SELECT COUNT(*) FROM Teams t WHERE t.game_id = m.rowid) AS teamCount,
                  (SELECT COUNT(*) FROM Rings r WHERE r.game_id = m.rowid) AS ringCount
           FROM map_start_detection m
           WHERE m.video_name = ?
           LIMIT 1`
        )
        .get(videoName) as
        | {
            gameId: number;
            videoName: string;
            videoPath: string;
            mapName: string | null;
            startTimestampSec: number | null;
            status: string;
            notes: string | null;
            confidence: number | null;
            updatedAt: string;
            teamCount: number;
            ringCount: number;
          }
        | undefined;

      if (!head) {
        throw new NotFoundException(`No detection row for video: ${videoName}`);
      }

      const gameId = Number(head.gameId);
      const teamRows = db
        .prepare(
          `SELECT team_name AS teamName, is_eliminated AS isEliminated, time_eliminated AS timeEliminated
           FROM Teams
           WHERE game_id = ?
           ORDER BY rowid ASC`
        )
        .all(gameId) as Array<{
          teamName: string | null;
          isEliminated: number;
          timeEliminated: number | null;
        }>;

      const teams: MapStartTeamRowOut[] = teamRows.map((row, idx) => ({
        slot: idx + 1,
        teamName: row.teamName,
        isEliminated: Boolean(row.isEliminated),
        timeEliminated: row.timeEliminated === null || row.timeEliminated === undefined ? null : Number(row.timeEliminated),
      }));

      const ringRows = db
        .prepare(
          `SELECT ring_number AS ringNumber, center AS center, radius AS radius,
                  time_start AS timeStart, time_end AS timeEnd
           FROM Rings
           WHERE game_id = ?
           ORDER BY ring_number ASC`
        )
        .all(gameId) as Array<{
          ringNumber: number;
          center: string | null;
          radius: number | null;
          timeStart: number | null;
          timeEnd: number | null;
        }>;

      const rings: MapStartRingRowOut[] = ringRows.map((row) => {
        const r = row.radius === null || row.radius === undefined ? null : Number(row.radius);
        return {
          ringNumber: Number(row.ringNumber),
          center: row.center,
          radius: r,
          diameter: r === null || Number.isNaN(r) ? null : 2 * r,
          timeStart: row.timeStart === null || row.timeStart === undefined ? null : Number(row.timeStart),
          timeEnd: row.timeEnd === null || row.timeEnd === undefined ? null : Number(row.timeEnd),
        };
      });

      return {
        videoName: head.videoName,
        videoPath: head.videoPath,
        mapName: head.mapName,
        startTimestampSec:
          head.startTimestampSec === null || head.startTimestampSec === undefined
            ? null
            : Number(head.startTimestampSec),
        status: head.status,
        updatedAt: head.updatedAt,
        teamCount: Number(head.teamCount ?? 0),
        ringCount: Number(head.ringCount ?? 0),
        notes: head.notes,
        confidence: head.confidence === null || head.confidence === undefined ? null : Number(head.confidence),
        teams,
        rings,
      };
    } finally {
      db.close();
    }
  }
}
