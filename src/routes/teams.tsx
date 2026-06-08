import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { BrandMark } from "@/components/BrandMark";
import { TeamLogo } from "@/components/admin/TeamLogo";
import { useAdminStore } from "@/lib/admin-store";
import type { MatchFull, Team, Tournament } from "@/lib/mock-match";

export const Route = createFileRoute("/teams")({
  component: TeamsPage,
  head: () => ({
    meta: [
      { title: "Teams - APEX STATS" },
      { name: "description", content: "All Apex Legends teams and their match schedule." },
    ],
  }),
});

type TeamStatus = NonNullable<Team["status"]>;
type ScheduleItem = {
  match: MatchFull;
  tour: Tournament | undefined;
  start: string;
  end: string;
};

function TeamsPage() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  if (pathname !== "/teams" && pathname !== "/teams/") return <Outlet />;
  return <TeamsList />;
}

function TeamsList() {
  const { teams, matches, tournaments } = useAdminStore();
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | TeamStatus>("all");

  const today = new Date().toISOString().slice(0, 10);
  const tournamentById = useMemo(() => new Map(tournaments.map((t) => [t.id, t])), [tournaments]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return teams.filter((team) => {
      const status = team.status ?? "active";
      if (statusFilter !== "all" && status !== statusFilter) return false;
      if (!q) return true;
      return [team.tag, team.name, ...(team.players ?? [])].some((value) =>
        String(value).toLowerCase().includes(q),
      );
    });
  }, [query, statusFilter, teams]);

  const counts = useMemo(() => {
    const byStatus = {
      all: teams.length,
      active: teams.filter((team) => (team.status ?? "active") === "active").length,
      archived: teams.filter((team) => (team.status ?? "active") === "archived").length,
    };
    return byStatus;
  }, [teams]);

  function teamSchedule(teamId: string) {
    const teamMatches = matches.filter((match) => match.teamIds?.includes(teamId));
    const items: ScheduleItem[] = teamMatches.map((match) => {
      const tour = tournamentById.get(match.tournamentId);
      return {
        match,
        tour,
        start: tour?.startDate ?? "",
        end: tour?.endDate ?? "",
      };
    });
    const live = items.find((item) => item.start && item.end && item.start <= today && today <= item.end);
    const past = items
      .filter((item) => item.end && item.end < today)
      .sort((a, b) => b.end.localeCompare(a.end))[0];
    const upcoming = items
      .filter((item) => item.start && item.start > today)
      .sort((a, b) => a.start.localeCompare(b.start))[0];
    return { live, last: past ?? items[0], next: live ?? upcoming };
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-20 flex h-14 items-center gap-4 border-b border-border bg-surface/80 px-4 backdrop-blur">
        <BrandMark subtitle="Teams" />
      </header>

      <div className="mx-auto max-w-7xl p-6">
        <div className="mb-4 flex items-center gap-3">
          <Link
            to="/"
            aria-label="Back"
            className="flex h-8 w-8 items-center justify-center rounded-sm border border-border-strong bg-surface-2 text-sm hover:bg-muted"
          >
            &larr;
          </Link>
          <h1 className="text-xl font-bold">Teams</h1>
        </div>

        <div className="mb-4 flex flex-wrap items-center gap-3">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search teams or players..."
            className="w-64 rounded-sm border border-border bg-background px-2 py-1.5 text-xs"
          />
          <div className="flex items-center gap-1 rounded-sm border border-border bg-background p-0.5">
            {(["all", "active", "archived"] as const).map((status) => (
              <button
                key={status}
                onClick={() => setStatusFilter(status)}
                className={
                  "rounded-sm px-2 py-1 text-xs font-semibold uppercase tracking-wider " +
                  (statusFilter === status
                    ? "bg-primary/15 text-primary"
                    : "bg-surface text-muted-foreground hover:text-foreground")
                }
              >
                {status} <span className="opacity-60">{counts[status]}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="hud-panel overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-surface-2">
              <tr className="label-eyebrow text-left text-xs">
                <th className="w-[64px] px-3 py-2">Logo</th>
                <th className="w-[100px] px-3 py-2">Tag</th>
                <th className="px-3 py-2">Name</th>
                <th className="w-[110px] px-3 py-2">Status</th>
                <th className="w-[260px] px-3 py-2">Last match</th>
                <th className="w-[260px] px-3 py-2">Next match</th>
                <th className="w-[150px] px-3 py-2 text-right">Links</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((team) => {
                const schedule = teamSchedule(team.id);
                const status = team.status ?? "active";
                return (
                  <tr key={team.id} className="border-b border-border last:border-0 hover:bg-surface-2">
                    <td className="px-3 py-2">
                      <TeamLogo team={team} size={32} />
                    </td>
                    <td className="px-3 py-2 text-mono text-xs font-bold">{team.tag}</td>
                    <td className="px-3 py-2 text-xs">
                      <Link
                        to="/teams/$teamId"
                        params={{ teamId: team.id }}
                        className="font-semibold text-foreground hover:text-primary"
                      >
                        {team.name}
                      </Link>
                      {team.players?.length > 0 && (
                        <div className="mt-0.5 truncate text-xs text-muted-foreground">
                          {team.players.join(" / ")}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      <StatusBadge status={status} />
                    </td>
                    <td className="px-3 py-2 text-xs">
                      <MatchCell item={schedule.last} />
                    </td>
                    <td className="px-3 py-2 text-xs">
                      <MatchCell item={schedule.next} live={!!schedule.live} />
                    </td>
                    <td className="px-3 py-2 text-right text-xs">
                      {team.liquipediaUrl && (
                        <a
                          href={team.liquipediaUrl}
                          target="_blank"
                          rel="noreferrer"
                          title="Open Liquipedia page"
                          className="inline-flex items-center rounded-sm border border-border bg-surface px-2 py-1 hover:bg-muted"
                        >
                          Liquipedia
                        </a>
                      )}
                    </td>
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-3 py-6 text-center text-xs text-muted-foreground">
                    No teams
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function MatchCell({ item, live = false }: { item?: ScheduleItem; live?: boolean }) {
  if (!item) return <span className="text-muted-foreground">-</span>;
  return (
    <Link
      to="/matches/$matchId"
      params={{ matchId: item.match.id }}
      className={live ? "block rounded-sm border border-destructive/40 bg-destructive/10 px-2 py-1" : "block"}
    >
      <div className="flex items-center gap-1.5 font-semibold">
        {live && (
          <span className="inline-flex items-center gap-1 rounded-sm bg-destructive px-1 py-[1px] text-xs font-bold uppercase tracking-wider text-destructive-foreground">
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
            LIVE
          </span>
        )}
        <span className="truncate">{item.match.name}</span>
      </div>
      <div className="truncate text-xs text-muted-foreground">{item.tour?.name ?? "-"}</div>
    </Link>
  );
}

function StatusBadge({ status }: { status: TeamStatus }) {
  return (
    <span
      className={
        "inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wider " +
        (status === "active" ? "status-active" : "status-archived")
      }
    >
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-current" />
      {status}
    </span>
  );
}
