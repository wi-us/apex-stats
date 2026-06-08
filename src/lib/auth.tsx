import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { Session, User } from "@supabase/supabase-js";
import { supabase } from "@/integrations/supabase/client";
import { browserSignIn, browserSignOut, getBrowserCurrentUser } from "@/lib/browser-auth-store";
import { isBrowserAuthMode, isSupabaseEnabled } from "@/lib/runtime-config";

export type AppRole = "user" | "operator" | "administrator";

type AuthCtx = {
  user: User | null;
  session: Session | null;
  role: AppRole | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<{ error?: string }>;
  signOut: () => Promise<void>;
};

const Ctx = createContext<AuthCtx | null>(null);

const RANK: Record<AppRole, number> = { user: 1, operator: 2, administrator: 3 };
const ROLE_CACHE_PREFIX = "auth:role:";
const LOCAL_SESSION_CHECK_MS = 15_000;

export function hasAtLeast(role: AppRole | null, min: AppRole) {
  return !!role && RANK[role] >= RANK[min];
}

function isAppRole(value: unknown): value is AppRole {
  return value === "user" || value === "operator" || value === "administrator";
}

function roleCacheKey(uid: string) {
  return `${ROLE_CACHE_PREFIX}${uid}`;
}

function readCachedRole(uid: string): AppRole | null {
  if (typeof window === "undefined") return null;
  try {
    const value = window.localStorage.getItem(roleCacheKey(uid));
    return isAppRole(value) ? value : null;
  } catch {
    return null;
  }
}

function writeCachedRole(uid: string, nextRole: AppRole) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(roleCacheKey(uid), nextRole);
  } catch {
    /* storage can be unavailable in private contexts */
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [role, setRole] = useState<AppRole | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isSupabaseEnabled) {
      loadLocalSession().finally(() => setLoading(false));
      const interval = window.setInterval(() => {
        void loadLocalSession();
      }, LOCAL_SESSION_CHECK_MS);
      return () => window.clearInterval(interval);
    }
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => {
      setSession(s);
      setUser(s?.user ?? null);
      if (s?.user) {
        const cachedRole = readCachedRole(s.user.id);
        if (cachedRole) setRole(cachedRole);
        // defer to avoid recursion
        setTimeout(() => {
          void fetchRole(s.user.id);
        }, 0);
      } else {
        setRole(null);
      }
    });
    supabase.auth
      .getSession()
      .then(({ data }) => {
        setSession(data.session);
        setUser(data.session?.user ?? null);
        if (data.session?.user) {
          const cachedRole = readCachedRole(data.session.user.id);
          if (cachedRole) setRole(cachedRole);
          fetchRole(data.session.user.id).finally(() => setLoading(false));
        } else {
          setLoading(false);
        }
      })
      .catch((error) => {
        console.warn("[auth] Supabase session load failed", error);
        setSession(null);
        setUser(null);
        setRole(null);
        setLoading(false);
      });
    return () => sub.subscription.unsubscribe();
  }, []);

  async function loadLocalSession() {
    if (isBrowserAuthMode) {
      const browserUser = getBrowserCurrentUser();
      if (!browserUser) {
        setSession(null);
        setUser(null);
        setRole(null);
        return;
      }
      setSession(null);
      setUser({
        id: browserUser.id,
        email: browserUser.email,
        app_metadata: {},
        user_metadata: { display_name: browserUser.display_name ?? browserUser.email },
        aud: "authenticated",
        created_at: browserUser.created_at,
      } as User);
      setRole(browserUser.role);
      return;
    }
    try {
      const response = await fetch("/api/local-auth/me", { cache: "no-store" });
      const data = await response.json();
      if (!data.user) {
        setSession(null);
        setUser(null);
        setRole(null);
        return;
      }
      setSession(null);
      setUser({
        id: data.user.id,
        email: data.user.email,
        app_metadata: {},
        user_metadata: { display_name: data.user.display_name ?? data.user.email },
        aud: "authenticated",
        created_at: data.user.created_at,
      } as User);
      setRole(data.user.role);
    } catch (error) {
      console.warn("[auth] Local session load failed", error);
      setSession(null);
      setUser(null);
      setRole(null);
    }
  }

  async function fetchRole(uid: string) {
    try {
      const { data, error } = await supabase
        .from("user_roles")
        .select("role")
        .eq("user_id", uid);
      if (error) throw error;
      const roles = (data ?? []).map((r) => r.role as AppRole);
      const best = roles.includes("administrator")
        ? "administrator"
        : roles.includes("operator")
          ? "operator"
          : roles.includes("user")
            ? "user"
            : null;
      if (best) writeCachedRole(uid, best);
      setRole(best);
    } catch (error) {
      console.warn("[auth] Supabase role load failed", error);
      setRole((current) => current ?? readCachedRole(uid) ?? (import.meta.env.DEV ? "administrator" : null));
    }
  }

  const signIn: AuthCtx["signIn"] = async (email, password) => {
    if (!isSupabaseEnabled) {
      if (isBrowserAuthMode) {
        const result = browserSignIn(email, password);
        if (result.error) return { error: result.error };
        await loadLocalSession();
        setLoading(false);
        return {};
      }
      try {
        const response = await fetch("/api/local-auth/login", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ email, password }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) return { error: data.error ?? "Authentication request failed" };
        await loadLocalSession();
        setLoading(false);
        return {};
      } catch (error) {
        const message = error instanceof Error ? error.message : "Authentication request failed";
        return { error: message };
      }
    }
    try {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      return error ? { error: error.message } : {};
    } catch (error) {
      const message = error instanceof Error ? error.message : "Authentication request failed";
      return { error: message };
    }
  };
  const signOut = async () => {
    if (!isSupabaseEnabled) {
      if (isBrowserAuthMode) {
        browserSignOut();
        setSession(null);
        setUser(null);
        setRole(null);
        return;
      }
      await fetch("/api/local-auth/logout", { method: "POST" }).catch(() => {});
      setSession(null);
      setUser(null);
      setRole(null);
      return;
    }
    await supabase.auth.signOut();
  };

  return (
    <Ctx.Provider value={{ user, session, role, loading, signIn, signOut }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth must be used inside AuthProvider");
  return c;
}
