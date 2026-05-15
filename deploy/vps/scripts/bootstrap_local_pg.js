/* eslint-disable no-console */
const { Client } = require("pg");

async function main() {
  const client = new Client({
    host: process.env.BOOTSTRAP_PG_HOST || "127.0.0.1",
    port: Number(process.env.BOOTSTRAP_PG_PORT || 5433),
    user: process.env.BOOTSTRAP_PG_USER || "postgres",
    password: process.env.BOOTSTRAP_PG_PASSWORD || undefined,
    database: process.env.BOOTSTRAP_PG_DB || "postgres",
  });

  await client.connect();

  const roleExists = await client.query("SELECT 1 FROM pg_roles WHERE rolname = 'apex'");
  if (!roleExists.rowCount) {
    await client.query("CREATE ROLE apex LOGIN PASSWORD 'apex'");
  }

  const dbExists = await client.query("SELECT 1 FROM pg_database WHERE datname = 'apex_stats'");
  if (!dbExists.rowCount) {
    await client.query("CREATE DATABASE apex_stats OWNER apex");
  }

  await client.end();
  console.log("local pg bootstrap complete");
}

main().catch((error) => {
  console.error("local pg bootstrap failed", error);
  process.exit(1);
});
