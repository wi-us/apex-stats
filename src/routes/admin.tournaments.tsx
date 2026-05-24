import { createFileRoute, Link } from "@tanstack/react-router";
import { Fragment, useState } from "react";
import {
  tournaments as seedT,
  maps as seedMaps,
  type Tournament,
  type TournamentType,
  type TournamentRegion,
  type TournamentStatus,
  type TournamentStage,
  type MatchFull,
} from "@/lib/mock-match";
import { useAdminStore, type AnalysisProcess } from "@/lib/admin-store";
import { TeamLogo } from "@/components/admin/TeamLogo";

export const Route = createFileRoute("/admin/tournaments")({ component: TournamentsAdmin });

const TYPES: TournamentType[] = ["LAN", "Online", "Qualifier"];
const REGIONS: TournamentRegion[] = ["EMEA", "APAC", "North America", "South America"];
const YEARS = [1, 2, 3, 4, 5, 6];
const STATUSES: TournamentStatus[] = ["draft", "upcoming", "active", "finished", "archived"];
const STAGES: TournamentStage[] = ["Regular Season", "Playoffs", "Finals", "Qualifier", "Group Stage"];
const SPLITS = ["1", "2", "3"];

/** ALGS-style points for placement. */
function placementPoints(p: number): number {
  if (p <= 0) return 0;
  const table = [12, 9, 7, 5, 4, 3, 3, 2, 2, 2, 1, 1, 1, 1, 1];
  return table[p - 1] ?? 0;
}

function fmt(d: string) {
  const [y, m, day] = d.split("-");
  return `${day}.${m}.${y}`;
}
function fmtRange(a: string, b: string) {
  return `${fmt(a)}–${fmt(b)}`;
}

const statusStyle: Record<TournamentStatus, string> = {
  draft:    "border-border bg-surface-2 text-muted-foreground",
  upcoming: "border-primary/40 bg-primary/10 text-primary",
  active:   "border-amber-500/40 bg-amber-500/10 text-amber-400",
  finished: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400",
  archived: "border-border bg-surface-2 text-muted-foreground/70",
};
function StatusBadge({ s }: { s: TournamentStatus }) {
  return (
    <span className={`inline-flex items-center rounded-sm border px-1.5 py-0.5 text-xs font-bold uppercase tracking-wider ${statusStyle[s]}`}>{s}</span>
  );
}

