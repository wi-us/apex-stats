import { maps as seedMaps, type ApexMap, type MatchFull } from "@/lib/mock-match";
import type { CustomMap } from "@/lib/admin-store";

export const TEST_GAME_ID = "m-test-g1";

function mapKey(map: Pick<ApexMap, "id" | "name">): string {
  const seed = seedMaps.find((item) => item.id === map.id);
  const name = seed?.name ?? map.name ?? map.id;
  return name
    .toLowerCase()
    .replace(/['’]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

export function isTestMatchId(matchId: string): boolean {
  return matchId === "m-test" || matchId.startsWith("m-test");
}

export function publicMatches<T extends { id: string }>(matches: T[]): T[] {
  return matches.filter((match) => !isTestMatchId(match.id));
}

export function publicMapRows(customMaps: CustomMap[]): ApexMap[] {
  const byKey = new Map<string, ApexMap>();
  for (const map of seedMaps) byKey.set(mapKey(map), map);
  for (const map of customMaps) {
    const key = mapKey(map);
    const prev = byKey.get(key);
    byKey.set(key, {
      id: prev?.id ?? map.id,
      name: map.name,
      image: map.image || prev?.image || "",
    });
  }
  return Array.from(byKey.values());
}

export function findPublicMap(mapId: string, customMaps: CustomMap[]): ApexMap | undefined {
  const rows = publicMapRows(customMaps);
  const direct = rows.find((map) => map.id === mapId);
  if (direct) return direct;
  const custom = customMaps.find((map) => map.id === mapId);
  if (!custom) return undefined;
  return rows.find((map) => mapKey(map) === mapKey(custom));
}

export function publicGameCount(matches: MatchFull[]): number {
  return publicMatches(matches).reduce((sum, match) => {
    const count = match.mapIds?.length || 1;
    return sum + count;
  }, 0);
}
