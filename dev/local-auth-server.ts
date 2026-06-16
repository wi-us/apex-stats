import type { Plugin, ViteDevServer } from "vite";
import { createHmac, pbkdf2Sync, randomBytes, timingSafeEqual } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

type AppRole = "user" | "operator" | "administrator";

type LocalUser = {
  id: string;
  email: string;
  displayName: string | null;
  passwordHash: string;
  salt: string;
  role: AppRole;
  createdAt: string;
  sessionVersion: number;
};

type LocalInvite = {
  id: string;
  token: string;
  email: string | null;
  role: AppRole;
  expiresAt: string | null;
  usedAt: string | null;
  createdBy: string;
  createdAt: string;
  maxUses: number;
  usesCount: number;
};

type LocalAuthDb = {
  version: 1;
  jwtSecret: string;
  users: LocalUser[];
  invites: LocalInvite[];
};

type LocalSettingsDb = {
  version: 1;
  settings: Record<string, { value: unknown; updatedAt: string; updatedBy: string }>;
};

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const DB_PATH = join(ROOT, "data", "local-auth.json");
const SETTINGS_PATH = join(ROOT, "data", "admin-settings.json");
const COOKIE_NAME = "apex_local_auth";
const SESSION_TTL_SECONDS = 7 * 24 * 60 * 60;
const DEFAULT_ADMIN_EMAIL = "admin@apex.local";
const DEFAULT_ADMIN_PASSWORD = "admin12345";
const RANK: Record<AppRole, number> = { user: 1, operator: 2, administrator: 3 };

function nowIso() {
  return new Date().toISOString();
}

function id(prefix: string) {
  return `${prefix}_${randomBytes(12).toString("hex")}`;
}

