/**
 * Clerk-hosted sign-up. Symmetric with /sign-in.
 */
import { SignUp } from "@clerk/nextjs";

export default function SignUpPage() {
  return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <SignUp />
    </div>
  );
}
