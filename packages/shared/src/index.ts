export interface IngestRequest {
  faceitMatchId: string;
}

export interface AnalyzeRequest {
  mapId: string;
  mapName: string;
  videoPath: string;
}

export interface TrackPointDto {
  timestampSec: number;
  x: number;
  y: number;
  confidence: number;
}
