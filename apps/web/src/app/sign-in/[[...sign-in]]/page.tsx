/**
 * Clerk-hosted sign-in. Catches /sign-in and any sub-paths Clerk uses for
 * its multi-step flows. The middleware already passes Clerk through, so
 * this just renders the form.
 */
import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
  return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <SignIn />
    </div>
  );
}
