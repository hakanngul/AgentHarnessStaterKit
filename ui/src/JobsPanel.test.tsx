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
});
