import type { Api } from "./api";

export const enc = new TextEncoder();

export function streamOf(chunks: (string | Uint8Array)[], failWith?: Error): ReadableStream<Uint8Array> {
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) {
        const chunk = chunks[i++]!;
        controller.enqueue(typeof chunk === "string" ? enc.encode(chunk) : chunk);
      } else if (failWith) {
        controller.error(failWith);
      } else {
        controller.close();
      }
    },
  });
}

export function frame(id: number, text: string, task: string | null = null): string {
  return `id: ${id}\ndata: ${JSON.stringify({ id, task, text })}\n\n`;
}

export function responseOf(body: ReadableStream<Uint8Array>): Response {
  return new Response(body, { status: 200 });
}

export function fakeApi(overrides: Partial<Api> = {}): Api {
  return {
    get: () => Promise.reject(new Error("unexpected get")),
    post: () => Promise.reject(new Error("unexpected post")),
    open: () => Promise.reject(new Error("unexpected open")),
    ...overrides,
  };
}
