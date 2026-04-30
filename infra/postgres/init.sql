CREATE TABLE IF NOT EXISTS tournaments (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  season TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
  id TEXT PRIMARY KEY,
  tournament_id TEXT NOT NULL REFERENCES tournaments(id),
  faceit_match_id TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  played_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS maps (
  id TEXT PRIMARY KEY,
  match_id TEXT NOT NULL REFERENCES matches(id),
  map_name TEXT NOT NULL,
  video_url TEXT NOT NULL,
  work_fragment_start_sec INTEGER NOT NULL,
  work_fragment_end_sec INTEGER NOT NULL,
  ring1_start_sec INTEGER NOT NULL,
  ring2_start_sec INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS map_team_configs (
  map_name TEXT NOT NULL,
  team_id TEXT NOT NULL REFERENCES teams(id),
  hsv_low_h INTEGER NOT NULL,
  hsv_low_s INTEGER NOT NULL,
  hsv_low_v INTEGER NOT NULL,
  hsv_high_h INTEGER NOT NULL,
  hsv_high_s INTEGER NOT NULL,
  hsv_high_v INTEGER NOT NULL,
  morph_kernel_size INTEGER NOT NULL,
  min_area INTEGER NOT NULL,
  max_area INTEGER NOT NULL,
  outlier_threshold_ratio NUMERIC(6, 4),
  PRIMARY KEY (map_name, team_id)
);

CREATE TABLE IF NOT EXISTS team_tracks (
  id BIGSERIAL PRIMARY KEY,
  map_id TEXT NOT NULL REFERENCES maps(id),
  team_id TEXT NOT NULL REFERENCES teams(id),
  timestamp_sec NUMERIC(10, 3) NOT NULL,
  x INTEGER NOT NULL,
  y INTEGER NOT NULL,
  confidence NUMERIC(6, 3) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_team_tracks_map_team_time
  ON team_tracks (map_id, team_id, timestamp_sec);
