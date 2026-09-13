import { NextResponse } from "next/server";

import {
  createApplication,
  getCurrentUserId,
  isApplicationStatus,
  updateApplicationStatus,
} from "@/lib/jobs";

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

// Updates the status of one of the signed-in user's applications.
export async function PATCH(request: Request) {
  const userId = await getCurrentUserId();
  if (!userId) {
    return NextResponse.json(
      { error: "You must be signed in with a linked account to update applications." },
      { status: 401 },
    );
  }

  let body: { id?: unknown; status?: unknown };
  try {
    body = await request.json();
  } catch {
    body = {};
  }
  const { id, status } = body;
  if (typeof id !== "string" || !id) {
    return NextResponse.json({ error: "Missing application id." }, { status: 400 });
  }
  if (!isApplicationStatus(status)) {
    return NextResponse.json({ error: "Invalid status." }, { status: 400 });
  }

  try {
    const updated = await updateApplicationStatus(userId, id, status);
    if (!updated) {
      return NextResponse.json({ error: "Application not found." }, { status: 404 });
    }
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ error: "Could not update status." }, { status: 500 });
  }
}
