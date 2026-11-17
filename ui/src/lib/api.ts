export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${status}: ${detail}`);
  }
}

export type Fetch = typeof fetch;

export interface Api {
  get<T>(path: string): Promise<T>;
  post<T>(path: string, body?: unknown): Promise<T>;
  /** For streams: the raw response, already checked for an error status. */
  open(path: string, signal?: AbortSignal): Promise<Response>;
}

// A fixed allowlist, not a blacklist: letters, digits and a few separators after `/api/`. No `%`,
// `\`, `:` or `//`, so nothing can encode a host, a scheme or a way out of /api/.
const API_PATH = /^\/api\/([A-Za-z0-9_\-/?=&]*)$/;

/** The request target, rebuilt from the part the allowlist matched (never the caller's string). */
function apiTarget(path: string): string {
  // only same-origin API paths: a token must never be sent anywhere else
  const match = API_PATH.exec(path);
  if (match === null || path.includes("//") || path.split(/[/?]/).includes("..")) {
    throw new Error(`not an API path: ${path}`);
  }
  return `/api/${match[1] ?? ""}`;
}

async function detailOf(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail !== undefined) return JSON.stringify(detail);
  } catch {
    // not JSON: fall through to the status text
  }
  return response.statusText || "request failed";
}

export function createApi(token: string, fetchImpl: Fetch = (...a) => fetch(...a)): Api {
  const auth = { Authorization: `Bearer ${token}` };

  async function request(path: string, init: RequestInit): Promise<Response> {
    const response = await fetchImpl(apiTarget(path), { ...init, credentials: "omit", cache: "no-store" });
    if (!response.ok) throw new ApiError(response.status, await detailOf(response));
    return response;
  }

  return {
    async get<T>(path: string) {
      return (await (await request(path, { headers: auth })).json()) as T;
    },
    async post<T>(path: string, body?: unknown) {
      const init: RequestInit = { method: "POST", headers: { ...auth } };
      if (body !== undefined) {
        init.headers = { ...auth, "Content-Type": "application/json" };
        init.body = JSON.stringify(body);
      }
      return (await (await request(path, init)).json()) as T;
    },
    open(path: string, signal?: AbortSignal) {
      return request(path, { headers: { ...auth, Accept: "text/event-stream" }, signal });
    },
  };
}
