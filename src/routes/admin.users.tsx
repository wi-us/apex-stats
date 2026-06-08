import { createFileRoute } from "@tanstack/react-router";
import { useServerFn } from "@tanstack/react-start";
import { useEffect, useState, type FormEvent } from "react";
import { RouteGuard } from "@/components/auth/RouteGuard";
import { supabase } from "@/integrations/supabase/client";
import {
  listUserAccounts,
  createUserAccount,
  setUserRole,
  deleteUserAccount,
} from "@/lib/admin-users.functions";
import {
  createInvite,
  listInvites,
  deleteInvite,
} from "@/lib/invites.functions";
import type { AppRole } from "@/lib/auth";
import { isSupabaseEnabled } from "@/lib/runtime-config";

export const Route = createFileRoute("/admin/users")({
  component: () => (
    <RouteGuard min="administrator">
      <UsersPage />
    </RouteGuard>
  ),
});

type Row = {
  id: string;
  email: string | null;
  display_name: string | null;
  created_at: string;
  role: AppRole | null;
};

function UsersPage() {
  const create = useServerFn(createUserAccount);
  const list = useServerFn(listUserAccounts);
  const setRole = useServerFn(setUserRole);
  const del = useServerFn(deleteUserAccount);

  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // create form
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRoleVal] = useState<AppRole>("user");
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    if (!isSupabaseEnabled) {
      try {
        const r = await list();
        setRows((r.users ?? []) as Row[]);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load");
      }
      setLoading(false);
      return;
    }
    const [{ data: profiles, error: pErr }, { data: roles, error: rErr }] = await Promise.all([
      supabase.from("profiles").select("id, email, display_name, created_at"),
      supabase.from("user_roles").select("user_id, role"),
    ]);
    if (pErr || rErr) {
      setError(pErr?.message ?? rErr?.message ?? "Failed to load");
      setLoading(false);
      return;
    }
    const roleMap = new Map<string, AppRole>();
    for (const r of roles ?? []) {
      const cur = roleMap.get(r.user_id);
      const next = r.role as AppRole;
      const rank = { user: 1, operator: 2, administrator: 3 } as const;
      if (!cur || rank[next] > rank[cur]) roleMap.set(r.user_id, next);
    }
    setRows(
      (profiles ?? []).map((p) => ({
        ...p,
        role: roleMap.get(p.id) ?? null,
      })),
    );
    setLoading(false);
  }

  useEffect(() => {
    load();
  }, []);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await create({
        data: {
          email,
          password,
          display_name: displayName || undefined,
          role,
        },
      });
      setEmail("");
      setPassword("");
      setDisplayName("");
      setRoleVal("user");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  async function onChangeRole(userId: string, next: AppRole) {
    try {
      await setRole({ data: { user_id: userId, role: next } });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  async function onDelete(userId: string, email: string | null) {
    if (!confirm(`Delete user ${email ?? userId}? This cannot be undone.`)) return;
    try {
      await del({ data: { user_id: userId } });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <div className="flex h-full flex-col overflow-auto">
      <header className="flex h-14 shrink-0 items-center border-b border-border bg-surface px-6">
        <h1 className="text-sm font-bold uppercase tracking-wider">Users</h1>
      </header>

      <div className="space-y-6 p-6">
        <section className="hud-panel p-4">
          <h2 className="label-eyebrow mb-3">Create account</h2>
          <form onSubmit={onCreate} className="grid gap-3 md:grid-cols-5">
            <Field label="Email">
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="h-9 w-full rounded-sm border border-border bg-surface-2 px-2 text-xs outline-none focus:border-primary"
              />
            </Field>
            <Field label="Password">
              <input
                type="text"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="h-9 w-full rounded-sm border border-border bg-surface-2 px-2 text-xs outline-none focus:border-primary"
              />
            </Field>
            <Field label="Display name">
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="h-9 w-full rounded-sm border border-border bg-surface-2 px-2 text-xs outline-none focus:border-primary"
              />
            </Field>
            <Field label="Role">
              <select
                value={role}
                onChange={(e) => setRoleVal(e.target.value as AppRole)}
                className="h-9 w-full rounded-sm border border-border bg-surface-2 px-2 text-xs outline-none focus:border-primary"
              >
                <option value="user">User</option>
                <option value="operator">Operator</option>
                <option value="administrator">Administrator</option>
              </select>
            </Field>
            <div className="flex items-end">
              <button
                type="submit"
                disabled={busy}
                className="w-full rounded-sm bg-primary px-3 py-2 text-xs font-bold uppercase tracking-wider text-primary-foreground hover:opacity-90 disabled:opacity-50"
              >
                {busy ? "Creating…" : "Create"}
              </button>
            </div>
          </form>
          {error && (
            <div className="mt-3 rounded-sm border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </div>
          )}
        </section>

        <section className="hud-panel">
          <div className="flex items-center justify-between border-b border-border px-4 py-2">
            <h2 className="label-eyebrow">Accounts</h2>
            <span className="text-mono text-xs text-muted-foreground">
              {loading ? "loading…" : `${rows.length} total`}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-surface-2 text-left label-eyebrow text-xs">
                <tr>
                  <th className="px-3 py-2">Email</th>
                  <th className="px-3 py-2">Display name</th>
                  <th className="px-3 py-2">Role</th>
                  <th className="px-3 py-2">Created</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-t border-border">
                    <td className="px-3 py-2 font-mono text-xs">{r.email}</td>
                    <td className="px-3 py-2">{r.display_name ?? "—"}</td>
                    <td className="px-3 py-2">
                      <select
                        value={r.role ?? "user"}
                        onChange={(e) => onChangeRole(r.id, e.target.value as AppRole)}
                        className="h-7 w-full rounded-sm border border-border bg-surface-2 px-2 text-xs outline-none focus:border-primary"
                      >
                        <option value="user">User</option>
                        <option value="operator">Operator</option>
                        <option value="administrator">Administrator</option>
                      </select>
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {new Date(r.created_at).toLocaleString()}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button
                        onClick={() => onDelete(r.id, r.email)}
                        className="rounded-sm border border-destructive/40 px-2 py-1 text-xs uppercase tracking-wider text-destructive hover:bg-destructive/10"
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
                {!loading && rows.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-3 py-6 text-center text-muted-foreground">
                      No accounts yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <InvitesTab />
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="space-y-1">
      <div className="label-eyebrow text-xs">{label}</div>
      {children}
    </label>
  );
}

type InviteRow = {
  id: string;
  email: string;
  role: AppRole;
  token: string;
  expires_at: string | null;
  used_at: string | null;
  created_at: string;
  max_uses?: number;
  uses_count?: number;
};

function InvitesTab() {
  const invCreate = useServerFn(createInvite);
  const invList = useServerFn(listInvites);
  const invDelete = useServerFn(deleteInvite);
  const [rows, setRows] = useState<InviteRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [role, setRole] = useState<AppRole>("user");
  const [days, setDays] = useState(7);
  const [neverExpires, setNeverExpires] = useState(false);
  const [maxUses, setMaxUses] = useState(1);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const isLocalInviteOrigin =
    typeof window !== "undefined" &&
    (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1");

  async function load() {
    setLoading(true);
    try {
      const r = await invList();
      setRows((r.invites ?? []) as InviteRow[]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await invCreate({ data: { role, expires_in_days: days, never_expires: neverExpires, max_uses: maxUses } });
      setRole("user");
      setDays(7);
      setNeverExpires(false);
      setMaxUses(1);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(id: string) {
    if (!confirm("Revoke this invite?")) return;
    try {
      await invDelete({ data: { id } });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  function linkFor(token: string) {
    if (typeof window === "undefined") return `/accept-invite?token=${token}`;
    return `${window.location.origin}/accept-invite?token=${token}`;
  }

  async function copyLink(token: string) {
    try {
      await navigator.clipboard.writeText(linkFor(token));
      setCopied(token);
      setTimeout(() => setCopied((c) => (c === token ? null : c)), 1500);
    } catch {
      // ignore
    }
  }

  function statusOf(r: InviteRow): { label: string; cls: string } {
    const used = r.uses_count ?? 0;
    const max = r.max_uses ?? 1;
    if (used >= max) return { label: "Used up", cls: "text-muted-foreground" };
    if (r.expires_at && new Date(r.expires_at).getTime() < Date.now())
      return { label: "Expired", cls: "text-destructive" };
    return { label: "Active", cls: "text-primary" };
  }

  return (
    <div className="space-y-6">
      <section className="hud-panel p-4">
        <h2 className="label-eyebrow mb-3">Create invite link</h2>
        <p className="mb-3 text-xs text-muted-foreground">
          Anyone with this link can self-register up to the configured number of times. They choose their own email and password.
        </p>
        {isLocalInviteOrigin && (
          <div className="mb-3 rounded-sm border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-200">
            Local invites are stored in the local auth database. For public links, create the invite on https://apex.wi-us.ru/admin/users.
          </div>
        )}
        <form onSubmit={onCreate} className="grid gap-3 md:grid-cols-5">
          <Field label="Role">
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as AppRole)}
              className="h-9 w-full rounded-sm border border-border bg-surface-2 px-2 text-xs outline-none focus:border-primary"
            >
              <option value="user">User</option>
              <option value="operator">Operator</option>
              <option value="administrator">Administrator</option>
            </select>
          </Field>
          <Field label="Max uses">
            <input
              type="number"
              min={1}
              max={1000}
              value={maxUses}
              onChange={(e) => setMaxUses(Number(e.target.value) || 1)}
              className="h-9 w-full rounded-sm border border-border bg-surface-2 px-2 text-xs outline-none focus:border-primary"
            />
          </Field>
          <Field label="Expires (days)">
            <div className="flex">
              <input
                type="number"
                min={1}
                max={30}
                value={days}
                disabled={neverExpires}
                onChange={(e) => setDays(Number(e.target.value) || 7)}
                className="h-9 min-w-0 flex-1 rounded-l-sm border border-border bg-surface-2 px-2 text-xs outline-none focus:border-primary disabled:opacity-50"
              />
              <button
                type="button"
                aria-pressed={neverExpires}
                title="Never expires"
                onClick={() => setNeverExpires((value) => !value)}
                className={
                  "h-9 w-10 rounded-r-sm border border-l-0 border-border text-base font-bold " +
                  (neverExpires ? "bg-primary text-primary-foreground" : "bg-surface-2 text-muted-foreground hover:text-foreground")
                }
              >
                ∞
              </button>
            </div>
          </Field>
          <div className="flex items-end md:col-span-2">
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-sm bg-primary px-3 py-2 text-xs font-bold uppercase tracking-wider text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "Creating…" : "Create invite"}
            </button>
          </div>
        </form>
        {error && (
          <div className="mt-3 rounded-sm border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </div>
        )}
      </section>

      <section className="hud-panel">
        <div className="flex items-center justify-between border-b border-border px-4 py-2">
          <h2 className="label-eyebrow">Invites</h2>
          <span className="text-mono text-xs text-muted-foreground">
            {loading ? "loading…" : `${rows.length} total`}
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-surface-2 text-left label-eyebrow text-xs">
              <tr>
                <th className="px-3 py-2">Role</th>
                <th className="px-3 py-2">Uses</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Expires</th>
                <th className="px-3 py-2">Link</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const s = statusOf(r);
                return (
                  <tr key={r.id} className="border-t border-border">
                    <td className="px-3 py-2">{r.role}</td>
                    <td className="px-3 py-2 text-mono">
                      {(r.uses_count ?? 0)} / {(r.max_uses ?? 1)}
                    </td>
                    <td className={`px-3 py-2 font-bold ${s.cls}`}>{s.label}</td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {r.expires_at ? new Date(r.expires_at).toLocaleString() : "Never"}
                    </td>
                    <td className="px-3 py-2">
                      <button
                        onClick={() => copyLink(r.token)}
                        disabled={(r.uses_count ?? 0) >= (r.max_uses ?? 1)}
                        className="rounded-sm border border-border px-2 py-1 text-xs uppercase tracking-wider hover:bg-surface-2 disabled:opacity-40"
                      >
                        {copied === r.token ? "Copied!" : "Copy link"}
                      </button>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button
                        onClick={() => onDelete(r.id)}
                        className="rounded-sm border border-destructive/40 px-2 py-1 text-xs uppercase tracking-wider text-destructive hover:bg-destructive/10"
                      >
                        Revoke
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!loading && rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-3 py-6 text-center text-muted-foreground">
                    No invites yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
