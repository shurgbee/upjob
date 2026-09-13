import { withAuth } from "@workos-inc/authkit-nextjs";
import { NextRequest } from "next/server";

export const runtime = "nodejs";

function allowedPath(path: string) {
  return (
    path === "" ||
    path === "plan" ||
    path === "retry" ||
    /^quests\/[0-9a-f-]{36}\/evaluate$/i.test(path)
  );
}

async function proxy(
  request: NextRequest,
  context: RouteContext<"/api/skills/[[...path]]">,
) {
  const { user } = await withAuth();
  if (!user) {
    return Response.json(
      { detail: "Sign in to access your skill plan." },
      { status: 401 },
    );
  }
  const path = (await context.params).path?.join("/") ?? "";
  if (!allowedPath(path)) return new Response(null, { status: 404 });
  if (
    request.method !== "GET" &&
    request.headers.get("origin") !== request.nextUrl.origin
  ) {
    return Response.json({ detail: "Invalid request origin." }, { status: 403 });
  }
  const base = process.env.RESUME_BACKEND_URL;
  const token = process.env.RESUME_SERVICE_TOKEN;
  if (!base || !token) {
    return Response.json(
      { detail: "The skill service is not configured yet." },
      { status: 503 },
    );
  }
  try {
    let body: string | undefined;
    if (request.method !== "GET") {
      const bytes = await request.arrayBuffer();
      if (bytes.byteLength > 64 * 1024) {
        return Response.json({ detail: "Request is too large." }, { status: 413 });
      }
      body = Buffer.from(bytes).toString("utf8");
    }
    const response = await fetch(
      `${base.replace(/\/$/, "")}/skills-workspace${path ? `/${path}` : ""}`,
      {
        method: request.method,
        body,
        cache: "no-store",
        signal: AbortSignal.timeout(20_000),
        headers: {
          "Content-Type": "application/json",
          "X-Resume-Service-Token": token,
          "X-WorkOS-User": user.id,
        },
      },
    );
    return new Response(response.body, {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("content-type") ?? "application/json",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return Response.json(
      { detail: "The skill service is unavailable. Try again in a moment." },
      { status: 503 },
    );
  }
}

export { proxy as GET, proxy as POST };
