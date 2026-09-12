import { PrismaPg } from "@prisma/adapter-pg";
import { PrismaClient } from "../generated/prisma/client.js";

/** Call once per service process; call $disconnect during bounded shutdown. */
export function createDatabaseClient(connectionString: string): PrismaClient {
  if (!connectionString) throw new Error("DATABASE_URL is required");
  const adapter = new PrismaPg({
    connectionString,
    max: 4,
    connectionTimeoutMillis: 5_000,
    idleTimeoutMillis: 30_000,
    statement_timeout: 5_000,
    application_name: "jarvis-runtime",
  });
  return new PrismaClient({ adapter, log: ["warn", "error"] });
}

export { PrismaClient } from "../generated/prisma/client.js";
