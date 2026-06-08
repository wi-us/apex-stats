import manifest from "@/data/player-images.manifest.json";

const playerImages = manifest as Record<string, string>;

export function optimizedPlayerImage(src: string | null | undefined) {
  if (!src) return null;
  return playerImages[src] ?? src;
}
