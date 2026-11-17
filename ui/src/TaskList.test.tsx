import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fakeApi } from "./lib/helpers.test-util";
import { TaskList } from "./TaskList";

describe("TaskList", () => {
  it("shows the tasks with status, accepted criteria, review and scope", async () => {
    const get = vi.fn().mockResolvedValue([
      { id: "T-1", title: "First", status: "DONE", scope: ["src/a", "src/b"], acceptance: 3, verified: 3, review: "approved" },
      { id: "T-2", title: "Second", status: "READY", scope: [], acceptance: 2, verified: 0, review: null },
    ]);
    render(<TaskList api={fakeApi({ get })} />);
    expect(await screen.findByText("First")).toBeTruthy();
    expect(get).toHaveBeenCalledWith("/api/tasks");
    expect(screen.getByText("3/3")).toBeTruthy();
    expect(screen.getByText("approved")).toBeTruthy();
    expect(screen.getByText("src/a, src/b")).toBeTruthy();
    expect(screen.getByText("0/2")).toBeTruthy();
    expect(screen.getByText("—")).toBeTruthy(); // no review yet
  });

  it("shows a broken task file without hiding the others", async () => {
    const get = vi.fn().mockResolvedValue([{ id: "T-1", title: "Fine", status: "DONE" }, { id: "T-2", error: "bad yaml" }]);
    render(<TaskList api={fakeApi({ get })} />);
    expect(await screen.findByText("Fine")).toBeTruthy();
    expect(screen.getByText(/Okunamadı: bad yaml/)).toBeTruthy();
  });

  it("says so when there are no tasks", async () => {
    render(<TaskList api={fakeApi({ get: () => Promise.resolve([]) as never })} />);
    expect(await screen.findByText("Görev yok.")).toBeTruthy();
  });

  it("shows the error when loading fails, and loads again on Yenile", async () => {
    const get = vi.fn().mockRejectedValueOnce(new Error("401: missing or wrong token")).mockResolvedValueOnce([]);
    render(<TaskList api={fakeApi({ get })} />);
    expect((await screen.findByRole("alert")).textContent).toContain("missing or wrong token");
    await userEvent.click(screen.getByRole("button", { name: "Yenile" }));
    expect(await screen.findByText("Görev yok.")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("renders a task title as text, never as markup", async () => {
    const get = vi.fn().mockResolvedValue([{ id: "T-1", title: "<img src=x onerror=alert(1)>", status: "DONE" }]);
    const { container } = render(<TaskList api={fakeApi({ get })} />);
    await screen.findByText("<img src=x onerror=alert(1)>");
    expect(container.querySelector("img")).toBeNull();
  });
});
