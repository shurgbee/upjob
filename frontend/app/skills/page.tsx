import type { Metadata } from "next";
import { AppShell } from "../_components/AppShell";
import { AuthenticatedShell } from "../_components/AuthenticatedShell";
import { SkillsWorkspace } from "./SkillsWorkspace";
import "./skills.css";

export const metadata: Metadata = {
  title: "Skill Builder",
  description: "Find the skills employers request and build projects that prove them.",
};

export default function SkillsPage() {
  return (
    <AuthenticatedShell>
      <AppShell>
        <SkillsWorkspace />
      </AppShell>
    </AuthenticatedShell>
  );
}
