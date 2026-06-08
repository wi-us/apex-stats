import { createFileRoute, Link } from "@tanstack/react-router";
import { MatchViewer } from "@/components/MatchViewer";
import { MTestDataIO } from "@/components/MTestDataIO";
import { parseGameId, getGames } from "@/lib/mock-match";
import { useAdminStore } from "@/lib/admin-store";
import { publicMapRows, TEST_GAME_ID } from "@/lib/public-data";

export const Route = createFileRoute("/games/$gameId")({
  component: GamePage,
  notFoundComponent: () => (
    <div className="flex h-screen w-screen flex-col items-center justify-center gap-3 bg-background text-foreground">
      <h1 className="text-lg font-bold">Game not found</h1>
      <Link to="/" className="text-xs text-primary hover:underline">← Back to hub</Link>
    </div>
  ),
});

function GamePage() {
  const { gameId } = Route.useParams();
  const { matches, tournaments, customMaps } = useAdminStore();
  if (gameId === TEST_GAME_ID) {
    return (
      <>
        <MatchViewer initialGameId={gameId} />
        <MTestDataIO />
      </>
    );
  }

  const parsed = parseGameId(gameId);
  const match = parsed ? matches.find((item) => item.id === parsed.matchId) : undefined;
  const games = match ? getGames(match) : [];
  const game = parsed ? games[parsed.index] : undefined;
  if (!parsed || !match || !game) {
    return (
      <div className="flex h-screen w-screen flex-col items-center justify-center gap-3 bg-background text-foreground">
        <h1 className="text-lg font-bold">Game not found</h1>
        <Link to="/games" className="text-xs text-primary hover:underline">в†ђ Back to games</Link>
      </div>
    );
  }

  const tournament = tournaments.find((item) => item.id === match.tournamentId);
  const maps = publicMapRows(customMaps);
  const map = maps.find((item) => item.id === game.mapId);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-20 flex h-14 items-center gap-4 border-b border-border bg-surface/80 px-4 backdrop-blur">
        <Link to="/" className="text-sm font-bold tracking-tight">APEX STATS</Link>
        <span className="text-mono text-xs text-muted-foreground">
          / <Link to="/games" className="hover:text-foreground">Games</Link> / {match.name} G{game.index + 1}
        </span>
      </header>
      <div className="mx-auto max-w-5xl p-6">
        <div className="mb-4 flex items-center gap-3">
          <Link to="/games" aria-label="Back" className="flex h-8 w-8 items-center justify-center rounded-sm border border-border-strong bg-surface-2 text-sm hover:bg-muted">←</Link>
          <div>
            <div className="label-eyebrow text-xs text-muted-foreground">{tournament?.name}</div>
            <h1 className="text-xl font-bold leading-tight">{match.name} · Game {game.index + 1}</h1>
          </div>
        </div>

        <div className="hud-panel overflow-hidden">
          {map?.image && (
            <div className="h-64 overflow-hidden bg-surface-2">
              <img src={map.image} alt={map.name} className="h-full w-full object-cover" />
            </div>
          )}
          <div className="grid gap-3 p-4 sm:grid-cols-3">
            <Info label="Map" value={map?.name ?? game.mapId} />
            <Info label="Duration" value={`${Math.round(game.durationSec / 60)} min`} />
            <Info label="Game id" value={game.id} />
          </div>
          <div className="border-t border-border p-4 text-xs text-muted-foreground">
            Detailed VOD playback is currently reserved for the test viewer at{" "}
            <Link to="/games/$gameId" params={{ gameId: TEST_GAME_ID }} className="text-primary hover:underline">
              /games/{TEST_GAME_ID}
            </Link>
            . This page uses the same match metadata source as admin.
          </div>
        </div>
      </div>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-sm border border-border bg-surface-2 px-3 py-2">
      <div className="label-eyebrow text-xs text-muted-foreground">{label}</div>
      <div className="text-sm font-semibold">{value}</div>
    </div>
  );
}
