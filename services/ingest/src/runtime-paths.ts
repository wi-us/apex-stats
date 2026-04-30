import fs from "node:fs";
import path from "node:path";

interface RuntimePathsRaw {
  artifacts?: {
    jobsStore?: string;
  };
}

function projectRootFromCwd(cwd: string): string {
  // service runs from repo root in normal npm workspace flow.
  return cwd;
}

export function loadJobsStorePath(cwd: string): string {
  const projectRoot = projectRootFromCwd(cwd);
  const cfgPath = path.resolve(projectRoot, "config", "runtime_paths.json");
  if (fs.existsSync(cfgPath)) {
    try {
      const payload = JSON.parse(fs.readFileSync(cfgPath, "utf-8")) as RuntimePathsRaw;
      const candidate = String(payload.artifacts?.jobsStore ?? "").trim();
      if (candidate) return path.resolve(projectRoot, candidate);
    } catch {
      // fallback below
    }
  }
  return path.resolve(projectRoot, "output", "jobs.json");
}
