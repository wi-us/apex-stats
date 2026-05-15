export interface FaceitTeam {
  id: string;
  name: string;
}

export interface FaceitMatchMetadata {
  matchId: string;
  tournamentName: string;
  mapName: string;
  teams: FaceitTeam[];
  vodUrl: string;
}

export interface WorkFragment {
  sourceVideoPath: string;
  outputVideoPath: string;
  startSec: number;
  endSec: number;
}

export interface RingWindow {
  ring1StartSec: number;
  ring2StartSec: number;
}
