export interface SseEvent {
  id: string | null;
  data: string;
}

/** Server-sent events read from a fetch body (EventSource cannot send an Authorization header).
 * Handles frames split over chunks, a UTF-8 character split over chunks, CRLF, comment lines
 * (keepalive) and multi-line data. It ends when the body ends. */
export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let id: string | null = null;
  let data: string[] = [];

  function* lines(text: string): Generator<string> {
    buffer += text;
    for (;;) {
      const match = /\r\n|\n|\r/.exec(buffer);
      // a lone "\r" at the very end may be the first half of "\r\n": wait for more
      if (!match || (match[0] === "\r" && match.index === buffer.length - 1)) return;
      yield buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
    }
  }

  function* handle(line: string): Generator<SseEvent> {
    if (line === "") {
      if (data.length > 0) yield { id, data: data.join("\n") };
      data = [];
      return;
    }
    if (line.startsWith(":")) return; // comment, e.g. the keepalive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") data.push(value);
    else if (field === "id" && !value.includes("\0")) id = value;
  }

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const line of lines(decoder.decode(value, { stream: true }))) yield* handle(line);
    }
    for (const line of lines(decoder.decode())) yield* handle(line);
    if (buffer.endsWith("\r")) {
      // the held-back "\r" was a line end after all: nothing more will come
      const last = buffer.slice(0, -1);
      buffer = "";
      yield* handle(last);
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}
