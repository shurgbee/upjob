"use client";

import { useState } from "react";
import { ArrowIcon, BriefcaseIcon, FlameIcon } from "./Icons";

type Job = {
  company: string;
  role: string;
  opened: string;
  status?: string;
};

const readyJobs: Job[] = [
  { company: "Northstar Labs", role: "Frontend Engineer", opened: "Sep 11" },
  { company: "Orbit Health", role: "Product Engineer", opened: "Sep 10" },
  { company: "Canvas AI", role: "Software Engineer", opened: "Sep 9" },
  { company: "Signalworks", role: "UI Engineer", opened: "Sep 8" },
];

const appliedJobs: Job[] = [
  { company: "Maple Systems", role: "React Developer", opened: "Sep 9", status: "In review" },
  { company: "Frame Financial", role: "Frontend Engineer", opened: "Sep 6", status: "Applied" },
  { company: "Sparrow", role: "Product Developer", opened: "Sep 4", status: "Follow-up due" },
];

export function Dashboard() {
  const [tab, setTab] = useState<"ready" | "applied">("ready");
  const jobs = tab === "ready" ? readyJobs : appliedJobs;

  return (
    <div className="dashboard">
      <section className="welcome-row">
        <div><p className="eyebrow">Friday, September 11</p><h1>Good morning, Jordan.</h1><p className="welcome-copy">Your next opportunity is ready when you are.</p></div>
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
        <div className="jobs-table" role="table" aria-label={tab === "ready" ? "Jobs to apply" : "Applied jobs"}>
          <div className="jobs-row jobs-table-head" role="row"><span role="columnheader">Company</span><span role="columnheader">Role</span><span role="columnheader">Date opened</span><span role="columnheader">{tab === "ready" ? "Details" : "Status"}</span></div>
          {jobs.map((job) => (
            <div className="jobs-row" role="row" key={`${job.company}-${job.role}`}>
              <span role="cell" className="company-cell"><span className="company-mark">{job.company.charAt(0)}</span>{job.company}</span>
              <span role="cell">{job.role}</span><span role="cell">{job.opened}</span>
              <span role="cell" className="row-action">
                {job.status ? <span className={`status-pill ${job.status.toLowerCase().replaceAll(" ", "-")}`}>{job.status}</span> : <a href={`https://www.google.com/search?q=${encodeURIComponent(`${job.company} ${job.role}`)}`} target="_blank" rel="noreferrer" aria-label={`View ${job.role} at ${job.company}`}>Go <ArrowIcon /></a>}
              </span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
