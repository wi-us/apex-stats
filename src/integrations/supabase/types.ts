export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.5"
  }
  public: {
    Tables: {
      algs_cc_leaderboard_players: {
        Row: {
          event_id: string
          player_id: string
          points: number | null
          position: number | null
          raw_json: Json | null
          region: string | null
          season_id: string
        }
        Insert: {
          event_id: string
          player_id: string
          points?: number | null
          position?: number | null
          raw_json?: Json | null
          region?: string | null
          season_id: string
        }
        Update: {
          event_id?: string
          player_id?: string
          points?: number | null
          position?: number | null
          raw_json?: Json | null
          region?: string | null
          season_id?: string
        }
        Relationships: []
      }
      algs_cc_leaderboard_teams: {
        Row: {
          event_id: string
          points: number | null
          position: number | null
          raw_json: Json | null
          region: string | null
          season_id: string
          team_id: string
        }
        Insert: {
          event_id: string
          points?: number | null
          position?: number | null
          raw_json?: Json | null
          region?: string | null
          season_id: string
          team_id: string
        }
        Update: {
          event_id?: string
          points?: number | null
          position?: number | null
          raw_json?: Json | null
          region?: string | null
          season_id?: string
          team_id?: string
        }
        Relationships: []
      }
      algs_characters: {
        Row: {
          character_type: string | null
          id: string
          image: string | null
          internal_name: string | null
          name: string | null
        }
        Insert: {
          character_type?: string | null
          id: string
          image?: string | null
          internal_name?: string | null
          name?: string | null
        }
        Update: {
          character_type?: string | null
          id?: string
          image?: string | null
          internal_name?: string | null
          name?: string | null
        }
        Relationships: []
      }
      algs_event_schedule: {
        Row: {
          event_id: string
          group_name: string | null
          logo: string | null
          phase_id: string
          team_name: string
        }
        Insert: {
          event_id: string
          group_name?: string | null
          logo?: string | null
          phase_id: string
          team_name: string
        }
        Update: {
          event_id?: string
          group_name?: string | null
          logo?: string | null
          phase_id?: string
          team_name?: string
        }
        Relationships: []
      }
      algs_event_standings: {
        Row: {
          event_id: string
          points: number | null
          position: number | null
          prize_money: string | null
          raw_json: Json | null
          team_id: string
          version_id: string | null
        }
        Insert: {
          event_id: string
          points?: number | null
          position?: number | null
          prize_money?: string | null
          raw_json?: Json | null
          team_id: string
          version_id?: string | null
        }
        Update: {
          event_id?: string
          points?: number | null
          position?: number | null
          prize_money?: string | null
          raw_json?: Json | null
          team_id?: string
          version_id?: string | null
        }
        Relationships: []
      }
      algs_event_teams: {
        Row: {
          event_id: string
          raw_json: Json | null
          team_id: string
          version_id: string | null
        }
        Insert: {
          event_id: string
          raw_json?: Json | null
          team_id: string
          version_id?: string | null
        }
        Update: {
          event_id?: string
          raw_json?: Json | null
          team_id?: string
          version_id?: string | null
        }
        Relationships: []
      }
      algs_events: {
        Row: {
          end_date: string | null
          fetched_at: string
          has_standings: boolean | null
          id: string
          name: string | null
          region_id: string | null
          start_date: string | null
          tournament_id: string | null
        }
        Insert: {
          end_date?: string | null
          fetched_at?: string
          has_standings?: boolean | null
          id: string
          name?: string | null
          region_id?: string | null
          start_date?: string | null
          tournament_id?: string | null
        }
        Update: {
          end_date?: string | null
          fetched_at?: string
          has_standings?: boolean | null
          id?: string
          name?: string | null
          region_id?: string | null
          start_date?: string | null
          tournament_id?: string | null
        }
        Relationships: []
      }
      algs_live_streams: {
        Row: {
          channel_name: string | null
          name: string | null
          provider: string | null
          raw_json: Json | null
          series_id: string
          stream_id: string
        }
        Insert: {
          channel_name?: string | null
          name?: string | null
          provider?: string | null
          raw_json?: Json | null
          series_id: string
          stream_id: string
        }
        Update: {
          channel_name?: string | null
          name?: string | null
          provider?: string | null
          raw_json?: Json | null
          series_id?: string
          stream_id?: string
        }
        Relationships: []
      }
      algs_maps: {
        Row: {
          active: boolean | null
          canonical_id: string | null
          id_ulid: string
          in_game_name: string | null
          name: string | null
        }
        Insert: {
          active?: boolean | null
          canonical_id?: string | null
          id_ulid: string
          in_game_name?: string | null
          name?: string | null
        }
        Update: {
          active?: boolean | null
          canonical_id?: string | null
          id_ulid?: string
          in_game_name?: string | null
          name?: string | null
        }
        Relationships: []
      }
      algs_match_banned_legends: {
        Row: {
          character_id: string
          match_id: string
        }
        Insert: {
          character_id: string
          match_id: string
        }
        Update: {
          character_id?: string
          match_id?: string
        }
        Relationships: []
      }
      algs_match_player_stats: {
        Row: {
          character_id: string | null
          killed: number | null
          kills: number | null
          knocked_down: number | null
          match_id: string
          player_id: string
          raw_json: Json | null
          team_id: string | null
        }
        Insert: {
          character_id?: string | null
          killed?: number | null
          kills?: number | null
          knocked_down?: number | null
          match_id: string
          player_id: string
          raw_json?: Json | null
          team_id?: string | null
        }
        Update: {
          character_id?: string | null
          killed?: number | null
          kills?: number | null
          knocked_down?: number | null
          match_id?: string
          player_id?: string
          raw_json?: Json | null
          team_id?: string | null
        }
        Relationships: []
      }
      algs_match_team_stats: {
        Row: {
          eliminated: boolean | null
          kills: number | null
          match_id: string
          match_point_eligible: boolean | null
          placement: number | null
          placement_points: number | null
          points: number | null
          raw_json: Json | null
          team_id: string
          version_id: string | null
        }
        Insert: {
          eliminated?: boolean | null
          kills?: number | null
          match_id: string
          match_point_eligible?: boolean | null
          placement?: number | null
          placement_points?: number | null
          points?: number | null
          raw_json?: Json | null
          team_id: string
          version_id?: string | null
        }
        Update: {
          eliminated?: boolean | null
          kills?: number | null
          match_id?: string
          match_point_eligible?: boolean | null
          placement?: number | null
          placement_points?: number | null
          points?: number | null
          raw_json?: Json | null
          team_id?: string
          version_id?: string | null
        }
        Relationships: []
      }
      algs_matches: {
        Row: {
          completed_at: string | null
          event_id: string | null
          id: string
          in_game_status: string | null
          map_id_ulid: string | null
          match_number: number | null
          phase_id: string | null
          play_started_at: string | null
          raw_json: Json | null
          region_id: string | null
          season_id: string | null
          series_id: string | null
          started_at: string | null
          status: string | null
          tournament_id: string | null
          winner_damage: number | null
          winner_determined: boolean | null
          winner_kills: number | null
          winner_team_id: string | null
        }
        Insert: {
          completed_at?: string | null
          event_id?: string | null
          id: string
          in_game_status?: string | null
          map_id_ulid?: string | null
          match_number?: number | null
          phase_id?: string | null
          play_started_at?: string | null
          raw_json?: Json | null
          region_id?: string | null
          season_id?: string | null
          series_id?: string | null
          started_at?: string | null
          status?: string | null
          tournament_id?: string | null
          winner_damage?: number | null
          winner_determined?: boolean | null
          winner_kills?: number | null
          winner_team_id?: string | null
        }
        Update: {
          completed_at?: string | null
          event_id?: string | null
          id?: string
          in_game_status?: string | null
          map_id_ulid?: string | null
          match_number?: number | null
          phase_id?: string | null
          play_started_at?: string | null
          raw_json?: Json | null
          region_id?: string | null
          season_id?: string | null
          series_id?: string | null
          started_at?: string | null
          status?: string | null
          tournament_id?: string | null
          winner_damage?: number | null
          winner_determined?: boolean | null
          winner_kills?: number | null
          winner_team_id?: string | null
        }
        Relationships: []
      }
      algs_phase_standings: {
        Row: {
          avg_survival_time: number | null
          group_name: string | null
          in_live_series: boolean | null
          match_series_played: number | null
          match_wins: number | null
          phase_id: string
          points: number | null
          position: number | null
          qualified: boolean | null
          raw_json: Json | null
          series_wins: number | null
          team_id: string
        }
        Insert: {
          avg_survival_time?: number | null
          group_name?: string | null
          in_live_series?: boolean | null
          match_series_played?: number | null
          match_wins?: number | null
          phase_id: string
          points?: number | null
          position?: number | null
          qualified?: boolean | null
          raw_json?: Json | null
          series_wins?: number | null
          team_id: string
        }
        Update: {
          avg_survival_time?: number | null
          group_name?: string | null
          in_live_series?: boolean | null
          match_series_played?: number | null
          match_wins?: number | null
          phase_id?: string
          points?: number | null
          position?: number | null
          qualified?: boolean | null
          raw_json?: Json | null
          series_wins?: number | null
          team_id?: string
        }
        Relationships: []
      }
      algs_phase_teams: {
        Row: {
          group_name: string | null
          phase_id: string
          raw_json: Json | null
          team_id: string
          version_id: string | null
        }
        Insert: {
          group_name?: string | null
          phase_id: string
          raw_json?: Json | null
          team_id: string
          version_id?: string | null
        }
        Update: {
          group_name?: string | null
          phase_id?: string
          raw_json?: Json | null
          team_id?: string
          version_id?: string | null
        }
        Relationships: []
      }
      algs_phases: {
        Row: {
          completed_at: string | null
          event_id: string | null
          format: string | null
          has_standings: boolean | null
          id: string
          name: string | null
          phase_number: number | null
          starts_at: string | null
        }
        Insert: {
          completed_at?: string | null
          event_id?: string | null
          format?: string | null
          has_standings?: boolean | null
          id: string
          name?: string | null
          phase_number?: number | null
          starts_at?: string | null
        }
        Update: {
          completed_at?: string | null
          event_id?: string | null
          format?: string | null
          has_standings?: boolean | null
          id?: string
          name?: string | null
          phase_number?: number | null
          starts_at?: string | null
        }
        Relationships: []
      }
      algs_players: {
        Row: {
          front_image: string | null
          id: string
          name: string | null
          personality_image: string | null
        }
        Insert: {
          front_image?: string | null
          id: string
          name?: string | null
          personality_image?: string | null
        }
        Update: {
          front_image?: string | null
          id?: string
          name?: string | null
          personality_image?: string | null
        }
        Relationships: []
      }
      algs_poi_drafts: {
        Row: {
          completed: boolean | null
          completed_at: string | null
          date: string | null
          event_id: string | null
          id: string
          raw_json: Json | null
          region_id: string | null
          series_id: string | null
          time_to_pick: number | null
        }
        Insert: {
          completed?: boolean | null
          completed_at?: string | null
          date?: string | null
          event_id?: string | null
          id: string
          raw_json?: Json | null
          region_id?: string | null
          series_id?: string | null
          time_to_pick?: number | null
        }
        Update: {
          completed?: boolean | null
          completed_at?: string | null
          date?: string | null
          event_id?: string | null
          id?: string
          raw_json?: Json | null
          region_id?: string | null
          series_id?: string | null
          time_to_pick?: number | null
        }
        Relationships: []
      }
      algs_poi_picks: {
        Row: {
          actual_pick_number: number | null
          draft_id: string | null
          id: string
          map_id_ulid: string | null
          pick_by_time: string | null
          pick_number: number | null
          picked_at: string | null
          player_id: string | null
          spawn_location_id: string | null
          team_id: string | null
          team_version_id: string | null
          timed_out: boolean | null
        }
        Insert: {
          actual_pick_number?: number | null
          draft_id?: string | null
          id: string
          map_id_ulid?: string | null
          pick_by_time?: string | null
          pick_number?: number | null
          picked_at?: string | null
          player_id?: string | null
          spawn_location_id?: string | null
          team_id?: string | null
          team_version_id?: string | null
          timed_out?: boolean | null
        }
        Update: {
          actual_pick_number?: number | null
          draft_id?: string | null
          id?: string
          map_id_ulid?: string | null
          pick_by_time?: string | null
          pick_number?: number | null
          picked_at?: string | null
          player_id?: string | null
          spawn_location_id?: string | null
          team_id?: string | null
          team_version_id?: string | null
          timed_out?: boolean | null
        }
        Relationships: []
      }
      algs_regions: {
        Row: {
          id: string
          name: string | null
          tournament_id: string | null
        }
        Insert: {
          id: string
          name?: string | null
          tournament_id?: string | null
        }
        Update: {
          id?: string
          name?: string | null
          tournament_id?: string | null
        }
        Relationships: []
      }
      algs_season_standings_players: {
        Row: {
          player_id: string
          raw_json: Json | null
          season_id: string
          team_id: string | null
          total_points: number | null
        }
        Insert: {
          player_id: string
          raw_json?: Json | null
          season_id: string
          team_id?: string | null
          total_points?: number | null
        }
        Update: {
          player_id?: string
          raw_json?: Json | null
          season_id?: string
          team_id?: string | null
          total_points?: number | null
        }
        Relationships: []
      }
      algs_season_standings_teams: {
        Row: {
          raw_json: Json | null
          region: string | null
          season_id: string
          team_id: string
          total_points: number | null
          version_id: string | null
        }
        Insert: {
          raw_json?: Json | null
          region?: string | null
          season_id: string
          team_id: string
          total_points?: number | null
          version_id?: string | null
        }
        Update: {
          raw_json?: Json | null
          region?: string | null
          season_id?: string
          team_id?: string
          total_points?: number | null
          version_id?: string | null
        }
        Relationships: []
      }
      algs_seasons: {
        Row: {
          end_date: string | null
          fetched_at: string
          id: string
          is_main: boolean | null
          name: string | null
          start_date: string | null
        }
        Insert: {
          end_date?: string | null
          fetched_at?: string
          id: string
          is_main?: boolean | null
          name?: string | null
          start_date?: string | null
        }
        Update: {
          end_date?: string | null
          fetched_at?: string
          id?: string
          is_main?: boolean | null
          name?: string | null
          start_date?: string | null
        }
        Relationships: []
      }
      algs_series: {
        Row: {
          completed_at: string | null
          event_id: string | null
          fetched_at: string
          id: string
          is_match_point: boolean | null
          match_point_threshold: number | null
          name: string | null
          phase_id: string | null
          poi_draft_id: string | null
          region_id: string | null
          season_id: string | null
          series_number: number | null
          starts_at: string | null
          status: string | null
          tournament_id: string | null
          vod_url: string | null
        }
        Insert: {
          completed_at?: string | null
          event_id?: string | null
          fetched_at?: string
          id: string
          is_match_point?: boolean | null
          match_point_threshold?: number | null
          name?: string | null
          phase_id?: string | null
          poi_draft_id?: string | null
          region_id?: string | null
          season_id?: string | null
          series_number?: number | null
          starts_at?: string | null
          status?: string | null
          tournament_id?: string | null
          vod_url?: string | null
        }
        Update: {
          completed_at?: string | null
          event_id?: string | null
          fetched_at?: string
          id?: string
          is_match_point?: boolean | null
          match_point_threshold?: number | null
          name?: string | null
          phase_id?: string | null
          poi_draft_id?: string | null
          region_id?: string | null
          season_id?: string | null
          series_number?: number | null
          starts_at?: string | null
          status?: string | null
          tournament_id?: string | null
          vod_url?: string | null
        }
        Relationships: []
      }
      algs_series_banned_legends_agg: {
        Row: {
          character_id: string
          latest_match_number: number | null
          series_id: string
        }
        Insert: {
          character_id: string
          latest_match_number?: number | null
          series_id: string
        }
        Update: {
          character_id?: string
          latest_match_number?: number | null
          series_id?: string
        }
        Relationships: []
      }
      algs_series_character_compositions: {
        Row: {
          character_id: string | null
          comp_idx: number
          series_id: string
          slot_idx: number
        }
        Insert: {
          character_id?: string | null
          comp_idx: number
          series_id: string
          slot_idx: number
        }
        Update: {
          character_id?: string | null
          comp_idx?: number
          series_id?: string
          slot_idx?: number
        }
        Relationships: []
      }
      algs_series_character_stats: {
        Row: {
          character_id: string
          damage: number | null
          kills: number | null
          name: string | null
          series_id: string
        }
        Insert: {
          character_id: string
          damage?: number | null
          kills?: number | null
          name?: string | null
          series_id: string
        }
        Update: {
          character_id?: string
          damage?: number | null
          kills?: number | null
          name?: string | null
          series_id?: string
        }
        Relationships: []
      }
      algs_series_player_agg: {
        Row: {
          assists: number | null
          average_assists: number | null
          average_kills: number | null
          kills: number | null
          match_series_played: number | null
          matches_played: number | null
          player_id: string
          raw_json: Json | null
          series_id: string
          team_id: string | null
        }
        Insert: {
          assists?: number | null
          average_assists?: number | null
          average_kills?: number | null
          kills?: number | null
          match_series_played?: number | null
          matches_played?: number | null
          player_id: string
          raw_json?: Json | null
          series_id: string
          team_id?: string | null
        }
        Update: {
          assists?: number | null
          average_assists?: number | null
          average_kills?: number | null
          kills?: number | null
          match_series_played?: number | null
          matches_played?: number | null
          player_id?: string
          raw_json?: Json | null
          series_id?: string
          team_id?: string | null
        }
        Relationships: []
      }
      algs_series_poi_stats: {
        Row: {
          avg_damage: number | null
          avg_kills: number | null
          avg_pick: number | null
          avg_placement: number | null
          avg_points: number | null
          avg_ring_damage: number | null
          avg_survival_time: number | null
          map_id_ulid: string | null
          raw_json: Json | null
          series_id: string
          spawn_location_id: string
          total_picks: number | null
        }
        Insert: {
          avg_damage?: number | null
          avg_kills?: number | null
          avg_pick?: number | null
          avg_placement?: number | null
          avg_points?: number | null
          avg_ring_damage?: number | null
          avg_survival_time?: number | null
          map_id_ulid?: string | null
          raw_json?: Json | null
          series_id: string
          spawn_location_id: string
          total_picks?: number | null
        }
        Update: {
          avg_damage?: number | null
          avg_kills?: number | null
          avg_pick?: number | null
          avg_placement?: number | null
          avg_points?: number | null
          avg_ring_damage?: number | null
          avg_survival_time?: number | null
          map_id_ulid?: string | null
          raw_json?: Json | null
          series_id?: string
          spawn_location_id?: string
          total_picks?: number | null
        }
        Relationships: []
      }
      algs_series_team_stats: {
        Row: {
          eliminated: boolean | null
          kills: number | null
          match_point_eligible: boolean | null
          placement_points: number | null
          points: number | null
          position: number | null
          raw_json: Json | null
          series_id: string
          team_id: string
          version_id: string | null
          won_match_point: boolean | null
        }
        Insert: {
          eliminated?: boolean | null
          kills?: number | null
          match_point_eligible?: boolean | null
          placement_points?: number | null
          points?: number | null
          position?: number | null
          raw_json?: Json | null
          series_id: string
          team_id: string
          version_id?: string | null
          won_match_point?: boolean | null
        }
        Update: {
          eliminated?: boolean | null
          kills?: number | null
          match_point_eligible?: boolean | null
          placement_points?: number | null
          points?: number | null
          position?: number | null
          raw_json?: Json | null
          series_id?: string
          team_id?: string
          version_id?: string | null
          won_match_point?: boolean | null
        }
        Relationships: []
      }
      algs_series_weapon_stats: {
        Row: {
          ammo_type: string | null
          gun_type: string | null
          kills: number | null
          series_id: string
          weapon: string
        }
        Insert: {
          ammo_type?: string | null
          gun_type?: string | null
          kills?: number | null
          series_id: string
          weapon: string
        }
        Update: {
          ammo_type?: string | null
          gun_type?: string | null
          kills?: number | null
          series_id?: string
          weapon?: string
        }
        Relationships: []
      }
      algs_spawn_locations: {
        Row: {
          id: string
          in_game_drop_id: number | null
          map_id_ulid: string | null
          name: string | null
          x_norm: number | null
          y_norm: number | null
        }
        Insert: {
          id: string
          in_game_drop_id?: number | null
          map_id_ulid?: string | null
          name?: string | null
          x_norm?: number | null
          y_norm?: number | null
        }
        Update: {
          id?: string
          in_game_drop_id?: number | null
          map_id_ulid?: string | null
          name?: string | null
          x_norm?: number | null
          y_norm?: number | null
        }
        Relationships: []
      }
      algs_sync_state: {
        Row: {
          fetched_at: string | null
          ident: string
          kind: string
          status: string | null
        }
        Insert: {
          fetched_at?: string | null
          ident: string
          kind: string
          status?: string | null
        }
        Update: {
          fetched_at?: string | null
          ident?: string
          kind?: string
          status?: string | null
        }
        Relationships: []
      }
      algs_team_versions: {
        Row: {
          logo_dark: string | null
          logo_light: string | null
          team_id: string | null
          version_id: string
        }
        Insert: {
          logo_dark?: string | null
          logo_light?: string | null
          team_id?: string | null
          version_id: string
        }
        Update: {
          logo_dark?: string | null
          logo_light?: string | null
          team_id?: string | null
          version_id?: string
        }
        Relationships: []
      }
      algs_teams: {
        Row: {
          disbanded: boolean | null
          id: string
          name: string | null
          region: string | null
          short_name: string | null
        }
        Insert: {
          disbanded?: boolean | null
          id: string
          name?: string | null
          region?: string | null
          short_name?: string | null
        }
        Update: {
          disbanded?: boolean | null
          id?: string
          name?: string | null
          region?: string | null
          short_name?: string | null
        }
        Relationships: []
      }
      algs_tournaments: {
        Row: {
          end_date: string | null
          fetched_at: string
          id: string
          name: string | null
          season_id: string | null
          start_date: string | null
          vendor_id: string | null
        }
        Insert: {
          end_date?: string | null
          fetched_at?: string
          id: string
          name?: string | null
          season_id?: string | null
          start_date?: string | null
          vendor_id?: string | null
        }
        Update: {
          end_date?: string | null
          fetched_at?: string
          id?: string
          name?: string | null
          season_id?: string | null
          start_date?: string | null
          vendor_id?: string | null
        }
        Relationships: []
      }
      invites: {
        Row: {
          created_at: string
          created_by: string
          email: string | null
          expires_at: string
          id: string
          max_uses: number
          role: Database["public"]["Enums"]["app_role"]
          token: string
          used_at: string | null
          uses_count: number
        }
        Insert: {
          created_at?: string
          created_by: string
          email?: string | null
          expires_at?: string
          id?: string
          max_uses?: number
          role?: Database["public"]["Enums"]["app_role"]
          token: string
          used_at?: string | null
          uses_count?: number
        }
        Update: {
          created_at?: string
          created_by?: string
          email?: string | null
          expires_at?: string
          id?: string
          max_uses?: number
          role?: Database["public"]["Enums"]["app_role"]
          token?: string
          used_at?: string | null
          uses_count?: number
        }
        Relationships: []
      }
      lp_game_participants: {
        Row: {
          created_at: string
          game_id: string
          place: number | null
          team_id: string
        }
        Insert: {
          created_at?: string
          game_id: string
          place?: number | null
          team_id: string
        }
        Update: {
          created_at?: string
          game_id?: string
          place?: number | null
          team_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "lp_game_participants_game_id_fkey"
            columns: ["game_id"]
            isOneToOne: false
            referencedRelation: "lp_games"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "lp_game_participants_team_id_fkey"
            columns: ["team_id"]
            isOneToOne: false
            referencedRelation: "lp_teams"
            referencedColumns: ["id"]
          },
        ]
      }
      lp_games: {
        Row: {
          created_at: string
          game_no: number
          id: string
          label: string | null
          tournament_id: string
        }
        Insert: {
          created_at?: string
          game_no: number
          id?: string
          label?: string | null
          tournament_id: string
        }
        Update: {
          created_at?: string
          game_no?: number
          id?: string
          label?: string | null
          tournament_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "lp_games_tournament_id_fkey"
            columns: ["tournament_id"]
            isOneToOne: false
            referencedRelation: "lp_tournaments"
            referencedColumns: ["id"]
          },
        ]
      }
      lp_teams: {
        Row: {
          created_at: string
          id: string
          logo_url: string | null
          name: string
          scraped_at: string
          slug: string
          tag: string | null
        }
        Insert: {
          created_at?: string
          id?: string
          logo_url?: string | null
          name: string
          scraped_at?: string
          slug: string
          tag?: string | null
        }
        Update: {
          created_at?: string
          id?: string
          logo_url?: string | null
          name?: string
          scraped_at?: string
          slug?: string
          tag?: string | null
        }
        Relationships: []
      }
      lp_tournament_teams: {
        Row: {
          created_at: string
          place: number | null
          team_id: string
          tournament_id: string
        }
        Insert: {
          created_at?: string
          place?: number | null
          team_id: string
          tournament_id: string
        }
        Update: {
          created_at?: string
          place?: number | null
          team_id?: string
          tournament_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "lp_tournament_teams_team_id_fkey"
            columns: ["team_id"]
            isOneToOne: false
            referencedRelation: "lp_teams"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "lp_tournament_teams_tournament_id_fkey"
            columns: ["tournament_id"]
            isOneToOne: false
            referencedRelation: "lp_tournaments"
            referencedColumns: ["id"]
          },
        ]
      }
      lp_tournaments: {
        Row: {
          created_at: string
          dates_text: string | null
          end_date: string | null
          id: string
          location: string | null
          name: string
          scraped_at: string
          slug: string
          start_date: string | null
          tier: string | null
          url: string
        }
        Insert: {
          created_at?: string
          dates_text?: string | null
          end_date?: string | null
          id?: string
          location?: string | null
          name: string
          scraped_at?: string
          slug: string
          start_date?: string | null
          tier?: string | null
          url: string
        }
        Update: {
          created_at?: string
          dates_text?: string | null
          end_date?: string | null
          id?: string
          location?: string | null
          name?: string
          scraped_at?: string
          slug?: string
          start_date?: string | null
          tier?: string | null
          url?: string
        }
        Relationships: []
      }
      profiles: {
        Row: {
          created_at: string
          display_name: string | null
          email: string | null
          id: string
        }
        Insert: {
          created_at?: string
          display_name?: string | null
          email?: string | null
          id: string
        }
        Update: {
          created_at?: string
          display_name?: string | null
          email?: string | null
          id?: string
        }
        Relationships: []
      }
      user_roles: {
        Row: {
          created_at: string
          id: string
          role: Database["public"]["Enums"]["app_role"]
          user_id: string
        }
        Insert: {
          created_at?: string
          id?: string
          role: Database["public"]["Enums"]["app_role"]
          user_id: string
        }
        Update: {
          created_at?: string
          id?: string
          role?: Database["public"]["Enums"]["app_role"]
          user_id?: string
        }
        Relationships: []
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      has_role: {
        Args: {
          _role: Database["public"]["Enums"]["app_role"]
          _user_id: string
        }
        Returns: boolean
      }
    }
    Enums: {
      app_role: "user" | "operator" | "administrator"
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {
      app_role: ["user", "operator", "administrator"],
    },
  },
} as const
