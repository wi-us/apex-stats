import { createFileRoute, Link } from "@tanstack/react-router";
import { getGames, matchDurationSec } from "@/lib/mock-match";
import { useAdminStore } from "@/lib/admin-store";
import { publicMapRows } from "@/lib/public-data";

export const Route = createFileRoute("/matches/$matchId")({
  component: MatchDetailPage,
  notFoundComponent: () => (
    <div className="flex h-screen w-screen flex-col items-center justify-center gap-3 bg-background text-foreground">
      <h1 className="text-lg font-bold">Матч не найден</h1>
      <Link to="/matches" className="text-xs text-primary hover:underline">← К матчам</Link>
    </div>
  ),
});

function MatchDetailPage() {
  const { matchId } = Route.useParams();
  const { matches, tournaments, customMaps } = useAdminStore();
  const maps = publicMapRows(customMaps);
  const match = matches.find((m) => m.id === matchId);
  if (!match) {
    return (
      <div className="flex h-screen w-screen flex-col items-center justify-center gap-3 bg-background text-foreground">
        <h1 className="text-lg font-bold">РњР°С‚С‡ РЅРµ РЅР°Р№РґРµРЅ</h1>
        <Link to="/matches" className="text-xs text-primary hover:underline">в†ђ Рљ РјР°С‚С‡Р°Рј</Link>
      </div>
    );
  }
  const tournament = tournaments.find((t) => t.id === match.tournamentId);
  const games = getGames(match);
  const total = matchDurationSec(match);
  const mm = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-20 flex h-14 items-center gap-4 border-b border-border bg-surface/80 px-4 backdrop-blur">
        <Link to="/" className="text-sm font-bold tracking-tight">APEX STATS</Link>
        <span className="text-mono text-xs text-muted-foreground">/ <Link to="/matches" className="hover:text-foreground">Матчи</Link> / {match.name}</span>
      </header>
      <div className="mx-auto max-w-5xl p-6">
        <div className="mb-4 flex items-center gap-3">
          <Link to="/matches" aria-label="Назад" className="flex h-8 w-8 items-center justify-center rounded-sm border border-border-strong bg-surface-2 text-sm hover:bg-muted">←</Link>
          <div>
            <div className="label-eyebrow text-xs text-muted-foreground">{tournament?.name}</div>
            <h1 className="text-xl font-bold leading-tight">{match.name}</h1>
          </div>
          <div className="text-mono ml-auto rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs text-muted-foreground">
            {games.length} {games.length === 1 ? "игра" : "игр"} · {Math.round(total / 60)} мин
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {games.map((g) => {
            const mp = maps.find((x) => x.id === g.mapId);
            return (
              <Link key={g.id} to="/games/$gameId" params={{ gameId: g.id }}
                className="hud-panel hover-lift group overflow-hidden">
                {mp?.image && (
                  <div className="relative h-28 overflow-hidden">
                    <img src={mp.image} alt={mp.name} loading="lazy" decoding="async" className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-110" />
                    <div className="absolute inset-0 bg-gradient-to-t from-surface via-surface/40 to-transparent" />
                    <div className="text-mono absolute left-2 top-2 rounded-sm border border-border-strong bg-surface/90 px-1.5 py-0.5 text-xs font-bold">
                      G{g.index + 1}
                    </div>
                  </div>
                )}
                <div className="p-3">
                  <div className="text-sm font-semibold leading-tight">{mp?.name ?? g.mapId}</div>
                  <div className="text-mono mt-1 text-xs text-muted-foreground">Длительность {mm(g.durationSec)}</div>
                </div>
              </Link>
            );
          })}
        </div>
      </div>
    </div>
  );
}