function Indicator({ label, state, valueLabel }: { label: string; state: "ok" | "missing" | "pending"; valueLabel?: string }) {
  const cls =
    state === "ok"
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-400"
      : state === "pending"
      ? "border-amber-500/40 bg-amber-500/10 text-amber-400"
      : "border-border bg-surface-2 text-muted-foreground";
  const tag = valueLabel ?? (state === "ok" ? "ready" : state === "pending" ? "partial" : "missing");
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wider ${cls}`}
      title={`${label}: ${tag}`}
    >
      <span>{label}</span>
      <span className="opacity-70">·</span>
      <span>{tag}</span>
    </span>
  );
}

function deriveStatus(t: Tournament, tMatches: MatchFull[]): TournamentStatus {
  if (t.status) return t.status;
  const today = new Date().toISOString().slice(0, 10);
  if (!t.startDate || !t.endDate) return "draft";
  if (today < t.startDate) return "upcoming";
  if (today > t.endDate) return "finished";
  return "active";
}
function isMatchReady(m: MatchFull): boolean {
  const mapIds = m.mapIds && m.mapIds.length > 0 ? m.mapIds : [m.mapId];
  return Boolean(m.vodLink) && mapIds.length > 0;
}
function fmtRelative(ts: number): string {
  const diff = Date.now() - ts;
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

type TabKey = "overview" | "matches" | "teams" | "maps";

function TournamentsAdmin() {
  const { matches: allMatches, teams: allTeams, processes } = useAdminStore();
  const [rows, setRows] = useState<Tournament[]>(seedT);
  const [editing, setEditing] = useState<Tournament | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [tabById, setTabById] = useState<Record<string, TabKey>>({});
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const filtered = q
    ? rows.filter((r) =>
        [r.name, r.region, r.type, `year ${r.year}`].some((v) =>
          String(v).toLowerCase().includes(q),
        ),
      )
    : rows;

  const startCreate = () =>
    setEditing({
      id: `t-${Date.now()}`,
      name: "",
      startDate: new Date().toISOString().slice(0, 10),
      endDate: new Date().toISOString().slice(0, 10),
      year: 6,
      type: "LAN",
      region: "EMEA",
    });
  const startEdit = (e: React.MouseEvent, row: Tournament) => {
    e.stopPropagation();
    setEditing({ ...row });
  };
  const remove = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (!confirm("Delete tournament?")) return;
    setRows(rows.filter((r) => r.id !== id));
  };
  const save = () => {
    if (!editing) return;
    const exists = rows.some((r) => r.id === editing.id);
    setRows(exists ? rows.map((r) => (r.id === editing.id ? editing : r)) : [...rows, editing]);
    setEditing(null);
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface px-6">
        <div className="flex items-center gap-4">
          <h1 className="text-sm font-bold uppercase tracking-wider">Tournaments</h1>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search tournaments…"
            className="w-64 rounded-sm border border-border bg-background px-2 py-1.5 text-xs"
          />
        </div>
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="hud-panel overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-surface-2">
              <tr className="label-eyebrow text-left text-xs">
                <th className="px-3 py-2 w-8"></th>
                <th className="px-3 py-2">Name</th>
                <th className="px-3 py-2 w-[200px]">Dates</th>
                <th className="px-3 py-2 w-[80px]">Year</th>
                <th className="px-3 py-2 w-[110px]">Type</th>
                <th className="px-3 py-2 w-[110px]">Status</th>
                <th className="px-3 py-2 w-[520px]">Progress</th>
                <th className="px-3 py-2 w-[150px]">Region</th>
                <th className="px-3 py-2 w-[180px] text-right">
                  <div className="flex items-center justify-end gap-2">
                    <span>Actions</span>
                    <button
                      onClick={startCreate}
                      className="rounded-sm bg-primary px-2 py-0.5 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110"
                    >
                      + Add
                    </button>
                  </div>
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => {
                const isOpen = expandedId === row.id;
                const tMatches = allMatches.filter((m) => m.tournamentId === row.id);
                const tMapIds = Array.from(new Set(tMatches.flatMap((m) => (m.mapIds && m.mapIds.length > 0 ? m.mapIds : [m.mapId]))));
                const tMaps = tMapIds.map((id) => seedMaps.find((mp) => mp.id === id)).filter(Boolean) as typeof seedMaps;
                const tTeamIds = Array.from(new Set(tMatches.flatMap((m) => m.teamIds ?? [])));
                const tTeams = (tTeamIds.length ? tTeamIds : allTeams.map((t) => t.id))
                  .map((id) => allTeams.find((t) => t.id === id))
                  .filter(Boolean) as typeof allTeams;
                const tProcesses = processes.filter((p: AnalysisProcess) => tMatches.some((m) => m.id === p.matchId));
                const activeJobs = tProcesses.filter((p) => p.status === "queued" || p.status === "running").length;
                const failedJobs = tProcesses.filter((p) => p.status === "failed").length;
                const doneJobs = tProcesses.filter((p) => p.status === "done").length;
                const lastTs = tProcesses.reduce((acc, p) => Math.max(acc, p.createdAt), 0);
                const readyMatches = tMatches.filter(isMatchReady).length;
                const status = deriveStatus(row, tMatches);
                const tab: TabKey = tabById[row.id] ?? "overview";
                const setTab = (t: TabKey) => setTabById((s) => ({ ...s, [row.id]: t }));

                const matchesState: "ok" | "pending" | "missing" =
                  tMatches.length === 0 ? "missing"
                  : readyMatches === tMatches.length ? "ok"
                  : readyMatches > 0 ? "pending" : "missing";
                const jobsState: "ok" | "pending" | "missing" =
                  tProcesses.length === 0 ? "missing"
                  : activeJobs > 0 ? "pending"
                  : doneJobs > 0 ? "ok" : "missing";
                const teamsState: "ok" | "pending" | "missing" =
                  tTeams.length >= 20 ? "ok" : tTeams.length > 0 ? "pending" : "missing";
                const mapsState: "ok" | "pending" | "missing" =
                  tMaps.length > 0 ? "ok" : "missing";

                return (
                  <Fragment key={row.id}>
                    <tr
                      onClick={() => setExpandedId(isOpen ? null : row.id)}
                      className={`cursor-pointer border-b border-border hover:bg-surface-2 ${isOpen ? "bg-surface-2" : ""}`}
                    >
                      <td className="px-3 py-2 text-xs text-muted-foreground">{isOpen ? "▾" : "▸"}</td>
                      <td className="px-3 py-2 text-xs font-semibold">{row.name}</td>
                      <td className="px-3 py-2 text-mono text-xs tabular-nums">{fmtRange(row.startDate, row.endDate)}</td>
                      <td className="px-3 py-2 text-xs">Year {row.year}</td>
                      <td className="px-3 py-2 text-xs"><TypeBadge type={row.type} /></td>
                      <td className="px-3 py-2 text-xs"><StatusBadge s={status} /></td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                          <Indicator label="Matches" state={matchesState} valueLabel={`${readyMatches}/${tMatches.length}`} />
                          <Indicator label="Teams" state={teamsState} valueLabel={String(tTeams.length)} />
                          <Indicator label="Maps" state={mapsState} valueLabel={String(tMaps.length)} />
                          <Indicator label="Jobs" state={jobsState} valueLabel={activeJobs > 0 ? `${activeJobs} active` : tProcesses.length === 0 ? "missing" : "done"} />
                          {failedJobs > 0 && (
                            <span className="rounded-sm border border-destructive/40 bg-destructive/10 px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wider text-destructive">
                              {failedJobs} failed
                            </span>
                          )}
                          <span className="text-xs text-muted-foreground">· last: {lastTs > 0 ? fmtRelative(lastTs) : "—"}</span>
                        </div>
                      </td>
                      <td className="px-3 py-2 text-xs">{row.region}</td>
                      <td className="px-3 py-2 text-right text-xs whitespace-nowrap">
                        <div className="inline-flex items-center gap-1">
                          <button onClick={(e) => startEdit(e, row)} className="rounded-sm border border-border bg-surface px-2 py-1 hover:bg-muted">Edit</button>
                          <button onClick={(e) => remove(e, row.id)} className="rounded-sm border border-destructive/40 bg-surface px-2 py-1 text-destructive hover:bg-destructive/10">Delete</button>
                        </div>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="border-b border-border bg-background">
                        <td colSpan={9} className="p-0">
                          <div className="p-5" onClick={(e) => e.stopPropagation()}>
                            <div className="mb-3 flex flex-wrap gap-1 border-b border-border pb-2">
                              {(["overview","matches","teams","maps"] as TabKey[]).map((k) => (
                                <button
                                  key={k}
                                  onClick={() => setTab(k)}
                                  className={`rounded-sm border px-2.5 py-1 text-xs font-semibold uppercase tracking-wider ${tab === k ? "border-primary/40 bg-primary/10 text-primary" : "border-border bg-surface text-muted-foreground hover:bg-muted"}`}
                                >
                                  {k}
                                </button>
                              ))}
                            </div>

                            {tab === "overview" && (
                              <div className="grid gap-4 md:grid-cols-[360px_1fr]">
                                <div className="hud-panel p-3">
                                  <div className="label-eyebrow mb-2 text-xs">Summary</div>
                                  <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
                                    <dt className="text-muted-foreground">Status</dt><dd><StatusBadge s={status} /></dd>
                                    <dt className="text-muted-foreground">Type</dt><dd><TypeBadge type={row.type} /></dd>
                                    <dt className="text-muted-foreground">Region</dt><dd className="truncate">{row.region}</dd>
                                    <dt className="text-muted-foreground">Year</dt><dd>Year {row.year}{row.split ? ` · Split ${row.split}` : ""}</dd>
                                    {row.stage && (<><dt className="text-muted-foreground">Stage</dt><dd>{row.stage}</dd></>)}
                                    <dt className="text-muted-foreground">Dates</dt><dd className="text-mono tabular-nums">{fmtRange(row.startDate, row.endDate)}</dd>
                                    <dt className="text-muted-foreground">Matches</dt><dd className="text-mono tabular-nums">{readyMatches} / {tMatches.length}</dd>
                                    <dt className="text-muted-foreground">Teams</dt><dd className="text-mono tabular-nums">{tTeams.length}</dd>
                                    <dt className="text-muted-foreground">Maps</dt><dd className="text-mono tabular-nums">{tMaps.length}</dd>
                                    <dt className="text-muted-foreground">Active jobs</dt><dd className="text-mono tabular-nums">{activeJobs}{failedJobs > 0 && <span className="ml-2 text-destructive">{failedJobs} failed</span>}</dd>
                                    <dt className="text-muted-foreground">Last updated</dt><dd>{lastTs > 0 ? fmtRelative(lastTs) : "—"}</dd>
                                    {row.liquipediaUrl && (
                                      <>
                                        <dt className="text-muted-foreground">Liquipedia</dt>
                                        <dd className="truncate">
                                          <a href={row.liquipediaUrl} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                                            {row.liquipediaUrl.replace(/^https?:\/\//, "")}
                                          </a>
                                        </dd>
                                      </>
                                    )}
                                  </dl>
                                  {row.description && (
                                    <div className="mt-3 border-t border-border pt-2 text-xs text-muted-foreground whitespace-pre-wrap">
                                      {row.description}
                                    </div>
                                  )}
                                </div>
                                <div className="hud-panel p-3">
                                  <div className="label-eyebrow mb-2 text-xs">Matches ({tMatches.length})</div>
                                  {tMatches.length === 0 ? <Empty /> : (
                                    <ol className="grid grid-cols-1 gap-1 sm:grid-cols-2 2xl:grid-cols-3">
                                      {tMatches.map((m) => {
                                        const ids = m.mapIds && m.mapIds.length > 0 ? m.mapIds : [m.mapId];
                                        const names = Array.from(new Set(ids.map((id) => seedMaps.find((x) => x.id === id)?.name ?? id))).join(", ");
                                        return (
                                          <li key={m.id}>
                                            <Link
                                              to={"/admin/matches/$matchId" as "/admin/matches"}
                                              params={{ matchId: m.id } as never}
                                              className="flex items-center gap-2 rounded-sm border border-border bg-surface px-2 py-1 text-xs hover:bg-muted"
                                            >
                                              <span className="flex-1 truncate font-semibold">{m.name}</span>
                                              <span className="truncate text-muted-foreground">{names}</span>
                                              <span className={`rounded-sm border px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wider ${isMatchReady(m) ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-400" : "border-border bg-surface-2 text-muted-foreground"}`}>{isMatchReady(m) ? "ready" : "draft"}</span>
                                            </Link>
                                          </li>
                                        );
                                      })}
                                    </ol>
                                  )}
                                </div>
                              </div>
                            )}

                            {tab === "matches" && (
                              <div className="hud-panel p-3">
                                <div className="label-eyebrow mb-2 text-xs">Matches ({tMatches.length})</div>
                                {tMatches.length === 0 ? <Empty /> : (
                                  <ul className="space-y-1">
                                    {tMatches.map((m) => {
                                      const ids = m.mapIds && m.mapIds.length > 0 ? m.mapIds : [m.mapId];
                                      const names = Array.from(new Set(ids.map((id) => seedMaps.find((x) => x.id === id)?.name ?? id))).join(", ");
                                      return (
                                        <li key={m.id}>
                                          <Link
                                            to={"/admin/matches/$matchId" as "/admin/matches"}
                                            params={{ matchId: m.id } as never}
                                            className="flex items-center justify-between rounded-sm border border-border bg-surface px-2 py-1.5 text-xs hover:bg-muted"
                                          >
                                            <span className="font-semibold">{m.name}</span>
                                            <span className="flex items-center gap-2 text-muted-foreground">
                                              <span>{names}</span>
                                              <span className={`rounded-sm border px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wider ${isMatchReady(m) ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-400" : "border-border bg-surface-2 text-muted-foreground"}`}>{isMatchReady(m) ? "ready" : "draft"}</span>
                                            </span>
                                          </Link>
                                        </li>
                                      );
                                    })}
                                  </ul>
                                )}
                              </div>
                            )}

                            {tab === "teams" && (
                              <div className="hud-panel p-3">
                                <div className="label-eyebrow mb-2 text-xs">Teams ({tTeams.length})</div>
                                <ul className="grid grid-cols-2 gap-1 md:grid-cols-3 lg:grid-cols-4">
                                  {[...tTeams]
                                    .map((t) => ({ t, pts: placementPoints(t.placement) + t.kills }))
                                    .sort((a, b) => b.pts - a.pts)
                                    .map(({ t, pts }) => (
                                      <li key={t.id}>
                                        <Link
                                          to="/admin/teams/$teamId"
                                          params={{ teamId: t.id }}
                                          className="flex items-center gap-2 rounded-sm border border-border bg-surface px-2 py-1.5 text-xs hover:bg-muted"
                                        >
                                          <TeamLogo team={t} size={22} />
                                          <span className="text-mono text-xs font-bold">{t.tag}</span>
                                          <span className="flex-1 truncate">{t.name}</span>
                                          <span
                                            className="rounded-sm border border-primary/40 bg-primary/10 px-1.5 py-0.5 text-mono text-xs font-bold tabular-nums text-primary"
                                            title={`Placement pts ${placementPoints(t.placement)} + kills ${t.kills}`}
                                          >
                                            {pts} pts
                                          </span>
                                        </Link>
                                      </li>
                                    ))}
                                </ul>
                              </div>
                            )}

                            {tab === "maps" && (
                              <div className="hud-panel p-3">
                                <div className="label-eyebrow mb-2 text-xs">Maps used ({tMaps.length})</div>
                                {tMaps.length === 0 ? <Empty /> : (
                                  <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                                    {tMaps.map((mp) => (
                                      <li key={mp.id} className="flex items-center gap-3 rounded-sm border border-border bg-surface p-2">
                                        <img src={mp.image} alt={mp.name} className="h-12 w-16 rounded-sm object-cover" />
                                        <div className="text-xs font-semibold">{mp.name}</div>
                                      </li>
                                    ))}
                                  </ul>
                                )}
                              </div>
                            )}

                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
              {filtered.length === 0 && (
                <tr><td colSpan={9} className="px-3 py-6 text-center text-xs text-muted-foreground">No tournaments</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {editing && (
        <EditDialog
          row={editing}
          isNew={!rows.some((r) => r.id === editing.id)}
          onChange={setEditing}
          onCancel={() => setEditing(null)}
          onSave={save}
        />
      )}
    </div>
  );
}

