import { withAuth } from "@workos-inc/authkit-nextjs";
import { redirect } from "next/navigation";

export default async function IndexPage() {
  const { user } = await withAuth();
  redirect(user ? "/home" : "/login");
}
