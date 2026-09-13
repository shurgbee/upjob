import { NextResponse } from "next/server";

// Server-side proxy to the backend Gmail sync endpoint. Runs on the server so it
// avoids CORS and keeps the backend URL out of the client bundle. No user_id is
// forwarded — the backend resolves the owning app_users row itself.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export async function POST(request: Request) {
  let hours = 2;
  try {
    const body = await request.json();
    if (typeof body?.hours === "number" && body.hours > 0) {
      hours = body.hours;
    }
  } catch {
    // No/invalid body: fall back to the default 2-hour window.
  }

  try {
    const res = await fetch(`${BACKEND_URL}/api/gmail/sync`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hours }),
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
