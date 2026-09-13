import { withAuth } from "@workos-inc/authkit-nextjs";
import { NextRequest } from "next/server";

export const runtime = "nodejs";
const routes = new Set(["", "pdf", "operations", "retry"]);

async function proxy(
  request: NextRequest,
  context: RouteContext<"/api/resume/[[...path]]">,
) {
  const { user } = await withAuth();
  if (!user)
    return Response.json(
      { detail: "Sign in to access your resume." },
      { status: 401 },
    );
  const path = (await context.params).path?.join("/") ?? "";
  if (!routes.has(path)) return new Response(null, { status: 404 });
  if (
    request.method !== "GET" &&
    request.headers.get("origin") !== request.nextUrl.origin
  ) {
    return Response.json(
      { detail: "Invalid request origin." },
      { status: 403 },
    );
  }
  const base = process.env.RESUME_BACKEND_URL;
  const token = process.env.RESUME_SERVICE_TOKEN;
  if (!base || !token)
    return Response.json(
      {
        detail:
          "Resume service is not configured. Set RESUME_BACKEND_URL and RESUME_SERVICE_TOKEN on the server.",
      },
      { status: 503 },
    );
  try {
    let body: string | undefined;
    if (request.method !== "GET") {
      // Bound bytes while reading, including requests without Content-Length.
      const reader = request.body?.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      if (reader)
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 2 * 1024 * 1024) {
            await reader.cancel();
            return Response.json(
              { detail: "Upload is too large." },
              { status: 413 },
            );
          }
          chunks.push(value);
        }
      body = Buffer.concat(chunks).toString("utf8");
    }
    const response = await fetch(
      `${base.replace(/\/$/, "")}/resume-workspace${path ? `/${path}` : ""}${request.nextUrl.search}`,
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
        "Content-Type":
          response.headers.get("content-type") ?? "application/json",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        ...(path === "pdf"
          ? { "Content-Disposition": "inline; filename=resume.pdf" }
          : {}),
      },
    });
  } catch {
    return Response.json(
      {
        detail:
          "Resume service is unavailable. Your local edits are still here; retry when the service is back.",
      },
      { status: 503 },
    );
  }
}

export { proxy as GET, proxy as PUT, proxy as POST };
