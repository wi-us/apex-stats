import { useEffect, useRef, type ReactNode } from "react";
import { setLayout, useBox, registerBoxDefault, type BoxLayout } from "../store";
import { useSlideScale } from "../SlideCanvas";
import { ColorButton, useColor } from "../ColorButton";

type Props = {
  id: string;
  defaultBox: BoxLayout;
  editing: boolean;
  children: ReactNode;
  minW?: number;
  minH?: number;
  /** When true, the whole body is the drag handle. Otherwise, only the top bar. */
  dragAnywhere?: boolean;
};

/**
 * Absolutely-positioned draggable + resizable wrapper for slide blocks.
 * Coordinates are in the slide's design space (1920×1080), persisted in the store.
 */
export function Movable({
  id, defaultBox, editing, children, minW = 80, minH = 60, dragAnywhere = false,
}: Props) {
  const box = useBox(id, defaultBox);
  const color = useColor(id, "var(--primary)");
  useEffect(() => { registerBoxDefault(id, defaultBox); }, [id]);
  const scale = useSlideScale();
  const startRef = useRef<{ box: BoxLayout; px: number; py: number; mode: string } | null>(null);

  const onPointerDown = (mode: string) => (e: React.PointerEvent) => {
    if (!editing) return;
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    startRef.current = { box, px: e.clientX, py: e.clientY, mode };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const s = startRef.current;
    if (!s || !editing) return;
    const dx = (e.clientX - s.px) / scale;
    const dy = (e.clientY - s.py) / scale;
    let { x, y, w, h } = s.box;
    if (s.mode === "move") {
      x += dx; y += dy;
    } else {
      if (s.mode.includes("e")) w = Math.max(minW, s.box.w + dx);
      if (s.mode.includes("s")) h = Math.max(minH, s.box.h + dy);
      if (s.mode.includes("w")) { w = Math.max(minW, s.box.w - dx); x = s.box.x + (s.box.w - w); }
      if (s.mode.includes("n")) { h = Math.max(minH, s.box.h - dy); y = s.box.y + (s.box.h - h); }
    }
    setLayout(id, { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) });
  };
  const onPointerUp = (e: React.PointerEvent) => {
    startRef.current = null;
    try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); } catch {}
  };

  const handleCls = "absolute z-30 bg-primary/80 rounded-sm shadow ring-1 ring-background";
  const handleSize = 12;

  return (
    <div
      className="absolute"
      style={{ left: box.x, top: box.y, width: box.w, height: box.h }}
    >
      {/* Color outline overlay */}
      <div
        className="pointer-events-none absolute inset-0 rounded-xl"
        style={{ boxShadow: `inset 0 0 0 2px ${color}` }}
      />
      {/* Drag layer */}
      {editing && dragAnywhere && (
        <div
          className="absolute inset-0 z-20 cursor-move"
          onPointerDown={onPointerDown("move")}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
        />
      )}
      {editing && !dragAnywhere && (
        <div className="absolute left-0 right-0 z-20 flex h-6 items-center gap-1 px-1" style={{ top: -24 }}>
          <div
            className="flex h-6 flex-1 cursor-move items-center justify-center rounded-t bg-primary/80 text-xs font-bold tracking-wider text-primary-foreground shadow"
            onPointerDown={onPointerDown("move")}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            title="Перетащить"
          >
            ⋮⋮  DRAG
          </div>
          <ColorButton id={id} fallback="var(--primary)" />
        </div>
      )}
      <div className="absolute inset-0">{children}</div>
      {/* Resize handles */}
      {editing && (
        <>
          {[
            { m: "nw", style: { left: -handleSize/2, top: -handleSize/2, cursor: "nwse-resize" } },
            { m: "ne", style: { right: -handleSize/2, top: -handleSize/2, cursor: "nesw-resize" } },
            { m: "sw", style: { left: -handleSize/2, bottom: -handleSize/2, cursor: "nesw-resize" } },
            { m: "se", style: { right: -handleSize/2, bottom: -handleSize/2, cursor: "nwse-resize" } },
            { m: "n",  style: { left: "50%", top: -handleSize/2, marginLeft: -handleSize/2, cursor: "ns-resize" } },
            { m: "s",  style: { left: "50%", bottom: -handleSize/2, marginLeft: -handleSize/2, cursor: "ns-resize" } },
            { m: "w",  style: { top: "50%", left: -handleSize/2, marginTop: -handleSize/2, cursor: "ew-resize" } },
            { m: "e",  style: { top: "50%", right: -handleSize/2, marginTop: -handleSize/2, cursor: "ew-resize" } },
          ].map((h) => (
            <div
              key={h.m}
              className={handleCls}
              style={{ width: handleSize, height: handleSize, ...h.style }}
              onPointerDown={onPointerDown(h.m)}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
            />
          ))}
        </>
      )}
    </div>
  );
}