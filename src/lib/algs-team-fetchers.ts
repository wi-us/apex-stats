/**
 * Per-team detail fetcher backed by ALGS Supabase tables.
 *
 * Pulled in one parallel batch from the team detail page via TanStack Query.
 * Every table is publicly readable (RLS: "Public read algs_*").
 */
import { supabase } from "@/integrations/supabase/client";

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
  mapCanonicalId: string | null;
  xNorm: number | null;
  yNorm: number | null;
  count: number;
  avgPickNumber: number;
};

export type TeamCurrentSeason = {
  seasonId: string;
  seasonName: string | null;
  totalPoints: number | null;
  isMain: boolean;
};

export type TeamRosterMember = {
  id: string;
  name: string;
  image: string | null;
  role: string;
  teamVersionId: string | null;
};

export type TeamWeaponStat = {
  weapon: string;
  gunType: string | null;
  ammoType: string | null;
  kills: number;
  series: number;
};

export type TeamDetail = {
  matches: TeamMatchStat[];
  players: TeamPlayer[];
  events: TeamEventResult[];
  seasons: TeamSeasonResult[];
  series: TeamSeriesResult[];
  phases: TeamPhaseResult[];
  poiPicks: TeamPoiPick[];
  currentSeason: TeamCurrentSeason | null;
  activeRoster: TeamRosterMember[];
  weapons: TeamWeaponStat[];
  /** Player ids that played the most recent match for this team. */
  lastMatchPlayerIds: string[];
  /** ISO timestamp of that most recent match, if known. */
  lastMatchAt: string | null;
};

