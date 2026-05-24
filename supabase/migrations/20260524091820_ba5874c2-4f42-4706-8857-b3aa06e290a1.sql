
-- ====== reference / structure ======
CREATE TABLE public.algs_seasons (
  id text PRIMARY KEY, name text, is_main boolean,
  start_date timestamptz, end_date timestamptz,
  fetched_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.algs_tournaments (
  id text PRIMARY KEY, season_id text, vendor_id text, name text,
  start_date timestamptz, end_date timestamptz,
  fetched_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.algs_regions (
  id text PRIMARY KEY, tournament_id text, name text
);
CREATE TABLE public.algs_events (
  id text PRIMARY KEY, tournament_id text, region_id text, name text,
  start_date timestamptz, end_date timestamptz, has_standings boolean,
  fetched_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.algs_phases (
  id text PRIMARY KEY, event_id text, name text, phase_number int,
  format text, starts_at timestamptz, completed_at timestamptz,
  has_standings boolean
);
CREATE TABLE public.algs_series (
  id text PRIMARY KEY, name text, status text, series_number int,
  phase_id text, event_id text, region_id text, tournament_id text,
  season_id text, poi_draft_id text,
  starts_at timestamptz, completed_at timestamptz,
  is_match_point boolean, match_point_threshold int, vod_url text,
  fetched_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.algs_maps (
  id_ulid text PRIMARY KEY, name text, in_game_name text,
  canonical_id text, active boolean
);
CREATE TABLE public.algs_matches (
  id text PRIMARY KEY, match_number int, series_id text, phase_id text,
  event_id text, region_id text, tournament_id text, season_id text,
  map_id_ulid text, status text, in_game_status text,
  winner_determined boolean, winner_team_id text,
  winner_damage int, winner_kills int,
  started_at timestamptz, play_started_at timestamptz,
  completed_at timestamptz, raw_json jsonb
);
CREATE TABLE public.algs_match_banned_legends (
  match_id text, character_id text, PRIMARY KEY (match_id, character_id)
);

-- ====== entities ======
CREATE TABLE public.algs_teams (
  id text PRIMARY KEY, name text, short_name text, region text, disbanded boolean
);
CREATE TABLE public.algs_team_versions (
  version_id text PRIMARY KEY, team_id text, logo_light text, logo_dark text
);
CREATE TABLE public.algs_characters (
  id text PRIMARY KEY, name text, image text,
  character_type text, internal_name text
);
CREATE TABLE public.algs_players (
  id text PRIMARY KEY, name text, front_image text, personality_image text
);
CREATE TABLE public.algs_spawn_locations (
  id text PRIMARY KEY, map_id_ulid text, name text,
  x_norm double precision, y_norm double precision, in_game_drop_id int
);
CREATE TABLE public.algs_poi_drafts (
  id text PRIMARY KEY, series_id text, event_id text, region_id text,
  completed boolean, completed_at timestamptz, date timestamptz,
  time_to_pick int, raw_json jsonb
);
CREATE TABLE public.algs_poi_picks (
  id text PRIMARY KEY, draft_id text, pick_number int, actual_pick_number int,
  timed_out boolean, pick_by_time timestamptz, picked_at timestamptz,
  map_id_ulid text, spawn_location_id text,
  team_id text, team_version_id text, player_id text
);

-- ====== stats ======
CREATE TABLE public.algs_series_team_stats (
  series_id text, team_id text, version_id text,
  position int, points int, placement_points int, kills int,
  match_point_eligible boolean, won_match_point boolean,
  eliminated boolean, raw_json jsonb,
  PRIMARY KEY (series_id, team_id)
);
CREATE TABLE public.algs_match_team_stats (
  match_id text, team_id text, version_id text,
  placement int, placement_points int, points int, kills int,
  eliminated boolean, match_point_eligible boolean, raw_json jsonb,
  PRIMARY KEY (match_id, team_id)
);
CREATE TABLE public.algs_match_player_stats (
  match_id text, player_id text, team_id text,
  kills int, killed int, knocked_down int,
  character_id text, raw_json jsonb,
  PRIMARY KEY (match_id, player_id)
);
CREATE TABLE public.algs_series_weapon_stats (
  series_id text, weapon text, ammo_type text, gun_type text, kills int,
  PRIMARY KEY (series_id, weapon)
);
CREATE TABLE public.algs_series_character_stats (
  series_id text, character_id text, name text, kills int, damage int,
  PRIMARY KEY (series_id, character_id)
);
CREATE TABLE public.algs_series_character_compositions (
  series_id text, comp_idx int, slot_idx int, character_id text,
  PRIMARY KEY (series_id, comp_idx, slot_idx)
);
CREATE TABLE public.algs_series_player_agg (
  series_id text, player_id text, team_id text,
  matches_played int, match_series_played int,
  kills int, assists int,
  average_kills double precision, average_assists double precision,
  raw_json jsonb,
  PRIMARY KEY (series_id, player_id)
);
CREATE TABLE public.algs_series_banned_legends_agg (
  series_id text, character_id text, latest_match_number int,
  PRIMARY KEY (series_id, character_id)
);
CREATE TABLE public.algs_series_poi_stats (
  series_id text, spawn_location_id text, map_id_ulid text,
  avg_pick double precision, total_picks int,
  avg_survival_time double precision, avg_damage double precision,
  avg_kills double precision, avg_points double precision,
  avg_ring_damage double precision, avg_placement double precision,
  raw_json jsonb,
  PRIMARY KEY (series_id, spawn_location_id)
);

-- ====== event/phase aggregates ======
CREATE TABLE public.algs_event_teams (
  event_id text, team_id text, version_id text, raw_json jsonb,
  PRIMARY KEY (event_id, team_id)
);
CREATE TABLE public.algs_event_standings (
  event_id text, team_id text, version_id text,
  position int, points int, prize_money text, raw_json jsonb,
  PRIMARY KEY (event_id, team_id)
);
CREATE TABLE public.algs_event_schedule (
  event_id text, phase_id text, team_name text,
  group_name text, logo text,
  PRIMARY KEY (event_id, phase_id, team_name)
);
CREATE TABLE public.algs_phase_teams (
  phase_id text, team_id text, version_id text,
  group_name text, raw_json jsonb,
  PRIMARY KEY (phase_id, team_id)
);
CREATE TABLE public.algs_phase_standings (
  phase_id text, team_id text, position int, points int,
  group_name text, qualified boolean, in_live_series boolean,
  series_wins int, match_wins int, match_series_played int,
  avg_survival_time double precision, raw_json jsonb,
  PRIMARY KEY (phase_id, team_id)
);
CREATE TABLE public.algs_season_standings_teams (
  season_id text, team_id text, version_id text,
  region text, total_points int, raw_json jsonb,
  PRIMARY KEY (season_id, team_id)
);
CREATE TABLE public.algs_season_standings_players (
  season_id text, player_id text, team_id text,
  total_points int, raw_json jsonb,
  PRIMARY KEY (season_id, player_id)
);
CREATE TABLE public.algs_cc_leaderboard_teams (
  season_id text, event_id text, team_id text,
  position int, points int, region text, raw_json jsonb,
  PRIMARY KEY (season_id, event_id, team_id)
);
CREATE TABLE public.algs_cc_leaderboard_players (
  season_id text, event_id text, player_id text,
  position int, points int, region text, raw_json jsonb,
  PRIMARY KEY (season_id, event_id, player_id)
);
CREATE TABLE public.algs_live_streams (
  series_id text, stream_id text, name text,
  channel_name text, provider text, raw_json jsonb,
  PRIMARY KEY (series_id, stream_id)
);
CREATE TABLE public.algs_sync_state (
  kind text, ident text, fetched_at timestamptz, status text,
  PRIMARY KEY (kind, ident)
);

-- ====== indexes ======
CREATE INDEX ON public.algs_matches (series_id);
CREATE INDEX ON public.algs_matches (event_id);
CREATE INDEX ON public.algs_series (event_id);
CREATE INDEX ON public.algs_phases (event_id);
CREATE INDEX ON public.algs_events (region_id);
CREATE INDEX ON public.algs_poi_picks (draft_id);
CREATE INDEX ON public.algs_poi_picks (team_id);
CREATE INDEX ON public.algs_spawn_locations (map_id_ulid);
CREATE INDEX ON public.algs_event_standings (event_id);
CREATE INDEX ON public.algs_phase_standings (phase_id);
CREATE INDEX ON public.algs_match_team_stats (team_id);
CREATE INDEX ON public.algs_match_player_stats (player_id);
CREATE INDEX ON public.algs_match_player_stats (team_id);
CREATE INDEX ON public.algs_series_poi_stats (spawn_location_id);
CREATE INDEX ON public.algs_series_poi_stats (map_id_ulid);

-- ====== RLS: всем читать, писать только service role ======
DO $$
DECLARE t text;
BEGIN
  FOR t IN
    SELECT tablename FROM pg_tables
    WHERE schemaname='public' AND tablename LIKE 'algs\_%' ESCAPE '\'
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY "Public read %1$s" ON public.%1$I FOR SELECT USING (true)',
      t
    );
  END LOOP;
END$$;
