import { defineConfig } from "drizzle-kit";

export default defineConfig({
  schema: "./src/schema.ts",
  out: "./migrations",
  dialect: "postgresql",
  dbCredentials: {
    url: process.env.DATABASE_URL ?? "postgresql://cratedigger:cratedigger@localhost:5432/cratediggerdb",
  },
  // Match Sift convention: numbered migrations
  migrations: {
    table: "drizzle_migrations",
    schema: "public",
  },
  verbose: true,
  strict: true,
});
