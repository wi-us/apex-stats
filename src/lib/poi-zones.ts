/**
 * POI zones: normalized (0..1) circles on canonical map images.
 *
 * Storage: JSON files at src/data/maps/<map_id>/poi_zones.json (one array
 * per map). Persistence in the admin editor is download-as-JSON for now;
 * a developer commits the file to the repo. A Lovable Cloud table can
 * replace this later without changing the runtime shape.
 */

import stormPointImg from "@/assets/maps/storm-point.webp";
import worldsEdgeImg from "@/assets/maps/worlds-edge.webp";
import eDistrictImg from "@/assets/maps/e-district.webp";
import brokenMoonImg from "@/assets/maps/broken-moon.webp";
import olympusImg from "@/assets/maps/olympus.webp";

import stormPointZones from "@/data/maps/storm_point/poi_zones.json";
import worldsEdgeZones from "@/data/maps/worlds_edge/poi_zones.json";
import eDistrictZones from "@/data/maps/e_district/poi_zones.json";
import brokenMoonZones from "@/data/maps/broken_moon/poi_zones.json";
import olympusZones from "@/data/maps/olympus/poi_zones.json";
import kingsCanyonZones from "@/data/maps/kings_canyon/poi_zones.json";

export type PoiZone = {
  /** Stable id used in datasets (slugified name). */
  id: string;
  /** Canonical display name, e.g. "Storm Catcher". */
  name: string;
  /** Alternate strings that should match this POI (e.g. "StormCatcher"). */
  aliases?: string[];
  /** Center X in [0..1] of the canonical map image. */
  cx: number;
  /** Center Y in [0..1] of the canonical map image. */
  cy: number;
  /** Radius in [0..1] of the canonical map image. */
  r: number;
  /** Optional ALGS in-game drop id (1..N) when sourced from the API. */
  inGameDropId?: number;
};

export type MapId =
  | "storm_point"
  | "worlds_edge"
  | "e_district"
  | "broken_moon"
  | "olympus"
  | "kings_canyon";

export const MAP_LABELS: Record<MapId, string> = {
  storm_point: "Storm Point",
  worlds_edge: "World's Edge",
  e_district: "E-District",
  broken_moon: "Broken Moon",
  olympus: "Olympus",
  kings_canyon: "Kings Canyon",
};

export const MAP_IDS: readonly MapId[] = [
  "storm_point",
  "worlds_edge",
  "e_district",
  "broken_moon",
  "olympus",
  "kings_canyon",
] as const;

const IMAGES: Record<MapId, string> = {
  storm_point: stormPointImg,
  worlds_edge: worldsEdgeImg,
  e_district: eDistrictImg,
  broken_moon: brokenMoonImg,
  olympus: olympusImg,
  kings_canyon: stormPointImg, // no asset yet — reuse SP placeholder
};

const SEED: Record<MapId, PoiZone[]> = {
  storm_point: stormPointZones as PoiZone[],
  worlds_edge: worldsEdgeZones as PoiZone[],
  e_district: eDistrictZones as PoiZone[],
  broken_moon: brokenMoonZones as PoiZone[],
  olympus: olympusZones as PoiZone[],
  kings_canyon: kingsCanyonZones as PoiZone[],
};

export function getMapImage(mapId: MapId): string {
  return IMAGES[mapId];
}

export function getSeedZones(mapId: MapId): PoiZone[] {
  return SEED[mapId].map((z) => ({ ...z, aliases: z.aliases ? [...z.aliases] : undefined }));
}

/** Normalize a free-form spot string for matching: lowercase + alphanum only. */
export function normalizeSpot(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

/** Find the POI zone whose name or alias matches `spot`. */
export function matchZone(zones: PoiZone[], spot: string | null | undefined): PoiZone | null {
  if (!spot) return null;
  const key = normalizeSpot(spot);
  for (const z of zones) {
    if (normalizeSpot(z.name) === key) return z;
    if (z.aliases?.some((a) => normalizeSpot(a) === key)) return z;
  }
  return null;
}

export function slugifyPoi(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "") || "poi";
}