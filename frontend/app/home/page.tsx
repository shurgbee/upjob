import { AppShell } from "../_components/AppShell";
import { AuthenticatedShell } from "../_components/AuthenticatedShell";
import { Dashboard } from "../_components/Dashboard";
import { getAppliedJobs, getCurrentUserId, getJobSpecs } from "@/lib/jobs";
import { connection } from "next/server";

export default async function HomePage() {
  await connection();
  const userId = await getCurrentUserId();
  const [jobs, appliedJobs] = await Promise.all([
    getJobSpecs(userId),
    getAppliedJobs(userId),
  ]);

  return (
    <AuthenticatedShell>
      <AppShell>
        <Dashboard jobs={jobs} appliedJobs={appliedJobs} />
      </AppShell>
    </AuthenticatedShell>
  );
}
