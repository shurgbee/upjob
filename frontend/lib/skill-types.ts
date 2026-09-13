export type MissingSkill = {
  name: string;
  job_count: number;
};

export type SkillEvaluation = {
  Passed_all_criteria: boolean;
  Score: number;
  Passed_criteria: string[];
  Failed_criteria: string[];
  Actionable_Feedback: string;
};

export type SkillQuest = {
  quest_id: string;
  title: string;
  description: string;
  target_skills: string[];
  learning_objective: string;
  acceptance_criteria: string[];
  status: "ready" | "evaluating" | "passed" | "needs_work";
  repository_url: string | null;
  evaluation: SkillEvaluation | null;
  created_at: string;
  updated_at: string;
};

export type SkillOperation = {
  id: string;
  quest_id: string | null;
  kind: "plan" | "evaluate";
  status: "queued" | "running" | "succeeded" | "failed";
  payload: { repository?: string };
  result: SkillEvaluation | { quest_count: number } | null;
  error: string | null;
  created_at: string;
};

export type SkillWorkspace = {
  job_count: number;
  known_skill_count: number;
  missing_skills: MissingSkill[];
  quests: SkillQuest[];
  operations: SkillOperation[];
};
