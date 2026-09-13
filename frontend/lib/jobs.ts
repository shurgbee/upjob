import "server-only";

import { withAuth } from "@workos-inc/authkit-nextjs";
import postgres from "postgres";

import type { ApplicationStatus } from "./application-status";

export {
  APPLICATION_STATUSES,
  isApplicationStatus,
  type ApplicationStatus,
} from "./application-status";

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
  skillMatch: number;
};

export type AppliedJob = {
  id: string;
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
  id: string;
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
 * job_applications rows are keyed on), creating the link on first use. Returns
 * null only when the request is unauthenticated. Mirrors the resume service's
 * lazy upsert so a user is linked the first time they hit any authed path,
 * rather than only after visiting the resume workspace.
 */
export async function getCurrentUserId(): Promise<string | null> {
  const { user } = await withAuth();
  if (!user) return null;

  const sql = getSql();
  const rows = await sql<{ user_id: string }[]>`
    INSERT INTO app_users (workos_user_id)
    VALUES (${user.id})
    ON CONFLICT (workos_user_id)
    DO UPDATE SET workos_user_id = EXCLUDED.workos_user_id
    RETURNING user_id::text AS user_id
  `;
  return rows[0]?.user_id ?? null;
}

/**
 * The union of technologies across a user's analyzed repos (projects table),
 * lowercased for case-insensitive matching against job_specs.technologies.
 * Empty when the user has no projects or is unauthenticated.
 */
async function getUserSkillSet(userId: string | null | undefined): Promise<Set<string>> {
  if (!userId) return new Set();

  const sql = getSql();
  const rows = await sql<{ skill: string }[]>`
    SELECT DISTINCT unnest(technologies) AS skill
    FROM projects
    WHERE user_id = ${userId}
  `;
  return new Set(rows.map((row) => row.skill.toLowerCase()));
}

/**
 * Open ("ready to apply") job specs. When userId is given, specs the user has
 * already applied to (any job_applications row for that spec) are excluded so
 * they appear only under "Jobs applied". Results are ranked by how well each
 * job's technologies overlap with the skills demonstrated in the user's
 * analyzed repos, highest match first.
 */
export async function getJobSpecs(userId?: string | null): Promise<Job[]> {
  const sql = getSql();
  const [rows, skillSet] = await Promise.all([
    sql<JobSpecRow[]>`
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
  `,
    getUserSkillSet(userId),
  ]);

  const jobs = rows.map((row) => {
    const url = asText(row.url);
    const technologies = asStringList(row.technologies);
    const matched = technologies.filter((tech) => skillSet.has(tech.toLowerCase())).length;
    return {
      id: row.id,
      specId: asText(row.spec_id),
      source: asText(row.company).trim() || sourceFromUrl(url),
      title: asText(row.title, "Untitled role"),
      url,
      technologies,
      architecture: asStringList(row.architecture),
      yearsOfExperience: typeof row.yoe === "number" ? row.yoe : 0,
      opened: row.opened_at instanceof Date ? row.opened_at.toISOString() : new Date().toISOString(),
      skillMatch: technologies.length > 0 ? matched / technologies.length : 0,
    };
  });

  return jobs.sort((first, second) => second.skillMatch - first.skillMatch);
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
      ja.id::text AS id,
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
      id: asText(row.id),
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

/**
 * Update one application's status for the given user. Scoped by user_id so a
 * user can only change their own rows. Returns true when a row was updated.
 */
export async function updateApplicationStatus(
  userId: string,
  applicationId: string,
  status: ApplicationStatus,
): Promise<boolean> {
  const sql = getSql();
  const rows = await sql`
    UPDATE job_applications
    SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('status', ${status}::text)
    WHERE id = ${applicationId}::uuid AND user_id = ${userId}::uuid
    RETURNING id
  `;
  return rows.length > 0;
}
