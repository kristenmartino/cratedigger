/**
 * Sign-out via POST form. Pattern adapted from sift/components/SignOutForm.tsx.
 * Posts to /api/sign-out which clears the Clerk session and redirects home.
 */
"use client";

export function SignOutForm() {
  return (
    <form action="/api/sign-out" method="POST">
      <button
        type="submit"
        className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink border-b-2 border-coral pb-[2px] hover:text-coral transition"
      >
        Sign out
      </button>
    </form>
  );
}
