import { NextResponse } from "next/server";

import { createApplication, getCurrentUserId } from "@/lib/jobs";

// Records an application (open row) for the signed-in user against a job spec.
// The row is later reconciled to its Gmail confirmation email by the sync agent.
export async function POST(request: Request) {
  const userId = await getCurrentUserId();
  if (!userId) {
    return NextResponse.json(
      { error: "You must be signed in with a linked account to track applications." },
      { status: 401 },
    );
  }

  let specId: unknown;
  try {
    specId = (await request.json())?.specId;
  } catch {
    specId = undefined;
  }
  if (typeof specId !== "string" || !specId) {
    return NextResponse.json({ error: "Missing specId." }, { status: 400 });
  }

  try {
    await createApplication(userId, specId);
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ error: "Could not record application." }, { status: 500 });
  }
}
