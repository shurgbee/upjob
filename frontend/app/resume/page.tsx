import { AuthenticatedShell } from "../_components/AuthenticatedShell";
import { AppShell } from "../_components/AppShell";
import { ResumeWorkspace } from "./ResumeWorkspace";
import "./resume.css";

export const metadata = { title: "Resume | UpJob" };

export default function ResumePage() {
  return (
    <AuthenticatedShell>
      <AppShell showGamification={false}>
        <ResumeWorkspace />
      </AppShell>
    </AuthenticatedShell>
  );
}
