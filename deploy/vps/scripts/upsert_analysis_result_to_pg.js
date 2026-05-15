/* eslint-disable no-console */
const fs = require("fs");
const path = require("path");
const { Pool } = require("pg");

try {
  // eslint-disable-next-line global-require
  require("dotenv").config({ path: path.resolve(__dirname, "..", "..", ".env") });
} catch {
  // optional
}

function parseArgs(argv) {
  const out = { file: "" };
  for (let i = 2; i < argv.length; i += 1) {
    const token = String(argv[i] || "");
    if (token === "--file") {
      out.file = String(argv[i + 1] || "");
      i += 1;
    }
  }
  return out;
}

function connectionStringFromEnv() {
  if (process.env.DATABASE_URL) {
    return process.env.DATABASE_URL;
  }
  const user = encodeURIComponent(process.env.POSTGRES_USER || "apex");
  const password = encodeURIComponent(process.env.POSTGRES_PASSWORD || "apex");
  const host = process.env.POSTGRES_HOST || "localhost";
  const port = process.env.POSTGRES_PORT || "5432";
  const db = process.env.POSTGRES_DB || "apex_stats";
  return `postgresql://${user}:${password}@${host}:${port}/${db}`;
}

function safeNum(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

async function ensureExtendedTrackColumns(client) {
  await client.query("ALTER TABLE team_tracks ADD COLUMN IF NOT EXISTS map_x NUMERIC(10, 3)");
  await client.query("ALTER TABLE team_tracks ADD COLUMN IF NOT EXISTS map_y NUMERIC(10, 3)");
  await client.query("ALTER TABLE team_tracks ADD COLUMN IF NOT EXISTS source_frame_x NUMERIC(10, 3)");
  await client.query("ALTER TABLE team_tracks ADD COLUMN IF NOT EXISTS source_frame_y NUMERIC(10, 3)");
  await client.query("ALTER TABLE team_tracks ADD COLUMN IF NOT EXISTS map_space_valid BOOLEAN");
  await client.query("ALTER TABLE team_tracks ADD COLUMN IF NOT EXISTS backup_frame_space BOOLEAN");
  await client.query("ALTER TABLE team_tracks ADD COLUMN IF NOT EXISTS transform_state TEXT");
  await client.query("ALTER TABLE team_tracks ADD COLUMN IF NOT EXISTS transform_residual NUMERIC(10, 3)");
  await client.query("ALTER TABLE team_tracks ADD COLUMN IF NOT EXISTS bg_confidence NUMERIC(6, 3)");
}

async function ensureCatalog(client, payload) {
  const tournamentId = String(payload.tournamentId || "test").trim() || "test";
  const matchId = String(payload.matchId || "test").trim() || "test";
  const mapId = String(payload.mapId || "").trim();
  if (!mapId) {
    throw new Error("payload.mapId is required");
  }
  const mapName = String(payload.map || "mp_unknown");
  const video = String(payload.video || "");

  await client.query(
    `INSERT INTO tournaments (id, name, season)
     VALUES ($1, $2, $3)
     ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name`,
    [tournamentId, tournamentId, "unknown"]
  );

  await client.query(
    `INSERT INTO matches (id, tournament_id, faceit_match_id, title, played_at)
     VALUES ($1, $2, $3, $4, NOW())
     ON CONFLICT (id) DO UPDATE
     SET tournament_id = EXCLUDED.tournament_id,
         title = EXCLUDED.title`,
    [matchId, tournamentId, matchId, matchId]
  );

  await client.query(
    `INSERT INTO maps (
       id, match_id, map_name, video_url,
       work_fragment_start_sec, work_fragment_end_sec, ring1_start_sec, ring2_start_sec
     )
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
     ON CONFLICT (id) DO UPDATE
     SET map_name = EXCLUDED.map_name,
         video_url = EXCLUDED.video_url`,
    [mapId, matchId, mapName, video, 0, 1200, 0, 375]
  );
}

async function upsertAnalysisResult(client, payload) {
  const mapId = String(payload.mapId || "").trim();
  if (!mapId) {
    throw new Error("mapId is empty");
  }

  const teams = Array.isArray(payload.teams) ? payload.teams : [];
  const rings = Array.isArray(payload.rings) ? payload.rings : [];

  if (teams.length === 0) {
    // Safety net: do not wipe already materialized map tracks with an empty/debug payload.
    return { skipped: true, reason: "no teams in payload" };
  }

  await client.query("DELETE FROM team_tracks WHERE map_id = $1", [mapId]);
  await client.query("DELETE FROM map_rings WHERE map_id = $1", [mapId]);

  for (const team of teams) {
    const teamId = String(team.team_id || team.teamId || "").trim();
    if (!teamId) continue;
    const teamName = String(team.team_name || team.teamName || teamId);

    await client.query(
      `INSERT INTO teams (id, name) VALUES ($1, $2)
       ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name`,
      [teamId, teamName]
    );

    await client.query(
      `INSERT INTO map_teams (map_id, team_id, team_name)
       VALUES ($1, $2, $3)
       ON CONFLICT (map_id, team_id) DO UPDATE SET team_name = EXCLUDED.team_name`,
      [mapId, teamId, teamName]
    );

    const points = Array.isArray(team.points) ? team.points : [];
    for (const point of points) {
      const ts = safeNum(point.timestampSec ?? point.timestamp, 0);
      await client.query(
        `INSERT INTO team_tracks (
           map_id, team_id, timestamp_sec, x, y, confidence,
           map_x, map_y, source_frame_x, source_frame_y,
           map_space_valid, backup_frame_space, transform_state, transform_residual, bg_confidence,
           eliminated, elimination_timestamp_sec, elimination_frame, elimination_confidence, elimination_method
         ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)`,
        [
          mapId,
          teamId,
          ts,
          Math.round(safeNum(point.x, 0)),
          Math.round(safeNum(point.y, 0)),
          safeNum(point.confidence, 1),
          point.mapX !== undefined && point.mapX !== null ? safeNum(point.mapX, 0) : null,
          point.mapY !== undefined && point.mapY !== null ? safeNum(point.mapY, 0) : null,
          point.sourceFrameX !== undefined && point.sourceFrameX !== null ? safeNum(point.sourceFrameX, 0) : null,
          point.sourceFrameY !== undefined && point.sourceFrameY !== null ? safeNum(point.sourceFrameY, 0) : null,
          point.mapSpaceValid !== undefined ? Boolean(point.mapSpaceValid) : null,
          point.backupFrameSpace !== undefined ? Boolean(point.backupFrameSpace) : null,
          point.transformState !== undefined ? String(point.transformState) : null,
          point.transformResidual !== undefined ? safeNum(point.transformResidual, 0) : null,
          point.bgConfidence !== undefined ? safeNum(point.bgConfidence, 0) : null,
          Boolean(team.eliminated),
          team.eliminationTimestampSec !== undefined ? safeNum(team.eliminationTimestampSec, 0) : null,
          team.eliminationFrame !== undefined ? Math.round(safeNum(team.eliminationFrame, 0)) : null,
          team.eliminationConfidence !== undefined ? safeNum(team.eliminationConfidence, 0) : null,
          team.eliminationMethod !== undefined ? String(team.eliminationMethod) : null,
        ]
      );
    }
  }

  for (const ring of rings) {
    await client.query(
      `INSERT INTO map_rings (map_id, timestamp_sec, x, y, radius, segment, confidence)
       VALUES ($1,$2,$3,$4,$5,$6,$7)`,
      [
        mapId,
        safeNum(ring.timestampSec, 0),
        safeNum(ring.x, 0),
        safeNum(ring.y, 0),
        safeNum(ring.radius, 0),
        Math.round(safeNum(ring.segment, 1)),
        safeNum(ring.confidence, 1),
      ]
    );
  }
  return { skipped: false };
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.file) {
    throw new Error("Usage: node upsert_analysis_result_to_pg.js --file <analysis-json>");
  }
  const filePath = path.isAbsolute(args.file) ? args.file : path.resolve(process.cwd(), args.file);
  if (!fs.existsSync(filePath)) {
    throw new Error(`File not found: ${filePath}`);
  }
  const payload = JSON.parse(fs.readFileSync(filePath, "utf-8"));

  const pool = new Pool({ connectionString: connectionStringFromEnv() });
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await ensureExtendedTrackColumns(client);
    await ensureCatalog(client, payload);
    const upsertResult = await upsertAnalysisResult(client, payload);
    await client.query("COMMIT");
    if (upsertResult?.skipped) {
      console.log(`postgres sync skipped: ${upsertResult.reason}`);
      return;
    }
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
    await pool.end();
  }
  const mapId = String(payload.mapId || "");
  const teams = Array.isArray(payload.teams) ? payload.teams.length : 0;
  console.log(`postgres sync completed: mapId=${mapId}, teams=${teams}`);
}

main().catch((error) => {
  console.error(`postgres sync failed: ${error?.message || error}`);
  process.exit(1);
});
