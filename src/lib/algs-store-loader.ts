import { fetchAlgsBundle } from "@/lib/algs-fetchers";
import { replaceFromAlgs } from "@/lib/admin-store";
import { isSupabaseEnabled } from "@/lib/runtime-config";

let loadPromise: Promise<void> | null = null;

export function ensureAlgsStoreLoaded(label = "app"): Promise<void> {
  if (!isSupabaseEnabled) return Promise.resolve();
  if (!loadPromise) {
    loadPromise = fetchAlgsBundle()
      .then((bundle) => {
        replaceFromAlgs(bundle);
      })
      .catch((error) => {
        loadPromise = null;
        console.warn(`[${label}] ALGS bundle load failed`, error);
      });
  }
  return loadPromise;
}
