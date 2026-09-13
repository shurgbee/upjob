import { AppShell } from "../_components/AppShell";
import { AuthenticatedShell } from "../_components/AuthenticatedShell";
import { Dashboard } from "../_components/Dashboard";
import { getJobSpecs } from "@/lib/jobs";
import { connection } from "next/server";

export default async function HomePage() {
  await connection();
  const jobs = await getJobSpecs();

  return <AuthenticatedShell><AppShell showGamification={false}><Dashboard jobs={jobs} /></AppShell></AuthenticatedShell>;
}
