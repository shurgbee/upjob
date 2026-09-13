"use client";

import { useAuth } from "@workos-inc/authkit-nextjs/components";
import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import type { AppliedJob, Job } from "@/lib/jobs";
import { ArrowIcon, BriefcaseIcon, FlameIcon } from "./Icons";

type JobTab = "ready" | "applied";
type SortKey = "source" | "title" | "opened";
type SortDirection = "asc" | "desc";
type SortState = { key: SortKey; direction: SortDirection };

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

// FastAPI errors arrive as either a string `detail` (HTTPException) or, on a 422,
// an array of `{ type, loc, msg, input }` objects. Flatten both to a plain string
// so they can be rendered as text instead of crashing React with a raw object.
function formatErrorDetail(data: unknown, fallback: string): string {
  if (data && typeof data === "object") {
    const detail = (data as { detail?: unknown; error?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) =>
          item && typeof item === "object" && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : null,
        )
        .filter((msg): msg is string => Boolean(msg));
      if (messages.length > 0) return messages.join("; ");
    }
    const error = (data as { error?: unknown }).error;
    if (typeof error === "string") return error;
  }
  return fallback;
}

function SortIndicator({
  active,
  direction,
}: {
  active: boolean;
  direction: SortDirection;
}) {
  return (
    <span
      className={`sort-indicator${active ? " is-active" : ""}`}
      aria-hidden="true"
    >
      {active && direction === "desc" ? "↓" : "↑"}
    </span>
  );
}

