const disabledValues = new Set(["0", "false", "off", "no", "disabled"]);

export const isSupabaseEnabled = !disabledValues.has(
  String(import.meta.env.VITE_SUPABASE_ENABLED ?? "true").trim().toLowerCase(),
);

export const authMode = String(import.meta.env.VITE_AUTH_MODE ?? "api").trim().toLowerCase();
export const isBrowserAuthMode = authMode === "browser";
