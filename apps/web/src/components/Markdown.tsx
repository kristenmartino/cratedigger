/**
 * Inline markdown for editor's notes and prose.
 *
 * Haiku emits `*italic*` and `**bold**` markers in agent-generated prose.
 * The text is stored raw in `issues.editor_note` and `recommendations.prose`
 * so the same value can feed both the email template (rendered server-side
 * in Python) and the web (rendered here).
 *
 * Why a small custom component instead of a markdown library:
 *  - We only need emphasis (`*`, `**`). No links, lists, headings.
 *  - A full markdown parser is a bigger dep + bigger XSS surface area.
 *  - The email side does the same conversion with the same regex pair —
 *    keeping the two surfaces aligned avoids drift.
 *
 * React handles escaping for us: each text segment is rendered as text
 * (not dangerouslySetInnerHTML), so any < or & in the input stays inert.
 */
import { Fragment } from "react";

const BOLD = /\*\*(.+?)\*\*/;
const ITALIC = /(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/;

type Token =
  | { type: "text"; value: string }
  | { type: "strong"; value: string }
  | { type: "em"; value: string };

function tokenize(input: string): Token[] {
  // Two-pass: bold first (longer marker), then italic on the leftover text.
  // We splice token objects in place so a bold span isn't re-scanned for
  // italics (which would turn **a *b* c** into nonsense).
  let working: Token[] = [{ type: "text", value: input }];

  const passes: Array<{ re: RegExp; type: "strong" | "em" }> = [
    { re: BOLD, type: "strong" },
    { re: ITALIC, type: "em" },
  ];

  for (const { re, type } of passes) {
    const next: Token[] = [];
    for (const tok of working) {
      if (tok.type !== "text") {
        next.push(tok);
        continue;
      }
      let remaining = tok.value;
      while (remaining.length > 0) {
        const m = remaining.match(re);
        if (!m || m.index === undefined) {
          next.push({ type: "text", value: remaining });
          break;
        }
        if (m.index > 0) next.push({ type: "text", value: remaining.slice(0, m.index) });
        next.push({ type, value: m[1] });
        remaining = remaining.slice(m.index + m[0].length);
      }
    }
    working = next;
  }

  return working;
}

export function Markdown({ children }: { children: string | null | undefined }) {
  if (!children) return null;
  const tokens = tokenize(children);
  return (
    <>
      {tokens.map((tok, i) => {
        if (tok.type === "strong") return <strong key={i}>{tok.value}</strong>;
        if (tok.type === "em") return <em key={i}>{tok.value}</em>;
        return <Fragment key={i}>{tok.value}</Fragment>;
      })}
    </>
  );
}