export function Dashboard({
  jobs: readyJobs,
  appliedJobs,
}: {
  jobs: Job[];
  appliedJobs: AppliedJob[];
}) {
  const router = useRouter();
  const [tab, setTab] = useState<JobTab>("ready");
  const [sort, setSort] = useState<Record<JobTab, SortState>>({
    ready: { key: "opened", direction: "desc" },
    applied: { key: "opened", direction: "desc" },
  });
  const { user } = useAuth({ ensureSignedIn: true });
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [syncError, setSyncError] = useState(false);
  const [applyingId, setApplyingId] = useState<string | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [, startTransition] = useTransition();
  const currentSort = sort[tab];
  const sortedJobs = useMemo(() => {
    const jobs = tab === "ready" ? readyJobs : [];

    return [...jobs].sort((first, second) => {
      const firstValue = first[currentSort.key] ?? "";
      const secondValue = second[currentSort.key] ?? "";
      const result = firstValue.localeCompare(secondValue, undefined, {
        sensitivity: "base",
      });

      return currentSort.direction === "asc" ? result : -result;
    });
  }, [currentSort, readyJobs, tab]);
  const firstName = user?.firstName ?? user?.name?.split(" ")[0] ?? "there";

  const handleSync = async () => {
    setSyncing(true);
    setSyncMessage(null);
    setSyncError(false);
    try {
      const response = await fetch("/api/gmail/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hours: 48 }),
      });
      const data = await response.json();
      if (!response.ok) {
        setSyncError(true);
        setSyncMessage(
          formatErrorDetail(data, "Sync failed. Please try again."),
        );
        return;
      }
      const qualified = data.qualified ?? 0;
      const matched = data.matched ?? 0;
      const created = data.created ?? 0;
      setSyncMessage(
        `Synced ${qualified} application email${qualified === 1 ? "" : "s"} from the last 2 hours — ${matched} matched, ${created} added.`,
      );
    } catch {
      setSyncError(true);
      setSyncMessage("Could not reach the sync service.");
    } finally {
      setSyncing(false);
    }
  };

  // Record the application, then open the posting. On success the server data is
  // refreshed so the job moves from "Jobs to apply" into "Jobs applied".
  const handleApply = async (job: Job) => {
    setApplyError(null);
    if (job.url) {
      window.open(job.url, "_blank", "noopener,noreferrer");
    }
    if (!job.specId) return;
    setApplyingId(job.specId);
    try {
      const response = await fetch("/api/applications", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ specId: job.specId }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        setApplyError(formatErrorDetail(data, "Could not track this application."));
        return;
      }
      startTransition(() => router.refresh());
    } catch {
      setApplyError("Could not reach the application tracker.");
    } finally {
      setApplyingId(null);
    }
  };

  const updateSort = (key: SortKey) => {
    setSort((current) => ({
      ...current,
      [tab]: {
        key,
        direction:
          current[tab].key === key && current[tab].direction === "asc"
            ? "desc"
            : "asc",
      },
    }));
  };

  const sortableHeader = (key: SortKey, label: string, className?: string) => {
    const isActive = currentSort.key === key;

    return (
      <th
        scope="col"
        className={className}
        aria-sort={
          isActive
            ? currentSort.direction === "asc"
              ? "ascending"
              : "descending"
            : "none"
        }
      >
        <button
          type="button"
          className="sort-button"
          onClick={() => updateSort(key)}
        >
          {label}
          <SortIndicator active={isActive} direction={currentSort.direction} />
        </button>
      </th>
    );
  };

  return (
    <div className="dashboard">
      <section className="welcome-row">
        <div>
          <p className="eyebrow">Friday, September 11</p>
          <h1>Good morning, {firstName}.</h1>
          <p className="welcome-copy">
            Your next opportunity is ready when you are.
          </p>
        </div>
        <div className="weekly-goal">
          <span>Weekly goal</span>
          <strong>8 of 10</strong>
          <div className="goal-track">
            <span />
          </div>
        </div>
      </section>

      <section className="stat-grid" aria-label="Job search overview">
        <article className="stat-card streak-card">
          <div className="stat-icon flame">
            <FlameIcon />
          </div>
          <div>
            <span className="stat-label">Current streak</span>
            <strong>
              7 <small>days</small>
            </strong>
          </div>
          <span className="streak-note">Personal best</span>
        </article>
        <article className="stat-card">
          <div className="stat-icon">
            <BriefcaseIcon />
          </div>
          <div>
            <span className="stat-label">Jobs applied</span>
            <strong>{appliedJobs.length}</strong>
          </div>
          <span className="stat-change">
            {appliedJobs.length > 0 ? "Keep it going" : "Track applications soon"}
          </span>
        </article>
        <article className="stat-card">
          <div className="stat-icon">
            <span className="pending-mark">{readyJobs.length}</span>
          </div>
          <div>
            <span className="stat-label">Job matches</span>
            <strong>{readyJobs.length}</strong>
          </div>
          <span className="stat-change">Ready to review</span>
        </article>
      </section>

      <section className="jobs-panel" aria-labelledby="jobs-heading">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Your pipeline</p>
            <h2 id="jobs-heading">Job matches</h2>
          </div>
          <div className="panel-controls">
            {tab === "applied" && (
              <button
                type="button"
                className="sync-button"
                onClick={handleSync}
                disabled={syncing}
              >
                {syncing ? "Syncing…" : "Sync from Gmail"}
              </button>
            )}
            <div className="job-tabs" role="tablist" aria-label="Job lists">
              <button
                type="button"
                role="tab"
                aria-selected={tab === "ready"}
                onClick={() => setTab("ready")}
              >
                Jobs to apply <span>{readyJobs.length}</span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === "applied"}
                onClick={() => setTab("applied")}
              >
                Jobs applied <span>{appliedJobs.length}</span>
              </button>
            </div>
          </div>
        </div>
        {syncMessage && (
          <p
            className={`sync-status${syncError ? " is-error" : ""}`}
            role="status"
          >
            {syncMessage}
          </p>
        )}
        {applyError && (
          <p className="sync-status is-error" role="status">
            {applyError}
          </p>
        )}
        <div className="jobs-table-wrap">
          <table className="jobs-table">
            <caption className="sr-only">
              {tab === "ready" ? "Jobs to apply" : "Applied jobs"}
            </caption>
            <colgroup>
              <col className="company-column" />
              <col className="role-column" />
              <col className="date-column" />
              <col className="action-column" />
            </colgroup>
            <thead>
              <tr>
                {sortableHeader("source", "Source")}
                {sortableHeader("title", "Role")}
                {tab === "ready" ? (
                  sortableHeader("opened", "Date opened")
                ) : (
                  <th scope="col">Date applied</th>
                )}
                <th scope="col" className="action-heading">
                  {tab === "ready" ? "Apply" : "Status"}
                </th>
              </tr>
            </thead>
            <tbody>
              {tab === "ready" &&
                sortedJobs.map((job) => (
                  <tr key={job.id}>
                    <td>
                      <span className="company-cell">
                        <span className="company-mark">
                          {job.source.charAt(0)}
                        </span>
                        {job.source}
                      </span>
                    </td>
                    <td>
                      <span className="job-title">{job.title}</span>
                      {job.technologies.length > 0 && (
                        <span className="job-technologies">
                          {job.technologies.slice(0, 3).join(" · ")}
                        </span>
                      )}
                    </td>
                    <td>
                      <time dateTime={job.opened}>
                        {dateFormatter.format(new Date(job.opened))}
                      </time>
                    </td>
                    <td className="row-action">
                      <button
                        type="button"
                        className="apply-link"
                        onClick={() => handleApply(job)}
                        disabled={applyingId === job.specId}
                        aria-label={`Apply for ${job.title}`}
                      >
                        {applyingId === job.specId ? "Applying…" : "Apply"}{" "}
                        <ArrowIcon />
                      </button>
                    </td>
                  </tr>
                ))}
              {tab === "applied" &&
                appliedJobs.map((job, index) => (
                  <tr key={job.specId ?? `applied-${index}`}>
                    <td>
                      <span className="company-cell">
                        <span className="company-mark">
                          {(job.source || "?").charAt(0)}
                        </span>
                        {job.source}
                      </span>
                    </td>
                    <td>
                      {job.url ? (
                        <a
                          className="job-title job-title-link"
                          href={job.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {job.title}
                        </a>
                      ) : (
                        <span className="job-title">{job.title}</span>
                      )}
                    </td>
                    <td>
                      {job.applied ? (
                        <time dateTime={job.applied}>
                          {dateFormatter.format(new Date(job.applied))}
                        </time>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      <span
                        className={`application-status${job.completed ? " is-confirmed" : ""}`}
                      >
                        {job.completed
                          ? job.status || "Confirmed"
                          : "Pending confirmation"}
                      </span>
                    </td>
                  </tr>
                ))}
              {tab === "ready" && sortedJobs.length === 0 && (
                <tr>
                  <td className="jobs-empty" colSpan={4}>
                    No job matches are available yet.
                  </td>
                </tr>
              )}
              {tab === "applied" && appliedJobs.length === 0 && (
                <tr>
                  <td className="jobs-empty" colSpan={4}>
                    No applications tracked yet. Apply to a job, then sync from
                    Gmail to confirm it.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
