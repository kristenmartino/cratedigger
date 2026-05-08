/**
 * The "Crate Digger." wordmark. Per DESIGN_SYSTEM.md typography:
 *   - Instrument Serif italic
 *   - Decorative C (1.18em, slight Y-translate)
 *   - D in Digger gets a quieter sister-treatment to the C
 *   - Coral-colored period as the only accent
 */
export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <h1
      className={`font-display italic font-normal text-wordmark inline-block whitespace-nowrap text-ink ${className}`}
    >
      <span className="inline-block text-[1.18em] leading-[0.74] -mr-[0.04em] translate-y-[2px]">
        C
      </span>
      rate{" "}
      <span className="inline-block ml-[0.04em]">D</span>
      igger
      <span className="text-coral not-italic text-[0.55em] relative -top-[0.05em] ml-[0.05em]">
        .
      </span>
    </h1>
  );
}
