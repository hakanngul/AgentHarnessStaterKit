import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { JobsPanel } from "./JobsPanel";
import { ApiError } from "./lib/api";
import { fakeApi, frame, responseOf, streamOf } from "./lib/helpers.test-util";
import type { JobView } from "./lib/types";

const job = (over: Partial<JobView> = {}): JobView => ({
  id: "0123456789abcdef0123456789abcdef",
  kind: "wave",
  status: "running",
  params: {},
  created: "",
  finished: null,
  error: null,
  result: null,
  last_event: 0,
  ...over,
});

function apiWith(jobs: JobView[], extra: Parameters<typeof fakeApi>[0] = {}) {
  return fakeApi({
    get: ((path: string) => {
      if (path === "/api/jobs") return Promise.resolve(jobs);
      return Promise.resolve(jobs.find((j) => path === `/api/jobs/${j.id}`) ?? job({ status: "done" }));
    }) as never,
    ...extra,
  });
}

describe("JobsPanel", () => {
  it("lists the jobs and offers cancel only for a running wave or review", async () => {
    const jobs = [
      job({ id: "a".repeat(32), kind: "wave", status: "running" }),
      job({ id: "b".repeat(32), kind: "commit", status: "running" }),
      job({ id: "c".repeat(32), kind: "review", status: "done" }),
      job({ id: "d".repeat(32), kind: "wave", status: "failed", error: "ledger has errors" }),
    ];
    render(<JobsPanel api={apiWith(jobs)} />);
    expect(await screen.findByText("wave aaaaaaaa")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "İptal" })).toHaveLength(1); // not commit, not finished
    expect(screen.getByText(/ledger has errors/)).toBeTruthy();
  });

  it("does not show a done job with result.ok=false as a plain success", async () => {
    const jobs = [
      job({ id: "a".repeat(32), status: "done", result: { ok: false } }),
      job({ id: "b".repeat(32), status: "done", result: { ok: true } }),
    ];
    render(<JobsPanel api={apiWith(jobs)} />);
    await screen.findByText("wave aaaaaaaa");
    expect(screen.getAllByText(/result\.ok=false/)).toHaveLength(1);
  });

  it("starts a wave, follows its events and shows them as plain text", async () => {
    const id = "e".repeat(32);
    const post = vi.fn().mockResolvedValue({ id });
    const open = vi.fn().mockResolvedValue(
      responseOf(streamOf([frame(1, "wave started"), frame(2, "session s-1", "T-1"), frame(3, "<b>bold?</b>")])),
    );
    const api = apiWith([job({ id, status: "done", last_event: 3 })], { post, open });
    const { container } = render(<JobsPanel api={api} />);
    await userEvent.click(await screen.findByRole("button", { name: "Başlat" }));
    expect(post).toHaveBeenCalledWith("/api/jobs", { kind: "wave" });
    const log = await screen.findByLabelText("Canlı olaylar");
    await waitFor(() => expect(log.textContent).toContain("T-1: session s-1"));
    expect(log.textContent).toContain("wave started");
    expect(log.textContent).toContain("<b>bold?</b>");
    expect(container.querySelector("b")).toBeNull();
    expect(open.mock.calls[0]![0]).toBe(`/api/jobs/${id}/events?after=0`);
  });

  it("asks for a review with the wave only when the box is ticked, and only for a wave", async () => {
    const post = vi.fn().mockResolvedValue({ id: "f".repeat(32) });
    const api = apiWith([], { post, open: () => Promise.resolve(responseOf(streamOf([]))) });
    render(<JobsPanel api={api} />);
    await userEvent.click(await screen.findByLabelText(/review de yap/));
    await userEvent.click(screen.getByRole("button", { name: "Başlat" }));
    expect(post).toHaveBeenLastCalledWith("/api/jobs", { kind: "wave", review: true });
    await userEvent.selectOptions(screen.getByLabelText(/Tür/), "review");
    expect(screen.queryByLabelText(/review de yap/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Başlat" }));
    expect(post).toHaveBeenLastCalledWith("/api/jobs", { kind: "review" });
  });

  it("shows who holds the project when starting is refused with 409", async () => {
    const post = vi.fn().mockRejectedValue(new ApiError(409, "another harness run is active on this project (wave, pid 7)"));
    render(<JobsPanel api={apiWith([], { post })} />);
    await userEvent.click(await screen.findByRole("button", { name: "Başlat" }));
    expect((await screen.findByRole("alert")).textContent).toContain("pid 7");
  });

  it("cancels a running job and refreshes the list", async () => {
    const id = "9".repeat(32);
    const post = vi.fn().mockResolvedValue({});
    const get = vi.fn().mockResolvedValue([job({ id, status: "running" })]);
    render(<JobsPanel api={fakeApi({ get: get as never, post })} />);
    await userEvent.click(await screen.findByRole("button", { name: "İptal" }));
    expect(post).toHaveBeenCalledWith(`/api/jobs/${id}/cancel`);
    await waitFor(() => expect(get.mock.calls.filter((c) => c[0] === "/api/jobs").length).toBeGreaterThan(1));
  });

  it("shows an error when the list cannot be loaded", async () => {
    const get = vi.fn().mockRejectedValue(new ApiError(401, "missing or wrong token"));
    render(<JobsPanel api={fakeApi({ get })} />);
    expect((await screen.findByRole("alert")).textContent).toContain("missing or wrong token");
  });

  function openStream() {
    let push!: (text: string) => void;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (text) => controller.enqueue(new TextEncoder().encode(text));
      },
    });
    return { body, push };
  }

  it("exposes the event log as a log region", async () => {
    const id = "1".repeat(32);
    const api = apiWith([job({ id, status: "done" })], { open: () => Promise.resolve(responseOf(streamOf([]))) });
    render(<JobsPanel api={api} />);
    await userEvent.click(await screen.findByRole("button", { name: /wave 11111111/ }));
    expect(await screen.findByRole("log", { name: "Canlı olaylar" })).toBeTruthy();
  });

  it("aborts the stream when the panel goes away", async () => {
    const id = "2".repeat(32);
    const stream = openStream();
    const signals: (AbortSignal | undefined)[] = [];
    const open = vi.fn().mockImplementation((_path: string, signal?: AbortSignal) => {
      signals.push(signal);
      return Promise.resolve(responseOf(stream.body));
    });
    const view = render(<JobsPanel api={apiWith([job({ id })], { open })} />);
    await userEvent.click(await screen.findByRole("button", { name: /wave 22222222/ }));
    await waitFor(() => expect(open).toHaveBeenCalled());
    view.unmount();
    expect(signals[0]!.aborted).toBe(true);
  });

  it("does not start a follow when the panel went away while the start request was in flight", async () => {
    let resolvePost!: (value: { id: string }) => void;
    const post = vi.fn().mockReturnValue(new Promise((resolve) => (resolvePost = resolve)));
    const open = vi.fn().mockResolvedValue(responseOf(streamOf([])));
    const view = render(<JobsPanel api={apiWith([], { post, open })} />);
    await userEvent.click(await screen.findByRole("button", { name: "Başlat" }));
    view.unmount();
    resolvePost({ id: "3".repeat(32) });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(open).not.toHaveBeenCalled();
  });

  it("keeps a stale event of the previous job out of the new job's log", async () => {
    const a = "a".repeat(32);
    const b = "b".repeat(32);
    const streamA = openStream();
    const open = vi.fn().mockImplementation((path: string) =>
      Promise.resolve(
        responseOf(path.includes(a) ? streamA.body : streamOf([frame(1, "from B")])),
      ),
    );
    const api = apiWith([job({ id: a }), job({ id: b, status: "done", last_event: 1 })], { open });
    render(<JobsPanel api={api} />);
    await userEvent.click(await screen.findByRole("button", { name: /wave aaaaaaaa/ }));
    await userEvent.click(await screen.findByRole("button", { name: /wave bbbbbbbb/ }));
    const log = await screen.findByRole("log");
    await waitFor(() => expect(log.textContent).toContain("from B"));
    streamA.push(frame(1, "stale from A"));
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(log.textContent).not.toContain("stale from A");
  });
});
