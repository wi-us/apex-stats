import { isBrowserAuthMode } from "@/lib/runtime-config";

const STORAGE_PREFIX = "admin:server-settings:";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/local-auth${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error ?? "Settings request failed");
  return data as T;
}

export async function loadAdminSetting<T>(key: string): Promise<T | null> {
  if (isBrowserAuthMode) {
    const raw = window.localStorage.getItem(`${STORAGE_PREFIX}${key}`);
    return raw ? (JSON.parse(raw) as T) : null;
  }
  const data = await request<{ value: T | null }>(`/settings/${encodeURIComponent(key)}`);
  return data.value ?? null;
}

export async function saveAdminSetting<T>(key: string, value: T): Promise<void> {
  if (isBrowserAuthMode) {
    window.localStorage.setItem(`${STORAGE_PREFIX}${key}`, JSON.stringify(value));
    return;
  }
  await request(`/settings/${encodeURIComponent(key)}`, {
    method: "PUT",
    body: JSON.stringify({ value }),
  });
}
