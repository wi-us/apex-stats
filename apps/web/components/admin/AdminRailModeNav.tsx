"use client";

import Link from "next/link";

export type AdminRailMode = "hsv" | "zones" | "poly" | "camera" | "database" | "management";

export function AdminRailModeNav({ active }: { active: AdminRailMode }) {
  return (
    <>
      <div className="rail-theme-label">Режим</div>
      <div className="mode-segment mode-segment--rail-nav" role="tablist" aria-label="Режим">
        <Link
          href="/admin/editor"
          className={`mode-btn${active === "hsv" ? " on" : ""}`}
          role="tab"
          aria-selected={active === "hsv"}
          prefetch={false}
        >
          HSV
        </Link>
        <Link
          href="/admin/editor?tab=zones"
          className={`mode-btn${active === "zones" ? " on" : ""}`}
          role="tab"
          aria-selected={active === "zones"}
          prefetch={false}
        >
          ZONES
        </Link>
        <Link
          href="/admin/editor?tab=poly"
          className={`mode-btn${active === "poly" ? " on" : ""}`}
          role="tab"
          aria-selected={active === "poly"}
          prefetch={false}
        >
          POLYGONS
        </Link>
        <Link
          href="/admin/camera"
          className={`mode-btn${active === "camera" ? " on" : ""}`}
          role="tab"
          aria-selected={active === "camera"}
          prefetch={false}
        >
          CAMERA
        </Link>
        <Link
          href="/admin/database"
          className={`mode-btn${active === "database" ? " on" : ""}`}
          role="tab"
          aria-selected={active === "database"}
          prefetch={false}
        >
          DATABASE
        </Link>
        <Link
          href="/admin/management"
          className={`mode-btn${active === "management" ? " on" : ""}`}
          role="tab"
          aria-selected={active === "management"}
          prefetch={false}
        >
          MANAGEMENT
        </Link>
      </div>
    </>
  );
}
