/* eslint-disable no-console */
const fs = require("fs");
const path = require("path");
const { DatabaseSync } = require("node:sqlite");
const { Pool } = require("pg");

try {
  // Load local .env when script runs from workspace root.
  // eslint-disable-next-line global-require
  require("dotenv").config({ path: path.resolve(__dirname, "..", "..", ".env") });
} catch {
  // no-op: fallback to process env / defaults
}

function loadRuntimePaths(projectRoot) {
  const cfgPath = path.join(projectRoot, "config", "runtime_paths.json");
  let cfg = {};
  if (fs.existsSync(cfgPath)) {
    try {
      cfg = JSON.parse(fs.readFileSync(cfgPath, "utf-8"));
    } catch {
      cfg = {};
    }
  }
  const resolve = (p) => (path.isAbsolute(p) ? p : path.join(projectRoot, p));
  const tdbs = Array.isArray(cfg?.databases?.tournaments) && cfg.databases.tournaments.length
    ? cfg.databases.tournaments
    : ["output/tournaments.sqlite", "output/youtube_ingest/tournaments.sqlite"];
  const tdbResolved = tdbs.map(resolve);
  // Prefer full ingest catalog DB (matches/maps/games), fallback to the first existing file.
  const preferredTdb = tdbResolved.find((p) => isCatalogDb(p)) || tdbResolved.find((p) => fs.existsSync(p)) || tdbResolved[0];
  return {
    tournamentsDb: preferredTdb,
    mapStartDb: resolve(cfg?.databases?.mapStartDetection || "output/map_start_detection.sqlite"),
    tracksDir: resolve(cfg?.artifacts?.tracksDir || "output/tracks"),
    jobsStore: resolve(cfg?.artifacts?.jobsStore || "output/jobs.json"),
  };
}

function canonicalTournamentId(rawId) {
  const value = String(rawId || "").trim();
  if (!value) return "";
  return value.replace(/_\d+$/g, "");
}

function isCatalogDb(dbPath) {
  if (!fs.existsSync(dbPath)) return false;
  try {
    const db = new DatabaseSync(dbPath, { readOnly: true });
    const hasTable = (name) => !!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1").get(name);
    const ok = hasTable("tournaments") && hasTable("matches") && hasTable("maps") && hasTable("games");
    db.close();
    return ok;
  } catch {
    return false;
  }
}

function getPool() {
  const databaseUrl = process.env.DATABASE_URL
    || `postgresql://${encodeURIComponent(process.env.POSTGRES_USER || "apex")}:${encodeURIComponent(process.env.POSTGRES_PASSWORD || "apex")}@${process.env.POSTGRES_HOST || "localhost"}:${process.env.POSTGRES_PORT || "5432"}/${process.env.POSTGRES_DB || "apex_stats"}`;
  return new Pool({ connectionString: databaseUrl });
}

async function upsertBaseCatalog(pool, tournamentsDbPath) {
  if (!fs.existsSync(tournamentsDbPath)) return;
  const db = new DatabaseSync(tournamentsDbPath, { readOnly: true });
  const hasTable = (name) => !!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1").get(name);
  if (!hasTable("tournaments") || !hasTable("matches") || !hasTable("maps")) {
    db.close();
    return;
  }
  const tournaments = db.prepare("SELECT tournament_id, tournament_name, year_number FROM tournaments").all();
  await pool.query(
    `INSERT INTO tournaments (id, name, season)
     VALUES ($1, $2, $3)
     ON CONFLICT (id) DO NOTHING`,
    ["unknown_tournament", "Unknown tournament", "unknown"]
  );
  for (const row of tournaments) {
    const id = canonicalTournamentId(row.tournament_id);
    if (!id) continue;
    await pool.query(
      `INSERT INTO tournaments (id, name, season)
       VALUES ($1, $2, $3)
       ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, season = EXCLUDED.season`,
      [id, String(row.tournament_name || id), row.year_number !== null && row.year_number !== undefined ? `Y${Number(row.year_number)}` : "unknown"]
    );
  }

  const matches = db.prepare("SELECT id, tournament_id, year_number, split_number, region, day, video_url FROM matches").all();
  for (const row of matches) {
    const id = String(row.id || "").trim();
    const tournamentIdRaw = canonicalTournamentId(row.tournament_id);
    const tournamentId = tournamentIdRaw || "unknown_tournament";
    if (!id) continue;
    const title = `Y${Number(row.year_number || 0)} S${Number(row.split_number || 0)} ${String(row.region || "UNKNOWN")} Day ${Number(row.day || 0)}`;
    await pool.query(
      `INSERT INTO matches (id, tournament_id, faceit_match_id, title, played_at)
       VALUES ($1, $2, $3, $4, NOW())
       ON CONFLICT (id) DO UPDATE
       SET tournament_id = EXCLUDED.tournament_id,
           faceit_match_id = EXCLUDED.faceit_match_id,
           title = EXCLUDED.title`,
      [id, tournamentId, id, title]
    );
  }

  const mapRows = db.prepare("SELECT id, match_id, mp_id, round_number FROM maps").all();
  const matchVideo = db.prepare("SELECT id, video_url, youtube_video_id FROM matches").all();
  const matchVideoById = new Map(matchVideo.map((m) => [String(m.id), m]));
  for (const row of mapRows) {
    const id = String(row.id || "").trim();
    const matchId = String(row.match_id || "").trim();
    if (!id || !matchId) continue;
    const m = matchVideoById.get(matchId) || {};
    const mapName = String(row.mp_id || `round_${Number(row.round_number || 1)}`);
    await pool.query(
      `INSERT INTO maps (
         id, match_id, map_name, video_url, work_fragment_start_sec, work_fragment_end_sec, ring1_start_sec, ring2_start_sec
       ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
       ON CONFLICT (id) DO UPDATE
       SET match_id = EXCLUDED.match_id,
           map_name = EXCLUDED.map_name,
           video_url = EXCLUDED.video_url`,
      [id, matchId, mapName, String(m.video_url || "/media/unknown.mp4"), 0, 1200, 0, 375]
    );
  }
  db.close();
}

