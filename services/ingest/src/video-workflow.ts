import { WorkFragment } from "./types";
import { detectWorkFragment as detectWorkFragmentStage } from "./work-fragment-detector";
import { detectFirstTwoRings as detectFirstTwoRingsStage } from "./ring-detector";

export async function downloadVideo(vodUrl: string, outputPath: string): Promise<string> {
  if (!vodUrl) {
    throw new Error("VOD URL is missing");
  }
  // Placeholder: in production use ffmpeg/ytdlp pipeline.
  return outputPath;
}

export async function detectWorkFragment(sourceVideoPath: string): Promise<WorkFragment> {
  return detectWorkFragmentStage(sourceVideoPath);
}

export async function detectFirstTwoRings(_fragmentVideoPath: string): Promise<{
  ring1StartSec: number;
  ring2StartSec: number;
}> {
  return detectFirstTwoRingsStage(_fragmentVideoPath);
}
