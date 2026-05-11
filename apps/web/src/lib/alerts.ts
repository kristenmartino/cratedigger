/**
 * Failure alerts via Resend's REST API.
 *
 * Mirror of services/agent/agent/alerts.py — same gating (RESEND_API_KEY +
 * OPS_ALERT_EMAIL), same from/subject conventions so failures from either
 * side thread together in the inbox.
 *
 * Uses fetch() directly rather than the Resend SDK because the web app
 * doesn't otherwise pull in the npm package, and a five-line POST is
 * cheaper than a new dep + lockfile churn.
 *
 * Never throws. The catch handler in callers must continue to surface the
 * original error — alerting is best-effort context, not failure recovery.
 */

const RESEND_ENDPOINT = "https://api.resend.com/emails";

interface ResendError {
  message?: string;
  statusCode?: number;
  name?: string;
}

export async function sendFailureAlert(
  subject: string,
  body: string,
): Promise<void> {
  const apiKey = process.env.RESEND_API_KEY;
  const opsEmail = process.env.OPS_ALERT_EMAIL;
  const fromAddress = process.env.RESEND_FROM_ADDRESS ?? "editor@kristenmartino.ai";

  if (!apiKey || !opsEmail) {
    console.info(
      "alert: skipped (RESEND_API_KEY=%s OPS_ALERT_EMAIL=%s)",
      Boolean(apiKey),
      Boolean(opsEmail),
    );
    return;
  }

  try {
    const res = await fetch(RESEND_ENDPOINT, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from: fromAddress,
        to: [opsEmail],
        subject,
        text: body,
      }),
    });

    if (!res.ok) {
      const errText = await res.text().catch(() => "<no body>");
      console.error("alert: Resend rejected (%d): %s", res.status, errText.slice(0, 500));
      return;
    }

    console.info("alert: sent %s to %s", JSON.stringify(subject), opsEmail);
  } catch (e) {
    const err = e as ResendError;
    console.error("alert: send failed (%s)", err.message ?? String(e));
  }
}
