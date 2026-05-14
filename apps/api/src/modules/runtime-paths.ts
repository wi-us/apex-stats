import * as fs from "node:fs";
import * as path from "node:path";

interface RuntimePathsRaw {
  databases?: {
    tournaments?: string[];
    mapStartDetection?: string;
  };
  artifacts?: {
    jobsStore?: string;
    tracksDir?: string;
    tracksFile?: string;
    mapAdminSettings?: string;
    zonesDir?: string;
    textZonesDir?: string;
  };
  media?: {
    recordsDir?: string;
    mapsDir?: string;
  };
}

export interface RuntimePaths {
  databases: {
    tournaments: string[];
    mapStartDetection: string;
  };
  artifacts: {
    jobsStore: string;
    tracksDir: string;
    tracksFile: string;
    mapAdminSettings: string;
    zonesDir: string;
    textZonesDir: string;
  };
  media: {
    recordsDir: string;
    mapsDir: string;
  };
}

const DEFAULTS: RuntimePaths = {
  databases: {
    tournaments: ["output/tournaments.sqlite", "output/youtube_ingest/tournaments.sqlite"],
    mapStartDetection: "output/map_start_detection.sqlite",
  },
  artifacts: {
    jobsStore: "output/jobs.json",
    tracksDir: "output/tracks",
    tracksFile: "output/tracks.json",
    mapAdminSettings: "output/admin_map_settings.json",
    zonesDir: "output/zones",
    textZonesDir: "output/text_zones",
  },
  media: {
    recordsDir: "ffmpeg_downloader/records",
    mapsDir: "maps",
  },
};

function resolvePath(projectRoot: string, relOrAbs: string): string {
  return path.isAbsolute(relOrAbs) ? relOrAbs : path.join(projectRoot, relOrAbs);
}

export function loadRuntimePaths(projectRoot: string): RuntimePaths {
  const cfgPath = path.join(projectRoot, "config", "runtime_paths.json");
  let raw: RuntimePathsRaw = {};
  if (fs.existsSync(cfgPath)) {
    try {
      raw = JSON.parse(fs.readFileSync(cfgPath, "utf-8")) as RuntimePathsRaw;
    } catch {
      raw = {};
    }
  }

  const tournamentsRel = raw.databases?.tournaments?.length ? raw.databases.tournaments : DEFAULTS.databases.tournaments;
  const runtime: RuntimePaths = {
    databases: {
      tournaments: tournamentsRel.map((item) => resolvePath(projectRoot, item)),
      mapStartDetection: resolvePath(projectRoot, raw.databases?.mapStartDetection ?? DEFAULTS.databases.mapStartDetection),
    },
    artifacts: {
      jobsStore: resolvePath(projectRoot, raw.artifacts?.jobsStore ?? DEFAULTS.artifacts.jobsStore),
      tracksDir: resolvePath(projectRoot, raw.artifacts?.tracksDir ?? DEFAULTS.artifacts.tracksDir),
      tracksFile: resolvePath(projectRoot, raw.artifacts?.tracksFile ?? DEFAULTS.artifacts.tracksFile),
      mapAdminSettings: resolvePath(projectRoot, raw.artifacts?.mapAdminSettings ?? DEFAULTS.artifacts.mapAdminSettings),
      zonesDir: resolvePath(projectRoot, raw.artifacts?.zonesDir ?? DEFAULTS.artifacts.zonesDir),
      textZonesDir: resolvePath(projectRoot, raw.artifacts?.textZonesDir ?? DEFAULTS.artifacts.textZonesDir),
    },
    media: {
      recordsDir: resolvePath(projectRoot, raw.media?.recordsDir ?? DEFAULTS.media.recordsDir),
      mapsDir: resolvePath(projectRoot, raw.media?.mapsDir ?? DEFAULTS.media.mapsDir),
    },
  };
  return runtime;
}
