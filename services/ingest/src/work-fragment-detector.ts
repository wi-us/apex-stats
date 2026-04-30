import { WorkFragment } from "./types";

export async function detectWorkFragment(sourceVideoPath: string): Promise<WorkFragment> {
  // Placeholder heuristic. Will be replaced with CV-based phase detection.
  return {
    sourceVideoPath,
    outputVideoPath: sourceVideoPath.replace(".mp4", ".work.mp4"),
    startSec: 0,
    endSec: 1200
  };
}
