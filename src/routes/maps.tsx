import { createFileRoute, Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { getGames } from "@/lib/mock-match";
import { useAdminStore } from "@/lib/admin-store";
import { publicMapRows } from "@/lib/public-data";
import { BrandMark } from "@/components/BrandMark";

export const Route = createFileRoute("/maps")({
  component: MapsPage,
  head: () => ({
    meta: [
      { title: "Карты — APEX STATS" },
      { name: "description", content: "Все карты Apex Legends и тепловые карты команд по матчам." },
    ],
  }),
});

function MapsPage() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  if (pathname !== "/maps" && pathname !== "/maps/") return <Outlet />;
  return <MapsGrid />;
}

function MapsGrid() {
  const { matches, customMaps } = useAdminStore();
  const rows = useMemo(() => publicMapRows(customMaps), [customMaps]);
  const [query, setQuery] = useState("");
  const navigate = useNavigate();
  const q = query.trim().toLowerCase();
  const filtered = q ? rows.filter((r) => r.name.toLowerCase().includes(q)) : rows;

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-20 flex h-14 items-center gap-4 border-b border-border bg-surface/80 px-4 backdrop-blur">
        <BrandMark subtitle="Карты" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Поиск карты…"
          className="ml-4 w-64 rounded-sm border border-border bg-background px-2 py-1.5 text-xs"
        />
        <div className="ml-auto flex items-center gap-2">
        </div>
      </header>

      <div className="mx-auto max-w-7xl p-6">
        <div className="mb-4 flex items-center gap-3">
          <Link to="/" aria-label="Назад" className="flex h-8 w-8 items-center justify-center rounded-sm border border-border-strong bg-surface-2 text-sm hover:bg-muted">←</Link>
          <h1 className="text-xl font-bold">Карты</h1>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filtered.map((mp) => {
            const playedIn = matches.reduce((acc, m) => acc + getGames(m).filter((g) => g.mapId === mp.id).length, 0);
            return (
              <button
                key={mp.id}
                onClick={() => navigate({ to: "/maps/$mapId", params: { mapId: mp.id } })}
                className="hud-panel group overflow-hidden text-left transition hover:border-primary/50"
              >
                <div className="aspect-video w-full overflow-hidden bg-surface-2">
                  <img src={mp.image} alt={mp.name} loading="lazy" decoding="async" className="h-full w-full object-cover transition group-hover:scale-105" />
                </div>
                <div className="flex items-center justify-between border-t border-border px-3 py-2">
                  <div className="text-xs font-semibold">{mp.name}</div>
                  <div className="text-mono text-xs text-muted-foreground">{playedIn} игр</div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
