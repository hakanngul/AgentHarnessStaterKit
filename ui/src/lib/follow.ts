import type { Api } from "./api";
import { parseSse } from "./sse";
import type { JobEvent, JobView } from "./types";

export interface FollowOptions {
  signal?: AbortSignal;
  /** wait between reconnects; replaced in tests */
  pause?: (ms: number) => Promise<void>;
  retryMs?: number;
}

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

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
    try {
      const response = await api.open(`/api/jobs/${jobId}/events?after=${after}`, signal);
      if (response.body) {
        for await (const frame of parseSse(response.body)) {
          const event = JSON.parse(frame.data) as JobEvent;
          after = event.id;
          yield event;
        }
      }
    } catch (error) {
      if (signal?.aborted) return;
      if (!(error instanceof TypeError || error instanceof SyntaxError)) throw error; // not a drop
    }
    if (signal?.aborted) return;
    const job = await api.get<JobView>(`/api/jobs/${jobId}`);
    if (job.status !== "running") {
      // everything was delivered, or the server has nothing more to give (a gap in its buffer)
      if (after >= job.last_event || after === before) return;
    } else {
      await pause(retryMs);
    }
  }
}