function mapVideoToMapIdLookup(tournamentsDbPath) {
  const lookup = new Map();
  if (!isCatalogDb(tournamentsDbPath)) return lookup;
  const db = new DatabaseSync(tournamentsDbPath, { readOnly: true });
  const rows = db.prepare(
    `SELECT g.output_filename AS output_filename, m.id AS match_id, g.game_number AS game_number, mp.id AS map_id
     FROM games g
     JOIN matches m ON m.youtube_video_id = g.youtube_video_id
     LEFT JOIN maps mp ON mp.match_id = m.id AND mp.round_number = g.game_number
     WHERE g.output_filename IS NOT NULL`
  ).all();
  for (const row of rows) {
    const videoName = path.basename(String(row.output_filename || ""));
    const mapId = String(row.map_id || `${String(row.match_id)}_map${Number(row.game_number || 1)}`);
    if (videoName) lookup.set(videoName, mapId);
  }
  db.close();
  return lookup;
}

async function upsertTeamsAndRings(pool, mapStartDbPath, videoToMapId) {
  if (!fs.existsSync(mapStartDbPath)) return;
  const db = new DatabaseSync(mapStartDbPath, { readOnly: true });
  const detections = db.prepare("SELECT rowid, video_name, teams FROM map_start_detection").all();
  for (const row of detections) {
    const videoName = String(row.video_name || "").trim();
    const mapId = videoToMapId.get(videoName);
    if (!mapId) continue;
    let teams = [];
    try {
      teams = JSON.parse(String(row.teams || "[]"));
    } catch {
      teams = [];
    }
    for (const t of teams) {
      const slot = Number(t.team_slot || 0);
      const teamId = slot > 0 ? `TEAM_${slot}` : "";
      const teamName = String(t.team_name || "").trim();
      if (!teamId || !teamName) continue;
      await pool.query(
        `INSERT INTO teams (id, name) VALUES ($1,$2)
         ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name`,
        [teamId, teamName]
      );
      await pool.query(
        `INSERT INTO map_teams (map_id, team_id, team_name)
         VALUES ($1,$2,$3)
         ON CONFLICT (map_id, team_id) DO UPDATE SET team_name = EXCLUDED.team_name`,
        [mapId, teamId, teamName]
      );
    }

    const rings = db.prepare(
      `SELECT ring_number, time_start, center, radius
       FROM Rings
       WHERE game_id = ?
       ORDER BY ring_number ASC`
    ).all(row.rowid);
    for (const ring of rings) {
      let cx = 0;
      let cy = 0;
      try {
        const center = JSON.parse(String(ring.center || "{}"));
        cx = Number(center.x || 0);
        cy = Number(center.y || 0);
      } catch {
        cx = 0;
        cy = 0;
      }
      await pool.query(
        `INSERT INTO map_rings (map_id, timestamp_sec, x, y, radius, segment, confidence)
         VALUES ($1,$2,$3,$4,$5,$6,$7)`,
        [
          mapId,
          Number(ring.time_start || 0),
          cx,
          cy,
          Number(ring.radius || 0),
          Number(ring.ring_number || 1),
          1,
        ]
      );
    }
  }
  db.close();
}

