import { withAuth } from "@workos-inc/authkit-nextjs";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { BriefcaseIcon, SparkIcon } from "../_components/Icons";

export const metadata: Metadata = {
  title: "Log in",
  description: "Log in to your UpJob workspace.",
};

export default async function LoginPage() {
  const { user } = await withAuth();

  if (user) {
    redirect("/home");
  }

  return (
    <main className="login-page">
      <section className="login-story">
        <Link href="/" className="wordmark login-wordmark" aria-label="UpJob home">
          <span>upjob</span><span className="wordmark-smile" aria-hidden="true" />
        </Link>
        <div className="login-story-copy">
          <p className="eyebrow">Your next move</p>
          <h1>Keep every opportunity moving forward.</h1>
          <p>Applications, follow-ups, and rewards stay together in one focused workspace.</p>
        </div>
        <div className="login-proof" aria-hidden="true">
          <div><BriefcaseIcon /><span>4 roles ready</span></div>
          <div><SparkIcon /><span>7 day streak</span></div>
        </div>
      </section>

      <section className="login-panel" aria-labelledby="login-heading">
        <div className="login-card">
          <p className="eyebrow">Welcome back</p>
          <h2 id="login-heading">Log in to UpJob</h2>
          <p>Continue with email or any OAuth provider connected to your WorkOS environment.</p>
          <div className="login-actions">
            <Link className="login-primary" href="/sign-in">Log in with AuthKit</Link>
            <Link className="login-secondary" href="/sign-up">Create an account</Link>
          </div>
          <p className="login-terms">Authentication is securely handled by WorkOS AuthKit.</p>
        </div>
      </section>
    </main>
  );
}