function b64url(input: Buffer | string) {
  return Buffer.from(input)
    .toString("base64")
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

function hashPassword(password: string, salt = randomBytes(16).toString("hex")) {
  const hash = pbkdf2Sync(password, salt, 120_000, 32, "sha256").toString("hex");
  return { salt, hash };
}

function verifyPassword(password: string, user: LocalUser) {
  const expected = Buffer.from(user.passwordHash, "hex");
  const actual = Buffer.from(hashPassword(password, user.salt).hash, "hex");
  return expected.length === actual.length && timingSafeEqual(expected, actual);
}

function signJwt(db: LocalAuthDb, user: LocalUser) {
  const header = b64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = b64url(JSON.stringify({
    sub: user.id,
    email: user.email,
    role: user.role,
    sv: user.sessionVersion,
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS,
  }));
  const data = `${header}.${payload}`;
  const sig = b64url(createHmac("sha256", db.jwtSecret).update(data).digest());
  return `${data}.${sig}`;
}

function verifyJwt(db: LocalAuthDb, token?: string): LocalUser | null {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [header, payload, sig] = parts;
  const expected = b64url(createHmac("sha256", db.jwtSecret).update(`${header}.${payload}`).digest());
  const a = Buffer.from(sig);
  const b = Buffer.from(expected);
  if (a.length !== b.length || !timingSafeEqual(a, b)) return null;

  try {
    const json = JSON.parse(Buffer.from(payload.replaceAll("-", "+").replaceAll("_", "/"), "base64").toString("utf8"));
    if (!json?.sub || typeof json.exp !== "number" || json.exp < Math.floor(Date.now() / 1000)) return null;
    const user = db.users.find((u) => u.id === json.sub) ?? null;
    if (!user) return null;
    if (typeof json.sv !== "number" || json.sv !== user.sessionVersion) return null;
    return user;
  } catch {
    return null;
  }
}

function loadDb(): LocalAuthDb {
  try {
    const db = JSON.parse(readFileSync(DB_PATH, "utf8")) as LocalAuthDb;
    if (db.version === 1 && Array.isArray(db.users) && Array.isArray(db.invites)) {
      let changed = false;
      for (const user of db.users) {
        const mutable = user as LocalUser;
        if (typeof mutable.sessionVersion !== "number") {
          mutable.sessionVersion = 1;
          changed = true;
        }
      }
      if (changed) saveDb(db);
      return db;
    }
  } catch {
    // Create below.
  }

  const password = hashPassword(DEFAULT_ADMIN_PASSWORD);
  const db: LocalAuthDb = {
    version: 1,
    jwtSecret: randomBytes(32).toString("hex"),
    users: [{
      id: "local-admin",
      email: DEFAULT_ADMIN_EMAIL,
      displayName: "Local Administrator",
      passwordHash: password.hash,
      salt: password.salt,
      role: "administrator",
      createdAt: nowIso(),
      sessionVersion: 1,
    }],
    invites: [],
  };
  saveDb(db);
  return db;
}

function saveDb(db: LocalAuthDb) {
  mkdirSync(dirname(DB_PATH), { recursive: true });
  const tmp = `${DB_PATH}.tmp`;
  writeFileSync(tmp, JSON.stringify(db, null, 2), "utf8");
  renameSync(tmp, DB_PATH);
}

function loadSettings(): LocalSettingsDb {
  try {
    const data = JSON.parse(readFileSync(SETTINGS_PATH, "utf8")) as LocalSettingsDb;
    if (data.version === 1 && data.settings && typeof data.settings === "object") return data;
  } catch {
    // Create below.
  }
  const data: LocalSettingsDb = { version: 1, settings: {} };
  saveSettings(data);
  return data;
}

function saveSettings(data: LocalSettingsDb) {
  mkdirSync(dirname(SETTINGS_PATH), { recursive: true });
  const tmp = `${SETTINGS_PATH}.tmp`;
  writeFileSync(tmp, JSON.stringify(data, null, 2), "utf8");
  renameSync(tmp, SETTINGS_PATH);
}

function assertSettingKey(value: string) {
  const key = String(value ?? "").trim();
  if (!/^[a-z0-9][a-z0-9_-]{1,80}$/i.test(key)) {
    throw new Error("Invalid settings key.");
  }
  return key;
}

function publicUser(user: LocalUser) {
  return {
    id: user.id,
    email: user.email,
    display_name: user.displayName,
    created_at: user.createdAt,
    role: user.role,
  };
}

function publicInvite(invite: LocalInvite) {
  return {
    id: invite.id,
    email: invite.email,
    role: invite.role,
    token: invite.token,
    expires_at: invite.expiresAt,
    used_at: invite.usedAt,
    created_at: invite.createdAt,
    max_uses: invite.maxUses,
    uses_count: invite.usesCount,
  };
}

function parseCookie(header?: string) {
  const out = new Map<string, string>();
  for (const part of (header ?? "").split(";")) {
    const idx = part.indexOf("=");
    if (idx > 0) out.set(part.slice(0, idx).trim(), decodeURIComponent(part.slice(idx + 1).trim()));
  }
  return out;
}

async function readJson(req: import("node:http").IncomingMessage) {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  if (chunks.length === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function send(res: import("node:http").ServerResponse, status: number, payload: unknown, cookie?: string) {
  const body = JSON.stringify(payload);
  res.statusCode = status;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.setHeader("cache-control", "no-store");
  if (cookie) res.setHeader("set-cookie", cookie);
  res.end(body);
}

function assertRole(user: LocalUser | null, min: AppRole) {
  if (!user) throw Object.assign(new Error("Unauthorized"), { status: 401 });
  if (RANK[user.role] < RANK[min]) throw Object.assign(new Error("Forbidden"), { status: 403 });
}

function isRole(value: unknown): value is AppRole {
  return value === "user" || value === "operator" || value === "administrator";
}

async function handle(req: import("node:http").IncomingMessage, res: import("node:http").ServerResponse) {
  const url = new URL(req.url ?? "/", "http://localhost");
  const path = url.pathname.replace(/^\/api\/local-auth/, "") || "/";
  const db = loadDb();
  const cookie = parseCookie(req.headers.cookie).get(COOKIE_NAME);
  const current = verifyJwt(db, cookie);

  try {
    if (req.method === "GET" && path === "/me") {
      return send(res, 200, { user: current ? publicUser(current) : null });
    }

    if (req.method === "POST" && path === "/login") {
      const data = await readJson(req);
      const email = String(data.email ?? "").trim().toLowerCase();
      const password = String(data.password ?? "");
      const user = db.users.find((u) => u.email.toLowerCase() === email);
      if (!user || !verifyPassword(password, user)) {
        return send(res, 401, { error: "Invalid email or password." });
      }
      const token = signJwt(db, user);
      return send(
        res,
        200,
        { user: publicUser(user) },
        `${COOKIE_NAME}=${encodeURIComponent(token)}; Path=/; Max-Age=${SESSION_TTL_SECONDS}; HttpOnly; SameSite=Lax`,
      );
    }

    if (req.method === "POST" && path === "/logout") {
      return send(res, 200, { ok: true }, `${COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax`);
    }

    if (req.method === "GET" && path.startsWith("/settings/")) {
      assertRole(current, "operator");
      const key = assertSettingKey(decodeURIComponent(path.slice("/settings/".length)));
      const settings = loadSettings();
      const item = settings.settings[key] ?? null;
      return send(res, 200, {
        key,
        value: item?.value ?? null,
        updated_at: item?.updatedAt ?? null,
        updated_by: item?.updatedBy ?? null,
      });
    }

    if (req.method === "PUT" && path.startsWith("/settings/")) {
      assertRole(current, "operator");
      const key = assertSettingKey(decodeURIComponent(path.slice("/settings/".length)));
      const data = await readJson(req) as { value?: unknown };
      if (!("value" in data)) throw new Error("Missing settings value.");
      const settings = loadSettings();
      settings.settings[key] = {
        value: data.value,
        updatedAt: nowIso(),
        updatedBy: current!.id,
      };
      saveSettings(settings);
      return send(res, 200, { ok: true, key, updated_at: settings.settings[key].updatedAt });
    }

    if (req.method === "GET" && path === "/users") {
      assertRole(current, "administrator");
      return send(res, 200, { users: db.users.map(publicUser).sort((a, b) => a.email.localeCompare(b.email)) });
    }

    if (req.method === "POST" && path === "/users") {
      assertRole(current, "administrator");
      const data = await readJson(req);
      const email = String(data.email ?? "").trim().toLowerCase();
      const password = String(data.password ?? "");
      const displayName = typeof data.display_name === "string" && data.display_name.trim() ? data.display_name.trim() : null;
      if (!email.includes("@")) throw new Error("Valid email is required.");
      if (password.length < 8) throw new Error("Password must be at least 8 characters.");
      if (!isRole(data.role)) throw new Error("Invalid role.");
      if (db.users.some((u) => u.email.toLowerCase() === email)) throw new Error("User already exists.");
      const pass = hashPassword(password);
      const user: LocalUser = {
        id: id("usr"),
        email,
        displayName,
        passwordHash: pass.hash,
        salt: pass.salt,
        role: data.role,
        createdAt: nowIso(),
        sessionVersion: 1,
      };
      db.users.push(user);
      saveDb(db);
      return send(res, 200, { user: publicUser(user) });
    }

    if (req.method === "POST" && path === "/users/role") {
      assertRole(current, "administrator");
      const data = await readJson(req);
      if (!isRole(data.role)) throw new Error("Invalid role.");
      const user = db.users.find((u) => u.id === data.user_id);
      if (!user) throw new Error("User not found.");
      user.role = data.role;
      user.sessionVersion += 1;
      saveDb(db);
      return send(res, 200, { ok: true });
    }

    if (req.method === "POST" && path === "/users/delete") {
      assertRole(current, "administrator");
      const data = await readJson(req);
      if (data.user_id === current?.id) throw new Error("You cannot delete your own account.");
      const before = db.users.length;
      db.users = db.users.filter((u) => u.id !== data.user_id);
      if (db.users.length === before) throw new Error("User not found.");
      saveDb(db);
      return send(res, 200, { ok: true });
    }

    if (req.method === "GET" && path === "/invites") {
      assertRole(current, "administrator");
      return send(res, 200, { invites: db.invites.map(publicInvite).sort((a, b) => b.created_at.localeCompare(a.created_at)) });
    }

    if (req.method === "POST" && path === "/invites") {
      assertRole(current, "administrator");
      const data = await readJson(req);
      if (!isRole(data.role)) throw new Error("Invalid role.");
      const neverExpires = data.never_expires === true;
      const expiresDays = Math.max(1, Math.min(30, Number(data.expires_in_days) || 7));
      const maxUses = Math.max(1, Math.min(1000, Number(data.max_uses) || 1));
      const invite: LocalInvite = {
        id: id("inv"),
        token: randomBytes(32).toString("hex"),
        email: typeof data.email === "string" && data.email.trim() ? data.email.trim().toLowerCase() : null,
        role: data.role,
        expiresAt: neverExpires ? null : new Date(Date.now() + expiresDays * 86400000).toISOString(),
        usedAt: null,
        createdBy: current!.id,
        createdAt: nowIso(),
        maxUses,
        usesCount: 0,
      };
      db.invites.push(invite);
      saveDb(db);
      return send(res, 200, { invite: publicInvite(invite) });
    }

    if (req.method === "POST" && path === "/invites/delete") {
      assertRole(current, "administrator");
      const data = await readJson(req);
      db.invites = db.invites.filter((i) => i.id !== data.id);
      saveDb(db);
      return send(res, 200, { ok: true });
    }

    if (req.method === "POST" && path === "/invites/lookup") {
      const data = await readJson(req);
      const invite = db.invites.find((i) => i.token === data.token);
      if (!invite) return send(res, 200, { status: "invalid" });
      if (invite.usesCount >= invite.maxUses) return send(res, 200, { status: "used" });
      if (invite.expiresAt && Date.parse(invite.expiresAt) < Date.now()) return send(res, 200, { status: "expired" });
      return send(res, 200, {
        status: "ok",
        email: invite.email,
        role: invite.role,
        remaining: invite.maxUses - invite.usesCount,
      });
    }

    if (req.method === "POST" && path === "/invites/accept") {
      const data = await readJson(req);
      const invite = db.invites.find((i) => i.token === data.token);
      if (!invite) throw new Error("Invalid invite token.");
      if (invite.usesCount >= invite.maxUses) throw new Error("This invite has reached its usage limit.");
      if (invite.expiresAt && Date.parse(invite.expiresAt) < Date.now()) throw new Error("This invite has expired.");
      const email = String(data.email ?? "").trim().toLowerCase();
      if (!email.includes("@")) throw new Error("Valid email is required.");
      if (invite.email && invite.email !== email) throw new Error("This invite link is bound to a different email address.");
      if (db.users.some((u) => u.email.toLowerCase() === email)) throw new Error("User already exists.");
      const password = String(data.password ?? "");
      if (password.length < 8) throw new Error("Password must be at least 8 characters.");
      const pass = hashPassword(password);
      db.users.push({
        id: id("usr"),
        email,
        displayName: typeof data.display_name === "string" && data.display_name.trim() ? data.display_name.trim() : null,
        passwordHash: pass.hash,
        salt: pass.salt,
        role: invite.role,
        createdAt: nowIso(),
        sessionVersion: 1,
      });
      invite.usesCount += 1;
      if (invite.usesCount >= invite.maxUses) invite.usedAt = nowIso();
      saveDb(db);
      return send(res, 200, { ok: true, email });
    }

    return send(res, 404, { error: "Not found" });
  } catch (error) {
    const err = error as Error & { status?: number };
    return send(res, err.status ?? 400, { error: err.message || "Request failed" });
  }
}

export function localAuthServerPlugin(): Plugin {
  return {
    name: "apex-local-auth-server",
    configureServer(server: ViteDevServer) {
      loadDb();
      loadSettings();
      server.middlewares.use("/api/local-auth", (req, res) => {
        void handle(req, res);
      });
      server.config.logger.info(
        `\n  Local auth: ${DEFAULT_ADMIN_EMAIL} / ${DEFAULT_ADMIN_PASSWORD}\n`,
      );
    },
  };
}
