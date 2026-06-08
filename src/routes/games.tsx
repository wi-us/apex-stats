import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { getGames } from "@/lib/mock-match";
import { useAdminStore } from "@/lib/admin-store";
import { publicMatches, publicMapRows } from "@/lib/public-data";
import { BrandMark } from "@/components/BrandMark";

export const Route = createFileRoute("/games")({
  component: GamesPage,
  head: () => ({
    meta: [
      { title: "Игры — APEX STATS" },
      { name: "description", content: "Все игры (карты) внутри матчей Apex Legends." },
    ],
  }),
});

type Status = "live" | "upcoming" | "finished";

function tournamentStatus(t: { startDate: string; endDate: string }, today: Date): Status {
  const s = new Date(t.startDate), e = new Date(t.endDate);
  return today < s ? "upcoming" : today > e ? "finished" : "live";
}

function GamesPage() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  if (pathname !== "/games" && pathname !== "/games/") return <Outlet />;
  return <GamesList />;
}

function GamesList() {
  const store = useAdminStore();
  const [filter, setFilter] = useState<"all" | Status>("all");
  const today = new Date();
  const mm = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;
  const matches = useMemo(() => publicMatches(store.matches), [store.matches]);
  const maps = useMemo(() => publicMapRows(store.customMaps), [store.customMaps]);

  const all = useMemo(() => matches.flatMap((m) => {
    const t = store.tournaments.find((x) => x.id === m.tournamentId);
    const status: Status = t ? tournamentStatus(t, today) : "finished";
    return getGames(m).map((g) => ({
      g, match: m, tournament: t, status, map: maps.find((x) => x.id === g.mapId),
    }));
  }), [matches, maps, store.tournaments, today]);

  const counts = {
    all: all.length,
    live: all.filter((x) => x.status === "live").length,
    upcoming: all.filter((x) => x.status === "upcoming").length,
    finished: all.filter((x) => x.status === "finished").length,
  };

  const filtered = filter === "all" ? all : all.filter((x) => x.status === filter);
  const groups: { key: Status; label: string }[] = [
    { key: "live", label: "В лайве" },
    { key: "upcoming", label: "Запланированы" },
    { key: "finished", label: "Завершены" },
  ];
  const visibleGroups = filter === "all" ? groups : groups.filter((g) => g.key === filter);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-20 flex h-14 items-center gap-4 border-b border-border bg-surface/80 px-4 backdrop-blur">
        <BrandMark subtitle="Игры" />
      </header>
      <div className="mx-auto max-w-7xl p-6">
        <div className="mb-4 flex items-center gap-3">
          <Link to="/" aria-label="Назад" className="flex h-8 w-8 items-center justify-center rounded-sm border border-border-strong bg-surface-2 text-sm hover:bg-muted">←</Link>
          <h1 className="text-xl font-bold">Игры</h1>
          <span className="text-mono ml-2 rounded-sm border border-border bg-surface-2 px-2 py-0.5 text-xs text-muted-foreground">{all.length}</span>
        </div>

        <div className="mb-5 flex flex-wrap gap-1.5">
          {([
            { key: "all", label: "Все" },
            { key: "live", label: "В лайве" },
            { key: "upcoming", label: "Запланированы" },
            { key: "finished", label: "Завершены" },
          ] as const).map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`rounded-sm border px-2.5 py-1 text-xs font-semibold uppercase tracking-wider transition ${
                filter === f.key
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border-strong bg-surface-2 hover:bg-muted"
              }`}
            >
              {f.label} <span className="opacity-60">· {counts[f.key]}</span>
            </button>
          ))}
        </div>

        {visibleGroups.map((g) => {
          const items = filtered.filter((x) => x.status === g.key);
          if (items.length === 0) return null;
          return (
            <section key={g.key} className="mb-6">
              <div className="mb-2 flex items-center gap-2 border-b border-border pb-1.5">
                <StatusDot status={g.key} />
                <h2 className="text-sm font-bold uppercase tracking-wider">{g.label}</h2>
                <span className="text-mono text-xs text-muted-foreground">{items.length}</span>
              </div>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {items.map(({ g: gg, match, tournament, status, map }) => (
                  <Link key={gg.id} to="/games/$gameId" params={{ gameId: gg.id }} className="hud-panel hover-lift group overflow-hidden">
                    {map?.image && (
                      <div className="relative h-28 overflow-hidden">
                        <img src={map.image} alt={map.name} loading="lazy" decoding="async" className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-110" />
                        <div className="absolute left-2 top-2"><StatusBadge status={status} /></div>
                        <div className="text-mono absolute right-2 top-2 rounded-sm border border-border-strong bg-surface/90 px-1.5 py-0.5 text-xs font-bold">
                          G{gg.index + 1} · {mm(gg.durationSec)}
                        </div>
                      </div>
                    )}
                    <div className="p-3">
                      <div className="label-eyebrow text-xs text-muted-foreground truncate">{tournament?.name}</div>
                      <div className="mt-1 text-sm font-semibold leading-tight">{map?.name ?? gg.mapId}</div>
                      <div className="text-mono mt-1 text-xs text-muted-foreground truncate">{match.name}</div>
                    </div>
                  </Link>
                ))}
              </div>
            </section>
          );
        })}
        {filtered.length === 0 && (
          <div className="hud-panel p-6 text-center text-xs text-muted-foreground">Нет игр с таким статусом</div>
        )}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: Status }) {
  const map = {
    live:     { label: "LIVE",         cls: "border-destructive/40 bg-destructive/15 text-destructive" },
    upcoming: { label: "ЗАПЛАНИРОВАН", cls: "border-primary/40 bg-primary/10 text-primary" },
    finished: { label: "ЗАВЕРШЁН",     cls: "border-border bg-surface-2 text-muted-foreground" },
  } as const;
  const m = map[status];
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-bold tracking-wider ${m.cls}`}>
      {status === "live" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-destructive" />}
      {m.label}
    </span>
  );
}

function StatusDot({ status }: { status: Status }) {
  const cls =
    status === "live" ? "bg-destructive" : status === "upcoming" ? "bg-primary" : "bg-muted-foreground";
  return <span className={`h-2 w-2 rounded-full ${cls}`} />;
}
