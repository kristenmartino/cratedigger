/**
 * `/issue/[number]` — the editorial digest surface.
 *
 * Pixel-level reference: docs/mockups/cratedigger-newsletter.html
 * Tokens: docs/DESIGN_SYSTEM.md
 *
 * Current scope: functional but not pixel-perfect. Renders the issue title,
 * editor's note (with markdown emphasis), and each non-withheld record
 * with cover art, artist, release title, prose, source attribution, and a
 * Listen button. Full design-system pass (system-strip, masthead, signal
 * blocks, matched-signals microsections, pull-quote, sticky track-nav,
 * now-digging widget, withheld seal) is a separate sprint.
 *
 * Server Component (RSC). Reads from the DB directly via @cratedigger/db.
 */
import { db, schema } from "@cratedigger/db";
import { and, eq, ne } from "drizzle-orm";
import { Markdown } from "@/components/Markdown";

type PageProps = {
  params: Promise<{ number: string }>;
};

export default async function IssuePage({ params }: PageProps) {
  const { number } = await params;
  const issueNumber = parseInt(number, 10);

  if (Number.isNaN(issueNumber) || issueNumber <= 0) {
    return (
      <div className="max-w-2xl mx-auto py-32 px-6 text-center">
        <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-coral">
          — Issue not found —
        </p>
      </div>
    );
  }

  const issue = await db.query.issues
    .findFirst({
      where: eq(schema.issues.issueNumber, issueNumber),
    })
    .catch(() => null);

  if (!issue) {
    return (
      <div className="max-w-2xl mx-auto py-32 px-6 text-center">
        <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-coral">
          — Issue {issueNumber} hasn&rsquo;t shipped yet —
        </p>
        <p className="mt-6 font-body italic text-ink-soft">
          The agent is still digging. Check back Sunday.
        </p>
      </div>
    );
  }

  // Fetch recommendations + their releases. Withheld picks ship in Friday's
  // email; the Sunday surface mirrors that — they don't appear here.
  const recs = await db
    .select({
      position: schema.recommendations.position,
      category: schema.recommendations.category,
      sourceAttr: schema.recommendations.sourceAttr,
      prose: schema.recommendations.prose,
      recCoverArtUrl: schema.recommendations.coverArtUrl,
      artist: schema.releases.artist,
      releaseTitle: schema.releases.title,
      relCoverArtUrl: schema.releases.coverArtUrl,
      bandcampUrl: schema.releases.bandcampUrl,
      spotifyUrl: schema.releases.spotifyUrl,
      appleMusicUrl: schema.releases.appleMusicUrl,
      youtubeUrl: schema.releases.youtubeUrl,
      soundcloudUrl: schema.releases.soundcloudUrl,
      releaseUrl: schema.releases.url,
    })
    .from(schema.recommendations)
    .innerJoin(
      schema.releases,
      eq(schema.recommendations.releaseId, schema.releases.id),
    )
    .where(
      and(
        eq(schema.recommendations.issueId, issue.id),
        ne(schema.recommendations.category, "withheld"),
      ),
    )
    .orderBy(schema.recommendations.position);

  return (
    <article className="max-w-3xl mx-auto py-24 px-6">
      <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-coral text-center">
        — Issue {issue.issueNumber} · {String(issue.publishDate)} —
      </p>
      <h1 className="font-display italic text-issue-display text-center mt-6">
        {String(issue.issueNumber).padStart(2, "0")}
      </h1>
      <p className="font-display italic text-[21px] text-ink-soft text-center mt-6">
        {issue.title}
      </p>

      <p className="mt-12 max-w-prose mx-auto font-body text-editor-note text-ink-soft">
        <Markdown>{issue.editorNote}</Markdown>
      </p>
      <p className="mt-4 max-w-prose mx-auto text-right font-display text-coral text-[26px] italic">
        — C.
      </p>

      {recs.length > 0 && (
        <div className="mt-20 space-y-16">
          {recs.map((rec) => {
            const cover = rec.recCoverArtUrl || rec.relCoverArtUrl;
            // Direct-audio only; no source-URL fallback. Some source pages
            // (Aquarium Drunkard articles, indie shop product pages) gate
            // behind login walls — better to omit the Listen button than
            // promise audio and deliver a paywall.
            // Editorial preference order: Bandcamp pays artists, Spotify
            // is the popular default, Apple Music is the runner-up paid
            // streamer, YouTube is broadly accessible, SoundCloud catches
            // niche/demo work.
            const listen =
              rec.bandcampUrl ||
              rec.spotifyUrl ||
              rec.appleMusicUrl ||
              rec.youtubeUrl ||
              rec.soundcloudUrl;
            return (
              <section
                key={`${rec.position}-${rec.artist}-${rec.releaseTitle}`}
                className="border-t border-ink/15 pt-12 max-w-prose mx-auto"
              >
                {cover && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={cover}
                    alt={`${rec.artist} — ${rec.releaseTitle}`}
                    className="block w-full max-w-[360px] mb-6 rounded-[2px]"
                  />
                )}
                <p className="font-mono text-[9.5px] uppercase tracking-[0.18em] text-ink-soft">
                  {rec.category} · via {rec.sourceAttr}
                </p>
                <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.22em] text-ink-soft">
                  {rec.artist}
                </p>
                <h2 className="mt-1 font-display italic text-[40px] leading-tight text-ink">
                  {rec.releaseTitle}
                </h2>
                <p className="mt-4 font-body text-editor-note text-ink-soft">
                  <Markdown>{rec.prose}</Markdown>
                </p>
                {listen && (
                  <a
                    href={listen}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-5 inline-block bg-ink text-paper font-mono text-[11px] uppercase tracking-[0.22em] px-[18px] py-[10px] hover:opacity-80 transition"
                  >
                    Listen ↗
                  </a>
                )}
              </section>
            );
          })}
        </div>
      )}

      <p className="mt-24 text-center font-mono text-[9px] tracking-[0.22em] uppercase text-ink-faint">
        Full editorial layout (system-strip, signal blocks, sticky nav)
        lands in a later sprint.
      </p>
    </article>
  );
}
