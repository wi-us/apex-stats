/* eslint-disable no-console */
const fs = require("fs");
const path = require("path");
const { Client } = require("pg");

async function main() {
  const sqlPath = path.resolve(__dirname, "..", "..", "infra", "postgres", "init.sql");
  const sql = fs.readFileSync(sqlPath, "utf-8");

  const client = new Client({
    host: process.env.APPLY_PG_HOST || "127.0.0.1",
    port: Number(process.env.APPLY_PG_PORT || 5433),
    user: process.env.APPLY_PG_USER || "apex",
    password: process.env.APPLY_PG_PASSWORD || "apex",
    database: process.env.APPLY_PG_DB || "apex_stats",
  });

  await client.connect();
  await client.query(sql);
  await client.end();
  console.log("pg schema applied from init.sql");
}

main().catch((error) => {
  console.error("pg schema apply failed", error);
  process.exit(1);
});
