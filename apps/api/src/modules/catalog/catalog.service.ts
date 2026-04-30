import { Injectable } from "@nestjs/common";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { MapAdminConfig, MapEntry, Match, RingPoint, Team, TeamTrack, TextRectZone, TextZonesPayload, Tournament, ZonesPayload } from "./catalog.types";
import { TEAM_DISPLAY_COLORS_BGR } from "./team-colors.constants";
import { loadRuntimePaths } from "../runtime-paths";

interface AnalysisRecord {
  filePath: string;
  fileName: string;
  mapId: string;
  mapName: string;
  matchId: string;
  tournamentId: string;
  mapNumber: number;
  video: string;
  teams: Array<{
    team_id: string;
    team_name?: string;
    color_bgr?: [number, number, number];
    points: Array<{ timestampSec?: number; timestamp?: number; x: number; y: number; confidence?: number }>;
    eliminated?: boolean;
    eliminationTimestampSec?: number;
    eliminationFrame?: number;
    eliminationConfidence?: number;
    eliminationMethod?: string;
  }>;
  rings: Array<{
    mapId?: string;
    timestampSec?: number;
    x?: number;
    y?: number;
    radius?: number;
    segment?: number;
    confidence?: number;
  }>;
}

interface TournamentDbMapRow {
  id: string;
  match_id: string;
  mp_id: string | null;
  round_number: number;
}

interface TournamentDbMatchRow {
  id: string;
  tournament_id: string | null;
  video_url: string | null;
  year_number: number | null;
  split_number: number | null;
  region: string | null;
  day: number | null;
}

interface TournamentDbGameRow {
  youtube_video_id: string;
  game_number: number;
  output_filename: string | null;
}

type SqlitePrimitive = string | number | null;

class PythonSqliteDb {
  constructor(private readonly dbPath: string) {}

  prepare(sql: string) {
    return {
      get: (...params: SqlitePrimitive[]) => this.run(sql, params, "get") as Record<string, unknown> | undefined,
      all: (...params: SqlitePrimitive[]) => this.run(sql, params, "all") as Array<Record<string, unknown>>,
    };
  }

  close(): void {
    // no persistent handle
  }

  private run(sql: string, params: SqlitePrimitive[], mode: "get" | "all") {
    const payload = JSON.stringify({ sql, params, mode });
    const pyCode = [
      "import json, sqlite3, sys",
      "db_path = sys.argv[1]",
      "payload = json.loads(sys.argv[2])",
      "conn = sqlite3.connect(db_path)",
      "conn.row_factory = sqlite3.Row",
      "cur = conn.execute(payload['sql'], payload.get('params', []))",
      "if payload.get('mode') == 'get':",
      "    row = cur.fetchone()",
      "    out = dict(row) if row is not None else None",
      "else:",
      "    out = [dict(r) for r in cur.fetchall()]",
      "conn.close()",
      "print(json.dumps(out, ensure_ascii=False))",
    ].join("\n");

    const result = spawnSync("python", ["-c", pyCode, this.dbPath, payload], {
      encoding: "utf-8",
    });
    if (result.status !== 0) {
      throw new Error(result.stderr || "python sqlite query failed");
    }
    const stdout = String(result.stdout ?? "").trim();
    if (!stdout) return mode === "get" ? undefined : [];
    return JSON.parse(stdout);
  }
}

@Injectable()
export class CatalogService {
  private readonly projectRoot = path.resolve(__dirname, "../../../../..");
  private readonly runtimePaths = loadRuntimePaths(this.projectRoot);
  private readonly tracksFilePath = this.runtimePaths.artifacts.tracksFile;
  private readonly tracksDirPath = this.runtimePaths.artifacts.tracksDir;
  private readonly mapSettingsPath = this.runtimePaths.artifacts.mapAdminSettings;
  private readonly mapStartDbPath = this.runtimePaths.databases.mapStartDetection;
  private readonly tournamentsDbPath = this.resolvePreferredExistingPath(this.runtimePaths.databases.tournaments);
  private readonly trackingSettingsPyPath = path.join(this.projectRoot, "team_tracking", "tracking_settings.py");

  private readonly tournaments: Tournament[] = [
    { id: "t1", name: "ALGS Pro League EMEA", season: "2026" }
  ];

  private readonly matches: Match[] = [
    {
      id: "m1",
      tournamentId: "t1",
      faceitMatchId: "faceit-demo-001",
      title: "Day 4 Match 6",
      playedAt: "2026-04-25T18:00:00.000Z"
    }
  ];

  private readonly maps: MapEntry[] = [
    {
      id: "map1",
      matchId: "m1",
      mapName: "mp_storm_point",
      videoUrl: "/media/m1-map1-work-fragment.mp4",
      backgroundUrl: "/catalog/maps/map1/background",
      workFragmentStartSec: 0,
      workFragmentEndSec: 1200,
      ring1StartSec: 180,
      ring2StartSec: 420
    }
  ];

  private readonly teams: Team[] = [];

  private readonly tracks: TeamTrack[] = [];
  private readonly rings: RingPoint[] = [];
  private readonly stormPointTeamHsvDefaults = this.loadStormPointTeamHsvDefaults();

