import { readFile } from "node:fs/promises";
import postgres from "postgres";

try {
  process.loadEnvFile(".env");
} catch {
  /* Deployment can supply env directly. */
}
if (!process.env.POSTGRES_URL)
  throw new Error("Set POSTGRES_URL before running this migration.");
if (!process.argv.includes("--check") && !process.argv.includes("--apply"))
  throw new Error("Pass --check (rollback) or --apply (commit).");
const migrations = await Promise.all(
  ["001_resume_workspace.sql", "002_skill_workspace.sql"].map(async (name) =>
    (await readFile(new URL(`../migrations/${name}`, import.meta.url), "utf8"))
      .replace(/^BEGIN;\s*/, "")
      .replace(/COMMIT;\s*$/, ""),
  ),
);
const sql = postgres(process.env.POSTGRES_URL, { max: 1, connect_timeout: 10 });
const rollback = new Error("dry-run rollback");
try {
  await sql.begin(async (transaction) => {
    await transaction`SET LOCAL lock_timeout = '5s'`;
    await transaction`SET LOCAL statement_timeout = '30s'`;
    for (const migration of migrations) await transaction.unsafe(migration);
    if (!process.argv.includes("--apply")) throw rollback;
  });
  console.log("Workspace migrations applied.");
} catch (error) {
  if (error === rollback)
    console.log("Workspace migrations validated; all changes rolled back.");
  else {
    console.error(
      `Migration failed (${error.code ?? error.name}): ${error.message}`,
    );
    process.exitCode = 1;
  }
} finally {
  await sql.end();
}
