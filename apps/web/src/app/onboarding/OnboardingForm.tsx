"use client";

/**
 * Onboarding form — client component.
 *
 * Two inputs:
 *   - Artists: textarea, one per line (10-50 required)
 *   - Tags: multi-select chips from a fixed list (3-12 required)
 *
 * On submit, POST to /api/onboarding which persists the seed and kicks
 * off the agent's centroid build. On success, route to /onboarding/done.
 *
 * Validation is duplicated in the API route — client-side is friction
 * reduction (highlight problems before submit), server-side is the real
 * gate. Both use the same min/max constants.
 */
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

const MIN_ARTISTS = 10;
const MAX_ARTISTS = 50;
const MIN_TAGS = 3;
const MAX_TAGS = 12;

// Curated chip list. Broad enough that most listeners find ≥3 honest fits;
// narrow enough that the matching layer has a tractable label set. The
// vocabulary matches the keys our scoring layer recognizes (tags coming
// in from sources or LLM-extracted release blurbs are normalized to this
// shortlist).
const TAG_OPTIONS = [
  "electronic",
  "techno",
  "house",
  "ambient",
  "dub",
  "experimental",
  "indie",
  "rock",
  "psych",
  "folk",
  "soul",
  "r&b",
  "hip-hop",
  "jazz",
  "country",
  "americana",
  "bluegrass",
  "metal",
  "punk",
  "shoegaze",
  "drum & bass",
  "downtempo",
  "world",
  "classical",
];

export function OnboardingForm() {
  const router = useRouter();
  const [artistsText, setArtistsText] = useState("");
  const [tags, setTags] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const artists = artistsText
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);

  const toggleTag = (tag: string) => {
    setTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (artists.length < MIN_ARTISTS) {
      setError(`At least ${MIN_ARTISTS} artists, please.`);
      return;
    }
    if (artists.length > MAX_ARTISTS) {
      setError(`Whoa — keep it under ${MAX_ARTISTS}.`);
      return;
    }
    if (tags.size < MIN_TAGS) {
      setError(`Pick at least ${MIN_TAGS} tags.`);
      return;
    }
    if (tags.size > MAX_TAGS) {
      setError(`At most ${MAX_TAGS} tags.`);
      return;
    }

    startTransition(async () => {
      try {
        const res = await fetch("/api/onboarding", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ artists, tags: [...tags] }),
        });
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as { error?: string };
          setError(body.error || `Server returned ${res.status}`);
          return;
        }
        router.push("/onboarding/done");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Network error");
      }
    });
  };

  return (
    <form onSubmit={onSubmit} className="space-y-10">
      {/* Artists */}
      <div>
        <label
          htmlFor="artists"
          className="block font-display italic text-[20px] text-ink"
        >
          Artists you actually listen to
        </label>
        <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.18em] text-ink-soft">
          One per line · {artists.length}/{MAX_ARTISTS}
        </p>
        <textarea
          id="artists"
          name="artists"
          rows={12}
          value={artistsText}
          onChange={(e) => setArtistsText(e.target.value)}
          className="mt-3 w-full rounded border border-ink/20 bg-paper px-3 py-2 font-body text-[15px] leading-relaxed text-ink focus:border-coral focus:outline-none"
          placeholder={"Burial\nFour Tet\nBuilt to Spill\n..."}
        />
      </div>

      {/* Tags */}
      <div>
        <span className="block font-display italic text-[20px] text-ink">
          Genres that feel honest
        </span>
        <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.18em] text-ink-soft">
          Pick {MIN_TAGS}–{MAX_TAGS} · {tags.size} selected
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {TAG_OPTIONS.map((tag) => {
            const on = tags.has(tag);
            return (
              <button
                type="button"
                key={tag}
                onClick={() => toggleTag(tag)}
                className={
                  "px-3 py-1.5 rounded-full text-[13px] font-body border transition-colors " +
                  (on
                    ? "bg-coral text-paper border-coral"
                    : "bg-paper text-ink border-ink/25 hover:border-ink/50")
                }
              >
                {tag}
              </button>
            );
          })}
        </div>
      </div>

      {error && (
        <p className="font-body text-[14px] text-coral italic">{error}</p>
      )}

      <div className="flex items-center gap-4">
        <button
          type="submit"
          disabled={pending}
          className="px-6 py-2.5 rounded-full bg-ink text-paper font-mono text-[12px] uppercase tracking-[0.18em] disabled:opacity-50"
        >
          {pending ? "Saving…" : "Set my taste"}
        </button>
        <span className="font-body italic text-[13px] text-ink-soft">
          You can edit any of this later.
        </span>
      </div>
    </form>
  );
}