  private resolvePreferredExistingPath(paths: string[]): string {
    if (!paths.length) return "";
    const existing = paths.find((candidate) => fs.existsSync(candidate));
    return existing ?? paths[0];
  }

  private normalizeTournamentId(raw: string | null | undefined): string {
    const id = String(raw ?? "").trim();
    if (!id) return "";
    return id.replace(/_\d+$/, "");
  }

  private loadStormPointTeamHsvDefaults(): Record<string, { lower: [number, number, number]; upper: [number, number, number] }> {
    if (!fs.existsSync(this.trackingSettingsPyPath)) {
      return {};
    }
    const raw = fs.readFileSync(this.trackingSettingsPyPath, "utf-8");
    const defaults: Record<string, { lower: [number, number, number]; upper: [number, number, number] }> = {};
    const teamBlocks = raw.match(/"TEAM_\d+"\s*:\s*\{[\s\S]*?\n\s*\},/g) ?? [];
    for (const block of teamBlocks) {
      const teamIdMatch = block.match(/"TEAM_\d+"/);
      const hsvMatch = block.match(/"hsv_range"\s*:\s*\(\((\d+),\s*(\d+),\s*(\d+)\),\s*\((\d+),\s*(\d+),\s*(\d+)\)\)/);
      if (!teamIdMatch || !hsvMatch) continue;
      const teamId = teamIdMatch[0].replaceAll('"', "");
      defaults[teamId] = {
        lower: [Number(hsvMatch[1]), Number(hsvMatch[2]), Number(hsvMatch[3])] as [number, number, number],
        upper: [Number(hsvMatch[4]), Number(hsvMatch[5]), Number(hsvMatch[6])] as [number, number, number],
      };
    }
    return defaults;
  }

  private loadMapSettingsStore(): { maps: Record<string, MapAdminConfig> } {
    if (!fs.existsSync(this.mapSettingsPath)) {
      return { maps: {} };
    }
    try {
      const raw = fs.readFileSync(this.mapSettingsPath, "utf-8");
      const payload = JSON.parse(raw) as { maps?: Record<string, MapAdminConfig> };
      return { maps: payload.maps ?? {} };
    } catch {
      return { maps: {} };
    }
  }