async function upsertTracks(pool, tracksDir) {
  if (!fs.existsSync(tracksDir)) return;
  const knownMapRows = await pool.query("SELECT id FROM maps");
  const knownMaps = new Set(knownMapRows.rows.map((row) => String(row.id)));
  const files = fs.readdirSync(tracksDir).filter((name) => name.toLowerCase().endsWith(".json"));
  for (const fileName of files) {
    const filePath = path.join(tracksDir, fileName);
    let payload;
    try {
      payload = JSON.parse(fs.readFileSync(filePath, "utf-8"));
    } catch {
      continue;
    }
    const mapId = String(payload.mapId || "").trim();
    if (!mapId || !Array.isArray(payload.teams)) continue;
    if (!knownMaps.has(mapId)) continue;
    for (const team of payload.teams) {
      const teamId = String(team.team_id || team.teamId || "").trim();
      if (!teamId) continue;
      const teamName = String(team.team_name || team.teamName || teamId);
      await pool.query(
        `INSERT INTO teams (id, name) VALUES ($1,$2)
         ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name`,
        [teamId, teamName]
      );
      await pool.query(
        `INSERT INTO map_teams (map_id, team_id, team_name)
         VALUES ($1,$2,$3)
         ON CONFLICT (map_id, team_id) DO UPDATE SET team_name = EXCLUDED.team_name`,
        [mapId, teamId, teamName]
      );

      const points = Array.isArray(team.points) ? team.points : [];
      for (const point of points) {
        const ts = Number(point.timestampSec ?? point.timestamp ?? 0);
        await pool.query(
          `INSERT INTO team_tracks (
             map_id, team_id, timestamp_sec, x, y, confidence,
             eliminated, elimination_timestamp_sec, elimination_frame, elimination_confidence, elimination_method
           ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`,
          [
            mapId,
            teamId,
            ts,
            Number(point.x || 0),
            Number(point.y || 0),
            Number(point.confidence ?? 1),
            Boolean(team.eliminated),
            team.eliminationTimestampSec !== undefined ? Number(team.eliminationTimestampSec) : null,
            team.eliminationFrame !== undefined ? Number(team.eliminationFrame) : null,
            team.eliminationConfidence !== undefined ? Number(team.eliminationConfidence) : null,
            team.eliminationMethod !== undefined ? String(team.eliminationMethod) : null,
          ]
        );
      }
    }
  }
}

async function upsertJobs(pool, jobsStore) {
  if (!fs.existsSync(jobsStore)) return;
  let payload = {};
  try {
    payload = JSON.parse(fs.readFileSync(jobsStore, "utf-8"));
  } catch {
    return;
  }
  const jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
  for (const job of jobs) {
    const id = String(job.id || "").trim();
    if (!id) continue;
    await pool.query(
      `INSERT INTO api_jobs (
         id, job_type, status, command, current_action, last_heartbeat_at, progress_percent, queued_at,
         started_at, finished_at, duration_ms, map_id, match_id, video, team_statuses, errors, payload
       ) VALUES (
         $1,$2,$3,$4,$5,$6,$7,$8,
         $9,$10,$11,$12,$13,$14,$15::jsonb,$16::jsonb,$17::jsonb
       )
       ON CONFLICT (id) DO UPDATE SET
         job_type = EXCLUDED.job_type,
         status = EXCLUDED.status,
         current_action = EXCLUDED.current_action,
         last_heartbeat_at = EXCLUDED.last_heartbeat_at,
         progress_percent = EXCLUDED.progress_percent,
         started_at = EXCLUDED.started_at,
         finished_at = EXCLUDED.finished_at,
         duration_ms = EXCLUDED.duration_ms,
         map_id = EXCLUDED.map_id,
         match_id = EXCLUDED.match_id,
         video = EXCLUDED.video,
         team_statuses = EXCLUDED.team_statuses,
         errors = EXCLUDED.errors,
         payload = EXCLUDED.payload`,
      [
        id,
        String(job.jobType || "analysis"),
        String(job.status || "queued"),
        String(job.command || ""),
        job.currentAction || null,
        job.lastHeartbeatAt || null,
        Number(job.progressPercent || 0),
        String(job.queuedAt || new Date().toISOString()),
        job.startedAt || null,
        job.finishedAt || null,
        job.durationMs !== undefined ? Number(job.durationMs) : null,
        job.mapId || null,
        job.matchId || null,
        job.video || null,
        JSON.stringify(Array.isArray(job.teamStatuses) ? job.teamStatuses : []),
        JSON.stringify(Array.isArray(job.errors) ? job.errors : []),
        JSON.stringify(job.payload && typeof job.payload === "object" ? job.payload : {}),
      ]
    );
  }
}

async function main() {
  const projectRoot = path.resolve(__dirname, "..", "..");
  const runtime = loadRuntimePaths(projectRoot);
  const pool = getPool();
  console.log("[ETL] Start", runtime);
  try {
    try {
      await pool.query("BEGIN");
      await pool.query("TRUNCATE TABLE api_jobs, team_tracks, map_rings, map_teams, maps, matches, tournaments, teams RESTART IDENTITY CASCADE");
      await pool.query("COMMIT");
    } catch (error) {
      await pool.query("ROLLBACK");
      throw error;
    }
    await upsertBaseCatalog(pool, runtime.tournamentsDb);
    const videoMap = mapVideoToMapIdLookup(runtime.tournamentsDb);
    await upsertTeamsAndRings(pool, runtime.mapStartDb, videoMap);
    await upsertTracks(pool, runtime.tracksDir);
    await upsertJobs(pool, runtime.jobsStore);
    console.log("[ETL] Done");
  } finally {
    await pool.end();
  }
}

main().catch((error) => {
  console.error("[ETL] failed", error);
  process.exit(1);
});
