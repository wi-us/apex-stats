/**
 * Per-team detail fetcher backed by ALGS Supabase tables.
 *
 * Pulled in one parallel batch from the team detail page via TanStack Query.
 * Every table is publicly readable (RLS: "Public read algs_*").
 */
import { supabase } from "@/integrations/supabase/client";

/** A single ALGS "match" is one map drop. Placement is per match. */
export type TeamMatchStat = {
  matchId: string;
  seriesId: string | null;
  eventId: string | null;
  tournamentId: string | null;
  mapIdUlid: string | null;
  startedAt: string | null;
  completedAt: string | null;
  kills: number;
  points: number;
  placementPoints: number;
  placement: number;
  eliminated: boolean;
  matchPointEligible: boolean;
};

export type TeamPlayer = {
  id: string;
  name: string;
  image: string | null;
  matchesPlayed: number;
  kills: number;
  knockedDown: number;
};

export type TeamEventResult = {
  eventId: string;
  eventName: string;
  startDate: string | null;
  endDate: string | null;
  position: number | null;
  points: number | null;
  prizeMoney: string | null;
};

export type TeamSeasonResult = {
  seasonId: string;
  seasonName: string | null;
  totalPoints: number | null;
};

export type TeamSeriesResult = {
  seriesId: string;
  seriesName: string | null;
  eventId: string | null;
  startsAt: string | null;
  position: number | null;
  points: number | null;
  kills: number | null;
  wonMatchPoint: boolean | null;
};

export type TeamPhaseResult = {
  phaseId: string;
  phaseName: string | null;
  eventId: string | null;
  position: number | null;
  points: number | null;
  matchWins: number | null;
  qualified: boolean | null;
  groupName: string | null;
};

export type TeamPoiPick = {
  spawnLocationId: string;
  spawnName: string;
  mapId: string | null;
  mapName: string | null;
  count: number;
  avgPickNumber: number;
};

export type TeamDetail = {
  matches: TeamMatchStat[];
  players: TeamPlayer[];
  events: TeamEventResult[];
  seasons: TeamSeasonResult[];
  series: TeamSeriesResult[];
  phases: TeamPhaseResult[];
  poiPicks: TeamPoiPick[];
};

/**
 * Fetch everything we render on /admin/teams/$teamId in one parallel batch.
 * Returns empty arrays for teams that have no ALGS rows yet (e.g. mock seed
 * teams kept around when ALGS sync hasn't populated the store).
 */
