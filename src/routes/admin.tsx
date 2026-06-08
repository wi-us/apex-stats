import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useState } from "react";
import { RouteGuard } from "@/components/auth/RouteGuard";
import { useAuth } from "@/lib/auth";

export const Route = createFileRoute("/admin")({
  component: () => (
    <RouteGuard min="operator">
      <AdminLayout />
    </RouteGuard>
  ),
});

type NavItem = { to: string; label: string; hint: string; exact?: boolean };

const dataItems: NavItem[] = [
  { to: "/admin/tournaments", label: "Tournaments", hint: "Series & events" },
  { to: "/admin/matches",     label: "Matches",     hint: "Games per tournament" },
  { to: "/admin/maps",        label: "Maps",        hint: "Map pool" },
  { to: "/admin/teams",       label: "Teams",       hint: "Rosters & colors" },
];

const calibrationItems: NavItem[] = [
  { to: "/admin/hsv",      label: "HSV",          hint: "Team color calibration" },
  { to: "/admin/zones",    label: "HUD Zones",    hint: "HUD areas (1920×1080)" },
  { to: "/admin/polygons", label: "Map Polygons", hint: "Forbidden / safe areas" },
  { to: "/admin/poi",      label: "POI Zones",    hint: "Drop points on canonical maps" },
];

const analysisItems: NavItem[] = [
  { to: "/admin/processes", label: "Processes",       hint: "Analysis & tracking" },
  { to: "/admin/minimap",   label: "Minimap Locator", hint: "Minimap detection" },
  { to: "/admin/tracking-lab", label: "Tracking Lab", hint: "VOD tracks.json viewer" },
  { to: "/admin/dataset",   label: "Dataset Builder", hint: "YOLO labels from ZIP" },
];

function AdminLayout() {
  const { role } = useAuth();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const dataActive = dataItems.some((i) => pathname.startsWith(i.to));
  const calibActive = calibrationItems.some((i) => pathname.startsWith(i.to));
  const analysisActive = analysisItems.some((i) => pathname.startsWith(i.to));
  const [openData, setOpenData] = useState<boolean>(true);
  const [openCalib, setOpenCalib] = useState<boolean>(true);
  const [openAnalysis, setOpenAnalysis] = useState<boolean>(true);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
      <aside className="flex w-[240px] shrink-0 flex-col border-r border-border bg-surface">
        <Link to="/" className="flex h-14 shrink-0 items-center gap-2.5 border-b border-border px-4">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-sm bg-primary text-primary-foreground">
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2.5}>
              <path d="M12 3 L21 20 H3 Z" />
            </svg>
          </div>
          <div className="leading-tight">
            <div className="text-sm font-bold tracking-tight">APEX STATS</div>
            <div className="label-eyebrow text-xs">Admin console</div>
          </div>
        </Link>

        <nav className="flex-1 overflow-y-auto p-2">
          <Link
            to="/admin"
            activeOptions={{ exact: true }}
            activeProps={{ className: "bg-primary/15 text-primary border-primary/30" }}
            inactiveProps={{ className: "text-foreground/80 hover:bg-muted border-transparent" }}
            className="mb-0.5 block rounded-sm border px-3 py-2 text-xs font-semibold uppercase tracking-wider transition-colors"
          >
            <div>Dashboard</div>
            <div className="mt-0.5 text-xs font-normal normal-case tracking-normal text-muted-foreground">Overview</div>
          </Link>

          {role === "administrator" && (
            <Link
              to="/admin/users"
              activeOptions={{ exact: true }}
              activeProps={{ className: "bg-primary/15 text-primary border-primary/30" }}
              inactiveProps={{ className: "text-foreground/80 hover:bg-muted border-transparent" }}
              className="mb-0.5 mt-0.5 block rounded-sm border px-3 py-2 text-xs font-semibold uppercase tracking-wider transition-colors"
            >
              <div>Users</div>
              <div className="mt-0.5 text-xs font-normal normal-case tracking-normal text-muted-foreground">Accounts & roles</div>
            </Link>
          )}

          <NavGroup
            label="Data"
            count={dataItems.length}
            open={openData || dataActive}
            onToggle={() => setOpenData((v) => !v)}
            items={dataItems}
          />

          <NavGroup
            label="Calibration"
            count={calibrationItems.length}
            open={openCalib || calibActive}
            onToggle={() => setOpenCalib((v) => !v)}
            items={calibrationItems}
          />

          <NavGroup
            label="Analysis"
            count={analysisItems.length}
            open={openAnalysis || analysisActive}
            onToggle={() => setOpenAnalysis((v) => !v)}
            items={analysisItems}
          />

        </nav>

      </aside>

      <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden [&_header]:pr-[300px]">
        <Outlet />
      </main>
    </div>
  );
}

function NavGroup({ label, count, open, onToggle, items }: {
  label: string; count: number; open: boolean; onToggle: () => void; items: NavItem[];
}) {
  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between px-2 py-1 label-eyebrow text-xs text-muted-foreground hover:text-foreground"
      >
        <span>{label} · {count}</span>
        <span className="text-mono">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="mt-1 space-y-0.5 border-l border-border pl-2">
          {items.map((item) => (
            <Link
              key={item.to}
              to={item.to as "/admin"}
              activeOptions={{ exact: item.exact ?? false }}
              activeProps={{ className: "bg-primary/15 text-primary border-primary/30" }}
              inactiveProps={{ className: "text-foreground/80 hover:bg-muted border-transparent" }}
              className="block rounded-sm border px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors"
            >
              <div>{item.label}</div>
              <div className="mt-0.5 text-xs font-normal normal-case tracking-normal text-muted-foreground">{item.hint}</div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
