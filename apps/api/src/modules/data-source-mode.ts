export type DataSourceMode = "sqlite" | "postgres" | "hybrid";

export function resolveDataSourceMode(raw: string | undefined, fallback: DataSourceMode): DataSourceMode {
  const value = String(raw ?? "").trim().toLowerCase();
  if (value === "sqlite" || value === "postgres" || value === "hybrid") {
    return value;
  }
  return fallback;
}
