/**
 * Drizzle client. Used by `apps/web` server components, route handlers,
 * and the seed script.
 *
 * `services/api` (Python/FastAPI) connects via asyncpg directly — see
 * `services/api/app/db.py`. The two stay in sync because `init.sql` is
 * the fresh-DB SOT mirroring this schema.
 *
 * ── Connection lifecycle (this is a billing concern, not a perf one) ──
 * Neon bills compute by *active time* and only suspends an endpoint after a
 * window with zero open connections. postgres.js defaults to `idle_timeout:
 * null` — idle connections are never closed — so on Vercel a single page
 * view could pin the compute for the whole lifetime of that function
 * instance. Hence the explicit `idle_timeout` below.
 *
 * The `globalThis` cache is likewise deliberately enabled in *production*.
 * It used to be dev-only, which disabled duplicate-pool protection exactly
 * where duplicates cost money: Next.js bundles the RSC graph and the
 * route-handler graph separately, so this module can be instantiated more
 * than once per instance, each copy opening its own pool.
 *
 * If this ever needs to be bulletproof rather than merely correct, the RSC
 * read path can move to `drizzle-orm/neon-http` + `@neondatabase/serverless`
 * — stateless HTTPS per query, so there is no socket to leave idle at all.
 * Caveat: no interactive transactions and no `SET LOCAL app.current_user_id`,
 * so the RLS tables (taste_profiles, feedback, annotations) must stay on
 * this client.
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

// Neon's pooled endpoint runs pgbouncer in transaction mode, which cannot
// support named prepared statements. Detected rather than hardcoded so the
// same code works against the direct endpoint and local Docker.
const isPooled = connectionString.includes("-pooler.");

// Lazy singleton — re-imported across RSC boundaries
declare global {
  // eslint-disable-next-line no-var
  var __cratedigger_pg: ReturnType<typeof postgres> | undefined;
}

const client =
  globalThis.__cratedigger_pg ??
  postgres(connectionString, {
    max: 3,
    ssl: isLocalhost ? false : "require",
    // Close idle sockets so Neon can suspend. Must stay well under Neon's
    // autosuspend window (default 5 min) to be the thing that lets go first.
    idle_timeout: 20,
    // Recycle long-lived sockets; also bounds damage from a stuck connection.
    max_lifetime: 60 * 30,
    // Surface a slow Neon resume as an error rather than an opaque hang.
    connect_timeout: 10,
    prepare: !isPooled,
    onnotice: () => {}, // suppress Neon's chatty NOTICEs
  });

// Cache in every environment. On Vercel this prevents one pool per module
// instance; in dev it prevents one pool per hot reload.
globalThis.__cratedigger_pg = client;

export const db = drizzle(client, { schema });
export { schema };
export type Db = typeof db;
