import type { AppRole } from "@/lib/auth";

type BrowserUser = {
  id: string;
  email: string;
  display_name: string | null;
  password: string;
  role: AppRole;
  created_at: string;
  session_version: number;
};

type BrowserInvite = {
  id: string;
  token: string;
  email: string | null;
  role: AppRole;
  expires_at: string | null;
  used_at: string | null;
  created_at: string;
  max_uses: number;
  uses_count: number;
};

type BrowserSession = {
  user_id: string;
  session_version: number;
};

const USERS_KEY = "apex:browser-auth:users";
const INVITES_KEY = "apex:browser-auth:invites";
const SESSION_KEY = "apex:browser-auth:session";

const DEFAULT_ADMIN_EMAIL = "admin@apex.local";
const DEFAULT_ADMIN_PASSWORD = "admin12345";

function nowIso() {
  return new Date().toISOString();
}

function randomId(prefix: string) {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;
  }
  return `${prefix}_${Math.random().toString(16).slice(2)}${Date.now().toString(16)}`;
}

function toBase64Url(value: string) {
  return btoa(value).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function fromBase64Url(value: string) {
  const padded = value.replaceAll("-", "+").replaceAll("_", "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  return atob(padded);
}

function makeInviteToken(invite: Omit<BrowserInvite, "token" | "used_at" | "uses_count">) {
  return `demo_${toBase64Url(JSON.stringify({
    id: invite.id,
    email: invite.email,
    role: invite.role,
    expires_at: invite.expires_at,
    created_at: invite.created_at,
    max_uses: invite.max_uses,
  }))}`;
}

function decodeInviteToken(token: unknown): BrowserInvite | null {
  if (typeof token !== "string" || !token.startsWith("demo_")) return null;
  try {
    const data = JSON.parse(fromBase64Url(token.slice(5))) as Partial<BrowserInvite>;
    if (!data.id || !isRole(data.role) || typeof data.created_at !== "string") return null;
    return {
      id: data.id,
      token,
      email: typeof data.email === "string" ? data.email : null,
      role: data.role,
      expires_at: typeof data.expires_at === "string" ? data.expires_at : null,
      used_at: null,
      created_at: data.created_at,
      max_uses: Math.max(1, Math.min(1000, Number(data.max_uses) || 1)),
      uses_count: 0,
    };
  } catch {
    return null;
  }
}

function readJson<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson<T>(key: string, value: T) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(key, JSON.stringify(value));
}

function isRole(value: unknown): value is AppRole {
  return value === "user" || value === "operator" || value === "administrator";
}

function ensureSeeded() {
  const users = readJson<BrowserUser[]>(USERS_KEY, []);
  if (users.some((user) => user.email.toLowerCase() === DEFAULT_ADMIN_EMAIL)) return users;
  const seeded = [
    ...users,
    {
      id: randomId("usr"),
      email: DEFAULT_ADMIN_EMAIL,
      display_name: "Local administrator",
      password: DEFAULT_ADMIN_PASSWORD,
      role: "administrator" as const,
      created_at: nowIso(),
      session_version: 1,
    },
  ];
  writeJson(USERS_KEY, seeded);
  return seeded;
}

function readUsers() {
  return ensureSeeded();
}

function writeUsers(users: BrowserUser[]) {
  writeJson(USERS_KEY, users);
}

function readInvites() {
  return readJson<BrowserInvite[]>(INVITES_KEY, []);
}

function writeInvites(invites: BrowserInvite[]) {
  writeJson(INVITES_KEY, invites);
}

function publicUser(user: BrowserUser) {
  return {
    id: user.id,
    email: user.email,
    display_name: user.display_name,
    role: user.role,
    created_at: user.created_at,
  };
}

function getSession() {
  return readJson<BrowserSession | null>(SESSION_KEY, null);
}

function writeSession(session: BrowserSession | null) {
  if (typeof window === "undefined") return;
  if (!session) window.localStorage.removeItem(SESSION_KEY);
  else writeJson(SESSION_KEY, session);
}

export function getBrowserCurrentUser() {
  const session = getSession();
  if (!session) return null;
  const user = readUsers().find((item) => item.id === session.user_id) ?? null;
  if (!user || user.session_version !== session.session_version) {
    writeSession(null);
    return null;
  }
  return publicUser(user);
}

export function browserSignIn(email: string, password: string) {
  const user = readUsers().find((item) => item.email.toLowerCase() === email.trim().toLowerCase()) ?? null;
  if (!user || user.password !== password) {
    return { error: "Invalid email or password." };
  }
  writeSession({ user_id: user.id, session_version: user.session_version });
  return { user: publicUser(user) };
}

export function browserSignOut() {
  writeSession(null);
}

export function browserListUsers() {
  return { users: readUsers().map(publicUser).sort((a, b) => a.email.localeCompare(b.email)) };
}

export function browserCreateUser(data: any) {
  const email = String(data?.email ?? "").trim().toLowerCase();
  const password = String(data?.password ?? "");
  if (!email.includes("@")) throw new Error("Valid email is required.");
  if (password.length < 8) throw new Error("Password must be at least 8 characters.");
  if (!isRole(data?.role)) throw new Error("Invalid role.");
  const users = readUsers();
  if (users.some((user) => user.email.toLowerCase() === email)) throw new Error("User already exists.");
  const user: BrowserUser = {
    id: randomId("usr"),
    email,
    display_name: typeof data.display_name === "string" && data.display_name.trim() ? data.display_name.trim() : null,
    password,
    role: data.role,
    created_at: nowIso(),
    session_version: 1,
  };
  writeUsers([...users, user]);
  return { user: publicUser(user), id: user.id };
}

export function browserSetUserRole(data: any) {
  if (!isRole(data?.role)) throw new Error("Invalid role.");
  const users = readUsers();
  const user = users.find((item) => item.id === data.user_id);
  if (!user) throw new Error("User not found.");
  user.role = data.role;
  user.session_version += 1;
  writeUsers(users);
  return { ok: true };
}

export function browserDeleteUser(data: any) {
  const current = getBrowserCurrentUser();
  if (current?.id === data?.user_id) throw new Error("You cannot delete your own account.");
  const users = readUsers();
  const next = users.filter((user) => user.id !== data?.user_id);
  if (next.length === users.length) throw new Error("User not found.");
  writeUsers(next);
  return { ok: true };
}

export function browserCreateInvite(data: any) {
  if (!isRole(data?.role)) throw new Error("Invalid role.");
  const neverExpires = data?.never_expires === true;
  const expiresDays = Math.max(1, Math.min(30, Number(data?.expires_in_days) || 7));
  const maxUses = Math.max(1, Math.min(1000, Number(data?.max_uses) || 1));
  const inviteBase = {
    id: randomId("inv"),
    email: typeof data?.email === "string" && data.email.trim() ? data.email.trim().toLowerCase() : null,
    role: data.role,
    expires_at: neverExpires ? null : new Date(Date.now() + expiresDays * 86400000).toISOString(),
    created_at: nowIso(),
    max_uses: maxUses,
  };
  const invite: BrowserInvite = {
    ...inviteBase,
    token: makeInviteToken(inviteBase),
    used_at: null,
    uses_count: 0,
  };
  writeInvites([invite, ...readInvites()]);
  return { invite };
}

export function browserListInvites() {
  return { invites: readInvites().sort((a, b) => b.created_at.localeCompare(a.created_at)) };
}

export function browserDeleteInvite(data: any) {
  writeInvites(readInvites().filter((invite) => invite.id !== data?.id));
  return { ok: true };
}

export function browserLookupInvite(data: any) {
  const invite = readInvites().find((item) => item.token === data?.token) ?? decodeInviteToken(data?.token);
  if (!invite) return { status: "invalid" };
  if (invite.uses_count >= invite.max_uses) return { status: "used" };
  if (invite.expires_at && Date.parse(invite.expires_at) < Date.now()) return { status: "expired" };
  return {
    status: "ok",
    email: invite.email,
    role: invite.role,
    remaining: invite.max_uses - invite.uses_count,
  };
}

export function browserAcceptInvite(data: any) {
  const invites = readInvites();
  const storedInvite = invites.find((item) => item.token === data?.token) ?? null;
  const invite = storedInvite ?? decodeInviteToken(data?.token);
  if (!invite) throw new Error("Invalid invite token.");
  if (invite.uses_count >= invite.max_uses) throw new Error("This invite has reached its usage limit.");
  if (invite.expires_at && Date.parse(invite.expires_at) < Date.now()) throw new Error("This invite has expired.");
  const email = String(data?.email ?? "").trim().toLowerCase();
  if (!email.includes("@")) throw new Error("Valid email is required.");
  if (invite.email && invite.email !== email) throw new Error("This invite link is bound to a different email address.");
  const password = String(data?.password ?? "");
  if (password.length < 8) throw new Error("Password must be at least 8 characters.");
  if (readUsers().some((user) => user.email.toLowerCase() === email)) throw new Error("User already exists.");
  browserCreateUser({
    email,
    password,
    display_name: data?.display_name,
    role: invite.role,
  });
  invite.uses_count += 1;
  if (invite.uses_count >= invite.max_uses) invite.used_at = nowIso();
  if (storedInvite) {
    writeInvites(invites);
  } else {
    writeInvites([invite, ...invites]);
  }
  return { ok: true, email };
}
