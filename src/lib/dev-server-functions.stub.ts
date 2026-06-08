import {
  browserAcceptInvite,
  browserCreateInvite,
  browserCreateUser,
  browserDeleteInvite,
  browserDeleteUser,
  browserListInvites,
  browserListUsers,
  browserLookupInvite,
  browserSetUserRole,
} from "@/lib/browser-auth-store";
import { isBrowserAuthMode } from "@/lib/runtime-config";

async function request(path: string, init?: RequestInit) {
  const response = await fetch(`/api/local-auth${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error ?? "Request failed");
  return data;
}

function payload(arg?: { data?: unknown }) {
  return JSON.stringify(arg?.data ?? {});
}

export async function listUserAccounts() {
  if (isBrowserAuthMode) return browserListUsers();
  return request("/users");
}

export async function createUserAccount(arg?: { data?: unknown }) {
  if (isBrowserAuthMode) return browserCreateUser(arg?.data);
  const data = await request("/users", { method: "POST", body: payload(arg) });
  return { id: data.user?.id };
}

export async function setUserRole(arg?: { data?: unknown }) {
  if (isBrowserAuthMode) return browserSetUserRole(arg?.data);
  return request("/users/role", { method: "POST", body: payload(arg) });
}

export async function deleteUserAccount(arg?: { data?: unknown }) {
  if (isBrowserAuthMode) return browserDeleteUser(arg?.data);
  return request("/users/delete", { method: "POST", body: payload(arg) });
}

export async function createInvite(arg?: { data?: unknown }) {
  if (isBrowserAuthMode) return browserCreateInvite(arg?.data);
  return request("/invites", { method: "POST", body: payload(arg) });
}

export async function listInvites() {
  if (isBrowserAuthMode) return browserListInvites();
  return request("/invites");
}

export async function deleteInvite(arg?: { data?: unknown }) {
  if (isBrowserAuthMode) return browserDeleteInvite(arg?.data);
  return request("/invites/delete", { method: "POST", body: payload(arg) });
}

export async function lookupInvite(arg?: { data?: unknown }) {
  if (isBrowserAuthMode) return browserLookupInvite(arg?.data);
  return request("/invites/lookup", { method: "POST", body: payload(arg) });
}

export async function acceptInvite(arg?: { data?: unknown }) {
  if (isBrowserAuthMode) return browserAcceptInvite(arg?.data);
  return request("/invites/accept", { method: "POST", body: payload(arg) });
}
