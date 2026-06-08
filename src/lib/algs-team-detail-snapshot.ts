import rosters from "@/data/algs-team-rosters.snapshot.json";
import weaponsSnapshot from "@/data/algs-team-weapons.snapshot.json";
import type {
  TeamDetail,
  TeamPlayer,
  TeamRosterMember,
  TeamWeaponStat,
} from "@/lib/algs-team-fetchers";

type WeaponSnapshot = {
  teams?: Record<string, TeamWeaponStat[]>;
  global?: TeamWeaponStat[];
};

function emptyDetail(activeRoster: TeamRosterMember[], weapons: TeamWeaponStat[]): TeamDetail {
  const players: TeamPlayer[] = activeRoster.map((p) => ({
    id: p.id,
    name: p.name,
    image: p.image,
    matchesPlayed: 0,
    kills: 0,
    knockedDown: 0,
  }));

  return {
    matches: [],
    players,
    events: [],
    seasons: [],
    series: [],
    phases: [],
    poiPicks: [],
    currentSeason: null,
    activeRoster,
    weapons,
    lastMatchPlayerIds: [],
    lastMatchAt: null,
  };
}

export function getTeamDetailSnapshot(teamId: string): TeamDetail {
  const activeRoster = ((rosters as Record<string, TeamRosterMember[]>)[teamId] ?? []);
  const snapshot = weaponsSnapshot as WeaponSnapshot;
  const weapons = snapshot.teams?.[teamId] ?? snapshot.global ?? [];
  return emptyDetail(activeRoster, weapons);
}
