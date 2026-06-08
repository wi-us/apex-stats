import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { Pencil, Eye, RotateCcw, Maximize2, Grid3x3 } from "lucide-react";
import { V2_SLIDES } from "@/components/presentation/v2/slides";
import { resetAllV2 } from "@/components/presentation/store-v2";

export const Route = createFileRoute("/presentation-2")({
  head: () => ({
    meta: [
      { title: "Apex Stats — Презентация · Издание" },
      { name: "description", content: "Светлая редакторская версия презентации платформы Apex Stats." },
    ],
  }),
  component: PresentationV2,
});

function PresentationV2() {
  const slides = V2_SLIDES;
  const [i, setI] = useState(0);
  const [overview, setOverview] = useState(false);
  const [editing, setEditing] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  const go = useCallback((n: number) => setI((c) => Math.max(0, Math.min(slides.length - 1, n))), [slides.length]);
  const next = useCallback(() => go(i + 1), [i, go]);
  const prev = useCallback(() => go(i - 1), [i, go]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      const isField = (e.target as HTMLElement | null)?.isContentEditable || tag === "INPUT" || tag === "TEXTAREA";
      if (isField) return;
      if (e.key === "ArrowRight" || e.key === " " || e.key === "PageDown") { e.preventDefault(); next(); }
      else if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); prev(); }
      else if (e.key === "Home") setI(0);
      else if (e.key === "End") setI(slides.length - 1);
      else if (e.key.toLowerCase() === "g") setOverview((o) => !o);
      else if (e.key.toLowerCase() === "e") setEditing((v) => !v);
      else if (e.key.toLowerCase() === "f") {
        const el = wrapRef.current;
        if (!document.fullscreenElement) el?.requestFullscreen?.();
        else document.exitFullscreen?.();
      } else if (e.key === "Escape") setOverview(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next, prev, slides.length]);

  const cur = slides[i];
  const Cur = cur.Component;

  return (
    <div ref={wrapRef} className="relative flex h-screen w-full flex-col overflow-hidden" style={{ background: "#1a1714", color: "#f5f1e8" }}>
      <header className="flex h-12 shrink-0 items-center gap-3 border-b px-4" style={{ borderColor: "#ffffff15", background: "#15110e" }}>
        <Link to="/" className="text-[11px] font-bold uppercase tracking-wider opacity-70 hover:opacity-100">← Home</Link>
        <Link to="/presentation" className="text-[11px] font-bold uppercase tracking-wider opacity-70 hover:opacity-100">Версия HUD →</Link>
        <div className="text-[11px] font-bold uppercase tracking-wider">Apex Stats · Издание</div>
        <div className="ml-2 text-xs font-mono opacity-60">{i + 1} / {slides.length}</div>
        <div className="ml-auto flex items-center gap-2 text-xs uppercase tracking-wider opacity-80">
          <button onClick={() => setEditing((v) => !v)}
            className={"flex items-center gap-1.5 rounded-sm border px-2 py-1 transition " + (editing ? "border-[#d4541c] bg-[#d4541c]/20 text-[#d4541c]" : "border-white/15 bg-white/5 hover:bg-white/10")}
            title="Toggle edit (E)">
            {editing ? <Eye className="h-3 w-3" /> : <Pencil className="h-3 w-3" />}
            {editing ? "Editing" : "Edit (E)"}
          </button>
          <button onClick={() => { if (confirm("Сбросить все правки слайдов?")) resetAllV2(); }}
            className="flex items-center gap-1.5 rounded-sm border border-white/15 bg-white/5 px-2 py-1 hover:bg-white/10">
            <RotateCcw className="h-3 w-3" /> Reset
          </button>
          <button onClick={() => setOverview((o) => !o)}
            className="flex items-center gap-1.5 rounded-sm border border-white/15 bg-white/5 px-2 py-1 hover:bg-white/10">
            <Grid3x3 className="h-3 w-3" />{overview ? "Slide" : "Grid (G)"}
          </button>
          <button onClick={() => {
            const el = wrapRef.current;
            if (!document.fullscreenElement) el?.requestFullscreen?.();
            else document.exitFullscreen?.();
          }} className="flex items-center gap-1.5 rounded-sm border border-white/15 bg-white/5 px-2 py-1 hover:bg-white/10">
            <Maximize2 className="h-3 w-3" /> Fullscreen (F)
          </button>
        </div>
      </header>

      {!overview ? (
        <>
          <div className="relative flex min-h-0 flex-1 items-center justify-center px-6 py-6" style={{ background: "#0d0a08" }}>
            <div className="relative h-full w-full max-h-full max-w-full">
              <Cur editing={editing} />
            </div>
            <button onClick={prev} disabled={i === 0}
              className="absolute left-4 top-1/2 z-20 -translate-y-1/2 rounded-full border border-white/15 bg-white/10 px-3 py-2 text-sm backdrop-blur transition hover:bg-white/20 disabled:opacity-30">‹</button>
            <button onClick={next} disabled={i === slides.length - 1}
              className="absolute right-4 top-1/2 z-20 -translate-y-1/2 rounded-full border border-white/15 bg-white/10 px-3 py-2 text-sm backdrop-blur transition hover:bg-white/20 disabled:opacity-30">›</button>
            <div className="pointer-events-none absolute bottom-4 left-1/2 z-20 -translate-x-1/2 rounded-md border border-white/15 bg-black/60 px-4 py-2 text-center backdrop-blur">
              <div className="text-[11px] font-bold uppercase tracking-wider">{cur.title}</div>
              <div className="text-xs opacity-70">{cur.subtitle}</div>
            </div>
          </div>
          <footer className="flex h-24 shrink-0 items-center gap-2 overflow-x-auto border-t border-white/10 px-3 py-2" style={{ background: "#15110e" }}>
            {slides.map((sl, idx) => (
              <button key={idx} onClick={() => setI(idx)}
                className={`relative h-full shrink-0 overflow-hidden rounded-sm border transition ${idx === i ? "border-[#d4541c] ring-1 ring-[#d4541c]/40" : "border-white/15 hover:border-white/40"}`}
                style={{ aspectRatio: "16 / 9" }}>
                <div className="pointer-events-none h-full w-full">
                  <sl.Component editing={false} />
                </div>
                <span className="absolute left-1 top-1 rounded-sm bg-black/60 px-1 text-[9px] font-bold text-white">{idx + 1}</span>
              </button>
            ))}
          </footer>
        </>
      ) : (
        <div className="grid flex-1 auto-rows-min grid-cols-1 gap-4 overflow-y-auto p-6 sm:grid-cols-2 lg:grid-cols-3" style={{ background: "#0d0a08" }}>
          {slides.map((sl, idx) => (
            <button key={idx} onClick={() => { setI(idx); setOverview(false); }}
              className={`group overflow-hidden rounded-md border text-left transition hover:-translate-y-0.5 hover:shadow-xl ${idx === i ? "border-[#d4541c]" : "border-white/15"}`}
              style={{ background: "#15110e" }}>
              <div className="relative" style={{ aspectRatio: "16 / 9", background: "#0d0a08" }}>
                <div className="pointer-events-none absolute inset-0">
                  <sl.Component editing={false} />
                </div>
                <span className="absolute left-2 top-2 rounded-sm bg-black/70 px-1.5 py-0.5 text-xs font-bold text-white">{idx + 1}</span>
              </div>
              <div className="border-t border-white/10 p-3">
                <div className="text-[11px] font-bold uppercase tracking-wider">{sl.title}</div>
                <div className="mt-0.5 text-xs opacity-70">{sl.subtitle}</div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}