/**
 * Server-side auth status. Pattern adapted from sift/components/AuthButtons.tsx.
 * Uses Clerk's `auth()` helper rather than the `<SignedIn>` / `<SignedOut>`
 * client components — keeps the home page server-rendered.
 *
 * Fail-soft: if Clerk isn't configured, just renders nothing instead of
 * blowing up the page.
 */
import Link from "next/link";
import { auth } from "@clerk/nextjs/server";
import { SignOutForm } from "./SignOutForm";

const clerkPk = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const clerkEnabled = !!clerkPk && clerkPk.startsWith("pk_");

export async function AuthButtons() {
  if (!clerkEnabled) return null;

  const { userId } = await auth();

  if (userId) {
    return <SignOutForm />;
  }

  return (
    <Link
      href="/sign-in"
      className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink border-b-2 border-coral pb-[2px] hover:text-coral transition"
    >
      Sign in
    </Link>
  );
}