/** Fetch everything we render on /admin/teams/$teamId in one parallel batch. */
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
    etRes,
  ] = await Promise.all([
    supabase.from("algs_match_team_stats")
      .select("match_id, kills, points, placement, placement_points, eliminated, match_point_eligible")
      .eq("team_id", teamId),
    supabase.from("algs_match_player_stats")
      .select("player_id, match_id, kills, knocked_down")
      .eq("team_id", teamId),
    supabase.from("algs_event_standings")
      .select("event_id, position, points, prize_money")
      .eq("team_id", teamId),
    supabase.from("algs_season_standings_teams")
      .select("season_id, total_points")
      .eq("team_id", teamId),
    supabase.from("algs_seasons").select("id, name, is_main, start_date"),
    supabase.from("algs_series_team_stats")
      .select("series_id, position, points, kills, won_match_point")
      .eq("team_id", teamId),
    supabase.from("algs_series").select("id, name, event_id, starts_at"),
    supabase.from("algs_phase_standings")
      .select("phase_id, position, points, match_wins, qualified, group_name")
      .eq("team_id", teamId),
    supabase.from("algs_phases").select("id, name, event_id"),
    supabase.from("algs_poi_picks")
      .select("spawn_location_id, map_id_ulid, pick_number")
      .eq("team_id", teamId),
    supabase.from("algs_maps").select("id_ulid, name, canonical_id"),
    supabase.from("algs_event_teams").select("event_id, raw_json").eq("team_id", teamId),
  ]);

  for (const r of [mtsRes, mpsRes, esRes, sstRes, seasonsRes, stsRes, seriesRes, psRes, phasesRes, poiRes, mapsRes, etRes]) {
    if (r.error) throw new Error(r.error.message);
  }

  // ---- Matches ----
  const matchIds = (mtsRes.data ?? []).map((r) => r.match_id).filter(Boolean);
  type MInfo = { series_id: string | null; event_id: string | null; tournament_id: string | null; map_id_ulid: string | null; started_at: string | null; completed_at: string | null };
  let matchInfo = new Map<string, MInfo>();
  if (matchIds.length > 0) {
    const chunks: string[][] = [];
    for (let i = 0; i < matchIds.length; i += 500) chunks.push(matchIds.slice(i, i + 500));
    const matchRows = (
      await Promise.all(
        chunks.map((c) =>
          supabase.from("algs_matches")
            .select("id, series_id, event_id, tournament_id, map_id_ulid, started_at, completed_at")
            .in("id", c),
        ),
      )
    ).flatMap((res) => {
      if (res.error) throw new Error(res.error.message);
      return res.data ?? [];
    });
    matchInfo = new Map(matchRows.map((m) => [m.id, m as MInfo]));
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

  // ---- Players (aggregated stats; kept for backwards compat) ----
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
    const playersRes = await supabase.from("algs_players").select("id, name, front_image").in("id", ids);
    if (playersRes.error) throw new Error(playersRes.error.message);
    const pById = new Map((playersRes.data ?? []).map((p) => [p.id, p]));
    players = ids.map((id) => {
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
    }).sort((a, b) => b.kills - a.kills);
  }

  // ---- Events meta (for participations + standings) ----
  const allEventIds = Array.from(new Set([
    ...(esRes.data ?? []).map((r) => r.event_id),
    ...(etRes.data ?? []).map((r) => r.event_id),
  ].filter(Boolean)));
  type EvMeta = { id: string; name: string | null; start_date: string | null; end_date: string | null };
  let eventMeta = new Map<string, EvMeta>();
  if (allEventIds.length > 0) {
    const evRes = await supabase.from("algs_events").select("id, name, start_date, end_date").in("id", allEventIds);
    if (evRes.error) throw new Error(evRes.error.message);
    eventMeta = new Map((evRes.data ?? []).map((e) => [e.id, e as EvMeta]));
  }
  const standingsByEvent = new Map((esRes.data ?? []).map((r) => [r.event_id, r]));

  // ---- Events: all participations, merged with standings if any ----
  const events: TeamEventResult[] = allEventIds.map((eid) => {
    const meta = eventMeta.get(eid);
    const st = standingsByEvent.get(eid);
    return {
      eventId: eid,
      eventName: (meta?.name as string) ?? eid,
      startDate: meta?.start_date ?? null,
      endDate: meta?.end_date ?? null,
      position: st?.position ?? null,
      points: st?.points ?? null,
      prizeMoney: (st?.prize_money as string) ?? null,
    };
  }).sort((a, b) => {
    const ad = a.startDate ? Date.parse(a.startDate) : 0;
    const bd = b.startDate ? Date.parse(b.startDate) : 0;
    return bd - ad;
  });

  // ---- Seasons ----
  const seasonName = new Map((seasonsRes.data ?? []).map((s) => [s.id, s.name as string | null]));
  const seasons: TeamSeasonResult[] = (sstRes.data ?? []).map((r) => ({
    seasonId: r.season_id,
    seasonName: seasonName.get(r.season_id) ?? null,
    totalPoints: r.total_points ?? null,
  }));

  // Current season = is_main (Year 6), with team's points if any
  const mainSeason = (seasonsRes.data ?? []).find((s) => s.is_main) ?? null;
  const sstById = new Map((sstRes.data ?? []).map((r) => [r.season_id, r]));
  const currentSeason: TeamCurrentSeason | null = mainSeason
    ? {
        seasonId: mainSeason.id,
        seasonName: (mainSeason.name as string) ?? null,
        totalPoints: sstById.get(mainSeason.id)?.total_points ?? null,
        isMain: true,
      }
    : null;

  // ---- Series ----
  const seriesMeta = new Map((seriesRes.data ?? []).map((s) => [s.id, s]));
  const series: TeamSeriesResult[] = (stsRes.data ?? []).map((r) => {
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
  }).sort((a, b) => {
    const at = a.startsAt ? Date.parse(a.startsAt) : 0;
    const bt = b.startsAt ? Date.parse(b.startsAt) : 0;
    return bt - at;
  });

  // ---- Phases (kept in detail; UI no longer renders) ----
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

  // ---- POI picks: aggregate, include map canonical + x/y for map rendering ----
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
    const spawnsRes = await supabase.from("algs_spawn_locations")
      .select("id, name, map_id_ulid, x_norm, y_norm")
      .in("id", ids);
    if (spawnsRes.error) throw new Error(spawnsRes.error.message);
    const sById = new Map((spawnsRes.data ?? []).map((s) => [s.id, s]));
    const mapById = new Map((mapsRes.data ?? []).map((m) => [m.id_ulid, m]));
    poiPicks = ids.map((id) => {
      const agg = poiAgg.get(id)!;
      const meta = sById.get(id);
      const mapId = (meta?.map_id_ulid as string) ?? agg.mapId;
      const mapMeta = mapId ? mapById.get(mapId) : null;
      return {
        spawnLocationId: id,
        spawnName: (meta?.name as string) ?? id,
        mapId,
        mapName: (mapMeta?.name as string) ?? null,
        mapCanonicalId: (mapMeta?.canonical_id as string) ?? null,
        xNorm: (meta?.x_norm as number | null) ?? null,
        yNorm: (meta?.y_norm as number | null) ?? null,
        count: agg.count,
        avgPickNumber: agg.sumPick / agg.count,
      };
    }).sort((a, b) => b.count - a.count);
  }

  // ---- Active roster: most recent event_teams version, role=player ----
  const sortedEt = (etRes.data ?? []).slice().sort((a, b) => {
    const ad = eventMeta.get(a.event_id)?.start_date ?? null;
    const bd = eventMeta.get(b.event_id)?.start_date ?? null;
    return (bd ? Date.parse(bd) : 0) - (ad ? Date.parse(ad) : 0);
  });
  let activeRoster: TeamRosterMember[] = [];
  for (const row of sortedEt) {
    type RawPlayer = { id: string; name: string; frontImage?: string | null; role: string; teamVersionId?: string };
    const raw = row.raw_json as { players?: RawPlayer[] } | null;
    const list = (raw?.players ?? []).filter((p) => p.role === "player");
    if (list.length > 0) {
      activeRoster = list.map((p) => ({
        id: p.id,
        name: p.name,
        image: p.frontImage ?? null,
        role: p.role,
        teamVersionId: p.teamVersionId ?? null,
      }));
      break;
    }
  }

  // ---- Weapon stats: aggregate over all series this team played -----------
  // NOTE: weapon stats are per-series globally (not per team), so this reflects
  // weapon meta in series this team participated in, not strictly the team's kills.
  const teamSeriesIds = Array.from(new Set((stsRes.data ?? []).map((r) => r.series_id).filter(Boolean)));
  let weapons: TeamWeaponStat[] = [];
  if (teamSeriesIds.length > 0) {
    const chunks: string[][] = [];
    for (let i = 0; i < teamSeriesIds.length; i += 500) chunks.push(teamSeriesIds.slice(i, i + 500));
    const wRows = (
      await Promise.all(
        chunks.map((c) =>
          supabase.from("algs_series_weapon_stats")
            .select("series_id, weapon, gun_type, ammo_type, kills")
            .in("series_id", c),
        ),
      )
    ).flatMap((res) => {
      if (res.error) throw new Error(res.error.message);
      return res.data ?? [];
    });
    const wAgg = new Map<string, { weapon: string; gunType: string | null; ammoType: string | null; kills: number; series: Set<string> }>();
    for (const r of wRows) {
      if (!r.weapon) continue;
      const cur = wAgg.get(r.weapon) ?? { weapon: r.weapon, gunType: (r.gun_type as string) ?? null, ammoType: (r.ammo_type as string) ?? null, kills: 0, series: new Set<string>() };
      cur.kills += r.kills ?? 0;
      if (r.series_id) cur.series.add(r.series_id);
      wAgg.set(r.weapon, cur);
    }
    weapons = Array.from(wAgg.values())
      .map((v) => ({ weapon: v.weapon, gunType: v.gunType, ammoType: v.ammoType, kills: v.kills, series: v.series.size }))
      .sort((a, b) => b.kills - a.kills);
  }

  return { matches, players, events, seasons, series, phases, poiPicks, currentSeason, activeRoster, weapons };
}