  private saveMapSettingsStore(store: { maps: Record<string, MapAdminConfig> }): void {
    fs.mkdirSync(path.dirname(this.mapSettingsPath), { recursive: true });
    const tmp = `${this.mapSettingsPath}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(store, null, 2), "utf-8");
    fs.renameSync(tmp, this.mapSettingsPath);
  }

  private withSqlite<T>(dbPath: string, handler: (db: PythonSqliteDb) => T, fallback: T): T {
    if (!fs.existsSync(dbPath)) return fallback;
    let db: PythonSqliteDb | null = null;
    try {
      db = new PythonSqliteDb(dbPath);
      return handler(db);
    } catch {
      return fallback;
    } finally {
      try {
        db?.close();
      } catch {
        // ignore close errors
      }
    }
  }

  private mapIdToVideoName(mapId: string): string | null {
    return this.withSqlite(
      this.tournamentsDbPath,
      (db) => {
        const mapRow = db
          .prepare("SELECT id, match_id, round_number FROM maps WHERE id = ?")
          .get(mapId) as TournamentDbMapRow | undefined;
        if (!mapRow) return null;
        const matchRow = db
          .prepare("SELECT id, youtube_video_id FROM matches WHERE id = ?")
          .get(mapRow.match_id) as { id: string; youtube_video_id: string } | undefined;
        if (!matchRow?.youtube_video_id) return null;
        const gameRow = db
          .prepare("SELECT output_filename FROM games WHERE youtube_video_id = ? AND game_number = ? LIMIT 1")
          .get(matchRow.youtube_video_id, mapRow.round_number) as { output_filename: string | null } | undefined;
        if (!gameRow?.output_filename) return null;
        return path.basename(String(gameRow.output_filename));
      },
      null
    );
  }

  private mapIdToGameId(mapId: string): number | null {
    return this.withSqlite(
      this.mapStartDbPath,
      (db) => {
        const videoName = this.mapIdToVideoName(mapId);
        if (!videoName) return null;
        const row = db
          .prepare("SELECT rowid FROM map_start_detection WHERE video_name = ?")
          .get(videoName) as { rowid: number } | undefined;
        return row?.rowid ?? null;
      },
      null
    );
  }

  private mapIdToDetectedMapName(mapId: string): string | null {
    return this.withSqlite(
      this.mapStartDbPath,
      (db) => {
        const videoName = this.mapIdToVideoName(mapId);
        if (!videoName) return null;
        const row = db
          .prepare("SELECT map_mp_id, map_name FROM map_start_detection WHERE video_name = ?")
          .get(videoName) as { map_mp_id: string | null; map_name: string | null } | undefined;
        const mpId = String(row?.map_mp_id ?? "").trim();
        if (mpId) return mpId;
        const rawName = String(row?.map_name ?? "").trim().toLowerCase();
        if (!rawName) return null;
        return `mp_${rawName.replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "")}`;
      },
      null
    );
  }

  private defaultMapConfig(mapId: string, mapName: string): MapAdminConfig {
    const normalizedMapName = mapName.startsWith("mp_") ? mapName : `mp_${mapName}`;
    return {
      mapId,
      mapName: normalizedMapName,
      basePresetFrom: "mp_storm_point",
      runtime: {
        frameSkip: 8,
        roundWindows: {
          round1: { startSec: 0, endSec: 375 },
          round2: { startSec: 375, endSec: 600 },
        },
      },
      teamHsv: Object.fromEntries(
        Object.entries(this.stormPointTeamHsvDefaults).map(([teamId, cfg]) => [teamId, { lower: [...cfg.lower], upper: [...cfg.upper] }])
      ),
      polygons: {
        zonesFile: path.relative(this.projectRoot, path.join(this.runtimePaths.artifacts.zonesDir, `${normalizedMapName}.zones.json`)).replaceAll("\\", "/"),
        enabled: true,
      },
      ring: {
        hsvLower: [0, 0, 67],
        hsvUpper: [180, 68, 89],
        grayMin: 68,
        grayMax: 88,
        morphK: 1,
        blurK: 13,
        houghP2: 100,
        minRPct: 4,
        maxRPct: 52,
        sampleStepFrames: 1000,
      },
    };
  }

  private listTrackPayloadFiles(): string[] {
    const files: string[] = [];
    if (fs.existsSync(this.tracksDirPath)) {
      for (const name of fs.readdirSync(this.tracksDirPath)) {
        if (name.toLowerCase().endsWith(".json")) {
          files.push(path.join(this.tracksDirPath, name));
        }
      }
    }
    // backward compatibility with legacy single-file mode
    if (fs.existsSync(this.tracksFilePath)) {
      files.push(this.tracksFilePath);
    }
    return files;
  }

  private loadAnalysisRecords(): AnalysisRecord[] {
    const records: AnalysisRecord[] = [];
    for (const filePath of this.listTrackPayloadFiles()) {
      try {
        const raw = fs.readFileSync(filePath, "utf-8");
        const payload = JSON.parse(raw) as Record<string, unknown>;
        const fileName = path.basename(filePath);
        const stem = fileName.replace(/\.json$/i, "");
        const parts = stem.split("_");
        const fallbackMapName = typeof payload.map === "string" ? payload.map : (parts.length >= 3 ? parts.slice(2).join("_") : "mp_storm_point");
        const fallbackMatchId = typeof payload.matchId === "string" ? payload.matchId : (parts[0] || "test");
        const fallbackMapNumberRaw = parts.length >= 2 ? Number(parts[1]) : Number.NaN;
        const fallbackMapNumber = Number.isFinite(fallbackMapNumberRaw) && fallbackMapNumberRaw > 0 ? fallbackMapNumberRaw : 1;
        const mapNumberRaw = Number(payload.mapNumber ?? fallbackMapNumber);
        const mapNumber = Number.isFinite(mapNumberRaw) && mapNumberRaw > 0 ? mapNumberRaw : 1;
        const matchId = String(payload.matchId ?? fallbackMatchId ?? "test");
        const tournamentId = String(payload.tournamentId ?? "test");
        const mapName = String(payload.map ?? fallbackMapName ?? "mp_storm_point");
        const mapId = String(payload.mapId ?? `${matchId}_map${mapNumber}`);
        const teams = Array.isArray(payload.teams) ? payload.teams as AnalysisRecord["teams"] : [];
        const rings = Array.isArray(payload.rings) ? payload.rings as AnalysisRecord["rings"] : [];
        records.push({
          filePath,
          fileName,
          mapId,
          mapName,
          matchId,
          tournamentId,
          mapNumber,
          video: String(payload.video ?? ""),
          teams,
          rings,
        });
      } catch {
        // Ignore malformed payloads to avoid breaking the catalog.
      }
    }
    records.sort((a, b) => b.fileName.localeCompare(a.fileName));
    return records;
  }

  private findAnalysisRecordByMapId(mapId: string): AnalysisRecord | undefined {
    const records = this.loadAnalysisRecords();
    const direct = records.find((item) => item.mapId === mapId);
    if (direct) return direct;
    const videoName = this.mapIdToVideoName(mapId);
    if (!videoName) return undefined;
    return records.find((item) => {
      const base = path.basename(String(item.video || ""));
      return base === videoName || item.fileName.includes(videoName.replace(/\.mp4$/i, ""));
    });
  }

  private colorByTeamId(teamId: string): [number, number, number] {
    const fromTrackingSettings = TEAM_DISPLAY_COLORS_BGR[teamId];
    if (fromTrackingSettings) return fromTrackingSettings;

    const teamNumber = Number(teamId.replace("TEAM_", ""));
    if (!Number.isFinite(teamNumber)) return [180, 180, 180];
    const b = (teamNumber * 73) % 255;
    const g = (teamNumber * 131) % 255;
    const r = (teamNumber * 197) % 255;
    return [b, g, r];
  }

  private buildTwentySlotTeamList(bySlot: Map<number, string>): Team[] {
    const result: Team[] = [];
    for (let i = 1; i <= 20; i++) {
      const teamId = `TEAM_${i}`;
      result.push({
        id: teamId,
        name: bySlot.get(i) ?? `Team ${i}`,
        colorBgr: this.colorByTeamId(teamId),
      });
    }
    return result;
  }

  /**
   * One row from map_start_detection.teams: JSON [{ team_slot, team_name }, ...]
   */
  private parseMapStartTeamsColumn(teamsColumn: string | null | undefined): Team[] | null {
    const raw = String(teamsColumn ?? "").trim();
    if (!raw) return null;
    let parsed: Array<{ team_slot?: number; team_name?: string }> = [];
    try {
      parsed = JSON.parse(raw) as Array<{ team_slot?: number; team_name?: string }>;
    } catch {
      return null;
    }
    if (!Array.isArray(parsed)) return null;
    const bySlot = new Map<number, string>();
    for (const entry of parsed) {
      const slot = Number(entry.team_slot ?? 0);
      if (!Number.isFinite(slot) || slot <= 0 || slot > 20) continue;
      const teamName = String(entry.team_name ?? "").trim();
      if (!teamName) continue;
      if (!bySlot.has(slot)) bySlot.set(slot, teamName);
    }
    if (bySlot.size === 0) return null;
    return this.buildTwentySlotTeamList(bySlot);
  }

  private teamsFromAnalysisRecordSlots(record: AnalysisRecord): Team[] {
    const bySlot = new Map<number, string>();
    for (const team of record.teams) {
      const m = /^TEAM_(\d+)$/i.exec(String(team.team_id ?? "").trim());
      if (!m) continue;
      const slot = Number(m[1]);
      if (!Number.isFinite(slot) || slot <= 0 || slot > 20) continue;
      const name = String(team.team_name ?? "").trim();
      if (name) bySlot.set(slot, name);
    }
    return this.buildTwentySlotTeamList(bySlot);
  }

  getTournaments() {
    const fromDb = this.withSqlite(
      this.tournamentsDbPath,
      (db) => {
        const rows = db
          .prepare(
            `
            SELECT tournament_id, tournament_name, year_number
            FROM tournaments
            ORDER BY year_number DESC, tournament_name ASC
            `
          )
          .all() as Array<{ tournament_id: string | null; tournament_name: string | null; year_number: number | null }>;
        const byCanonical = new Map<string, Tournament>();
        for (const row of rows) {
          const sourceId = String(row.tournament_id ?? "").trim();
          const id = this.normalizeTournamentId(sourceId);
          if (!id || byCanonical.has(id)) continue;
          byCanonical.set(id, {
            id,
            name: String(row.tournament_name ?? id),
            season: row.year_number !== null ? `Y${Number(row.year_number)}` : "unknown",
          });
        }
        return Array.from(byCanonical.values());
      },
      []
    );
    if (fromDb.length) return fromDb;
    const records = this.loadAnalysisRecords();
    if (!records.length) return this.tournaments;
    const ids = Array.from(new Set(records.map((item) => item.tournamentId || "test")));
    return ids.map((id) => ({ id, name: id, season: "test" }));
  }

  getMatches(tournamentId: string) {
    const fromDb = this.withSqlite(
      this.tournamentsDbPath,
      (db) => {
        const requestedCanonicalId = this.normalizeTournamentId(tournamentId);
        const candidateRows = db
          .prepare("SELECT tournament_id FROM tournaments")
          .all() as Array<{ tournament_id: string | null }>;
        const groupedIds = Array.from(
          new Set(
            candidateRows
              .map((row) => String(row.tournament_id ?? "").trim())
              .filter((id) => id && this.normalizeTournamentId(id) === requestedCanonicalId)
          )
        );
        const idsToQuery = groupedIds.length ? groupedIds : [tournamentId];
        const placeholders = idsToQuery.map(() => "?").join(", ");
        const rows = db
          .prepare(
            `
            SELECT id, tournament_id, year_number, split_number, region, day, video_url
            FROM matches
            WHERE tournament_id IN (${placeholders})
            ORDER BY day ASC, id ASC
            `
          )
          .all(...idsToQuery) as unknown as TournamentDbMatchRow[];
        return rows.map((row) => ({
          id: row.id,
          tournamentId: requestedCanonicalId || String(row.tournament_id ?? tournamentId),
          faceitMatchId: row.id,
          title: `Y${Number(row.year_number ?? 0)} S${Number(row.split_number ?? 0)} ${String(row.region ?? "UNKNOWN")} Day ${Number(row.day ?? 0)}`,
          playedAt: new Date().toISOString(),
        }));
      },
      []
    );
    if (fromDb.length) return fromDb;
    const records = this.loadAnalysisRecords().filter((item) => (item.tournamentId || "test") === tournamentId);
    if (!records.length) return this.matches.filter((m) => m.tournamentId === tournamentId);
    const grouped = new Map<string, AnalysisRecord>();
    for (const item of records) {
      if (!grouped.has(item.matchId)) grouped.set(item.matchId, item);
    }
    return Array.from(grouped.values()).map((item) => ({
      id: item.matchId,
      tournamentId: item.tournamentId || "test",
      faceitMatchId: item.matchId,
      title: item.matchId,
      playedAt: new Date().toISOString(),
    }));
  }

  getMaps(matchId: string) {
    const fromDb = this.withSqlite(
      this.tournamentsDbPath,
      (db) => {
        const mapRows = db
          .prepare(
            `
            SELECT id, match_id, mp_id, round_number
            FROM maps
            WHERE match_id = ?
            ORDER BY round_number ASC
            `
          )
          .all(matchId) as unknown as TournamentDbMapRow[];
        const matchRow = db
          .prepare("SELECT video_url, youtube_video_id FROM matches WHERE id = ?")
          .get(matchId) as { video_url: string | null; youtube_video_id: string | null } | undefined;
        const ringsByRound = this.withSqlite(
          this.mapStartDbPath,
          (ringDb) => {
            const out = new Map<number, { start: number; end: number }>();
            for (const row of mapRows) {
              const gameRow = matchRow?.youtube_video_id
                ? db
                    .prepare("SELECT output_filename FROM games WHERE youtube_video_id = ? AND game_number = ? LIMIT 1")
                    .get(matchRow.youtube_video_id, row.round_number) as { output_filename: string | null } | undefined
                : undefined;
              const videoName = gameRow?.output_filename ? path.basename(String(gameRow.output_filename)) : null;
              if (!videoName) continue;
              const rowId = ringDb
                .prepare("SELECT rowid FROM map_start_detection WHERE video_name = ?")
                .get(videoName) as { rowid: number } | undefined;
              if (!rowId?.rowid) continue;
              const ringRows = ringDb
                .prepare("SELECT time_start, time_end FROM Rings WHERE game_id = ? ORDER BY ring_number")
                .all(rowId.rowid) as Array<{ time_start: number | null; time_end: number | null }>;
              if (!ringRows.length) continue;
              const start = Number(ringRows[0].time_start ?? 0);
              const endCandidate = Number(ringRows[ringRows.length - 1].time_end ?? NaN);
              const end = Number.isFinite(endCandidate) ? endCandidate : Math.max(start + 1200, 1200);
              out.set(Number(row.round_number), { start, end });
            }
            return out;
          },
          new Map<number, { start: number; end: number }>()
        );

        return mapRows.map((row) => {
          const detectedMapName = this.mapIdToDetectedMapName(row.id);
          const ringWindow = ringsByRound.get(Number(row.round_number));
          const start = ringWindow?.start ?? 0;
          const end = ringWindow?.end ?? 1200;
          return {
            id: row.id,
            matchId: row.match_id,
            mapName: row.mp_id ?? detectedMapName ?? `round_${Number(row.round_number)}`,
            videoUrl: String(matchRow?.video_url ?? "/media/unknown.mp4"),
            backgroundUrl: `/catalog/maps/${encodeURIComponent(row.id)}/background`,
            workFragmentStartSec: start,
            workFragmentEndSec: end,
            ring1StartSec: start,
            ring2StartSec: Math.min(end, start + 375),
          };
        });
      },
      []
    );
    if (fromDb.length) return fromDb;
    const records = this.loadAnalysisRecords().filter((item) => item.matchId === matchId);
    if (!records.length) return this.maps.filter((m) => m.matchId === matchId);
    return records
      .sort((a, b) => a.mapNumber - b.mapNumber)
      .map((item) => ({
        id: item.mapId,
        matchId: item.matchId,
        mapName: item.mapName,
        videoUrl: item.video || "/media/unknown.mp4",
        backgroundUrl: `/catalog/maps/${encodeURIComponent(item.mapId)}/background`,
        workFragmentStartSec: 0,
        workFragmentEndSec: 1200,
        ring1StartSec: 0,
        ring2StartSec: 375,
      }));
  }

  /**
   * Prefer map-specific roster (teams for that VOD/game) via GET /catalog/maps/:mapId/teams.
   * This legacy endpoint returns placeholders so callers are not fooled by a merged roster.
   */
  getTeams() {
    return this.buildTwentySlotTeamList(new Map());
  }

  /** Display names TEAM_1..TEAM_20 for this map's game row in map_start_detection (teams JSON). */
  getTeamsForMap(mapId: string): Team[] {
    const videoName = this.mapIdToVideoName(mapId);
    if (videoName) {
      const teamsColumn = this.withSqlite(
        this.mapStartDbPath,
        (db) => {
          const row = db
            .prepare("SELECT teams FROM map_start_detection WHERE video_name = ?")
            .get(videoName) as { teams: string | null } | undefined;
          return row?.teams ?? null;
        },
        null as string | null
      );
      const fromDetection = typeof teamsColumn === "string" ? this.parseMapStartTeamsColumn(teamsColumn) : null;
      if (fromDetection?.length) {
        return fromDetection;
      }
    }

    const record = this.findAnalysisRecordByMapId(mapId);
    if (record?.teams?.length) {
      return this.teamsFromAnalysisRecordSlots(record);
    }

    return this.buildTwentySlotTeamList(new Map());
  }

  getTracks(mapId: string, teamIds?: string[], fromSec?: number, toSec?: number) {
    const record = this.findAnalysisRecordByMapId(mapId);
    if (!record) return [];
    const picked = record.teams
      .map((team) => ({
        mapId: record.mapId,
        teamId: String(team.team_id),
        points: (Array.isArray(team.points) ? team.points : []).map((point) => ({
          timestampSec: Number(point.timestampSec ?? point.timestamp ?? 0),
          x: Number(point.x),
          y: Number(point.y),
          confidence: Number(point.confidence ?? 1),
        })),
        eliminated: Boolean(team.eliminated),
        eliminationTimestampSec:
          team.eliminationTimestampSec !== undefined ? Number(team.eliminationTimestampSec) : undefined,
        eliminationFrame:
          team.eliminationFrame !== undefined ? Number(team.eliminationFrame) : undefined,
        eliminationConfidence:
          team.eliminationConfidence !== undefined ? Number(team.eliminationConfidence) : undefined,
        eliminationMethod:
          team.eliminationMethod !== undefined ? String(team.eliminationMethod) : undefined,
      }))
      .filter((track) => !teamIds?.length || teamIds.includes(track.teamId));

    if (fromSec === undefined && toSec === undefined) {
      return picked;
    }

    return picked.map((track) => ({
      ...track,
      points: track.points.filter((point) => {
        if (fromSec !== undefined && point.timestampSec < fromSec) return false;
        if (toSec !== undefined && point.timestampSec > toSec) return false;
        return true;
      })
    }));
  }

  getRings(mapId: string, fromSec?: number, toSec?: number) {
    const fromDb = this.withSqlite(
      this.mapStartDbPath,
      (db) => {
        const gameId = this.mapIdToGameId(mapId);
        if (!gameId) return [] as RingPoint[];
        const rows = db
          .prepare("SELECT ring_number, center, radius, time_start, time_end FROM Rings WHERE game_id = ? ORDER BY ring_number ASC")
          .all(gameId) as Array<{
            ring_number: number;
            center: string | null;
            radius: number | null;
            time_start: number | null;
            time_end: number | null;
          }>;
        const points: RingPoint[] = [];
        for (const row of rows) {
          if (row.time_start === null || row.center === null || row.radius === null) continue;
          let center: { x?: number; y?: number } = {};
          try {
            center = JSON.parse(row.center) as { x?: number; y?: number };
          } catch {
            center = {};
          }
          const x = Number(center.x ?? NaN);
          const y = Number(center.y ?? NaN);
          if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
          points.push({
            mapId,
            timestampSec: Number(row.time_start ?? 0),
            x,
            y,
            radius: Number(row.radius ?? 0),
            segment: Number(row.ring_number ?? 1),
            confidence: 1,
          });
        }
        return points;
      },
      []
    );
    return fromDb.filter((ring) => {
      if (fromSec !== undefined && ring.timestampSec < fromSec) return false;
      if (toSec !== undefined && ring.timestampSec > toSec) return false;
      return true;
    });
  }

  getMapBackgroundPath(mapId: string): string | null {
    const record = this.findAnalysisRecordByMapId(mapId);
    const mapName = record?.mapName ?? this.mapIdToDetectedMapName(mapId) ?? this.maps.find((map) => map.id === mapId)?.mapName;
    if (!mapName) {
      return null;
    }

    // 1) Prefer static map image from maps/ by map name.
    const mapsDir = this.runtimePaths.media.mapsDir;
    const candidates = this.buildMapFileCandidates(mapName);
    for (const candidate of candidates) {
      const candidatePath = path.join(mapsDir, candidate);
      if (fs.existsSync(candidatePath)) {
        return candidatePath;
      }
    }
    // 1.5) Flexible fallback: match assets by normalized key (ignoring case and separators).
    if (fs.existsSync(mapsDir)) {
      const wantedKey = this.normalizeMapAssetKey(mapName);
      for (const fileName of fs.readdirSync(mapsDir)) {
        if (!/\.(png|jpg|jpeg|webp)$/i.test(fileName)) continue;
        if (this.normalizeMapAssetKey(fileName) === wantedKey) {
          return path.join(mapsDir, fileName);
        }
      }
    }

    // 2) Fallback to extracted map background from analysis output.
    const generatedFileName = `map_background_${mapName.replace("/", "_")}.png`;
    const generatedPath = path.join(path.dirname(this.runtimePaths.artifacts.tracksDir), generatedFileName);
    if (fs.existsSync(generatedPath)) {
      return generatedPath;
    }
    return null;
  }

  listMapAssets() {
    const mapsDir = this.runtimePaths.media.mapsDir;
    if (!fs.existsSync(mapsDir)) return [];
    const entries = fs.readdirSync(mapsDir, { withFileTypes: true });
    const all = entries
      .filter((item) => item.isFile())
      .map((item) => item.name)
      .filter((name) => /\.(png|jpg|jpeg|webp)$/i.test(name))
      .map((name) => ({
        mapName: name.replace(/\.(png|jpg|jpeg|webp)$/i, ""),
        fileName: name,
      }))
      .sort((a, b) => a.mapName.localeCompare(b.mapName));

    // Keep only one file per map name (e.g. avoid png+webp duplicates in admin select).
    const byMapName = new Map<string, { mapName: string; fileName: string }>();
    for (const item of all) {
      if (!byMapName.has(item.mapName)) {
        byMapName.set(item.mapName, item);
      }
    }
    return Array.from(byMapName.values());
  }

  getMapAdminConfig(mapId: string) {
    const record = this.findAnalysisRecordByMapId(mapId);
    const mapEntry = this.maps.find((map) => map.id === mapId);
    const mapName = record?.mapName ?? mapEntry?.mapName ?? mapId;
    const store = this.loadMapSettingsStore();
    const defaults = this.defaultMapConfig(mapId, mapName);
    const stored = store.maps[mapId];
    if (!stored) {
      return defaults;
    }
    return {
      ...defaults,
      ...stored,
      mapId,
      mapName: stored.mapName ?? defaults.mapName,
      runtime: {
        ...defaults.runtime,
        ...(stored.runtime ?? {}),
        roundWindows: {
          ...defaults.runtime.roundWindows,
          ...(stored.runtime?.roundWindows ?? {}),
          round1: {
            ...defaults.runtime.roundWindows.round1,
            ...(stored.runtime?.roundWindows?.round1 ?? {}),
          },
          round2: {
            ...defaults.runtime.roundWindows.round2,
            ...(stored.runtime?.roundWindows?.round2 ?? {}),
          },
        },
      },
      polygons: { ...defaults.polygons, ...(stored.polygons ?? {}) },
      ring: { ...defaults.ring, ...(stored.ring ?? {}) },
      teamHsv: { ...defaults.teamHsv, ...(stored.teamHsv ?? {}) },
    };
  }

  updateMapAdminConfig(mapId: string, incoming: Partial<MapAdminConfig>) {
    const current = this.getMapAdminConfig(mapId);
    const incomingRuntime: Partial<MapAdminConfig["runtime"]> = incoming.runtime ?? {};
    const incomingRoundWindows: Partial<MapAdminConfig["runtime"]["roundWindows"]> = incomingRuntime.roundWindows ?? {};
    const next: MapAdminConfig = {
      ...current,
      ...incoming,
      mapId,
      runtime: {
        ...current.runtime,
        ...incomingRuntime,
        roundWindows: {
          ...current.runtime.roundWindows,
          ...incomingRoundWindows,
          round1: {
            ...current.runtime.roundWindows.round1,
            ...(incomingRoundWindows.round1 ?? {}),
          },
          round2: {
            ...current.runtime.roundWindows.round2,
            ...(incomingRoundWindows.round2 ?? {}),
          },
        },
      },
      polygons: { ...current.polygons, ...(incoming.polygons ?? {}) },
      ring: { ...current.ring, ...(incoming.ring ?? {}) },
      teamHsv: { ...current.teamHsv, ...(incoming.teamHsv ?? {}) },
    };
    const store = this.loadMapSettingsStore();
    store.maps[mapId] = next;
    this.saveMapSettingsStore(store);
    return next;
  }

  private resolveZonesPath(mapId: string): string {
    const config = this.getMapAdminConfig(mapId);
    const fallback = path.relative(this.projectRoot, path.join(this.runtimePaths.artifacts.zonesDir, `${config.mapName}.zones.json`)).replaceAll("\\", "/");
    const candidate = String(config.polygons?.zonesFile ?? fallback).trim() || fallback;
    const resolved = path.isAbsolute(candidate)
      ? path.resolve(candidate)
      : path.resolve(this.projectRoot, candidate);
    const root = path.resolve(this.projectRoot);
    if (!resolved.toLowerCase().startsWith(root.toLowerCase())) {
      throw new Error("Zones path must stay inside project root.");
    }
    return resolved;
  }

  private resolveTextZonesPath(mapId: string): string {
    const config = this.getMapAdminConfig(mapId);
    const fallback = path.relative(this.projectRoot, path.join(this.runtimePaths.artifacts.textZonesDir, `${config.mapName}.text-zones.json`)).replaceAll("\\", "/");
    const candidate = fallback;
    const resolved = path.isAbsolute(candidate)
      ? path.resolve(candidate)
      : path.resolve(this.projectRoot, candidate);
    const root = path.resolve(this.projectRoot);
    if (!resolved.toLowerCase().startsWith(root.toLowerCase())) {
      throw new Error("Text zones path must stay inside project root.");
    }
    return resolved;
  }

  getMapZones(mapId: string): ZonesPayload {
    const config = this.getMapAdminConfig(mapId);
    const filePath = this.resolveZonesPath(mapId);
    if (!fs.existsSync(filePath)) {
      return {
        map: config.mapName,
        image_size: { width: 0, height: 0 },
        zones: [],
      };
    }
    try {
      const raw = fs.readFileSync(filePath, "utf-8");
      const payload = JSON.parse(raw) as ZonesPayload;
      return {
        map: String(payload.map ?? config.mapName),
        image_path: payload.image_path,
        image_size: {
          width: Number(payload.image_size?.width ?? 0),
          height: Number(payload.image_size?.height ?? 0),
        },
        zones: Array.isArray(payload.zones) ? payload.zones : [],
      };
    } catch {
      return {
        map: config.mapName,
        image_size: { width: 0, height: 0 },
        zones: [],
      };
    }
  }

  updateMapZones(mapId: string, payload: ZonesPayload): ZonesPayload {
    const config = this.getMapAdminConfig(mapId);
    const filePath = this.resolveZonesPath(mapId);
    const normalized: ZonesPayload = {
      map: String(payload.map || config.mapName),
      image_path: payload.image_path ? String(payload.image_path) : undefined,
      image_size: {
        width: Number(payload.image_size?.width ?? 0),
        height: Number(payload.image_size?.height ?? 0),
      },
      zones: Array.isArray(payload.zones) ? payload.zones : [],
    };
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    const tmp = `${filePath}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(normalized, null, 2), "utf-8");
    fs.renameSync(tmp, filePath);
    return normalized;
  }

  getMapTextZones(mapId: string): TextZonesPayload {
    const config = this.getMapAdminConfig(mapId);
    const filePath = this.resolveTextZonesPath(mapId);
    if (!fs.existsSync(filePath)) {
      return {
        map: config.mapName,
        image_size: { width: 0, height: 0 },
        zones: [],
      };
    }
    try {
      const raw = fs.readFileSync(filePath, "utf-8");
      const payload = JSON.parse(raw) as TextZonesPayload;
      return {
        map: String(payload.map ?? config.mapName),
        image_path: payload.image_path ? String(payload.image_path) : undefined,
        image_size: {
          width: Number(payload.image_size?.width ?? 0),
          height: Number(payload.image_size?.height ?? 0),
        },
        zones: Array.isArray(payload.zones) ? payload.zones.map((zone, idx) => this.normalizeTextZone(zone, idx)) : [],
      };
    } catch {
      return {
        map: config.mapName,
        image_size: { width: 0, height: 0 },
        zones: [],
      };
    }
  }

  updateMapTextZones(mapId: string, payload: TextZonesPayload): TextZonesPayload {
    const config = this.getMapAdminConfig(mapId);
    const filePath = this.resolveTextZonesPath(mapId);
    const imageWidth = Math.max(0, Number(payload.image_size?.width ?? 0));
    const imageHeight = Math.max(0, Number(payload.image_size?.height ?? 0));
    const zones = Array.isArray(payload.zones) ? payload.zones.map((zone, idx) => this.normalizeTextZone(zone, idx, imageWidth, imageHeight)) : [];
    const normalized: TextZonesPayload = {
      map: String(payload.map || config.mapName),
      image_path: payload.image_path ? String(payload.image_path) : undefined,
      image_size: {
        width: imageWidth,
        height: imageHeight,
      },
      zones,
    };
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    const tmp = `${filePath}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(normalized, null, 2), "utf-8");
    fs.renameSync(tmp, filePath);
    return normalized;
  }

  private normalizeTextZone(
    zone: TextRectZone,
    index: number,
    imageWidth?: number,
    imageHeight?: number
  ): TextRectZone {
    const safeWidth = imageWidth !== undefined ? Math.max(0, Number(imageWidth)) : undefined;
    const safeHeight = imageHeight !== undefined ? Math.max(0, Number(imageHeight)) : undefined;

    let x = Math.max(0, Number(zone?.x ?? 0));
    let y = Math.max(0, Number(zone?.y ?? 0));
    let width = Math.max(1, Number(zone?.width ?? 1));
    let height = Math.max(1, Number(zone?.height ?? 1));

    if (safeWidth !== undefined && safeWidth > 0) {
      x = Math.min(x, safeWidth - 1);
      width = Math.min(width, Math.max(1, safeWidth - x));
    }
    if (safeHeight !== undefined && safeHeight > 0) {
      y = Math.min(y, safeHeight - 1);
      height = Math.min(height, Math.max(1, safeHeight - y));
    }

    return {
      id: String(zone?.id ?? `text_zone_${index + 1}`),
      x: Math.round(x),
      y: Math.round(y),
      width: Math.round(width),
      height: Math.round(height),
      label: zone?.label ? String(zone.label) : undefined,
      enabled: zone?.enabled !== false,
    };
  }

  private getTeamColorFromTracks(teamId: string): [number, number, number] | null {
    if (!fs.existsSync(this.tracksFilePath)) {
      return null;
    }
    try {
      const raw = fs.readFileSync(this.tracksFilePath, "utf-8");
      const payload = JSON.parse(raw) as {
        teams?: Array<{ team_id: string; color_bgr?: [number, number, number] }>;
      };
      const team = payload.teams?.find((item) => item.team_id === teamId);
      if (team?.color_bgr && team.color_bgr.length === 3) {
        return [
          Number(team.color_bgr[0]),
          Number(team.color_bgr[1]),
          Number(team.color_bgr[2]),
        ];
      }
      return null;
    } catch {
      return null;
    }
  }

  private buildMapFileCandidates(mapName: string): string[] {
    const normalized = mapName.replaceAll("\\", "/");
    const base = normalized.replace(/^mp_/, "");
    const normalizedKebab = normalized.replaceAll("_", "-");
    const baseKebab = base.replaceAll("_", "-");
    const normalizedSnake = normalized.replaceAll("-", "_");
    const baseSnake = base.replaceAll("-", "_");
    const variants = Array.from(
      new Set([
        normalized,
        base,
        normalizedKebab,
        baseKebab,
        normalizedSnake,
        baseSnake,
        normalized.toLowerCase(),
        base.toLowerCase(),
        normalizedKebab.toLowerCase(),
        baseKebab.toLowerCase(),
        normalizedSnake.toLowerCase(),
        baseSnake.toLowerCase(),
      ])
    );

    const exts = ["png", "jpg", "jpeg", "webp"];
    const candidates: string[] = [];
    for (const variant of variants) {
      for (const ext of exts) {
        candidates.push(`${variant}.${ext}`);
      }
    }
    return candidates;
  }

  private normalizeMapAssetKey(input: string): string {
    const withoutExt = input.replace(/\.(png|jpg|jpeg|webp)$/i, "");
    const withoutPrefix = withoutExt.replace(/^mp_/i, "");
    return withoutPrefix.toLowerCase().replace(/[^a-z0-9]/g, "");
  }
}
