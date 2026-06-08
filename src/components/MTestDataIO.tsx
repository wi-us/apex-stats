import { useRef, useState } from "react";
import {
  MTEST_LS_KEYS,
} from "@/lib/test-game-data";
import tracksRaw from "@/data/m-test-g1/tracks.json";
import ringsRaw from "@/data/m-test-g1/rings.json";
import ringsV2Raw from "@/data/m-test-g1/ring_geometry_v2.json";
import elimRaw from "@/data/m-test-g1/eliminations.json";
import slotToTagRaw from "@/data/m-test-g1/slot-to-tag.json";

type Kind = "tracks" | "rings" | "ringsV2" | "eliminations" | "slotToTag";

const META: { key: Kind; label: string; filename: string; bundled: unknown }[] = [
  { key: "tracks",       label: "Tracks (перемещения)", filename: "tracks.json",            bundled: tracksRaw },
  { key: "rings",        label: "Rings phases (HUD)",   filename: "rings.json",             bundled: ringsRaw },
  { key: "ringsV2",      label: "Ring geometry v2",     filename: "ring_geometry_v2.json",  bundled: ringsV2Raw },
  { key: "eliminations", label: "HUD eliminations",     filename: "eliminations.json",      bundled: elimRaw },
  { key: "slotToTag",    label: "Slot → tag",           filename: "slot-to-tag.json",       bundled: slotToTagRaw },
];

function current(kind: Kind): unknown {
  const lsKey = MTEST_LS_KEYS[kind];
  if (typeof window !== "undefined") {
    const raw = window.localStorage.getItem(lsKey);
    if (raw) {
      try { return JSON.parse(raw); } catch { /* fall through */ }
    }
  }
  return META.find((m) => m.key === kind)!.bundled;
}

function download(name: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

export function MTestDataIO() {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<string>("");
  const inputs = useRef<Record<Kind, HTMLInputElement | null>>({
    tracks: null, rings: null, ringsV2: null, eliminations: null, slotToTag: null,
  });

  function onPick(kind: Kind, file: File | null) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result));
        window.localStorage.setItem(MTEST_LS_KEYS[kind], JSON.stringify(parsed));
        setStatus(`${kind}: импортировано. Перезагрузка…`);
        setTimeout(() => window.location.reload(), 400);
      } catch (e) {
        setStatus(`${kind}: ошибка парсинга JSON`);
      }
    };
    reader.readAsText(file);
  }

  function resetAll() {
    for (const m of META) window.localStorage.removeItem(MTEST_LS_KEYS[m.key]);
    setStatus("Сброшено к встроенным данным. Перезагрузка…");
    setTimeout(() => window.location.reload(), 400);
  }

  return (
    <div className="pointer-events-auto fixed bottom-3 right-3 z-50 font-mono text-[11px]">
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="rounded-md border border-border bg-background/90 px-3 py-1.5 shadow hover:bg-accent"
        >
          Data I/O
        </button>
      )}
      {open && (
        <div className="w-[320px] rounded-md border border-border bg-background/95 p-3 shadow-lg backdrop-blur">
          <div className="mb-2 flex items-center justify-between">
            <div className="font-bold">m-test-g1 · Data I/O</div>
            <button onClick={() => setOpen(false)} className="text-muted-foreground hover:text-foreground">×</button>
          </div>
          <div className="space-y-2">
            {META.map((m) => {
              const overridden = typeof window !== "undefined"
                && window.localStorage.getItem(MTEST_LS_KEYS[m.key]) != null;
              return (
                <div key={m.key} className="flex items-center justify-between gap-2 rounded border border-border/60 p-1.5">
                  <div className="min-w-0">
                    <div className="truncate">{m.label}{overridden && <span className="ml-1 text-primary">●</span>}</div>
                    <div className="truncate text-xs text-muted-foreground">{m.filename}</div>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <button
                      onClick={() => download(m.filename, current(m.key))}
                      className="rounded border border-border px-2 py-0.5 hover:bg-accent"
                    >↓</button>
                    <button
                      onClick={() => inputs.current[m.key]?.click()}
                      className="rounded border border-border px-2 py-0.5 hover:bg-accent"
                    >↑</button>
                    <input
                      ref={(el) => { inputs.current[m.key] = el; }}
                      type="file" accept="application/json,.json" className="hidden"
                      onChange={(e) => onPick(m.key, e.target.files?.[0] ?? null)}
                    />
                  </div>
                </div>
              );
            })}
          </div>
          <button
            onClick={resetAll}
            className="mt-2 w-full rounded border border-border px-2 py-1 text-muted-foreground hover:bg-accent"
          >Reset to bundled</button>
          {status && <div className="mt-2 text-xs text-primary">{status}</div>}
        </div>
      )}
    </div>
  );
}