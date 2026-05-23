
-- Tournaments scraped from Liquipedia
CREATE TABLE public.lp_tournaments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text NOT NULL UNIQUE,
  url text NOT NULL,
  name text NOT NULL,
  dates_text text,
  start_date date,
  end_date date,
  location text,
  tier text,
  scraped_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.lp_teams (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text NOT NULL UNIQUE,
  name text NOT NULL,
  tag text,
  logo_url text,
  scraped_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.lp_tournament_teams (
  tournament_id uuid NOT NULL REFERENCES public.lp_tournaments(id) ON DELETE CASCADE,
  team_id uuid NOT NULL REFERENCES public.lp_teams(id) ON DELETE CASCADE,
  place int,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tournament_id, team_id)
);

CREATE TABLE public.lp_games (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tournament_id uuid NOT NULL REFERENCES public.lp_tournaments(id) ON DELETE CASCADE,
  game_no int NOT NULL,
  label text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tournament_id, game_no)
);

CREATE TABLE public.lp_game_participants (
  game_id uuid NOT NULL REFERENCES public.lp_games(id) ON DELETE CASCADE,
  team_id uuid NOT NULL REFERENCES public.lp_teams(id) ON DELETE CASCADE,
  place int,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (game_id, team_id)
);

CREATE INDEX idx_lp_tournament_teams_team ON public.lp_tournament_teams(team_id);
CREATE INDEX idx_lp_game_participants_team ON public.lp_game_participants(team_id);
CREATE INDEX idx_lp_games_tournament ON public.lp_games(tournament_id);

-- RLS
ALTER TABLE public.lp_tournaments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lp_teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lp_tournament_teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lp_games ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lp_game_participants ENABLE ROW LEVEL SECURITY;

-- Read: any authenticated user
CREATE POLICY "auth read lp_tournaments" ON public.lp_tournaments FOR SELECT TO authenticated USING (true);
CREATE POLICY "auth read lp_teams" ON public.lp_teams FOR SELECT TO authenticated USING (true);
CREATE POLICY "auth read lp_tournament_teams" ON public.lp_tournament_teams FOR SELECT TO authenticated USING (true);
CREATE POLICY "auth read lp_games" ON public.lp_games FOR SELECT TO authenticated USING (true);
CREATE POLICY "auth read lp_game_participants" ON public.lp_game_participants FOR SELECT TO authenticated USING (true);

-- Write: administrators only
CREATE POLICY "admin write lp_tournaments" ON public.lp_tournaments FOR ALL TO authenticated
  USING (has_role(auth.uid(), 'administrator'::app_role))
  WITH CHECK (has_role(auth.uid(), 'administrator'::app_role));
CREATE POLICY "admin write lp_teams" ON public.lp_teams FOR ALL TO authenticated
  USING (has_role(auth.uid(), 'administrator'::app_role))
  WITH CHECK (has_role(auth.uid(), 'administrator'::app_role));
CREATE POLICY "admin write lp_tournament_teams" ON public.lp_tournament_teams FOR ALL TO authenticated
  USING (has_role(auth.uid(), 'administrator'::app_role))
  WITH CHECK (has_role(auth.uid(), 'administrator'::app_role));
CREATE POLICY "admin write lp_games" ON public.lp_games FOR ALL TO authenticated
  USING (has_role(auth.uid(), 'administrator'::app_role))
  WITH CHECK (has_role(auth.uid(), 'administrator'::app_role));
CREATE POLICY "admin write lp_game_participants" ON public.lp_game_participants FOR ALL TO authenticated
  USING (has_role(auth.uid(), 'administrator'::app_role))
  WITH CHECK (has_role(auth.uid(), 'administrator'::app_role));
