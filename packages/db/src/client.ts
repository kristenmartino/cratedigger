/**
 * Drizzle client. Used by `apps/web` server components, route handlers,
 * and the seed script.
 *
 * `services/api` (Python/FastAPI) connects via asyncpg directly — see
 * `services/api/app/db.py`. The two stay in sync because `init.sql` is
 * the fresh-DB SOT mirroring this schema.
 *
 * Pool sizing: max=5 to stay within Neon's hobby/free connection cap
 * (Sift convention; see HARVESTED_FROM_SIFT.md).
 */
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "./schema";

const connectionString =
  process.env.DATABASE_URL ??
  "postgresql://cratedigger:cratedigger@localhost:5432/cratediggerdb";

const isLocalhost =
  connectionString.includes("localhost") ||
  connectionString.includes("127.0.0.1");

// Lazy singleton — re-imported across RSC boundaries
declare global {
  // eslint-disable-next-line no-var
  var __cratedigger_pg: ReturnType<typeof postgres> | undefined;
}

const client =
  globalThis.__cratedigger_pg ??
  postgres(connectionString, {
    max: 5,
    ssl: isLocalhost ? false : "require",
    onnotice: () => {}, // suppress Neon's chatty NOTICEs
  });

if (process.env.NODE_ENV !== "production") {
  globalThis.__cratedigger_pg = client;
}

export const db = drizzle(client, { schema });
export { schema };
export type Db = typeof db;
