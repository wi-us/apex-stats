import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { getGames } from "@/lib/mock-match";
import { useAdminStore } from "@/lib/admin-store";
import { publicMatches, publicMapRows } from "@/lib/public-data";
import { BrandMark } from "@/components/BrandMark";

export const Route = createFileRoute("/tournaments")({
  component: TournamentsPage,
  head: () => ({
    meta: [
      { title: "Турниры — APEX STATS" },
      { name: "description", content: "Все турниры Apex Legends с матчами и картами." },
    ],
  }),
});

function TournamentsPage() {
  const store = useAdminStore();
  const [filter, setFilter] = useState<"all" | "live" | "upcoming" | "finished">("all");
  const today = new Date();
  const matches = useMemo(() => publicMatches(store.matches), [store.matches]);
  const maps = useMemo(() => publicMapRows(store.customMaps), [store.customMaps]);
  const { tournaments } = store;
  const withStatus = useMemo(() => {
    return tournaments.map((t) => {
      const start = new Date(t.startDate);
      const end = new Date(t.endDate);
      const status: "live" | "upcoming" | "finished" =
        today < start ? "upcoming" : today > end ? "finished" : "live";
      return { ...t, status };
    });
  }, [tournaments, today]);
  const filtered = filter === "all" ? withStatus : withStatus.filter((t) => t.status === filter);
  const counts = {
    all: withStatus.length,
    live: withStatus.filter((t) => t.status === "live").length,
    upcoming: withStatus.filter((t) => t.status === "upcoming").length,
    finished: withStatus.filter((t) => t.status === "finished").length,
  };
  const groups: { key: "live" | "upcoming" | "finished"; label: string }[] = [
    { key: "live", label: "В лайве" },
    { key: "upcoming", label: "Запланированы" },
    { key: "finished", label: "Завершены" },
  ];
  const visibleGroups = filter === "all" ? groups : groups.filter((g) => g.key === filter);
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-20 flex h-14 items-center gap-4 border-b border-border bg-surface/80 px-4 backdrop-blur">
        <BrandMark subtitle="Турниры" />
        <div className="ml-auto flex items-center gap-2">
        </div>
      </header>
      <div className="mx-auto max-w-7xl p-6">
        <div className="mb-4 flex items-center gap-3">
          <Link to="/" aria-label="Назад" className="flex h-8 w-8 items-center justify-center rounded-sm border border-border-strong bg-surface-2 text-sm hover:bg-muted">←</Link>
          <h1 className="text-xl font-bold">Турниры</h1>
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
          const items = filtered.filter((t) => t.status === g.key);
          if (items.length === 0) return null;
          return (
            <section key={g.key} className="mb-6">
              <div className="mb-2 flex items-center gap-2 border-b border-border pb-1.5">
                <StatusDot status={g.key} />
                <h2 className="text-sm font-bold uppercase tracking-wider">{g.label}</h2>
                <span className="text-mono text-xs text-muted-foreground">{items.length}</span>
              </div>
              <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                {items.map((t) => {
                  const tMatches = matches.filter((m) => m.tournamentId === t.id);
                  return (
                    <div key={t.id} className="hud-panel p-4">
                      <div className="flex items-start justify-between gap-2">
                        <div className="label-eyebrow text-xs">{t.region} · {t.type} · Y{t.year}</div>
                        <StatusBadge status={t.status} />
                      </div>
                      <div className="mt-1 text-sm font-semibold">{t.name}</div>
                      <div className="text-mono mt-0.5 text-xs text-muted-foreground">{t.startDate} → {t.endDate}</div>
                      <ul className="mt-3 space-y-1">
                        {tMatches.map((m) => {
                          const games = getGames(m);
                          return (
                            <li key={m.id}>
                              <Link to="/matches/$matchId" params={{ matchId: m.id }}
                                className="block rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs hover:border-primary/40 hover:text-primary">
                                <div className="flex items-center justify-between gap-2">
                                  <span className="font-semibold">{m.name}</span>
                                  <span className="text-mono text-xs text-muted-foreground">{games.length} {games.length === 1 ? "игра" : "игр"}</span>
                                </div>
                                <div className="mt-1 flex flex-wrap gap-1">
                                  {games.map((g) => {
                                    const gmp = maps.find((x) => x.id === g.mapId);
                                    return (
                                      <span key={g.id} className="text-mono rounded-sm border border-border bg-surface px-1 py-0.5 text-xs text-muted-foreground">
                                        {gmp?.name ?? g.mapId}
                                      </span>
                                    );
                                  })}
                                </div>
                              </Link>
                            </li>
                          );
                        })}
                        {tMatches.length === 0 && <li className="text-xs text-muted-foreground">Матчей пока нет</li>}
                      </ul>
                    </div>
                  );
                })}
              </div>
            </section>
          );
        })}
        {filtered.length === 0 && (
          <div className="hud-panel p-6 text-center text-xs text-muted-foreground">Нет турниров с таким статусом</div>
        )}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: "live" | "upcoming" | "finished" }) {
  const map = {
    live:     { label: "LIVE",        cls: "border-destructive/40 bg-destructive/15 text-destructive" },
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

function StatusDot({ status }: { status: "live" | "upcoming" | "finished" }) {
  const cls =
    status === "live" ? "bg-destructive" : status === "upcoming" ? "bg-primary" : "bg-muted-foreground";
  return <span className={`h-2 w-2 rounded-full ${cls}`} />;
}
