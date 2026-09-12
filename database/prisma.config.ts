import "dotenv/config";
import { defineConfig } from "prisma/config";

export default defineConfig({
  schema: "schema.prisma",
  migrations: { path: "migrations" },
  // Empty URL lets offline validate/generate run without a live database.
  // Migration commands still fail closed until DATABASE_URL is configured.
  datasource: { url: process.env.DATABASE_URL ?? "" },
});
