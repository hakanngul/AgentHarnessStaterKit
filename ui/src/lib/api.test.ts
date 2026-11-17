import { ApiError, createApi } from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("createApi", () => {
  it("sends the bearer token, no cookies and no caching on a GET", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse([1]));
    const api = createApi("secret-token", fetchImpl);
    expect(await api.get("/api/tasks")).toEqual([1]);
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/api/tasks");
    expect(init.headers).toEqual({ Authorization: "Bearer secret-token" });
    expect(init.credentials).toBe("omit");
    expect(init.cache).toBe("no-store");
  });

  it("posts JSON with a content type, and posts without a body or content type when empty", async () => {
    const fetchImpl = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse({ ok: true })));
    const api = createApi("t".repeat(20), fetchImpl);
    await api.post("/api/jobs", { kind: "wave" });
    const withBody = fetchImpl.mock.calls[0]![1];
    expect(withBody.method).toBe("POST");
    expect(withBody.headers["Content-Type"]).toBe("application/json");
    expect(withBody.body).toBe('{"kind":"wave"}');
    await api.post("/api/jobs/x/cancel");
    const bare = fetchImpl.mock.calls[1]![1];
    expect(bare.body).toBeUndefined();
    expect(bare.headers["Content-Type"]).toBeUndefined();
  });

  it("turns an error status into an ApiError carrying the server's detail", async () => {
    const api = createApi("t", vi.fn().mockResolvedValue(jsonResponse({ detail: "missing or wrong token" }, 401)));
    const error = await api.get("/api/plan").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect((error as ApiError).detail).toBe("missing or wrong token");
  });

  it("stringifies a structured detail and survives a non-JSON error body", async () => {
    const structured = createApi("t", vi.fn().mockResolvedValue(jsonResponse({ detail: [{ field: "kind" }] }, 422)));
    expect(((await structured.post("/api/jobs", {}).catch((e: unknown) => e)) as ApiError).detail).toContain("kind");
    const html = createApi("t", vi.fn().mockResolvedValue(new Response("<html>", { status: 502, statusText: "Bad Gateway" })));
    expect(((await html.get("/api/x").catch((e: unknown) => e)) as ApiError).detail).toBe("Bad Gateway");
  });

  it.each(["/other", "api/tasks", "//evil.example/api/x", "https://evil.example/api/x", "/api/../x://y", "/api/%2e%2e/x", "/api/a/../b", "/api\\evil", "/api//x", "/api/x?u=http://e"])(
    "refuses a path that is not a same-origin API path: %s",
    async (path) => {
      const fetchImpl = vi.fn();
      const api = createApi("t", fetchImpl);
      await expect(api.get(path)).rejects.toThrow("not an API path");
      await expect(api.post(path)).rejects.toThrow("not an API path");
      await expect(api.open(path)).rejects.toThrow("not an API path");
      expect(fetchImpl).not.toHaveBeenCalled();
    },
  );

  it("opens a stream with the token and an event-stream accept header", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
    const controller = new AbortController();
    await createApi("tok", fetchImpl).open("/api/jobs/x/events?after=3", controller.signal);
    const init = fetchImpl.mock.calls[0]![1];
    expect(init.headers).toEqual({ Authorization: "Bearer tok", Accept: "text/event-stream" });
    expect(init.signal).toBe(controller.signal);
  });
});