export async function fetchTeamDetail(teamId: string): Promise<TeamDetail> {
  const [
    mtsRes,
    mpsRes,
    esRes,
    sstRes,
    seasonsRes,
    stsRes,
    seriesRes,
    psRes,
    phasesRes,
    poiRes,
    mapsRes,
  ] = await Promise.all([
    supabase
      .from("algs_match_team_stats")
      .select("match_id, kills, points, placement, placement_points, eliminated, match_point_eligible")
      .eq("team_id", teamId),
    supabase
      .from("algs_match_player_stats")
      .select("player_id, match_id, kills, knocked_down")
      .eq("team_id", teamId),
    supabase
      .from("algs_event_standings")
      .select("event_id, position, points, prize_money")
      .eq("team_id", teamId),
    supabase
      .from("algs_season_standings_teams")
      .select("season_id, total_points")
      .eq("team_id", teamId),
    supabase.from("algs_seasons").select("id, name"),
    supabase
      .from("algs_series_team_stats")
      .select("series_id, position, points, kills, won_match_point")
      .eq("team_id", teamId),
    supabase.from("algs_series").select("id, name, event_id, starts_at"),
    supabase
      .from("algs_phase_standings")
      .select("phase_id, position, points, match_wins, qualified, group_name")
      .eq("team_id", teamId),
    supabase.from("algs_phases").select("id, name, event_id"),
    supabase
      .from("algs_poi_picks")
      .select("spawn_location_id, map_id_ulid, pick_number")
      .eq("team_id", teamId),
    supabase.from("algs_maps").select("id_ulid, name"),
  ]);

  for (const r of [mtsRes, mpsRes, esRes, sstRes, seasonsRes, stsRes, seriesRes, psRes, phasesRes, poiRes, mapsRes]) {
    if (r.error) throw new Error(r.error.message);
  }

  // ---- Matches: join algs_match_team_stats with algs_matches for context ---
  const matchIds = (mtsRes.data ?? []).map((r) => r.match_id).filter(Boolean);
  let matchInfo: Map<string, {
    series_id: string | null;
    event_id: string | null;
    tournament_id: string | null;
    map_id_ulid: string | null;
    started_at: string | null;
    completed_at: string | null;
  }> = new Map();
  if (matchIds.length > 0) {
    // Supabase caps `.in()` at 1000; chunk just in case.
    const chunks: string[][] = [];
    for (let i = 0; i < matchIds.length; i += 500) chunks.push(matchIds.slice(i, i + 500));
    const matchRows = (
      await Promise.all(
        chunks.map((c) =>
          supabase
            .from("algs_matches")
            .select("id, series_id, event_id, tournament_id, map_id_ulid, started_at, completed_at")
            .in("id", c),
        ),
      )
    ).flatMap((res) => {
      if (res.error) throw new Error(res.error.message);
      return res.data ?? [];
    });
    matchInfo = new Map(matchRows.map((m) => [m.id, m]));
  }

  const matches: TeamMatchStat[] = (mtsRes.data ?? []).map((r) => {
    const info = matchInfo.get(r.match_id);
    return {
      matchId: r.match_id,
      seriesId: info?.series_id ?? null,
      eventId: info?.event_id ?? null,
      tournamentId: info?.tournament_id ?? null,
      mapIdUlid: info?.map_id_ulid ?? null,
      startedAt: info?.started_at ?? null,
      completedAt: info?.completed_at ?? null,
      kills: r.kills ?? 0,
      points: r.points ?? 0,
      placementPoints: r.placement_points ?? 0,
      placement: r.placement ?? 0,
      eliminated: !!r.eliminated,
      matchPointEligible: !!r.match_point_eligible,
    };
  });

  // ---- Players: aggregate per-match player stats + join with algs_players ---
  const playerAgg = new Map<string, { kills: number; knocked: number; matches: Set<string> }>();
  for (const row of mpsRes.data ?? []) {
    if (!row.player_id) continue;
    const cur = playerAgg.get(row.player_id) ?? { kills: 0, knocked: 0, matches: new Set<string>() };
    cur.kills += row.kills ?? 0;
    cur.knocked += row.knocked_down ?? 0;
    if (row.match_id) cur.matches.add(row.match_id);
    playerAgg.set(row.player_id, cur);
  }
  let players: TeamPlayer[] = [];
  if (playerAgg.size > 0) {
    const ids = Array.from(playerAgg.keys());
    const playersRes = await supabase
      .from("algs_players")
      .select("id, name, front_image")
      .in("id", ids);
    if (playersRes.error) throw new Error(playersRes.error.message);
    const pById = new Map((playersRes.data ?? []).map((p) => [p.id, p]));
    players = ids
      .map((id) => {
        const agg = playerAgg.get(id)!;
        const p = pById.get(id);
        return {
          id,
          name: (p?.name as string) ?? "Unknown",
          image: (p?.front_image as string) ?? null,
          matchesPlayed: agg.matches.size,
          kills: agg.kills,
          knockedDown: agg.knocked,
        };
      })
      .sort((a, b) => b.kills - a.kills);
  }

  // ---- Events: enrich with event name + dates ----
  const eventIds = (esRes.data ?? []).map((r) => r.event_id);
  let eventMeta = new Map<string, { name: string; start_date: string | null; end_date: string | null }>();
  if (eventIds.length > 0) {
    const eventsRes = await supabase
      .from("algs_events")
      .select("id, name, start_date, end_date")
      .in("id", eventIds);
    if (eventsRes.error) throw new Error(eventsRes.error.message);
    eventMeta = new Map((eventsRes.data ?? []).map((e) => [e.id, e]));
  }
  const events: TeamEventResult[] = (esRes.data ?? [])
    .map((r) => {
      const meta = eventMeta.get(r.event_id);
      return {
        eventId: r.event_id,
        eventName: (meta?.name as string) ?? r.event_id,
        startDate: meta?.start_date ?? null,
        endDate: meta?.end_date ?? null,
        position: r.position ?? null,
        points: r.points ?? null,
        prizeMoney: (r.prize_money as string) ?? null,
      };
    })
    .sort((a, b) => (a.position ?? 999) - (b.position ?? 999));

  // ---- Seasons ----
  const seasonName = new Map((seasonsRes.data ?? []).map((s) => [s.id, s.name as string | null]));
  const seasons: TeamSeasonResult[] = (sstRes.data ?? []).map((r) => ({
    seasonId: r.season_id,
    seasonName: seasonName.get(r.season_id) ?? null,
    totalPoints: r.total_points ?? null,
  }));

  // ---- Series ----
  const seriesMeta = new Map((seriesRes.data ?? []).map((s) => [s.id, s]));
  const series: TeamSeriesResult[] = (stsRes.data ?? [])
    .map((r) => {
      const meta = seriesMeta.get(r.series_id);
      return {
        seriesId: r.series_id,
        seriesName: (meta?.name as string) ?? null,
        eventId: (meta?.event_id as string) ?? null,
        startsAt: (meta?.starts_at as string) ?? null,
        position: r.position ?? null,
        points: r.points ?? null,
        kills: r.kills ?? null,
        wonMatchPoint: r.won_match_point ?? null,
      };
    })
    .sort((a, b) => {
      const at = a.startsAt ? Date.parse(a.startsAt) : 0;
      const bt = b.startsAt ? Date.parse(b.startsAt) : 0;
      return bt - at;
    });

  // ---- Phases ----
  const phaseMeta = new Map((phasesRes.data ?? []).map((p) => [p.id, p]));
  const phases: TeamPhaseResult[] = (psRes.data ?? []).map((r) => {
    const meta = phaseMeta.get(r.phase_id);
    return {
      phaseId: r.phase_id,
      phaseName: (meta?.name as string) ?? null,
      eventId: (meta?.event_id as string) ?? null,
      position: r.position ?? null,
      points: r.points ?? null,
      matchWins: r.match_wins ?? null,
      qualified: r.qualified ?? null,
      groupName: r.group_name ?? null,
    };
  });

  // ---- POI picks: aggregate by spawn_location_id ----
  const poiAgg = new Map<string, { count: number; sumPick: number; mapId: string | null }>();
  for (const row of poiRes.data ?? []) {
    if (!row.spawn_location_id) continue;
    const cur = poiAgg.get(row.spawn_location_id) ?? { count: 0, sumPick: 0, mapId: row.map_id_ulid };
    cur.count += 1;
    cur.sumPick += row.pick_number ?? 0;
    if (!cur.mapId && row.map_id_ulid) cur.mapId = row.map_id_ulid;
    poiAgg.set(row.spawn_location_id, cur);
  }
  let poiPicks: TeamPoiPick[] = [];
  if (poiAgg.size > 0) {
    const ids = Array.from(poiAgg.keys());
    const spawnsRes = await supabase
      .from("algs_spawn_locations")
      .select("id, name, map_id_ulid")
      .in("id", ids);
    if (spawnsRes.error) throw new Error(spawnsRes.error.message);
    const sById = new Map((spawnsRes.data ?? []).map((s) => [s.id, s]));
    const mapNameById = new Map((mapsRes.data ?? []).map((m) => [m.id_ulid, m.name as string]));
    poiPicks = ids
      .map((id) => {
        const agg = poiAgg.get(id)!;
        const meta = sById.get(id);
        const mapId = (meta?.map_id_ulid as string) ?? agg.mapId;
        return {
          spawnLocationId: id,
          spawnName: (meta?.name as string) ?? id,
          mapId,
          mapName: mapId ? mapNameById.get(mapId) ?? null : null,
          count: agg.count,
          avgPickNumber: agg.sumPick / agg.count,
        };
      })
      .sort((a, b) => b.count - a.count);
  }

  return { matches, players, events, seasons, series, phases, poiPicks };
}