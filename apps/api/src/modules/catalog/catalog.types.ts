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

export interface ObserverRoi {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface MapEntry {
  id: string;
  matchId: string;
  mapName: string;
  displayName?: string;
  videoUrl: string;
  backgroundUrl?: string;
  workFragmentStartSec: number;
  workFragmentEndSec: number;
  ring1StartSec: number;
  ring2StartSec: number;
  observerRoi?: ObserverRoi;
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
  mapX?: number | null;
  mapY?: number | null;
  sourceFrameX?: number;
  sourceFrameY?: number;
  mapSpaceValid?: boolean;
  backupFrameSpace?: boolean;
  transformState?: "tracked" | "relocalized" | "degraded" | string;
  transformResidual?: number;
  bgConfidence?: number;
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
  timeStartSec?: number;
  timeEndSec?: number;
  ringNumber?: number;
  ringEventType?: "closing" | "countdown";
  countdownRingNumber?: number;
  ringNumberSource?: string;
  timingSource?: string;
  x: number;
  y: number;
  radius: number;
  segment: number;
  confidence: number;
  coordinateSpace?: "map" | "frame";
}

export interface CameraTrackPoint {
  mapId: string;
  timestampSec: number;
  ringStatus: "closing" | "countdown";
  ringNumber: number;
  centerX: number;
  centerY: number;
  cameraX: number;
  cameraY: number;
  radius: number;
  zoomRatio: number;
  cameraSize: number;
  roiX1: number;
  roiY1: number;
  roiX2: number;
  roiY2: number;
  jumpScore: number;
  jumpFlag: boolean;
  x1?: number;
  x2?: number;
  y1?: number;
  y2?: number;
  moveDx?: number;
  moveDy?: number;
  moveDist?: number;
  moveSide?: string;
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
  type: "forbidden" | "transient" | "trusted" | "respawn";
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
