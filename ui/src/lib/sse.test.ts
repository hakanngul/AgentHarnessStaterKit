import { enc, streamOf } from "./helpers.test-util";
import { parseSse, type SseEvent } from "./sse";

async function collect(chunks: (string | Uint8Array)[]): Promise<SseEvent[]> {
  const out: SseEvent[] = [];
  for await (const event of parseSse(streamOf(chunks))) out.push(event);
  return out;
}

describe("parseSse", () => {
  it("reads a frame with its id", async () => {
    expect(await collect(["id: 7\ndata: hello\n\n"])).toEqual([{ id: "7", data: "hello" }]);
  });

  it("reads several frames in one chunk, in order", async () => {
    const events = await collect(["id: 1\ndata: a\n\nid: 2\ndata: b\n\n"]);
    expect(events.map((e) => e.data)).toEqual(["a", "b"]);
  });

  it("joins a frame split anywhere across chunks", async () => {
    const text = "id: 5\ndata: split here\n\n";
    for (let cut = 1; cut < text.length; cut++) {
      expect(await collect([text.slice(0, cut), text.slice(cut)]), `cut at ${cut}`).toEqual([
        { id: "5", data: "split here" },
      ]);
    }
  });

  it("decodes a multi-byte character split between two chunks", async () => {
    const bytes = enc.encode("data: şğü€\n\n");
    for (let cut = 1; cut < bytes.length; cut++) {
      const events = await collect([bytes.slice(0, cut), bytes.slice(cut)]);
      expect(events, `cut at ${cut}`).toEqual([{ id: null, data: "şğü€" }]);
    }
  });

  it("accepts CRLF and a lone CR as line ends, also when CR and LF arrive in different chunks", async () => {
    expect(await collect(["data: a\r\n\r\n"])).toEqual([{ id: null, data: "a" }]);
    expect(await collect(["data: b\r\r"])).toEqual([{ id: null, data: "b" }]);
    expect(await collect(["data: c\r", "\n\r", "\n"])).toEqual([{ id: null, data: "c" }]);
  });

  it("ignores comment lines such as the keepalive", async () => {
    expect(await collect([": keepalive\n\n", "data: x\n\n", ": keepalive\n\n"])).toEqual([{ id: null, data: "x" }]);
  });

  it("joins several data lines with a newline and drops one leading space only", async () => {
    expect(await collect(["data: line1\ndata:  line2\ndata\n\n"])).toEqual([
      { id: null, data: "line1\n line2\n" },
    ]);
  });

  it("does not deliver a frame that was cut off before its blank line", async () => {
    expect(await collect(["id: 1\ndata: complete\n\n", "id: 2\ndata: cut off"])).toEqual([
      { id: "1", data: "complete" },
    ]);
  });

  it("does not produce an event for a frame with no data", async () => {
    expect(await collect(["id: 3\n\n"])).toEqual([]);
  });

  it("keeps the last id for the frames after it, and ignores an id with a NUL", async () => {
    expect(await collect(["id: 1\ndata: a\n\ndata: b\n\nid: 2\0x\ndata: c\n\n"])).toEqual([
      { id: "1", data: "a" },
      { id: "1", data: "b" },
      { id: "1", data: "c" },
    ]);
  });

  it("propagates a network error from the body", async () => {
    const failing = streamOf(["data: a\n\n"], new TypeError("network"));
    const seen: string[] = [];
    await expect(
      (async () => {
        for await (const e of parseSse(failing)) seen.push(e.data);
      })(),
    ).rejects.toThrow("network");
    expect(seen).toEqual(["a"]);
  });
});
