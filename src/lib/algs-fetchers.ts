/**
 * Browser-side fetchers that read ALGS data from Supabase (algs_* tables,
 * public read RLS) and map them into the shapes used by the admin store
 * (Team / Tournament / MatchFull / CustomMap).
 *
 * Manual refresh only — call {@link fetchAlgsBundle} from a button/effect
 * and feed the result into {@link replaceFromAlgs} in admin-store.
 */
import { supabase } from "@/integrations/supabase/client";
import type {
  Team,
  Tournament,
  MatchFull,
  TournamentRegion,
  TournamentType,
  TournamentStatus,
} from "@/lib/mock-match";
import type { CustomMap } from "@/lib/admin-store";
import worldsEdgeImg from "@/assets/maps/worlds-edge.webp";
import kingsCanyonImg from "@/assets/maps/kings-canyon.webp";
import stormPointImg from "@/assets/maps/storm-point.webp";
import brokenMoonImg from "@/assets/maps/broken-moon.webp";
import olympusImg from "@/assets/maps/olympus.webp";
import eDistrictImg from "@/assets/maps/e-district.webp";

const MAP_IMAGE_BY_CANONICAL: Record<string, string> = {
  worlds_edge: worldsEdgeImg,
  kings_canyon: kingsCanyonImg,
  storm_point: stormPointImg,
  broken_moon: brokenMoonImg,
  olympus: olympusImg,
  e_district: eDistrictImg,
};

/** UI map id from canonical_id ("worlds_edge" → "worlds-edge"). */
function toUiMapId(canonical: string | null | undefined): string | null {
  return canonical ? canonical.replace(/_/g, "-") : null;
}

/** ALGS region name → UI TournamentRegion enum (best-effort). */
function toUiRegion(name: string | null | undefined): TournamentRegion {
  const n = (name ?? "").toLowerCase();
  if (n.includes("america") && n.includes("south")) return "South America";
  if (n.includes("america")) return "North America";
  if (n.includes("europe") || n.includes("emea")) return "EMEA";
  if (n.includes("pacific") || n.includes("apac") || n.includes("asia")) return "APAC";
  return "EMEA";
}

function toIsoDate(ts: string | null | undefined): string {
  if (!ts) return "";
  return ts.slice(0, 10);
}

function deriveType(name: string | null | undefined): TournamentType {
  const n = (name ?? "").toLowerCase();
  if (n.includes("qualifier") || n.includes("scrim")) return "Qualifier";
  if (n.includes("playoff") || n.includes("final") || n.includes("championship")) return "LAN";
  return "Online";
}

function deriveStatus(start: string, end: string, completed: string | null): TournamentStatus {
  if (completed) return "finished";
  if (!start || !end) return "draft";
  const today = new Date().toISOString().slice(0, 10);
  if (today < start) return "upcoming";
  if (today > end) return "finished";
  return "active";
}

export type AlgsBundle = {
  tournaments: Tournament[];
  teams: Team[];
  matches: MatchFull[];
  maps: CustomMap[];
  fetchedAt: number;
};

