import { NextResponse } from "next/server";

import { getCurrentUserId } from "@/lib/jobs";

// Server-side proxy to the backend Gmail sync endpoint. Runs on the server so it
// avoids CORS and keeps the backend URL out of the client bundle. The signed-in
// user's id is forwarded so the sync reconciles against THEIR open applications
// (the backend's "oldest app_users row" default is wrong once >1 user exists).
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export async function POST(request: Request) {
  let hours = 24;
  try {
    const body = await request.json();
    if (typeof body?.hours === "number" && body.hours > 0) {
      hours = body.hours;
    }
  } catch {
    // No/invalid body: fall back to the default 24-hour window.
  }

  const userId = await getCurrentUserId();

  try {
    const res = await fetch(`${BACKEND_URL}/api/gmail/sync`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hours, user_id: userId ?? undefined }),
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json(
      { error: "Could not reach the Gmail sync service." },
      { status: 502 },
    );
  }
}
