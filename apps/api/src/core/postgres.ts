import { Pool } from "pg";

let sharedPool: Pool | null = null;

function buildConnectionString(): string {
  if (process.env.DATABASE_URL) return process.env.DATABASE_URL;
  const host = process.env.POSTGRES_HOST ?? "localhost";
  const port = process.env.POSTGRES_PORT ?? "5432";
  const user = process.env.POSTGRES_USER ?? "apex";
  const password = process.env.POSTGRES_PASSWORD ?? "apex";
  const db = process.env.POSTGRES_DB ?? "apex_stats";
  return `postgresql://${encodeURIComponent(user)}:${encodeURIComponent(password)}@${host}:${port}/${db}`;
}

export function getPostgresPool(): Pool {
  if (sharedPool) return sharedPool;
  sharedPool = new Pool({
    connectionString: buildConnectionString(),
    max: Number(process.env.PG_POOL_MAX ?? 10),
    idleTimeoutMillis: Number(process.env.PG_IDLE_TIMEOUT_MS ?? 30000),
  });
  return sharedPool;
}
