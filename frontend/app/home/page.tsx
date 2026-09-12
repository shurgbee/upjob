import { AppShell } from "../_components/AppShell";
import { AuthenticatedShell } from "../_components/AuthenticatedShell";
import { Dashboard } from "../_components/Dashboard";

export default function HomePage() {
  return <AuthenticatedShell><AppShell><Dashboard /></AppShell></AuthenticatedShell>;
}
