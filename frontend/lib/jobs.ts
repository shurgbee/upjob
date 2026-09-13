import "server-only";

import { withAuth } from "@workos-inc/authkit-nextjs";
import postgres from "postgres";

export type Job = {
  id: number;
  specId: string;
  source: string;
  title: string;
  url: string;
  technologies: string[];
  architecture: string[];
  yearsOfExperience: number;
  opened: string;
};

export type AppliedJob = {
  specId: string | null;
  source: string;
  title: string;
  url: string;
  status: string;
  applied: string;
  completed: boolean;
};

type JobSpecRow = {
  id: number;
  spec_id: string;
  title: string;
  url: string;
  company: string;
  technologies: string[];
  architecture: string[];
  yoe: number;
  opened_at: Date;
};

type AppliedRow = {
  spec_id: string | null;
  applied_at: Date | null;
  completed: boolean;
  metadata: Record<string, unknown> | null;
  spec_title: string | null;
  spec_url: string | null;
  spec_company: string | null;
};

const globalForPostgres = globalThis as typeof globalThis & {
  upjobSql?: ReturnType<typeof postgres>;
};

function getSql() {
  const connectionString = process.env.POSTGRES_URL;

  if (!connectionString) {
    throw new Error("POSTGRES_URL is not configured.");
  }

  if (!globalForPostgres.upjobSql) {
    globalForPostgres.upjobSql = postgres(connectionString, {
      max: 5,
      idle_timeout: 20,
      connect_timeout: 10,
    });
  }

  return globalForPostgres.upjobSql;
}

function asText(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function titleCase(value: string) {
  return value
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function sourceFromUrl(value: string) {
  try {
    const url = new URL(value);
    const hostname = url.hostname.replace(/^www\./, "");
    const pathCompany = url.pathname.split("/").filter(Boolean)[0];

    if (
      pathCompany &&
      ["job-boards.greenhouse.io", "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"].includes(hostname)
    ) {
      return titleCase(pathCompany);
    }

    if (hostname.endsWith("oraclecloud.com")) {
      return "Oracle Cloud";
    }

    const hostnameParts = hostname.split(".");
    const domain = hostnameParts.at(-2) ?? hostnameParts[0];
    return titleCase(domain);
  } catch {
    return "Job board";
  }
}

/**
 * Resolve the signed-in WorkOS user to their app_users.user_id (the uuid the
 * job_applications rows are keyed on). Returns null when unauthenticated or when
 * no app_users row is mapped to this WorkOS id yet.
 */
export async function getCurrentUserId(): Promise<string | null> {
  const { user } = await withAuth();
  if (!user) return null;

  const sql = getSql();
  const rows = await sql<{ user_id: string }[]>`
    SELECT user_id::text AS user_id
    FROM app_users
    WHERE workos_user_id = ${user.id}
    LIMIT 1
  `;
  return rows[0]?.user_id ?? null;
}

/**
 * Open ("ready to apply") job specs. When userId is given, specs the user has
 * already applied to (any job_applications row for that spec) are excluded so
 * they appear only under "Jobs applied".
 */
export async function getJobSpecs(userId?: string | null): Promise<Job[]> {
  const sql = getSql();
  const rows = await sql<JobSpecRow[]>`
    SELECT
      js.id,
      js.spec_id::text AS spec_id,
      js.title,
      js.url,
      js.company,
      js.technologies,
      js.architecture,
      js.yoe,
      COALESCE(js.publish_date, js.spec_created_at) AS opened_at
    FROM job_specs js
    WHERE ${
      userId
        ? sql`NOT EXISTS (
            SELECT 1 FROM job_applications ja
            WHERE ja.job_spec_id = js.spec_id AND ja.user_id = ${userId}::uuid
          )`
        : sql`TRUE`
    }
    ORDER BY COALESCE(js.publish_date, js.spec_created_at) DESC, js.id DESC
  `;

  return rows.map((row) => {
    const url = asText(row.url);
    return {
      id: row.id,
      specId: asText(row.spec_id),
      source: asText(row.company).trim() || sourceFromUrl(url),
      title: asText(row.title, "Untitled role"),
      url,
      technologies: asStringList(row.technologies),
      architecture: asStringList(row.architecture),
      yearsOfExperience: typeof row.yoe === "number" ? row.yoe : 0,
      opened: row.opened_at instanceof Date ? row.opened_at.toISOString() : new Date().toISOString(),
    };
  });
}

/**
 * Record that a user applied to a spec: an OPEN row (thread_id NULL,
 * is_completed false) that the Gmail sync later reconciles against the
 * confirmation email. Canonical company/title are copied into metadata so the
 * matcher has context. Idempotent — a second click for the same user+spec is a
 * no-op, so it never duplicates or resurrects a completed row.
 */
export async function createApplication(userId: string, specId: string): Promise<void> {
  const sql = getSql();
  await sql`
    INSERT INTO job_applications
      (user_id, job_id, job_spec_id, aplied_date, metadata, is_completed, thread_id)
    SELECT
      ${userId}::uuid, NULL, js.spec_id, now(),
      jsonb_build_object(
        'source', 'apply-button',
        'company', js.company,
        'role', js.title,
        'url', js.url
      ),
      false, NULL
    FROM job_specs js
    WHERE js.spec_id = ${specId}::uuid
      AND NOT EXISTS (
        SELECT 1 FROM job_applications ja
        WHERE ja.job_spec_id = js.spec_id AND ja.user_id = ${userId}::uuid
      )
  `;
}

/**
 * Applications for a user, joined back to their job spec. Rows the Gmail sync
 * inserted without a matching spec (job_spec_id NULL) fall back to the email's
 * metadata for title/company. Ordered newest first.
 */
export async function getAppliedJobs(userId: string | null): Promise<AppliedJob[]> {
  if (!userId) return [];

  const sql = getSql();
  const rows = await sql<AppliedRow[]>`
    SELECT
      ja.job_spec_id::text AS spec_id,
      ja.aplied_date AS applied_at,
      ja.is_completed AS completed,
      ja.metadata AS metadata,
      js.title AS spec_title,
      js.url AS spec_url,
      js.company AS spec_company
    FROM job_applications ja
    LEFT JOIN job_specs js ON js.spec_id = ja.job_spec_id
    WHERE ja.user_id = ${userId}::uuid
    ORDER BY ja.aplied_date DESC NULLS LAST
  `;

  return rows.map((row) => {
    const meta = row.metadata && typeof row.metadata === "object" ? row.metadata : {};
    const url = asText(row.spec_url) || asText(meta.url);
    const company = asText(row.spec_company).trim() || asText(meta.company).trim();
    const title = asText(row.spec_title) || asText(meta.role, "Untitled role");
    return {
      specId: asText(row.spec_id) || null,
      source: company || (url ? sourceFromUrl(url) : "Unknown"),
      title,
      url,
      status: asText(meta.status),
      applied: row.applied_at instanceof Date ? row.applied_at.toISOString() : "",
      completed: Boolean(row.completed),
    };
  });
}
