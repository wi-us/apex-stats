import { createFileRoute, Link } from "@tanstack/react-router";
import { Activity, CheckCircle2, XCircle, Loader2, RotateCw, Bug, ExternalLink, History, AlertTriangle, Palette, Shapes, Video, Database, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { fetchAlgsBundle } from "@/lib/algs-fetchers";
import { replaceFromAlgs, useAdminStore } from "@/lib/admin-store";

export const Route = createFileRoute("/admin/")({ component: AdminDashboard });

const toolGroups = [
  {
    label: "Data",
    items: [
      { to: "/admin/tournaments", title: "Tournaments", desc: "Series & events" },
      { to: "/admin/matches",     title: "Matches",     desc: "Upload VODs, link screenshots, manage games" },
      { to: "/admin/maps",        title: "Maps",        desc: "Map pool" },
      { to: "/admin/teams",       title: "Teams",       desc: "Rosters & colors" },
    ],
  },
  {
    label: "Calibration",
    items: [
      { to: "/admin/hsv",      title: "HSV",          desc: "Team color calibration" },
      { to: "/admin/zones",    title: "HUD Zones",    desc: "Markup HUD areas on a 1920×1080 frame" },
      { to: "/admin/polygons", title: "Map Polygons", desc: "Forbidden / safe map areas" },
      { to: "/admin/camera",   title: "Camera",       desc: "Observer camera tracking" },
    ],
  },
  {
    label: "Analysis",
    items: [
      { to: "/admin/processes", title: "Processes",       desc: "Video analysis & tracking jobs" },
      { to: "/admin/minimap",   title: "Minimap Locator", desc: "Detect & align minimap to full map" },
    ],
  },
  {
    label: "System",
    items: [
      { to: "/admin/schema",   title: "Database Schema", desc: "DB schema editor" },
      { to: "/admin/diagrams", title: "Diagrams",        desc: "Flowcharts for reports" },
    ],
  },
] as const;

type TaskStatus = "processing" | "completed" | "failed" | "queued";

const activeTasks: { id: string; title: string; subtitle: string; status: TaskStatus; progress?: number; icon: typeof Video }[] = [
  { id: "job-124", title: "Video analysis · Game 2", subtitle: "Storm Point · ALGS Pro League", status: "processing", progress: 42, icon: Video },
  { id: "job-123", title: "Minimap Locator · Game 1", subtitle: "World's Edge", status: "completed", icon: Shapes },
  { id: "job-122", title: "Camera calibration · TSM POV", subtitle: "frame 00:14:32", status: "failed", icon: Palette },
  { id: "job-121", title: "HSV profile · Group B", subtitle: "queued · 6 teams", status: "queued", progress: 0, icon: Palette },
];

const recentActions: { id: string; text: string; time: string; kind: "info" | "ok" | "warn" | "err" }[] = [
  { id: "a1", text: "Updated HSV profile for Storm Point", time: "2 min ago", kind: "info" },
  { id: "a2", text: "Completed video job #124", time: "9 min ago", kind: "ok" },
  { id: "a3", text: "Added polygon forbidden_21 on World's Edge", time: "23 min ago", kind: "info" },
  { id: "a4", text: "Error: no_minimap detected on 18 frames", time: "41 min ago", kind: "err" },
  { id: "a5", text: "Camera calibration drift > 4px on TSM POV", time: "1 hr ago", kind: "warn" },
];

function AdminDashboard() {
  return (
    <div className="flex h-full flex-col overflow-auto">
      <header className="flex h-14 shrink-0 items-center border-b border-border bg-surface px-6">
        <h1 className="text-sm font-bold uppercase tracking-wider">Dashboard</h1>
      </header>


      <div className="flex-1 overflow-auto p-6 space-y-8">
        <AlgsSyncCard />

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <section className="xl:col-span-2">
            <SectionHead icon={Activity} title="Active processes" hint={`${activeTasks.filter(t => t.status === "processing" || t.status === "queued").length} running`} />
            <div className="mt-3 space-y-2">
              {activeTasks.map((t) => <TaskRow key={t.id} task={t} />)}
            </div>
          </section>

          <section>
            <SectionHead icon={History} title="Recent actions" hint={`${recentActions.length} events`} />
            <div className="mt-3 hud-panel divide-y divide-border">
              {recentActions.map((a) => <ActionRow key={a.id} action={a} />)}
            </div>
          </section>
        </div>

        {toolGroups.map((g) => (
          <section key={g.label}>
            <h2 className="mb-3 label-eyebrow">{g.label} · {g.items.length}</h2>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
              {g.items.map((t) => (
                <Link key={t.to} to={t.to as "/admin/matches"}
                  className="group hud-panel block p-4 transition-colors hover:border-primary/40 hover:bg-surface-2">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-bold uppercase tracking-wider">{t.title}</h3>
                    <span className="text-mono text-primary opacity-0 transition-opacity group-hover:opacity-100">→</span>
                  </div>
                  <p className="mt-1.5 text-xs leading-snug text-muted-foreground">{t.desc}</p>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}


function SectionHead({ icon: Icon, title, hint }: { icon: typeof Activity; title: string; hint?: string }) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <Icon className="h-3.5 w-3.5 text-primary" />
        <h2 className="label-eyebrow">{title}</h2>
      </div>
      {hint && <span className="text-mono text-xs text-muted-foreground">{hint}</span>}
    </div>
  );
}

const STATUS_META: Record<TaskStatus, { label: string; cls: string; icon: typeof Activity }> = {
  processing: { label: "Processing", cls: "text-primary border-primary/40 bg-primary/10",          icon: Loader2 },
  completed:  { label: "Completed",  cls: "text-emerald-500 border-emerald-500/40 bg-emerald-500/10", icon: CheckCircle2 },
  failed:     { label: "Failed",     cls: "text-red-500 border-red-500/40 bg-red-500/10",          icon: XCircle },
  queued:     { label: "Queued",     cls: "text-muted-foreground border-border bg-muted/40",       icon: Activity },
};

function TaskRow({ task }: { task: typeof activeTasks[number] }) {
  const meta = STATUS_META[task.status];
  const StatusIcon = meta.icon;
  const Icon = task.icon;
  return (
    <div className="hud-panel p-3">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-sm border border-border bg-surface-2">
          <Icon className="h-4 w-4 text-muted-foreground" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <div className="truncate text-sm font-semibold">{task.title}</div>
            <span className={`inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-bold uppercase tracking-wider ${meta.cls}`}>
              <StatusIcon className={`h-3 w-3 ${task.status === "processing" ? "animate-spin" : ""}`} />
              {meta.label}
              {typeof task.progress === "number" && task.status === "processing" ? ` ${task.progress}%` : ""}
            </span>
          </div>
          <div className="mt-0.5 truncate text-muted-foreground text-sm">{task.subtitle}</div>

          {task.status === "processing" && typeof task.progress === "number" && (
            <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-primary transition-all" style={{ width: `${task.progress}%` }} />
            </div>
          )}

          <div className="mt-2 flex items-center gap-1.5">
            <Link to="/admin/processes" className="text-mono inline-flex items-center gap-1 rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted">
              <ExternalLink className="h-3 w-3" /> Open
            </Link>
            {task.status === "failed" && (
              <button className="text-mono inline-flex items-center gap-1 rounded-sm border border-primary/40 bg-primary/10 px-2 py-1 text-xs uppercase tracking-wider text-primary hover:bg-primary/20">
                <RotateCw className="h-3 w-3" /> Retry
              </button>
            )}
            <button className="text-mono inline-flex items-center gap-1 rounded-sm border border-border bg-surface-2 px-2 py-1 text-xs uppercase tracking-wider hover:bg-muted">
              <Bug className="h-3 w-3" /> Debug
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ActionRow({ action }: { action: typeof recentActions[number] }) {
  const meta = {
    info: { color: "text-primary",        Icon: Activity },
    ok:   { color: "text-emerald-500",    Icon: CheckCircle2 },
    warn: { color: "text-amber-500",      Icon: AlertTriangle },
    err:  { color: "text-red-500",        Icon: XCircle },
  }[action.kind];
  const Icon = meta.Icon;
  return (
    <div className="flex items-start gap-2.5 px-3 py-2">
      <Icon className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${meta.color}`} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs">{action.text}</div>
        <div className="text-mono text-xs text-muted-foreground">{action.time}</div>
      </div>
    </div>
  );
}

const SYNC_KEY = "admin:algsSync:at";
const STALE_MS = 60 * 60 * 1000; // 1h

function AlgsSyncCard() {
  const { teams, tournaments, matches, customMaps } = useAdminStore();
  const [status, setStatus] = useState<"idle" | "loading" | "ok" | "err">("idle");
  const [error, setError] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<number | null>(() => {
    if (typeof window === "undefined") return null;
    const raw = window.localStorage.getItem(SYNC_KEY);
    return raw ? Number(raw) : null;
  });
  const ranOnce = useRef(false);

  const doSync = async () => {
    setStatus("loading");
    setError(null);
    try {
      const bundle = await fetchAlgsBundle();
      replaceFromAlgs(bundle);
      const ts = bundle.fetchedAt;
      window.localStorage.setItem(SYNC_KEY, String(ts));
      setLastSync(ts);
      setStatus("ok");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("err");
    }
  };

  // Auto-sync once per session if data is stale.
  useEffect(() => {
    if (ranOnce.current) return;
    ranOnce.current = true;
    // Always auto-sync on dashboard mount (manual button still available as fallback).
    void doSync();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const ago = lastSync ? formatAgo(Date.now() - lastSync) : "never";

  return (
    <section className="hud-panel p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-sm border border-border bg-surface-2">
            <Database className="h-4 w-4 text-primary" />
          </div>
          <div>
            <div className="text-sm font-bold uppercase tracking-wider">ALGS Data Sync</div>
            <div className="text-xs text-muted-foreground">
              Tournaments, Teams, Matches, Maps · last sync: <span className="text-mono">{ago}</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-mono text-xs text-muted-foreground">
            {tournaments.length} tour · {teams.length} teams · {matches.length} matches · {customMaps.length} maps
          </div>
          <button
            onClick={doSync}
            disabled={status === "loading"}
            className="text-mono inline-flex items-center gap-1.5 rounded-sm border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary hover:bg-primary/20 disabled:opacity-60"
          >
            <RefreshCw className={`h-3 w-3 ${status === "loading" ? "animate-spin" : ""}`} />
            {status === "loading" ? "Syncing…" : "Sync now"}
          </button>
        </div>
      </div>
      {status === "err" && error && (
        <div className="mt-2 rounded-sm border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          Sync failed: {error}
        </div>
      )}
      {status === "ok" && (
        <div className="mt-2 text-xs text-emerald-400">Synced from ALGS database.</div>
      )}
    </section>
  );
}

function formatAgo(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}
