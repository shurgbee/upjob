"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import type {
  SkillEvaluation,
  SkillOperation,
  SkillQuest,
  SkillWorkspace,
} from "@/lib/skill-types";

async function api<T>(path = "", options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/skills${path}`, {
    ...options,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item: { msg: string }) => item.msg).join(" ")
          : "Request failed. Please retry.",
    );
  }
  return response.json();
}

const emptyWorkspace: SkillWorkspace = {
  job_count: 0,
  known_skill_count: 0,
  missing_skills: [],
  quests: [],
  operations: [],
};

function statusLabel(quest: SkillQuest) {
  if (quest.status === "passed") return "Verified";
  if (quest.status === "needs_work") return "Keep building";
  if (quest.status === "evaluating") return "Evaluating";
  return "Ready to build";
}

function feedbackItems(feedback: string) {
  const items = feedback
    .split(/\r?\n/)
    .map((item) => item.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, "").trim())
    .filter(Boolean);
  return items.length > 0 ? items : [feedback];
}

function EvaluationResult({ evaluation }: { evaluation: SkillEvaluation }) {
  const scoreStyle = {
    "--score": `${evaluation.Score * 3.6}deg`,
  } as CSSProperties;
  return (
    <section className="skill-evaluation" aria-label="Repository evaluation">
      <div
        className="skill-score"
        style={scoreStyle}
        aria-label={`${evaluation.Score} out of 100`}
      >
        <span>{evaluation.Score}</span>
        <small>/100</small>
      </div>
      <div className="skill-evaluation-copy">
        <h4>
          {evaluation.Passed_all_criteria
            ? "Objective demonstrated"
            : "Needs some work"}
        </h4>
        {evaluation.Passed_criteria.length > 0 && (
          <div className="criteria-group criteria-passed">
            <strong>Demonstrated</strong>
            <ul>
              {evaluation.Passed_criteria.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}
        {evaluation.Failed_criteria.length > 0 && (
          <div className="criteria-group criteria-failed">
            <strong>Still to prove</strong>
            <ul>
              {evaluation.Failed_criteria.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}
        <div className="skill-feedback">
          <strong>Next move</strong>
          <ul>
            {feedbackItems(evaluation.Actionable_Feedback).map(
              (item, index) => (
                <li key={`${index}-${item}`}>{item}</li>
              ),
            )}
          </ul>
        </div>
      </div>
    </section>
  );
}

function QuestCard({
  quest,
  number,
  operation,
  disabled,
  onSubmit,
  onRetry,
}: {
  quest: SkillQuest;
  number: number;
  operation?: SkillOperation;
  disabled: boolean;
  onSubmit: (questId: string, repository: string) => Promise<void>;
  onRetry: (operationId: string) => Promise<void>;
}) {
  const [repository, setRepository] = useState(quest.repository_url ?? "");
  const busy =
    quest.status === "evaluating" ||
    ["queued", "running"].includes(operation?.status ?? "");
  return (
    <article className={`skill-quest status-${quest.status}`}>
      <div className="quest-number" aria-hidden="true">
        {String(number).padStart(2, "0")}
      </div>
      <div className="quest-heading">
        <span className={`quest-status ${quest.status}`}>
          {statusLabel(quest)}
        </span>
        <h3>{quest.title}</h3>
        <p>{quest.description}</p>
      </div>
      <div className="skill-tags" aria-label="Skills this project builds">
        {quest.target_skills.map((skill) => (
          <span key={skill}>{skill}</span>
        ))}
      </div>
      <div className="quest-criteria">
        <strong>Definition of done</strong>
        <ul>
          {quest.acceptance_criteria.map((criterion) => (
            <li key={criterion}>{criterion}</li>
          ))}
        </ul>
      </div>

      {busy && (
        <div className="quest-working" role="status">
          <span className="working-pulse" />
          {operation?.status === "queued"
            ? "Waiting for an analyzer…"
            : "Reading the repository evidence…"}
        </div>
      )}
      {operation?.status === "failed" && (
        <div className="skill-error" role="alert">
          <p>{operation.error}</p>
          <button
            type="button"
            disabled={disabled}
            onClick={() => void onRetry(operation.id)}
          >
            Retry evaluation
          </button>
        </div>
      )}
      {quest.evaluation && <EvaluationResult evaluation={quest.evaluation} />}

      {!busy && operation?.status !== "failed" && (
        <form
          className="quest-submit"
          onSubmit={(event) => {
            event.preventDefault();
            void onSubmit(quest.quest_id, repository);
          }}
        >
          <label htmlFor={`repo-${quest.quest_id}`}>
            {quest.evaluation
              ? "Submit an updated repository"
              : "Finished building? Submit your public repo"}
          </label>
          <div>
            <input
              id={`repo-${quest.quest_id}`}
              type="url"
              required
              maxLength={500}
              value={repository}
              onChange={(event) => setRepository(event.target.value)}
              placeholder="https://github.com/you/project"
              autoComplete="url"
            />
            <button type="submit" disabled={disabled || !repository.trim()}>
              {quest.evaluation ? "Evaluate again" : "Evaluate project"}
            </button>
          </div>
          <small>
            Public GitHub repositories only. Analysis uses code and test
            evidence.
          </small>
        </form>
      )}
    </article>
  );
}

export function SkillsWorkspace() {
  const [workspace, setWorkspace] = useState(emptyWorkspace);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const planRequested = useRef(false);

  const refresh = useCallback(async () => {
    const data = await api<SkillWorkspace>();
    setWorkspace(data);
    setLoaded(true);
    return data;
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await refresh();
        if (cancelled) return;
        const hasPlan = data.operations.some(
          (operation) => operation.kind === "plan",
        );
        if (
          !planRequested.current &&
          !hasPlan &&
          data.quests.length === 0 &&
          data.missing_skills.length > 0
        ) {
          planRequested.current = true;
          await api("/plan", { method: "POST", body: "{}" });
          await refresh();
        }
      } catch (failure) {
        if (!cancelled) {
          setError((failure as Error).message);
          setLoaded(true);
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const active = workspace.operations.some((operation) =>
    ["queued", "running"].includes(operation.status),
  );
  useEffect(() => {
    if (!active) return;
    const timer = setTimeout(
      () =>
        void refresh().catch((failure) => setError((failure as Error).message)),
      2200,
    );
    return () => clearTimeout(timer);
  }, [active, refresh, workspace.operations]);

  async function action(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await work();
      await refresh();
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const planOperation = workspace.operations.find(
    (operation) => operation.kind === "plan",
  );
  const completed = workspace.quests.filter(
    (quest) => quest.status === "passed",
  ).length;
  const topCount = workspace.missing_skills[0]?.job_count ?? 1;

  return (
    <div className="skills-page">
      <header className="skills-heading">
        <div>
          <p className="eyebrow">Skill signal</p>
          <h1>Build what employers are asking for.</h1>
          <p>
            Turn the most common gaps in your job matches into work you can
            prove.
          </p>
        </div>
        <div
          className="skills-progress"
          aria-label={`${completed} of ${workspace.quests.length || 3} projects verified`}
        >
          <span>Projects verified</span>
          <strong>
            {completed}
            <small> / {workspace.quests.length || 3}</small>
          </strong>
          <div>
            <i
              style={{
                width: `${workspace.quests.length ? (completed / workspace.quests.length) * 100 : 0}%`,
              }}
            />
          </div>
        </div>
      </header>

      {error && (
        <div className="skills-alert" role="alert">
          <span>{error}</span>
          <button
            type="button"
            onClick={() =>
              void action(async () => {
                await refresh();
              })
            }
          >
            Try again
          </button>
        </div>
      )}

      {!loaded ? (
        <div className="skills-loading" role="status">
          <span />
          Scanning job competencies and your projects…
        </div>
      ) : (
        <>
          <section className="skill-radar" aria-labelledby="gap-heading">
            <div className="radar-summary">
              <p className="eyebrow">Your opportunity map</p>
              <h2 id="gap-heading">
                {workspace.missing_skills.length} skills worth adding
              </h2>
              <p>
                Based on {workspace.job_count} current job{" "}
                {workspace.job_count === 1 ? "listing" : "listings"} and{" "}
                {workspace.known_skill_count} skills you already demonstrate.
              </p>
              <div className="radar-stat">
                <strong>{workspace.missing_skills[0]?.job_count ?? 0}</strong>
                <span>jobs request your top missing skill</span>
              </div>
            </div>
            <div className="gap-list">
              {workspace.missing_skills.slice(0, 8).map((skill, index) => (
                <div className="gap-row" key={skill.name}>
                  <span className="gap-rank">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <strong>{skill.name}</strong>
                  <div className="gap-track">
                    <i
                      style={{
                        width: `${(skill.job_count / topCount) * 100}%`,
                      }}
                    />
                  </div>
                  <span>
                    {skill.job_count} {skill.job_count === 1 ? "job" : "jobs"}
                  </span>
                </div>
              ))}
              {workspace.job_count === 0 && (
                <p className="gap-empty">
                  Job competencies will appear here after listings are imported.
                </p>
              )}
              {workspace.job_count > 0 &&
                workspace.missing_skills.length === 0 && (
                  <p className="gap-empty">
                    You already demonstrate every competency in the current job
                    set.
                  </p>
                )}
            </div>
          </section>

          <section className="quest-section" aria-labelledby="quests-heading">
            <div className="quest-section-heading">
              <div>
                <p className="eyebrow">Three ways forward</p>
                <h2 id="quests-heading">Your build list</h2>
              </div>
              <p>
                Complete a project, publish the repository, then let UpJob check
                the evidence.
              </p>
            </div>

            {workspace.quests.length === 0 &&
              planOperation &&
              ["queued", "running"].includes(planOperation.status) && (
                <div className="plan-state" role="status">
                  <span className="working-pulse" />
                  <div>
                    <strong>Designing your project plan</strong>
                    <p>Grouping related gaps into three practical builds…</p>
                  </div>
                </div>
              )}
            {workspace.quests.length === 0 &&
              planOperation?.status === "failed" && (
                <div className="plan-state plan-error">
                  <div>
                    <strong>We couldn’t create the plan</strong>
                    <p>{planOperation.error}</p>
                  </div>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void action(async () => {
                        await api("/retry", {
                          method: "POST",
                          body: JSON.stringify({ id: planOperation.id }),
                        });
                      })
                    }
                  >
                    Retry
                  </button>
                </div>
              )}
            {workspace.quests.length === 0 &&
              !planOperation &&
              workspace.missing_skills.length === 0 && (
                <div className="plan-state">
                  <div>
                    <strong>No build projects needed yet</strong>
                    <p>
                      Add job listings or scan more repositories to refresh your
                      skill signal.
                    </p>
                  </div>
                </div>
              )}

            <div className="quest-grid">
              {workspace.quests.map((quest, index) => (
                <QuestCard
                  key={quest.quest_id}
                  quest={quest}
                  number={index + 1}
                  operation={workspace.operations.find(
                    (operation) =>
                      operation.kind === "evaluate" &&
                      operation.quest_id === quest.quest_id,
                  )}
                  disabled={busy}
                  onSubmit={async (questId, repository) => {
                    await action(async () => {
                      await api(`/quests/${questId}/evaluate`, {
                        method: "POST",
                        body: JSON.stringify({ repository }),
                      });
                    });
                  }}
                  onRetry={async (operationId) => {
                    await action(async () => {
                      await api("/retry", {
                        method: "POST",
                        body: JSON.stringify({ id: operationId }),
                      });
                    });
                  }}
                />
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
