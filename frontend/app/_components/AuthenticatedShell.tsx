import { withAuth } from "@workos-inc/authkit-nextjs";
import { AuthKitProvider } from "@workos-inc/authkit-nextjs/components";

export async function AuthenticatedShell({ children }: { children: React.ReactNode }) {
  const auth = await withAuth({ ensureSignedIn: true });
  const { accessToken, ...initialAuth } = auth;
  void accessToken;

  return <AuthKitProvider initialAuth={initialAuth}>{children}</AuthKitProvider>;
}
