import { RingWindow } from "../types";

export async function detectFirstTwoRings(_fragmentVideoPath: string): Promise<RingWindow> {
  // Placeholder. Will be replaced with ring-state/event detection from HUD.
  return { ring1StartSec: 180, ring2StartSec: 420 };
}
