"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addProject,
  insertBullet,
  parseResume,
  replaceBullet,
  type ResumeBullet,
} from "@/lib/resume-latex";
import type {
  Operation,
  Project,
  Resume,
  Suggestion,
  Workspace,
} from "@/lib/resume-types";

async function api<T>(path = "", options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/resume${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
    cache: "no-store",
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

function download(source: string, filename: string) {
  const url = URL.createObjectURL(
    new Blob([source], { type: "application/x-tex;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function BoldText({ text }: { text: string }) {
  return text.split(/(\*\*[^*\n]+?\*\*)/g).map((part, index) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={index}>{part.slice(2, -2)}</strong>
    ) : (
      part
    ),
  );
}

function BulletCard({
  bullet,
  disabled,
  edit,
  review,
}: {
  bullet: ResumeBullet;
  disabled: boolean;
  edit: (text: string) => void;
  review: (kind: "review" | "generate") => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(bullet.editableText);
  const editor = useRef<HTMLTextAreaElement>(null);

  function toggleBold() {
    const field = editor.current;
    if (!field) return;
    const start = field.selectionStart;
    const end = field.selectionEnd;
    const selected = draft.slice(start, end);
    const alreadyBold =
      start >= 2 &&
      draft.slice(start - 2, start) === "**" &&
      draft.slice(end, end + 2) === "**";
    const next = alreadyBold
      ? draft.slice(0, start - 2) + selected + draft.slice(end + 2)
      : draft.slice(0, start) + `**${selected}**` + draft.slice(end);
    const nextStart = alreadyBold ? start - 2 : start + 2;
    const nextEnd = nextStart + selected.length;
    setDraft(next);
    requestAnimationFrame(() => {
      field.focus();
      field.setSelectionRange(nextStart, nextEnd);
    });
  }

  return (
    <article className="resume-bullet">
      {editing ? (
        <>
          <label className="sr-only" htmlFor={`bullet-${bullet.id}`}>
            Edit bullet
          </label>
          <textarea
            ref={editor}
            id={`bullet-${bullet.id}`}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={4}
          />
          <div className="resume-formatting" aria-label="Text formatting">
            <button
              type="button"
              aria-label="Bold selected text"
              title="Bold selected text"
              onClick={toggleBold}
            >
              <strong>B</strong>
            </button>
          </div>
          <p className="resume-hint">
            Select text and choose Bold. Other inline formatting is converted
            to plain text when you save.
          </p>
        </>
      ) : (
        <p>
          <BoldText text={bullet.editableText} />
        </p>
      )}
      <div className="resume-actions">
        {editing ? (
          <>
            <button
              type="button"
              disabled={!draft.replaceAll("**", "").trim()}
              onClick={() => {
                edit(draft);
                setEditing(false);
              }}
            >
              Save bullet
            </button>
            <button type="button" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={() => {
                setDraft(bullet.editableText);
                setEditing(true);
              }}
            >
              Edit
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => review("review")}
            >
              Review
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => review("generate")}
            >
              Regenerate
            </button>
          </>
        )}
      </div>
    </article>
  );
}

export function ResumeWorkspace() {
  const [workspace, setWorkspace] = useState<Workspace>({
    resume: null,
    projects: [],
    operations: [],
  });
  const [source, setSource] = useState("");
  const [saved, setSaved] = useState({
    source: "",
    filename: "resume.tex",
    revision: 0,
  });
  const [filename, setFilename] = useState("resume.tex");
  const [tab, setTab] = useState("Bullets");
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [status, setStatus] = useState("");
  const [repository, setRepository] = useState("");
  const [context, setContext] = useState("");
  const [evidenceProject, setEvidenceProject] = useState("");
  const [targetGroup, setTargetGroup] = useState("");
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [projectDraft, setProjectDraft] = useState<{
    name: string;
    dates: string;
    technologies: string;
  } | null>(null);
  const sourceRef = useRef("");
  const filenameRef = useRef("resume.tex");
  const savedRef = useRef("");
  const savedFilenameRef = useRef("resume.tex");
  const revisionRef = useRef(0);
  const saveTask = useRef<Promise<void> | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const repositoryInput = useRef<HTMLInputElement>(null);
  const activitySnapshot = useRef<Map<string, string>>(new Map());
  const activityReady = useRef(false);
  const parsed = useMemo(() => parseResume(source), [source]);
  const dirty = source !== saved.source || filename !== saved.filename;

  const refresh = useCallback(async (initial = false) => {
    const data = await api<Workspace>();
    setWorkspace((previous) => {
      // A poll started before a save must not move the preview/status backward.
      if (
        data.resume &&
        previous.resume &&
        data.resume.revision < previous.resume.revision
      )
        return { ...data, resume: previous.resume };
      return data;
    });
    if (initial) {
      sourceRef.current = savedRef.current = data.resume?.source ?? "";
      filenameRef.current = savedFilenameRef.current =
        data.resume?.filename ?? "resume.tex";
      revisionRef.current = data.resume?.revision ?? 0;
      setSource(sourceRef.current);
      setFilename(filenameRef.current);
      setSaved({
        source: sourceRef.current,
        filename: filenameRef.current,
        revision: revisionRef.current,
      });
      setLoaded(true);
      setError("");
      setSaveError("");
    }
  }, []);

  useEffect(() => {
    // refresh updates state only after its network request resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh(true).catch((failure: Error) => setError(failure.message));
  }, [refresh]);

  useEffect(() => {
    if (!loaded) return;
    const interval = setInterval(() => {
      void refresh().catch((failure: Error) => setError(failure.message));
    }, 2500);
    return () => clearInterval(interval);
  }, [loaded, refresh]);

  const save = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    if (saveTask.current) await saveTask.current;
    if (
      sourceRef.current === savedRef.current &&
      filenameRef.current === savedFilenameRef.current
    )
      return;
    const task = (async () => {
      setSaving(true);
      setSaveError("");
      try {
        // Capture each revision; edits made while saving remain dirty and are saved next.
        while (
          sourceRef.current !== savedRef.current ||
          filenameRef.current !== savedFilenameRef.current
        ) {
          const text = sourceRef.current;
          const name = filenameRef.current;
          const result = await api<{ revision: number }>("", {
            method: "PUT",
            body: JSON.stringify({
              source: text,
              filename: name,
              revision: revisionRef.current,
            }),
          });
          revisionRef.current = result.revision;
          savedRef.current = text;
          savedFilenameRef.current = name;
          setSaved({ source: text, filename: name, revision: result.revision });
          setWorkspace((old) => ({
            ...old,
            resume: {
              ...old.resume,
              source: text,
              filename: name,
              revision: result.revision,
              pdf_revision: old.resume?.pdf_revision ?? null,
              compile_status: "queued",
              compile_error: null,
            } as Resume,
          }));
        }
      } catch (failure) {
        const message = (failure as Error).message;
        setSaveError(message);
        throw failure;
      } finally {
        setSaving(false);
      }
    })();
    saveTask.current = task;
    try {
      await task;
    } finally {
      if (saveTask.current === task) saveTask.current = null;
    }
  }, []);

  function change(next: string, immediate = false, name = filenameRef.current) {
    sourceRef.current = next;
    filenameRef.current = name;
    setSource(next);
    setFilename(name);
    setStatus("");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(
      () => {
        void save().catch(() => {});
      },
      immediate ? 0 : 750,
    );
  }

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (
        sourceRef.current !== savedRef.current ||
        filenameRef.current !== savedFilenameRef.current
      )
        event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
      if (timer.current) clearTimeout(timer.current);
      // Flush pending edits when navigating within the app before the debounce fires.
      void save().catch(() => {});
    };
  }, [save]);

  async function action(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function upload(file?: File) {
    if (!file) return;
    await action(async () => {
      if (!file.name.toLowerCase().endsWith(".tex") || file.size > 1024 * 1024)
        throw new Error("Choose a .tex file up to 1 MB.");
      const text = new TextDecoder("utf-8", { fatal: true }).decode(
        await file.arrayBuffer(),
      );
      if (!text.trim() || text.includes("\0"))
        throw new Error("Choose a non-empty UTF-8 LaTeX text file.");
      if (
        sourceRef.current &&
        !window.confirm(
          "Replace your current resume with this upload? Download the current LaTeX first if you want to keep a copy.",
        )
      )
        return;
      change(text, true, file.name);
    });
  }

  async function startTemplate() {
    await action(async () => {
      const response = await fetch("/resume-template.tex");
      if (!response.ok) throw new Error("Could not load the starter template.");
      change(await response.text(), true);
      setTab("LaTeX");
    });
  }

  async function requestOperation(
    kind: Operation["kind"],
    bullets: ResumeBullet[] = [],
    projectId = evidenceProject,
    previousSuggestions: string[] = [],
  ) {
    await action(async () => {
      await save();
      await api("/operations", {
        method: "POST",
        body: JSON.stringify({
          kind,
          revision: revisionRef.current || null,
          repository,
          context,
          project_id: projectId || null,
          bullets: bullets.map((bullet) => ({
            id: bullet.id,
            text: bullet.text,
          })),
          previous_suggestions: previousSuggestions,
        }),
      });
      setStatus(
        kind === "scan"
          ? "Repository scan queued. You can keep editing while it runs."
          : "Request queued. Results will appear below.",
      );
      await refresh();
    });
  }

  function applySuggestions(operation: Operation, suggestions: Suggestion[]) {
    try {
      if (dirty || operation.revision !== revisionRef.current)
        throw new Error(
          "Your resume changed since this review. Request a fresh review before applying it.",
        );
      let next = sourceRef.current;
      if (suggestions.every((suggestion) => !suggestion.original)) {
        for (const suggestion of suggestions)
          next = insertBullet(next, targetGroup, suggestion.text);
      } else {
        const replacements = suggestions
          .map((suggestion) => {
            const bullet = parsed.bullets.find(
              (candidate) =>
                candidate.id === suggestion.id &&
                candidate.text === suggestion.original,
            );
            if (!bullet)
              throw new Error("The original bullet changed. Review it again.");
            return { bullet, suggestion };
          })
          .sort((a, b) => b.bullet.start - a.bullet.start);
        for (const { bullet, suggestion } of replacements)
          next = replaceBullet(next, bullet, suggestion.text);
      }
      change(next, true);
      setDismissed((old) => [
        ...old,
        ...suggestions.map((suggestion) => `${operation.id}:${suggestion.id}`),
      ]);
    } catch (failure) {
      setError((failure as Error).message);
    }
  }

  function prepareProject(project: Project) {
    setEvidenceProject(project.project_id);
    setProjectDraft({
      name: project.name,
      dates: "",
      technologies: project.technologies.join(", "),
    });
  }

  const resume = workspace.resume;
  const currentGroups = parsed.groups;
  const operations = workspace.operations.filter(
    (operation) =>
      operation.kind !== "compile" && operation.status !== "superseded",
  );

  useEffect(() => {
    if (!loaded) return;
    const visible = workspace.operations.filter(
      (operation) =>
        operation.kind !== "compile" && operation.status !== "superseded",
    );
    const next = new Map(
      visible.map((operation) => [
        operation.id,
        `${operation.status}:${operation.error ?? ""}:${operation.result ? "result" : ""}`,
      ]),
    );
    if (!activityReady.current) {
      activityReady.current = true;
      activitySnapshot.current = next;
      return;
    }
    const changed = visible.find((operation) => {
      const previous = activitySnapshot.current.get(operation.id);
      if (!previous) return true;
      const actionable =
        operation.status === "succeeded" || operation.status === "failed";
      return actionable && previous !== next.get(operation.id);
    });
    activitySnapshot.current = next;
    if (!changed || dismissed.includes(changed.id)) return;
    const frame = requestAnimationFrame(() => {
      document.getElementById(`resume-operation-${changed.id}`)?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [dismissed, loaded, workspace.operations]);

  return (
    <div className="resume-page">
      <header className="resume-heading">
        <div>
          <span className="resume-eyebrow">YOUR NEXT CHAPTER</span>
          <h1>A resume that speaks for you.</h1>
          <p>Turn what you’ve built into a story worth reading.</p>
        </div>
        <div className="resume-actions">
          {source && (
            <button type="button" onClick={() => download(source, filename)}>
              Download .tex
            </button>
          )}
          <button
            type="button"
            className="resume-primary"
            disabled={!loaded || busy}
            onClick={() => input.current?.click()}
          >
            ↑ Upload LaTeX
          </button>
          <input
            ref={input}
            type="file"
            accept=".tex"
            className="sr-only"
            aria-label="Upload LaTeX resume"
            onChange={(event) => {
              void upload(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </div>
      </header>
      {error && (
        <div className="resume-alert" role="alert">
          {error}
          <button
            type="button"
            onClick={() => {
              void action(() => refresh(!loaded));
            }}
          >
            Retry connection
          </button>
        </div>
      )}
      {saveError && (
        <div className="resume-alert" role="alert">
          {saveError}
          <div className="resume-actions">
            <button
              type="button"
              onClick={() => {
                void action(save);
              }}
            >
              Retry save
            </button>
            <button
              type="button"
              onClick={() => {
                if (
                  window.confirm(
                    "Discard local edits and load the saved resume? Download your .tex first to keep a copy.",
                  )
                )
                  void action(() => refresh(true));
              }}
            >
              Reload saved resume
            </button>
          </div>
        </div>
      )}
      {status && (
        <p className="resume-notice" role="status">
          {status}
        </p>
      )}
      {!loaded ? (
        <div className="resume-empty">
          <h2>
            {error ? "Resume service unavailable" : "Loading your workspace…"}
          </h2>
          <p>Your resume will appear here once the service is connected.</p>
        </div>
      ) : !source && !resume ? (
        <div className="resume-empty">
          <span className="resume-empty-icon">↥</span>
          <h2>Your experience. Your next opportunity.</h2>
          <p>
            Upload your LaTeX resume to preview and improve it, or start with a
            template using Jake’s Resume conventions.
          </p>
          <div className="resume-actions">
            <button
              type="button"
              className="resume-primary"
              onClick={() => input.current?.click()}
            >
              Upload .tex file
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void startTemplate()}
            >
              Start with a template
            </button>
          </div>
          <small>
            Single .tex file · up to 1 MB · your formatting stays yours
          </small>
        </div>
      ) : (
        <>
          <div className="resume-filebar">
            <span>▤ {filename}</span>
            <span aria-live="polite">
              {saving
                ? "Saving…"
                : dirty
                  ? "Unsaved changes"
                  : `Saved · revision ${resume?.revision ?? 0}`}
            </span>
          </div>
          <div className="resume-grid">
            <section
              className="resume-panel resume-editor"
              aria-label="Resume editing tools"
            >
              <div
                className="resume-tabs"
                role="tablist"
                aria-label="Resume tools"
              >
                {["Bullets", "LaTeX", "Projects"].map((name) => (
                  <button
                    key={name}
                    id={`tab-${name}`}
                    role="tab"
                    type="button"
                    aria-selected={tab === name}
                    aria-controls="resume-tabpanel"
                    onClick={() => setTab(name)}
                  >
                    {name}
                  </button>
                ))}
              </div>
              <div
                id="resume-tabpanel"
                role="tabpanel"
                aria-labelledby={`tab-${tab}`}
                className="resume-tabbody"
              >
                {tab === "LaTeX" && (
                  <>
                    <div className="resume-section-heading">
                      <div>
                        <h2>Make it yours</h2>
                        <p>Edits save and update the PDF automatically.</p>
                      </div>
                    </div>
                    <label className="sr-only" htmlFor="latex-source">
                      LaTeX source
                    </label>
                    <textarea
                      id="latex-source"
                      className="resume-source"
                      value={source}
                      spellCheck={false}
                      onChange={(event) => change(event.target.value)}
                    />
                  </>
                )}
                {tab === "Bullets" && (
                  <>
                    <div className="resume-section-heading">
                      <div>
                        <h2>Make every line count</h2>
                        <p>Review your impact. Keep the final say.</p>
                      </div>
                      <button
                        type="button"
                        disabled={busy || !parsed.bullets.length}
                        onClick={() =>
                          void requestOperation("review", parsed.bullets)
                        }
                      >
                        Review all
                      </button>
                    </div>
                    <label className="resume-label">
                      Project evidence for review
                      <select
                        value={evidenceProject}
                        onChange={(event) =>
                          setEvidenceProject(event.target.value)
                        }
                      >
                        <option value="">
                          Use the bullet’s existing facts
                        </option>
                        {workspace.projects.map((project) => (
                          <option
                            key={project.project_id}
                            value={project.project_id}
                          >
                            {project.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="resume-label">
                      Additional facts or verified metrics
                      <textarea
                        rows={2}
                        value={context}
                        onChange={(event) => setContext(event.target.value)}
                        placeholder="Your contribution, measured outcomes, or important context…"
                        maxLength={10000}
                      />
                    </label>
                    {!parsed.bullets.length && (
                      <div className="resume-inline-empty">
                        <h3>No recognized bullets yet</h3>
                        <p>
                          Structured review supports Jake’s Resume item
                          commands. You can still edit any template in the LaTeX
                          tab.
                        </p>
                      </div>
                    )}
                    {parsed.bullets.map((bullet, index) => (
                      <div key={`${bullet.id}:${bullet.latex}`}>
                        {(index === 0 ||
                          parsed.bullets[index - 1].group !== bullet.group) && (
                          <h3 className="resume-group-title">
                            {currentGroups.find(
                              (group) => group.id === bullet.group,
                            )?.title ?? "Resume bullets"}
                          </h3>
                        )}
                        <BulletCard
                          bullet={bullet}
                          disabled={busy}
                          edit={(text) =>
                            change(
                              replaceBullet(sourceRef.current, bullet, text),
                              true,
                            )
                          }
                          review={(kind) =>
                            void requestOperation(kind, [bullet])
                          }
                        />
                      </div>
                    ))}
                  </>
                )}
                {tab === "Projects" && (
                  <>
                    <div className="resume-section-heading">
                      <div>
                        <h2>Let your work do the talking</h2>
                        <p>
                          Scan a public GitHub repository for resume material.
                        </p>
                      </div>
                    </div>
                    <form
                      onSubmit={(event) => {
                        event.preventDefault();
                        void requestOperation("scan", [], "");
                      }}
                      className="resume-scan"
                    >
                      <label className="resume-label">
                        GitHub repository
                        <input
                          ref={repositoryInput}
                          required
                          value={repository}
                          onChange={(event) =>
                            setRepository(event.target.value)
                          }
                          placeholder="https://github.com/you/project"
                          maxLength={500}
                        />
                      </label>
                      <label className="resume-label">
                        Your contribution and impact <span>(optional)</span>
                        <textarea
                          value={context}
                          onChange={(event) => setContext(event.target.value)}
                          rows={3}
                          placeholder="What did you build? Include any verified metrics."
                          maxLength={10000}
                        />
                      </label>
                      <button
                        className="resume-primary"
                        disabled={busy || !repository.trim()}
                        type="submit"
                      >
                        Scan repository ↗
                      </button>
                      <p className="resume-hint">
                        Analysis runs in the background and is saved to your
                        account.
                      </p>
                    </form>
                    <label className="resume-label">
                      Resume entry to receive generated bullets
                      <select
                        value={targetGroup}
                        onChange={(event) => setTargetGroup(event.target.value)}
                      >
                        <option value="">Select an existing entry</option>
                        {currentGroups.map((group) => (
                          <option key={group.id} value={group.id}>
                            {group.title}
                          </option>
                        ))}
                      </select>
                    </label>
                    {workspace.projects.length === 0 && (
                      <p className="resume-inline-empty">
                        Your analyzed projects will appear here.
                      </p>
                    )}
                    {workspace.projects.map((project) => (
                      <article
                        className="resume-project"
                        key={project.project_id}
                      >
                        <h3>{project.name}</h3>
                        <p>{project.description}</p>
                        <div className="resume-tags">
                          {project.technologies.map((technology) => (
                            <span key={technology}>{technology}</span>
                          ))}
                        </div>
                        <details>
                          <summary>Evidence from this project</summary>
                          <p>
                            <strong>Architecture:</strong>{" "}
                            {project.architecture.join(", ") ||
                              "Not identified"}
                          </p>
                          {project.analysis.Actions?.map((item, index) => (
                            <p key={index}>• {item}</p>
                          ))}
                          <p>
                            <strong>Metrics:</strong>{" "}
                            {project.analysis.Metrics?.join("; ") ||
                              "No verified metrics found."}
                          </p>
                          {project.user_context && (
                            <p>
                              <strong>Your context:</strong>{" "}
                              {project.user_context}
                            </p>
                          )}
                        </details>
                        <div className="resume-actions">
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() =>
                              void requestOperation(
                                "generate",
                                [],
                                project.project_id,
                              )
                            }
                          >
                            Generate 3 bullets
                          </button>
                          <button
                            type="button"
                            onClick={() => prepareProject(project)}
                          >
                            Add project entry
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setEvidenceProject(project.project_id);
                              setTab("Bullets");
                            }}
                          >
                            Use for review
                          </button>
                        </div>
                      </article>
                    ))}
                    {projectDraft && (
                      <form
                        className="resume-project"
                        onSubmit={(event) => {
                          event.preventDefault();
                          try {
                            const next = addProject(
                              sourceRef.current,
                              projectDraft.name,
                              projectDraft.dates,
                              projectDraft.technologies,
                            );
                            change(next, true);
                            const groups = parseResume(next).groups;
                            setTargetGroup(groups[groups.length - 1]?.id ?? "");
                            setProjectDraft(null);
                          } catch (failure) {
                            setError((failure as Error).message);
                          }
                        }}
                      >
                        <h3>Review the new project entry</h3>
                        <label className="resume-label">
                          Project name
                          <input
                            required
                            value={projectDraft.name}
                            onChange={(event) =>
                              setProjectDraft({
                                ...projectDraft,
                                name: event.target.value,
                              })
                            }
                          />
                        </label>
                        <label className="resume-label">
                          Dates
                          <input
                            value={projectDraft.dates}
                            onChange={(event) =>
                              setProjectDraft({
                                ...projectDraft,
                                dates: event.target.value,
                              })
                            }
                            placeholder="e.g. June 2025 – Present"
                          />
                        </label>
                        <label className="resume-label">
                          Technologies
                          <input
                            value={projectDraft.technologies}
                            onChange={(event) =>
                              setProjectDraft({
                                ...projectDraft,
                                technologies: event.target.value,
                              })
                            }
                          />
                        </label>
                        <div className="resume-actions">
                          <button type="submit">Insert entry</button>
                          <button
                            type="button"
                            onClick={() => setProjectDraft(null)}
                          >
                            Cancel
                          </button>
                        </div>
                      </form>
                    )}
                  </>
                )}
                {operations.length > 0 && (
                  <section
                    className="resume-results"
                    aria-label="Analysis and review results"
                  >
                    <h2>Activity & suggestions</h2>
                    {operations.map((operation) => {
                      const allSuggestions =
                        operation.result?.suggestions ?? [];
                      const suggestions = allSuggestions.filter(
                          (suggestion) =>
                            !dismissed.includes(
                              `${operation.id}:${suggestion.id}`,
                            ),
                        );
                      const stale =
                        dirty || operation.revision !== saved.revision;
                      const inserting =
                        suggestions.length > 0 && !suggestions[0].original;
                      const isCollapsed = collapsed.includes(operation.id);
                      return (
                        <article
                          className="resume-operation"
                          key={operation.id}
                          id={`resume-operation-${operation.id}`}
                          tabIndex={-1}
                        >
                          <div className="resume-operation-heading">
                            <strong>
                              {operation.kind === "scan"
                                ? "Repository analysis"
                                : operation.kind === "generate"
                                  ? "Generated bullets"
                                  : "Bullet review"}
                            </strong>
                            <span>{operation.status}</span>
                          </div>
                          {isCollapsed ? (
                            <button
                              type="button"
                              className="resume-dismiss"
                              onClick={() =>
                                setCollapsed((old) =>
                                  old.filter((id) => id !== operation.id),
                                )
                              }
                            >
                              Unhide
                            </button>
                          ) : (
                            <>
                          {operation.status === "queued" && (
                            <p className="resume-hint">
                              Waiting for the worker. You can continue editing.
                            </p>
                          )}
                          {operation.status === "running" && (
                            <p className="resume-hint" role="status">
                              Working through the evidence…
                            </p>
                          )}
                          {operation.error && (
                            <p role="alert">{operation.error}</p>
                          )}
                          {operation.status === "failed" && (
                            <div className="resume-actions">
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() =>
                                  void action(async () => {
                                    await api("/retry", {
                                      method: "POST",
                                      body: JSON.stringify({ id: operation.id }),
                                    });
                                    await refresh();
                                  })
                                }
                              >
                                Retry
                              </button>
                              {operation.kind === "scan" && (
                                <button
                                  type="button"
                                  onClick={() => {
                                    setTab("Projects");
                                    requestAnimationFrame(() =>
                                      repositoryInput.current?.focus(),
                                    );
                                  }}
                                >
                                  Add a repository
                                </button>
                              )}
                            </div>
                          )}
                          {suggestions.length > 0 && (
                            <>
                              {stale ? (
                                <p className="resume-hint">
                                  The resume has changed. Request a fresh review
                                  before applying these suggestions.
                                </p>
                              ) : (
                                <div className="resume-actions">
                                  {inserting && (
                                    <label className="resume-label">
                                      Insert into
                                      <select
                                        value={targetGroup}
                                        onChange={(event) =>
                                          setTargetGroup(event.target.value)
                                        }
                                      >
                                        <option value="">
                                          Select an entry
                                        </option>
                                        {currentGroups.map((group) => (
                                          <option
                                            key={group.id}
                                            value={group.id}
                                          >
                                            {group.title}
                                          </option>
                                        ))}
                                      </select>
                                    </label>
                                  )}
                                  <button
                                    type="button"
                                    className="resume-accept"
                                    disabled={inserting && !targetGroup}
                                    onClick={() =>
                                      applySuggestions(operation, suggestions)
                                    }
                                  >
                                    Accept all {suggestions.length}
                                  </button>
                                </div>
                              )}
                              {suggestions.map((suggestion) => (
                                <div
                                  className="resume-suggestion"
                                  key={suggestion.id}
                                >
                                  {suggestion.original && (
                                    <>
                                      <small>ORIGINAL</small>
                                      <p className="resume-original">
                                        {suggestion.original}
                                      </p>
                                    </>
                                  )}
                                  <small>SUGGESTION</small>
                                  <p>
                                    <BoldText text={suggestion.text} />
                                  </p>
                                  <ul className="resume-feedback">
                                    {suggestion.feedback.map(
                                      (feedback, index) => (
                                        <li key={index}>{feedback}</li>
                                      ),
                                    )}
                                  </ul>
                                  <div className="resume-actions">
                                    <button
                                      type="button"
                                      className="resume-accept"
                                      disabled={
                                        stale || (inserting && !targetGroup)
                                      }
                                      onClick={() =>
                                        applySuggestions(operation, [
                                          suggestion,
                                        ])
                                      }
                                    >
                                      Accept
                                    </button>
                                    <button
                                      type="button"
                                      className="resume-reject"
                                      onClick={() =>
                                        setDismissed((old) => [
                                          ...old,
                                          `${operation.id}:${suggestion.id}`,
                                        ])
                                      }
                                    >
                                      Reject
                                    </button>
                                  </div>
                                </div>
                              ))}
                            </>
                          )}
                          {operation.status === "succeeded" &&
                            operation.kind !== "scan" &&
                            allSuggestions.length > 0 && (
                              <div className="resume-actions resume-regenerate">
                                <button
                                  type="button"
                                  disabled={busy}
                                  onClick={() => {
                                    const bullets =
                                      operation.payload.bullets
                                        ?.map((item) =>
                                          parsed.bullets.find(
                                            (bullet) =>
                                              bullet.id === item.id &&
                                              bullet.text === item.text,
                                          ),
                                        )
                                        .filter(
                                          (item): item is ResumeBullet =>
                                            !!item,
                                        ) ?? [];
                                    if (
                                      operation.payload.bullets?.length &&
                                      bullets.length !==
                                        operation.payload.bullets.length
                                    ) {
                                      setError(
                                        "Select the updated bullets and review them again.",
                                      );
                                      return;
                                    }
                                    void requestOperation(
                                      "generate",
                                      bullets,
                                      operation.payload.project_id ?? "",
                                      allSuggestions.map(
                                        (suggestion) => suggestion.text,
                                      ),
                                    );
                                    setCollapsed((old) =>
                                      old.includes(operation.id)
                                        ? old
                                        : [...old, operation.id],
                                    );
                                  }}
                                >
                                  Generate suggestions
                                </button>
                              </div>
                            )}
                          {operation.status === "succeeded" &&
                            operation.kind === "scan" && (
                              <p>
                                Saved to your projects. Open Projects to
                                generate bullets.
                              </p>
                            )}
                          {["succeeded", "failed", "superseded"].includes(
                            operation.status,
                          ) && (
                            <button
                              type="button"
                              className="resume-dismiss"
                              onClick={() =>
                                setCollapsed((old) => [...old, operation.id])
                              }
                            >
                              Hide
                            </button>
                          )}
                            </>
                          )}
                        </article>
                      );
                    })}
                  </section>
                )}
              </div>
            </section>
            <section
              className="resume-panel resume-preview"
              aria-label="PDF preview"
            >
              <div className="resume-preview-heading">
                <div>
                  <h2>Live preview</h2>
                  <span aria-live="polite">
                    {resume?.compile_status === "running"
                      ? "Compiling…"
                      : resume?.compile_status === "queued"
                        ? "Compilation queued"
                        : resume?.pdf_revision
                          ? `PDF · revision ${resume.pdf_revision}`
                          : "Waiting for your first PDF"}
                    {resume?.pdf_revision &&
                    (dirty || resume.pdf_revision !== resume.revision)
                      ? " · showing last successful render"
                      : ""}
                  </span>
                </div>
                {resume?.pdf_revision && (
                  <a
                    href={`/api/resume/pdf?v=${resume.pdf_revision}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open PDF ↗
                  </a>
                )}
              </div>
              {resume?.compile_error && (
                <div className="resume-compile-error" role="alert">
                  <strong>We couldn’t compile this revision.</strong>
                  <pre>{resume.compile_error}</pre>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void requestOperation("compile", [], "")}
                  >
                    Retry compilation
                  </button>
                </div>
              )}
              {resume?.pdf_revision ? (
                <object
                  key={resume.pdf_revision}
                  data={`/api/resume/pdf?v=${resume.pdf_revision}`}
                  type="application/pdf"
                  className="resume-pdf"
                  aria-label="Compiled resume PDF"
                >
                  <p>
                    Your browser cannot display PDFs inline.{" "}
                    <a href="/api/resume/pdf" target="_blank" rel="noreferrer">
                      Open or download your PDF.
                    </a>
                  </p>
                </object>
              ) : (
                <div className="resume-preview-empty">
                  <span>▤</span>
                  <h3>Your resume, in focus.</h3>
                  <p>
                    The compiled PDF will appear here automatically. You can
                    keep editing while it renders.
                  </p>
                  {resume?.compile_status === "queued" && (
                    <small>
                      Compilation requires the resume worker to be running.
                    </small>
                  )}
                </div>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