function Empty() {
  return <div className="rounded-sm border border-dashed border-border px-2 py-4 text-center text-xs text-muted-foreground">No data</div>;
}

function TypeBadge({ type }: { type: TournamentType }) {
  const color =
    type === "LAN" ? "bg-primary/15 text-primary border-primary/30"
    : type === "Online" ? "bg-success/20 text-success border-success/40"
    : "bg-cyan/15 text-cyan border-cyan/40";
  return <span className={`rounded-sm border px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wider ${color}`}>{type}</span>;
}

function EditDialog({ row, isNew, onChange, onCancel, onSave }: {
  row: Tournament; isNew: boolean;
  onChange: (r: Tournament) => void; onCancel: () => void; onSave: () => void;
}) {
  const set = <K extends keyof Tournament>(k: K, v: Tournament[K]) => onChange({ ...row, [k]: v });
  const base = "mt-1 w-full rounded-sm border border-border bg-background px-2 py-1.5 text-sm";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onCancel}>
      <div className="hud-panel w-full max-w-2xl max-h-[90vh] overflow-y-auto bg-surface" onClick={(e) => e.stopPropagation()}>
        <div className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-bold uppercase tracking-wider">{isNew ? "New tournament" : "Edit tournament"}</h2>
        </div>
        <div className="space-y-5 p-4">
          <section>
            <div className="label-eyebrow mb-2 text-xs text-muted-foreground">Basic info</div>
            <div className="space-y-3">
              <div>
                <label className="label-eyebrow text-xs">Name</label>
                <input className={base} value={row.name} onChange={(e) => set("name", e.target.value)} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="label-eyebrow text-xs">Status</label>
                  <select className={base} value={row.status ?? ""} onChange={(e) => set("status", (e.target.value || undefined) as TournamentStatus | undefined)}>
                    <option value="">Auto (from dates)</option>
                    {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
                <div>
                  <label className="label-eyebrow text-xs">Type</label>
                  <select className={base} value={row.type} onChange={(e) => set("type", e.target.value as TournamentType)}>
                    {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
              </div>
            </div>
          </section>

          <section>
            <div className="label-eyebrow mb-2 text-xs text-muted-foreground">Dates</div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label-eyebrow text-xs">Start date</label>
                <input type="date" className={base} value={row.startDate} onChange={(e) => set("startDate", e.target.value)} />
              </div>
              <div>
                <label className="label-eyebrow text-xs">End date</label>
                <input type="date" className={base} value={row.endDate} onChange={(e) => set("endDate", e.target.value)} />
              </div>
            </div>
          </section>

          <section>
            <div className="label-eyebrow mb-2 text-xs text-muted-foreground">Season & region</div>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <div>
                <label className="label-eyebrow text-xs">Year</label>
                <select className={base} value={row.year} onChange={(e) => set("year", Number(e.target.value))}>
                  {YEARS.map((y) => <option key={y} value={y}>Year {y}</option>)}
                </select>
              </div>
              <div>
                <label className="label-eyebrow text-xs">Split</label>
                <select className={base} value={row.split ?? ""} onChange={(e) => set("split", e.target.value || undefined)}>
                  <option value="">—</option>
                  {SPLITS.map((s) => <option key={s} value={s}>Split {s}</option>)}
                </select>
              </div>
              <div>
                <label className="label-eyebrow text-xs">Stage</label>
                <select className={base} value={row.stage ?? ""} onChange={(e) => set("stage", (e.target.value || undefined) as TournamentStage | undefined)}>
                  <option value="">—</option>
                  {STAGES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <div>
                <label className="label-eyebrow text-xs">Region</label>
                <select className={base} value={row.region} onChange={(e) => set("region", e.target.value as TournamentRegion)}>
                  {REGIONS.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
            </div>
          </section>

          <section>
            <div className="label-eyebrow mb-2 text-xs text-muted-foreground">Links & notes</div>
            <div className="space-y-3">
              <div>
                <label className="label-eyebrow text-xs">Liquipedia URL</label>
                <input
                  type="url"
                  placeholder="https://liquipedia.net/apexlegends/..."
                  className={base}
                  value={row.liquipediaUrl ?? ""}
                  onChange={(e) => set("liquipediaUrl", e.target.value || undefined)}
                />
              </div>
              <div>
                <label className="label-eyebrow text-xs">Description / notes</label>
                <textarea
                  rows={3}
                  className={base}
                  value={row.description ?? ""}
                  onChange={(e) => set("description", e.target.value || undefined)}
                />
              </div>
            </div>
          </section>
        </div>
        <div className="flex justify-end gap-2 border-t border-border bg-surface-2 px-4 py-3">
          <button onClick={onCancel} className="rounded-sm border border-border bg-surface px-3 py-1.5 text-xs uppercase tracking-wider hover:bg-muted">Cancel</button>
          <button onClick={onSave} className="rounded-sm bg-primary px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary-foreground hover:brightness-110">Save</button>
        </div>
      </div>
    </div>
  );
}
