import "server-only";

import postgres from "postgres";

export type Job = {
  id: number;
  source: string;
  title: string;
  url: string;
  technologies: string[];
  architecture: string[];
  yearsOfExperience: number;
  opened: string;
};

type JobSpecRow = {
  id: number;
  title: string;
  url: string;
  technologies: string[];
  architecture: string[];
  yoe: number;
  opened_at: Date;
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

export async function getJobSpecs(): Promise<Job[]> {
  const sql = getSql();
  const rows = await sql<JobSpecRow[]>`
    SELECT
      id,
      title,
      url,
      technologies,
      architecture,
      yoe,
      COALESCE(publish_date, spec_created_at) AS opened_at
    FROM job_specs
    ORDER BY COALESCE(publish_date, spec_created_at) DESC, id DESC
  `;

  return rows.map((row) => ({
    id: row.id,
    source: sourceFromUrl(row.url),
    title: row.title,
    url: row.url,
    technologies: row.technologies,
    architecture: row.architecture,
    yearsOfExperience: row.yoe,
    opened: row.opened_at.toISOString(),
  }));
}
