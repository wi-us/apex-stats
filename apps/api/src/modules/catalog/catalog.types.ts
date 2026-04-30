export interface Tournament {
  id: string;
  name: string;
  season: string;
}

export interface Match {
  id: string;
  tournamentId: string;
  faceitMatchId: string;
  title: string;
  playedAt: string;
}

export interface MapEntry {
  id: string;
  matchId: string;
  mapName: string;
  videoUrl: string;
  backgroundUrl?: string;
  workFragmentStartSec: number;
  workFragmentEndSec: number;
  ring1StartSec: number;
  ring2StartSec: number;
}

export interface Team {
  id: string;
  name: string;
  colorBgr: [number, number, number];
}

export interface TeamTrackPoint {
  timestampSec: number;
  x: number;
  y: number;
  confidence: number;
}

export interface TeamTrack {
  mapId: string;
  teamId: string;
  points: TeamTrackPoint[];
  eliminated?: boolean;
  eliminationTimestampSec?: number;
  eliminationFrame?: number;
  eliminationConfidence?: number;
  eliminationMethod?: string;
}

export interface RingPoint {
  mapId: string;
  timestampSec: number;
  x: number;
  y: number;
  radius: number;
  segment: number;
  confidence: number;
}

export interface TeamHsvConfig {
  lower: [number, number, number];
  upper: [number, number, number];
}

export interface RingDetectorConfig {
  hsvLower: [number, number, number];
  hsvUpper: [number, number, number];
  grayMin: number;
  grayMax: number;
  morphK: number;
  blurK: number;
  houghP2: number;
  minRPct: number;
  maxRPct: number;
  sampleStepFrames: number;
}

export interface MapAdminConfig {
  mapId: string;
  mapName: string;
  basePresetFrom: string;
  runtime: {
    frameSkip: number;
    roundWindows: {
      round1: { startSec: number; endSec: number };
      round2: { startSec: number; endSec: number };
    };
  };
  teamHsv: Record<string, TeamHsvConfig>;
  polygons: {
    zonesFile: string;
    enabled: boolean;
  };
  ring: RingDetectorConfig;
}

export interface ZonePolygon {
  id: string;
  type: "forbidden" | "transient" | "trusted";
  polygon: number[][];
  max_dwell_sec?: number;
}

export interface ZonesPayload {
  map: string;
  image_path?: string;
  image_size: {
    width: number;
    height: number;
  };
  zones: ZonePolygon[];
}

export interface TextRectZone {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  label?: string;
  enabled?: boolean;
}

export interface TextZonesPayload {
  map: string;
  image_path?: string;
  image_size: {
    width: number;
    height: number;
  };
  zones: TextRectZone[];
}
