/**
 * `/archive` — the archive / tool surface.
 *
 * Pixel-level reference: cratedigger-handoff/mockups/cratedigger-archive.html
 *
 * Window-chromed, sidebar + records grid + status bar. Stub for now; full
 * layout lands Sprint Week 5.
 */
export default function ArchivePage() {
  return (
    <div className="max-w-5xl mx-auto py-24 px-6">
      <h1 className="font-display italic text-[64px] leading-[0.92] text-ink">
        Archive
      </h1>
      <p className="mt-4 font-mono text-[10px] uppercase tracking-[0.22em] text-ink-soft">
        Window chrome · sidebar filters · issue dividers · records grid
      </p>
      <p className="mt-12 font-body italic text-ink-soft">
        The archive surface is queued for Sprint Week 5. Until then, the
        editorial digest lives at <code className="font-mono">/issue/[n]</code>.
      </p>
    </div>
  );
}
