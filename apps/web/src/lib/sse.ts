/**
 * Server-Sent Events parser. Harvested verbatim from sift/lib/sse.ts.
 * Used by the "now digging" widget to subscribe to live agent status.
 */
export async function* readSSE<T = unknown>(
  response: Response,
): AsyncGenerator<{ event: string; data: T }> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";

      for (const part of parts) {
        if (!part.trim()) continue;
        let event = "message";
        let dataStr = "";
        for (const line of part.split("\n")) {
          if (line.startsWith("event: ")) {
            event = line.slice(7);
          } else if (line.startsWith("data: ")) {
            dataStr += (dataStr ? "\n" : "") + line.slice(6);
          }
        }
        if (dataStr) {
          try {
            yield { event, data: JSON.parse(dataStr) as T };
          } catch {
            // Skip malformed JSON
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
