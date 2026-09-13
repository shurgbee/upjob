import { authkitProxy } from "@workos-inc/authkit-nextjs";

export default authkitProxy({
  middlewareAuth: {
    enabled: true,
    unauthenticatedPaths: ["/", "/login"],
  },
});

export const config = {
  matcher: [
    "/",
    "/login",
    "/home/:path*",
    "/shop/:path*",
    "/resume/:path*",
    "/api/resume/:path*",
    "/api/gmail/:path*",
    "/api/applications",
    "/api/applications/:path*",
  ],
};
