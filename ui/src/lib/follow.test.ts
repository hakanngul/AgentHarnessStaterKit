import { ApiError } from "./api";
import { followJob } from "./follow";
import { fakeApi, frame, responseOf, streamOf } from "./helpers.test-util";
import type { JobEvent, JobView } from "./types";

const job = (status: JobView["status"], last_event: number): JobView => ({
  id: "j",
  kind: "wave",
  status,
  params: {},
  created: "",
  finished: null,
  error: null,
  result: null,
  last_event,
});

async function collect(gen: AsyncGenerator<JobEvent>): Promise<JobEvent[]> {
  const out: JobEvent[] = [];
  for await (const e of gen) out.push(e);
  return out;
}

describe("followJob", () => {
  it("yields every event of a stream and stops when the job is done", async () => {
    const open = vi.fn().mockResolvedValue(responseOf(streamOf([frame(1, "wave started"), frame(2, "wave done")])));
    const api = fakeApi({ open, get: () => Promise.resolve(job("done", 2)) as never });
    const events = await collect(followJob(api, "j"));
    expect(events.map((e) => e.text)).toEqual(["wave started", "wave done"]);
    expect(open).toHaveBeenCalledTimes(1);
    expect(open.mock.calls[0]![0]).toBe("/api/jobs/j/events?after=0");
  });

  it("resumes from the last id when the stream drops while the job still runs", async () => {
    const open = vi
      .fn()
      .mockResolvedValueOnce(responseOf(streamOf([frame(1, "a"), frame(2, "b")], new TypeError("network"))))
      .mockResolvedValueOnce(responseOf(streamOf([frame(3, "c")])));
    const statuses = [job("running", 3), job("done", 3)];
    const pause = vi.fn().mockResolvedValue(undefined);
    const api = fakeApi({ open, get: () => Promise.resolve(statuses.shift()) as never });
    const events = await collect(followJob(api, "j", { pause }));
    expect(events.map((e) => e.id)).toEqual([1, 2, 3]);
    expect(open.mock.calls.map((c) => c[0])).toEqual([
      "/api/jobs/j/events?after=0",
      "/api/jobs/j/events?after=2",
    ]);
    expect(pause).toHaveBeenCalledTimes(1);
  });

  it("reconnects after a failed connection attempt (not only a mid-stream drop)", async () => {
    const open = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockResolvedValueOnce(responseOf(streamOf([frame(1, "a")])));
    const statuses = [job("running", 0), job("done", 1)];
    const api = fakeApi({ open, get: () => Promise.resolve(statuses.shift()) as never });
    const events = await collect(followJob(api, "j", { pause: () => Promise.resolve() }));
    expect(events.map((e) => e.text)).toEqual(["a"]);
  });

  it("reads once more when the job finished after the stream was read but events are missing", async () => {
    const open = vi
      .fn()
      .mockResolvedValueOnce(responseOf(streamOf([frame(1, "a")])))
      .mockResolvedValueOnce(responseOf(streamOf([frame(2, "b")])));
    const api = fakeApi({ open, get: () => Promise.resolve(job("done", 2)) as never });
    const events = await collect(followJob(api, "j"));
    expect(events.map((e) => e.id)).toEqual([1, 2]);
  });

  it("does not loop forever when a finished job has nothing more to give (a gap in the server buffer)", async () => {
    const open = vi.fn().mockImplementation(() => Promise.resolve(responseOf(streamOf([]))));
    const api = fakeApi({ open, get: () => Promise.resolve(job("done", 5)) as never });
    expect(await collect(followJob(api, "j"))).toEqual([]);
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("stops on abort and does not reconnect", async () => {
    const controller = new AbortController();
    const open = vi.fn().mockImplementation(() => {
      controller.abort();
      return Promise.reject(new TypeError("aborted"));
    });
    const api = fakeApi({ open, get: () => Promise.reject(new Error("must not be called")) });
    expect(await collect(followJob(api, "j", { signal: controller.signal }))).toEqual([]);
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("does not hide an error that is not a network drop (e.g. a 401)", async () => {
    const open = vi.fn().mockRejectedValue(new ApiError(401, "missing or wrong token"));
    const api = fakeApi({ open });
    await expect(collect(followJob(api, "j"))).rejects.toThrow("missing or wrong token");
    expect(open).toHaveBeenCalledTimes(1);
  });
});
