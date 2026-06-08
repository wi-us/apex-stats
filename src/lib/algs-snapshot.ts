import snapshot from "@/data/algs-bundle.snapshot.json";
import type { AlgsBundle } from "@/lib/algs-fetchers";
import worldsEdgeImg from "@/assets/maps/worlds-edge.webp";
import kingsCanyonImg from "@/assets/maps/kings-canyon.webp";
import stormPointImg from "@/assets/maps/storm-point.webp";
import brokenMoonImg from "@/assets/maps/broken-moon.webp";
import olympusImg from "@/assets/maps/olympus.webp";
import eDistrictImg from "@/assets/maps/e-district.webp";

type SnapshotMap = { id: string; name: string; imageKey?: string | null };

const imageByKey: Record<string, string> = {
  "worlds-edge": worldsEdgeImg,
  "kings-canyon": kingsCanyonImg,
  "storm-point": stormPointImg,
  "broken-moon": brokenMoonImg,
  olympus: olympusImg,
  "e-district": eDistrictImg,
};

export function getAlgsSnapshotBundle(): AlgsBundle {
  const raw = snapshot as Omit<AlgsBundle, "maps"> & { maps: SnapshotMap[] };
  return {
    ...raw,
    maps: raw.maps.map((map) => ({
      id: map.id,
      name: map.name,
      image: map.imageKey ? imageByKey[map.imageKey] ?? "" : "",
    })),
  };
}
