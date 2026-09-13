export type Resume = {
  source: string;
  filename: string;
  revision: number;
  pdf_revision: number | null;
  compile_status: string;
  compile_error: string | null;
};
export type Project = {
  project_id: string;
  name: string;
  description: string;
  technologies: string[];
  architecture: string[];
  github_repo_url: string | null;
  user_context: string;
  analysis: { Summary?: string; Actions?: string[]; Metrics?: string[] };
};
export type Suggestion = {
  id: string;
  original: string;
  text: string;
  feedback: string[];
};
export type Operation = {
  id: string;
  kind: "compile" | "scan" | "review" | "generate";
  status: string;
  error: string | null;
  revision: number | null;
  payload: {
    bullets?: { id: string; text: string }[];
    project_id?: string;
    repository?: string;
    previous_suggestions?: string[];
  };
  result: { suggestions?: Suggestion[] } | null;
  created_at: string;
};
export type Workspace = {
  resume: Resume | null;
  projects: Project[];
  operations: Operation[];
};