/** Pull everything we need in parallel. Each table is publicly readable. */
export async function fetchAlgsBundle(): Promise<AlgsBundle> {
  const [
    eventsRes,
    regionsRes,
    tournRes,
    teamsRes,
    teamVerRes,
    seriesRes,
    matchRowsRes,
    mapsRes,
    matchTeamsRes,
    eventTeamsRes,
  ] = await Promise.all([
    supabase.from("algs_events").select("id, name, start_date, end_date, tournament_id, region_id").order("start_date", { ascending: false }),
    supabase.from("algs_regions").select("id, name"),
    supabase.from("algs_tournaments").select("id, name"),
    supabase.from("algs_teams").select("id, name, short_name, region, disbanded"),
    supabase.from("algs_team_versions").select("version_id, team_id, logo_light, logo_dark").order("version_id", { ascending: true }),
    supabase.from("algs_series").select("id, name, status, starts_at, completed_at, event_id"),
    supabase.from("algs_matches").select("id, series_id, match_number, map_id_ulid, started_at, completed_at"),
    supabase.from("algs_maps").select("id_ulid, name, canonical_id, active"),
    supabase.from("algs_match_team_stats").select("match_id, team_id"),
    supabase.from("algs_event_teams").select("event_id, team_id"),
  ]);

  for (const r of [eventsRes, regionsRes, tournRes, teamsRes, teamVerRes, seriesRes, matchRowsRes, mapsRes, matchTeamsRes, eventTeamsRes]) {
    if (r.error) throw new Error(r.error.message);
  }

  const regionName = new Map((regionsRes.data ?? []).map((r) => [r.id, r.name as string]));
  const tournName = new Map((tournRes.data ?? []).map((t) => [t.id, t.name as string]));
  const mapById = new Map((mapsRes.data ?? []).map((m) => [m.id_ulid, m]));

  // teamIds per match (from match_team_stats)
  const teamsByMatch = new Map<string, string[]>();
  for (const r of matchTeamsRes.data ?? []) {
    if (!r.match_id || !r.team_id) continue;
    const arr = teamsByMatch.get(r.match_id) ?? [];
    if (!arr.includes(r.team_id)) arr.push(r.team_id);
    teamsByMatch.set(r.match_id, arr);
  }
  // teamIds per event (fallback for matches without per-match stats)
  const teamsByEvent = new Map<string, string[]>();
  for (const r of eventTeamsRes.data ?? []) {
    if (!r.event_id || !r.team_id) continue;
    const arr = teamsByEvent.get(r.event_id) ?? [];
    if (!arr.includes(r.team_id)) arr.push(r.team_id);
    teamsByEvent.set(r.event_id, arr);
  }

  // Tournaments ← ALGS events (one event = one UI tournament)
  const tournaments: Tournament[] = (eventsRes.data ?? []).map((ev) => {
    const start = toIsoDate(ev.start_date);
    const end = toIsoDate(ev.end_date);
    const parent = ev.tournament_id ? tournName.get(ev.tournament_id) : undefined;
    const reg = ev.region_id ? regionName.get(ev.region_id) : undefined;
    const fullName = parent ? `${parent} — ${ev.name ?? "Event"}` : (ev.name ?? "Event");
    const year = start ? Math.max(1, Math.min(6, new Date(start).getFullYear() - 2020)) : 6;
    return {
      id: ev.id,
      name: fullName,
      startDate: start,
      endDate: end,
      year,
      type: deriveType(fullName),
      region: toUiRegion(reg),
      status: deriveStatus(start, end, null),
      stage: undefined,
    };
  });

  // Teams ← ALGS teams + latest team_version logo
  // Ordered by version_id ASC (ULID → chronological). Always overwrite so the
  // LAST seen version wins → we end up with the latest known logo per team.
  const logoByTeam = new Map<string, { light?: string; dark?: string }>();
  for (const v of teamVerRes.data ?? []) {
    if (!v.team_id) continue;
    const prev = logoByTeam.get(v.team_id) ?? {};
    logoByTeam.set(v.team_id, {
      light: (v.logo_light || undefined) ?? prev.light,
      dark: (v.logo_dark || undefined) ?? prev.dark,
    });
  }
  const teams: Team[] = (teamsRes.data ?? []).map((t) => {
    const logos = logoByTeam.get(t.id) ?? {};
    const fallback = logos.dark || logos.light;
    return {
      id: t.id,
      tag: (t.short_name as string) || (t.name as string)?.slice(0, 4).toUpperCase() || "",
      name: (t.name as string) ?? "",
      color: "#888888",
      logo: fallback,
      logoLight: logos.light,
      logoDark: logos.dark,
      players: [],
      placement: 0,
      kills: 0,
      alive: true,
      status: t.disbanded ? "archived" : "active",
    };
  });

  // Maps ← active algs_maps
  const maps: CustomMap[] = (mapsRes.data ?? []).map((m) => ({
    id: m.id_ulid,
    name: (m.name as string) ?? "",
    image: (m.canonical_id && MAP_IMAGE_BY_CANONICAL[m.canonical_id]) || "",
  }));
  // Also register a UI-id alias (e.g. "worlds-edge") so seedMaps lookups
  // from the admin UI resolve to ALGS map images.
  for (const m of mapsRes.data ?? []) {
    const uiId = toUiMapId(m.canonical_id ?? null);
    if (!uiId) continue;
    maps.push({
      id: uiId,
      name: (m.name as string) ?? "",
      image: (m.canonical_id && MAP_IMAGE_BY_CANONICAL[m.canonical_id]) || "",
    });
  }

  // Matches ← ALGS series + games derived from algs_matches
  const matchesBySeries = new Map<string, typeof matchRowsRes.data>();
  for (const row of matchRowsRes.data ?? []) {
    if (!row.series_id) continue;
    const arr = matchesBySeries.get(row.series_id) ?? [];
    arr.push(row);
    matchesBySeries.set(row.series_id, arr);
  }

  const matches: MatchFull[] = (seriesRes.data ?? [])
    .filter((s) => s.event_id)
    .map((s) => {
      const games = (matchesBySeries.get(s.id) ?? [])
        .slice()
        .sort((a, b) => (a.match_number ?? 0) - (b.match_number ?? 0));
      const mapIds = games
        .map((g) => {
          const m = g.map_id_ulid ? mapById.get(g.map_id_ulid) : null;
          return toUiMapId(m?.canonical_id ?? null) ?? g.map_id_ulid ?? null;
        })
        .filter((x): x is string => !!x);
      const gameDurations = games.map((g) => {
        if (g.started_at && g.completed_at) {
          return Math.max(60, Math.round(
            (new Date(g.completed_at).getTime() - new Date(g.started_at).getTime()) / 1000,
          ));
        }
        return 1200;
      });
      const firstMap = mapIds[0] ?? "storm-point";
      const matchTeamIds = new Set<string>();
      for (const g of games) {
        for (const tid of teamsByMatch.get(g.id) ?? []) matchTeamIds.add(tid);
      }
      const eventTeamIds = teamsByEvent.get(s.event_id as string) ?? [];
      const teamIds = matchTeamIds.size > 0 ? Array.from(matchTeamIds) : eventTeamIds;
      return {
        id: s.id,
        name: (s.name as string) ?? "Series",
        tournamentId: s.event_id as string,
        mapId: firstMap,
        durationSec: gameDurations[0] ?? 1200,
        mapIds: mapIds.length > 0 ? mapIds : undefined,
        gameDurations: gameDurations.length > 0 ? gameDurations : undefined,
        teamIds,
        teamVods: {},
        vodLink: "",
      };
    });

  return { tournaments, teams, matches, maps, fetchedAt: Date.now() };
}