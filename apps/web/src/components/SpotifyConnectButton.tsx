/**
 * Spotify connect button.
 *
 * Renders a green "Connect Spotify" button that links to /api/spotify/connect
 * (which starts the OAuth flow). When `connected` is true, shows a "Spotify
 * connected" indicator instead — minimal, no fanfare.
 *
 * Visual: brand-correct Spotify green but using our brand type. The button
 * is the action; the connected state is the receipt.
 */
export function SpotifyConnectButton({ connected }: { connected: boolean }) {
  if (connected) {
    return (
      <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-ink-soft">
        ♪ Spotify connected — playlist updates each Sunday
      </p>
    );
  }
  return (
    <a
      href="/api/spotify/connect"
      className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full bg-[#1DB954] text-white font-mono text-[12px] uppercase tracking-[0.18em] hover:opacity-90 transition-opacity"
    >
      Connect Spotify
    </a>
  );
}
