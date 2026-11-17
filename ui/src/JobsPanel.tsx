import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, type Api } from "./lib/api";
import { followJob } from "./lib/follow";
import type { JobEvent, JobKind, JobView } from "./lib/types";

const MAX_SHOWN = 500; // events kept on screen

export function JobsPanel({ api }: { api: Api }) {
  const [kind, setKind] = useState<JobKind>("wave");
  const [withReview, setWithReview] = useState(false);
  const [jobs, setJobs] = useState<JobView[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    setJobs(await api.get<JobView[]>("/api/jobs"));
  }, [api]);

  const follow = useCallback(
    (jobId: string) => {
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      setActiveId(jobId);
      setEvents([]);
      void (async () => {
        try {
          for await (const event of followJob(api, jobId, { signal: controller.signal })) {
            setEvents((old) => [...old, event].slice(-MAX_SHOWN));
          }
        } catch (error) {
          if (!controller.signal.aborted) setMessage((error as Error).message);
        }
        if (!controller.signal.aborted) await refresh().catch(() => undefined);
      })();
    },
    [api, refresh],
  );

  useEffect(() => {
    refresh().catch((e: Error) => setMessage(e.message));
    return () => abort.current?.abort();
  }, [refresh]);

  async function start() {
    setMessage(null);
    const body: Record<string, unknown> = { kind };
    if (kind === "wave" && withReview) body.review = true;
    try {
      const job = await api.post<{ id: string }>("/api/jobs", body);
      await refresh();
      follow(job.id);
    } catch (error) {
      // 409: another run (a job here, the CLI, another server) holds the project
      setMessage(error instanceof ApiError ? error.detail : (error as Error).message);
    }
  }

  async function cancel(jobId: string) {
    setMessage(null);
    try {
      await api.post(`/api/jobs/${jobId}/cancel`);
      await refresh();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.detail : (error as Error).message);
    }
  }

  const active = jobs.find((job) => job.id === activeId);

  return (
    <section aria-labelledby="jobs-title">
      <h2 id="jobs-title">İşler</h2>
      <div className="row">
        <label>
          Tür{" "}
          <select value={kind} onChange={(e) => setKind(e.target.value as JobKind)}>
            <option value="wave">wave</option>
            <option value="review">review</option>
            <option value="commit">commit</option>
          </select>
        </label>
        {kind === "wave" && (
          <label>
            <input
              type="checkbox"
              checked={withReview}
              onChange={(e) => setWithReview(e.target.checked)}
            />{" "}
            review de yap
          </label>
        )}
        <button type="button" onClick={() => void start()}>
          Başlat
        </button>
      </div>
      {message && <p role="alert">{message}</p>}
      <ul className="jobs">
        {jobs.map((job) => (
          <li key={job.id}>
            <button type="button" onClick={() => follow(job.id)}>
              {job.kind} {job.id.slice(0, 8)}
            </button>{" "}
            <span className={`status status-${job.status}`}>{job.status}</span>
            {job.status === "running" && job.kind !== "commit" && (
              <button type="button" onClick={() => void cancel(job.id)}>
                İptal
              </button>
            )}
            {job.error && <span> — {job.error}</span>}
          </li>
        ))}
      </ul>
      {activeId && (
        <div>
          <h3>
            Olaylar {active ? `(${active.kind}, ${active.status})` : ""}
          </h3>
          <pre className="log" aria-label="Canlı olaylar">
            {events.map((e) => `${e.task ? e.task + ": " : ""}${e.text}`).join("\n")}
          </pre>
        </div>
      )}
    </section>
  );
}
