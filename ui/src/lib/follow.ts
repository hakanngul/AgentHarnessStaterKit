import { ApiError, type Api } from "./api";
import { parseSse } from "./sse";
import type { JobEvent, JobView } from "./types";

export interface FollowOptions {
  signal?: AbortSignal;
  /** wait between reconnects; replaced in tests */
  pause?: (ms: number) => Promise<void>;
  retryMs?: number;
}

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function parseEvent(data: string): JobEvent | null {
  try {
    const value: unknown = JSON.parse(data);
    if (typeof value !== "object" || value === null) return null;
    const event = value as Partial<JobEvent>;
    if (!Number.isInteger(event.id) || typeof event.text !== "string") return null;
    return event as JobEvent;
  } catch {
    return null; // a frame the server cannot have meant; asking again would return it again
  }
}

/** Yields the events of a job in order, and again from the last id when the stream drops while
 * the job still runs. The server keeps only the last 2000 events per job, so after a long gap
 * some events may be missing; that is not reported by the server either. Ends when the job is
 * no longer running and its stream has been read to the end. */
export async function* followJob(
  api: Api,
  jobId: string,
  { signal, pause = wait, retryMs = 1000 }: FollowOptions = {},
): AsyncGenerator<JobEvent> {
  let after = 0;
  while (!signal?.aborted) {
    const before = after;
    let streamRead = false;
    try {
      const response = await api.open(`/api/jobs/${encodeURIComponent(jobId)}/events?after=${after}`, signal);
      if (response.body) {
        for await (const frame of parseSse(response.body)) {
          const event = parseEvent(frame.data);
          if (event === null) continue; // skipped, not retried
          after = Math.max(after, event.id);
          yield event;
        }
      }
      streamRead = true;
    } catch (error) {
      if (signal?.aborted) return;
      if (!(error instanceof TypeError)) throw error; // only a network drop is retried
    }
    if (signal?.aborted) return;
    let job: JobView;
    try {
      job = await api.get<JobView>(`/api/jobs/${encodeURIComponent(jobId)}`);
    } catch (error) {
      if (signal?.aborted) return;
      if (error instanceof ApiError && error.status === 404) return; // pruned after it finished
      if (!(error instanceof TypeError)) throw error;
      await pause(retryMs); // the network blinked between the stream and the status read
      continue;
    }
    if (job.status !== "running") {
      // everything was delivered, or the server has nothing more to give (a gap in its buffer)
      // the no-progress guard applies only when the stream was really read to its end
      if (after >= job.last_event || (streamRead && after === before)) return;
    }
    await pause(retryMs);
  }
}
