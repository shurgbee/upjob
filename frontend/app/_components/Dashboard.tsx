"use client";

import { useAuth } from "@workos-inc/authkit-nextjs/components";
import { useMemo, useState } from "react";
import { ArrowIcon, BriefcaseIcon, FlameIcon } from "./Icons";

type Job = {
  company: string;
  role: string;
  opened: string;
  status?: string;
};

type JobTab = "ready" | "applied";
type SortKey = "company" | "role" | "opened" | "status";
type SortDirection = "asc" | "desc";
type SortState = { key: SortKey; direction: SortDirection };

const readyJobs: Job[] = [
  { company: "Northstar Labs", role: "Frontend Engineer", opened: "2026-09-11" },
  { company: "Orbit Health", role: "Product Engineer", opened: "2026-09-10" },
  { company: "Canvas AI", role: "Software Engineer", opened: "2026-09-09" },
  { company: "Signalworks", role: "UI Engineer", opened: "2026-09-08" },
];

const appliedJobs: Job[] = [
  { company: "Maple Systems", role: "React Developer", opened: "2026-09-09", status: "In review" },
  { company: "Frame Financial", role: "Frontend Engineer", opened: "2026-09-06", status: "Applied" },
  { company: "Sparrow", role: "Product Developer", opened: "2026-09-04", status: "Follow-up due" },
];

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

function SortIndicator({ active, direction }: { active: boolean; direction: SortDirection }) {
  return <span className={`sort-indicator${active ? " is-active" : ""}`} aria-hidden="true">{active && direction === "desc" ? "↓" : "↑"}</span>;
}

export function Dashboard() {
  const [tab, setTab] = useState<JobTab>("ready");
  const [sort, setSort] = useState<Record<JobTab, SortState>>({
    ready: { key: "opened", direction: "desc" },
    applied: { key: "opened", direction: "desc" },
  });
  const { user } = useAuth({ ensureSignedIn: true });
  const jobs = tab === "ready" ? readyJobs : appliedJobs;
  const currentSort = sort[tab];
  const sortedJobs = useMemo(() => {
    return [...jobs].sort((first, second) => {
      const firstValue = first[currentSort.key] ?? "";
      const secondValue = second[currentSort.key] ?? "";
      const result = firstValue.localeCompare(secondValue, undefined, { sensitivity: "base" });

      return currentSort.direction === "asc" ? result : -result;
    });
  }, [currentSort, jobs]);
  const firstName = user?.firstName ?? user?.name?.split(" ")[0] ?? "there";

  const updateSort = (key: SortKey) => {
    setSort((current) => ({
      ...current,
      [tab]: {
        key,
        direction: current[tab].key === key && current[tab].direction === "asc" ? "desc" : "asc",
      },
    }));
  };

  const sortableHeader = (key: SortKey, label: string, className?: string) => {
    const isActive = currentSort.key === key;

    return (
      <th scope="col" className={className} aria-sort={isActive ? (currentSort.direction === "asc" ? "ascending" : "descending") : "none"}>
        <button type="button" className="sort-button" onClick={() => updateSort(key)}>
          {label}
          <SortIndicator active={isActive} direction={currentSort.direction} />
        </button>
      </th>
    );
  };

  return (
    <div className="dashboard">
      <section className="welcome-row">
        <div><p className="eyebrow">Friday, September 11</p><h1>Good morning, {firstName}.</h1><p className="welcome-copy">Your next opportunity is ready when you are.</p></div>
        <div className="weekly-goal"><span>Weekly goal</span><strong>8 of 10</strong><div className="goal-track"><span /></div></div>
      </section>

      <section className="stat-grid" aria-label="Job search overview">
        <article className="stat-card streak-card"><div className="stat-icon flame"><FlameIcon /></div><div><span className="stat-label">Current streak</span><strong>7 <small>days</small></strong></div><span className="streak-note">Personal best</span></article>
        <article className="stat-card"><div className="stat-icon"><BriefcaseIcon /></div><div><span className="stat-label">Jobs applied</span><strong>24</strong></div><span className="stat-change">+6 this week</span></article>
        <article className="stat-card"><div className="stat-icon"><span className="pending-mark">12</span></div><div><span className="stat-label">Jobs pending</span><strong>4</strong></div><span className="stat-change">Ready to review</span></article>
      </section>

      <section className="jobs-panel" aria-labelledby="jobs-heading">
        <div className="panel-heading">
          <div><p className="eyebrow">Your pipeline</p><h2 id="jobs-heading">Job matches</h2></div>
          <div className="job-tabs" role="tablist" aria-label="Job lists">
            <button type="button" role="tab" aria-selected={tab === "ready"} onClick={() => setTab("ready")}>Jobs to apply <span>4</span></button>
            <button type="button" role="tab" aria-selected={tab === "applied"} onClick={() => setTab("applied")}>Jobs applied <span>24</span></button>
          </div>
        </div>
        <div className="jobs-table-wrap">
          <table className="jobs-table">
            <caption className="sr-only">{tab === "ready" ? "Jobs to apply" : "Applied jobs"}</caption>
            <colgroup>
              <col className="company-column" />
              <col className="role-column" />
              <col className="date-column" />
              <col className="action-column" />
            </colgroup>
            <thead>
              <tr>
                {sortableHeader("company", "Company")}
                {sortableHeader("role", "Role")}
                {sortableHeader("opened", "Date opened")}
                {tab === "applied" ? sortableHeader("status", "Status", "action-heading") : <th scope="col" className="action-heading">Apply</th>}
              </tr>
            </thead>
            <tbody>
              {sortedJobs.map((job) => (
                <tr key={`${job.company}-${job.role}`}>
                  <td><span className="company-cell"><span className="company-mark">{job.company.charAt(0)}</span>{job.company}</span></td>
                  <td>{job.role}</td>
                  <td><time dateTime={job.opened}>{dateFormatter.format(new Date(`${job.opened}T00:00:00Z`))}</time></td>
                  <td className="row-action">
                    {job.status ? <span className={`status-pill ${job.status.toLowerCase().replaceAll(" ", "-")}`}>{job.status}</span> : <a href={`https://www.google.com/search?q=${encodeURIComponent(`${job.company} ${job.role}`)}`} target="_blank" rel="noreferrer" aria-label={`Apply for ${job.role} at ${job.company}`}>Apply <ArrowIcon /></a>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
