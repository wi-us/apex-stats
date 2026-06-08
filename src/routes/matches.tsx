import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { getGames, matchDurationSec } from "@/lib/mock-match";
import { useAdminStore } from "@/lib/admin-store";
import { publicMatches, publicMapRows } from "@/lib/public-data";
import { BrandMark } from "@/components/BrandMark";

export const Route = createFileRoute("/matches")({
  component: MatchesPage,
  head: () => ({
    meta: [
      { title: "Матчи — APEX STATS" },
      { name: "description", content: "Все матчи Apex Legends с VOD-аналитикой." },
    ],
  }),
});

type Status = "live" | "upcoming" | "finished";

function tournamentStatus(t: { startDate: string; endDate: string }, today: Date): Status {
  const s = new Date(t.startDate), e = new Date(t.endDate);
  return today < s ? "upcoming" : today > e ? "finished" : "live";
}

function MatchesPage() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  if (pathname !== "/matches" && pathname !== "/matches/") return <Outlet />;
  return <MatchesList />;
}

function MatchesList() {
  const store = useAdminStore();
  const [filter, setFilter] = useState<"all" | Status>("all");
  const today = new Date();
  const matches = useMemo(() => publicMatches(store.matches), [store.matches]);
  const maps = useMemo(() => publicMapRows(store.customMaps), [store.customMaps]);

  const enriched = useMemo(() => matches.map((m) => {
    const t = store.tournaments.find((x) => x.id === m.tournamentId);
    const status: Status = t ? tournamentStatus(t, today) : "finished";
    return { m, t, status, games: getGames(m), total: matchDurationSec(m) };
  }), [matches, store.tournaments, today]);

  const counts = {
    all: enriched.length,
    live: enriched.filter((x) => x.status === "live").length,
    upcoming: enriched.filter((x) => x.status === "upcoming").length,
    finished: enriched.filter((x) => x.status === "finished").length,
  };

  const filtered = filter === "all" ? enriched : enriched.filter((x) => x.status === filter);
  const groups: { key: Status; label: string }[] = [
    { key: "live", label: "В лайве" },
    { key: "upcoming", label: "Запланированы" },
    { key: "finished", label: "Завершены" },
  ];
  const visibleGroups = filter === "all" ? groups : groups.filter((g) => g.key === filter);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-20 flex h-14 items-center gap-4 border-b border-border bg-surface/80 px-4 backdrop-blur">
        <BrandMark subtitle="Матчи" />
        <div className="ml-auto flex items-center gap-2">
        </div>
      </header>
      <div className="mx-auto max-w-7xl p-6">
        <div className="mb-4 flex items-center gap-3">
          <Link to="/" aria-label="Назад" className="flex h-8 w-8 items-center justify-center rounded-sm border border-border-strong bg-surface-2 text-sm hover:bg-muted">←</Link>
          <h1 className="text-xl font-bold">Матчи</h1>
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
                {items.map(({ m, t, status, games, total }) => {
                  const firstMap = maps.find((x) => x.id === games[0].mapId);
                  return (
                    <Link key={m.id} to="/matches/$matchId" params={{ matchId: m.id }}
                      className="hud-panel hover-lift group overflow-hidden">
                      {firstMap?.image && (
                        <div className="relative h-28 overflow-hidden">
                          <img src={firstMap.image} alt={firstMap.name} loading="lazy" decoding="async" className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-110" />
                          <div className="absolute inset-0 bg-gradient-to-t from-surface via-surface/40 to-transparent" />
                          <div className="absolute left-2 top-2"><StatusBadge status={status} /></div>
                          <div className="text-mono absolute right-2 top-2 rounded-sm border border-border-strong bg-surface/90 px-1.5 py-0.5 text-xs font-bold">
                            {games.length} {games.length === 1 ? "игра" : "игр"}
                          </div>
                        </div>
                      )}
                      <div className="p-3">
                        <div className="label-eyebrow text-xs text-muted-foreground truncate">{t?.name}</div>
                        <div className="mt-1 text-sm font-semibold leading-tight">{m.name}</div>
                        <div className="text-mono mt-1 flex flex-wrap gap-1 text-xs text-muted-foreground">
                          {games.map((gg) => {
                            const gmp = maps.find((x) => x.id === gg.mapId);
                            return (
                              <span key={gg.id} className="rounded-sm border border-border bg-surface-2 px-1.5 py-0.5">
                                {gmp?.name ?? gg.mapId}
                              </span>
                            );
                          })}
                        </div>
                        <div className="text-mono mt-1 text-xs text-muted-foreground">{Math.round(total / 60)} мин</div>
                      </div>
                    </Link>
                  );
                })}
              </div>
            </section>
          );
        })}
        {filtered.length === 0 && (
          <div className="hud-panel p-6 text-center text-xs text-muted-foreground">Нет матчей с таким статусом</div>
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
