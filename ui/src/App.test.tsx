import { render, screen } from "@testing-library/react";
import { App } from "./App";
import { fakeApi } from "./lib/helpers.test-util";

describe("App", () => {
  it("explains how to pass the token and makes no request without one", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<App api={null} />);
    expect(screen.getByRole("alert").textContent).toContain("#token=");
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("shows the task list by default and the jobs panel on #/jobs", async () => {
    const get = vi.fn().mockImplementation((path: string) => Promise.resolve(path === "/api/tasks" ? [] : []));
    window.location.hash = "";
    const { unmount } = render(<App api={fakeApi({ get })} />);
    expect(await screen.findByRole("heading", { name: "Görevler" })).toBeTruthy();
    unmount();
    window.location.hash = "#/jobs";
    render(<App api={fakeApi({ get })} />);
    expect(await screen.findByRole("heading", { name: "İşler" })).toBeTruthy();
    window.location.hash = "";
  });

  it("marks the current section in the navigation", () => {
    window.location.hash = "#/jobs";
    render(<App api={fakeApi({ get: () => Promise.resolve([]) as never })} />);
    expect(screen.getByRole("link", { name: "İşler" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Görevler" }).getAttribute("aria-current")).toBeNull();
    window.location.hash = "";
  });
});
